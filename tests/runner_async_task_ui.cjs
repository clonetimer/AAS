'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync('web/visualization/runner-v2.js','utf8');
const html = fs.readFileSync('web/visualization/runner.html','utf8');
for (const id of ['cancelTrainTask','cancelEvalTask','evalProgressTab','evalProgressPanel','evalProgressBar']) {
  assert.ok(html.includes(`id="${id}"`), `runner page is missing ${id}`);
}

function extract(startText,endText){
  const start=src.indexOf(startText); const end=src.indexOf(endText,start);
  assert.ok(start>=0&&end>start,`missing ${startText}`);
  return src.slice(start,end);
}
const helpers=extract('function setTrainingProgress(', 'function finiteOptional(') +
  (src.includes('function updateTaskProgress(') ? extract('function updateTaskProgress(', 'async function train()') : '');
const trainCode=extract('async function train(){','async function publish()');
const evalCode=extract('async function evaluate(){','function attempt(');

function node(){return {value:0,textContent:'',disabled:false,hidden:false,replaceChildren(){},append(){},classList:{add(){},remove(){}}};}
function contextFor(waitImpl){
  const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  const waitCalls=[];const cancelCalls=[];const posts=[];const diagnostics=[];
  const context={
    window:{AASTasks:{
      wait:async(id,options)=>{waitCalls.push({id,options});return waitImpl(id,options);},
      cancel:async id=>{cancelCalls.push(id);return {accepted:true,status:'cancelling'};},
    },AASReportNavigation:{renderLink:()=>''}},
    document:{getElementById:$,createElement:node},
    $,console,Date,Math,Number,String,Object,Promise,Error,JSON,
    activeTaskIds:{train:null,eval:null},latest:null,trainingSnapshot:null,
    selection:()=>({method:'exact',runs:[]}),collectParameters:()=>({}),
    models:[{id:'m1',integrity:'schema_bound',alignment_method:'exact',tolerance_seconds:0}],
    isScoreOnlySelection:()=>false,evaluationTarget:()=>({scope:'run'}),
    setTimeout:callback=>{callback();return 1;},
    post:async(path,payload)=>{posts.push({path,payload});return {status:'accepted',task_id:path.includes('training')?'train-task-1':'eval-task-1'};},
    api:async()=>({}),setWorkspace:()=>{},workspaceNotice:()=>{},
    message:(role,data)=>{if(role==='eval')$('evalResultSummary').textContent=JSON.stringify(data.metrics||{});},
    clearTaskDiagnostic:()=>{},renderTaskDiagnostic:(...args)=>diagnostics.push(args),renderResultChart:()=>{},
    showResult:()=>{},refreshModels:async()=>{},
  };
  vm.createContext(context);
  vm.runInContext(helpers+'\n'+trainCode+'\n'+evalCode,context);
  return {context,$,waitCalls,cancelCalls,posts,diagnostics};
}

(async()=>{
  const trainingResult={experiment_id:'exp-train',metrics:{f1:0.91},archive_status:'complete'};
  const training=contextFor(async(_id,options)=>{
    options.onUpdate({status:'running',progress:37,message:'算法训练中',cancellable:true});
    return {status:'completed',progress:100,result:trainingResult};
  });
  training.$('trainAlgorithm').value='zscore';
  training.$('trainRatio').value='0.8';
  training.$('trainNormalization').value='min_max';
  await training.context.train();
  assert.equal(training.waitCalls[0].id,'train-task-1');
  assert.equal(training.$('trainProgressBar').value,100);
  assert.equal(training.context.activeTaskIds.train,null);
  assert.equal(training.$('cancelTrainTask').disabled,true);

  const evaluationResult={status:'success',model_id:'m1',experiment_id:'exp-eval',metrics:{f1:0.88},archive_status:'complete'};
  const evaluation=contextFor(async(_id,options)=>{
    options.onUpdate({status:'running',progress:64,message:'正在执行监督评估',cancellable:true});
    return {status:'completed',progress:100,result:evaluationResult,attention:null};
  });
  evaluation.$('evalModel').value='m1';
  await evaluation.context.evaluate();
  assert.equal(evaluation.waitCalls[0].id,'eval-task-1');
  assert.equal(evaluation.$('evalProgressBar').value,100);
  assert.equal(evaluation.context.activeTaskIds.eval,null);
  assert.equal(evaluation.$('cancelEvalTask').disabled,true);
  assert.match(evaluation.$('evalResultSummary').textContent,/f1/);

  const archiveAttention={code:'archive_failed',title:'报告待处理'};
  const archivedEvaluation=contextFor(async()=>({status:'completed',progress:100,
    result:{status:'partial',model_id:'m1',archive_status:'failed',metrics:{f1:0.87}},attention:archiveAttention}));
  archivedEvaluation.$('evalModel').value='m1';
  await archivedEvaluation.context.evaluate();
  assert.equal(archivedEvaluation.diagnostics[0][1].code,'archive_failed',
    'task-level archive attention must survive unwrapping the task result');

  const cancellation=contextFor(async()=>({status:'completed',result:{}}));
  cancellation.context.activeTaskIds.train='train-running';
  await cancellation.context.cancelActiveTask('train');
  assert.deepEqual(cancellation.cancelCalls,['train-running']);
  assert.match(cancellation.$('trainProgressLabel').textContent,/取消/);
  cancellation.context.activeTaskIds.eval='eval-running';
  await cancellation.context.cancelActiveTask('eval');
  assert.deepEqual(cancellation.cancelCalls,['train-running','eval-running']);
  assert.match(cancellation.$('evalProgressLabel').textContent,/取消/);
  const cancelledEvaluation=contextFor(async()=>({status:'cancelled',progress:30,message:'任务已取消'}));
  cancelledEvaluation.$('evalModel').value='m1';
  await assert.rejects(()=>cancelledEvaluation.context.evaluate(),/取消/);
  assert.equal(cancelledEvaluation.context.activeTaskIds.eval,null);
  console.log('Training/evaluation task polling, progress and confirmed cancellation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
