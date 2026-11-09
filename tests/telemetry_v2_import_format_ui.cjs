'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const html=fs.readFileSync('web/visualization/import.html','utf8');
for(const id of ['v2ImpCsvFormatField','v2ImpCsvEncoding','v2ImpCsvDelimiter',
  'v2ImpCsvFormatConfirmed','v2ImpCsvInspect'])assert.ok(html.includes(`id="${id}"`),`Missing ${id}`);

class Node {
  constructor(id=''){
    this.id=id;this.value='';this.textContent='';this.innerHTML='';this.hidden=false;this.disabled=false;
    this.checked=false;this.required=false;this.files=[];this.children=[];this.handlers={};this.style={};
    this.className='';this.maxLength=0;this.parentNode={insertBefore(){}};this.classList={toggle(){},add(){},remove(){}};
  }
  addEventListener(type,fn){this.handlers[type]=fn;}
  append(...items){this.children.push(...items);}
  appendChild(item){this.children.push(item);return item;}
  replaceChildren(...items){this.children=[...items];}
  querySelectorAll(){return [];}
  setAttribute(name,value){this[name]=value;}
  reportValidity(){return true;}
}
const nodes=new Map();
const document={
  getElementById(id){if(!nodes.has(id))nodes.set(id,new Node(id));return nodes.get(id);},
  createElement(tag){return new Node(tag);},
  dispatchEvent(){},
};
for(const name of ['v2ImpCsvEncoding','v2ImpCsvDelimiter'])document.getElementById(name).value='auto';
document.getElementById('v2ImpMode').value='table';
document.getElementById('v2ImpTimeDomain').value='datetime_utc';
const xhrCalls=[];
class FakeXHR {
  constructor(){this.events={};this.headers={};this.upload={events:{},addEventListener:(n,fn)=>{this.upload.events[n]=fn;}};this.status=200;this.responseText='';}
  open(method,url){this.method=method;this.url=url;}
  setRequestHeader(name,value){this.headers[name]=value;}
  addEventListener(name,fn){this.events[name]=fn;}
  send(file){
    const meta=JSON.parse(Buffer.from(this.headers['X-AAS-Import-Metadata'],'base64').toString('utf8'));
    xhrCalls.push({url:this.url,meta,headers:this.headers,file});
    if(meta.operation==='inspect'){
      this.responseText=JSON.stringify({format:'csv',columns:['time_s','adcs.rw.speed','label'],
        has_labels:true,csv_encoding:meta.csv_encoding==='auto'?'gb18030':meta.csv_encoding,
        csv_delimiter:meta.csv_delimiter==='auto'?';':meta.csv_delimiter,
        format_review_required:true,numeric_columns:['adcs.rw.speed'],sampled_rows:2});
    }else{
      this.responseText=JSON.stringify({status:'preview',row_count:2,column_count:1,
        time_start:0,time_end:1,columns:[{source_column:'adcs.rw.speed',subsystem:'ADCS',device:'RW1',variable:'speed'}],
        label_status:'from_source',sha256:'a'.repeat(64),quality:{missing_by_column:{},label_counts:{'0':1,'1':1}},
        import_context:{run_token:'',dataset_split:'',label_scope:'run',evidence_origin:''}});
    }
    this.events.load?.();
  }
}
const window={confirm:()=>true,parent:null,postMessage(){},addEventListener(){}};window.parent=window;
const scope={document,window,console,location:{origin:'http://localhost'},TextEncoder,Buffer,
  btoa:value=>Buffer.from(value,'binary').toString('base64'),XMLHttpRequest:FakeXHR,
  CustomEvent:function(type){this.type=type;},
  fetch:async url=>({ok:true,json:async()=>url.includes('column-catalog')?{items:{
    'adcs.rw.speed':{subsystem:'ADCS',device:'RW1',variable:'speed',unit:'rad/s'}
  }}:{items:[]}})};
vm.createContext(scope);
vm.runInContext(fs.readFileSync('web/visualization/telemetry-v2-import.js','utf8'),scope);

(async()=>{
  const file={name:'现场.csv',size:128};
  document.getElementById('v2ImpFile').files=[file];
  document.getElementById('v2ImpModel').value='SIM-SAT-001';
  document.getElementById('v2ImpNamespace').value='run-A';
  document.getElementById('v2ImpRole').value='test';
  document.getElementById('v2ImpTimeDomain').value='datetime_utc';
  await document.getElementById('v2ImpCsvInspect').handlers.click();
  assert.equal(xhrCalls[0].meta.operation,'inspect');
  assert.equal(xhrCalls[0].meta.csv_encoding,'auto');
  assert.equal(xhrCalls[0].meta.csv_delimiter,'auto');
  assert.equal(document.getElementById('v2ImpCsvEncoding').value,'gb18030');
  assert.equal(document.getElementById('v2ImpCsvDelimiter').value,';');
  assert.equal(document.getElementById('v2ImpCsvFormatConfirmed').checked,false);

  // A format override must be re-inspected and cannot reuse the previous inspection.
  document.getElementById('v2ImpCsvDelimiter').value=',';
  document.getElementById('v2ImpCsvDelimiter').handlers.change();
  assert.equal(document.getElementById('v2ImpCsvFormatConfirmed').checked,false);
  await document.getElementById('v2ImpCsvInspect').handlers.click();
  assert.equal(xhrCalls[1].meta.csv_encoding,'gb18030');
  assert.equal(xhrCalls[1].meta.csv_delimiter,',');

  const form=document.getElementById('v2ImportForm');
  await form.handlers.submit({preventDefault(){}});
  assert.equal(xhrCalls.filter(x=>x.meta.operation==='preview').length,0,
    'preview must be blocked until the user confirms the inspected format');

  document.getElementById('v2ImpCsvFormatConfirmed').checked=true;
  await form.handlers.submit({preventDefault(){}});
  const preview=xhrCalls.find(x=>x.meta.operation==='preview');
  assert.ok(preview,'plain CSV preview must use the binary import-source endpoint');
  assert.equal(preview.url,'/api/v2/telemetry/import-source');
  assert.equal(preview.meta.csv_encoding,'gb18030');
  assert.equal(preview.meta.csv_delimiter,',');
  assert.equal(preview.meta.format_confirmed,true);
  assert.equal(preview.file,file);
  console.log('Historical CSV binary import, format re-inspection and explicit confirmation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
