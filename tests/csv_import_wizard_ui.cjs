'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {JSDOM, VirtualConsole} = require('jsdom');
const {implForWrapper} = require('jsdom/lib/jsdom/living/generated/utils');

const ROOT = path.resolve(__dirname, '..');
const WEB = path.join(ROOT, 'web', 'visualization');
const SHARED = path.join(WEB, 'csv-import-wizard.js');

test('all four real import pages expose the shared staged import experience', () => {
  assert.ok(fs.existsSync(SHARED), 'the shared CSV import wizard module must exist');
  for (const [file, ids] of [
    ['import.html', ['v2ImpWizard']],
    ['simulation.html', ['csvImportWizard']],
    ['runner.html', ['trainCsvWizard', 'evalCsvWizard']],
  ]) {
    const dom = new JSDOM(fs.readFileSync(path.join(WEB, file), 'utf8'));
    try {
      for (const id of ids) {
        const root = dom.window.document.getElementById(id);
        assert.ok(root, `${file} must provide the ${id} wizard`);
        assert.deepEqual(
          [...root.querySelectorAll('fieldset[data-csv-stage]')].map(node => node.dataset.csvStage),
          ['file', 'fields', 'review', 'done'],
          `${id} must group real importer controls into the four stages`
        );
      }
    } finally {
      dom.window.close();
    }
  }
});

const FEATURE = {
  feature_key: 'ADCS.RW1.speed', subsystem: 'ADCS', device: 'RW1',
  variable: 'speed', unit: 'rad/s'
};
const CSV_TEXT = 'time_s;ADCS.RW1.speed;note\n0;10;original note\n1;11;unchanged\n' +
  '2;12;unchanged\n3;13;unchanged\n4;14;unchanged\n5;15;unchanged\n6;16;unchanged\n7;17;last\n';
const SIMULATION_CSV_TEXT = 'time_s;ADCS.RW1.speed;note;label;aas_fault_events\n' +
  '0;10;keep this note;1;keep-original-event\n1;11;unchanged;0;\n2;12;unchanged;0;\n' +
  '3;13;unchanged;0;\n4;14;unchanged;0;\n5;15;unchanged;0;\n6;16;unchanged;0;\n7;17;last;0;\n';
