/* External wide CSV table and single-channel V2 import. Both preview before commit. */
(() => {'use strict';
const form=document.getElementById('v2ImportForm');if(!form)return;
const id=x=>document.getElementById(x);
const commit=id('v2ImpCommit'),preview=id('v2ImpPreview'),status=id('v2ImpStatus');
const fields={namespace:'v2ImpNamespace',model:'v2ImpModel',subsystem:'v2ImpSubsystem',device:'v2ImpDevice',variable:'v2ImpVariable',unit:'v2ImpUnit',role:'v2ImpRole',time_domain:'v2ImpTimeDomain',time_reference:'v2ImpTimeReference',time_unit:'v2ImpTimeUnit'};
const importWizard=window.AASCsvImportWizard?.create({rootId:'v2ImpWizard',purpose:'history',
 nextLabel:'返回历史库筛选参量',onNext:()=>{
  if(window.parent!==window)window.parent.postMessage({type:'aas:v2-import-next'},location.origin);
  else window.location.href='browser.html';
 }});
function importSummary(result={},config={}){return {...result,
 filename:result.filename||id('v2ImpFile').files?.[0]?.name,
 role:config.role||result.role||id('v2ImpRole').value,
 columns:(result.columns||[]).map(item=>typeof item==='string'?item:item.source_column||item.variable),
 csv_encoding:result.csv_encoding||csvFormatReview?.encoding,
 csv_delimiter:result.csv_delimiter||csvFormatReview?.delimiter};}
function syncWizardFile(){const file=id('v2ImpFile').files?.[0];
 const ready=Boolean(file&&inspectedFile===file&&sourceHeaders.length&&
  (sourceType(file)==='sqlite'||(csvFormatReview?.file===file&&id('v2ImpCsvFormatConfirmed').checked&&
    id('v2ImpCsvEncoding').value===csvFormatReview.encoding&&id('v2ImpCsvDelimiter').value===csvFormatReview.delimiter)));
 importWizard?.fileReady(ready,{filename:file?.name,role:id('v2ImpRole').value});
}
let candidate=null,busy=false,uiRevision=0,inspectionsInFlight=0;
let csvHasLabels=null;let mappingGeneration=0;let sourceHeaders=[];let csvFormatReview=null;
let autoMapping={},catalog=null,manualMapping=false,lastFileName='',lastInferredToken='';
let selectedColumns=new Set(),roleTime='',roleLabel='',inspectedFile=null;
let numericSuggestions=[],manualMetadata={};
const METADATA_COLUMNS=['timestamp','time_s','label','fault_label'];
async function loadCatalog(){
 if(catalog)return catalog;
 const response=await fetch('telemetry-v2-column-catalog.json');
 if(!response.ok)throw Error('标准参量目录无法加载');
 catalog=(await response.json()).items||{};return catalog;
}
function guessRunToken(filename){const m=String(filename||'').match(/^([0-9a-f]{24})\.csv(?:\(\d+\))?(?:\.gz)?$/i);return m?m[1].toLowerCase():'';}
function updateRunSource(file){
 if(file.name!==lastFileName){
   if(lastInferredToken&&id('v2ImpRunToken').value===lastInferredToken)id('v2ImpRunToken').value='';
   if(lastInferredToken&&id('v2ImpNamespace').value===lastInferredToken)id('v2ImpNamespace').value='';
   lastFileName=file.name;lastInferredToken='';
 }
 const token=guessRunToken(file.name);
 if(token&&!id('v2ImpRunToken').value){id('v2ImpRunToken').value=token;lastInferredToken=token;}
 if(token&&!id('v2ImpNamespace').value){id('v2ImpNamespace').value=token;lastInferredToken=token;}
}
async function readTargetEvidence(){
 const f=id('v2ImpTargetJson').files?.[0];if(!f)return null;
 if(f.size>16384)throw Error('运行标签 JSON 超过 16KB');
 let data;try{data=JSON.parse(await f.text());}catch{throw Error('运行标签 JSON 无法解析');}
 return data;
}
async function loadTargetToForm(){
 try{
  const t=await readTargetEvidence();if(!t)return;
  for(const [el,key] of [['v2ImpRunToken','token'],['v2ImpDatasetSplit','split'],['v2ImpGroup','group'],['v2ImpRunRole','role']])
    if(typeof t[key]==='string')id(el).value=t[key];
  id('v2ImpLabelScope').value='run';
  if(!id('v2ImpNamespace').value&&t.token)id('v2ImpNamespace').value=t.token;
  invalidate();tell('已读取单个运行标签 JSON。预检将核对内容和身份；它尚未被证明属于权威 targets 归档。');
 }catch(error){tell(error.message,true);invalidate();}
}
id('v2ImpTargetJson').addEventListener('change',loadTargetToForm);

function tell(message,error=false){status.textContent=message;status.classList.toggle('error',error);if(error)importWizard?.error(message);}
function invalidate(){uiRevision++;candidate=null;commit.disabled=true;importWizard?.invalidate();}
function setBusy(on){busy=on;preview.disabled=on;commit.disabled=on||!candidate||Boolean(transfer.pendingCommit);importWizard?.setBusy(on);}
function updateLabelControls(){
 const role=id('v2ImpRole').value;
 const noLabel=csvHasLabels===false;
 id('v2ImpNormalLabelField').hidden=!(noLabel&&role==='train');
 id('v2ImpUnknownTestField').hidden=!(noLabel&&role==='test');
 const hint=id('v2ImpLabelHint');
 hint.textContent=csvHasLabels===null?'选择文件后将检查是否包含 label / fault_label 列。':
   csvHasLabels?'检测到真实标签列：保留原始 0/1 标签，不会修改。':
   role==='train'?'本文件没有标签。仅确认全部正常后才能导入训练数据。':
   role==='test'?'本文件没有标签。可作为测试数据存储，但标签保持未知；检测精度等指标暂不可计算。':
   '本文件没有标签。请先明确选择训练或测试用途。';
}
function chosenColumns(){return sourceHeaders.filter(x=>selectedColumns.has(x) && x!==roleTime && x!==roleLabel);}
function option(select,value,title){const opt=document.createElement('option');opt.value=value;opt.textContent=title;select.append(opt);}
function showQuality(quality){
 const summary=id('v2ImpQualitySummary'),issues=id('v2ImpQualityIssues');issues.replaceChildren();
 if(!quality){summary.textContent='当前来源未提供质量检查信息。';return;}
 const sum=x=>Object.values(x||{}).reduce((a,b)=>a+Number(b||0),0);
 summary.textContent=`共 ${quality.row_count||0} 帧 · 缺失 ${sum(quality.missing_by_column)} · 非数值 ${sum(quality.non_numeric_by_column)} · NaN/Inf ${sum(quality.nonfinite_by_column)} · 重复时间 ${quality.duplicate_times||0} · 时间倒退 ${quality.time_reversals||0} · 大间隔 ${quality.gaps_gt_3x_median||0} · 标签 0/1/未知 ${quality.label_counts?.['0']||0}/${quality.label_counts?.['1']||0}/${quality.label_counts?.unknown||0}。问题 ${quality.issue_count||0} 项${quality.issues_truncated?'（仅显示前 20 项）':''}。`;
 for(const issue of quality.issues||[]){const line=document.createElement('p');
  line.textContent=`第 ${issue.line} 行 / 第 ${issue.frame} 帧 · ${issue.column} · ${issue.code} · ${issue.value||'空'} · ${issue.suggestion||''}`;
  issues.append(line);
 }
 if(!quality.issues?.length){const line=document.createElement('p');line.textContent='未发现问题。';issues.append(line);}
}
function renderRoleControls(){
 const time=id('v2ImpTimeColumn'),label=id('v2ImpLabelColumn');
 time.replaceChildren();option(time,'','选择时间列');
 for(const name of sourceHeaders)option(time,name,name);
 time.value=sourceHeaders.includes(roleTime)?roleTime:'';
 label.replaceChildren();option(label,'','无标签（保持未知）');
 for(const name of sourceHeaders.filter(x=>x!==roleTime))option(label,name,name);
 label.value=sourceHeaders.includes(roleLabel)&&roleLabel!==roleTime?roleLabel:'';
 const host=id('v2ImpMeasureColumns');host.replaceChildren();
 for(const name of sourceHeaders.filter(x=>x!==roleTime&&x!==roleLabel)){
  const wrapper=document.createElement('label');wrapper.className='v2-check';
  const check=document.createElement('input');check.type='checkbox';check.value=name;check.checked=selectedColumns.has(name);
  check.addEventListener('change',()=>{if(check.checked)selectedColumns.add(name);else selectedColumns.delete(name);
    renderCurrentMapping();invalidate();});
  const description=document.createElement('span');description.textContent=`${name}${numericSuggestions.includes(name)?'（数值建议）':'（请核验）'}`;
  wrapper.append(check,description);host.append(wrapper);
 }
 csvHasLabels=Boolean(roleLabel);updateLabelControls();
}
function renderCurrentMapping(){
 const metadata=manualMapping?manualMetadata:autoMapping;
 const base={model:id('v2ImpModel').value.trim(),namespace:id('v2ImpNamespace').value.trim()};
 renderMapping({items:chosenColumns().map(column=>({column,...base,...(metadata[column]||{})}))});
}
function roleChanged(){
 roleTime=id('v2ImpTimeColumn').value;
 roleLabel=id('v2ImpLabelColumn').value;
 if(roleTime===roleLabel)roleLabel='';
 selectedColumns.delete(roleTime);selectedColumns.delete(roleLabel);
 renderRoleControls();renderCurrentMapping();invalidate();
}
id('v2ImpTimeColumn').addEventListener('change',roleChanged);
id('v2ImpLabelColumn').addEventListener('change',roleChanged);
function view(){const single=id('v2ImpMode').value==='single';id('v2ImpMappingField').hidden=single;const numeric=id('v2ImpTimeDomain').value==='numeric_unspecified';form.querySelectorAll('.v2-numeric-field').forEach(e=>e.hidden=!numeric);id('v2ImpTimeReference').required=numeric;updateLabelControls();invalidate();}
function values(){return Object.fromEntries(Object.entries(fields).map(([key,name])=>[key,id(name).value.trim()]));}
async function api(url,payload){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});let data;try{data=await response.json();}catch{throw Error(`服务器返回非 JSON (${response.status})`);}if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);return data;}
function endpoint(){return id('v2ImpMode').value==='single'?'/api/v2/telemetry/import-csv':'/api/v2/telemetry/import-table';}
function sourceType(file){
  if(!file)return 'none';
  const name=file.name.toLowerCase();
  if(/\.csv(?:\(\d+\))?\.gz$/.test(name))return 'gzip';
  if(/\.(?:db|sqlite|sqlite3)$/.test(name))return 'sqlite';
  return name.endsWith('.csv')?'csv':'unknown';
}
function sourceMetaBytes(value){
  const bytes=new TextEncoder().encode(JSON.stringify(value));
  // Header stores only mapping/role, never the uploaded file itself.
  if(bytes.length>22000)throw Error('导入映射参数过大，请减少列数');
  let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
  return btoa(binary);
}
// 检查与预检可能并行；上传结束后继续保留服务端任务，不把受理响应当成操作结果。
const transfer={wrap:null,bar:null,label:null,cancel:null,requests:new Set(),tasks:new Map(),pendingCommit:null};
function fmtBytes(n){
  if(!Number.isFinite(n))return '—';
  return n>=1048576?(n/1048576).toFixed(1)+' MB':n>=1024?(n/1024).toFixed(0)+' KB':n+' B';
}
function ensureTransferUi(){
  if(transfer.wrap)return transfer.wrap;
  const wrap=document.createElement('div');
  wrap.style.cssText='display:flex;align-items:center;gap:10px;margin:8px 0;font-size:12px;flex-wrap:wrap';
  const label=document.createElement('span');
  label.style.cssText='min-width:150px;color:var(--muted,#64748b)';
  const bar=document.createElement('progress');
  bar.max=100;bar.value=0;
  bar.setAttribute('aria-label','历史数据导入进度');
  bar.style.cssText='flex:1;min-width:140px;height:8px;accent-color:var(--accent,#0891b2)';
  const cancel=document.createElement('button');
  cancel.type='button';cancel.textContent='取消上传';
  cancel.style.cssText='padding:3px 10px;border-radius:6px;border:1px solid var(--line,#cbd5e1);background:var(--surface,#fff);color:var(--text,#1e293b);cursor:pointer;font-size:12px';
  wrap.append(label,bar,cancel);
  wrap.hidden=true;
  status.parentNode.insertBefore(wrap,status.nextSibling);
  transfer.wrap=wrap;transfer.label=label;transfer.bar=bar;transfer.cancel=cancel;
  cancel.addEventListener('click',cancelTransfer);
  return wrap;
}
function updateTransferUi(){
  if(!transfer.wrap)return;
  const tasks=[...transfer.tasks.values()],current=tasks[tasks.length-1];
  transfer.wrap.hidden=!transfer.requests.size&&!current;
  if(current){
    transfer.bar.value=Math.max(0,Math.min(100,Number(current.progress)||0));
    transfer.label.textContent=current.message+' · 任务 '+current.taskId;
  }
  transfer.cancel.textContent=current?'取消任务':'取消上传';
  transfer.cancel.disabled=!transfer.requests.size&&!(typeof window.AASTasks?.cancel==='function'&&
    tasks.some(task=>task.cancellable&&!task.cancelRequested));
}
async function cancelTransfer(){
  for(const xhr of [...transfer.requests]){try{xhr.abort();}catch(error){/* 已结束的请求忽略 */}}
  if(typeof window.AASTasks?.cancel!=='function')return;
  for(const task of [...transfer.tasks.values()]){
    if(!task.cancellable||task.cancelRequested)continue;
    task.cancelRequested=true;updateTransferUi();
    try{
      const result=await window.AASTasks.cancel(task.taskId);
      if(result.accepted){task.cancellable=false;task.message='已请求取消，正在等待服务器确认…';}
      else{task.cancelRequested=false;if(!result.cancelled_by_user)task.message=result.reason||'当前阶段不能取消';}
    }catch(error){task.cancelRequested=false;task.message='取消请求未确认：'+error.message;}
    updateTransferUi();
  }
}
function pendingCommitMessage(){
  const taskId=transfer.pendingCommit?.taskId;
  return '上次写入结果尚未核对'+(taskId?'（任务 '+taskId+'）':'')+'；请先在任务查询与导出或导入记录中核对，勿重复导入。';
}
function validTaskResult(result,operation){
  if(!result||typeof result!=='object'||Array.isArray(result))return false;
  if(operation==='inspect')return Array.isArray(result.columns)||Array.isArray(result.tables);
  return result.status===(operation==='commit'?'imported':'preview')&&
    Number.isInteger(result.row_count)&&result.row_count>0&&
    Number.isInteger(result.column_count)&&result.column_count>0&&Array.isArray(result.columns)&&
    (operation!=='commit'||(Number.isInteger(result.source_id)&&result.source_id>0));
}
/* 上传走 XHR 而不是 fetch：fetch 无法提供上传进度，也无法中止已开始的传输。 */
async function binaryApi(file,meta){
  if(meta.operation==='commit'&&transfer.pendingCommit)throw Error(pendingCommitMessage());
  const metadata=sourceMetaBytes(meta);
  const opText=meta.operation==='inspect'?'读取源文件':meta.operation==='commit'?'写入数据库':'预检导入';
  const task={taskId:null,progress:10,message:'文件已上传，等待服务器处理…',cancellable:false,cancelRequested:false};
  let rejectedByServer=false;
  try{
   const response=await new Promise((resolve,reject)=>{
    const wrap=ensureTransferUi();
    const xhr=new XMLHttpRequest();
    transfer.requests.add(xhr);
    wrap.hidden=false;transfer.bar.value=0;
    transfer.cancel.disabled=false;transfer.cancel.textContent='取消上传';
    transfer.label.textContent='准备'+opText+'：'+file.name+'…';
    let finished=false;
    const finish=(error,data)=>{
      if(finished)return;finished=true;
      transfer.requests.delete(xhr);
      updateTransferUi();
      if(error)reject(error);else resolve(data);
    };
    xhr.open('POST','/api/v2/telemetry/import-source');
    xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.setRequestHeader('X-AAS-Import-Metadata',metadata);
    xhr.upload.addEventListener('progress',event=>{
      if(!event.lengthComputable)return;
      const pct=Math.round(event.loaded/event.total*100);
      transfer.bar.value=Math.min(10,pct/10);
      transfer.label.textContent=`正在${opText} ${file.name} · ${pct}%（${fmtBytes(event.loaded)} / ${fmtBytes(event.total)}）`;
    });
    xhr.upload.addEventListener('load',()=>{
      transfer.bar.value=10;
      transfer.label.textContent='上传完成，服务器正在校验'+(meta.operation==='commit'?'并写库':'')+'…';
    });
    xhr.addEventListener('error',()=>finish(Error('网络中断，'+opText+'失败')));
    // 客户端中止不等于服务端没写完：已收完请求时事务仍会提交，必须让用户去导入记录核对。
    xhr.addEventListener('abort',()=>finish(Error('已取消'+opText+'；若服务端已收完请求，本次仍可能入库，请在导入记录中核对')));
    xhr.addEventListener('timeout',()=>finish(Error(opText+'超时，请检查网络或改用 .csv.gz 压缩导入')));
    xhr.addEventListener('load',()=>{
      let data;try{data=JSON.parse(xhr.responseText);}catch{return finish(Error(`服务器返回非 JSON (${xhr.status})`));}
      if(xhr.status<200||xhr.status>=300){rejectedByServer=true;return finish(Error(data?.error||`HTTP ${xhr.status}`));}
      return finish(null,{data,status:xhr.status});
    });
    if(meta.operation==='commit')transfer.pendingCommit=task;
    xhr.send(file);
   });
   const data=response.data;
   if(response.status!==202&&data?.status!=='accepted'){
     if(meta.operation==='commit')transfer.pendingCommit=null;
     return data;
   }
   if(data?.status!=='accepted'||typeof data.task_id!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(data.task_id))
     throw Error('服务器已受理请求，但未返回有效任务编号；请在任务查询与导出核对结果。');
   task.taskId=data.task_id;transfer.tasks.set(task.taskId,task);updateTransferUi();
   if(typeof window.AASTasks?.wait!=='function')throw Error('任务查询模块尚未加载');
   const terminal=await window.AASTasks.wait(task.taskId,{
     onUpdate:update=>{
       task.progress=update.progress;
       task.cancellable=update.status==='running'&&update.cancellable===true;
       task.cancelRequested=task.cancelRequested||update.status==='cancelling';
       task.message=update.message||update.stage_label||update.status;
       if(!task.cancellable&&update.cancel_reason&&!task.message.includes(update.cancel_reason))
         task.message+='；'+update.cancel_reason;
       updateTransferUi();
     },
     onError:error=>{task.message='任务状态暂时不可用，正在继续查询：'+error.message;updateTransferUi();},
   });
   if(terminal?.status==='completed'){
     task.cancellable=false;
     if(!validTaskResult(terminal.result,meta.operation))throw Error('任务已完成，但未返回有效的操作结果');
     transfer.tasks.delete(task.taskId);
     if(meta.operation==='commit')transfer.pendingCommit=null;
     return terminal.result;
   }
   if(terminal?.status==='failed'||terminal?.status==='cancelled'){
     transfer.tasks.delete(task.taskId);
     if(meta.operation==='commit')transfer.pendingCommit=null;
     throw Error(terminal.status==='cancelled'?'CSV 导入任务已取消':
       terminal.failure?.message||terminal.failure?.title||terminal.error||'CSV 导入任务失败');
   }
   if(terminal?.status==='interrupted'){
     task.cancellable=false;
     throw Error('服务重启导致 CSV 导入任务中断，请先核对任务与导入记录');
   }
   throw Error('无法确认 CSV 导入任务的最终状态');
  }catch(error){
    if(rejectedByServer&&meta.operation==='commit')transfer.pendingCommit=null;
    if(task.taskId&&transfer.tasks.has(task.taskId)){
      task.message=error.message+'；执行结果尚未确认，请在任务查询与导出中核对。';
      throw Error(task.message+' · 任务 '+task.taskId);
    }
    if(meta.operation==='commit'&&transfer.pendingCommit)throw Error(error.message+'；'+pendingCommitMessage());
    throw error;
  }finally{updateTransferUi();}
}
function selectedSourceTable(){return id('v2ImpSqliteTable').value;}
function sourceIsBinary(file){return ['csv','gzip','sqlite'].includes(sourceType(file));}
function validateSourceFile(file){
  const type=sourceType(file);
  if(type==='unknown')throw Error('只支持 CSV、CSV.GZ、SQLite 或 DB 文件');
  // CSV 与 CSV.GZ 均走二进制源文件接口，避免浏览器默认 UTF-8 解码破坏原始字节。
  if(type==='csv'&&file.size>12_000_000)throw Error('普通 CSV 上限 12MB，请按运行或时间范围拆分');
  if(type!=='csv'&&file.size>64_000_000)throw Error('CSV.GZ / SQLite 上传上限 64MB（'+(type==='gzip'?'GZIP 解压后不得超过 12MB，请分时段导出':'请拆分后导入')+'）');
  if(!file.size)throw Error('文件为空');
  return type;
}
form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);
for(const name of ['v2ImpNamespace','v2ImpModel'])id(name).addEventListener('input',()=>{
 if(!manualMapping&&Object.keys(autoMapping).length){
  const parsed={items:chosenColumns().map(column=>({column,model:id('v2ImpModel').value.trim(),namespace:id('v2ImpNamespace').value.trim(),...autoMapping[column]}))};
  renderMapping(parsed);
 }
});
id('v2ImpFile').addEventListener('change',()=>{
 mappingGeneration++;inspectedFile=null;sourceHeaders=[];selectedColumns=new Set();csvHasLabels=null;
 id('v2ImpCsvEncoding').value='auto';id('v2ImpCsvDelimiter').value='auto';
 id('v2ImpCsvFormatConfirmed').checked=false;id('v2ImpCsvFormatConfirmed').disabled=true;csvFormatReview=null;
 id('v2ImpNormalConfirmed').checked=false;id('v2ImpUnknownTestConfirmed').checked=false;
 id('v2ImpMappingRows').replaceChildren();id('v2ImpMeasureColumns').replaceChildren();showQuality(null);
 id('v2ImpSqliteTableField').hidden=true;
 const file=id('v2ImpFile').files?.[0];id('v2ImpCsvFormatField').hidden=!['csv','gzip'].includes(sourceType(file));
 invalidate();importWizard?.reset();updateLabelControls();
 tell(file?'已选择文件。点击“检查文件与字段”，读取格式和列信息。':'请选择本地文件。');
});
id('v2ImpMapFile').addEventListener('change',()=>inspectFiles({useCurrentFormat:true}));
id('v2ImpMapClear')?.addEventListener('click',()=>{
 id('v2ImpMapFile').value='';manualMapping=false;manualMetadata={};
 inspectFiles({useCurrentFormat:true});
});
id('v2ImpSqliteTable').addEventListener('change',()=>inspectFiles({useCurrentFormat:true}));
id('v2ImpCsvInspect').addEventListener('click',()=>inspectFiles({useCurrentFormat:true}));
for(const name of ['v2ImpCsvEncoding','v2ImpCsvDelimiter'])id(name).addEventListener('change',()=>{
 mappingGeneration++;csvFormatReview=null;id('v2ImpCsvFormatConfirmed').checked=false;id('v2ImpCsvFormatConfirmed').disabled=true;invalidate();importWizard?.reset();
 tell('编码或分隔符已修改，请点击“检查文件与字段”，再确认后预检。');
});
id('v2ImpCsvFormatConfirmed').addEventListener('change',()=>{
 if(!id('v2ImpCsvFormatConfirmed').checked)invalidate();
 syncWizardFile();
});
id('v2ImpRole').addEventListener('change',()=>{updateLabelControls();invalidate();});
id('v2ImpTimeDomain').addEventListener('change',view);
// RFC-4180 quoted fields, including embedded commas, quotes and newlines.
function csvRows(text){
  const rows=[];let row=[],cell='',quoted=false;
  text=String(text).replace(/^\uFEFF/,'');
  for(let i=0;i<text.length;i++){
    const c=text[i];if(c==='"'){
      if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;
    }else if(c===','&&!quoted){row.push(cell);cell='';}
    else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(v=>v.trim()))rows.push(row);row=[];cell='';}
    else cell+=c;
  }
  if(quoted)throw Error('映射 CSV 引号没有闭合');
  row.push(cell);if(row.some(v=>v.trim()))rows.push(row);
  return rows;
}
async function mappingFromFile(){
  const file=id('v2ImpMapFile').files?.[0];if(!file)return mappingFromAuto();
  if(file.size>200000)throw Error('映射文件大小上限为 200KB');
  const rows=csvRows(await file.text());
  const header=(rows.shift()||[]).map(x=>x.trim().toLowerCase());
  const keys=['column','model','namespace','subsystem','device','unit','variable'];
  if(keys.some(k=>!header.includes(k)))throw Error('映射表必须包含 column,model,namespace,subsystem,device,unit,variable 列');
  const metadata={};let base=null;
  if(!rows.length)throw Error('映射表至少需要一行参量');
  for(let i=0;i<rows.length;i++){
    const row=rows[i];if(row.length!==header.length)throw Error(`映射表第 ${i+2} 行列数错误`);
    const item=Object.fromEntries(header.map((key,index)=>[key,row[index].trim()]));
    if(keys.some(key=>!item[key]))throw Error(`映射表第 ${i+2} 行存在空白必填字段`);
    if(metadata[item.column])throw Error(`映射文件中重复列名：${item.column}`);
    if(!base)base=item;
    if(item.model!==base.model||item.namespace!==base.namespace)throw Error('一次导入只支持一个型号与一个独立运行标识；请拆分 CSV');
    metadata[item.column]={subsystem:item.subsystem,device:item.device,unit:item.unit,variable:item.variable};
  }
  return {base,metadata,items:rows.map(row=>Object.fromEntries(header.map((key,index)=>[key,row[index].trim()]))) };
}
function mappingFromAuto(){
 const base={model:id('v2ImpModel').value.trim(),namespace:id('v2ImpNamespace').value.trim()};
 if(!base.model||!base.namespace)throw Error('请填写卫星型号和运行命名空间');
 const items=chosenColumns().map(column=>({column,...base,...autoMapping[column]}));
 if(!items.length)throw Error('请先选择遥测文件并加载列目录');
 for(const row of items){
  if(['subsystem','device','variable','unit'].some(key=>!row[key]))throw Error('参量 '+row.column+' 未完成属性或单位绑定，请在表格编辑');
 }
 return {base,metadata:Object.fromEntries(items.map(x=>[x.column,{subsystem:x.subsystem,device:x.device,variable:x.variable,unit:x.unit}])),items};
}
function renderMapping(parsed){
 const host=id('v2ImpMappingRows');host.replaceChildren();
 const table=document.createElement('table');
 const thead=document.createElement('thead');const hr=document.createElement('tr');
 for(const name of ['源数据列','卫星型号','数据来源 / 运行','分系统','部件','参量名称','单位']){
   const th=document.createElement('th');th.textContent=name;hr.appendChild(th);
 }
 thead.appendChild(hr);table.appendChild(thead);
 const body=document.createElement('tbody');
 for(const row of parsed.items){
   const tr=document.createElement('tr');
   for(const key of ['column','model','namespace','subsystem','device','variable','unit']){
     const td=document.createElement('td');
     if(!manualMapping&&['subsystem','device','variable','unit'].includes(key)){
       const input=document.createElement('input');input.type='text';input.value=row[key]||'';
       input.style.minWidth='85px';input.style.maxWidth='145px';input.maxLength=100;
       input.setAttribute('aria-label',row.column+' '+key);
       input.addEventListener('input',()=>{autoMapping[row.column][key]=input.value.trim();invalidate();});
       td.append(input);
     }else td.textContent= row[key]||'待确认';
     tr.appendChild(td);
   }
   body.appendChild(tr);
 }
 table.appendChild(body);host.appendChild(table);
}
async function inspectFiles({useCurrentFormat=false}={}){
 const generation=++mappingGeneration;
 const dataFile=id('v2ImpFile').files?.[0],mapFile=id('v2ImpMapFile').files?.[0];
 const clearMapping=id('v2ImpMapClear');if(clearMapping)clearMapping.disabled=!mapFile;
 let sourceInspected=false;
 const sameFile=inspectedFile===dataFile;
 const previousTime=roleTime,previousLabel=roleLabel,previousSelected=new Set(selectedColumns);
 const previousMapping=autoMapping;
 const host=id('v2ImpMappingRows');host.replaceChildren();csvHasLabels=null;sourceHeaders=[];autoMapping={};manualMetadata={};csvFormatReview=null;
 id('v2ImpCsvFormatConfirmed').checked=false;id('v2ImpCsvFormatConfirmed').disabled=true;
 updateLabelControls();invalidate();importWizard?.reset();
 if(!dataFile){id('v2ImpSqliteTableField').hidden=true;id('v2ImpCsvFormatField').hidden=true;return;}
 inspectionsInFlight++;setBusy(true);
 try{
   const type=validateSourceFile(dataFile);updateRunSource(dataFile);
   const sqlField=id('v2ImpSqliteTableField'),sqlSelect=id('v2ImpSqliteTable');
   const csvField=id('v2ImpCsvFormatField'),csvLike=type==='csv'||type==='gzip';
   sqlField.hidden=type!=='sqlite';csvField.hidden=!csvLike;
   let header=[],inspection=null;
   if(csvLike){
     const requested=useCurrentFormat?{csv_encoding:id('v2ImpCsvEncoding').value,
       csv_delimiter:id('v2ImpCsvDelimiter').value}:{csv_encoding:'auto',csv_delimiter:'auto'};
     const result=await binaryApi(dataFile,{operation:'inspect',filename:dataFile.name,...requested,
        ...(sameFile&&previousTime?{time_column:previousTime,label_column:previousLabel}:{})});
     if(generation!==mappingGeneration)return;
     if(result.format_review_required!==true||!result.csv_encoding||!result.csv_delimiter)
       throw Error('服务器未返回可确认的 CSV 格式，请检查服务版本');
     header=result.columns||[];inspection=result;
     id('v2ImpCsvEncoding').value=result.csv_encoding;
     id('v2ImpCsvDelimiter').value=result.csv_delimiter;
     id('v2ImpCsvFormatConfirmed').checked=false;id('v2ImpCsvFormatConfirmed').disabled=false;
     csvFormatReview={file:dataFile,encoding:result.csv_encoding,delimiter:result.csv_delimiter};
   }else{
     id('v2ImpCsvFormatConfirmed').checked=false;id('v2ImpCsvFormatConfirmed').disabled=true;
     const previous=sqlSelect.value;
     const result=await binaryApi(dataFile,{operation:'inspect',filename:dataFile.name});
     if(generation!==mappingGeneration)return;
     const tables=result.tables||[];
     sqlSelect.replaceChildren();
     for(const table of tables){
       const opt=document.createElement('option');opt.value=table.name;opt.textContent=`${table.name} (${table.columns.length} 列)`;
       sqlSelect.append(opt);
     }
     if(!tables.length)throw Error('当前 SQLite 没有符合条件的遥测表（需 timestamp/time_s 和数值参量列）');
     sqlSelect.value=tables.some(row=>row.name===previous)?previous:tables[0].name;
     const chosen=tables.find(row=>row.name===sqlSelect.value);
     header=chosen.columns;inspection=chosen;
   }
   if(generation!==mappingGeneration)return;
   if(new Set(header).size!==header.length||!header.length)throw Error('数据列名重复或表头为空');
   sourceHeaders=header;inspectedFile=dataFile;
   numericSuggestions=inspection.numeric_columns||header.filter(x=>!METADATA_COLUMNS.includes(x));
   const suggestedTime=header.find(x=>['time_s','timestamp'].includes(x))||inspection.time_candidates?.[0]||'';
   const suggestedLabel=header.find(x=>['label','fault_label'].includes(x))||'';
   roleTime=sameFile?(header.includes(previousTime)?previousTime:''):suggestedTime;
   roleLabel=sameFile?(header.includes(previousLabel)&&previousLabel!==roleTime?previousLabel:''):suggestedLabel;
   selectedColumns=sameFile?new Set([...previousSelected].filter(x=>header.includes(x))):new Set(numericSuggestions);
   selectedColumns.delete(roleTime);selectedColumns.delete(roleLabel);
   renderRoleControls();showQuality(inspection.quality);
   sourceInspected=true;
   const measurements=chosenColumns();
   if(!header.length)throw Error('找不到数据列');
   manualMapping=Boolean(mapFile);
   if(!mapFile){
     const reference=await loadCatalog();if(generation!==mappingGeneration)return;
     for(const name of header.filter(x=>x!==roleTime&&x!==roleLabel)){
       const known=reference[name];
       autoMapping[name]=(sameFile&&previousMapping[name])?{...previousMapping[name]}:
         known?{...known}:{subsystem:'',device:'',variable:'',unit:''};
     }
   }
   const parsed=manualMapping?await mappingFromFile():{
     base:{model:id('v2ImpModel').value.trim(),namespace:id('v2ImpNamespace').value.trim()},
     metadata:autoMapping,
     items:measurements.map(column=>({column,model:id('v2ImpModel').value.trim(),namespace:id('v2ImpNamespace').value.trim(),...autoMapping[column]}))
   };if(generation!==mappingGeneration)return;
   if(manualMapping){
     if(!id('v2ImpModel').value)id('v2ImpModel').value=parsed.base.model;
     if(!id('v2ImpNamespace').value)id('v2ImpNamespace').value=parsed.base.namespace;
   }
   if(manualMapping)manualMetadata=parsed.metadata;
   const missing=measurements.filter(h=>!parsed.metadata[h]);
   const extra=manualMapping?[]:parsed.items.filter(item=>!measurements.includes(item.column)).map(item=>item.column);
   if(missing.length||extra.length){
     throw Error('映射列与源数据不一致：'+(missing.length?'未映射 '+missing.join('、')+'；':'')+(extra.length?'不存在 '+extra.join('、'):'') );
   }
   renderMapping({...parsed,items:parsed.items.filter(item=>measurements.includes(item.column))});
   syncWizardFile();
   const unresolved=parsed.items.filter(row=>['subsystem','device','variable','unit'].some(k=>!row[k]));
   const formatHint=csvLike?`编码 ${csvFormatReview.encoding}；分隔符 ${csvFormatReview.delimiter==='\t'?'TAB':csvFormatReview.delimiter}。请勾选“已核对格式”后再预检。 `:'';
   tell(`${formatHint}识别 ${parsed.items.length} 个参量列，${unresolved.length} 列需要人工确认${unresolved.length?'（包括缺少明确单位的字段）':''}。请核对运行身份、列属性后预检。`);
 }catch(error){if(generation===mappingGeneration){
   // A valid source inspection survives a mapping error. The user must still
   // approve its format, but can then reach the mapping picker and remove or
   // replace the bad mapping. Preview/commit remain revoked by invalidate().
   if(sourceInspected)syncWizardFile();
   else{csvFormatReview=null;importWizard?.fileReady(false);}
   const recovery=sourceInspected?'。数据文件已读取；完成第一步的格式确认后，请进入第二步修正或移除映射文件。':'';
   tell('列绑定检查失败：'+error.message+recovery,true);
 }}
 finally{inspectionsInFlight--;if(inspectionsInFlight===0)setBusy(false);}
}
const PRESET_KEY='aas.v2.column_presets.v1';
function readPresets(){
 const data=localStorage.getItem(PRESET_KEY);
 if(!data)return [];
 const result=JSON.parse(data);
 if(!Array.isArray(result))throw Error('本机预设内容格式无效，请检查浏览器存储');
 return result;
}
function writePresets(items){localStorage.setItem(PRESET_KEY,JSON.stringify(items));}
function presetSignature(header=sourceHeaders){return JSON.stringify([...header].sort());}
function refreshPresets(name=''){
 const select=id('v2ImpPresetSelect');select.replaceChildren();option(select,'','选择预设');
 for(const item of readPresets())option(select,item.name,item.name);
 if(name)select.value=name;
}
function presetStatus(message,error=false){const target=id('v2ImpPresetStatus');
 target.textContent=message;target.classList.toggle('error',error);}
