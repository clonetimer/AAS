'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Node {
  constructor() { this.children=[]; this.handlers={}; this.value=0; this.files=[]; this.disabled=false;
    this.checked=false; this.textContent=''; this.style={}; this.type=''; }
  addEventListener(name, callback) { this.handlers[name]=callback; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=[...nodes]; }
  querySelectorAll(query) {
    if (query !== 'input[type=checkbox]') throw new Error('unexpected query ' + query);
    return this.children.flatMap(label => label.children.filter(child => child.type === 'checkbox'));
  }
}
function setup(waitImpl, holdUpload=false) {
  const nodes = new Map();
  const document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id,new Node()); return nodes.get(id); }, createElement() { return new Node(); }};
  const $ = id => document.getElementById(id);
  for (const role of ['train','eval']) {
    for (const [field,value] of [['CsvEncoding','auto'],['CsvDelimiter','auto'],['CsvModel','SAT-X'],
      ['CsvReference','run-x'],['CsvSubsystem','ADCS'],['CsvDevice','RW1'],['CsvUnit','rad/s']]) $(role+field).value=value;
    $(role+'CsvFile').files=[{name:role+'.csv.gz',size:1024}];
    $(role==='train'?'trainCsvNormalConfirmed':'evalCsvUnknownConfirmed').checked=true;
  }
  const requests=[];
  const waits=[];
  const cancels=[];
  class FakeXHR {
    constructor() { this.upload={}; this.headers={}; this.status=0; this.responseText=''; }
    open(method,url) { this.method=method; this.url=url; }
    setRequestHeader(key,value) { this.headers[key]=value; }
    send(file) {
      const metadata=JSON.parse(Buffer.from(this.headers['X-AAS-Import-Metadata'],'base64').toString('utf8'));
      requests.push({metadata,file});
      if (this.upload.onprogress) this.upload.onprogress({lengthComputable:true,loaded:512,total:file.size});
      if (holdUpload) return;
      this.status=202;
      this.responseText=JSON.stringify({status:'accepted',task_id:'csv-task-'+requests.length});
      this.onload();
    }
    abort() { this.aborted=true; if (this.onabort) this.onabort(); }
  }
  const context={document,window:{confirm:()=>true,AASRunnerV2:{onCsvImported:async()=>{}},AASTasks:{
      wait:async (taskId,options)=>{ waits.push(taskId); return waitImpl(taskId,options); },
      cancel:async taskId=>{ cancels.push(taskId); return {accepted:true,status:'cancelling'}; },
    }},console,Date,Math,TextEncoder,btoa,XMLHttpRequest:FakeXHR};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('web/visualization/runner-csv-import.js','utf8'),context);
  return {$,requests,waits,cancels,context};
}
async function click($,id) { assert.equal(typeof $(id).handlers.click,'function',id); return $(id).handlers.click(); }

(async()=>{
  let release;
  const waiting=setup(async (_taskId,options)=>{
    options.onUpdate({status:'running',progress:42,message:'正在解析 CSV',cancellable:true});
    return new Promise(resolve=>{release=resolve;});
  });
  const inflight=click(waiting.$,'evalCsvInspect');
  await Promise.resolve(); await Promise.resolve();
  assert.equal(waiting.waits[0],'csv-task-1');
  assert.equal(waiting.$('evalCsvProgress').value,42);
  assert.match(waiting.$('evalCsvProgressMeta').textContent,/正在解析 CSV/);
  assert.equal(waiting.$('evalCsvCancel').disabled,false);
  await click(waiting.$,'evalCsvCancel');
  assert.deepEqual(waiting.cancels,['csv-task-1']);
  release({status:'cancelled',progress:42,message:'已取消'});
  await inflight;
  assert.match(waiting.$('evalCsvStatus').textContent,/取消/);
  assert.equal(waiting.$('evalCsvCancel').disabled,true);

  const uploadOnly=setup(async()=>{throw new Error('task polling must not start after upload abort');},true);
  const uploadInFlight=click(uploadOnly.$,'evalCsvInspect');
  await Promise.resolve();
  assert.equal(uploadOnly.$('evalCsvCancel').disabled,false);
  assert.ok(uploadOnly.$('evalCsvProgress').value>0,'upload progress is visible before the task ID is returned');
  await click(uploadOnly.$,'evalCsvCancel');
  await uploadInFlight;
  assert.equal(uploadOnly.waits.length,0);
  assert.match(uploadOnly.$('evalCsvStatus').textContent,/上传已中止/);
  assert.equal(uploadOnly.$('evalCsvCancel').disabled,true);

  const resultByOperation={
    inspect:{columns:['time_s','adcs.rw.speed','label'],has_labels:true,csv_encoding:'utf-8',csv_delimiter:',',numeric_columns:['adcs.rw.speed'],sampled_rows:2,format_review_required:true},
    preview:{row_count:2,column_count:1,label_status:'provided'},
    commit:{row_count:2,column_count:1,source_id:17,dataset_id:'dataset-17',status:'imported'},
  };
  const successful=setup(async (taskId,options)=>{
    const operation=successful.requests[Number(taskId.split('-').at(-1))-1].metadata.operation;
    options.onUpdate({status:'running',progress:66,message:'处理任务'});
    return {status:'completed',progress:100,result:resultByOperation[operation]};
  });
  await click(successful.$,'evalCsvInspect');
  assert.equal(successful.$('evalCsvPreview').disabled,true, 'format must be explicitly confirmed before preview');
  assert.equal(successful.$('evalCsvProgress').value,100);
  assert.equal(successful.$('evalCsvCancel').disabled,true);
  successful.$('evalCsvFormatConfirmed').checked=true;
  successful.$('evalCsvFormatConfirmed').handlers.change();
  await click(successful.$,'evalCsvPreview');
  assert.equal(successful.$('evalCsvCommit').disabled,false);
  await click(successful.$,'evalCsvCommit');
  assert.equal(successful.requests.length,3);
  assert.deepEqual(successful.waits,['csv-task-1','csv-task-2','csv-task-3']);
  assert.match(successful.$('evalCsvStatus').textContent,/导入成功/);
  console.log('CSV async task polling, upload progress, cancellation and result handling: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
