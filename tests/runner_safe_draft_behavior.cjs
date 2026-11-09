'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('web/visualization/runner-v2.js','utf8');
const start=src.indexOf("const RUNNER_DRAFT_KEY='aas.runner.safe-draft.v1'");
const end=src.indexOf('const previewCache=',start);
assert.ok(start>=0&&end>start);
const storage=new Map();
const nodes={};
for(const id of ['trainDataSource','trainUserCsvDataset','trainStart','trainEnd','trainAlgorithm','evalDataSource','evalUserCsvDataset','evalStart','evalEnd','evalSourceModel','evalTargetSplit']){
 nodes[id]={value:'',tagName:id.includes('DataSource')||id.includes('Dataset')||id.includes('Algorithm')||id.includes('SourceModel')||id.includes('TargetSplit')?'SELECT':'INPUT',type:id.includes('Start')||id.includes('End')?'datetime-local':'text',options:[],dataset:{},listeners:{},addEventListener(k,v){this.listeners[k]=v;}};
}
const window={addEventListener(){}};
const context={localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},window,$:id=>nodes[id],Object,JSON,Date,String};
vm.createContext(context);vm.runInContext(src.slice(start,end),context);
// Populate valid current options before restore.
nodes.trainDataSource.options=[{value:'history'},{value:'user_csv'}];
nodes.trainUserCsvDataset.options=[{value:'ds-1'}];
nodes.trainAlgorithm.options=[{value:'cusum'}];
nodes.evalDataSource.options=[{value:'history'},{value:'user_csv'}];
nodes.evalUserCsvDataset.options=[{value:'ds-2'}];
nodes.evalSourceModel.options=[{value:'model-1'}];
nodes.evalTargetSplit.options=[{value:'test'}];
context.restoreRunnerDraft({trainDataSource:'user_csv',trainUserCsvDataset:'ds-1',trainStart:'2026-11-01T10:00',trainAlgorithm:'cusum',evalDataSource:'user_csv',evalUserCsvDataset:'stale',evalSourceModel:'model-1',evalTargetSplit:'test'});
assert.equal(nodes.trainDataSource.value,'user_csv');
assert.equal(nodes.trainUserCsvDataset.value,'ds-1');
assert.equal(nodes.evalUserCsvDataset.value,'');
assert.equal(nodes.evalSourceModel.value,'model-1');
context.bindRunnerDraft();nodes.trainStart.value='2026-11-01T10:00';context.saveRunnerDraft();
const saved=JSON.parse(storage.get('aas.runner.safe-draft.v1'));
assert.equal(saved.version,1);assert.equal(saved.fields.trainStart,'2026-11-01T10:00');
assert.ok(storage.get('aas.runner.safe-draft.v1').length<=4096);
console.log('Runner safe draft restore, stale selection rejection and bounded persistence: PASS');
