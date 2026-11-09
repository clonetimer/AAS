'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/visualization/result-chart.js', 'utf8');
const view = {innerHTML:'', clientWidth:850, clientHeight:320};
const graph = {window: {}};
vm.createContext(graph);
vm.runInContext(source, graph);
const draw = graph.window.AASResultChart;
let result = {rows:[{timestamp:'00:00', value:100},{timestamp:'00:01',value:110}], threshold:[0.1,3], decision_threshold:2.5};
assert.equal(draw.render(view,result),true);
assert.match(view.innerHTML,/异常分数/);
assert.match(view.innerHTML,/判定阈值/);
assert.ok(view.innerHTML.includes('stroke="#e57364"'));
assert.deepEqual([...draw.extract(result).scores],[0.1,3]);
result.decision_threshold = null;
assert.equal(draw.render(view,result),true);
assert.match(view.innerHTML,/阈值不可用/);
assert.ok(!view.innerHTML.includes('stroke="#e57364"'));
result.decision_thresholds=[0.11,0.2];
assert.equal(draw.render(view,result),true);
assert.ok(view.innerHTML.includes('stroke="#e57364"'));
result.rows[0].timestamp='<bad>';
draw.render(view,result);
assert.match(view.innerHTML,/&lt;bad&gt;/);
assert.ok(!view.innerHTML.includes('<bad>'));
result.threshold=[null,3];
assert.equal(draw.render(view,result),true);
const count = 5000;
const pairs = Array.from({length:count},(_,i)=>i===4000?900:0);
const chosen = draw.chooseIndices(pairs,Array(count).fill(4));
assert.ok(chosen.length<=1800);
assert.ok(chosen.includes(4000));
console.log('score + decision threshold render: passed');
// Event bands are grounded in real timestamps and remain separate from scores.
const eventPlot = {
 rows: [{timestamp:0},{timestamp:1},{timestamp:2},{timestamp:3},{timestamp:4}],
 scores: [0.1,0.5,4,0.2,1], decision_threshold: 2,
 pred_labels: [0,0,1,0,0],
 event_overlay: {status:'ok',items:[
  {label:1,start_timestamp:'1',end_timestamp:'3',source_table:'temperature'},
  {label:0,start_timestamp:'3',end_timestamp:'4',source_table:'normal'},
  {label:1,start_timestamp:'15',end_timestamp:'20',source_table:'outside'}]},
 event_metrics: {status:'ok',event_recall:1,fault_events_detected:1,fault_events_evaluable:1,
  median_first_alarm_delay_seconds:1,confirmed_normal_samples:1,confirmed_normal_false_alarms:0}
};
assert.equal(draw.render(view,eventPlot),true);
assert.equal((view.innerHTML.match(/detector-event-band/g)||[]).length,2);
assert.match(view.innerHTML,/事件级召回率/);
assert.match(view.innerHTML,/首次报警延迟/);
const legacy = {...eventPlot,event_overlay:null,event_metrics:null};
assert.equal(draw.render(view,legacy),true);
assert.ok(!view.innerHTML.includes('detector-event-band'));
console.log('event bands, interval clipping and event-level metrics: passed');


// Report search filter runs the real production script in an isolated DOM stub.
class FakeNode {
  constructor(){this.innerHTML='';this.style={};this.value='';this.handlers={};this.children=[];this.textContent='';this.classList={toggle(){},add(){},remove(){}};}
  append(...nodes){this.children.push(...nodes)}
  replaceChildren(...nodes){this.children=[...nodes]}
  querySelectorAll(){return []}
  querySelector(){return null}
  addEventListener(ev,fn){this.handlers[ev]=fn}
  focus(){}
}
const nodes = new Map();
const fakeDoc = {
  getElementById(id){if(!nodes.has(id))nodes.set(id,new FakeNode());return nodes.get(id)},
  createElement(){return new FakeNode()},
  querySelectorAll(){return []},
};
const sandbox = {
  document:fakeDoc,window:{AASResultChart:draw},location:{hash:''},
  console:{...console, error(){}},
  fetch:async(path)=>({ok:true,json:async()=>path.startsWith('/api/experiments?')?
    {items:[]}:{items:[]}}),
  decodeURIComponent,encodeURIComponent,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('web/visualization/reports.js','utf8'),sandbox);
vm.runInContext(`state.experiments=[
 {experiment_id:'2026_T',type:'training',algorithm:'mlp',table:'DEMO_train_temperature',path:'train',success_count:1,result_count:1},
 {experiment_id:'2026_E',type:'evaluation',model_name:'温度检测器',eval_table:'DEMO_test_temperature',path:'eval',success_count:1,result_count:2}
 ];`,sandbox);
const get=(query)=>vm.runInContext(`state.searchText=${JSON.stringify(query)};getFilteredExperiments().map(x=>x.path).join(',')`,sandbox);
assert.equal(get('mlp'),'train');
assert.equal(get('温度检测'),'eval');
assert.equal(get('test_temperature'),'eval');
assert.equal(get('2026_'),'train,eval');
assert.equal(get('unknown'),'');
vm.runInContext(`state.searchText='';state.statusFilter='error'`,sandbox);
assert.equal(vm.runInContext(`getFilteredExperiments()[0].path`,sandbox),'eval');
vm.runInContext(`state.statusFilter='all';state.currentView='cards';`,sandbox);
assert.doesNotThrow(()=>vm.runInContext(`renderCardsView([{detector:'old',status:'success',metrics:{accuracy:0.9}}])`,sandbox));
assert.match(nodes.get('cardsContainer').innerHTML,/未保存异常分数/);
console.log('search by task/model/table/status & legacy record: passed');
