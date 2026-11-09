/* In-page CSV source import for V2 training and evaluation; no legacy tables. */
(() => {'use strict';
const $=id=>document.getElementById(id);
const snapshots={train:null,eval:null};
const activeUploads={train:null,eval:null};
const activeTasks={train:null,eval:null};
const roleBusy={train:false,eval:false};
const wizards=Object.fromEntries(['train','eval'].map(role=>[role,
 window.AASCsvImportWizard?.create({rootId:role+'CsvWizard',purpose:role,
  nextLabel:role==='train'?'检查训练时间范围':'选择模型并检查测试数据',
  nextTarget:role==='train'?'trainStart':'evalModel',
  onNext:()=>{const dialog=$(role+'CsvDialog');if(dialog?.open)dialog.close();
   $(role==='train'?'tabTrain':'tabEval')?.click?.();}})]));
function importSummary(role,info={}){
 return {...info,filename:$(role+'CsvFile').files?.[0]?.name||info.filename,
  role:role==='train'?'train':'test',
  columns:(info.columns||[]).map(item=>typeof item==='string'?item:item.source_column||item.variable),
  label_status:info.label_status||(!info.has_labels?'unknown':'provided')};
}
function formatReady(role){const current=snapshots[role],chosen=format(role);
 return Boolean(current&&current.file===$(role+'CsvFile').files?.[0]&&$(role+'CsvFormatConfirmed').checked&&
  chosen.csv_encoding===current.format.csv_encoding&&chosen.csv_delimiter===current.format.csv_delimiter);}
function note(role,text,error=false){const host=$(role+'CsvStatus');host.textContent=text;
 host.style.color=error?'#b42336':'#31566f';if(error)wizards[role]?.error(text);}
function metadataHeader(meta){const bytes=new TextEncoder().encode(JSON.stringify(meta));
 if(bytes.length>22000)throw Error('CSV 映射参数过大');
 let binary='';for(const b of bytes)binary+=String.fromCharCode(b);return btoa(binary);}
function progress(role,value,message){
 const bar=$(role+'CsvProgress'),label=$(role+'CsvProgressMeta');
 if(bar)bar.value=Math.max(0,Math.min(100,Number(value)||0));
 if(label)label.textContent=message||'';
}
function cancelEnabled(role,enabled){const button=$(role+'CsvCancel');if(button)button.disabled=!enabled;}
function uploadFile(file,meta,role){
 const headers={'Content-Type':'application/octet-stream',
  'X-AAS-Import-Metadata':metadataHeader(meta),
  'X-AAS-CSV-Role':role==='train'?'train':'test'};
 if(typeof XMLHttpRequest==='undefined'){
  return fetch('/api/v2/user-csv-workspace/import-source',{method:'POST',headers,body:file}).then(async response=>{
   let payload;try{payload=await response.json();}catch{throw Error('后端未返回有效的导入结果');}
   if(!response.ok)throw Error(payload.error||`导入失败 HTTP ${response.status}`);
   progress(role,10,'文件上传完成，等待后端处理');return payload;
  });
 }
 return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();activeUploads[role]=xhr;cancelEnabled(role,true);
  xhr.open('POST','/api/v2/user-csv-workspace/import-source',true);
  for(const [key,value] of Object.entries(headers))xhr.setRequestHeader(key,value);
  xhr.upload.onprogress=event=>{
   if(event.lengthComputable)progress(role,Math.min(10,Math.round(10*event.loaded/event.total)),
    `上传文件 ${event.loaded}/${event.total} 字节`);
  };
  xhr.onload=()=>{
   activeUploads[role]=null;
   let payload;try{payload=JSON.parse(xhr.responseText||'{}');}catch{reject(Error('后端未返回有效的导入结果'));return;}
   if(xhr.status<200||xhr.status>=300){cancelEnabled(role,false);reject(Error(payload.error||`导入失败 HTTP ${xhr.status}`));return;}
   progress(role,10,'文件上传完成，等待后端处理');resolve(payload);
  };
  xhr.onerror=()=>{activeUploads[role]=null;cancelEnabled(role,false);reject(Error('文件上传失败，请检查网络连接'));};
  xhr.onabort=()=>{activeUploads[role]=null;cancelEnabled(role,false);progress(role,0,'上传已中止');reject(Error('上传已中止；服务端会清理未完整接收的文件'));};
  xhr.send(file);
 });
}
async function transmit(file,meta,role){
 if(!file||!file.size||file.size>64_000_000||!/\.csv(?:\.gz)?$/.test(file.name.toLowerCase()))
   throw Error('请选择大小不超过 64 MB 的 CSV 或 CSV.GZ 文件');
 if(activeUploads[role]||activeTasks[role])throw Error('当前已有导入任务正在运行');
 progress(role,0,'准备上传');
 const payload=await uploadFile(file,meta,role);
 if(payload.status!=='accepted'||!payload.task_id){cancelEnabled(role,false);progress(role,100,'操作完成');return payload;}
 activeTasks[role]=payload.task_id;cancelEnabled(role,true);
 if(!window.AASTasks?.wait)throw Error('任务查询模块尚未加载；任务已提交，可在任务查询与导出页面查看');
 let terminal=null;
 try{
  terminal=await window.AASTasks.wait(payload.task_id,{onUpdate:task=>{
   progress(role,task.progress,task.message||task.stage_label||task.status);
   cancelEnabled(role,Boolean(task.cancellable)||task.status==='cancelling');
  }});
 }finally{
  if(terminal){activeTasks[role]=null;cancelEnabled(role,false);}
 }
 if(terminal.status==='completed'){
  progress(role,100,terminal.message||'任务完成');
  if(!terminal.result)throw Error('任务已完成，但未返回可展示的导入结果');
  return terminal.result;
 }
 if(terminal.status==='failed')throw Error(terminal.failure?.message||terminal.failure?.title||'CSV 导入失败');
 if(terminal.status==='cancelled')throw Error('CSV 导入任务已取消');
 if(terminal.status==='interrupted')throw Error('服务重启导致 CSV 导入中断；请重新检查文件后手动提交新任务');
 throw Error('CSV 导入任务进入未知状态：'+terminal.status);
}
async function cancelRole(role){
 const upload=activeUploads[role];
 if(upload){
  if(!window.confirm('当前文件仍在上传。中止上传后，服务器会清理未完整接收的文件。确定继续吗？'))return;
  upload.abort();return;
 }
 const taskId=activeTasks[role];
 if(!taskId||!window.AASTasks?.cancel)return;
 try{
  const result=await window.AASTasks.cancel(taskId);
  if(result.accepted)note(role,'已请求取消，正在等待当前安全步骤结束…');
  else if(!result.cancelled_by_user)note(role,result.reason||'当前阶段不能取消',true);
 }catch(error){note(role,error.message,true);}
}
function selectedColumns(role){const choices=$(role+'CsvColumns');
 return [...choices.querySelectorAll('input[type=checkbox]')].filter(input=>input.checked).map(input=>input.value);}
