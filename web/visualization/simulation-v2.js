'use strict';
const $ = id => document.getElementById(id);
const state={catalog:[],config:null,preview:null,selectedId:null,records:[],faultTypes:[],sourceBounds:null,sourceBoundsSeq:0};
const faultLabels={bias:'偏置',drift:'渐变漂移',stuck:'固定值',spike:'周期尖峰'};
async function query(url,opts){const r=await fetch(url,{credentials:'same-origin',...opts});const x=await r.json();if(!r.ok)throw Error(x.error||'请求失败');return x;}
const post=(url,payload)=>query(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
function message(value,error=false){$('simStatus').textContent=value;$('simStatus').classList.toggle('sim-error',error);}
function choices(id,items,label){const el=$(id),before=el.value;el.replaceChildren();for(const [value,txt] of items){const opt=document.createElement('option');opt.value=String(value);opt.textContent=txt;el.appendChild(opt);}if(items.some(([v])=>String(v)===before))el.value=before;else if(!items.length){const opt=document.createElement('option');opt.value='';opt.textContent=label||'暂无可选数据';el.appendChild(opt);}return el.value;}
const unique=(items,key)=>[...new Set(items.map(x=>x[key]))].sort((a,b)=>String(a).localeCompare(String(b),'zh-CN'));
function invalidate(){state.preview=null;state.config=null;$('simSave').disabled=true;}
function cascades(level=0){
 const all=state.catalog;
 if(level<=0)choices('simModel',unique(all,'model').map(v=>[v,v]),'无可用型号');
 const m=all.filter(x=>x.model===$('simModel').value);
 if(level<=1)choices('simSubsystem',unique(m,'subsystem').map(v=>[v,v]),'无可用分系统');
 const sub=m.filter(x=>x.subsystem===$('simSubsystem').value);
 if(level<=2)choices('simDevice',unique(sub,'device').map(v=>[v,v]),'无可用部件');
 const dev=sub.filter(x=>x.device===$('simDevice').value);
 if(level<=3)choices('simColumn',unique(dev,'variable').map(v=>[v,v]),'无可用参量');
 const vars=dev.filter(x=>x.variable===$('simColumn').value);
 choices('simSource',vars.map(x=>[`${x.source_id}:${x.channel_id}`,`${x.source_name} · ${x.source_id} · ${x.source_run_role==='FAULT'?'原故障运行（仅故障前正常段）':x.source_run_role==='NORMAL'?'原正常运行':x.source_role==='test'?'测试来源（仅正常段）':'训练来源（仅正常段）'}`]),'无可用正常运行');
 invalidate();
}
async function updateSourceBounds(){
 const serial=++state.sourceBoundsSeq;state.sourceBounds=null;
 const start=$('simStartFrame'),end=$('simEndFrame');
 for(const input of [start,end]){input.removeAttribute('min');input.removeAttribute('max');}
 const hint=$('simTimeHint');if(!hint)return;
 const pair=$('simSource').value.split(':'),count=Number($('simPoints').value);
 if(pair.length!==2||!pair.every(x=>/^\d+$/.test(x))||!Number.isInteger(count)||count<8||count>4000){
   hint.textContent='先选择有效来源及 8～4000 帧。';return;
 }
 hint.textContent='正在读取本次故障模拟的有效帧数…';
 try{
   const q=new URLSearchParams({source_id:pair[0],channel_id:pair[1],max_points:String(count)});
   const r=await query('/api/v2/simulation/source-time-bounds?'+q);
   if(serial!==state.sourceBoundsSeq)return;
   state.sourceBounds=r;
   for(const input of [start,end]){
     input.min='1';input.max=String(r.sampling_rows);input.step='1';
     if(input.value===''||Number(input.value)<1)input.value='1';
     if(Number(input.value)>r.sampling_rows)input.value=String(r.sampling_rows);
   }
   if(Number(start.value)>Number(end.value))end.value=start.value;
   hint.textContent=`本次读取 ${r.sampling_rows} 帧连续已标注正常数据${r.baseline_truncated?'（在原有故障/无效样本前截断）':''}；注入可选择第 1～${r.sampling_rows} 帧（首尾包含）。`;
 }catch(e){if(serial===state.sourceBoundsSeq)hint.textContent='无法获取来源时间范围：'+e.message;}
}
function selectedFault(){return state.faultTypes.find(x=>x.name===$('simType').value);}
function renderFaultControls(){
 const item=selectedFault(),host=$('simFaultPluginParams');host.replaceChildren();host.hidden=true;
 const custom=item&&!item.v2_legacy;
 const magnitude=$('simMagnitude');
 $('simMagnitudeField').hidden=Boolean(custom&&!item.uses_severity);
 if(custom&&magnitude.value==='18')magnitude.value='0.2';
 if(!custom&&magnitude.value==='0.2')magnitude.value='18';
 if(custom&&!item.uses_severity)magnitude.value='1';
 $('simMagnitudeLabel').textContent=custom?'故障强度（相对比例）':'注入幅值 / 固定值';
 $('simFaultPluginHelp').textContent=item?(item.description||''):'请先选择可用故障类型';
 if(!custom)return;
 for(const field of item.config_schema||[]){
   if(!field||!field.name||field.name==='severity')continue;
   const label=document.createElement('label');label.textContent=field.label||field.name;
   let control;
   if(field.type==='select'){
     control=document.createElement('select');
     for(const option of field.options||[]){const opt=document.createElement('option');opt.value=String(option.value??option);opt.textContent=option.label||String(option.value??option);control.appendChild(opt);}
   } else if(field.type==='bool'||field.type==='boolean'){
     control=document.createElement('input');control.type='checkbox';control.checked=Boolean(field.default);
   } else {
     control=document.createElement('input');control.type=(['float','number','int','integer'].includes(field.type)?'number':'text');
     if(control.type==='number'){control.step=['int','integer'].includes(field.type)?'1':'any';if(field.min!==undefined)control.min=field.min;if(field.max!==undefined)control.max=field.max;}
   }
   control.dataset.param=field.name;control.dataset.kind=field.type||'str';
   if(field.default!==undefined&&control.type!=='checkbox')control.value=String(field.default);
   control.addEventListener('input',invalidate);control.addEventListener('change',invalidate);
   label.appendChild(control);
   if(field.description){const help=document.createElement('small');help.textContent=field.description;label.appendChild(help);}
   host.appendChild(label);
 }
 host.hidden=!host.childNodes.length;
}
async function loadFaultTypes(){
 const catalog=await query('/api/v2/simulation/fault-types');state.faultTypes=catalog.items||[];
 choices('simType',state.faultTypes.map(t=>[t.name,t.label+(t.v2_legacy?'':' · 插件')]),'暂无可用故障类型');
 renderFaultControls();
}
function pluginParameters(){
 const obj={};for(const node of $('simFaultPluginParams').querySelectorAll('[data-param]')){
   if(node.type==='checkbox')obj[node.dataset.param]=node.checked;
   else if(['float','number','int','integer'].includes(node.dataset.kind)){
     if(node.value.trim()==='')throw Error('请填写故障类型参数：'+node.dataset.param);
     obj[node.dataset.param]=Number(node.value);
   }else obj[node.dataset.param]=node.value;
 }return obj;
}
function buildConfig(){const pair=$('simSource').value.split(':');if(pair.length!==2||!pair.every(x=>/^\d+$/.test(x)))throw Error('请选择一个正常来源参量');
 const text=id=>$(id).value.trim();
 for(const id of ['simStartFrame','simEndFrame','simMagnitude','simPoints'])if(text(id)==='')throw Error('请完整填写注入参数');
 const config={source_id:Number(pair[0]),channel_id:Number(pair[1]),fault_type:$('simType').value,
  start_frame:Number(text('simStartFrame')),end_frame:Number(text('simEndFrame')),
  magnitude:Number(text('simMagnitude')),max_points:Number(text('simPoints'))};
 if(!Number.isInteger(config.max_points))throw Error('读取帧数必须是整数');
 if(!state.sourceBounds)throw Error('请等待来源的可用时间范围加载完成');
 if(!Number.isInteger(config.start_frame)||!Number.isInteger(config.end_frame)||
   config.start_frame<1||config.end_frame<config.start_frame||config.end_frame>state.sourceBounds.sampling_rows)
   throw Error(`故障帧区间必须在 1～${state.sourceBounds.sampling_rows} 内，且开始帧不晚于结束帧`);
 if(selectedFault()&&!selectedFault().v2_legacy)config.plugin_params=pluginParameters();
 return config;
}
function svgEl(tag,attrs){const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);return el;}
function formatTick(value){
 if(!Number.isFinite(value))return '';
 const abs=Math.abs(value);
 if(abs>=1000||abs===0&&value!==0)return value.toExponential(2);
 if(abs>=100)return value.toFixed(0);
 if(abs>=10)return value.toFixed(1).replace(/\.0$/,'');
 if(abs>=1)return value.toFixed(2).replace(/0+$/,'').replace(/\.$/,'');
 return value.toFixed(3).replace(/0+$/,'').replace(/\.$/,'');
}
function inferUnit(info){
 const parts=String(info||'').split('·').map(x=>x.trim()).filter(Boolean);
 return parts.length>=3?parts[parts.length-1]:'';
}
function draw(points,title,info,opts={}){const svg=$('simPreview');svg.replaceChildren();$('simChartTitle').textContent=title;$('simChartInfo').textContent=info;
 const left=58,right=816,top=18,bottom=214;
 const xLabel=(opts&&opts.xLabel)||'帧号（从 1 开始）';
 const yLabel=(opts&&opts.yLabel)||inferUnit(info)||'数值';
 if(points.length<2){
   const tx=svgEl('text',{x:(left+right)/2,y:(top+bottom)/2,'text-anchor':'middle',fill:'#6b7280','font-size':'13'});
   tx.textContent='暂无可展示的曲线数据';svg.appendChild(tx);return;
 }
 const indexOf=p=>p.frame??p.t;
 const ts=points.map(indexOf),vs=points.flatMap(p=>p.original===undefined?[p.value]:[p.value,p.original]).filter(v=>Number.isFinite(v));
 const t0=Math.min(...ts),t1=Math.max(...ts),v0=Math.min(...vs),v1=Math.max(...vs);
 const pad=(v1-v0||1)*.07;const min=v0-pad,max=v1+pad;
 const x=t=>left+(t-t0)/(t1-t0||1)*(right-left),y=v=>bottom-(v-min)/(max-min)*(bottom-top);
 const axisColor='#748292',textColor='#475569';
 const first=points.find(p=>p.label===1),last=points.slice().reverse().find(p=>p.label===1);
 if(first&&last)svg.appendChild(svgEl('rect',{x:x(indexOf(first)),y:top,
  width:Math.max(2,x(indexOf(last))-x(indexOf(first))+Math.max(1,(right-left)/Math.max(1,points.length-1)/2)),
  height:bottom-top,class:'fault-zone'}));
 const yTicks=5,xTicks=5;
 for(let i=0;i<yTicks;i++){
   const ratio=i/(yTicks-1),value=max-(max-min)*ratio,yy=top+(bottom-top)*ratio;
   svg.appendChild(svgEl('line',{x1:left,x2:right,y1:yy,y2:yy,class:'grid-line'}));
   const tick=svgEl('text',{x:left-8,y:yy+4,'text-anchor':'end',fill:textColor,'font-size':'11'});
   tick.textContent=formatTick(value);svg.appendChild(tick);
 }
 for(let i=0;i<xTicks;i++){
   const ratio=i/(xTicks-1),value=t0+(t1-t0)*ratio,xx=left+(right-left)*ratio;
   svg.appendChild(svgEl('line',{x1:xx,x2:xx,y1:bottom,y2:bottom+5,stroke:axisColor,'stroke-width':'1'}));
   const tick=svgEl('text',{x:xx,y:bottom+19,'text-anchor':'middle',fill:textColor,'font-size':'11'});
   tick.textContent=formatTick(value);svg.appendChild(tick);
 }
 svg.appendChild(svgEl('line',{x1:left,x2:right,y1:bottom,y2:bottom,stroke:axisColor,'stroke-width':'1.2'}));
 svg.appendChild(svgEl('line',{x1:left,x2:left,y1:top,y2:bottom,stroke:axisColor,'stroke-width':'1.2'}));
 const xt=svgEl('text',{x:(left+right)/2,y:250,'text-anchor':'middle',fill:textColor,'font-size':'12','font-weight':'600'});xt.textContent=xLabel;svg.appendChild(xt);
 const yt=svgEl('text',{x:16,y:(top+bottom)/2,transform:`rotate(-90 16 ${(top+bottom)/2})`,'text-anchor':'middle',fill:textColor,'font-size':'12','font-weight':'600'});yt.textContent=yLabel;svg.appendChild(yt);
 const d=k=>points.map((p,i)=>({p,i})).filter(({p})=>Number.isFinite(p[k])).map(({p,i},j,arr)=>(j&&arr[j-1].i===i-1?'L':'M')+x(indexOf(p)).toFixed(2)+' '+y(p[k]).toFixed(2)).join(' ');
 if(points[0].original!==undefined)svg.appendChild(svgEl('path',{d:d('original'),class:'original'}));
 svg.appendChild(svgEl('path',{d:d('value'),class:'injected'}));
}
async function loadSaved(id){const r=await query('/api/v2/simulation/preview?id='+encodeURIComponent(id));state.selectedId=id;state.preview=null;state.config=null;$('simSave').disabled=true;
 for(const tr of $('simEntries').querySelectorAll('tr[data-id]'))tr.classList.toggle('selected',Number(tr.dataset.id)===id);
 draw(r.points||[],r.record.model+' · '+r.record.source_column,(state.faultTypes.find(x=>x.name===r.record.fault_type)?.label||faultLabels[r.record.fault_type]||r.record.fault_type)+` · 第 ${r.record.start_frame}～${r.record.end_frame} 帧 · 已保存记录 #${id}`,{xLabel:'帧号',yLabel:'数值'});
}
const faultText=row=>state.faultTypes.find(x=>x.name===row.fault_type)?.label||faultLabels[row.fault_type]||row.fault_type;
function filterValues(){return {text:$('simFilterText').value.trim().toLowerCase(),type:$('simFilterType').value,column:$('simFilterColumn').value,sort:$('simFilterSort').value};}
function filterChoices(id,items,label){
 const el=$(id),before=el.value;el.replaceChildren();
 const all=document.createElement('option');all.value='';all.textContent=label;el.appendChild(all);
 for(const [value,text] of items){const opt=document.createElement('option');opt.value=String(value);opt.textContent=text;el.appendChild(opt);}
 el.value=items.some(([value])=>String(value)===before)?before:'';
}
function visibleRecords(){
 const f=filterValues();
 const rows=state.records.filter(row=>{
  if(f.type&&row.fault_type!==f.type)return false;
  if(f.column&&row.source_column!==f.column)return false;
  if(f.text){
   const hay=[row.id,row.injected_model,row.source_column,row.source_token].map(x=>String(x??'')).join(' ').toLowerCase();
   if(!hay.includes(f.text))return false;
  }
  return true;
 });
 const sorters={id_desc:(a,b)=>b.id-a.id,id_asc:(a,b)=>a.id-b.id,
  onset_desc:(a,b)=>(Number(b.start_frame)||0)-(Number(a.start_frame)||0)||b.id-a.id};
 return rows.slice().sort(sorters[f.sort]||sorters.id_desc);
}
function fillFilterOptions(){
 filterChoices('simFilterType',unique(state.records,'fault_type').map(v=>[v,faultText({fault_type:v})]),'全部故障类型');
 filterChoices('simFilterColumn',unique(state.records,'source_column').map(v=>[v,v]),'全部参量');
}
function renderRecords(){
 const rows=visibleRecords(),el=$('simEntries');
 el.replaceChildren();
 $('simFilterCount').textContent=`共 ${state.records.length} 条 · 显示 ${rows.length} 条`;
 $('simFilterHint').textContent=(state.records.length&&!rows.length)?'没有符合当前筛选条件的记录，可点“清除筛选”恢复。':'';
 if(!rows.length){
  const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=8;
  td.textContent=state.records.length?'没有符合当前筛选条件的记录。':'尚无已保存的故障模拟记录：在左侧选好来源与参数，预览后点“保存”即可生成。';
  tr.appendChild(td);el.appendChild(tr);return;
 }
 for(const row of rows){
  const tr=document.createElement('tr');tr.dataset.id=String(row.id);tr.tabIndex=0;
  if(row.id===state.selectedId)tr.classList.add('selected');
  const cells=[`#${row.id}`,row.injected_model,row.source_column||'—',faultText(row),`第 ${row.start_frame}～${row.end_frame} 帧`,row.magnitude,row.injected_rows];
  for(const val of cells){const td=document.createElement('td');td.textContent=String(val??'');tr.appendChild(td);}
  tr.children[6].title=`共读取 ${row.sampled_rows} 帧，其中故障影响 ${row.injected_rows} 帧`;
  tr.children[1].title=row.source_token?`来源 token：${row.source_token}`:row.injected_model;
  const td=document.createElement('td');
  const exportLink=document.createElement('a');exportLink.className='sim-export-link';exportLink.textContent='导出 CSV';
  exportLink.href='/api/v2/simulation/export?id='+encodeURIComponent(row.id);
  exportLink.download='aas-v2-injection-'+row.id+'.csv';
  exportLink.title='下载此记录的完整逐帧单参量模拟数据';
  exportLink.addEventListener('click',e=>e.stopPropagation());
  const continueButton=document.createElement('button');continueButton.type='button';
  continueButton.className='secondary';continueButton.textContent='继续注入';
  continueButton.title='以此历史模拟记录作为新的 CSV 工作区版本继续叠加故障';
  continueButton.addEventListener('click',async e=>{e.stopPropagation();continueButton.disabled=true;
   try{
    message('正在保存历史记录的完整数据到独立 CSV 模拟工作区…');
    const response=await fetch('/api/v2/simulation/export?id='+encodeURIComponent(row.id)+'&raw=1',{credentials:'same-origin'});
    if(!response.ok){let detail={};try{detail=await response.json();}catch{}
      throw Error(detail.error||`无法导出历史记录（HTTP ${response.status}）`);}
    const blob=await response.blob();
    if(blob.size>8_000_000)throw Error('导出文件超过 CSV 模拟工作区 8 MB 上限，请下载文件手工拆分后再导入');
    const name=`historical-injection-${row.id}.csv`;
    const result=await query('/api/v2/csv-workspace/import',{method:'POST',
      headers:{'Content-Type':'application/octet-stream','X-AAS-CSV-Filename-B64':btoa(name),
      'X-AAS-CSV-Format-B64':btoa(JSON.stringify({csv_encoding:'utf-8',csv_delimiter:',',format_confirmed:true}))},body:blob});
    document.querySelector('[data-sim-mode="csv"]')?.click();
    if(!window.AASCsvWorkspace?.refreshDatasets)throw Error('工作区已导入，但无法自动刷新；请重新打开页面选择版本');
    await window.AASCsvWorkspace.refreshDatasets(result.id);
    message(`历史记录 #${row.id} 已作为 CSV 工作区原始版本导入，请选择下一次故障和帧区间；原记录保持不变。`);
    const csvStatus=$('csvStatus');csvStatus.textContent=`已从历史模拟记录 #${row.id} 继续；新版本保留原有模拟故障标签，不将其当作真实设备诊断标签。`;
   }catch(err){message('继续注入失败：'+err.message,true);}finally{continueButton.disabled=false;}
  });
  const remove=document.createElement('button');remove.type='button';remove.className='danger';remove.textContent='删除';remove.title='删除这条记录及其注入样本';
  remove.addEventListener('click',async e=>{e.stopPropagation();if(!window.confirm(`确定删除“${row.injected_model}”及其模拟采样吗？此操作不能撤销。`))return;
   remove.disabled=true;try{await post('/api/v2/simulation/remove',{id:row.id});message('已删除选中的模拟记录。');if(state.selectedId===row.id)state.selectedId=null;await refresh(false);}catch(err){message(err.message,true);remove.disabled=false;}});
  td.append(exportLink,continueButton,remove);tr.appendChild(td);
  tr.addEventListener('click',()=>loadSaved(row.id).catch(err=>message(err.message,true)));
  tr.addEventListener('keydown',e=>{if(e.target===tr&&(e.key==='Enter'||e.key===' ')){e.preventDefault();loadSaved(row.id).catch(err=>message(err.message,true));}});
  el.appendChild(tr);
 }
}
async function refresh(keep=true){const info=await query('/api/v2/simulation/list');state.records=info.items||[];
 fillFilterOptions();renderRecords();
 if(!state.records.length){if(!state.preview)draw([],'故障数据预览','尚无模拟记录：可从左侧选择来源进行预览。');return;}
 if(state.preview)return;
 if(keep&&state.records.some(r=>r.id===state.selectedId))return;
 await loadSaved(visibleRecords()[0]?.id??state.records[0].id);
}
async function init(){
 try{await loadFaultTypes();}catch(e){message('故障类型加载失败：'+e.message,true);}
 try{const r=await query('/api/v2/simulation/sources');state.catalog=r.items||[];cascades(0);updateSourceBounds();if(!state.catalog.length)message('当前主数据库没有具有至少 8 个连续、已标注正常采样点的参量；请检查标签和数据来源。',true);}
 catch(e){choices('simModel',[],'主数据库尚不可用');message('无法读取可注入的原始参量：'+e.message+'。已保存记录仍可查看。',true);}
 await refresh();
}
for(const [id,level] of [['simModel',1],['simSubsystem',2],['simDevice',3],['simColumn',4]])$(id).addEventListener('change',()=>{cascades(level);updateSourceBounds();});
$('simType').addEventListener('change',()=>{renderFaultControls();invalidate();});
for(const id of ['simSource','simStartFrame','simEndFrame','simMagnitude','simPoints'])$(id).addEventListener('change',()=>{invalidate();if(id==='simSource'||id==='simPoints')updateSourceBounds();});
$('simDryRun').addEventListener('click',async()=>{const btn=$('simDryRun');btn.disabled=true;invalidate();try{const config=buildConfig();message('正在计算预览，不会修改数据库…');const r=await post('/api/v2/simulation/dry-run',config);
 state.config=config;state.preview=r;state.selectedId=null;$('simSave').disabled=false;draw(r.points,'未保存预览 · '+r.variable,`${r.sampling_rows} 帧 · 第 ${r.start_frame}～${r.end_frame} 帧注入 · ${r.fault_rows} 帧受影响 · ${r.unit||''}`,{xLabel:'帧号',yLabel:r.unit||'数值'});message('预览完成。确认效果后点击“保存”。');}
 catch(e){message(e.message,true);}finally{btn.disabled=false;}});
$('simSave').addEventListener('click',async()=>{const btn=$('simSave');btn.disabled=true;try{if(!state.preview||!state.config)throw Error('请先预览');const config=buildConfig();if(JSON.stringify(config)!==JSON.stringify(state.config))throw Error('参数发生变化，请重新预览');
 message('正在保存模拟记录…');const r=await post('/api/v2/simulation/save',{...config,preview_sha256:state.preview.preview_sha256});state.preview=null;state.config=null;state.selectedId=r.id;
 message(`已保存 ${r.sampling_rows} 帧（其中故障 ${r.fault_rows} 帧），未修改主数据库。`);await refresh();}
 catch(e){message(e.message,true);if(state.preview)btn.disabled=false;}});
$('simRefresh').addEventListener('click',()=>refresh().catch(e=>message(e.message,true)));
for(const id of ['simFilterText','simFilterType','simFilterColumn','simFilterSort']){
 $(id).addEventListener('input',renderRecords);$(id).addEventListener('change',renderRecords);
}
$('simFilterReset').addEventListener('click',()=>{$('simFilterText').value='';$('simFilterType').value='';$('simFilterColumn').value='';$('simFilterSort').value='id_desc';renderRecords();});
init().catch(e=>message(e.message,true));