const clone = value => JSON.parse(JSON.stringify(value));
const turn = () => new Promise(resolve => setImmediate(resolve));
async function flush() { for (let index = 0; index < 4; index++) await turn(); }
async function until(predicate, description) {
  const deadline = Date.now() + 2000;
  while (!predicate()) {
    if (Date.now() >= deadline) assert.fail('Timed out waiting for ' + description);
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

function quality(hasLabels = false) {
  return {
    row_count: 8, missing_by_column: {}, non_numeric_by_column: {},
    nonfinite_by_column: {}, duplicate_times: 0, time_reversals: 0,
    gaps_gt_3x_median: 0, sample_period_median_seconds: 1,
    source_order_reversals: 0, issue_count: 0, issues: [], issues_truncated: false,
    label_counts: {'0': hasLabels ? 8 : 0, '1': 0, unknown: hasLabels ? 0 : 8}
  };
}

// Only HTTP/task boundaries are replaced. Forms, FileLists, event propagation,
// fieldset disabled state, importer adapters, and the runner bridge are real.
function networkFixture(kind) {
  const state = {
    kind, calls: [], unexpected: [], sources: [], datasets: [], tasks: new Map(),
    asynchronous: new Set(), held: new Set(), failures: new Map(), nextId: 101,
    refreshFailure: false, hasLabels: false, cancelled: [], confirmations: 0,
    inspectColumns: ['time_s', 'ADCS.RW1.speed', 'note']
  };
  const result = data => ({status: 200, data});
  state.isImport = call => call.url.pathname === '/api/v2/telemetry/import-source' ||
    ['/api/v2/csv-workspace/inspect', '/api/v2/csv-workspace/import'].includes(call.url.pathname);
  state.importCalls = () => state.calls.filter(state.isImport);
  state.importOperations = () => state.importCalls().map(call =>
    call.meta ? call.meta.operation : call.url.pathname.split('/').pop());
  state.inspect = meta => ({
    status: 'inspected', format: 'csv', columns: [...state.inspectColumns],
    numeric_columns: ['ADCS.RW1.speed'], sampled_rows: 8, row_count: 8,
    has_labels: state.hasLabels, time_candidates: ['time_s'],
    label_candidates: state.hasLabels ? ['label'] : [],
    csv_encoding: meta.csv_encoding === 'auto' ? 'gb18030' : meta.csv_encoding,
    csv_delimiter: meta.csv_delimiter === 'auto' ? ';' : meta.csv_delimiter,
    format_review_required: true, quality: quality(state.hasLabels)
  });
  state.sourceResult = meta => {
    if (meta.operation === 'inspect') return state.inspect(meta);
    const role = meta.config.role;
    const labelStatus = state.hasLabels ? 'from_source' :
      role === 'train' ? 'confirmed_normal' : 'unknown';
    const columns = Object.entries(meta.config.column_metadata).map(([name, entry]) => ({
      source_column: name, ...entry
    }));
    const payload = {
      status: meta.operation === 'commit' ? 'imported' : 'preview',
      row_count: 8, column_count: columns.length, columns, label_status: labelStatus,
      time_start: 0, time_end: 7, sha256: 'a'.repeat(64),
      quality: quality(state.hasLabels), import_context: {
        run_token: '', dataset_split: '', label_scope: 'unknown', evidence_origin: ''
      },
      csv_encoding: meta.csv_encoding, csv_delimiter: meta.csv_delimiter
    };
    if (meta.operation === 'commit') {
      payload.source_id = state.nextId++;
      state.sources.push({
        source_id: payload.source_id, allowed_role: role === 'train' ? 'train' :
          labelStatus === 'unknown' ? 'score_only' : 'test',
        split: role === 'train' ? 'train' : 'test', model_alias: meta.config.model,
        run_role: role === 'train' ? 'NORMAL' : '', normal_frames: role === 'train' ? 8 : 0,
        fault_frames: 0, unknown_frames: labelStatus === 'unknown' ? 8 : 0,
        feature_keys: columns.map(column =>
          [column.subsystem, column.device, column.variable].join('.'))
      });
    }
    return payload;
  };
  state.handle = async call => {
    state.calls.push(call);
    const pathname = call.url.pathname;
    if (pathname === '/api/v2/telemetry/import-source') {
      const failure = state.failures.get(call.meta.operation);
      if (failure) return {status: 422, data: {error: failure}};
      const data = state.sourceResult(call.meta);
      if (state.asynchronous.has(call.meta.operation)) {
        const id = 'csv-task-' + state.tasks.size;
        state.tasks.set(id, {id, operation: call.meta.operation, result: data});
        return {status: 202, data: {status: 'accepted', task_id: id}};
      }
      return result(data);
    }
    if (pathname.endsWith('/telemetry-v2-column-catalog.json')) {
      return result({items: {'ADCS.RW1.speed': {...FEATURE}}});
    }
    if (pathname === '/api/v2/telemetry/imports') return result({items: []});
    if (pathname === '/api/v2/run-dataset/catalog') {
      if (state.refreshFailure && state.sources.length) {
        return {status: 503, data: {error: 'catalog temporarily unavailable'}};
      }
      return result({
        runs: clone(state.sources), features: [{...FEATURE}], max_features: 8,
        run_features: Object.fromEntries(state.sources.map(source =>
          [String(source.source_id), [{...FEATURE}]])),
        default_train_ids: [], default_test_ids: []
      });
    }
    if (pathname === '/api/available-algorithms') return result({algorithms: []});
    if (pathname === '/api/v2/models') {
      return result({models: [{
        id: 7, model_name: 'Published baseline', algorithm: 'baseline',
        feature_count: 1, feature_keys: ['ADCS.RW1.speed'],
        feature_semantics: [['ADCS', 'RW1', 'speed', 'rad/s']],
        integrity: 'schema_bound', schema_sha256: 'b'.repeat(64),
        train_source_ids: [90], alignment_method: 'exact', tolerance_seconds: 0
      }]});
    }
    if (pathname === '/api/v2/run-dataset/coverage') {
      return result({
        has_common_window: true, start_s: 0, end_s: 7,
        coverage: [{source_id: call.json.source_ids[0], feature_key: 'ADCS.RW1.speed',
          start_s: 0, end_s: 7}]
      });
    }
    if (pathname === '/api/v2/csv-workspace/list') {
      if (state.refreshFailure && state.datasets.length) {
        return {status: 503, data: {error: 'workspace refresh temporarily unavailable'}};
      }
      return result({items: clone(state.datasets)});
    }
    if (pathname === '/api/v2/csv-workspace/inspect') {
      if (state.failures.has('inspect')) {
        return {status: 422, data: {error: state.failures.get('inspect')}};
      }
      return result({
        frame_count: 8, columns: ['time_s', 'ADCS.RW1.speed', 'note', 'label', 'aas_fault_events'],
        numeric_columns: ['ADCS.RW1.speed'], has_truth_labels: true,
        csv_encoding: call.format.csv_encoding === 'auto' ? 'gb18030' : call.format.csv_encoding,
        csv_delimiter: call.format.csv_delimiter === 'auto' ? ';' : call.format.csv_delimiter,
        format_review_required: true,
        quality: {...quality(true), label_counts: {'0': 7, '1': 1, unknown: 0}},
        sample: [{frame: 1, time_s: 0, 'ADCS.RW1.speed': 10, note: 'keep this note',
          label: 1, aas_fault_events: 'keep-original-event'}]
      });
    }
    if (pathname === '/api/v2/csv-workspace/import') {
      if (state.failures.has('commit')) {
        return {status: 422, data: {error: state.failures.get('commit')}};
      }
      const saved = {
        id: 'dataset-original-v0', filename: call.body.name, version: 0, parent_id: null,
        frame_count: 8, numeric_columns: ['ADCS.RW1.speed'],
        columns: ['time_s', 'ADCS.RW1.speed', 'note', 'label', 'aas_fault_events'],
        has_truth_labels: true, active_event_count: 0, event_count: 0, operation_count: 0,
        csv_encoding: call.format.csv_encoding, csv_delimiter: call.format.csv_delimiter,
        csv_format: {encoding: call.format.csv_encoding, delimiter: call.format.csv_delimiter}
      };
      state.datasets.push(saved);
      return result(saved);
    }
    if (pathname === '/api/v2/csv-workspace/events') {
      return result({events: [], operations: [], event_count: 0, active_event_count: 0});
    }
    if (pathname === '/api/v2/csv-workspace/ancestors') return result({ancestors: []});
    if (pathname === '/api/v2/csv-workspace/dataset-preview') {
      return result({
        version: 0, frame_count: 8, column: 'ADCS.RW1.speed', events: [],
        points: [{frame: 1, value: 10}, {frame: 8, value: 17}]
      });
    }
    state.unexpected.push(call.method + ' ' + pathname);
    throw Error('Unexpected HTTP boundary: ' + call.method + ' ' + pathname);
  };
  state.waitTask = async (id, options = {}) => {
    const task = state.tasks.get(id);
    assert.ok(task, 'the importer must query the accepted task ID');
    task.waiting = true;
    options.onUpdate?.({
      task_id: id, status: 'running', progress: 42, cancellable: true,
      message: 'Backend is validating the original file'
    });
    if (state.held.has(task.operation)) return new Promise(resolve => { task.release = resolve; });
    return {task_id: id, status: 'completed', progress: 100, result: clone(task.result)};
  };
  state.finish = (operation, status = 'completed') => {
    const task = [...state.tasks.values()].find(item => item.operation === operation && item.release);
    assert.ok(task, 'expected a waiting ' + operation + ' task');
    task.release({
      task_id: task.id, status, progress: status === 'completed' ? 100 : 42,
      result: status === 'completed' ? clone(task.result) : undefined,
      failure: status === 'failed' ? {message: 'Task failed without an import receipt'} : undefined
    });
  };
  return state;
}

function browser(t, kind, options = {}) {
  assert.ok(fs.existsSync(SHARED), 'the shared CSV import wizard module must exist');
  const page = kind === 'runner' ? 'runner.html' : kind === 'history' ? 'import.html' : 'simulation.html';
  const html = fs.readFileSync(path.join(WEB, page), 'utf8');
  const errors = [], messages = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = options.embedded
    ? new JSDOM('<iframe id="import" src="https://aas.test/import.html?dialog=1"></iframe>', {
      url: 'https://aas.test/browser.html', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole
    })
    : new JSDOM(html, {
      url: 'https://aas.test/' + page, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole
    });
  const w = options.embedded ? dom.window.document.querySelector('iframe').contentWindow : dom.window;
  if (options.embedded) {
    w.document.open(); w.document.write(html); w.document.close();
    dom.window.addEventListener('message', event => messages.push(event.data));
  }
  const d = w.document, net = networkFixture(kind);
  w.TextEncoder = TextEncoder;
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.confirm = () => { net.confirmations++; return true; };
  // jsdom has no native file picker; keep its actual FileList implementation so
  // required file inputs and reportValidity are exercised, rather than mocked.
  if (!w.Blob.prototype.text) {
    w.Blob.prototype.text = function () {
      return new Promise((resolve, reject) => {
        const reader = new w.FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsText(this);
      });
    };
  }
  const request = (url, init = {}) => {
    const headers = Object.fromEntries(Object.entries(init.headers || {}).map(([key, value]) =>
      [key.toLowerCase(), value]));
    const call = {url: new URL(url, w.location.href), method: init.method || 'GET',
      body: init.body, headers};
    if (headers['x-aas-import-metadata']) {
      call.meta = JSON.parse(Buffer.from(headers['x-aas-import-metadata'], 'base64').toString('utf8'));
    }
    if (headers['x-aas-csv-format-b64']) {
      call.format = JSON.parse(Buffer.from(headers['x-aas-csv-format-b64'], 'base64').toString('utf8'));
    }
    if (typeof init.body === 'string' && headers['content-type'] === 'application/json') {
      call.json = JSON.parse(init.body);
    }
    return net.handle(call);
  };
  w.fetch = async (url, init) => {
    const reply = await request(url, init);
    return {ok: reply.status >= 200 && reply.status < 300, status: reply.status,
      json: async () => clone(reply.data)};
  };
  class NetworkXHR extends w.EventTarget {
    constructor() {
      super(); this.headers = {}; this.upload = new w.EventTarget();
      this.status = 0; this.responseText = ''; this.aborted = false;
    }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(key, value) { this.headers[key] = value; }
    emit(target, name, props = {}) {
      const event = new w.ProgressEvent(name, props);
      target.dispatchEvent(event);
      if (typeof target['on' + name] === 'function') target['on' + name](event);
    }
    send(body) {
      Promise.resolve().then(async () => {
        this.emit(this.upload, 'progress', {lengthComputable: true, loaded: body.size, total: body.size});
        this.emit(this.upload, 'load');
        const reply = await request(this.url, {method: this.method, headers: this.headers, body});
        if (this.aborted) return;
        this.status = reply.status; this.responseText = JSON.stringify(reply.data);
        this.emit(this, 'load');
      }).catch(error => {
        if (!this.aborted) { errors.push(error); this.emit(this, 'error'); }
      });
    }
    abort() { this.aborted = true; this.emit(this, 'abort'); }
  }
  w.XMLHttpRequest = NetworkXHR;
  w.AASTasks = {
    wait: (id, opts) => net.waitTask(id, opts),
    cancel: async id => { net.cancelled.push(id); return {accepted: true, status: 'cancelling'}; }
  };
  const load = filename => w.eval(fs.readFileSync(path.join(WEB, filename), 'utf8') +
    '\n//# sourceURL=' + filename);
  load('csv-import-wizard.js');
  if (kind === 'runner') load('runner-v2.js');
  load(kind === 'runner' ? 'runner-csv-import.js' :
    kind === 'history' ? 'telemetry-v2-import.js' : 'simulation-csv-workspace.js');
  const el = id => {
    const node = d.getElementById(id);
    assert.ok(node, 'missing real page control #' + id);
    return node;
  };
  const root = id => el(id);
  const stage = id => {
    const current = root(id).querySelector('[data-csv-go][aria-current="step"]');
    assert.ok(current, id + ' must expose its active stage');
    return current.dataset.csvGo;
  };
  const panel = (id, name) => root(id).querySelector('[data-csv-stage="' + name + '"]');
  const enabled = node => !node.matches(':disabled');
  const visible = node => !node.closest('[hidden]');
  const click = async selector => {
    const node = typeof selector === 'string' ? el(selector) : selector;
    assert.ok(visible(node), 'user action must be visible: ' + (node.id || node.outerHTML));
    assert.ok(enabled(node), 'user action must be enabled: ' + (node.id || node.outerHTML));
    node.click(); await flush();
  };
  const change = (id, value, type = 'change') => {
    const node = el(id);
    assert.ok(visible(node) && enabled(node), 'editable user control #' + id);
    node.value = value; node.dispatchEvent(new w.Event(type, {bubbles: true}));
  };
  const chooseFile = async (id, name = 'input.csv', content = CSV_TEXT) => {
    const input = el(id);
    assert.ok(visible(input) && enabled(input), 'file chooser must be usable #' + id);
    const picker = d.createElement('input'); picker.type = 'file';
    const list = picker.files, file = new w.File([content], name, {type: 'text/csv'});
    implForWrapper(list).push(implForWrapper(file));
    input.files = list;
    input.dispatchEvent(new w.Event('change', {bubbles: true}));
    await flush(); return file;
  };
  const nav = (id, name) => root(id).querySelector('[data-csv-go="' + name + '"]');
  const nextFields = id => root(id).querySelector('[data-csv-continue]');
  const doneAction = id => root(id).querySelector('[data-csv-next-action]');
  t.after(() => {
    dom.window.close();
    assert.deepEqual(net.unexpected, [], 'no accidental backend or algorithm calls');
    assert.deepEqual(errors.map(error => error.message), [], 'no swallowed browser/runtime errors');
  });
  return {dom, w, d, net, messages, el, root, stage, panel, enabled, visible,
    click, change, chooseFile, nav, nextFields, doneAction};
}

async function enterRunnerFields(f, role, name) {
  await flush();
  await f.click(role === 'train' ? 'tabTrain' : 'tabEval');
  const disclosure = f.el(role + 'CsvImport');
  if (!disclosure.open) await f.click(disclosure.querySelector('summary'));
  const before = f.net.importCalls().length;
  const file = await f.chooseFile(role + 'CsvFile', name || role + '.csv');
  assert.equal(f.net.importCalls().length, before, 'choosing a file must not submit an inspection');
  await f.click(role + 'CsvInspect');
  assert.equal(f.stage(role + 'CsvWizard'), 'file');
  assert.equal(f.el(role + 'CsvFormatConfirmed').checked, false);
  assert.equal(f.nextFields(role + 'CsvWizard').disabled, true,
    'detected format must remain unapproved until the user checks it');
  const checkedRequests = f.net.importCalls().length;
  await f.click(role + 'CsvFormatConfirmed');
  await f.click(f.nextFields(role + 'CsvWizard'));
  assert.equal(f.stage(role + 'CsvWizard'), 'fields');
  assert.equal(f.net.importCalls().length, checkedRequests,
    'format confirmation and stage navigation must not submit the file again');
  f.change(role + 'CsvUnit', 'rad/s', 'input');
  return file;
}

async function enterHistoryFields(f) {
  await flush();
  const file = await f.chooseFile('v2ImpFile', 'history.csv');
  assert.deepEqual(f.net.importOperations(), [], 'file selection alone is not an inspection action');
  await f.click('v2ImpCsvInspect');
  assert.equal(f.stage('v2ImpWizard'), 'file');
  const checkedRequests = f.net.calls.length;
  await f.click('v2ImpCsvFormatConfirmed');
  await f.click(f.nextFields('v2ImpWizard'));
  assert.equal(f.stage('v2ImpWizard'), 'fields');
  assert.equal(f.net.calls.length, checkedRequests,
    'historical format confirmation and stage navigation are presentation only');
  f.change('v2ImpNamespace', 'history-run-A', 'input');
  f.change('v2ImpModel', 'HISTORY-SAT', 'input');
  f.change('v2ImpRole', 'test');
  f.change('v2ImpTimeDomain', 'numeric_unspecified');
  f.change('v2ImpTimeReference', 'history-clock-A', 'input');
  await f.click('v2ImpUnknownTestConfirmed');
  return file;
}

async function enterSimulationReview(f) {
  await flush();
  const file = await f.chooseFile('csvFile', 'simulation.csv', SIMULATION_CSV_TEXT);
  assert.deepEqual(f.net.importOperations(), [], 'simulation selection must not upload by itself');
  await f.click('csvInspect');
  assert.equal(f.stage('csvImportWizard'), 'file');
  const checkedRequests = f.net.calls.length;
  await f.click('csvFormatConfirmed');
  await f.click(f.nextFields('csvImportWizard'));
  assert.equal(f.stage('csvImportWizard'), 'fields');
  assert.equal(f.net.calls.length, checkedRequests,
    'simulation format confirmation and stage navigation must not upload again');
  const before = f.net.calls.length;
  await f.click('csvReview');
  assert.equal(f.stage('csvImportWizard'), 'review');
  assert.equal(f.net.calls.length, before, 'simulation review uses the inspected full-table result');
  return file;
}

test('unknown test CSV completes in its original eval view and next navigation never evaluates', async t => {
  const f = browser(t, 'runner');
  const file = await enterRunnerFields(f, 'eval', 'unknown-test.csv');
  await f.click('evalCsvUnknownConfirmed');
  await f.click('evalCsvPreview');
  assert.equal(f.stage('evalCsvWizard'), 'review');
  assert.match(f.panel('evalCsvWizard', 'review').textContent, /未知.*仅推理|仅推理.*未知/);
  assert.equal(f.stage('trainCsvWizard'), 'file', 'test approval cannot advance the training wizard');
  const preview = f.net.importCalls().find(call => call.meta.operation === 'preview');
  assert.equal(preview.meta.config.role, 'test');
  assert.equal(preview.meta.config.unlabeled_test_confirmed, true);
  assert.equal(preview.meta.config.all_normal_confirmed, false);
  assert.equal(preview.meta.config.bind_digest_group, true);
  assert.equal(preview.body, file, 'the original File reaches the source importer');
  await f.click('evalCsvCommit');
  await until(() => f.stage('evalCsvWizard') === 'done', 'test import completion');
  assert.equal(f.el('evalView').hidden, false, 'catalog refresh must not hide the completed test wizard');
  assert.equal(f.el('trainView').hidden, true, 'test completion must remain in the test view');
  assert.equal(f.el('evalTruthControls').hidden, true);
  assert.match(f.el('runEval').textContent, /仅推理/);
  const commit = f.net.importCalls().find(call => call.meta.operation === 'commit');
  assert.equal(commit.meta.config.namespace, preview.meta.config.namespace,
    'preview and commit must use one stable import identity');
  assert.deepEqual(commit.meta.config, preview.meta.config);
  assert.equal(commit.body, file);
  assert.match(f.panel('evalCsvWizard', 'done').textContent, /unknown-test\.csv/);
  await until(() => f.net.calls.some(call => call.url.pathname.endsWith('/coverage')),
    'automatic imported-source coverage query');
  const before = f.net.calls.length;
  await f.click(f.doneAction('evalCsvWizard'));
  await flush();
  assert.equal(f.net.calls.length, before, 'next action must not submit a preview or algorithm request');
  assert.ok(['evalSourceModel', 'evalStart', 'evalModel'].includes(f.d.activeElement.id),
    'next action must focus an existing test-data or model control');
  assert.deepEqual(f.net.importOperations(), ['inspect', 'preview', 'commit']);
});

test('training requires explicit normal-label approval and never changes the test wizard or purpose', async t => {
  const f = browser(t, 'runner');
  await enterRunnerFields(f, 'train', 'normal-baseline.csv');
  await f.click('trainCsvPreview');
  assert.deepEqual(f.net.importOperations(), ['inspect'],
    'missing normal confirmation must be rejected before a backend preview');
  assert.equal(f.stage('trainCsvWizard'), 'fields');
  assert.match(f.el('trainCsvStatus').textContent, /正常/);
  await f.click('trainCsvNormalConfirmed');
  await f.click('trainCsvPreview');
  assert.equal(f.stage('trainCsvWizard'), 'review');
  assert.equal(f.stage('evalCsvWizard'), 'file');
  const preview = f.net.importCalls().find(call => call.meta.operation === 'preview');
  assert.equal(preview.meta.config.role, 'train');
  assert.equal(preview.meta.config.all_normal_confirmed, true);
  assert.equal(preview.meta.config.unlabeled_test_confirmed, false);
  assert.deepEqual(preview.meta.config.selected_columns, ['ADCS.RW1.speed']);
  await f.click('trainCsvCommit');
  await until(() => f.stage('trainCsvWizard') === 'done', 'training source completion');
  const commit = f.net.importCalls().find(call => call.meta.operation === 'commit');
  assert.deepEqual(commit.meta.config, preview.meta.config);
  assert.equal(f.stage('evalCsvWizard'), 'file');
  assert.equal(f.el('evalCsvUnknownConfirmed').checked, false);
  assert.ok(f.el('trainRuns').querySelector('input:checked'),
    'the actual runner bridge selects the newly imported training source');
});

test('runner mapping and file edits revoke the previously reviewed import', async t => {
  const f = browser(t, 'runner');
  await enterRunnerFields(f, 'train');
  await f.click('trainCsvNormalConfirmed');
  await f.click('trainCsvPreview');
  assert.equal(f.stage('trainCsvWizard'), 'review');
  await f.click(f.nav('trainCsvWizard', 'fields'));
  const before = f.net.calls.length;
  f.change('trainCsvUnit', 'revolutions/s', 'input');
  assert.equal(f.stage('trainCsvWizard'), 'fields');
  assert.equal(f.nav('trainCsvWizard', 'review').disabled, true);
  assert.equal(f.enabled(f.el('trainCsvCommit')), false);
  assert.equal(f.net.calls.length, before, 'editing a mapping cannot submit a request');
  await f.click('trainCsvPreview');
  assert.equal(f.stage('trainCsvWizard'), 'review');
  assert.equal(f.net.importCalls().at(-1).meta.config.column_metadata['ADCS.RW1.speed'].unit,
    'revolutions/s');
  await f.click(f.nav('trainCsvWizard', 'file'));
  await f.chooseFile('trainCsvFile', 'replacement.csv');
  assert.equal(f.stage('trainCsvWizard'), 'file');
  assert.equal(f.el('trainCsvFormatConfirmed').checked, false);
  assert.equal(f.nextFields('trainCsvWizard').disabled, true);
  assert.equal(f.nav('trainCsvWizard', 'review').disabled, true);
  assert.equal(f.net.importCalls().filter(call => call.meta.operation === 'commit').length, 0);
});

test('runner inspection and preview errors preserve inputs without reaching completion', async t => {
  const f = browser(t, 'runner');
  await flush();
  f.el('trainCsvImport').open = true;
  const file = await f.chooseFile('trainCsvFile', 'repairable.csv');
  f.net.failures.set('inspect', 'CSV inspection needs a corrected delimiter');
  await f.click('trainCsvInspect');
  assert.equal(f.stage('trainCsvWizard'), 'file');
  assert.equal(f.nextFields('trainCsvWizard').disabled, true);
  assert.equal(f.el('trainCsvFile').files[0], file);
  assert.match(f.el('trainCsvStatus').textContent, /corrected delimiter/);
  f.net.failures.delete('inspect');
  await f.click('trainCsvInspect');
  await f.click('trainCsvFormatConfirmed');
  await f.click(f.nextFields('trainCsvWizard'));
  await f.click('trainCsvNormalConfirmed');
  f.net.failures.set('preview', 'Data quality failed strict preview');
  await f.click('trainCsvPreview');
  assert.equal(f.stage('trainCsvWizard'), 'fields');
  assert.equal(f.nav('trainCsvWizard', 'review').disabled, true);
  assert.equal(f.nav('trainCsvWizard', 'done').disabled, true);
  assert.equal(f.el('trainCsvFile').files[0], file);
  assert.match(f.el('trainCsvStatus').textContent, /strict preview/);
});

test('an in-flight runner preview cannot approve a later mapping revision', async t => {
  const f = browser(t, 'runner');
  await enterRunnerFields(f, 'train', 'pending-preview.csv');
  await f.click('trainCsvNormalConfirmed');
  f.net.asynchronous.add('preview');
  f.net.held.add('preview');
  await f.click('trainCsvPreview');
  await until(() => [...f.net.tasks.values()].some(task => task.operation === 'preview' && task.waiting),
    'pending runner preflight task');
  assert.equal(f.root('trainCsvWizard').getAttribute('aria-busy'), 'true');
  assert.equal(f.enabled(f.el('trainCsvUnit')), false, 'pending requests lock normal user edits');
  assert.equal(f.nav('trainCsvWizard', 'file').disabled, true);
  assert.equal(f.el('trainCsvCancel').disabled, false, 'task cancellation stays available outside the fieldset');
  // A queued change event may outlive the user gesture that produced it.
  // The adapter must reject stale receipts in addition to disabling controls.
  f.el('trainCsvUnit').value = 'changed-while-pending';
  f.el('trainCsvUnit').dispatchEvent(new f.w.Event('input', {bubbles: true}));
  f.net.finish('preview');
  await until(() => f.root('trainCsvWizard').getAttribute('aria-busy') === 'false',
    'rejected stale preflight unlock');
  assert.equal(f.stage('trainCsvWizard'), 'fields');
  assert.equal(f.nav('trainCsvWizard', 'review').disabled, true);
  assert.equal(f.nav('trainCsvWizard', 'done').disabled, true);
  assert.match(f.el('trainCsvStatus').textContent, /预检期间.*变化/);
  assert.equal(f.net.importCalls().filter(call => call.meta.operation === 'commit').length, 0);
});

test('a cancelled runner import task never turns an accepted request into a completion receipt', async t => {
  const f = browser(t, 'runner');
  await enterRunnerFields(f, 'eval', 'cancelled-test.csv');
  await f.click('evalCsvUnknownConfirmed');
  await f.click('evalCsvPreview');
  f.net.asynchronous.add('commit');
  f.net.held.add('commit');
  await f.click('evalCsvCommit');
  await until(() => [...f.net.tasks.values()].some(task => task.operation === 'commit' && task.waiting),
    'pending test import task');
  assert.equal(f.stage('evalCsvWizard'), 'review');
  await f.click('evalCsvCancel');
  assert.equal(f.net.cancelled.length, 1);
  f.net.finish('commit', 'cancelled');
  await until(() => f.root('evalCsvWizard').getAttribute('aria-busy') === 'false',
    'cancelled import unlock');
  assert.notEqual(f.stage('evalCsvWizard'), 'done');
  assert.equal(f.nav('evalCsvWizard', 'done').disabled, true);
  assert.match(f.el('evalCsvStatus').textContent, /取消/);
  assert.equal(f.net.importCalls().filter(call => call.meta.operation === 'commit').length, 1);
});

test('a committed runner import stays done when automatic source refresh fails', async t => {
  const f = browser(t, 'runner');
  await enterRunnerFields(f, 'eval', 'saved-despite-refresh.csv');
  await f.click('evalCsvUnknownConfirmed');
  await f.click('evalCsvPreview');
  f.net.refreshFailure = true;
  await f.click('evalCsvCommit');
  await until(() => f.stage('evalCsvWizard') === 'done', 'saved source receipt despite refresh failure');
  assert.equal(f.el('evalView').hidden, false);
  assert.match(f.panel('evalCsvWizard', 'done').textContent, /已保存|已入库|导入完成/);
  assert.match(f.panel('evalCsvWizard', 'done').textContent, /catalog temporarily unavailable/);
  assert.equal(f.nav('evalCsvWizard', 'review').disabled, true);
  const commits = f.net.importCalls().filter(call => call.meta.operation === 'commit').length;
  await f.click(f.doneAction('evalCsvWizard'));
  assert.equal(f.net.importCalls().filter(call => call.meta.operation === 'commit').length, commits,
    'refresh recovery cannot repeat the write');
});

test('simulation retains the full file and original labels through explicit review and import', async t => {
  const f = browser(t, 'simulation');
  const file = await enterSimulationReview(f);
  assert.match(f.el('csvPreviewTable').textContent, /keep this note/);
  assert.match(f.el('csvPreviewTable').textContent, /keep-original-event/);
  assert.match(f.panel('csvImportWizard', 'review').textContent, /模拟|独立/);
  assert.deepEqual(f.net.importOperations(), ['inspect']);
  await f.click('csvImport');
  await until(() => f.stage('csvImportWizard') === 'done', 'simulation v0 completion');
  const imported = f.net.importCalls().find(call => call.url.pathname.endsWith('/import'));
  assert.equal(imported.body, file, 'the whole original file, including text and labels, must be uploaded');
  assert.equal(await imported.body.text(), SIMULATION_CSV_TEXT,
    'original notes, 0/1 labels and pre-existing event strings must remain byte-for-byte intact');
  assert.deepEqual(imported.format, {
    csv_encoding: 'gb18030', csv_delimiter: ';', format_confirmed: true
  });
  assert.equal(imported.meta, undefined, 'simulation must not use telemetry source role metadata');
  assert.equal(f.el('csvDatasetSelect').value, 'dataset-original-v0');
  assert.equal(f.el('csvColumn').value, 'ADCS.RW1.speed');
  const before = f.net.calls.length;
  await f.click(f.doneAction('csvImportWizard'));
  assert.equal(f.d.activeElement.id, 'csvColumn');
  assert.equal(f.net.calls.length, before, 'continue must only navigate to fault configuration');
  assert.deepEqual(f.net.importOperations(), ['inspect', 'import']);
});

test('simulation completion survives list refresh failure and never invites a repeated write', async t => {
  const f = browser(t, 'simulation');
  await enterSimulationReview(f);
  f.net.refreshFailure = true;
  await f.click('csvImport');
  await until(() => f.stage('csvImportWizard') === 'done', 'saved simulation receipt');
  assert.match(f.panel('csvImportWizard', 'done').textContent, /已保存|导入完成/);
  assert.match(f.panel('csvImportWizard', 'done').textContent, /workspace refresh temporarily unavailable/);
  assert.equal(f.nav('csvImportWizard', 'review').disabled, true);
  const before = f.net.importCalls().length;
  await f.click(f.doneAction('csvImportWizard'));
  assert.equal(f.net.importCalls().length, before);
});

test('simulation format changes revoke inspection and reviewed approval', async t => {
  const f = browser(t, 'simulation');
  await enterSimulationReview(f);
  await f.click(f.nav('csvImportWizard', 'file'));
  const before = f.net.calls.length;
  f.change('csvDelimiter', ',');
  assert.equal(f.stage('csvImportWizard'), 'file');
  assert.equal(f.el('csvFormatConfirmed').checked, false);
  assert.equal(f.nextFields('csvImportWizard').disabled, true);
  assert.equal(f.nav('csvImportWizard', 'review').disabled, true);
  assert.equal(f.enabled(f.el('csvImport')), false);
  assert.equal(f.net.calls.length, before, 'changing format cannot silently re-upload the file');
});

test('historical async completion waits for its real result and keeps the iframe open for next action', async t => {
  const f = browser(t, 'history', {embedded: true});
  f.net.asynchronous = new Set(['inspect', 'preview', 'commit']);
  const file = await enterHistoryFields(f);
  f.net.held = new Set(['preview', 'commit']);
  await f.click('v2ImpPreview');
  await until(() => [...f.net.tasks.values()].some(task => task.operation === 'preview' && task.waiting),
    'accepted historical preflight task');
  assert.equal(f.stage('v2ImpWizard'), 'fields');
  assert.equal(f.root('v2ImpWizard').getAttribute('aria-busy'), 'true');
  assert.equal(f.enabled(f.el('v2ImpCommit')), false);
  assert.equal(f.messages.some(message => message.type === 'aas:v2-imported'), false);
  f.net.finish('preview');
  await until(() => f.stage('v2ImpWizard') === 'review', 'completed preflight receipt');
  assert.match(f.panel('v2ImpWizard', 'review').textContent, /8 帧/);
  const preview = f.net.importCalls().find(call => call.meta.operation === 'preview');
  assert.equal(preview.body, file);
  assert.equal(preview.meta.config.role, 'test');
  assert.equal(preview.meta.config.unlabeled_test_confirmed, true);
  assert.equal(preview.meta.config.all_normal_confirmed, false);
  await f.click('v2ImpCommit');
  await until(() => [...f.net.tasks.values()].some(task => task.operation === 'commit' && task.waiting),
    'accepted historical commit task');
  assert.equal(f.stage('v2ImpWizard'), 'review', 'HTTP 202 is not an import completion receipt');
  assert.equal(f.messages.some(message => message.type === 'aas:v2-imported'), false);
  f.net.finish('commit');
  await until(() => f.stage('v2ImpWizard') === 'done', 'completed historical write');
  await until(() => f.messages.some(message => message.type === 'aas:v2-imported'),
    'parent import notification');
  assert.equal(f.messages.find(message => message.type === 'aas:v2-imported').keepOpen, true,
    'the historical parent must leave the completion stage visible');
  assert.equal(f.messages.some(message => message.type === 'aas:v2-import-next'), false);
  const commit = f.net.importCalls().find(call => call.meta.operation === 'commit');
  assert.deepEqual(commit.meta.config, preview.meta.config);
  assert.equal(commit.body, file);
  assert.match(f.panel('v2ImpWizard', 'done').textContent, /history\.csv/);
  const before = f.net.calls.length;
  await f.click(f.doneAction('v2ImpWizard'));
  await until(() => f.messages.some(message => message.type === 'aas:v2-import-next'),
    'explicit return-to-browser message');
  assert.equal(f.net.calls.length, before, 'return-to-browser navigation cannot repeat import');
  assert.deepEqual(f.net.importOperations(), ['inspect', 'preview', 'commit']);
});

test('historical mapping changes invalidate a successful preflight before any commit', async t => {
  const f = browser(t, 'history');
  await enterHistoryFields(f);
  await f.click('v2ImpPreview');
  assert.equal(f.stage('v2ImpWizard'), 'review');
  await f.click(f.nav('v2ImpWizard', 'fields'));
  const mapping = f.el('v2ImpMappingRows').querySelector('input[aria-label="ADCS.RW1.speed unit"]');
  assert.ok(mapping, 'real mapping table must expose its editable unit field');
  mapping.value = 'revolutions/s';
  mapping.dispatchEvent(new f.w.Event('input', {bubbles: true}));
  assert.equal(f.stage('v2ImpWizard'), 'fields');
  assert.equal(f.nav('v2ImpWizard', 'review').disabled, true);
  assert.equal(f.enabled(f.el('v2ImpCommit')), false);
  const before = f.net.calls.length;
  assert.equal(f.nav('v2ImpWizard', 'review').disabled, true);
  f.nav('v2ImpWizard', 'review').click();
  await flush();
  assert.equal(f.net.calls.length, before, 'blocked step navigation cannot submit');
  await f.click('v2ImpPreview');
  assert.equal(f.stage('v2ImpWizard'), 'review');
  assert.equal(f.net.importCalls().at(-1).meta.config.column_metadata['ADCS.RW1.speed'].unit,
    'revolutions/s');
  assert.equal(f.net.importCalls().filter(call => call.meta.operation === 'commit').length, 0);
});

test('historical preflight failure stays editable and cannot unlock the completed stage', async t => {
  const f = browser(t, 'history');
  const file = await enterHistoryFields(f);
  f.net.failures.set('preview', 'Historical quality check rejected the source');
  await f.click('v2ImpPreview');
  assert.equal(f.stage('v2ImpWizard'), 'fields');
  assert.equal(f.nav('v2ImpWizard', 'review').disabled, true);
  assert.equal(f.nav('v2ImpWizard', 'done').disabled, true);
  assert.equal(f.el('v2ImpFile').files[0], file);
  assert.equal(f.el('v2ImpNamespace').value, 'history-run-A');
  assert.match(f.el('v2ImpStatus').textContent, /rejected the source/);
  assert.deepEqual(f.net.importOperations(), ['inspect', 'preview']);
});

for (const [role, truthControl] of [
  ['train', 'trainCsvNormalConfirmed'], ['eval', 'evalCsvUnknownConfirmed']
]) {
  test('choosing another ' + role + ' file requires its own truth confirmation', async t => {
    const f = browser(t, 'runner');
    await enterRunnerFields(f, role, 'first-' + role + '.csv');
    await f.click(truthControl);
    assert.equal(f.el(truthControl).checked, true);
    await f.click(f.nav(role + 'CsvWizard', 'file'));
    await f.chooseFile(role + 'CsvFile', 'different-' + role + '.csv');
    assert.equal(f.el(truthControl).checked, false,
      'a truth statement about the previous file cannot authorize the replacement');
    await f.click(role + 'CsvInspect');
    await f.click(role + 'CsvFormatConfirmed');
    await f.click(f.nextFields(role + 'CsvWizard'));
    await f.click(role + 'CsvPreview');
    assert.equal(f.stage(role + 'CsvWizard'), 'fields');
    assert.deepEqual(f.net.importOperations(), ['inspect', 'inspect'],
      'a replacement file needs its own truth approval before preview');
  });
}

function historicalParent(t) {
  const errors = [], calls = [], unexpected = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(fs.readFileSync(path.join(WEB, 'browser.html'), 'utf8'), {
    url: 'https://aas.test/browser.html', runScripts: 'outside-only',
    pretendToBeVisual: true, virtualConsole
  });
  const w = dom.window, d = w.document;
  // jsdom exposes HTMLDialogElement but has no modal top-layer implementation.
  // Only the missing native methods are supplied; the production handler owns
  // deciding whether and when either method is called.
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  const state = {items: []};
  w.fetch = async (url, options = {}) => {
    const parsed = new URL(url, w.location.href);
    calls.push({url: parsed, method: options.method || 'GET'});
    let data;
    if (parsed.pathname === '/api/v2/telemetry/databases') {
      data = {databases: ['active-test.db'], default: 'active-test.db'};
    } else if (parsed.pathname === '/api/v2/telemetry/filter-options') {
      data = {namespaces: [], models: [], subsystems: [], devices: []};
    } else if (parsed.pathname === '/api/v2/telemetry/series/search') {
      data = {items: clone(state.items), count: state.items.length,
        limit: 50, offset: 0, has_more: false, next_offset: null};
    } else {
      unexpected.push((options.method || 'GET') + ' ' + parsed.pathname);
      throw Error('Unexpected historical parent request: ' + parsed.pathname);
    }
    return {ok: true, status: 200, json: async () => data};
  };
  w.eval(fs.readFileSync(path.join(WEB, 'telemetry-v2-browser.js'), 'utf8') +
    '\n//# sourceURL=telemetry-v2-browser.js');
  const dialog = d.getElementById('v2ImportDialog');
  const frame = d.getElementById('v2ImportFrame');
  const send = (data, origin = w.location.origin, source = frame.contentWindow) =>
    w.dispatchEvent(new w.MessageEvent('message', {data, origin, source}));
  t.after(() => {
    dom.window.close();
    assert.deepEqual(unexpected, [], 'parent completion actions may only refresh existing reads');
    assert.deepEqual(errors.map(error => error.message), [], 'parent message handler has no runtime errors');
  });
  return {dom, w, d, dialog, frame, calls, state, send};
}

test('historical parent refreshes on confirmed import but closes only on the explicit next action', async t => {
  const f = historicalParent(t);
  await flush();
  f.d.getElementById('v2OpenImport').click();
  assert.equal(f.dialog.open, true);
  const before = f.calls.length;
  f.state.items = [{
    id: 55, model: 'HISTORY-SAT', subsystem: 'ADCS', device: 'RW1',
    variable: 'newly-imported-speed', namespace: 'history-run-A', unit: 'rad/s',
    segment_count: 1, row_count: 8, time_domains: ['numeric_unspecified'],
    unresolved_role_split: false
  }];
  f.send({type: 'aas:v2-imported', keepOpen: true});
  await flush();
  assert.equal(f.dialog.open, true, 'successful import must leave its completion receipt visible');
  assert.match(f.d.getElementById('v2Candidates').textContent, /newly-imported-speed/,
    'the actual browser must refresh and render the newly available source');
  assert.equal(f.calls.length, before + 2, 'completion refreshes facets and catalog only');
  const afterRefresh = f.calls.length;
  f.send({type: 'aas:v2-import-next'});
  await flush();
  assert.equal(f.dialog.open, false);
  assert.equal(f.d.activeElement.id, 'v2Keyword');
  assert.equal(f.calls.length, afterRefresh, 'explicit next navigation is not another import or search');
});

test('historical parent ignores import and next messages from a foreign origin or another window', async t => {
  const f = historicalParent(t);
  await flush();
  f.d.getElementById('v2OpenImport').click();
  f.d.getElementById('v2CloseImport').focus();
  const initialFocus = f.d.activeElement, before = f.calls.length;
  for (const data of [
    {type: 'aas:v2-imported', keepOpen: true}, {type: 'aas:v2-import-next'}
  ]) {
    f.send(data, 'https://foreign.test', f.frame.contentWindow);
    f.send(data, f.w.location.origin, f.w);
  }
  await flush();
  assert.equal(f.dialog.open, true);
  assert.equal(f.d.activeElement, initialFocus);
  assert.equal(f.calls.length, before, 'untrusted messages cannot refresh or navigate the parent');
});

test('a queued historical file change rejects the old inspection and releases its busy lock', async t => {
  const f = browser(t, 'history');
  await flush();
  const original = await f.chooseFile('v2ImpFile', 'old-pending.csv');
  f.net.asynchronous.add('inspect');
  f.net.held.add('inspect');
  await f.click('v2ImpCsvInspect');
  await until(() => [...f.net.tasks.values()].some(task => task.operation === 'inspect' && task.waiting),
    'pending historical file inspection');
  assert.equal(f.root('v2ImpWizard').getAttribute('aria-busy'), 'true');
  assert.equal(f.enabled(f.el('v2ImpFile')), false);
  // Model a file-picker change queued before the fieldset became disabled.
  // Keep native FileList semantics while deliberately delivering the queued event.
  const picker = f.d.createElement('input'); picker.type = 'file';
  const replacement = new f.w.File([CSV_TEXT], 'new-unchecked.csv', {type: 'text/csv'});
  implForWrapper(picker.files).push(implForWrapper(replacement));
  f.el('v2ImpFile').files = picker.files;
  f.el('v2ImpFile').dispatchEvent(new f.w.Event('change', {bubbles: true}));
  f.net.finish('inspect');
  await flush();
  assert.equal(f.root('v2ImpWizard').getAttribute('aria-busy'), 'false',
    'a stale inspection must release its lock even after its generation was invalidated');
  assert.equal(f.stage('v2ImpWizard'), 'file');
  assert.equal(f.nextFields('v2ImpWizard').disabled, true);
  assert.equal(f.el('v2ImpCsvFormatConfirmed').checked, false);
  assert.equal(f.el('v2ImpFile').files[0], replacement);
  assert.equal(f.enabled(f.el('v2ImpCsvInspect')), true, 'the replacement can be checked explicitly');
  assert.deepEqual(f.net.importOperations(), ['inspect'], 'the queued change cannot upload the new file');
  assert.equal(f.net.importCalls()[0].body, original);
  f.net.held.delete('inspect');
  await f.click('v2ImpCsvInspect');
  assert.equal(f.net.importCalls()[1].body, replacement);
  await f.click('v2ImpCsvFormatConfirmed');
  await f.click(f.nextFields('v2ImpWizard'));
  assert.equal(f.stage('v2ImpWizard'), 'fields');
});

for (const [name, broken, errorPattern, recovery] of [
  ['malformed headers', 'wrong,headers\n1,2\n', /映射表必须包含/, 'replace'],
  ['missing selected column', 'column,model,namespace,subsystem,device,unit,variable\n' +
    'other,HISTORY-SAT,history-run-A,ADCS,RW1,rad\/s,other\n', /未映射/, 'remove']
]) {
  test('historical ' + name + ' remains correctable without losing source or approving import', async t => {
    const f = browser(t, 'history');
    const source = await enterHistoryFields(f);
    await f.click('v2ImpPreview');
    await f.click(f.nav('v2ImpWizard', 'fields'));
    await f.chooseFile('v2ImpMapFile', 'bad-map.csv', broken);
    await until(() => f.root('v2ImpWizard').getAttribute('aria-busy') === 'false', 'mapping failure');
    assert.match(f.el('v2ImpStatus').textContent, errorPattern);
    assert.equal(f.nav('v2ImpWizard', 'review').disabled, true, 'the old preview must be revoked');
    assert.equal(f.el('v2ImpFile').files[0], source);
    assert.equal(f.el('v2ImpNamespace').value, 'history-run-A');
    await f.click('v2ImpCsvFormatConfirmed');
    await f.click(f.nextFields('v2ImpWizard'));
    assert.ok(f.visible(f.el('v2ImpMapFile')) && f.enabled(f.el('v2ImpMapFile')),
      'mapping correction must remain reachable after a binding failure');
    const before = f.net.importCalls().length;
    await f.click('v2ImpPreview');
    assert.equal(f.net.importCalls().length, before, 'an invalid mapping cannot reach the backend preview');
    assert.equal(f.nav('v2ImpWizard', 'review').disabled, true);
    if (recovery === 'replace') {
      await f.chooseFile('v2ImpMapFile', 'corrected-map.csv',
        'column,model,namespace,subsystem,device,unit,variable\n' +
        'ADCS.RW1.speed,HISTORY-SAT,history-run-A,ADCS,RW1,rad/s,speed\n');
    } else {
      await f.click('v2ImpMapClear');
      assert.equal(f.el('v2ImpMapFile').files.length, 0);
    }
    await until(() => f.root('v2ImpWizard').getAttribute('aria-busy') === 'false', 'corrected mapping');
    await f.click('v2ImpCsvFormatConfirmed');
    await f.click(f.nextFields('v2ImpWizard'));
    await f.click('v2ImpPreview');
    assert.equal(f.stage('v2ImpWizard'), 'review', 'the corrected mapping can be reviewed normally');
    assert.equal(f.net.importCalls().filter(call => call.meta.operation === 'commit').length, 0);
  });
}