function mapper(column,role){
 const tokens=column.split('.');
 return {subsystem:tokens.length>=3?tokens[0]:$(role+'CsvSubsystem').value.trim(),
  device:tokens.length>=3?tokens.slice(1,-1).join('.'):$(role+'CsvDevice').value.trim(),
  variable:tokens.length>=3?tokens[tokens.length-1]:column,
  unit:$(role+'CsvUnit').value.trim()};
}
function config(role,info){
 const names=selectedColumns(role);
 if(!names.length)throw Error('请选择至少一个数值遥测参量');
 const model=$(role+'CsvModel').value.trim(),ref=$(role+'CsvReference').value.trim();
 if(!model||!ref)throw Error('请填写型号及时间基准或帧序列 ID');
 const frameMode=!info.columns.includes('time_s')&&!info.columns.includes('timestamp');
 if(frameMode&&!info.columns.includes('frame')&&!$(role+'CsvRowOrderConfirmed').checked)
  throw Error('无显式 frame 列时须确认原始行序代表帧序');
 const confirm=role==='train'?$('trainCsvNormalConfirmed').checked:$('evalCsvUnknownConfirmed').checked;
 if(!info.has_labels&&!confirm)throw Error(role==='train'?'无标签训练 CSV 必须确认全部正常':'无标签测试 CSV 必须确认真值未知');
 const mapping=Object.fromEntries(names.map(name=>[name,mapper(name,role)]));
 for(const item of Object.values(mapping))if(Object.values(item).some(value=>!value))throw Error('列映射中的分系统、部件、参量和单位不可留空');
 return {namespace:'USER_CSV_'+Date.now()+'_'+Math.round(Math.random()*1_000_000),
  model,subsystem:$(role+'CsvSubsystem').value.trim(),device:$(role+'CsvDevice').value.trim(),
  variable:'wide_csv',unit:$(role+'CsvUnit').value.trim(),role:role==='train'?'train':'test',
  ...(frameMode?{coordinate_mode:'frame',frame_sequence_id:ref,
      frame_column:info.columns.includes('frame')?'frame':null,
      row_order_confirmed:$(role+'CsvRowOrderConfirmed').checked}:
      {coordinate_mode:'time',time_domain:'numeric_unspecified',time_reference:ref,time_unit:'s'}),
  column_metadata:mapping,selected_columns:names,bind_digest_group:true,
  all_normal_confirmed:role==='train'&&confirm,
  unlabeled_test_confirmed:role==='eval'&&confirm};
}
function invalidate(role){snapshots[role]=null;$(role+'CsvCommit').disabled=true;$(role+'CsvPreview').disabled=true;
 wizards[role]?.reset();}
