'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('web/visualization/simulation.html','utf8');
const simV2 = fs.readFileSync('web/visualization/simulation-v2.js','utf8');
for (const id of ['simCsvWorkspace','simHistoricalWorkspace','csvFile','csvInspect','csvImport',
  'csvEncoding','csvDelimiter','csvFormatConfirmed','csvDatasetSelect','csvColumn','csvFaultType',
  'csvStartFrame','csvEndFrame','csvMagnitude','csvFaultPreview','csvFaultSave','csvExport',
  'csvPreviewPlot','csvVersions','csvQualitySummary','csvQualityIssues',
  'csvAncestorSelect','csvCompareButton','csvRollbackButton','csvCompareSummary']) {
  assert.ok(html.includes(`id="${id}"`),`Missing visible CSV control ${id}`);
}
assert.match(html,/simulation-csv-workspace\.js/);
assert.match(html,/data-sim-mode="csv"/);
assert.match(html,/data-sim-mode="historical"/);
assert.match(simV2,/X-AAS-CSV-Format-B64/);
assert.match(simV2,/format_confirmed:true/);

class Node {
  constructor() {
    this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.children=[];
    this.files=[];this.handlers={};this.className='';this.style={};this.max='';this.checked=false;
    this.classList={add(){},remove(){},toggle(){}};
  }
  addEventListener(type,fn){this.handlers[type]=fn;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(name,value){this[name]=value;}
  removeAttribute(name){delete this[name];}
}
const nodes=new Map();
const doc={
  getElementById(id){if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},
  createElement(){return new Node();},
  createElementNS(){return new Node();},
  querySelectorAll(){return [];},
};
const calls=[];
const scope={document:doc,window:{},console,TextEncoder,btoa,encodeURIComponent,
  fetch:async (url,options={})=>{
    calls.push({url,options});
    if(url.endsWith('/list'))return {ok:true,json:async()=>({items:[]})};
    if(url.endsWith('/inspect')){
      const requested=JSON.parse(Buffer.from(options.headers['X-AAS-CSV-Format-B64'],'base64').toString());
      return {ok:true,json:async()=>({
        filename:'现场.csv',frame_count:2,columns:['time_s','adcs.rw.speed','note'],
        numeric_columns:['adcs.rw.speed'],time_column:'time_s',label_column:null,
        has_truth_labels:false,source_truth:'unlabeled',
        csv_encoding:requested.csv_encoding==='auto'?'gb18030':requested.csv_encoding,
        csv_delimiter:requested.csv_delimiter==='auto'?';':requested.csv_delimiter,
        compression:'none',format_review_required:true,
        sample:[{frame:1,time_s:'0','adcs.rw.speed':'1.5',note:'中文;说明'}],
        quality:{row_count:2,missing_by_column:{},non_numeric_by_column:{note:2},
          nonfinite_by_column:{},duplicate_times:0,time_reversals:0,gaps_gt_3x_median:0,
          label_counts:{'0':0,'1':0,unknown:2},issue_count:1,issues_truncated:false,
          issues:[{line:3,frame:2,column:'note',code:'non_numeric',value:'中文',suggestion:'作为文本忽略'}]},
      })};
    }
    if(url.endsWith('/import'))return {ok:true,json:async()=>({id:'a'.repeat(32),frame_count:2,
      numeric_columns:['adcs.rw.speed'],version:0})};
    throw Error('unexpected fetch '+url);
  }};
vm.createContext(scope);
vm.runInContext(fs.readFileSync('web/visualization/simulation-csv-workspace.js','utf8'),scope);
const contract=scope.window.AASCsvWorkspace;
assert.ok(contract?.validateFrames);
assert.equal(contract.validateFrames(1,4,4).start_frame,1);
assert.equal(contract.validateFrames(4,4,4).end_frame,4);
for(const args of [[0,2,4],[3,2,4],[2,5,4],[1.1,3,4]])assert.throws(()=>contract.validateFrames(...args));
assert.ok(calls.some(call=>call.url==='/api/v2/csv-workspace/list'));

(async()=>{
  const file={name:'现场.csv',size:128};
  doc.getElementById('csvFile').files=[file];
  await doc.getElementById('csvFile').handlers.change();
  await doc.getElementById('csvInspect').handlers.click();
  assert.equal(doc.getElementById('csvEncoding').value,'gb18030');
  assert.match(doc.getElementById('csvQualitySummary').textContent,/2 帧/);
  assert.ok(doc.getElementById('csvQualityIssues').children.some(x=>String(x.textContent).includes('第 3 行')));
  assert.equal(doc.getElementById('csvDelimiter').value,';');
  assert.equal(doc.getElementById('csvFormatConfirmed').checked,false);
  assert.equal(doc.getElementById('csvFormatConfirmed').disabled,false);
  assert.equal(doc.getElementById('csvImport').disabled,true,
    'format inspection alone must not authorize simulation import');
  const inspectCall=calls.find(call=>call.url.endsWith('/inspect'));
  assert.equal(inspectCall.options.headers['Content-Type'],'application/octet-stream');
  assert.deepEqual(JSON.parse(Buffer.from(inspectCall.options.headers['X-AAS-CSV-Format-B64'],'base64').toString()),
    {csv_encoding:'auto',csv_delimiter:'auto'});

  // Changing a format choice invalidates the inspected bytes/format approval.
  doc.getElementById('csvDelimiter').value=',';
  doc.getElementById('csvDelimiter').handlers.change();
  assert.equal(doc.getElementById('csvImport').disabled,true);
  assert.equal(doc.getElementById('csvFormatConfirmed').checked,false);
  await doc.getElementById('csvInspect').handlers.click();
  doc.getElementById('csvFormatConfirmed').checked=true;
  doc.getElementById('csvFormatConfirmed').handlers.change();
  assert.equal(doc.getElementById('csvImport').disabled,false);
  await doc.getElementById('csvImport').handlers.click();
  const importCall=calls.find(call=>call.url.endsWith('/import'));
  assert.deepEqual(JSON.parse(Buffer.from(importCall.options.headers['X-AAS-CSV-Format-B64'],'base64').toString()),
    {csv_encoding:'gb18030',csv_delimiter:',',format_confirmed:true});
  assert.equal(doc.getElementById('csvImport').disabled,true,
    'an imported file cannot reuse its previous approval');
  console.log('CSV UI format detection, confirmation invalidation, binary upload: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
