'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync('web/visualization/import.html','utf8');
for(const id of ['v2ImpQualityPanel','v2ImpQualitySummary','v2ImpQualityIssues',
  'v2ImpTimeColumn','v2ImpLabelColumn','v2ImpMeasureColumns',
  'v2ImpPresetName','v2ImpPresetSelect','v2ImpSavePreset','v2ImpApplyPreset','v2ImpDeletePreset']){
  assert.ok(html.includes(`id="${id}"`),`Missing user-facing ${id}`);
}
class Node {
  constructor(tag=''){
    this.tag=tag;this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;
    this.required=false;this.files=[];this.children=[];this.handlers={};this.style={};this.maxLength=100;
    this.parentNode={insertBefore(){}};this.classList={toggle(){},add(){},remove(){}};
  }
  addEventListener(n,fn){this.handlers[n]=fn;}
  append(...n){this.children.push(...n);}
  appendChild(n){this.children.push(n);return n;}
  replaceChildren(...n){this.children=[...n];}
  querySelectorAll(){return [];}
  setAttribute(k,v){this[k]=v;}
  reportValidity(){return true;}
}
const nodes=new Map();const document={
  getElementById(k){if(!nodes.has(k))nodes.set(k,new Node(k));return nodes.get(k);},
  createElement(tag){return new Node(tag);},dispatchEvent(){}
};
const el=id=>document.getElementById(id);
el('v2ImpCsvEncoding').value='auto';el('v2ImpCsvDelimiter').value='auto';
el('v2ImpMode').value='table';el('v2ImpRole').value='test';
el('v2ImpTimeDomain').value='numeric_unspecified';
el('v2ImpTimeReference').value='run-A';
el('v2ImpNamespace').value='run-A';el('v2ImpModel').value='SIM-SAT';
const requests=[];const stored={};let deferNextPreview=false,delayedPreview=null;
let inspectColumns=['采样时刻','rw_speed','fault_state','note'];
const localStorage={getItem:k=>stored[k]??null,setItem:(k,v)=>{stored[k]=v;},removeItem:k=>{delete stored[k];}};
class XHR{
  constructor(){this.events={};this.headers={};this.upload={addEventListener(){}};this.status=200;}
  open(method,url){this.method=method;this.url=url;}
  setRequestHeader(k,v){this.headers[k]=v;}
  addEventListener(k,fn){this.events[k]=fn;}
  send(file){
    const meta=JSON.parse(Buffer.from(this.headers['X-AAS-Import-Metadata'],'base64').toString('utf8'));
    requests.push({meta,file});
    this.responseText=JSON.stringify(meta.operation==='inspect'?{
      format:'csv',columns:inspectColumns,
      time_candidates:['采样时刻'],label_candidates:['fault_state'],
      csv_encoding:meta.csv_encoding==='auto'?'utf-8':meta.csv_encoding,
      csv_delimiter:meta.csv_delimiter==='auto'?',':meta.csv_delimiter,
      format_review_required:true,numeric_columns:['rw_speed','fault_state'],
      quality:{row_count:3,missing_by_column:{rw_speed:1},non_numeric_by_column:{note:3},
        nonfinite_by_column:{},duplicate_times:1,time_reversals:0,gaps_gt_3x_median:0,
        label_counts:{'0':1,'1':1,unknown:1},issue_count:1,issues_truncated:false,
        issues:[{line:4,frame:3,column:'rw_speed',code:'missing',value:'',suggestion:'检查'}]}
    }:{status:'preview',row_count:3,column_count:1,time_start:0,time_end:2,
      columns:[{source_column:'rw_speed',subsystem:'ADCS',device:'RW',variable:'speed'}],
      label_status:'provided',sha256:'a'.repeat(64),quality:{missing_by_column:{rw_speed:1}},import_context:{}});
    if(meta.operation==='preview'&&deferNextPreview){deferNextPreview=false;delayedPreview=()=>this.events.load?.();return;}
    this.events.load?.();
  }
}
const window={parent:null,confirm:()=>true,addEventListener(){}};window.parent=window;
const scope={document,window,localStorage,console,Buffer,TextEncoder,XMLHttpRequest:XHR,
  btoa:s=>Buffer.from(s,'binary').toString('base64'),
  fetch:async url=>({ok:true,json:async()=>url.includes('column-catalog')?{items:{rw_speed:{subsystem:'ADCS',device:'RW',variable:'speed',unit:'rad/s'}}}:{items:[]}}),
  CustomEvent:function(type){this.type=type;},location:{origin:'http://localhost'}};
