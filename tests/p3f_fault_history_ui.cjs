'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('web/visualization/simulation.html', 'utf8');
const script = fs.readFileSync('web/visualization/simulation-csv-workspace.js', 'utf8');
for (const id of ['csvEventHistory', 'csvAncestorSelect', 'csvCompareButton',
  'csvRollbackButton', 'csvCompareSummary']) {
  assert.ok(html.includes(`id="${id}"`), `Missing P3-F control ${id}`);
}

class Node {
  constructor() {
    this.textContent = ''; this.value = ''; this.hidden = false; this.disabled = false;
    this.children = []; this.handlers = {}; this.className = ''; this.attributes = {};
    this.classList = {add(){}, remove(){}, toggle(){}};
  }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute(name, value) { this.attributes[name] = value; }
}
const nodes = new Map();
const doc = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); },
  createElement() { return new Node(); },
  createElementNS() { return new Node(); },
  querySelectorAll() { return []; },
};
const ids = {current:'c'.repeat(32), parent:'b'.repeat(32), root:'a'.repeat(32),
  event:'e'.repeat(16), rolled:'d'.repeat(32)};
let current = {
  id:ids.current, parent_id:ids.parent, filename:'ui.csv', version:2, frame_count:4,
  numeric_columns:['speed'], csv_format:{encoding:'utf-8',delimiter:','},
  root_sha256:'f'.repeat(64), event_count:1, active_event_count:1, operation_count:0,
};
let timelineStatus = 'active';
const calls = [];
const activeEvent = {event_id:ids.event, sequence:1, status:'active', fault_type:'bias',
  column:'speed', start_frame:2, end_frame:3, magnitude:5, overlaps:[]};
const fetch = async (url, options={}) => {
  calls.push({url, options});
  const payload = options.body ? JSON.parse(options.body) : null;
  if (url.endsWith('/list')) return {ok:true, json:async()=>({items:[current]})};
  if (url.includes('/events?id=')) return {ok:true, json:async()=>({
    dataset_id:current.id, event_count:1, active_event_count:timelineStatus==='active'?1:0,
    events:[{...activeEvent,status:timelineStatus}], operations:[]})};
  if (url.includes('/ancestors?id=')) return {ok:true, json:async()=>({ancestors:[{
    id:ids.parent, filename:'ui.csv', version:1, frame_count:4, active_event_count:0,
  }, {id:ids.root, filename:'ui.csv', version:0, frame_count:4, active_event_count:0}]})};
  if (url.includes('/dataset-preview')) return {ok:true, json:async()=>({version:current.version,
    frame_count:4,column:'speed',events:[],points:[{frame:1,value:10},{frame:2,value:11},
      {frame:3,value:12},{frame:4,value:13}]})};
  if (url.endsWith('/event-retract-preview')) return {ok:true,json:async()=>({
    preview_sha256:'1'.repeat(64),changed_frame_count:2,changed_cell_count:2})};
  if (url.endsWith('/event-retract')) {
    assert.equal(payload.event_id, ids.event);
    timelineStatus='retracted';
    current={...current,id:ids.rolled,parent_id:ids.current,version:3,event_count:1,
      active_event_count:0,operation_count:1};
    return {ok:true,json:async()=>({id:ids.rolled,version:3,parent_id:ids.current})};
  }
  if (url.includes('/compare?')) return {ok:true,json:async()=>({
    frame_count:4,column:'speed',changed_frame_count:2,changed_cell_count:2,
    changed_by_column:{speed:2},changed_ranges:[{start_frame:2,end_frame:3,frame_count:2}],
    events_added:[],events_removed:[],points:[{frame:1,before:10,after:10},
      {frame:2,before:11,after:16},{frame:3,before:12,after:17},{frame:4,before:13,after:13}]})};
  if (url.endsWith('/rollback-preview')) return {ok:true,json:async()=>({
    preview_sha256:'2'.repeat(64),changed_frame_count:2,changed_cell_count:2})};
  if (url.endsWith('/rollback')) {
    assert.equal(payload.ancestor_id, ids.parent);
    current={...current,id:'9'.repeat(32),parent_id:ids.rolled,version:4};
    return {ok:true,json:async()=>({id:current.id,version:4,parent_id:ids.rolled})};
  }
  throw Error('unexpected request: '+url);
};
const scope = {document:doc,window:{confirm:()=>true},console,TextEncoder,btoa,
  encodeURIComponent,fetch};
vm.createContext(scope);
vm.runInContext(script, scope);

(async()=>{
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.ok(nodes.get('csvEventHistory').children.some(child=>
    child.children?.some(item=>item.textContent==='撤销此事件')));
  const eventRow = nodes.get('csvEventHistory').children.find(child=>
    child.children?.some(item=>item.textContent==='撤销此事件'));
  const retractButton = eventRow.children.find(item=>item.textContent==='撤销此事件');
  await retractButton.handlers.click();
  assert.ok(calls.some(call=>call.url.endsWith('/event-retract-preview')));
  assert.ok(calls.some(call=>call.url.endsWith('/event-retract')));
  assert.equal(nodes.get('csvDatasetSelect').value, ids.rolled);

  nodes.get('csvAncestorSelect').value=ids.parent;
  await nodes.get('csvCompareButton').handlers.click();
  assert.match(nodes.get('csvCompareSummary').textContent,/变化 2 帧/);
  assert.ok(calls.some(call=>call.url.includes('/compare?')));

  await nodes.get('csvRollbackButton').handlers.click();
  assert.ok(calls.some(call=>call.url.endsWith('/rollback-preview')));
  assert.ok(calls.some(call=>call.url.endsWith('/rollback')));
  console.log('P3-F timeline, event retract, version compare and ancestor rollback UI: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