function invalidateApproval(role){const current=snapshots[role];
 if(current){current.config=null;current.preview=null;current.revision=(current.revision||0)+1;}
 $(role+'CsvCommit').disabled=true;$(role+'CsvPreview').disabled=!Boolean(current&&$(role+'CsvFormatConfirmed').checked);
 wizards[role]?.invalidate();wizards[role]?.fileReady(formatReady(role),importSummary(role,current?.info));
 note(role,current?'确认状态、参量或映射已调整，请重新预检后再确认导入':'请先检查文件与列');}
function format(role){return {csv_encoding:$(role+'CsvEncoding').value,
 csv_delimiter:$(role+'CsvDelimiter').value};}
function invalidateFormat(role){invalidate(role);$(role+'CsvPreview').disabled=true;
 $(role+'CsvFormatConfirmed').checked=false;
 $(role+'CsvColumns').replaceChildren();note(role,'文件解析格式已变更，请重新检查列并确认编码与分隔符');}
function confirmedFormat(role,current){const chosen=format(role);
 if(!$(role+'CsvFormatConfirmed').checked||chosen.csv_encoding==='auto'||chosen.csv_delimiter==='auto'||
  chosen.csv_encoding!==current.format.csv_encoding||chosen.csv_delimiter!==current.format.csv_delimiter)
   throw Error('请先明确确认 CSV 编码与分隔符；如已修改格式，请重新检查列');
 return chosen;}
