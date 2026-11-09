'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Node {
  constructor(){this.children=[];this.handlers={};this.value='';this.files=[];
    this.disabled=false;this.checked=false;this.textContent='';this.style={};this.type='';}
  addEventListener(name,cb){this.handlers[name]=cb;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  querySelectorAll(query){if(query!=='input[type=checkbox]')throw Error('unexpected query');
    return this.children.flatMap(label=>label.children.filter(child=>child.type==='checkbox'));}
}
const nodes = new Map();
const doc = {getElementById(id){if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},
  createElement(){return new Node();}};
const $=id=>doc.getElementById(id);
for(const role of ['train','eval']){
  for(const [field,value] of [['CsvEncoding','auto'],['CsvDelimiter','auto'],['CsvModel','SAT-X'],
    ['CsvReference','run-x'],['CsvSubsystem','ADCS'],['CsvDevice','RW1'],['CsvUnit','rad/s']]) $(role+field).value=value;
  $(role+'CsvFile').files=[{name:role+'.csv',size:66}];
  $(role==='train'?'trainCsvNormalConfirmed':'evalCsvUnknownConfirmed').checked=true;
}
const requests=[];
let pausePreview=false, releasePreview=null;
const context={document:doc,window:{AASRunnerV2:{onCsvImported:async()=>{}}},console,Date,Math,
  TextEncoder,btoa,fetch:async (url,opts)=>{
    assert.equal(url,'/api/v2/user-csv-workspace/import-source');
    const meta=JSON.parse(Buffer.from(opts.headers['X-AAS-Import-Metadata'],'base64').toString('utf8'));
    requests.push(meta);
    if(pausePreview&&meta.operation==='preview')await new Promise(resolve=>{releasePreview=resolve;});
    const result=meta.operation==='inspect'?{columns:['time_s','adcs.rw.speed','note'],has_labels:false,
      csv_encoding:'gb18030',csv_delimiter:meta.csv_delimiter==='auto'?';':meta.csv_delimiter,
      numeric_columns:meta.filename==='train.csv'?[]:['adcs.rw.speed'],
      sampled_rows:2,format_review_required:true}:meta.operation==='preview'?{
      row_count:2,column_count:1,label_status:'unknown'}:{row_count:2,column_count:1,source_id:5};
    return {ok:true,json:async()=>result};
  }};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/visualization/runner-csv-import.js','utf8'),context);
async function click(id){assert.equal(typeof $(id).handlers.click,'function',id);await $(id).handlers.click();}
(async()=>{
  await click('evalCsvInspect');
  assert.deepEqual([$('evalCsvEncoding').value,$('evalCsvDelimiter').value],['gb18030',';']);
  assert.match($('evalCsvStatus').textContent,/GB18030|gb18030/);
  assert.equal($('evalCsvFormatConfirmed').checked,false);
  assert.equal($('evalCsvPreview').disabled,true,'inspection alone must not authorize preview');
  $('evalCsvFormatConfirmed').checked=true;$('evalCsvFormatConfirmed').handlers.change();
  assert.equal($('evalCsvPreview').disabled,false);
  const columns=$('evalCsvColumns').querySelectorAll('input[type=checkbox]');
  assert.equal(columns.length,2);
  assert.deepEqual(columns.map(col=>col.checked),[true,false], 'text columns should not be preselected');
  $('evalCsvModel').value='SAT-Y'; $('evalCsvModel').handlers.input();
  assert.equal($('evalCsvPreview').disabled,false,'mapping edit should only invalidate preview approval');
  await click('evalCsvPreview');
  assert.equal(requests[1].csv_encoding,'gb18030');
  assert.equal(requests[1].csv_delimiter,';');
  assert.equal(requests[1].format_confirmed,true);
  assert.equal(requests[1].config.model,'SAT-Y');
  assert.equal($('evalCsvCommit').disabled,false);
  columns[0].checked=false;columns[0].handlers.change();
  assert.equal($('evalCsvCommit').disabled,true,'changing selected columns revokes preview approval');
  columns[0].checked=true;columns[0].handlers.change();
  assert.equal($('evalCsvPreview').disabled,false,'column changes should not force a second file inspect');
  await click('evalCsvPreview');
  assert.equal(requests.at(-1).operation,'preview');
  $('evalCsvDelimiter').value='\t';$('evalCsvDelimiter').handlers.change();
  assert.equal($('evalCsvCommit').disabled,true,'changing delimiter invalidates approval');
  assert.equal($('evalCsvPreview').disabled,true,'changing format requires re-inspect');
  assert.equal($('evalCsvFormatConfirmed').checked,false);
  await click('evalCsvInspect');
  assert.equal($('evalCsvPreview').disabled,true);
  $('evalCsvFormatConfirmed').checked=true;$('evalCsvFormatConfirmed').handlers.change();
  await click('evalCsvPreview');
  await click('evalCsvCommit');
  const submitted=requests.at(-1),preview=requests.at(-2);
  assert.equal(submitted.csv_encoding,preview.csv_encoding);
  assert.equal(submitted.csv_delimiter,preview.csv_delimiter);
  assert.equal(submitted.format_confirmed,true);
  assert.deepEqual(submitted.config.selected_columns,preview.config.selected_columns);
  assert.equal($('evalCsvPreview').disabled,true,'imported file cannot reuse obsolete preview');
  await click('trainCsvInspect');
  assert.equal($('trainCsvPreview').disabled,true);
  const sparse=$('trainCsvColumns').querySelectorAll('input[type=checkbox]')[0];
  assert.equal(sparse.checked,false,'sparse sensor not auto-selected');
  sparse.checked=true;sparse.handlers.change();
  $('trainCsvFormatConfirmed').checked=true;$('trainCsvFormatConfirmed').handlers.change();
  pausePreview=true;
  const inflight=click('trainCsvPreview');
  await Promise.resolve();
  assert.equal(typeof releasePreview,'function');
  $('trainCsvUnit').value='rps';$('trainCsvUnit').handlers.input();
  releasePreview(); await inflight;
  assert.equal($('trainCsvCommit').disabled,true,'in-flight preview must not approve later mapping');
  assert.match($('trainCsvStatus').textContent,/预检期间.*变化/);
  pausePreview=false;
  await click('trainCsvPreview');
  assert.equal($('trainCsvCommit').disabled,false);
  console.log('CSV encoding/delimiter detection, approval invalidation, stable commit: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
