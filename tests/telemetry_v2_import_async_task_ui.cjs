'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const importedColumn = {source_column:'adcs.rw.speed',subsystem:'ADCS',device:'RW1',variable:'speed',unit:'rad/s'};
const results = {
  inspect:{format:'csv',columns:['time_s','adcs.rw.speed','label'],has_labels:true,
    csv_encoding:'utf-8',csv_delimiter:',',format_review_required:true,
    numeric_columns:['adcs.rw.speed'],sampled_rows:2,uncompressed_bytes:128},
  preview:{status:'preview',row_count:2,column_count:1,columns:[importedColumn],
    time_start:'0',time_end:'1',label_status:'provided',sha256:'a'.repeat(64),
    quality:{label_counts:{'0':1,'1':1,unknown:0},missing_by_column:{}},import_context:{}},
  commit:{status:'imported',row_count:2,column_count:1,columns:[importedColumn],source_id:17},
};

class Element {
  constructor(tag='') {
    this.tag=tag;this.value='';this.textContent='';this.hidden=false;this.disabled=false;
    this.checked=false;this.required=false;this.files=[];this.children=[];this.handlers={};
    this.style={};this.classList={toggle(){},add(){},remove(){}};
    this.parentNode={insertBefore(){}};
  }
  addEventListener(type,handler) { this.handlers[type]=handler; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); return item; }
  replaceChildren(...items) { this.children=[...items]; }
  querySelectorAll() { return []; }
  setAttribute(name,value) { this[name]=value; }
  reportValidity() { return true; }
}

function setup() {
  const app={nodes:new Map(),requests:[],waits:[],cancels:[],events:[],inserted:[],sync:false};
  const document={
    getElementById(id) {
      if(!app.nodes.has(id))app.nodes.set(id,new Element(id));
      return app.nodes.get(id);
    },
    createElement:tag=>new Element(tag),
    dispatchEvent:event=>app.events.push(event.type),
  };
  const $=id=>document.getElementById(id);
  $('v2ImpStatus').parentNode={insertBefore:node=>app.inserted.push(node)};
  for(const [id,value] of Object.entries({v2ImpMode:'table',v2ImpCsvEncoding:'auto',
    v2ImpCsvDelimiter:'auto',v2ImpModel:'SAT-X',v2ImpNamespace:'run-A',v2ImpRole:'test',
    v2ImpTimeDomain:'numeric_unspecified',v2ImpTimeReference:'run-A',v2ImpTimeUnit:'s'}))$(id).value=value;
  $('v2ImpFile').files=[{name:'现场.csv',size:128}];
  class XHR {
    constructor() {
      this.events={};this.headers={};this.status=0;this.aborted=false;
      this.upload={events:{},addEventListener:(name,fn)=>{this.upload.events[name]=fn;}};
    }
    open(method,url) { this.method=method;this.url=url; }
    setRequestHeader(name,value) { this.headers[name]=value; }
    addEventListener(name,fn) { this.events[name]=fn; }
    send(file) {
      const meta=JSON.parse(Buffer.from(this.headers['X-AAS-Import-Metadata'],'base64').toString('utf8'));
      const request={file,meta,xhr:this,taskId:'history-task-'+(app.requests.length+1)};
      app.requests.push(request);
      this.upload.events.progress?.({lengthComputable:true,loaded:64,total:128});
      if(app.holdUpload)return;
      this.upload.events.load?.();
      this.status=app.sync?200:202;
      const response=app.response?.(request)??(app.sync?results[meta.operation]:{status:'accepted',task_id:request.taskId});
      this.responseText=JSON.stringify(response);
      this.events.load?.();
    }
    abort() { this.aborted=true;this.events.abort?.(); }
  }
  const window={confirm:()=>true,addEventListener(){},postMessage(){},AASTasks:{
    wait:async(taskId,options)=>{
      const request=app.requests.find(item=>item.taskId===taskId);
      assert.ok(request,'the server task must belong to the uploaded request');
      app.waits.push({taskId,operation:request.meta.operation,options});
      if(app.waitTask)return app.waitTask(request,options);
      options.onUpdate({task_id:taskId,status:'running',progress:55,message:'正在校验 CSV',cancellable:true});
      return {task_id:taskId,status:'completed',progress:100,result:results[request.meta.operation]};
    },
    cancel:async taskId=>{
      app.cancels.push(taskId);
      return app.cancelResult||{accepted:true,status:'cancelling'};
    },
  }};
  window.parent=window;
  const context={document,window,console,location:{origin:'http://localhost'},TextEncoder,
    btoa:text=>Buffer.from(text,'binary').toString('base64'),XMLHttpRequest:XHR,
    CustomEvent:function(type){this.type=type;},
    fetch:async url=>({ok:true,json:async()=>url.includes('column-catalog')?
      {items:{'adcs.rw.speed':{subsystem:'ADCS',device:'RW1',variable:'speed',unit:'rad/s'}}}:{items:[]}})};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('web/visualization/telemetry-v2-import.js','utf8'),context);
  return Object.assign(app,{$,window,
    inspect:()=>$('v2ImpCsvInspect').handlers.click(),
    preview:()=>$('v2ImportForm').handlers.submit({preventDefault(){}}),
    commit:()=>$('v2ImpCommit').handlers.click(),
    transfer:()=>{const wrap=app.inserted[0];return {wrap,label:wrap.children[0],bar:wrap.children[1],cancel:wrap.children[2]};},
  });
}

