'use strict';
// Invoked by LOCAL_TEST/run_csv_wizard_acceptance.py; intentionally outside npm test's glob.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {JSDOM, CookieJar, VirtualConsole} = require('jsdom');
const {implForWrapper} = require('jsdom/lib/jsdom/living/generated/utils');
const [base, fixturePath, reportPath] = process.argv.slice(2);
const inputs = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
const jar = new CookieJar(), calls = [], errors = [], flows = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(test, description, diagnostics = () => '') {
  const deadline = Date.now() + 25000;
  while (!test()) {
    if (Date.now() > deadline) throw Error(description + ': ' + diagnostics());
    await delay(25);
  }
}
async function http(url, init = {}) {
  const target = new URL(url, base), headers = new Headers(init.headers || {});
  const cookie = jar.getCookieStringSync(target.href);
  if (cookie) headers.set('Cookie', cookie);
  const response = await fetch(target, {...init, headers});
  for (const value of response.headers.getSetCookie?.() || [response.headers.get('set-cookie')].filter(Boolean))
    jar.setCookieSync(value, target.href);
  calls.push({method: init.method || 'GET', path: target.pathname, status: response.status});
  return response;
}
async function authenticate() {
  const body = JSON.stringify({username: 'acceptance', password: crypto.randomBytes(20).toString('hex')});
  const anonymous = await http('/api/v2/run-dataset/catalog');
  assert.equal(anonymous.status, 401, 'business APIs must require authentication');
  for (const action of ['setup', 'login']) {
    const response = await http('/api/auth/' + action, {method: 'POST', headers: {'Content-Type': 'application/json'}, body});
    assert.equal(response.status, 200, await response.text());
  }
  assert.equal((await (await http('/api/auth/status')).json()).authenticated, true);
}
function browserMethods(w) {
  w.TextEncoder = TextEncoder;
  w.confirm = () => true;
  w.HTMLElement.prototype.scrollIntoView = function () {};
  const read = (blob, kind) => new Promise((resolve, reject) => {
    const reader = new w.FileReader(); reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error); reader[kind](blob);
  });
  w.Blob.prototype.text ||= function () { return read(this, 'readAsText'); };
  // jsdom lacks fetch; supply real HTTP with the same authenticated cookie jar.
  w.fetch = async (url, init = {}) => {
    const body = init.body instanceof w.Blob ? Buffer.from(await read(init.body, 'readAsArrayBuffer')) : init.body;
    return http(new URL(url, w.location.href), {...init, body});
  };
  // Observe genuine jsdom XHR without replacing its transport or response.
  const proto = w.XMLHttpRequest.prototype, open = proto.open, send = proto.send, header = proto.setRequestHeader;
  proto.open = function (method, url, ...rest) {
    this.acceptance = {method, path: new URL(url, w.location.href).pathname};
    return open.call(this, method, url, ...rest);
  };
  proto.setRequestHeader = function (name, value) {
    if (name.toLowerCase() === 'x-aas-import-metadata')
      this.acceptance.operation = JSON.parse(Buffer.from(value, 'base64').toString('utf8')).operation;
    return header.call(this, name, value);
  };
  proto.send = function (body) {
    const record = this.acceptance; calls.push(record);
    this.addEventListener('loadend', () => {
      record.status = this.status;
      try { record.task_id = JSON.parse(this.responseText).task_id; } catch (_) {}
    });
    return send.call(this, body);
  };
}
async function exercise(purpose) {
  const runner = ['train', 'eval'].includes(purpose), simulation = purpose === 'simulation';
  const page = runner ? 'runner.html' : simulation ? 'simulation.html' : 'import.html';
  const vc = new VirtualConsole(); vc.on('jsdomError', error => errors.push({purpose, message: error.message}));
  const dom = await JSDOM.fromURL(base + '/' + page, {cookieJar: jar, resources: 'usable',
    runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc, beforeParse: browserMethods});
  const w = dom.window, d = w.document, el = id => d.getElementById(id);
  const rootId = runner ? purpose + 'CsvWizard' : simulation ? 'csvImportWizard' : 'v2ImpWizard';
  const prefix = runner ? purpose + 'Csv' : simulation ? 'csv' : 'v2Imp';
  const status = () => el(runner ? prefix + 'Status' : simulation ? 'csvImportStatus' : 'v2ImpStatus')?.textContent || d.body.textContent;
  const stage = () => el(rootId)?.querySelector('[aria-current="step"]')?.dataset.csvGo;
  async function click(node) {
    if (typeof node === 'string') node = el(node);
    assert.ok(node && !node.matches(':disabled') && !node.closest('[hidden]'), 'usable control ' + node?.id);
    node.click(); await delay(0);
  }
  const change = (id, value) => { const node = el(id); node.value = value;
    node.dispatchEvent(new w.Event(node.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); };
  try {
    await until(() => d.readyState === 'complete' && stage() === 'file', 'page initialization', status);
    if (runner) {
      await until(() => el('v2Connection').textContent.includes('已连接'), 'runner catalog', status);
      await click(purpose === 'train' ? 'tabTrain' : 'tabEval');
      await click(purpose === 'train' ? 'openTrainCsvDialog' : 'openEvalCsvDialog');
      assert.equal(el(purpose + 'CsvDialog').open, true, 'CSV import dialog is open');
    } else if (simulation) {
      await click('openSimCsvDialog');
      assert.equal(el('simCsvDialog').open, true, 'simulation CSV import dialog is open');
    }
    const fixture = inputs[purpose], picker = d.createElement('input'); picker.type = 'file';
    const file = new w.File([fixture.text], fixture.name, {type: 'text/csv'}), list = picker.files;
    implForWrapper(list).push(implForWrapper(file)); el(prefix + 'File').files = list;
    const uploadsBefore = calls.filter(c => c.method === 'POST').length;
    el(prefix + 'File').dispatchEvent(new w.Event('change', {bubbles: true})); await delay(50);
    assert.equal(calls.filter(c => c.method === 'POST').length, uploadsBefore, 'file selection must not upload');
    await click(purpose === 'history' ? 'v2ImpCsvInspect' : prefix + 'Inspect');
    const confirm = purpose === 'history' ? 'v2ImpCsvFormatConfirmed' : prefix + 'FormatConfirmed';
    await until(() => !el(confirm).disabled && el(rootId).getAttribute('aria-busy') === 'false' &&
      (simulation || el(prefix + (runner ? 'Columns' : 'MeasureColumns')).children.length), 'inspection', status);
    await click(confirm); await click(el(rootId).querySelector('[data-csv-continue]'));
    assert.equal(stage(), 'fields');
    if (purpose === 'history') {
      for (const [id, value] of Object.entries({v2ImpNamespace:'acceptance-history', v2ImpModel:'HISTORY-LIVE',
        v2ImpRole:'test', v2ImpTimeDomain:'numeric_unspecified', v2ImpTimeReference:'history-clock'})) change(id, value);
    } else if (runner) {
      change(prefix + 'Model', purpose.toUpperCase() + '-LIVE'); change(prefix + 'Reference', purpose + '-clock');
      change(prefix + 'Unit', 'rad/s'); await click(purpose === 'train' ? 'trainCsvNormalConfirmed' : 'evalCsvUnknownConfirmed');
    }
    await click(simulation ? 'csvReview' : prefix + 'Preview');
    await until(() => stage() === 'review' && el(rootId).getAttribute('aria-busy') === 'false', 'preview', status);
    const priorCommits = calls.filter(c => c.operation === 'commit' || c.path === '/api/v2/csv-workspace/import').length;
    await click(simulation ? 'csvImport' : prefix + 'Commit');
    await until(() => stage() === 'done' && el(rootId).getAttribute('aria-busy') === 'false', 'import completion', status);
    assert.equal(calls.filter(c => c.operation === 'commit' || c.path === '/api/v2/csv-workspace/import').length, priorCommits + 1);
    const receipt = el(rootId).querySelector('[data-csv-stage="done"]').textContent;
    assert.ok(receipt.includes(fixture.name), receipt);
    if (runner) {
      const selected = el(purpose === 'train' ? 'trainRuns' : 'evalRuns').querySelector('input:checked');
      assert.ok(selected, 'committed runner source is selected: ' + status());
      const catalog = await (await http('/api/v2/run-dataset/catalog')).json();
      assert.equal(catalog.runs.find(run => run.source_id === Number(selected.value)).allowed_role,
        purpose === 'train' ? 'train' : 'score_only');
      if (purpose === 'eval') assert.equal(el('evalTruthControls').hidden, true);
    }
    await delay(350);
    flows.push({purpose, filename:fixture.name, stage:stage(), receipt});
    console.log(purpose + ': done');
  } finally { dom.window.close(); }
}
(async () => {
  const report = {status:'FAIL', transport:'real authenticated HTTP, jsdom XHR and task polling', flows};
  try {
    await authenticate();
    for (const purpose of ['history', 'simulation', 'train', 'eval']) await exercise(purpose);
    const allowedPosts = new Set(['/api/auth/setup','/api/auth/login','/api/v2/telemetry/import-source',
      '/api/v2/csv-workspace/inspect','/api/v2/csv-workspace/import','/api/v2/run-dataset/coverage']);
    assert.deepEqual(calls.filter(c => c.method === 'POST' && !allowedPosts.has(c.path)), [], 'no automatic algorithm/fault submission');
    assert.equal(calls.filter(c => c.task_id && c.status === 202).length, 9, 'all source operations used real async tasks');
    assert.deepEqual(calls.filter(c => !Number.isFinite(c.status) || c.status < 200 || c.status >= 300),
      [{method:'GET', path:'/api/v2/run-dataset/catalog', status:401}], 'only the deliberate unauthenticated request may fail');
    assert.deepEqual(errors, [], 'page script/resource errors'); report.status = 'PASS';
  } catch (error) { report.error = error.stack; console.error(error.stack); process.exitCode = 1; }
  finally { report.requests = calls; report.dom_errors = errors;
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); }
})();