vm.createContext(scope);
vm.runInContext(fs.readFileSync('web/visualization/telemetry-v2-import.js','utf8'),scope);
function descend(root){return [root,...root.children.flatMap(descend)];}
(async()=>{
  const file={name:'local.csv',size:400};el('v2ImpFile').files=[file];
  await el('v2ImpCsvInspect').handlers.click();
  assert.match(el('v2ImpQualitySummary').textContent,/缺失/);
  assert.ok(descend(el('v2ImpQualityIssues')).some(e=>String(e.textContent).includes('第 4 行')));
  el('v2ImpTimeColumn').value='采样时刻';el('v2ImpTimeColumn').handlers.change();
  el('v2ImpLabelColumn').value='fault_state';el('v2ImpLabelColumn').handlers.change();
  const note=descend(el('v2ImpMeasureColumns')).find(x=>x.value==='note');
  assert.ok(note,'include/ignore controls must be created for each candidate measurement');
  note.checked=false;note.handlers.change();
  const edit=descend(el('v2ImpMappingRows')).find(x=>x['aria-label']==='rw_speed unit');
  assert.ok(edit,'a selected measurement must have editable metadata');
  edit.value='radian/s';edit.handlers.input();
  await el('v2ImpCsvInspect').handlers.click();
  assert.equal(el('v2ImpTimeColumn').value,'采样时刻','re-inspection must keep time role');
  assert.equal(el('v2ImpLabelColumn').value,'fault_state','re-inspection must keep chosen label role');
  const retained=descend(el('v2ImpMappingRows')).find(x=>x['aria-label']==='rw_speed unit');
  assert.equal(retained.value,'radian/s','re-inspection must retain user-edited unit');
  el('v2ImpCsvFormatConfirmed').checked=true;
  await el('v2ImportForm').handlers.submit({preventDefault(){}});
  const preview=requests.find(x=>x.meta.operation==='preview');
  assert.ok(preview,'selected roles must be sent through real preview endpoint');
  assert.equal(preview.meta.config.time_column,'采样时刻');
  assert.equal(preview.meta.config.label_column,'fault_state');
  assert.equal(JSON.stringify(preview.meta.config.selected_columns),'["rw_speed"]');
  assert.equal(preview.meta.config.column_metadata.rw_speed.unit,'radian/s');
  // Editing the mapping while a preview is in flight must invalidate that receipt.
  deferNextPreview=true;
  const pending=el('v2ImportForm').handlers.submit({preventDefault(){}});
  for(let i=0;i<20&&!delayedPreview;i++)await Promise.resolve();
  assert.ok(delayedPreview,'preview must be in flight');
  const edited=descend(el('v2ImpMappingRows')).find(x=>x['aria-label']==='rw_speed unit');
  edited.value='deg/s';edited.handlers.input();
  delayedPreview();await pending;
  assert.equal(el('v2ImpCommit').disabled,true,'outdated preview must not authorize import');

  el('v2ImpPresetName').value='RW';el('v2ImpSavePreset').handlers.click();
  assert.ok(Object.values(stored).join('').includes('rw_speed'));
  assert.ok(!Object.values(stored).join('').includes('正常,故障'));
  // Simulate a fresh browser page loading the same localStorage, not just
  // the original page's in-memory preset array.
  const reloadedNodes=new Map();
  const reloadedDocument={getElementById(k){if(!reloadedNodes.has(k))reloadedNodes.set(k,new Node(k));return reloadedNodes.get(k);},
    createElement(tag){return new Node(tag);},dispatchEvent(){}};
  const reloadedScope={...scope,document:reloadedDocument};
  vm.createContext(reloadedScope);
  vm.runInContext(fs.readFileSync('web/visualization/telemetry-v2-import.js','utf8'),reloadedScope);
  assert.ok(reloadedDocument.getElementById('v2ImpPresetSelect').children.some(x=>x.value==='RW'),
    'presets should be discoverable after a browser reload');
  // An applied preset must restore roles and edited units, not only list its name.
  el('v2ImpLabelColumn').value='';el('v2ImpLabelColumn').handlers.change();
  el('v2ImpPresetSelect').value='RW';el('v2ImpApplyPreset').handlers.click();
  assert.equal(el('v2ImpLabelColumn').value,'fault_state');
  assert.equal(descend(el('v2ImpMappingRows')).find(x=>x['aria-label']==='rw_speed unit').value,'deg/s');
  assert.equal(el('v2ImpCommit').disabled,true,'preset application needs a new preview');
  // A file with a changed header MUST NOT silently reuse this semantic mapping.
  inspectColumns=['采样时刻','rw_speed','fault_state','note','extra'];
  el('v2ImpFile').files=[{name:'different.csv',size:400}];
  await el('v2ImpCsvInspect').handlers.click();
  el('v2ImpPresetSelect').value='RW';el('v2ImpApplyPreset').handlers.click();
  assert.match(el('v2ImpPresetStatus').textContent,/列集合与预设不一致/);
  // Browser storage may be disabled; this is an error message, not a page crash.
  const originalWrite=localStorage.setItem;
  localStorage.setItem=()=>{throw Error('storage unavailable')};
  el('v2ImpSavePreset').handlers.click();
  assert.match(el('v2ImpPresetStatus').textContent,/storage unavailable/);
  localStorage.setItem=originalWrite;
  el('v2ImpDeletePreset').handlers.click();
  assert.ok(!Object.values(stored).join('').includes('"name":"RW"'));
  console.log('History import quality, explicit roles, mapping edits, and preset lifecycle: PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