function formatLabel(value){return value==='\t'?'TAB':value===';'?'分号':value===','?'逗号':value.toUpperCase();}
function availableFields(role,headers,numericColumns){
 const node=$(role+'CsvColumns');node.replaceChildren();
 const excluded=new Set(['time_s','timestamp','frame','label','fault_label','aas_frame','aas_injected_fault','aas_fault_events']);
 const suggestions=new Set(numericColumns);
 const candidates=headers.filter(name=>!excluded.has(name)&&!name.startsWith('aas_'));
 for(const name of candidates){const label=document.createElement('label'),check=document.createElement('input');
  check.type='checkbox';check.value=name;check.checked=suggestions.has(name);
  check.addEventListener('change',()=>invalidateApproval(role));
  const text=document.createElement('span');text.textContent=suggestions.has(name)?name:name+'（前 50 行未确认数值，默认不选）';
  label.append(check,text);node.append(label);}
}
async function inspect(role){const file=$(role+'CsvFile').files?.[0];
 invalidate(role);$(role+'CsvPreview').disabled=true;
 if(!file)throw Error('请先选择本地 CSV 文件');
 const requested=format(role);
 const result=await transmit(file,{operation:'inspect',filename:file.name,...requested},role);
 if(result.format_review_required!==true)throw Error('后端未提供可确认的文件解析格式，请检查服务版本');
 if($(role+'CsvFile').files?.[0]!==file||JSON.stringify(requested)!==JSON.stringify(format(role)))
  throw Error('文件或解析选项已变化，请重新检查');
 if(!result.columns.includes('time_s')&&!result.columns.includes('timestamp'))
  note(role,'检测到无时间列 CSV：将按 frame 列或经确认的行序导入帧序列。');
 $(role+'CsvEncoding').value=result.csv_encoding;
 $(role+'CsvDelimiter').value=result.csv_delimiter;
 $(role+'CsvFormatConfirmed').checked=false;
 availableFields(role,result.columns,result.numeric_columns||[]);
 snapshots[role]={file,info:result,format:format(role),config:null,revision:0};
 wizards[role]?.fileReady(false,importSummary(role,result));
 $(role+'CsvPreview').disabled=true;
 note(role,`识别编码 ${formatLabel(result.csv_encoding)}、分隔符 ${formatLabel(result.csv_delimiter)}；已读取 ${result.columns.length} 列，抽查 ${result.sampled_rows} 帧并推荐 ${(result.numeric_columns||[]).length} 个数值参量。${result.has_labels?'保留原始 0/1 标签':'未找到标签，需确认后导入'}。请先勾选“已核对格式”，再预检。`);
}
async function preview(role){
 const current=snapshots[role],file=$(role+'CsvFile').files?.[0];
 if(!current||current.file!==file)throw Error('文件已更换，请先重新检查');
 const chosen=confirmedFormat(role,current);
 const revision=current.revision;
 const payload=config(role,current.info);
 const result=await transmit(file,{operation:'preview',filename:file.name,config:payload,...chosen,format_confirmed:true},role);
 if(snapshots[role]!==current||current.revision!==revision||JSON.stringify(chosen)!==JSON.stringify(format(role)))
  throw Error('预检期间文件或映射设置已变化，请重新预检');
 current.config=payload;current.preview=result;
 $(role+'CsvCommit').disabled=false;
 wizards[role]?.review(importSummary(role,{...result,...chosen}));
 note(role,`预检通过：${result.row_count} 帧 · ${result.column_count} 个数值参量 · 标签状态 ${result.label_status} · 未写入数据库。确认后导入。`);
}
async function commit(role){
 const current=snapshots[role],file=$(role+'CsvFile').files?.[0];
 if(!current?.preview||current.file!==file)throw Error('必须先对同一文件预检');
 const chosen=confirmedFormat(role,current);
 // Namespace includes the import identity and must be byte-for-byte identical
 // between preview and commit. No second random ID may enter at this stage.
 const stable={...current.config,namespace:current.config.namespace};
 const latest={...config(role,current.info),namespace:stable.namespace};
 if(JSON.stringify(latest)!==JSON.stringify(stable))throw Error('导入设置已变化，请重新预检');
 $(role+'CsvCommit').disabled=true;
 const result=await transmit(file,{operation:'commit',filename:file.name,config:stable,...chosen,format_confirmed:true},role);
 note(role,`CSV 已保存到独立${role==='train'?'训练':'测试'}工作区：${result.row_count} 帧，数据集 ID=${result.dataset_id}；正在更新运行选择…`);
 invalidate(role);
 const summary=importSummary(role,{...result,...chosen});wizards[role]?.complete(summary);
 try{
  if(!window.AASRunnerV2?.onCsvImported)throw Error('训练评估模块尚未就绪，请刷新后手动选择数据');
  await window.AASRunnerV2.onCsvImported(role,result);
  note(role,`导入成功并选中独立工作区数据集 ${result.dataset_id}：${result.row_count} 帧、${result.column_count} 个参量。该 CSV 不写入历史数据库；请检查时间覆盖并预览训练/评估数据。`);
 }catch(error){const followup=`CSV 已保存到独立工作区，但自动选择暂未完成：${error.message}。请在本页“我的 CSV 数据集”中手动选择。`;
  note(role,`CSV 已入库，${followup}`,true);wizards[role]?.complete({...summary,followup_error:followup});
 }finally{
  // Reloading the catalog initializes its default tab; keep this receipt visible.
  $(role==='train'?'tabTrain':'tabEval')?.click?.();
 }
}
for(const role of ['train','eval']){
 $(role+'CsvFile').addEventListener('change',()=>{invalidate(role);$(role+'CsvPreview').disabled=true;
  $(role+'CsvColumns').replaceChildren();$(role+'CsvEncoding').value='auto';$(role+'CsvDelimiter').value='auto';
  $(role+'CsvFormatConfirmed').checked=false;
  $(role==='train'?'trainCsvNormalConfirmed':'evalCsvUnknownConfirmed').checked=false;
  note(role,'文件改变，请重新检查');});
 cancelEnabled(role,false);progress(role,0,'');
 $(role+'CsvCancel').addEventListener('click',()=>cancelRole(role));
 for(const suffix of ['CsvEncoding','CsvDelimiter'])$(role+suffix).addEventListener('change',()=>invalidateFormat(role));
 $(role+'CsvFormatConfirmed').addEventListener('change',()=>invalidateApproval(role));
 for(const suffix of ['CsvModel','CsvSubsystem','CsvDevice','CsvUnit','CsvReference']){
  $(role+suffix).addEventListener('input',()=>invalidateApproval(role));
 }
 $(role+'CsvRowOrderConfirmed').addEventListener('change',()=>invalidateApproval(role));
 $(role==='train'?'trainCsvNormalConfirmed':'evalCsvUnknownConfirmed').addEventListener('change',()=>invalidateApproval(role));
 for(const [suffix,action] of [['CsvInspect',inspect],['CsvPreview',preview],['CsvCommit',commit]]){
  $(role+suffix).addEventListener('click',async()=>{
    if(roleBusy[role])return;roleBusy[role]=true;wizards[role]?.setBusy(true);
    const button=$(role+suffix);button.disabled=true;
    try{await action(role);}catch(error){note(role,error.message,true);}
    finally{roleBusy[role]=false;wizards[role]?.setBusy(false);
     button.disabled=suffix==='CsvCommit'?!Boolean(snapshots[role]?.preview):suffix==='CsvPreview'?!formatReady(role):false;}
  });
 }
}
})();
