'use strict';
// Verify the production V2 runner exposes score-only behavior and keeps
// unrelated labeled evaluation untouched. This is an offline Node contract.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('web/visualization/runner-v2.js','utf8');
const html=fs.readFileSync('web/visualization/runner.html','utf8');
const reports=fs.readFileSync('web/visualization/reports.js','utf8');
assert.ok(reports.includes('仅推理（无真实标签）'),'report list needs explicit unknown-truth label');

for(const route of ['/api/v2/inference/preview','/api/v2/inference/run']){
 assert.ok(src.includes(route),'missing score-only route '+route);
}
assert.ok(src.includes("?'score_only'"),'missing server time coverage for unknown truth');
assert.ok(src.includes('ground_truth_available'),'UI must reflect that truth is unavailable');
assert.ok(html.includes('id="evalTruthControls"'),'missing mode-dependent truth control');
assert.ok(src.includes('refreshEvalSourceOptions()'),'test split changes must refresh source dropdown');
const optionalStart=src.indexOf('function finiteOptional(');
const optionalEnd=src.indexOf('\nfunction renderResultChart(',optionalStart);
assert.ok(optionalStart>=0&&optionalEnd>optionalStart,'score trace must handle missing decision thresholds');
const numericCtx=vm.createContext({Number});
vm.runInContext(src.slice(optionalStart,optionalEnd),numericCtx);
assert.equal(vm.runInContext('Number.isNaN(finiteOptional(null))',numericCtx),true);
assert.equal(vm.runInContext('finiteOptional(0.25)',numericCtx),0.25);

const a=src.indexOf('function isScoreOnlySelection()');
const b=src.indexOf('\nfunction updateEvalRunMode()',a);
assert.ok(a>=0&&b>a,'expected inspectable mode selection helper');
const modeSource=src.slice(a,b);
const ds={runs:[{source_id:11,allowed_role:'test'},
                {source_id:22,allowed_role:'score_only'},
                {source_id:33,allowed_role:'score_only'}],
          features:[{feature_key:'CSV.RW.temp',subsystem:'ADCS',device:'RW1',
                     variable:'temp',unit:'K'},
                    {feature_key:'ADCS.RW1.speed',subsystem:'ADCS',device:'RW1',
                     variable:'speed',unit:'deg/s'}],
          run_features:{22:[{feature_key:'CSV.RW.temp',subsystem:'ADCS',device:'RW1',
                             variable:'temp',unit:'C'},
                            {feature_key:'ADCS.RW1.speed',subsystem:'ADCS',device:'RW1',
                             variable:'speed',unit:'rad/s'}],
                        33:[{feature_key:'CSV.RW.temp',subsystem:'ADCS',device:'RW1',
                             variable:'temp',unit:'K'},
                            {feature_key:'ADCS.RW1.speed',subsystem:'ADCS',device:'RW1',
                             variable:'speed',unit:'deg/s'}]}};
const selectedRuns={train:[],test:[22]};
const roleCatalogs={train:ds,eval:ds};
const catalogFor=role=>role==='train'?roleCatalogs.train:roleCatalogs.eval;
const selectedScoreOnlyFeatures=()=>catalogFor('eval').run_features[String(selectedRuns.test[0])]||[];
const dataSourcePayload=()=>({type:'history'});
const sandbox=vm.createContext({ds,selectedRuns,roleCatalogs,catalogFor,selectedScoreOnlyFeatures,dataSourcePayload,Error});
vm.runInContext(modeSource,sandbox);
assert.equal(vm.runInContext('isScoreOnlySelection()',sandbox),true);
selectedRuns.test=[11];
assert.equal(vm.runInContext('isScoreOnlySelection()',sandbox),false);
selectedRuns.test=[11,22];
assert.throws(()=>vm.runInContext('isScoreOnlySelection()',sandbox),/不能混合/);
selectedRuns.test=[22,33];
assert.throws(()=>vm.runInContext('isScoreOnlySelection()',sandbox),/一个|单个/);

