'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync('web/visualization/telemetry-v2-browser.js', 'utf8');
const html = fs.readFileSync('web/visualization/browser.html', 'utf8');

for (const id of ['v2PrevPage', 'v2NextPage', 'v2PageInfo', 'v2ZoomIn', 'v2ZoomOut', 'v2ZoomReset',
  'v2DetailChannel', 'v2LoadDetail', 'v2DetailPrev', 'v2DetailNext', 'v2DetailBody', 'v2DetailStatus']) {
  assert.ok(html.includes(`id="${id}"`), `history browser is missing ${id}`);
}

class Element {
  constructor(tagName = 'div', id = '') {
    this.tagName = tagName.toUpperCase(); this.id = id; this.children = []; this.listeners = new Map();
    this.textContent = ''; this.className = ''; this.disabled = false; this.hidden = false;
    this.value = ''; this.checked = false; this.type = ''; this.dataset = {}; this.style = {};
    this.attributes = {}; this.open = false; this.src = ''; this.href = ''; this.download = '';
    this.classList = {toggle: () => {}, add: () => {}, remove: () => {}};
    this.contentWindow = {};
  }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  async fire(name, event = {}) {
    const result = [];
    for (const callback of this.listeners.get(name) || []) {
      result.push(await callback({target: this, button: 0, pointerId: 1, clientX: 0,
        preventDefault() {}, ...event}));
    }
    return result;
  }
  async click() { if (!this.disabled) return this.fire('click'); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; }
  remove() {}
  removeAttribute(name) { delete this.attributes[name]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  setCustomValidity(value) { this.validationMessage = value; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  scrollIntoView() {}
  getBoundingClientRect() { return {left: 0, top: 0, width: 1060, height: 400}; }
  setPointerCapture() {}
  querySelector(selector) { return selector === 'svg' ? this.svg || null : null; }
  set innerHTML(value) {
    this._innerHTML = String(value);
    this.svg = this._innerHTML.includes('<svg') ? new Element('svg') : null;
  }
  get innerHTML() { return this._innerHTML || ''; }
}

class Option extends Element {
  constructor(text, value) { super('option'); this.textContent = text; this.value = value; }
}

const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, new Element('div', id));
  return elements.get(id);
};
get('v2Browser');
get('v2ImportFrame').dataset.src = 'import.html';

