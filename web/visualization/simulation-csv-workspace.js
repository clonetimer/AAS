/* Full-table CSV simulation. Immutable snapshots, inclusive 1-based frame ranges. */
(() => {'use strict';
const $ = id => document.getElementById(id);
const state = {items:[], inspected:null, file:null, preview:null, config:null, sequence:0};
const API='/api/v2/csv-workspace';
const importWizard=window.AASCsvImportWizard?.create({rootId:'csvImportWizard',purpose:'simulation',
 nextLabel:'继续设置故障注入',nextTarget:'csvColumn'});
let importBusy=false;
function importSummary(info={},filename){return {filename:filename||state.file?.name||info.filename,
 row_count:info.frame_count,column_count:info.numeric_columns?.length,columns:info.numeric_columns||[],
 csv_encoding:info.csv_encoding,csv_delimiter:info.csv_delimiter,
 label_status:info.has_truth_labels?'provided':'unknown',
 column_notice:`按原始帧顺序保留全部 ${info.columns?.length??'已检查'} 列，文本列和已有标签一并保存。`};}
function tell(id,text,error=false){const e=$(id);e.textContent=text;e.classList.toggle('error',error);
 if(id==='csvImportStatus'&&error)importWizard?.error(text);}
function validateFrames(start,end,total){
 if(!Number.isInteger(start)||!Number.isInteger(end)||!Number.isInteger(total)||!Number.isFinite(total)
    ||!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||!Number.isSafeInteger(total)
    ||start<1||end<start||end>total){throw Error(`帧区间必须为 1～${total} 的整数闭区间`);}
 return {start_frame:start,end_frame:end};
}
window.AASCsvWorkspace=Object.freeze({validateFrames,refreshDatasets});
async function request(url,options){
 const response=await fetch(url,options);let result;
 try{result=await response.json();}catch{throw Error(`服务器返回非 JSON (${response.status})`);}
 if(!response.ok)throw Error(result.error||`请求失败：HTTP ${response.status}`);
 return result;
}
const get=path=>request(API+path);
const post=(path,data)=>request(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
async function binary(path,file,formatMeta={}){
 if(!file||!/\.csv(?:\.gz)?$/i.test(file.name)||!file.size||file.size>8_000_000)
   throw Error('请选择不超过 8 MB 的 CSV 或 CSV.GZ 文件');
 const bytes=new TextEncoder().encode(file.name);let name='';for(const ch of bytes)name+=String.fromCharCode(ch);
 return request(API+path,{method:'POST',headers:{'Content-Type':'application/octet-stream',
  'X-AAS-CSV-Filename-B64':btoa(name),
  'X-AAS-CSV-Format-B64':btoa(JSON.stringify(formatMeta))},body:file});
}
function option(select,value,label){const el=document.createElement('option');el.value=value;el.textContent=label;select.append(el);}
function invalidate(){state.preview=null;state.config=null;$('csvFaultSave').disabled=true;}
function selected(){return state.items.find(item=>item.id===$('csvDatasetSelect').value)||null;}
function asEvent(event){return `${event.fault_type} · ${event.column} · 第 ${event.start_frame}～${event.end_frame} 帧 · 幅值 ${event.magnitude}`;}
async function updateEventView(source,sequence){
 const node=$('csvEventHistory');node.replaceChildren();
 const history=await get('/events?id='+encodeURIComponent(source.id));
 if(sequence!==state.sequence)return;
 const title=document.createElement('strong');
 title.textContent=`故障事件时间线：${history.active_event_count}/${history.event_count} 项生效`;
 node.append(title);
 for(const event of history.events){
  const row=document.createElement('div');row.className='csv-event-row';
  const line=document.createElement('p');
  const overlap=(event.overlaps||[]).map(item=>`与 ${item.event_id.slice(0,8)} 重叠第 ${item.start_frame}～${item.end_frame} 帧`).join('；');
  const more=event.overlaps_truncated?`；另有 ${event.overlap_count-event.overlaps.length} 项重叠` : '';
  line.textContent=`#${event.sequence} · ${event.status==='active'?'生效':'已撤销'} · ${asEvent(event)}${overlap?' · '+overlap:''}${more}`;
  row.append(line);
  if(event.status==='active'){
   const button=document.createElement('button');button.type='button';button.className='danger';
   button.textContent='撤销此事件';
   button.addEventListener('click',async()=>{button.disabled=true;try{await retractEvent(source,event);}
    catch(error){tell('csvStatus',error.message,true);button.disabled=false;}});
   row.append(button);
  }
  node.append(row);
 }
 const operations=document.createElement('strong');
 operations.textContent=`版本操作记录：${(history.operations||[]).length} 项`;
 node.append(operations);
 for(const operation of history.operations||[]){
  const line=document.createElement('p');
  const label=operation.type==='event_retracted'
   ? `撤销事件 ${String(operation.event_id||'').slice(0,8)}`
   : operation.type==='version_rollback'
    ? `回退到祖先版本 ${String(operation.ancestor_id||'').slice(0,8)}`
    : `版本操作 ${operation.type||'未知'}`;
  line.textContent=`${label} · ${operation.created_utc||'时间未知'}`;
  node.append(line);
 }
}
async function populateAncestors(source,sequence){
 const select=$('csvAncestorSelect');select.replaceChildren();
 if(!source){option(select,'','没有可回退的祖先版本');return;}
 const result=await get('/ancestors?id='+encodeURIComponent(source.id));
 if(sequence!==state.sequence)return;
 if(!result.ancestors.length)option(select,'','当前已是原始版本');
 for(const ancestor of result.ancestors)
  option(select,ancestor.id,`${ancestor.filename} · v${ancestor.version} · ${ancestor.active_event_count} 项生效故障`);
 $('csvRollbackButton').disabled=!result.ancestors.length;
 $('csvCompareButton').disabled=!result.ancestors.length;
}
async function retractEvent(source,event){
 const preview=await post('/event-retract-preview',{dataset_id:source.id,event_id:event.event_id});
 const message=`撤销事件 ${event.event_id.slice(0,8)}（${event.column}，第 ${event.start_frame}～${event.end_frame} 帧）？\n预计变化 ${preview.changed_frame_count} 帧、${preview.changed_cell_count} 个单元格。\n系统将从原始 CSV 重放其余生效事件，并另存为新版本。`;
 if(!window.confirm(message))return;
 const saved=await post('/event-retract',{dataset_id:source.id,event_id:event.event_id,
  preview_sha256:preview.preview_sha256});
 tell('csvStatus',`事件已撤销，创建新版本 v${saved.version}；原版本保持不变。`);
 await refreshDatasets(saved.id);
}
async function compareSelectedAncestor(){
 const source=selected(),ancestorId=$('csvAncestorSelect').value;
 if(!source||!ancestorId)throw Error('请选择一个祖先版本进行比较');
 const result=await get('/compare?before_id='+encodeURIComponent(ancestorId)+'&after_id='+encodeURIComponent(source.id)+'&column='+encodeURIComponent($('csvColumn').value));
 const ancestor=state.items.find(item=>item.id===ancestorId);
 $('csvCompareSummary').textContent=`v${ancestor?.version??'?'} → v${source.version}：变化 ${result.changed_frame_count} 帧、${result.changed_cell_count} 个单元格；参量变化：${Object.entries(result.changed_by_column||{}).map(([name,count])=>name+' '+count).join('、')||'无'}；${result.changed_ranges.length} 段区间${result.changed_ranges_truncated?'（区间列表已截断）':''}；新增事件 ${result.events_added.length} 项，撤销事件 ${result.events_removed.length} 项。`;
 const points=result.points.map(point=>({frame:point.frame,original:point.before,value:point.after}));
 draw(points,result.frame_count,`版本比较 · ${result.column} · ${result.changed_ranges.length} 段变化区间`,[],true);
}
async function rollbackSelectedAncestor(){
 const source=selected(),ancestorId=$('csvAncestorSelect').value;
 if(!source||!ancestorId)throw Error('请选择一个祖先版本进行回退');
 const preview=await post('/rollback-preview',{dataset_id:source.id,ancestor_id:ancestorId});
 const ancestor=state.items.find(item=>item.id===ancestorId);
 const message=`将当前 v${source.version} 回退到祖先 v${ancestor?.version??'?'} 的数据内容？\n预计变化 ${preview.changed_frame_count} 帧、${preview.changed_cell_count} 个单元格。\n将撤销 ${preview.events_retracted||0} 项事件、恢复 ${preview.events_reactivated||0} 项事件。\n操作会创建新的子版本，不会删除或覆盖任何旧版本。`;
 if(!window.confirm(message))return;
 const saved=await post('/rollback',{dataset_id:source.id,ancestor_id:ancestorId,
  preview_sha256:preview.preview_sha256});
 tell('csvStatus',`版本回退完成：创建新版本 v${saved.version}；旧版本及事件账本均保留。`);
 await refreshDatasets(saved.id);
}

function svgElement(name,attrs){const el=document.createElementNS('http://www.w3.org/2000/svg',name);
 for(const [key,val] of Object.entries(attrs||{}))el.setAttribute(key,String(val));return el;}
function draw(points,total,desc,events=[],before=false){
 const plot=$('csvPreviewPlot');plot.replaceChildren();
 if(!Array.isArray(points)||!points.length){$('csvPlotDescription').textContent=desc;return;}
 const values=points.flatMap(p=>before?[Number(p.value),Number(p.original)]:[Number(p.value)]).filter(Number.isFinite);
 if(!values.length){$('csvPlotDescription').textContent='所选参量没有可绘制数值';return;}
 let low=Math.min(...values),high=Math.max(...values);const diff=high-low||Math.max(1,Math.abs(low)*0.1);
 low-=diff*0.09;high+=diff*0.09;
 const left=62,top=25,width=808,height=252;
 const x=n=>left+(n-1)/Math.max(1,total-1)*width;
 const y=n=>top+(high-n)/(high-low)*height;
 for(const event of events){
  const start=Math.max(1,Number(event.start_frame)),end=Math.min(total,Number(event.end_frame));
  if(start>end)continue;
  plot.append(svgElement('rect',{x:x(start),y:top,width:Math.max(2,x(end)-x(start)+3),height,fill:'#fce6eb'}));
 }
 for(let index=0;index<=4;index++){
  const yy=top+height*index/4,val=high-(high-low)*index/4;
  plot.append(svgElement('line',{x1:left,y1:yy,x2:left+width,y2:yy,stroke:'#e2e8f0'}));
  const text=svgElement('text',{x:left-10,y:yy+4,'text-anchor':'end','font-size':12,fill:'#475569'});
  text.textContent=Number(val.toPrecision(4)).toString();plot.append(text);
  const frame=Math.round(1+Math.max(0,total-1)*index/4);
  const xt=svgElement('text',{x:x(frame),y:top+height+22,'text-anchor':'middle','font-size':12,fill:'#475569'});
  xt.textContent=String(frame);plot.append(xt);
 }
 function line(name,color,dash){const segments=[];for(const p of points){const val=Number(p[name]);if(!Number.isFinite(val))continue;
  segments.push(`${segments.length?'L':'M'}${x(p.frame).toFixed(2)},${y(val).toFixed(2)}`);}
  const shape=svgElement('path',{d:segments.join(' '),stroke:color,'stroke-width':2,fill:'none'});
  if(dash)shape.setAttribute('stroke-dasharray','6 4');plot.append(shape);
 }
 if(before)line('original','#7c8d9a',true);
 line('value','#187ba2',false);
 const axis=svgElement('text',{x:left+width/2,y:320,'text-anchor':'middle',fill:'#334155','font-size':13});axis.textContent='帧号（从 1 开始）';plot.append(axis);
 $('csvPlotDescription').textContent=desc+` · 当前绘制 ${points.length}/${total} 帧；完整数据导出不抽样。`;
}
async function showDataset(){
 invalidate();const dataset=selected();const seq=++state.sequence;
 if(!dataset){$('csvColumn').replaceChildren();$('csvExport').hidden=true;$('csvExportRaw').hidden=true;
  $('csvEventHistory').replaceChildren();$('csvAncestorSelect').replaceChildren();
  $('csvCompareButton').disabled=true;$('csvRollbackButton').disabled=true;
  $('csvCompareSummary').textContent='选择祖先版本后，可比较曲线或回退到该版本。';
  draw([],0,'请先导入 CSV 文件。');return;}
 const choose=$('csvColumn'),previous=choose.value;choose.replaceChildren();
 for(const column of dataset.numeric_columns)option(choose,column,column);
 choose.value=dataset.numeric_columns.includes(previous)?previous:dataset.numeric_columns[0];
 $('csvStartFrame').max=String(dataset.frame_count);$('csvEndFrame').max=String(dataset.frame_count);
 $('csvStartFrame').value='1';$('csvEndFrame').value=String(dataset.frame_count);
 $('csvFrameHelp').textContent=`共 ${dataset.frame_count} 帧，可选 1～${dataset.frame_count}（含起止帧）。保存后在版本下拉框选择新版本以继续注入。`;
 const ref=encodeURIComponent(dataset.id);
 $('csvExport').href=API+'/export?id='+ref;
 $('csvExportRaw').href=API+'/export?id='+ref+'&raw=1';
 $('csvExport').hidden=false;$('csvExportRaw').hidden=false;
 await updateEventView(dataset,seq);
 await populateAncestors(dataset,seq);
 await showChart(seq);
}
async function showChart(sequence=++state.sequence){
 const dataset=selected();if(!dataset)return;
 try{
  const r=await get('/dataset-preview?id='+encodeURIComponent(dataset.id)+'&column='+encodeURIComponent($('csvColumn').value));
  if(sequence!==state.sequence)return;
  draw(r.points,r.frame_count,`版本 v${r.version} · ${r.column}`,(r.events||[]));
 }catch(error){if(sequence===state.sequence)tell('csvStatus','读取数据集曲线失败：'+error.message,true);}
}
async function refreshDatasets(preferred){
 const info=await get('/list');state.items=info.items||[];
 const select=$('csvDatasetSelect'),previous=select.value;select.replaceChildren();
 if(!state.items.length)option(select,'','尚未导入本地 CSV');
 for(const entry of state.items){const fmt=entry.csv_format||{};const formatLabel=fmt.encoding?` · ${fmt.encoding}/${fmt.delimiter==='\t'?'TAB':fmt.delimiter}`:'';
  option(select,entry.id,`${entry.filename}${formatLabel} · v${entry.version} · ${entry.frame_count} 帧 · ${entry.active_event_count||0}/${entry.event_count||0} 项生效事件 · ${entry.id.slice(0,8)}`);}
 select.value=state.items.find(x=>x.id===preferred)?.id||state.items.find(x=>x.id===previous)?.id||state.items[0]?.id||'';
 const current=selected();$('csvVersions').textContent=current?`当前版本 v${current.version}；父版本：${current.parent_id?current.parent_id.slice(0,8):'原始导入'}；${current.active_event_count||0}/${current.event_count||0} 项故障事件生效；${current.operation_count||0} 项版本操作；来源格式 ${current.csv_format?.encoding||'未知'}/${current.csv_format?.delimiter==='\t'?'TAB':current.csv_format?.delimiter||'未知'}。`:'当前没有工作区数据。';
 await showDataset();
}
const csvFaultGuidance={
 bias:{label:'偏置幅值（按当前参量单位）',help:'在选定帧区间内，对每一帧叠加相同幅值。正值使曲线上移，负值使曲线下移；例如温度参量单位为 ℃ 时，填写 2 表示增加 2 ℃。'},
 drift:{label:'漂移终值增量（按当前参量单位）',help:'在选定区间内逐帧线性累积，从起始帧的较小增量逐渐增加，到结束帧达到所填幅值。填写 4 表示区间末端相对原始值累计增加 4 个参量单位。'},
 stuck:{label:'固定目标值（按当前参量单位）',help:'把选定区间内的参量固定为同一个目标值，而不是在原值上叠加。允许填写 0；例如电压单位为 V 时，填写 0 表示该区间固定为 0 V。'},
 spike:{label:'尖峰幅值（按当前参量单位）',help:'在选定区间内叠加周期性尖峰，正值形成向上尖峰，负值形成向下尖峰。当前实现每隔 8 帧产生一次尖峰；幅值表示每次尖峰相对原始值的增量。'}
};
function updateCsvFaultGuidance(){
 const kind=$('csvFaultType')?.value||'bias',item=csvFaultGuidance[kind]||csvFaultGuidance.bias;
 const label=$('csvMagnitudeLabel'),help=$('csvFaultHelp');
 if(label)label.textContent=item.label;
 if(help)help.textContent=item.help;
}
function faultConfig(){
 const source=selected();if(!source)throw Error('请先选择已导入的数据版本');
 const start=$('csvStartFrame').value.trim(),end=$('csvEndFrame').value.trim();
 if(!start||!end)throw Error('请填写起止帧');
 const bounds=validateFrames(Number(start),Number(end),source.frame_count);
 const raw=$('csvMagnitude').value.trim();if(!raw)throw Error('请输入故障幅值');
 const mag=Number(raw);if(!Number.isFinite(mag))throw Error('故障幅值必须为有限数值');
 return {dataset_id:source.id,column:$('csvColumn').value,fault_type:$('csvFaultType').value,
         ...bounds,magnitude:mag};
}
function selectedFormat(){return {csv_encoding:$('csvEncoding').value,csv_delimiter:$('csvDelimiter').value};}
function clearInspection(message){state.inspected=null;state.file=null;$('csvFormatConfirmed').checked=false;
 $('csvFormatConfirmed').disabled=true;$('csvImport').disabled=true;importWizard?.reset();if(message)tell('csvImportStatus',message);}
function showCsvQuality(report){
 const summary=$('csvQualitySummary'),host=$('csvQualityIssues');host.replaceChildren();
 if(!report){summary.textContent='当前文件没有质量分析数据。';return;}
 const sum=obj=>Object.values(obj||{}).reduce((n,v)=>n+Number(v||0),0);
 summary.textContent=`共 ${report.row_count||0} 帧 · 缺失 ${sum(report.missing_by_column)} · 非数值 ${sum(report.non_numeric_by_column)} · NaN/Inf ${sum(report.nonfinite_by_column)} · 重复时间 ${report.duplicate_times||0} · 时间倒退 ${report.time_reversals||0} · 长间隔 ${report.gaps_gt_3x_median||0} · 问题 ${report.issue_count||0} 项${report.issues_truncated?'（显示前20项）':''}`;
 for(const issue of report.issues||[]){const line=document.createElement('p');
  line.textContent=`第 ${issue.line} 行 / 第 ${issue.frame} 帧 · ${issue.column} · ${issue.code} · ${issue.value||'空'} · ${issue.suggestion||''}`;
  host.append(line);
 }
}
async function inspect(){
 const file=$('csvFile').files?.[0];clearInspection('正在检查文件格式与字段…');
 $('csvPreviewTable').replaceChildren();showCsvQuality(null);
 if(!file)throw Error('请先选择 CSV 或 CSV.GZ 文件');
 const requested=selectedFormat();
 const result=await binary('/inspect',file,requested);
 if($('csvFile').files?.[0]!==file||JSON.stringify(requested)!==JSON.stringify(selectedFormat()))
  throw Error('检查期间文件或格式设置发生变化，请重新检查');
 if(result.format_review_required!==true)throw Error('后端没有返回可确认的 CSV 格式信息，请检查服务版本');
 $('csvEncoding').value=result.csv_encoding;$('csvDelimiter').value=result.csv_delimiter;
 $('csvFormatConfirmed').checked=false;$('csvFormatConfirmed').disabled=false;state.file=file;state.inspected=result;
 importWizard?.fileReady(false,importSummary(result));
 if($('csvImportColumns'))$('csvImportColumns').textContent=`可注入数值参量：${result.numeric_columns.join('、')}。完整文件共有 ${result.columns.length} 列，全部保留。`;
 showCsvQuality(result.quality);
 const delim=result.csv_delimiter==='\t'?'TAB':result.csv_delimiter;
 const summary=`${file.name} · ${result.frame_count} 帧 · ${result.columns.length} 列 · ${result.csv_encoding} / ${delim}；可注入数值参量 ${result.numeric_columns.length} 个。`;
 tell('csvImportStatus',summary+(result.has_truth_labels?' 已发现原始 0/1 标签并保留。':' 原始标签未知或无效，不会推定为正常。')+' 请核对格式并勾选确认。');
 const host=$('csvPreviewTable'),table=document.createElement('table'),thead=document.createElement('thead'),tr=document.createElement('tr');
 for(const label of ['帧号',...result.columns]){const th=document.createElement('th');th.textContent=label;tr.append(th);}
 thead.append(tr);table.append(thead);const tbody=document.createElement('tbody');
 for(const row of result.sample){const tr=document.createElement('tr');for(const key of ['frame',...result.columns]){
  const td=document.createElement('td');td.textContent=String(row[key]??'');tr.append(td);}tbody.append(tr);}
 table.append(tbody);host.append(table);
}
async function importSelected(){
 if(importBusy)return;
 if(!state.inspected||!state.file||state.file!==$('csvFile').files?.[0])throw Error('文件已变化，请重新检查');
 const chosen=selectedFormat();
 if(!$('csvFormatConfirmed').checked||chosen.csv_encoding!==state.inspected.csv_encoding||
    chosen.csv_delimiter!==state.inspected.csv_delimiter)throw Error('请先核对编码与分隔符；格式有变化时必须重新检查');
 const button=$('csvImport');button.disabled=true;importBusy=true;importWizard?.setBusy(true);
 const summary=importSummary(state.inspected);
 try{tell('csvImportStatus','正在将完整 CSV 导入独立模拟库，不会修改历史数据库…');
  const result=await binary('/import',state.file,{...chosen,format_confirmed:true});
  tell('csvImportStatus',`导入成功：${result.frame_count} 帧，${result.numeric_columns.length} 个数值参量；格式 ${result.csv_encoding}/${result.csv_delimiter==='\t'?'TAB':result.csv_delimiter}；版本 v0 已保存。`);
  state.inspected=null;state.file=null;$('csvFormatConfirmed').checked=false;$('csvFormatConfirmed').disabled=true;
  importWizard?.complete({...summary,row_count:result.frame_count,column_count:result.numeric_columns.length});
  try{await refreshDatasets(result.id);}catch(error){
   const followup=`版本列表暂未刷新：${error.message}。请刷新页面后选择已保存版本。`;
   tell('csvImportStatus',`CSV 已保存为原始版本。${followup}`,true);
   importWizard?.complete({...summary,followup_error:followup});
  }
 }finally{importBusy=false;importWizard?.setBusy(false);button.disabled=!Boolean(state.inspected&&$('csvFormatConfirmed').checked);}
}
function on(id,type,fn){$(id).addEventListener(type,fn);}
for(const tab of document.querySelectorAll('[data-sim-mode]')){
 tab.addEventListener('click',()=>{const csv=tab.dataset.simMode==='csv';
  $('simCsvWorkspace').hidden=!csv;$('simHistoricalWorkspace').hidden=csv;
  for(const other of document.querySelectorAll('[data-sim-mode]')){
    other.classList.toggle('active',other===tab);other.setAttribute('aria-selected',String(other===tab));
  }
 });
}
on('csvFile','change',()=>{clearInspection('文件已更换，请先执行字段检查。');
 $('csvEncoding').value='auto';$('csvDelimiter').value='auto';$('csvPreviewTable').replaceChildren();});
for(const id of ['csvEncoding','csvDelimiter'])on(id,'change',()=>{
 clearInspection('编码或分隔符已修改，请重新检查文件与字段。');$('csvPreviewTable').replaceChildren();});
on('csvFormatConfirmed','change',()=>{
 const current=state.inspected,chosen=selectedFormat();
 $('csvImport').disabled=!(Boolean(current&&state.file===$('csvFile').files?.[0]&&
  chosen.csv_encoding===current.csv_encoding&&chosen.csv_delimiter===current.csv_delimiter&&
  $('csvFormatConfirmed').checked));
 importWizard?.fileReady(!$('csvImport').disabled,importSummary(current||{}));
});
on('csvInspect','click',async()=>{if(importBusy)return;const button=$('csvInspect');button.disabled=true;importWizard?.setBusy(true);
 try{await inspect();}catch(e){clearInspection(e.message);tell('csvImportStatus',e.message,true);}finally{button.disabled=false;importWizard?.setBusy(false);}});
if($('csvReview'))on('csvReview','click',()=>{
 if(!state.inspected||$('csvImport').disabled){tell('csvImportStatus','请先检查文件并确认格式。',true);return;}
 importWizard?.review(importSummary(state.inspected));
});
on('csvImport','click',()=>importSelected().catch(e=>tell('csvImportStatus',e.message,true)));
on('csvDatasetSelect','change',()=>showDataset().catch(e=>tell('csvStatus',e.message,true)));
on('csvCompareButton','click',async()=>{const button=$('csvCompareButton');button.disabled=true;
 try{await compareSelectedAncestor();}catch(e){tell('csvCompareSummary',e.message,true);}finally{button.disabled=!Boolean($('csvAncestorSelect').value);}});
on('csvRollbackButton','click',async()=>{const button=$('csvRollbackButton');button.disabled=true;
 try{await rollbackSelectedAncestor();}catch(e){tell('csvStatus',e.message,true);}finally{button.disabled=!Boolean($('csvAncestorSelect').value);}});
on('csvColumn','change',()=>{invalidate();showChart().catch(e=>tell('csvStatus',e.message,true));});
on('csvFaultType','change',()=>{updateCsvFaultGuidance();invalidate();});
for(const id of ['csvFaultType','csvStartFrame','csvEndFrame','csvMagnitude'])on(id,'input',invalidate);
updateCsvFaultGuidance();
on('csvFaultPreview','click',async()=>{const btn=$('csvFaultPreview');btn.disabled=true;invalidate();
 try{const config=faultConfig();tell('csvStatus','正在计算本次故障的完整帧区间，预览不写库…');
  const r=await post('/fault-preview',config);state.preview=r;state.config=config;$('csvFaultSave').disabled=false;
  draw(r.points,r.total_frames,`未保存预览 · 第 ${r.event.start_frame}～${r.event.end_frame} 帧`,[r.event],true);
  tell('csvStatus',`预览成功：影响 ${r.fault_frames} 帧，其中 ${r.modified_frames} 帧数值实际变化。确认后另存版本。`);
 }catch(e){tell('csvStatus',e.message,true);}finally{btn.disabled=false;}});
on('csvFaultSave','click',async()=>{const btn=$('csvFaultSave');btn.disabled=true;
 try{if(!state.preview||!state.config)throw Error('请先预览故障');const current=faultConfig();
  if(JSON.stringify(current)!==JSON.stringify(state.config))throw Error('参数已变化，请重新预览');
  const saved=await post('/fault-save',{...current,preview_sha256:state.preview.preview_sha256});
  tell('csvStatus',`故障保存成功：新版本 v${saved.version}，保留原始及父版本。可再次选择故障类型与帧区间继续注入。`);
  invalidate();await refreshDatasets(saved.id);
 }catch(e){tell('csvStatus',e.message,true);if(state.preview)btn.disabled=false;}});
refreshDatasets().catch(e=>tell('csvStatus','CSV 模拟工作区暂不可用：'+e.message,true));
})();