const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function readyToCommit(app) {
  await app.inspect();
  app.$('v2ImpCsvFormatConfirmed').checked=true;
  app.$('v2ImpCsvFormatConfirmed').handlers.change();
  await app.preview();
  assert.equal(app.$('v2ImpCommit').disabled,false,'a real successful preview enables commit');
}

async function inspectionWaitsForFinalResult() {
  const app=setup();let finish;
  app.waitTask=(request,options)=>{
    options.onUpdate({task_id:request.taskId,status:'running',progress:42,message:'正在解析 CSV',cancellable:true});
    return new Promise(resolve=>{finish=resolve;});
  };
  const pending=app.inspect();
  await flush();
  assert.equal(app.waits.length,1,'a 202 response must be resolved through task status, not used as inspection data');
  assert.equal(app.$('v2ImpCsvFormatConfirmed').disabled,true,'acceptance is not a completed inspection');
  assert.equal(app.transfer().wrap.hidden,false);
  assert.equal(app.transfer().bar.value,42);
  assert.match(app.transfer().label.textContent,/正在解析 CSV/);
  finish({status:'completed',progress:100,result:results.inspect});
  await pending;
  assert.equal(app.$('v2ImpCsvEncoding').value,'utf-8');
  assert.equal(app.$('v2ImpCsvFormatConfirmed').disabled,false);
  assert.equal(app.transfer().wrap.hidden,true);
}

async function cancellationTargetsServerAfterUpload() {
  const app=setup();let finish;let update;
  app.waitTask=(request,options)=>{
    update=task=>options.onUpdate({task_id:request.taskId,...task});
    update({status:'running',progress:61,message:'解析中',cancellable:true});
    return new Promise(resolve=>{finish=resolve;});
  };
  const pending=app.inspect();
  await flush();
  assert.equal(app.transfer().cancel.disabled,false);
  assert.match(app.transfer().cancel.textContent,/任务/);
  await app.transfer().cancel.handlers.click();
  assert.deepEqual(app.cancels,['history-task-1']);
  assert.equal(app.requests[0].xhr.aborted,false,'after acceptance cancellation must not abort a completed upload');
  assert.match(app.transfer().label.textContent,/取消/);
  update({status:'running',progress:85,message:'数据库事务提交中',cancellable:false,
    cancel_reason:'数据库写入期间不可取消'});
  assert.equal(app.transfer().cancel.disabled,true,'the server controls whether the task can be cancelled');
  finish({status:'cancelled',progress:61,result:null});
  await pending;
  assert.match(app.$('v2ImpStatus').textContent,/取消/);
  assert.equal(app.$('v2ImpCsvFormatConfirmed').disabled,true);
}

async function onlyCompletedCommitEmitsImportEvent() {
  const app=setup();
  await readyToCommit(app);
  let finish;
  app.waitTask=(_request,options)=>{
    options.onUpdate({status:'running',progress:89,message:'正在写入数据库',cancellable:false});
    return new Promise(resolve=>{finish=resolve;});
  };
  const pending=app.commit();
  await flush();
  assert.equal(app.events.length,0,'task acceptance must not announce an import');
  assert.equal(app.$('v2ImpCommit').disabled,true);
  assert.equal(app.transfer().cancel.disabled,true);
  finish({status:'completed',progress:100,result:results.commit});
  await pending;
  assert.deepEqual(app.waits.map(item=>item.operation),['inspect','preview','commit']);
  assert.deepEqual(app.events,['aas:v2-imported']);
  assert.match(app.$('v2ImpStatus').textContent,/导入完成：2/);
  assert.equal(app.$('v2ImpCommit').disabled,true);
  assert.equal(app.transfer().wrap.hidden,true);
}