const calls = {search: [], facets: [], queries: [], detail: [], coverage: []};
let deferQueries = false;
let responseDomain = 'numeric_unspecified';
const pendingQueries = [];
function channel(id) {
  return {id, namespace: id <= 50 ? 'mission-A' : 'mission-B', model: 'SAT-A',
    subsystem: 'ADCS', device: `RW-${id}`, variable: 'wheel_speed', unit: 'rpm',
    segment_count: 1, row_count: 100, time_domains: ['numeric_unspecified'],
    unresolved_role_split: false};
}
function chartResponse(frameCount, domain = responseDomain) {
  let startKey=0,endKey=100,first='0',last='100';
  if(domain==='datetime_naive'){
    startKey='2026-01-01 00:00:00.123456';endKey='2026-01-01 00:00:10.123456';
    first=startKey;last=endKey;
  }else if(domain==='datetime_utc'){
    startKey=1767225600123456;endKey=1767225610123456;
    first='2026-01-01T00:00:00.123456Z';last='2026-01-01T00:00:10.123456Z';
  }
  const channelInfo={...channel(51),time_domains:[domain]};
  return {series: [{channel: channelInfo, time_domain: domain,
    points: [
      {time_key: startKey, timestamp: first, value: 1, source_table: 'telemetry', source_ordinal: 0, label: 0},
      {time_key: endKey, timestamp: last, value: 9, source_table: 'telemetry', source_ordinal: 99, label: 1},
    ], frame_count: frameCount, valid_sample_count: frameCount - 2, plot_point_count: 2,
    duplicate_count: 0, truncated: true,
    window: {start_key: startKey, end_key: endKey}, coverage: {start_key: startKey, end_key: endKey}}]};
}
function response(body, status = 200) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function detailResponse(cursor) {
  const first=cursor==='cursor-2'?101:cursor==='cursor-3'?201:1;
  const count=first===201?50:100;
  const points=Array.from({length:count},(_,index)=>{
    const frame=first+index,segment=Math.floor((frame-1)/100)+1;
    return {frame_index:frame,time_key:frame,timestamp:`2026-01-01 00:00:${String(frame%60).padStart(2,'0')}`,
      value:frame,label:frame===150?1:0,source_table:`telemetry-part-${segment}`,
      source_ordinal:(frame-1)%100,segment_id:segment};
  });
  return {series:[{channel:channel(51),time_domain:'numeric_unspecified',points,
    frame_count:250,valid_sample_count:245,matched_count:252,duplicate_count:2,
    plot_point_count:0,page_row_count:count,page_has_more:first<201,
    next_cursor:first===1?'cursor-2':first===101?'cursor-3':null,
    window:{start_key:1,end_key:250},coverage:{start_key:1,end_key:250}}]};
}
async function fetch(url, options = {}) {
  const parsed = new URL(url, 'http://aas.test');
  if (parsed.pathname === '/api/v2/telemetry/databases') {
    return response({databases: ['aas_telemetry_v2.sqlite'], default: 'aas_telemetry_v2.sqlite'});
  }
  if (parsed.pathname === '/api/v2/telemetry/filter-options') {
    calls.facets.push(Object.fromEntries(parsed.searchParams.entries()));
    const ns = parsed.searchParams.get('namespace');
    return response({namespaces: ['mission-A', 'mission-B'], models: ['SAT-A'],
      subsystems: ['ADCS'], devices: ns === 'mission-A' ? ['RW-1', 'RW-2'] : ['RW-51', 'RW-52']});
  }
  if (parsed.pathname === '/api/v2/telemetry/series/search') {
    const offset = Number(parsed.searchParams.get('offset') || 0);
    calls.search.push({offset, limit: Number(parsed.searchParams.get('limit') || 0),
      namespace: parsed.searchParams.get('namespace') || ''});
    const items = Array.from({length: Math.min(50, Math.max(0, 120 - offset))}, (_, i) => channel(offset + i + 1));
    return response({items, count: 120, limit: 50, offset, has_more: offset + items.length < 120,
      next_offset: offset + items.length < 120 ? offset + items.length : null});
  }
  if (parsed.pathname === '/api/v2/telemetry/series/coverage') {
    calls.coverage.push(Object.fromEntries(parsed.searchParams.entries()));
    return response({has_common_window: true, min: '2026-01-01 00:00:00', max: '2026-01-01 00:01:40'});
  }
  if (parsed.pathname === '/api/v2/telemetry/query') {
    const body = JSON.parse(options.body);
    if(body.mode==='page'){
      calls.detail.push(body);
      return response(detailResponse(body.cursor));
    }
    calls.queries.push(body);
    if (deferQueries) return await new Promise(resolve => pendingQueries.push({body, resolve}));
    return response(chartResponse(40));
  }
  if (parsed.pathname === '/api/v2/telemetry/events') return response({items: [], run_annotations: []});
  return response({items: []});
}

const document = {
  getElementById: get,
  createElement: tag => new Element(tag),
  createElementNS: (_ns, tag) => new Element(tag),
  addEventListener() {},
  body: new Element('body'),
};
const window = {addEventListener() {}, location: {origin: 'http://aas.test'}};
const context = vm.createContext({document, window, fetch, Option, URL, URLSearchParams,
  console, Promise, Set, Map, Math, Number, String, Date, Error, JSON, Object, Array,
  setTimeout, clearTimeout});
vm.runInContext(src, context);

const treeText = node => [node.textContent || '', ...(node.children || []).map(treeText)].join(' ');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function waitFor(predicate, message) {
  for (let i = 0; i < 40; i++) { if (predicate()) return; await tick(); }
  assert.fail(message || 'condition did not become true');
}