const matchA=src.indexOf('function modelMatchesSelectedSemantics(');
const matchB=src.indexOf('\nasync function refreshModels()',matchA);
assert.ok(matchA>=0&&matchB>matchA,'must compare saved physical meanings');
vm.runInContext(src.slice(matchA,matchB),sandbox);
sandbox.published={feature_keys:['legacy.temperature'],feature_semantics:[['ADCS','RW1','temp','C']]};
assert.equal(vm.runInContext("modelMatchesSelectedSemantics(published,['CSV.RW.temp'])",sandbox),true);
sandbox.published={feature_keys:['legacy.speed'],feature_semantics:[['ADCS','RW1','speed','rad/s']]};
assert.equal(vm.runInContext("modelMatchesSelectedSemantics(published,['ADCS.RW1.speed'])",sandbox),true,
  'selected imported run must override global catalog unit');
selectedRuns.test=[33];
assert.equal(vm.runInContext("modelMatchesSelectedSemantics(published,['ADCS.RW1.speed'])",sandbox),false);
selectedRuns.test=[22];
sandbox.published.feature_semantics=[['ADCS','RW1','temp','K']];
assert.equal(vm.runInContext("modelMatchesSelectedSemantics(published,['CSV.RW.temp'])",sandbox),false);

const evalA=src.indexOf('async function evaluate(){');
const evalB=src.indexOf('\nfunction attempt(',evalA);
assert.ok(evalA>=0&&evalB>evalA,'missing live evaluation handler');
const handler=src.slice(evalA,evalB);
const calls=[],diagnostics=[],elements={evalModel:{value:'5'},runEval:{disabled:false},
                     evalResultEmpty:{hidden:true}};
let archiveAttention=null;
const runCtx=vm.createContext({
 ds,selectedRuns,roleCatalogs,catalogFor,selectedScoreOnlyFeatures,models:[{id:5,integrity:'schema_bound',alignment_method:'exact',tolerance_seconds:0}],
 $:id=>elements[id]||{value:''},
 selection:()=>({source_ids:[...selectedRuns.test],feature_keys:['ADCS.RW1.temp']}),
 clearTaskDiagnostic:()=>{},
 evaluationTarget:()=>({scope:'run'}),dataSourcePayload:()=>({type:'history'}),showResult:()=>{},workspaceNotice:()=>{},
 post:async(path,payload)=>{calls.push({path,payload});return {metrics:{},task_id:'task-score-archive',archive_status:archiveAttention?'failed':'complete',
  archive_attention:archiveAttention,input_contract:{},summary:{result:{rows:[],ground_truth_available:payload.label_target===undefined?false:true}}};},
 message:()=>{},setWorkspace:()=>{},renderResultChart:()=>{},
 renderTaskDiagnostic:(...args)=>diagnostics.push(args),
 window:{AASReportNavigation:{renderLink:()=>{}}},activeTaskIds:{train:null,eval:null},Error,JSON,Number,
 });
vm.runInContext(modeSource+'\nlet evaluationSnapshot=null;\n'+handler,runCtx);
(async()=>{
 selectedRuns.test=[22];
 await vm.runInContext('evaluate()',runCtx);
 assert.equal(calls[0].path,'/api/v2/inference/run');
 assert.equal(calls[0].payload.label_target,undefined);
 assert.deepEqual(calls[0].payload.data_source,{type:'history'});
 assert.deepEqual([...calls[0].payload.dataset_selection.source_ids],[22]);
 assert.equal(elements.runEval.disabled,false);
 selectedRuns.test=[11];
 await vm.runInContext('evaluate()',runCtx);
 assert.equal(calls[1].path,'/api/v2/evaluation/run');
 assert.equal(calls[1].payload.label_target.scope,'run');
 assert.deepEqual(calls[1].payload.data_source,{type:'history'});
 selectedRuns.test=[22];
 archiveAttention={code:'archive_failed',title:'报告未归档',recovery_label:'查看任务记录'};
 await vm.runInContext('evaluate()',runCtx);
 assert.equal(diagnostics[0][1].code,'archive_failed',
   'successful inference with an archive failure must show a separate recovery notice');
 assert.equal(diagnostics[0][2],'task-score-archive');
 console.log('V2 score-only UI contract and evaluation routing: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