function presetAction(fn){try{fn();}catch(error){presetStatus('预设操作失败：'+error.message,true);}}
id('v2ImpSavePreset').addEventListener('click',()=>presetAction(()=>{
 const name=id('v2ImpPresetName').value.trim();
 if(!name||name.length>50||!sourceHeaders.length||!roleTime||!chosenColumns().length)
   throw Error('请先检查文件、选择时间列和有效参量，并填写预设名称');
 const metadata=manualMapping?manualMetadata:autoMapping;
 const mapping=Object.fromEntries(chosenColumns().map(x=>[x,{...(metadata[x]||{})}]));
 const preset={name,header_signature:presetSignature(),time_column:roleTime,label_column:roleLabel,
    selected_columns:chosenColumns(),column_metadata:mapping};
 const existing=readPresets().filter(x=>x.name!==name);
 if(existing.length>=30)throw Error('本机最多保存 30 组映射预设');
 writePresets([...existing,preset]);refreshPresets(name);
 presetStatus(`已保存“${name}”列映射预设（仅本机）`);
}));
id('v2ImpApplyPreset').addEventListener('click',()=>presetAction(()=>{
 const name=id('v2ImpPresetSelect').value;
 const preset=readPresets().find(x=>x.name===name);
 if(!preset)throw Error('请先选择已有预设');
 if(!sourceHeaders.length||preset.header_signature!==presetSignature())
   throw Error('当前文件列集合与预设不一致，拒绝套用');
 if(!sourceHeaders.includes(preset.time_column)||
    (preset.label_column&&!sourceHeaders.includes(preset.label_column))||
    !Array.isArray(preset.selected_columns)||
    preset.selected_columns.some(x=>!sourceHeaders.includes(x)||x===preset.time_column||x===preset.label_column))
   throw Error('预设列角色无效');
 roleTime=preset.time_column;roleLabel=preset.label_column||'';
 selectedColumns=new Set(preset.selected_columns);autoMapping={...autoMapping,...preset.column_metadata};
 manualMapping=false;id('v2ImpMapFile').value='';
 renderRoleControls();renderCurrentMapping();invalidate();
 presetStatus(`已应用“${name}”预设，请核对单位和参量后重新预检`);
}));
id('v2ImpDeletePreset').addEventListener('click',()=>presetAction(()=>{
 const name=id('v2ImpPresetSelect').value;
 if(!name)throw Error('请选择要删除的预设');
 writePresets(readPresets().filter(x=>x.name!==name));refreshPresets();
 presetStatus(`已删除“${name}”预设`);
}));
try{refreshPresets();}catch(error){presetStatus('浏览器本机预设不可用：'+error.message,true);}
function downloadTemplate(){
  const csv='\uFEFFcolumn,model,namespace,subsystem,device,unit,variable\r\nmeasured_speed_rad_s_0,SIM-SAT-001,mission_001,ADCS,reaction_wheel,rad/s,speed_0\r\nmeasured_speed_rad_s_1,SIM-SAT-001,mission_001,ADCS,reaction_wheel,rad/s,speed_1\r\n';
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download='AAS_参量列映射模板.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
id('v2ImpMapTemplate').addEventListener('click',downloadTemplate);
form.addEventListener('submit',async event=>{event.preventDefault();if(busy||!form.reportValidity())return;
 invalidate();const revision=uiRevision;const file=id('v2ImpFile').files?.[0];if(!file){tell('请先选择本地文件',true);return;}
 let type;try{type=validateSourceFile(file);}catch(error){tell(error.message,true);return;}
 let parsed;try{parsed=await mappingFromFile();}catch(error){tell(error.message,true);return;}
 const {base}=parsed;
 const names=chosenColumns();
 const metadata=Object.fromEntries(Object.entries(parsed.metadata).filter(([key])=>names.includes(key)));
 if(manualMapping&&(id('v2ImpModel').value.trim()!==base.model||id('v2ImpNamespace').value.trim()!==base.namespace)){
   tell('映射 CSV 中的型号/运行命名空间与当前来源填写的不一致，请重新核对后导入。',true);return;
 }
 const headers=sourceHeaders;
 if(!headers.length){tell('请先等待文件字段检查完成',true);return;}
 if(!roleTime||!headers.includes(roleTime)){tell('请选择正确的时间列',true);return;}
 const missing=names.filter(h=>!metadata[h]);const extra=Object.keys(metadata).filter(h=>!names.includes(h));
 if(!names.length||missing.length||extra.length){tell('列映射不完整或与源数据不一致：'+[...missing,...extra].join('、'),true);return;}
 const noLabels=!roleLabel;
 if(noLabels&&id('v2ImpRole').value==='train'&&!id('v2ImpNormalConfirmed').checked){tell('请确认训练数据全部正常；无标签不能自动视为正常。',true);return;}
 if(noLabels&&id('v2ImpRole').value==='test'&&!id('v2ImpUnknownTestConfirmed').checked){tell('请确认无标签测试数据将保持未知标签。',true);return;}
 renderMapping(parsed);
 let targetMetadata;
 try{targetMetadata=await readTargetEvidence();}catch(error){tell(error.message,true);return;}
 const first=Object.values(metadata)[0];
 const identity={namespace:base.namespace,model:base.model};
 if(!identity.namespace||!identity.model){tell('必须填写运行命名空间与型号',true);return;}
 const payload={...values(),...identity,subsystem:first.subsystem,device:first.device,unit:first.unit,
  variable:first.variable,filename:file.name,commit:false,column_metadata:metadata,require_explicit_mapping:true,
  time_column:roleTime,label_column:roleLabel,selected_columns:names,
  import_context:{run_token:id('v2ImpRunToken').value.trim(),dataset_split:id('v2ImpDatasetSplit').value,
   group_id:id('v2ImpGroup').value.trim(),run_role:id('v2ImpRunRole').value,origin_type:id('v2ImpOrigin').value,
   label_scope:id('v2ImpLabelScope').value,...(targetMetadata?{target_metadata:targetMetadata}:{})},
  all_normal_confirmed:id('v2ImpNormalConfirmed').checked,unlabeled_test_confirmed:id('v2ImpUnknownTestConfirmed').checked};
 setBusy(true);tell('正在校验时间、各参量、标签和已有分片冲突…');
 try{
   let result,request;
   let formatMeta={};
   if(type==='csv'||type==='gzip'){
     const chosen={csv_encoding:id('v2ImpCsvEncoding').value,csv_delimiter:id('v2ImpCsvDelimiter').value};
     if(!csvFormatReview||csvFormatReview.file!==file||!id('v2ImpCsvFormatConfirmed').checked||
        chosen.csv_encoding!==csvFormatReview.encoding||chosen.csv_delimiter!==csvFormatReview.delimiter){
       tell('请先检查并确认 CSV 编码与分隔符；文件或格式变化后必须重新检查。',true);return;
     }
     formatMeta={...chosen,format_confirmed:true};
   }
   if(sourceIsBinary(file)){
     request={file,meta:{operation:'preview',filename:file.name,...formatMeta,
       table:type==='sqlite'?selectedSourceTable():undefined,config:payload},binary:true};
     result=await binaryApi(file,request.meta);
   }else{
     request={endpoint:endpoint(),payload:{...payload,csv:await file.text()},binary:false};
     result=await api(request.endpoint,request.payload);
   }
   if(revision!==uiRevision){tell('预检期间文件或映射已变化，请重新预检。',true);return;}
   candidate=request;
   importWizard?.review(importSummary(result,payload));
   if(id('v2ImpAudit'))id('v2ImpAudit').textContent=`${formatMeta.csv_encoding?`CSV 格式 ${formatMeta.csv_encoding}/${formatMeta.csv_delimiter==='\t'?'TAB':formatMeta.csv_delimiter}；\n`:''}预检通过：${result.row_count} 个时间采样；${result.column_count||1} 个参量。\n时间 ${result.time_start} ～ ${result.time_end}\n${(result.columns||[]).map(c=>c.source_column+' → '+base.model+'/'+c.subsystem+'/'+c.device+'/'+c.variable).join('\n')}\n标签：${result.label_status==='unknown'?'未知（不能计算需要真值的评估指标）':result.label_status==='confirmed_normal'?'用户确认全部正常':'来自源文件'}\n原始文件 SHA-256 ${result.sha256}\n质量统计：缺测 ${Object.values(result.quality?.missing_by_column||{}).reduce((a,b)=>a+b,0)}；中位采样间隔 ${result.quality?.sample_period_median_seconds??'—'} 秒；大于3倍采样间隔的缺口 ${result.quality?.gaps_gt_3x_median??'—'}；源数据乱序 ${result.quality?.source_order_reversals??'—'}；正常/故障/未知标签 ${result.quality?.label_counts?.['0']??'—'} / ${result.quality?.label_counts?.['1']??'—'} / ${result.quality?.label_counts?.['unknown']??'—'}\n运行身份：${result.import_context?.run_token||'未声明'}；split=${result.import_context?.dataset_split||'未声明'}；标签作用范围=${result.import_context?.label_scope||'未知'}；凭据=${result.import_context?.evidence_origin||'无'}\n请确认后执行导入。`;
   tell(`预检通过：${result.row_count} 帧、${result.column_count||1} 个参量。请核对上方摘要，再确认导入。`);
 }catch(error){tell(error.message,true);}finally{setBusy(false);}
});
commit.addEventListener('click',async()=>{if(busy||!candidate)return;
 if(transfer.pendingCommit){tell(pendingCommitMessage(),true);return;}
 if(!window.confirm('确认将已预检的全部参量写入遥测数据库？多列写入将作为一个事务完成。'))return;
 const approvedConfig=candidate.meta?.config||candidate.payload;
 setBusy(true);tell('正在事务写入…');try{
   const result=candidate.binary?
      await binaryApi(candidate.file,{...candidate.meta,operation:'commit'}):
      await api(candidate.endpoint,{...candidate.payload,commit:true});
   tell(`导入完成：${result.row_count} 个时间采样、${result.column_count||1} 个逻辑参量，所有列已入库。`);
   const summary=importSummary(result,approvedConfig);
   invalidate();csvFormatReview=null;id('v2ImpCsvFormatConfirmed').checked=false;id('v2ImpCsvFormatConfirmed').disabled=true;document.dispatchEvent(new CustomEvent('aas:v2-imported'));
   importWizard?.complete(summary);
   if(window.parent!==window)window.parent.postMessage({type:'aas:v2-imported',keepOpen:Boolean(importWizard)},location.origin);refreshHistory();
 }catch(error){invalidate();tell('导入未完成：'+error.message,true);}finally{setBusy(false);}
});
async function refreshHistory(){const output=id('v2ImpHistory');output.textContent='正在读取…';try{const response=await fetch('/api/v2/telemetry/imports?limit=30');const data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');output.replaceChildren();for(const row of data.items||[]){const line=document.createElement('div');line.className='v2-import-item';line.textContent=`${row.model} / ${row.subsystem} / ${row.device} / ${row.variable} · ${row.role}`+
  `${row.dataset_split?' · '+row.dataset_split:''}${row.run_token?' · '+row.run_token:''}`+
  ` · ${row.source_row_count} 行 · 缺测 ${Object.values(row.quality?.missing_by_column||{}).reduce((a,b)=>a+b,0)} · ${row.source_name}`;output.append(line);}if(!data.items?.length)output.textContent='暂无导入记录。';}catch(error){output.textContent=error.message;}}
id('v2ImpRefresh').addEventListener('click',refreshHistory);view();refreshHistory();
})();