(async () => {
  await waitFor(() => calls.search.length === 1, 'initial catalog page should load');
  assert.equal(calls.search[0].limit, 50);
  assert.equal(calls.search[0].offset, 0);
  assert.equal(get('v2Count').textContent, '共 120 项');
  assert.match(get('v2PageInfo').textContent, /第 1 \/ 3 页/);

  await get('v2NextPage').click();
  await waitFor(() => calls.search.length === 2 && /第 2 \/ 3 页/.test(get('v2PageInfo').textContent), 'second catalog page should render');
  assert.equal(calls.search[1].offset, 50);
  const pageTwoCheckbox = get('v2Candidates').children[0].children[0];
  assert.equal(pageTwoCheckbox.value, '51');
  pageTwoCheckbox.checked = true;
  await pageTwoCheckbox.fire('change', {target: pageTwoCheckbox});
  await waitFor(() => calls.coverage.length === 1, 'selected channel coverage should load');

  await get('v2NextPage').click();
  await waitFor(() => calls.search.length === 3 && /第 3 \/ 3 页/.test(get('v2PageInfo').textContent), 'third catalog page should render');
  assert.equal(calls.search[2].offset, 100);
  assert.match(treeText(get('v2Selected')), /RW-51/,
    'selected channel must remain visible after moving to another catalog page');
  await get('v2PrevPage').click();
  await waitFor(() => calls.search.length === 4 && /第 2 \/ 3 页/.test(get('v2PageInfo').textContent), 'returning to page two should render');
  assert.equal(get('v2Candidates').children[0].children[0].checked, true,
    'selected channel must remain checked when its page is revisited');

  await get('v2Search').click();
  await waitFor(() => calls.search.length === 5 && /第 1 \/ 3 页/.test(get('v2PageInfo').textContent), 'search should reload catalog on page one');
  assert.equal(calls.search[4].offset, 0, 'search/filter changes must reset catalog to page one');

  get('v2Namespace').value='mission-A';
  await get('v2Namespace').fire('change');
  await waitFor(() => calls.search.length === 6 && calls.facets.length >= 3 && /第 1 \/ 3 页/.test(get('v2PageInfo').textContent),
    'changing a hierarchy filter should reload dependent facets and reset pagination');
  assert.equal(calls.facets.at(-1).namespace, 'mission-A');
  assert.equal(calls.search[5].namespace, 'mission-A');
  assert.deepEqual(get('v2Device').children.slice(1).map(option=>option.value), ['RW-1','RW-2']);

  await get('v2Plot').click();
  await waitFor(() => calls.queries.length === 1, 'initial chart overview should load');
  assert.equal(calls.queries[0].mode, 'overview');
  assert.equal(calls.queries[0].limit, 3000);
  assert.match(get('v2PlotMeta').textContent, /逻辑帧 40 · 有效样本 38 · 绘图点 2 · 重复记录 0/);

  await get('v2ZoomIn').click();
  await waitFor(() => calls.queries.length === 2 && get('v2ZoomStatus').textContent !== '正在读取窗口…', 'zoom button should request and render a new server window');
  assert.equal(calls.queries[1].start, 14);
  assert.equal(calls.queries[1].end, 86);
  assert.match(get('v2ZoomStatus').textContent, /已放大/);

  const svg = get('v2Chart').svg;
  assert.ok(svg, 'chart must render an SVG interaction surface');
  await svg.fire('pointerdown', {clientX: 105 + 0.2 * (1060 - 105 - 24)});
  await svg.fire('pointerup', {clientX: 105 + 0.6 * (1060 - 105 - 24)});
  await waitFor(() => calls.queries.length === 3 && get('v2ZoomStatus').textContent !== '正在读取窗口…', 'drag selection should request and render a new server window');
  assert.ok(calls.queries[2].start > calls.queries[1].start);
  assert.ok(calls.queries[2].end < calls.queries[1].end);

  await get('v2ZoomReset').click();
  await waitFor(() => calls.queries.length === 4 && get('v2ZoomStatus').textContent !== '正在读取窗口…', 'reset should restore the original range');
  assert.equal(calls.queries[3].start, 0);
  assert.equal(calls.queries[3].end, 100);

  const wheelSvg=get('v2Chart').svg;
  await wheelSvg.fire('wheel',{deltaY:-1,clientX:105+0.5*(1060-105-24)});
  await waitFor(() => calls.queries.length === 5 && get('v2ZoomStatus').textContent !== '正在读取窗口…',
    'wheel zoom should request a server-side time window');
  assert.equal(calls.queries[4].start,14);
  assert.equal(calls.queries[4].end,86);

  responseDomain='datetime_naive';
  await get('v2Plot').click();
  await waitFor(() => calls.queries.length === 6 && get('v2ZoomStatus').textContent !== '正在读取窗口…',
    'naive datetime overview should load');
  await get('v2ZoomIn').click();
  await waitFor(() => calls.queries.length === 7 && get('v2ZoomStatus').textContent !== '正在读取窗口…',
    'naive datetime zoom should reload from the server');
  assert.match(calls.queries[6].start, /^2026-01-01T00:00:01\.\d{6}$/);
  assert.match(calls.queries[6].end, /^2026-01-01T00:00:08\.\d{6}$/);

  responseDomain='datetime_utc';
  await get('v2Plot').click();
  await waitFor(() => calls.queries.length === 8 && get('v2ZoomStatus').textContent !== '正在读取窗口…',
    'UTC datetime overview should load');
  await get('v2ZoomIn').click();
  await waitFor(() => calls.queries.length === 9 && get('v2ZoomStatus').textContent !== '正在读取窗口…',
    'UTC datetime zoom should reload from the server');
  assert.match(calls.queries[8].start, /^2026-01-01T00:00:01\.\d{6}Z$/);
  assert.match(calls.queries[8].end, /^2026-01-01T00:00:08\.\d{6}Z$/);

  // The browser must ignore a late response from an older chart request.
  deferQueries = true;
  const firstPlot = get('v2Plot').click();
  await waitFor(() => pendingQueries.length === 1, 'first deferred chart request should start');
  const secondPlot = get('v2Plot').click();
  await waitFor(() => pendingQueries.length === 2, 'second deferred chart request should start');
  pendingQueries[1].resolve(response(chartResponse(22)));
  await secondPlot;
  pendingQueries[0].resolve(response(chartResponse(11)));
  await firstPlot;
  assert.match(get('v2PlotMeta').textContent, /逻辑帧 22/,
    'late older chart response must not overwrite the newest query result');

  const staleAfterFilter=get('v2Plot').click();
  await waitFor(() => pendingQueries.length === 3, 'chart request before filter change should start');
  get('v2Namespace').value='mission-B';
  await get('v2Namespace').fire('change');
  await waitFor(() => calls.search.length === 7 && /第 1 \/ 3 页/.test(get('v2PageInfo').textContent),
    'filter change should invalidate the in-flight chart and reload the catalog');
  pendingQueries[2].resolve(response(chartResponse(7)));
  await staleAfterFilter;
  assert.match(get('v2PlotMeta').textContent, /逻辑帧 22/,
    'a chart request started before a filter change must not overwrite the current chart');

  assert.equal(get('v2DetailChannel').value, '51', 'detail view should target one currently selected channel');
  await get('v2LoadDetail').click();
  await waitFor(() => calls.detail.length===1 && get('v2DetailBody').children.length===100,
    'first detail page should render at most 100 logical frames');
  assert.equal(calls.detail[0].mode,'page');
  assert.deepEqual(calls.detail[0].channel_ids,[51]);
  assert.equal(calls.detail[0].limit,100);
  assert.match(get('v2DetailStatus').textContent,/逻辑帧 1–100 \/ 250/);
  assert.match(treeText(get('v2DetailBody')),/telemetry-part-1/);
  assert.match(treeText(get('v2DetailBody')),/第 1 行/);
  assert.equal(get('v2DetailPrev').disabled,true);

  await get('v2DetailNext').click();
  await waitFor(() => calls.detail.length===2 && get('v2DetailBody').children[0]?.children[0]?.textContent==='101',
    'next detail page should continue at logical frame 101');
  assert.equal(calls.detail[1].cursor,'cursor-2');
  assert.match(treeText(get('v2DetailBody')),/telemetry-part-2/);
  assert.match(treeText(get('v2DetailBody')),/第 1 行/);
  assert.equal(get('v2DetailPrev').disabled,false);

  await get('v2DetailNext').click();
  await waitFor(() => calls.detail.length===3 && get('v2DetailBody').children.length===50,
    'last detail page should contain the remaining 50 frames');
  assert.equal(calls.detail[2].cursor,'cursor-3');
  assert.match(get('v2DetailStatus').textContent,/逻辑帧 201–250 \/ 250/);
  assert.equal(get('v2DetailNext').disabled,true);

  await get('v2DetailPrev').click();
  await waitFor(() => calls.detail.length===4 && get('v2DetailBody').children[0]?.children[0]?.textContent==='101',
    'previous detail page should return to frame 101');
  assert.equal(calls.detail[3].cursor,'cursor-2');

  await get('v2DetailPrev').click();
  await waitFor(() => calls.detail.length===5 && get('v2DetailBody').children[0]?.children[0]?.textContent==='1',
    'previous detail page should return to the first frame');
  assert.equal(Object.hasOwn(calls.detail[4],'cursor'),false);
  assert.equal(get('v2DetailPrev').disabled,true);

  console.log('P3-E browser catalog, chart windows, latest-request-wins and single-channel detail cursor UI: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
