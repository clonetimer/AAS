/* Execute the production selector functions in a minimal DOM harness, without browser rendering. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const src = fs.readFileSync('web/visualization/runner.js', 'utf8');
function part(start, stop) {
  const a = src.indexOf(start), b = src.indexOf(stop, a + start.length);
  assert(a >= 0 && b > a, `Missing function ${start}`);
  return src.slice(a, b);
}
const funcs = [
  part('function validTrainingTables(', 'function invalidateTrainingResult('),
  part('async function loadDbTables()', 'async function loadEvalTableRows()'),
  part('function showEvaluationDomainDifferences()', 'async function refreshTrainTimeRange()'),
  part('function updateTrainTestOptions(', 'function updatePreprocessingModuleVisibility()'),
].join('\n');
const ident = (variable) => ({model:'DEMO', subsystem:'ADCS', device:'unit', variable});
const record = (name, role, variable, single = true) => ({
  name, data_role:role, role_valid:true, is_single_measurement:single,
  columns:['timestamp','value','label'], hierarchy:[ident(variable)], row_count:10,
});
const trainA = record('train_temp','train','temperature');
const testA = record('test_temp','test','temperature');
const testB = record('test_current','test','current');
const tableSets = {
  'db_a': [trainA, record('wide_train','train','temperature',false), testA, testB],
  'db_b': [record('train_b','train','temperature'), record('test_b','test','temperature')],
};
const el = (value = '') => ({value, textContent:'', checked:false,
  classList:{add(){},remove(){}}, replaceChildren(){},
  addEventListener(){}, append(){}, prepend(){}, style:{}});
const els = {
  dbSelect:el('db_a'), evalDbSelect:el('db_a'),
  tableSelect:el(), trainTableSelect:el(), testTableSelect:el(),
  evalTableSelect:el(), runOutput:el(), evalOutput:el(),
  dataViewRadios:[el('raw')], evalDataViewRadios:[el('raw'), el('clean')],
  modelFilter:el(),subsystemFilter:el(),deviceFilter:el(),variableFilter:el(),
  evalDomainHint:el(),
};
const state = {databases:[{value:'db_a'},{value:'db_b'}],selectedDb:null,selectedTrainTable:null,selectedTestTable:null,
  selectedModel:null,evalSelectedTable:'',extraEvaluationTables:[],
  evalDataView:'raw',evalTableRevision:0,trainTableRevision:0,evalCandidates:[],
  extraTrainingTables:[],evaluationMode:'rigorous', filters:{},
};
function parse(url) {const p=new URL(url,'http://local');return {pathname:p.pathname, args:p.searchParams};}
const ctx = vm.createContext({state,els, console, Set, String, Number, Option:class{constructor(text,value){this.text=text;this.value=value;}},
  renderSelectOptions: (e, items, selected='') => {
    if (!e) return;
    e.options=items; e.value = items.find(x=>x.value===selected)?.value || items[0]?.value || '';
  },
  formatRoleTableOption: (item) => item.name,
  invalidateTrainingResult:()=>{}, invalidateEvaluationResult:()=>{},
  resetEvaluationResultView:()=>{}, renderAdditionalEvalTables:()=>{},
  loadEvalTableRows:async()=>{},loadTableRows:async()=>{},
  api:async(url)=> {
    const {pathname,args}=parse(url);
    if(pathname==='/api/sqlite-tables') return {items:tableSets[args.get('db')]};
    if(pathname==='/api/hierarchy-options') return {models:[],subsystems:[],devices:[],variables:[]};
    if(pathname==='/api/model-input-sources') return ctx.modelContract;
    throw new Error(url);
  },
});
vm.runInContext(funcs,ctx);
const call=(code)=>vm.runInContext(code,ctx);
(async()=>{
  await call('loadDbTables()');
  assert.deepEqual(els.tableSelect.options.map(t=>t.value),['train_temp']);
  assert.deepEqual(els.trainTableSelect.options.map(t=>t.value),['train_temp']);
  assert.deepEqual(els.testTableSelect.options.map(t=>t.value),['test_temp','test_current']);
  els.dbSelect.value='db_b';
  await call('loadDbTables()');
  assert.equal(state.selectedTrainTable,'train_b');
  assert.equal(state.selectedTestTable,'test_b');
  assert.deepEqual(els.trainTableSelect.options.map(t=>t.value),['train_b']);
  assert.deepEqual(els.testTableSelect.options.map(t=>t.value),['test_b']);
  state.selectedModel={id:20}; els.evalDbSelect.value='db_a';
  ctx.modelContract={complete:true,data_view:'clean',sources:[
    {table:'train_temp',identity:ident('temperature')},
    {table:'train_current',identity:ident('current')}],};
  await call('loadEvalTables()');
  assert.deepEqual(els.evalTableSelect.options.map(t=>t.value),['test_temp','test_current']);
  assert.equal(state.evalSelectedTable,'test_temp');
  assert.deepEqual(state.extraEvaluationTables,['test_current']);
  assert.equal(state.evalDataView,'clean');
  ctx.modelContract={complete:true,data_view:'raw',sources:[{table:'train_temp',identity:ident('pressure')}]};
  await call('loadEvalTables()');
  assert.equal(state.evalSelectedTable,''); // no misleading auto match
  assert.deepEqual(els.evalTableSelect.options.map(t=>t.value),['test_temp','test_current']); // selectable manually
  assert.equal(state.extraEvaluationTables.length,0);
  ctx.modelContract={complete:false,data_view:'raw',sources:[{table:'old',identity:null}]};
  await call('loadEvalTables()');
  assert.deepEqual(els.evalTableSelect.options.map(t=>t.value),['test_temp','test_current']);
  assert(els.evalOutput.textContent.includes('缺少可核验'));
  tableSets.db_a = [trainA,
    {...testA, name:'test_normal_only', role_valid:false, normal_count:8, fault_count:0, unknown_label_count:0},
    {...testA, name:'test_fault_only', role_valid:false, normal_count:0, fault_count:8, unknown_label_count:0}];
  els.evalLogicalSeries = el();
  els.evalLogicalSeries.checked = true;
  state.selectedModel = null;
  await call('loadEvalTables()');
  assert.deepEqual(els.evalTableSelect.options.map(t => t.value), ['test_normal_only', 'test_fault_only']);
  els.evalLogicalSeries.checked = false;
  await call('loadEvalTables()');
  assert.deepEqual(els.evalTableSelect.options.map(t => t.value), []);
  console.log('Runner selector behavior: 5 scenarios passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