async function unsuccessfulTerminalsNeverAnnounceCommit() {
  for(const [terminal,expected] of [
    [{status:'failed',failure:{message:'数据格式校验失败'}},/数据格式校验失败/],
    [{status:'cancelled'},/取消/],
    [{status:'interrupted'},/中断/],
    [{status:'completed',result:null},/结果/],
    [{status:'completed',result:{}},/结果/],
    [{status:'running'},/状态|确认/],
  ]) {
    const app=setup();app.sync=true;
    await readyToCommit(app);
    app.sync=false;app.waitTask=async()=>terminal;
    await app.commit();
    assert.equal(app.events.length,0,'no success event for '+JSON.stringify(terminal));
    assert.match(app.$('v2ImpStatus').textContent,expected);
    assert.doesNotMatch(app.$('v2ImpStatus').textContent,/导入完成|已回滚/);
    assert.equal(app.$('v2ImpCommit').disabled,true,'a failed or unverifiable commit cannot reuse its old preview');
    const count=app.requests.length;
    await app.commit();
    assert.equal(app.requests.length,count,'a second click cannot repeat the write');
  }
}

async function statusLossKeepsWriteBlockedAcrossNewPreview() {
  const app=setup();app.sync=true;
  await readyToCommit(app);
  app.sync=false;
  app.waitTask=async()=>{throw Error('任务服务暂时不可访问');};
  await app.commit();
  assert.equal(app.events.length,0);
  assert.match(app.$('v2ImpStatus').textContent,/history-task-3/,'an uncertain outcome identifies the task to verify');
  assert.doesNotMatch(app.$('v2ImpStatus').textContent,/导入完成|已回滚/);
  assert.equal(app.$('v2ImpCommit').disabled,true);
  app.sync=true;app.waitTask=null;
  await app.preview();
  assert.equal(app.$('v2ImpCommit').disabled,true,'another preview cannot clear the unresolved write guard');
  await app.commit();
  assert.equal(app.requests.filter(item=>item.meta.operation==='commit').length,1);
}

async function missingTaskClientDoesNotAuthorizeRetry() {
  const app=setup();app.sync=true;
  await readyToCommit(app);
  app.sync=false;delete app.window.AASTasks;
  await app.commit();
  assert.equal(app.events.length,0);
  assert.match(app.$('v2ImpStatus').textContent,/history-task-3/);
  assert.doesNotMatch(app.$('v2ImpStatus').textContent,/导入完成|已回滚/);
  assert.equal(app.$('v2ImpCommit').disabled,true);
  assert.equal(app.transfer().cancel.disabled,true);
}

async function uploadCancellationStillAbortsTransport() {
  const app=setup();app.holdUpload=true;
  const pending=app.inspect();
  assert.equal(app.transfer().cancel.disabled,false);
  await app.transfer().cancel.handlers.click();
  await pending;
  assert.equal(app.requests[0].xhr.aborted,true);
  assert.equal(app.waits.length,0);
  assert.match(app.$('v2ImpStatus').textContent,/取消/);
}

async function synchronousResponsesRemainSupported() {
  const app=setup();app.sync=true;
  await readyToCommit(app);
  await app.commit();
  assert.equal(app.waits.length,0);
  assert.deepEqual(app.events,['aas:v2-imported']);
  assert.match(app.$('v2ImpStatus').textContent,/导入完成：2/);
}

(async()=>{
  for(const test of [inspectionWaitsForFinalResult,cancellationTargetsServerAfterUpload,
    onlyCompletedCommitEmitsImportEvent,unsuccessfulTerminalsNeverAnnounceCommit,
    statusLossKeepsWriteBlockedAcrossNewPreview,missingTaskClientDoesNotAuthorizeRetry,
    uploadCancellationStillAbortsTransport,synchronousResponsesRemainSupported]) {
    await test();
    console.log(test.name+': PASS');
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
