/* Unified V2 run/feature/time dataset selection. No legacy tables. */
(()=>{'use strict';
const $=id=>document.getElementById(id);
let ds={runs:[],features:[],run_features:{},default_train_ids:[],default_test_ids:[]},models=[],latest=null,selectedFeatures=[],evalFeatures=[],selectedRuns={train:[],test:[]},algorithmSchema=null;
const roleCatalogs={train:null,eval:null};let historyCatalog=null;
const parameterControls=new Map();
// Safe, bounded runner draft: preferences only. Never persist file bytes, previews,
// dynamic run/feature bindings, task IDs, credentials or preflight authorization.
const RUNNER_DRAFT_KEY='aas.runner.safe-draft.v1',RUNNER_DRAFT_MAX=4096;
const RUNNER_DRAFT_FIELDS={train:['trainDataSource','trainUserCsvDataset','trainStart','trainEnd','trainAlgorithm'],eval:['evalDataSource','evalUserCsvDataset','evalStart','evalEnd','evalSourceModel','evalTargetSplit']};
function readRunnerDraft(){try{const raw=localStorage.getItem(RUNNER_DRAFT_KEY);if(!raw||raw.length>RUNNER_DRAFT_MAX)return null;const item=JSON.parse(raw);return item&&item.version===1&&item.fields&&typeof item.fields==='object'?item.fields:null;}catch(_){return null;}}
function saveRunnerDraft(){const fields={};for(const ids of Object.values(RUNNER_DRAFT_FIELDS))for(const id of ids){const node=$(id);if(node){const value=String(node.value||'');if(value.length<=256)fields[id]=value;}}const raw=JSON.stringify({version:1,saved_at:new Date().toISOString(),fields});if(raw.length<=RUNNER_DRAFT_MAX){try{localStorage.setItem(RUNNER_DRAFT_KEY,raw);}catch(_){}}}
function restoreRunnerDraft(fields){if(!fields)return;for(const ids of Object.values(RUNNER_DRAFT_FIELDS))for(const id of ids){const node=$(id),value=fields[id];if(!node||typeof value!=='string'||value.length>256)continue;if(node.tagName==='SELECT'&&![...node.options].some(option=>option.value===value))continue;if(node.type==='datetime-local'&&value&&!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value))continue;node.value=value;}}
function bindRunnerDraft(){for(const ids of Object.values(RUNNER_DRAFT_FIELDS))for(const id of ids){const node=$(id);if(!node||node.dataset.draftBound==='1')continue;node.dataset.draftBound='1';node.addEventListener('change',saveRunnerDraft);node.addEventListener('input',saveRunnerDraft);}window.addEventListener('pagehide',saveRunnerDraft);}
const previewCache={train:null,eval:null},workspaceSelection={train:'data',eval:'data'};
let previewRequestSequence={train:0,eval:0},trainingSnapshot=null,evaluationSnapshot=null;
const activeTaskIds={train:null,eval:null};
let evalFilterListenersBound=false;
const coverageState={train:null,eval:null},coverageSequence={train:0,eval:0},coverageTimers={train:null,eval:null};
async function api(url,config){const response=await fetch(url,config);let body;try{body=await response.json();}catch{throw Error(`HTTP ${response.status}: invalid JSON`);}if(!response.ok){const error=Error(body.error||'HTTP '+response.status);if(body.failure&&typeof body.failure==='object')error.failure=body.failure;if(body.task_id)error.task_id=body.task_id;throw error;}return body;}
const post=(url,p)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
function message(role,msg,isError=false){
 const x=$(role+'Output');x.replaceChildren();x.classList.toggle('error',isError);
 if(typeof msg==='string'){x.textContent=msg;return;}
 const data=msg||{},metrics=data.metrics||{},keys=Object.entries(metrics).filter(([,v])=>typeof v==='number'&&Number.isFinite(v));
 const grid=document.createElement('div');grid.className='v2-metric-grid';
 for(const [name,value] of keys){const card=document.createElement('div');card.className='v2-metric';
  const title=document.createElement('small');title.textContent=name.replaceAll('_',' ');
  const number=document.createElement('strong');number.textContent=Number(value).toPrecision(4);card.append(title,number);grid.append(card);}
 if(keys.length)x.append(grid);
 for(const [key,value] of Object.entries(data)){
  if(key==='metrics')continue;
  if(typeof value==='string'||typeof value==='number'){
   const row=document.createElement('p');row.textContent=`${key}: ${value}`;x.append(row);}
 }
 const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='完整执行记录 / 指标 JSON';
 const pre=document.createElement('pre');pre.textContent=JSON.stringify(data,null,2);details.append(summary,pre);x.append(details);
}
function setWorkspace(role,view){
 const panel=$(role+'View');panel.classList.remove('is-idle');
 const permitted=['data','progress','result'];
 if(!permitted.includes(view))throw Error('未知工作区视图');
 workspaceSelection[role]=view;
 for(const name of permitted){
  const active=name===view,tab=$(role+name[0].toUpperCase()+name.slice(1)+'Tab');
  const body=$(role+name[0].toUpperCase()+name.slice(1)+'Panel');
  tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
  body.hidden=!active;
 }
}
function workspaceNotice(role,text){$(role+'WorkspaceState').textContent=text;}
const svgNode=(tag,attributes={})=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attributes))n.setAttribute(key,String(value));return n;};
function svgText(svg,text,attributes){const node=svgNode('text',attributes);node.textContent=text;svg.appendChild(node);return node;}
function tick(v){if(!Number.isFinite(v))return '';const a=Math.abs(v);if(a>=100000||a>0&&a<0.001)return v.toExponential(2);return Number(v.toPrecision(5)).toString();}
function renderDataPlot(role){
 const host=$(role+'DataPlot');host.replaceChildren();
 const preview=previewCache[role];if(!preview||!preview.plot_series?.length)return;
 const key=role+'Preview',tools=document.createElement('div');tools.className='v2-data-plot-tools';
 const label=document.createElement('label');label.textContent='参量';const featureSel=document.createElement('select');featureSel.setAttribute('aria-label','选择预览参量');
 for(const key of preview.feature_keys)featureSel.add(new Option(key,key));
 const runLabel=document.createElement('label');runLabel.textContent='来源运行';const runSel=document.createElement('select');runSel.setAttribute('aria-label','选择预览运行');
 runSel.add(new Option('全部运行（独立曲线）','all'));
 for(const item of preview.plot_series)runSel.add(new Option(item.model_alias,String(item.source_id)));
 label.appendChild(featureSel);runLabel.appendChild(runSel);tools.append(label,runLabel);host.append(tools);
 const chart=document.createElement('div');chart.className='v2-data-plot-chart';host.append(chart);
 const render=()=>{
  chart.replaceChildren();const idx=preview.feature_keys.indexOf(featureSel.value);
  const groups=preview.plot_series.filter(item=>runSel.value==='all'||String(item.source_id)===runSel.value);
  const readings=groups.flatMap(item=>item.points.map(point=>Number(point.values[idx])).filter(Number.isFinite));
  const frameMode=preview.plot_series.some(item=>item.points.some(point=>point.coordinate_mode==='frame'));
  const axis=point=>frameMode?Number(point.frame_index):Number(point.time_s);
  const times=groups.flatMap(item=>item.points.map(axis).filter(Number.isFinite));
  if(!readings.length||!times.length){chart.textContent='当前选择没有可绘制的采样';return;}
  let ymin=Math.min(...readings),ymax=Math.max(...readings),xmin=Math.min(...times),xmax=Math.max(...times);
  const yPad=(ymax-ymin||Math.max(Math.abs(ymax)*0.1,1))*0.08;
  ymin-=yPad;ymax+=yPad;
  if(xmax===xmin)xmax+=1;
  const W=900,H=330,L=80,R=26,T=22,B=60,innerW=W-L-R,innerH=H-T-B;
  const X=t=>L+(t-xmin)/(xmax-xmin)*innerW,Y=v=>T+(ymax-v)/(ymax-ymin)*innerH;
  const svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':frameMode?'帧序列曲线':'独立运行时序曲线'});
  for(let i=0;i<5;i++){
   const r=i/4,yy=T+r*innerH,val=ymax-r*(ymax-ymin),xx=L+r*innerW,ts=xmin+r*(xmax-xmin);
   svg.appendChild(svgNode('line',{x1:L,y1:yy,x2:W-R,y2:yy,stroke:'#e2e8f0'}));
   svgText(svg,tick(val),{x:L-8,y:yy+4,'text-anchor':'end','font-size':12,fill:'#475569'});
   svgText(svg,tick(ts),{x:xx,y:H-B+25,'text-anchor':'middle','font-size':12,fill:'#475569'});
  }
  svg.appendChild(svgNode('path',{d:`M${L} ${T}V${H-B}H${W-R}`,stroke:'#64748b',fill:'none'}));
  svgText(svg,frameMode?'帧号':'运行时间 / 秒',{x:L+innerW/2,y:H-8,'text-anchor':'middle','font-size':13,fill:'#334155'});
  const feature=(role==='eval'&&isScoreOnlySelection()?selectedScoreOnlyFeatures():catalogFor(role==='train'?'train':'eval').features)
    .find(x=>x.feature_key===featureSel.value);
  const unit=feature?.unit||'数值';
  svgText(svg,`${unit}`,{x:18,y:T+innerH/2,transform:`rotate(-90 18 ${T+innerH/2})`,'text-anchor':'middle','font-size':13,fill:'#334155'});
  const palette=['#2563eb','#0d9488','#d97706','#7c3aed','#db2777','#64748b','#059669'];
  const legend=document.createElement('div');legend.className='v2-data-plot-legend';
  groups.forEach((run,i)=>{
   const color=palette[i%palette.length];let d='';let lastT=-Infinity;
   for(const point of run.points){const t=axis(point),v=Number(point.values[idx]);if(!Number.isFinite(t)||!Number.isFinite(v))continue;
    d+=(d&&t>lastT?'L':'M')+X(t).toFixed(2)+' '+Y(v).toFixed(2)+' ';lastT=t;
    if(Number(point.label)===1)svg.appendChild(svgNode('circle',{cx:X(t),cy:Y(v),r:2.2,fill:'#dc2626',opacity:0.7}));
   }
   svg.appendChild(svgNode('path',{d:d.trim(),stroke:color,'stroke-width':2,fill:'none'}));
   const item=document.createElement('span'),dot=document.createElement('i');dot.style.background=color;
   item.append(dot,document.createTextNode(`${run.model_alias} · ${run.plotted_count}/${run.original_count} 点`));legend.appendChild(item);
  });
  chart.append(svg,legend);
  const desc=document.createElement('p');desc.className='field-hint';desc.textContent=preview.ground_truth_available===false?
   '绘图经过限量抽样，完整选定帧用于模型推理。本 CSV 没有真实标签，曲线颜色仅区分来源，不代表故障判定。':
   '绘图数据经过限量抽样；训练/评估实际使用完整选定样本。红色标记表示当前选定评估目标的明确故障真值，不代表该参量自身故障或模型报警。不同运行不会被拼接。';chart.append(desc);
 };
 featureSel.addEventListener('change',render);runSel.addEventListener('change',render);render();
}
function setTrainingProgress(value,text){
 const n=Math.min(100,Math.max(0,Number(value)||0));$('trainProgressBar').value=n;
 $('trainProgressLabel').textContent=text;$('trainProgressMeta').textContent=`当前进度 ${n.toFixed(0)}% · 训练过程仅显示后端实际报告的状态`;
}
function finiteOptional(value){return value==null||value===''?NaN:Number(value);}
function renderResultChart(role,result){
 const node=$(role+'ResultChart');node.replaceChildren();
 if(!result||!Array.isArray(result.rows)||!result.rows.length)return;
 const all=result.rows,limit=1600,skip=Math.max(1,Math.ceil(all.length/limit));
 const samples=all.map((row,i)=>({...row,index:i})).filter((row,i)=>i%skip===0||i===all.length-1);
 const numeric=samples.filter(row=>Number.isFinite(Number(row.value)));
 if(!numeric.length)return;
 const values=numeric.map(row=>Number(row.value));let min=Math.min(...values),max=Math.max(...values);
 if(min===max){min-=1;max+=1;}
 const W=940,H=278,L=52,R=20,T=18,B=40;
 const frameMode=all.some(row=>Number.isInteger(row.frame_index));
 const frames=all.map(row=>row.frame_index).filter(Number.isInteger);
 const frameMin=frames.length?Math.min(...frames):1,frameMax=frames.length?Math.max(...frames):1;
 const X=i=>L+(frameMode?(Number(all[i]?.frame_index)-frameMin)/Math.max(1,frameMax-frameMin):i/Math.max(1,all.length-1))*(W-L-R);
 const Y=v=>T+(max-v)/(max-min)*(H-T-B);
 const predictions=result.pred_labels||[];
 const unknown=result.ground_truth_available===false||result.evaluation_mode==='score_only';
 const title=document.createElement('h3');title.textContent=unknown?'仅推理：遥测与算法报警':(role==='eval'?'评估遥测与故障判定':'训练验证数据曲线');node.append(title);
 const hint=document.createElement('p');hint.className='field-hint';hint.textContent=unknown?
  `显示 ${samples.length} / ${all.length} 帧（横轴为${frameMode?'帧号':'采样序号'}；红色圆点为算法报警）。数据没有真实标签，不显示故障真值，也不计算监督指标。`:
  `显示 ${samples.length} / ${all.length} 帧（横轴为${frameMode?'帧号':'采样序号'}；红色背景为故障真值；红色圆点为算法报警）`;
 node.append(hint);
 let chart=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${title.textContent}">`;
 for(let t=0;t<=4;t++){const x=L+t/4*(W-L-R);chart+=`<path d="M${x} ${T}V${H-B}" stroke="#e3e9ef"/><text x="${x}" y="${H-12}" text-anchor="middle" font-size="11">${frameMode?Math.round(frameMin+t/4*(frameMax-frameMin)):Math.round(t/4*(all.length-1))}</text>`;}
 for(let t=0;t<=4;t++){const value=min+t/4*(max-min);chart+=`<text x="${L-7}" y="${Y(value)+4}" font-size="10" text-anchor="end">${value.toPrecision(3)}</text>`;}
 if(!unknown)for(const row of samples){if(Number(row.label)===1){const x=X(row.index);chart+=`<rect x="${x-1}" y="${T}" width="${Math.max(2,(W-L-R)/Math.max(1,samples.length))}" height="${H-T-B}" fill="#e75a5a" fill-opacity=".11"/>`;}}
 // Keep model-result traces separated at run boundaries, even when run-relative
 // timestamps happen to repeat across independent simulations.
 const paths=[];let current=[],previousRun=null;
 const runKey=row=>row.run_source_id==null?String(row.timestamp||'').split('@')[0]:String(row.run_source_id);
 for(const row of numeric){const key=runKey(row);if(current.length&&key!==previousRun){paths.push(current);current=[];}
   current.push(row);previousRun=key;}
 if(current.length)paths.push(current);
 for(const trace of paths){
  chart+=`<polyline points="${trace.map(row=>`${X(row.index).toFixed(2)},${Y(Number(row.value)).toFixed(2)}`).join(' ')}" fill="none" stroke="#2679bb" stroke-width="1.8"/>`;
 }
 for(const row of samples){if(Number(predictions[row.index])===1){chart+=`<circle cx="${X(row.index).toFixed(2)}" cy="${Y(Number(row.value)).toFixed(2)}" r="3.1" fill="#d93c3c"/>`;}}
 chart+='</svg>';const holder=document.createElement('div');holder.innerHTML=chart;node.append(holder);
 if(unknown){
  const raw=result.threshold;
  if(Array.isArray(raw)&&raw.length===all.length){
   const points=samples.filter(row=>Number.isFinite(finiteOptional(raw[row.index])));
   if(points.length){
    const scoreValues=points.map(row=>Number(raw[row.index]));
    const thresholds=Array.isArray(result.decision_thresholds)&&result.decision_thresholds.length===all.length?
     result.decision_thresholds.map(finiteOptional):null;
    const scalar=result.decision_threshold==null?null:Number(result.decision_threshold);
    const boundValues=[...scoreValues,...(thresholds||[]).filter(Number.isFinite)];
    if(Number.isFinite(scalar))boundValues.push(scalar);
    let low=Math.min(...boundValues),high=Math.max(...boundValues);
    if(low===high){low-=1;high+=1;}
    const Yscore=v=>T+(high-v)/(high-low)*(H-T-B);
    let scoreSvg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="模型异常分数和算法报警阈值">`;
    for(let t=0;t<=4;t++){
     const v=low+t/4*(high-low);
     scoreSvg+=`<text x="${L-7}" y="${Yscore(v)+4}" font-size="10" text-anchor="end">${v.toPrecision(3)}</text>`;
    }
    scoreSvg+=`<polyline points="${points.map(row=>`${X(row.index).toFixed(2)},${Yscore(Number(raw[row.index])).toFixed(2)}`).join(' ')}" fill="none" stroke="#1d4ed8" stroke-width="2"/>`;
    if(Number.isFinite(scalar))scoreSvg+=`<path d="M${L} ${Yscore(scalar)}H${W-R}" stroke="#d97706" stroke-dasharray="5 4"/>`;
    else if(thresholds){const dots=points.filter(row=>Number.isFinite(thresholds[row.index]));
     if(dots.length)scoreSvg+=`<polyline points="${dots.map(row=>`${X(row.index).toFixed(2)},${Yscore(thresholds[row.index]).toFixed(2)}`).join(' ')}" fill="none" stroke="#d97706" stroke-dasharray="5 4"/>`;}
    scoreSvg+='</svg>';
    const label=document.createElement('h3');label.textContent='模型异常分数（蓝）与算法阈值（橙）';node.append(label);
    const scoreHost=document.createElement('div');scoreHost.innerHTML=scoreSvg;node.append(scoreHost);
   }
  }
 }
}
function clearTaskDiagnostic(role){const panel=$(role+'FailurePanel');if(!panel)return;panel.replaceChildren();panel.hidden=true;}
function renderTaskDiagnostic(role,diagnostic,taskId){
 const panel=$(role+'FailurePanel');if(!panel)return;
 clearTaskDiagnostic(role);panel.hidden=false;panel.setAttribute('data-task-id',String(taskId||''));
 const stage=document.createElement('p');stage.className='v2-task-diagnostic-stage';stage.textContent=diagnostic.stage_label||'任务执行';
 const title=document.createElement('h3');title.textContent=diagnostic.title||'任务未能完成';
 const messageNode=document.createElement('p');messageNode.textContent=diagnostic.message||'系统未能完成当前任务。';
 const action=document.createElement('p');action.className='v2-task-diagnostic-action';action.textContent=diagnostic.recovery_action||'返回运行设置，检查数据后重新提交。';
 const taskMeta=document.createElement('small');taskMeta.className='v2-task-diagnostic-meta';taskMeta.textContent=taskId?`任务编号：${String(taskId).slice(0,40)}`:'';
 const button=document.createElement('button');button.type='button';button.textContent=diagnostic.recovery_label||'返回运行设置';
 button.addEventListener('click',()=>{
  panel.hidden=true;
  if(diagnostic.code==='archive_failed'){window.location.href='reports.html';return;}
  setWorkspace(role,'data');
 });
 panel.append(stage,title,messageNode,action,taskMeta,button);
}
window.AASTaskDiagnostics={render:(target,diagnostic,taskId)=>renderTaskDiagnostic(target,diagnostic,taskId),clear:clearTaskDiagnostic};
function showResult(role){const panel=$(role==='train'?'trainView':'evalView');panel.classList.remove('is-idle');}
function selectTab(which){ds=roleCatalogs[which]||ds;$('trainView').hidden=which!=='train';$('evalView').hidden=which!=='eval';$('tabTrain').classList.toggle('active',which==='train');$('tabEval').classList.toggle('active',which==='eval');}
function catalogFor(role){return role==='train'?(roleCatalogs.train||ds):(roleCatalogs.eval||ds);}
function coordinateFor(role){
 const target=role==='train'?'train':'eval',ids=selectedRuns[role==='train'?'train':'test'];
 const runs=ids.map(id=>catalogFor(target).runs.find(run=>run.source_id===id)).filter(Boolean);
 const modes=new Set(runs.map(run=>run.coordinate_mode||'time'));
 if(modes.size>1)throw Error('时间序列与帧序列不能混合选择');
 const sequences=new Set(runs.map(run=>run.frame_sequence_id).filter(Boolean));
 if(modes.has('frame')&&sequences.size>1)throw Error('所选帧运行须使用相同帧序列 ID');
 return {mode:[...modes][0]||'time',frame_sequence_id:[...sequences][0]||null};
}
function dataSourcePayload(role){const prefix=role==='train'?'train':'eval';const type=$(prefix+'DataSource')?.value||'history';if(type==='history')return {type:'history'};const dataset_id=$(prefix+'UserCsvDataset')?.value;if(!dataset_id)throw Error('请先选择已保存的用户 CSV 数据集');return {type:'user_csv',dataset_id};}
function isScoreOnlySelection(){
 const current=catalogFor('eval');
 const runs=selectedRuns.test.map(id=>current.runs.find(run=>run.source_id===id)).filter(Boolean);
 const unknown=runs.filter(run=>run.allowed_role==='score_only');
 if(unknown.length&&unknown.length!==runs.length)throw Error('无标签仅推理不能混合已标注测试运行');
 if(unknown.length>1)throw Error('无标签仅推理每次只能使用一个 CSV 测试来源');
 return unknown.length===1;
}
function selectedScoreOnlyFeatures(){
 const sid=selectedRuns.test[0];
 const current=catalogFor('eval');return (current.run_features&&current.run_features[String(sid)])||[];
}
function updateEvalRunMode(){
 const scoreOnly=isScoreOnlySelection();
 $('evalTruthControls').hidden=scoreOnly;
 $('runEval').textContent=scoreOnly?'开始仅推理（无真值）':'开始评估（不重新训练）';
 $('previewEval').textContent=scoreOnly?'预览无标签测试帧':'预览测试数据';
 $('evalResultEmpty').textContent=scoreOnly?
  '仅推理结束后显示异常分数与算法报警；由于没有真实标签，不计算准确率、召回率等监督指标。':
  '评估完成后，将在此显示真实标签、报警点和检测指标。';
}
function renderRuns(role){const current=catalogFor(role==='train'?'train':'eval');const node=$(role==='train'?'trainRuns':'evalRuns');node.replaceChildren();let runs=current.runs.filter(x=>(x.allowed_role===role||(role==='test'&&x.allowed_role==='score_only')) && (role!=='test'||x.split===$('evalTargetSplit').value));if(!runs.length){node.textContent='无符合划分策略的运行';return;}
 for(const run of runs){const label=document.createElement('label');label.className='v2-run-item';const input=document.createElement('input');input.type='checkbox';input.value=String(run.source_id);input.checked=selectedRuns[role].includes(run.source_id);input.addEventListener('change',()=>{if(input.checked){
    if(role==='test'&&(run.allowed_role==='score_only'||selectedRuns.test.some(id=>current.runs.find(x=>x.source_id===id)?.allowed_role==='score_only')))
     selectedRuns.test=[run.source_id];
    else if(!selectedRuns[role].includes(run.source_id))selectedRuns[role].push(run.source_id);
   }else selectedRuns[role]=selectedRuns[role].filter(x=>x!==run.source_id);
   if(role==='test'){renderRuns(role);updateEvalRunMode();renderEvalFeatures();refreshModels();}
   scheduleCoverage(role==='train'?'train':'eval');});
 const span=document.createElement('span');const bold=document.createElement('strong');bold.textContent=run.model_alias+' · '+run.run_role;const detail=document.createElement('small');detail.textContent=run.allowed_role==='score_only'?`${run.split} · 未知真值 ${run.unknown_frames||0} 帧（仅推理）`:`${run.split} · 正常 ${run.normal_frames} / 故障 ${run.fault_frames} 帧`;span.append(bold,detail);label.append(input,span);node.append(label);}}
function fillFilters(){const current=catalogFor('train');for(const [id,key,title] of [['featureSubsystem','subsystem','全部分系统'],['featureDevice','device','全部部件']]){const node=$(id),old=node.value;const vals=[...new Set(current.features.map(f=>f[key]))].sort();node.replaceChildren(new Option(title,''),...vals.map(v=>new Option(v,v)));node.value=vals.includes(old)?old:'';}}
function renderFeatures(){const current=catalogFor('train');const filters={subsystem:$('featureSubsystem').value,device:$('featureDevice').value,search:$('featureSearch').value.trim().toLowerCase()};const node=$('featureColumns');node.replaceChildren();for(const f of current.features){if(filters.subsystem&&f.subsystem!==filters.subsystem)continue;if(filters.device&&f.device!==filters.device)continue;if(filters.search&&!f.feature_key.toLowerCase().includes(filters.search))continue;const label=document.createElement('label');label.className='v2-run-item';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=selectedFeatures.includes(f.feature_key);checkbox.addEventListener('change',()=>{if(checkbox.checked&&!selectedFeatures.includes(f.feature_key)){if(selectedFeatures.length>=(current.max_features||32)){checkbox.checked=false;message('train',`最多选择 ${current.max_features||32} 列`,true);return;}selectedFeatures.push(f.feature_key);}else if(!checkbox.checked)selectedFeatures=selectedFeatures.filter(x=>x!==f.feature_key);describeFeatures();});
 const span=document.createElement('span');const bold=document.createElement('strong');bold.textContent=f.feature_key;const detail=document.createElement('small');detail.textContent=`${f.subsystem} / ${f.device} · ${f.unit||'无单位'}`;span.append(bold,detail);label.append(checkbox,span);node.append(label);}describeFeatures();}
function renderEvalFeatures(){
 const node=$('evalFeatureColumns');node.replaceChildren();
 const keyword=$('evalFeatureSearch').value.trim().toLowerCase();const subsystem=$('evalFeatureSubsystem').value,device=$('evalFeatureDevice').value;
 const current=catalogFor('eval');const available=isScoreOnlySelection()?selectedScoreOnlyFeatures():current.features;
 for(const feature of available){
  if(subsystem&&feature.subsystem!==subsystem)continue;if(device&&feature.device!==device)continue;
  if(keyword&&!feature.feature_key.toLowerCase().includes(keyword))continue;
  const label=document.createElement('label');label.className='v2-run-item';
  const check=document.createElement('input');check.type='checkbox';
  check.checked=evalFeatures.includes(feature.feature_key);
  check.addEventListener('change',()=>{
   if(check.checked&&!evalFeatures.includes(feature.feature_key)){
    if(evalFeatures.length>=(current.max_features||32)){check.checked=false;message('eval','最多选择 '+(current.max_features||32)+' 个参量',true);return;}
    evalFeatures.push(feature.feature_key);
   }else evalFeatures=evalFeatures.filter(x=>x!==feature.feature_key);
   describeEvalFeatures();refreshModels();updateLabelTargetOptions();
  });
  const info=document.createElement('span');const strong=document.createElement('strong');strong.textContent=feature.feature_key;
  const detail=document.createElement('small');detail.textContent=`${feature.subsystem} / ${feature.device} · ${feature.unit||'无单位'}`;
  info.append(strong,detail);label.append(check,info);node.append(label);
 }
 describeEvalFeatures();
}
function describeEvalFeatures(){
 scheduleCoverage('eval');$('evalFeatureSelected').textContent=evalFeatures.length?`已选 ${evalFeatures.length} 个参量；兼容模型必须包含相同参量，评估时按模型原定顺序输入。`:'请选择待评估参量（或稍后从模型恢复特征）';
}
function describeFeatures(){scheduleCoverage('train');const order=[...selectedFeatures].sort();$('featureSelected').textContent=order.length?`模型特征顺序（按参量名规范化，训练后不可调整）：${order.join(' → ')}`:'尚未选择参量列';}
function numeric(id){let text=$(id).value.trim();if(!text)return null;
 if($(id).type==='datetime-local'){text=text.replace('T',' ');if(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(text))text+=':00';return text;}
 if(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(text))return text;
 const number=Number(text);if(!Number.isFinite(number)||number<0)throw Error('请输入 YYYY-MM-DD HH:mm:ss 时间');return number;}

/* Read the intersection of real sample coverage for all selected runs/features.
   The calendar displays a synthetic date mapped from time_s; no host timezone
   conversions or guessed timestamps are used. The data loader still validates
   exact alignment, gaps and source isolation at execution time. */
function displaySecond(value,boundary){
 const milliseconds=Date.UTC(2026,0,1)+(boundary==='min'?Math.ceil(Number(value)*1000-1e-8):Math.floor(Number(value)*1000+1e-8));
 if(!Number.isFinite(milliseconds))throw Error('数据时间超出支持范围');
 const iso=new Date(milliseconds).toISOString();
 return iso.slice(0,19)+(iso.slice(20,23)==='000'?'':'.'+iso.slice(20,23));
}
function timeValueMs(value){return value?Date.parse(value+'Z'):null;}
function compareTime(a,b){return timeValueMs(a)-timeValueMs(b);}
function coverageInputs(role){return role==='train'?[$('trainStart'),$('trainEnd')]:[$('evalStart'),$('evalEnd')];}
function currentCoverageFeatures(role){
 if(role==='train')return [...selectedFeatures];
 const model=models.find(x=>String(x.id)===$('evalModel').value);
 return model?[...model.feature_keys]:[...evalFeatures];
}
function coverageMessage(role,text,error=false,loading=false){
 const node=$(role+'TimeCoverage');node.textContent=text;
 node.classList.toggle('is-error',error);node.classList.toggle('is-loading',loading);
}
function resetCoverageBounds(role){
 for(const input of coverageInputs(role)){
  input.removeAttribute('min');input.removeAttribute('max');input.setCustomValidity('');
 }
}
function constrainTimeValues(role,note=''){
 const data=coverageState[role];if(!data||!data.has_common_window)return;
 const [start,end]=coverageInputs(role);let changed=false;
 if(data.coordinate_mode==='frame'){
  for(const input of [start,end]){if(input.value&&Number(input.value)<Number(input.min)){input.value=input.min;changed=true;}
   if(input.value&&Number(input.value)>Number(input.max)){input.value=input.max;changed=true;}}
  if(start.value&&end.value&&Number(start.value)>Number(end.value)){end.value=start.value;changed=true;}
  coverageMessage(role,`有效帧范围：${data.min} ～ ${data.max}`+(changed?'；已调整超出范围的帧号。':note));return;
 }
 for(const input of [start,end]){
  if(input.value && compareTime(input.value,input.min)<0){input.value=input.min;changed=true;}
  if(input.value && compareTime(input.value,input.max)>0){input.value=input.max;changed=true;}
  input.setCustomValidity('');
 }
 if(start.value&&end.value&&compareTime(start.value,end.value)>0){end.value=start.value;changed=true;}
 coverageMessage(role,`有效时间范围：${data.min.replace('T',' ')} ～ ${data.max.replace('T',' ')}（${data.coverage.length} 组参量来源）`+(changed?'；已将超出范围的日期调整到有效区间。':note));
}
function validateCoverage(role){
 if(!coverageState[role]||coverageState[role].pending)throw Error('请等待所选参量的可用时间范围加载完成');
 const data=coverageState[role];if(!data.has_common_window)throw Error('所选参量和运行没有共同时间区间，请调整筛选条件');
 const [start,end]=coverageInputs(role);
 if(data.coordinate_mode==='frame'){
  for(const input of [start,end])if(input.value&&(!Number.isInteger(Number(input.value))||Number(input.value)<Number(input.min)||Number(input.value)>Number(input.max)))throw Error('帧号不在有效范围内');
  if(start.value&&end.value&&Number(start.value)>Number(end.value))throw Error('开始帧不能大于结束帧');return;
 }
 for(const input of [start,end]){
  if(input.value && (compareTime(input.value,input.min)<0||compareTime(input.value,input.max)>0))throw Error('所选日期不在有效时间范围内');
 }
 if(start.value&&end.value&&compareTime(start.value,end.value)>0)throw Error('开始时间不能晚于结束时间');
}
async function refreshCoverage(role){
 const serial=++coverageSequence[role],keys=currentCoverageFeatures(role),runs=[...selectedRuns[role==='train'?'train':'test']];
 coverageState[role]={pending:true};resetCoverageBounds(role);
 if(!keys.length||!runs.length){
  coverageState[role]=null;coverageMessage(role,'请先选择至少一个参量和一个数据来源。');return;
 }
 const coordinate=coordinateFor(role==='train'?'train':'test');
 for(const input of coverageInputs(role)){input.type=coordinate.mode==='frame'?'number':'datetime-local';input.step=coordinate.mode==='frame'?'1':'1';}
 coverageMessage(role,coordinate.mode==='frame'?'正在计算共同帧范围…':'正在计算所选参量的有效时间范围…',false,true);
 try{
  const scoreOnly=role==='eval'&&isScoreOnlySelection();
  const model=scoreOnly?models.find(item=>String(item.id)===$('evalModel').value):null;
  const result=await post('/api/v2/run-dataset/coverage',{
   role:role==='train'?'train':(scoreOnly?'score_only':'test'),source_ids:runs,feature_keys:keys,data_source:dataSourcePayload(role),coordinate_mode:coordinate.mode,frame_sequence_id:coordinate.frame_sequence_id,
   ...(scoreOnly&&model?{feature_semantics:model.feature_semantics}:{}),
  });
  if(serial!==coverageSequence[role])return;
  if(!result.has_common_window){
   coverageState[role]={...result,has_common_window:false};
   for(const input of coverageInputs(role))input.value='';
   coverageMessage(role,'所选运行与参量的时间范围没有交集。请减少参量或切换卫星型号。',true);return;
  }
  const min=coordinate.mode==='frame'?String(result.start_frame):displaySecond(result.start_s,'min'),max=coordinate.mode==='frame'?String(result.end_frame):displaySecond(result.end_s,'max');
  if(min>max){
   coverageState[role]={...result,has_common_window:false};
   coverageMessage(role,'共同时间范围不足 1 毫秒，不能使用当前日期控件选择。',true);return;
  }
  coverageState[role]={...result,min,max,pending:false};
  for(const input of coverageInputs(role)){input.min=min;input.max=max;input.step=coordinate.mode==='frame'?'1':'0.001';}
  constrainTimeValues(role);
 }catch(error){
  if(serial!==coverageSequence[role])return;
  coverageState[role]={pending:true};coverageMessage(role,'无法计算时间范围：'+error.message,true);
 }
}
function scheduleCoverage(role){
 ++coverageSequence[role];coverageState[role]={pending:true};resetCoverageBounds(role);
 clearTimeout(coverageTimers[role]);
 coverageTimers[role]=setTimeout(()=>refreshCoverage(role),180);
}
for(const role of ['train','eval']){
 const [start,end]=coverageInputs(role);
 for(const input of [start,end]){
  input.addEventListener('change',()=>{
   const c=coverageState[role];if(!c||!c.has_common_window)return;
   constrainTimeValues(role);
  });
 }
}
function chooseSourceModel(role){
 const current=catalogFor(role==='train'?'train':'eval');
 const selected=$(role==='train'?'trainSourceModel':'evalSourceModel').value;
 const candidates=current.runs.filter(r=>(r.allowed_role===role||(role==='test'&&r.allowed_role==='score_only')) && (role!=='test'||r.split===$('evalTargetSplit').value) && (!selected||r.model_alias===selected));
 selectedRuns[role]=(candidates[0]?.allowed_role==='score_only'?candidates.slice(0,1):candidates.filter(r=>r.allowed_role===role).slice(0,2)).map(r=>r.source_id);
 renderRuns(role);
 if(role==='test'){updateEvalRunMode();renderEvalFeatures();}
 scheduleCoverage(role==='train'?'train':'eval');
}
function refreshEvalSourceOptions(){
 const current=catalogFor('eval');const picker=$('evalSourceModel'),old=picker.value;
 const eligible=current.runs.filter(run=>['test','score_only'].includes(run.allowed_role)&&
                              run.split===$('evalTargetSplit').value);
 const aliases=[...new Set(eligible.map(run=>run.model_alias))];
 picker.replaceChildren(new Option('全部测试型号',''),...aliases.map(alias=>{
  const unknown=eligible.some(run=>run.model_alias===alias&&run.allowed_role==='score_only');
  return new Option(alias+(unknown?' · 含未知真值测试':''),alias);
 }));
 if(aliases.includes(old))picker.value=old;
}
function updateLabelTargetOptions(){
 const scope=$('evalLabelScope').value,node=$('evalLabelEntity'),field=$('evalLabelEntityField');
 field.hidden=scope==='run';
 const old=node.value;node.replaceChildren();
 const model=models.find(x=>String(x.id)===$('evalModel').value);
 const keys=model?.feature_keys || evalFeatures;
 const features=catalogFor('eval').features.filter(f=>keys.includes(f.feature_key));
 if(scope==='component'){
  const components=[...new Set(features.map(f=>JSON.stringify([f.subsystem,f.device])))].sort();
  for(const key of components){const [sub,dev]=JSON.parse(key);node.add(new Option(`${sub} / ${dev}`,key));}
  $('evalLabelHelp').textContent='只使用明确部件位置凭据生成真值；其他部件故障后的未知样本不会当作正常。';
 }else if(scope==='parameter'){
  for(const feature of features)node.add(new Option(feature.feature_key,feature.feature_key));
  $('evalLabelHelp').textContent='只使用该参量已经确认的正常/故障事件区间；未标注样本不参与监督指标。';
 }else $('evalLabelHelp').textContent='根据卫星运行级标签评估整体异常；不表示每个部件或参量自身故障。';
 if([...node.options].some(option=>option.value===old))node.value=old;
}
function evaluationTarget(){
 const scope=$('evalLabelScope').value;
 if(scope==='run')return {scope:'run'};
 const value=$('evalLabelEntity').value;
 if(!value)throw Error('请先选择具体的故障对象');
 if(scope==='component'){const [subsystem,device]=JSON.parse(value);return {scope,subsystem,device};}
 if(scope==='parameter')return {scope,feature_key:value};
 throw Error('未知评估目标');
}
function selection(role){const train=role==='train';validateCoverage(train?'train':'eval');const model=models.find(x=>String(x.id)===$('evalModel').value);if(!train&&!model)throw Error('请先选择已发布模型，再预览特征对齐结果');const keys=train?[...selectedFeatures].sort():model.feature_keys;const runs=selectedRuns[role];if(!keys.length)throw Error('至少选择一个参量列');if(!runs.length)throw Error('至少选择一个运行');const coordinate=coordinateFor(role);if(!train&&(model.coordinate_contract?.mode||'time')!==coordinate.mode)throw Error('模型坐标模式与测试数据不一致');const method=coordinate.mode==='frame'?'exact':train?$('trainMethod').value:(model.alignment_method||'exact'),tolerance_seconds=coordinate.mode==='frame'?0:train?numeric('trainTolerance'):(model.tolerance_seconds||0);
 return {source_ids:[...runs],feature_keys:[...keys],start:numeric(train?'trainStart':'evalStart'),end:numeric(train?'trainEnd':'evalEnd'),method,tolerance_seconds,coordinate_mode:coordinate.mode,...(coordinate.mode==='frame'?{frame_sequence_id:coordinate.frame_sequence_id}:{})};}
async function preview(role){
 let config;
 const scoreOnly=role==='test'&&isScoreOnlySelection();
 if(scoreOnly&&!$('evalModel').value)throw Error('无标签推理需要先选择列定义完整的已发布模型');
 if(role==='test'&&!$('evalModel').value){
  if(!evalFeatures.length)throw Error('请先选择待评估参量');
  config={source_ids:[...selectedRuns.test],feature_keys:[...evalFeatures],
   start:numeric('evalStart'),end:numeric('evalEnd'),method:'exact',tolerance_seconds:0,coordinate_mode:coordinateFor('test').mode,...(coordinateFor('test').mode==='frame'?{frame_sequence_id:coordinateFor('test').frame_sequence_id}:{})};
 }else config=selection(role);
 if(role==='test'&&$('evalModel').value){const model=models.find(x=>String(x.id)===$('evalModel').value);config.method=model.alignment_method||'exact';config.tolerance_seconds=model.tolerance_seconds||0;}
 const displayRole=role==='train'?'train':'eval';validateCoverage(displayRole);const request=++previewRequestSequence[displayRole];
 const result=scoreOnly?
  await post('/api/v2/inference/preview',{model_id:Number($('evalModel').value),dataset_selection:config,data_source:dataSourcePayload('eval')}):
  await post('/api/v2/run-dataset/preview',{role,dataset_selection:config,data_source:dataSourcePayload(role==='train'?'train':'eval'),
   label_target:role==='test'?evaluationTarget():{scope:'run'}});
 if(request!==previewRequestSequence[displayRole])return;
 previewCache[displayRole]=result;setWorkspace(displayRole,'data');
 const node=$(role==='train'?'trainPreview':'evalPreview');
 const invalid=result.sample_check?.unfilled_or_invalid_source_samples||0;
 node.textContent=scoreOnly?
  `已选择 ${result.feature_keys.length} 个参量、${result.aligned_count} 帧；全部真实标签未知。仅供模型推理，不计算准确率等监督指标；无效原始采样 ${invalid} 条。`:
  `已选择 ${result.feature_keys.length} 个参量，${result.runs.length} 次运行；目标 ${result.label_target?.scope||'run'}；已确认标签 ${result.aligned_count} 帧（正常 ${result.normal_count} / 故障 ${result.fault_count}），未知排除 ${result.label_coverage?.excluded_unknown||0}；无效原始采样 ${invalid} 条。`;
 node.title=(result.sample_check?.per_run||[]).map(run=>`运行 #${run.source_id}: `+run.per_feature.map(f=>`${f.feature_key}: 有效 ${f.valid_samples}/${f.total}`).join('、')).join('\n');
 workspaceNotice(displayRole,'已更新数据预览');renderDataPlot(displayRole);
}
function collectParameters(){const result={};for(const [name,entry] of parameterControls){const {control,definition}=entry;const guard=definition.visible_when;if(guard){const parent=parameterControls.get(guard.name);if(parent&&parent.control.value!==String(guard.value))continue;}
 let value;if(definition.type==='bool')value=control.checked;else if(definition.type==='int'||definition.type==='float'){if(!control.value.trim())continue;value=Number(control.value);if(!Number.isFinite(value)||(definition.type==='int'&&!Number.isInteger(value)))throw Error(name+' 需要数字');}else if(definition.type==='list'){try{value=JSON.parse(control.value);}catch{throw Error(name+' 应为 JSON 数组');}if(!Array.isArray(value))throw Error(name+' 应为数组');}else value=control.value;
 if((definition.type==='int'||definition.type==='float')&&((definition.min!==undefined&&value<definition.min)||(definition.max!==undefined&&value>definition.max)))throw Error(name+' 超出参数范围');result[name]=value;}return result;}
async function loadParameters(){const name=$('trainAlgorithm').value,node=$('trainAlgorithmParams');node.replaceChildren();parameterControls.clear();if(!name)return;const result=await api('/api/algorithm-config-schema?algorithm='+encodeURIComponent(name));algorithmSchema=result.schema;if(!algorithmSchema||!Array.isArray(algorithmSchema.parameters))throw Error('缺少算法参数 Schema');
 for(const def of algorithmSchema.parameters){const label=document.createElement('label');label.className='field';const title=document.createElement('span');title.textContent=def.label||def.name;let control;
 if(Array.isArray(def.options)&&def.options.length){control=document.createElement('select');for(const option of def.options)control.add(new Option(String(option),String(option)));}else{control=document.createElement('input');control.type=def.type==='bool'?'checkbox':(def.type==='int'||def.type==='float'?'number':'text');if(def.type==='float')control.step='any';if(def.min!==undefined)control.min=String(def.min);if(def.max!==undefined)control.max=String(def.max);}
 const value=def.default!==undefined?def.default:algorithmSchema.defaults?.[def.name];if(def.type==='bool')control.checked=Boolean(value);else if(value!==undefined&&value!==null)control.value=def.type==='list'?JSON.stringify(value):String(value);
 label.append(title,control);if(def.description){const hint=document.createElement('small');hint.textContent=def.description;label.append(hint);}node.append(label);parameterControls.set(def.name,{control,definition:def,label});}
 function vis(){for(const {definition,label} of parameterControls.values()){const rule=definition.visible_when,depends=rule?parameterControls.get(rule.name):null;if(rule)label.hidden=Boolean(depends)&&depends.control.value!==String(rule.value);}}for(const x of parameterControls.values())x.control.addEventListener('change',vis);vis();}
function updateTaskProgress(role,task){
 const progress=Math.min(100,Math.max(0,Number(task?.progress)||0));
 const status=String(task?.status||'running');
 const label=status==='cancelling'?'已请求取消，正在等待安全停止':
  (task?.message||task?.stage_label|| (role==='train'?'训练正在执行':'评估正在执行'));
 if(role==='train')setTrainingProgress(progress,label);
 else{
  $('evalProgressBar').value=progress;
  $('evalProgressLabel').textContent=label;
  $('evalProgressMeta').textContent=`当前进度 ${progress.toFixed(0)}% · ${task?.stage_label||'后端任务状态'}`;
 }
 const cancelButton=$(role==='train'?'cancelTrainTask':'cancelEvalTask');
 cancelButton.disabled=!(status==='running'&&task?.cancellable===true);
}
async function waitForTask(role,taskId,legacyStatusPath){
 activeTaskIds[role]=taskId;
 if(window.AASTasks?.wait){
  const terminal=await window.AASTasks.wait(taskId,{onUpdate:task=>updateTaskProgress(role,task)});
  updateTaskProgress(role,terminal);
  activeTaskIds[role]=null;
  return terminal;
 }
 // Compatibility path for older pages/facades. A browser timeout never cancels the server task.
 for(let index=0;index<7200;index++){
  await new Promise(resolve=>setTimeout(resolve,1000));
  const response=await api(legacyStatusPath+'?task_id='+encodeURIComponent(taskId));
  const task=response.task||response;
  updateTaskProgress(role,task);
  if(['completed','failed','cancelled','interrupted','success','error'].includes(task.status)||task.error){
   activeTaskIds[role]=null;
   return task;
  }
 }
 throw Error('本页等待超时，任务仍可能在后台运行；可在任务查询与导出中继续查看');
}
async function cancelActiveTask(role){
 const taskId=activeTaskIds[role];
 if(!taskId||!window.AASTasks?.cancel)return {accepted:false,reason:'没有可取消的活动任务'};
 const result=await window.AASTasks.cancel(taskId);
 if(result.accepted){
  const progress=$(role==='train'?'trainProgressBar':'evalProgressBar').value||0;
  updateTaskProgress(role,{status:'cancelling',progress,message:'已请求取消，正在等待当前安全步骤结束',cancellable:false});
 }else if(!result.cancelled_by_user){
  const label=$(role==='train'?'trainProgressLabel':'evalProgressLabel');
  label.textContent=result.reason||'当前阶段不能取消';
 }
 return result;
}
function taskResultOrThrow(role,task,taskId){
 if(task.status==='failed'||task.status==='error'||task.error){
  const error=Error(task.failure?.message||task.error||'任务执行失败');
  if(task.failure)error.failure=task.failure;
  error.task_id=taskId;
  throw error;
 }
 if(task.status==='cancelled')throw Error(role==='train'?'训练任务已取消':'评估/推理任务已取消');
 if(task.status==='interrupted')throw Error('服务重启导致任务中断；请检查任务历史后手动重新提交');
 if(task.status!=='completed'&&task.status!=='success')throw Error('任务进入未知状态：'+task.status);
 return task.result||task;
}
async function train(){
 clearTaskDiagnostic('train');
 const payload={dataset_selection:selection('train'),data_source:dataSourcePayload('train'),algorithm:$('trainAlgorithm').value,
  train_ratio:Number($('trainRatio').value),normalization_method:$('trainNormalization').value,algorithm_params:collectParameters()};
 if(!payload.algorithm)throw Error('请选择检测算法');
 trainingSnapshot=JSON.parse(JSON.stringify(payload));latest=null;
 $('publishPanel').hidden=true;$('runTrain').disabled=true;setWorkspace('train','progress');
 workspaceNotice('train','训练中');setTrainingProgress(0,'正在提交训练任务…');
 try{
  const submitted=await post('/api/v2/training/start',payload);
  if(!submitted.task_id)throw Error('训练服务未返回任务编号');
  setTrainingProgress(3,`任务已提交：${submitted.task_id}`);
  const task=await waitForTask('train',submitted.task_id,'/api/train-status');
  latest=taskResultOrThrow('train',task,submitted.task_id);
  const archived=latest.archive_status==='complete';
  setTrainingProgress(100,archived?'训练及实验归档已完成':'模型训练完成，但实验归档未完成');
  workspaceNotice('train',archived?'最近一次训练已归档':'训练完成，报告保存失败，请检查提示');
  $('trainResultEmpty').hidden=true;message('train',{experiment_id:latest.experiment_id,metrics:latest.metrics,
    task_selection:trainingSnapshot.dataset_selection,algorithm:trainingSnapshot.algorithm,
    archive_status:latest.archive_status});
  if(task.attention)renderTaskDiagnostic('train',task.attention,submitted.task_id);
  else clearTaskDiagnostic('train');
  window.AASReportNavigation?.renderLink('train',latest);
  renderResultChart('train',latest.summary?.result);$('publishPanel').hidden=false;
  setWorkspace('train','result');
 }catch(err){setTrainingProgress($('trainProgressBar').value,'任务未完成：'+err.message);
  workspaceNotice('train',activeTaskIds.train?'训练仍在后台运行':(String(err.message||'').includes('已取消')?'训练已取消':'训练失败或已中断'));throw err;}
 finally{$('runTrain').disabled=Boolean(activeTaskIds.train);}
}
async function publish(){if(!latest)throw Error('没有刚训练完成的模型');const name=$('publishName').value.trim();if(!name)throw Error('请输入模型名称');const result=await post('/api/models',{experiment_id:latest.experiment_id,model_name:name,model_desc:$('publishDesc').value.trim(),algorithm:latest.algorithm,dataset:latest.dataset,table_name:latest.table_name,file_path:latest.model_dir,metrics:latest.metrics||{},config:latest.config||{}});$('publishPanel').hidden=true;workspaceNotice('train','已发布模型');await refreshModels();}
function modelMatchesSelectedSemantics(model, selected){
 const saved=model.feature_semantics;
 if(!Array.isArray(saved)||saved.length!==selected.length)return false;
 const source=selectedScoreOnlyFeatures();
 const actual=selected.map(key=>source.find(feature=>feature.feature_key===key));
 if(actual.some(item=>!item))return false;
 const asKey=item=>JSON.stringify(item);
 const expected=saved.map(asKey).sort();
 const observed=actual.map(item=>asKey([item.subsystem,item.device,item.variable,item.unit])).sort();
 return expected.every((value,index)=>value===observed[index]);
}
async function refreshModels(){const result=await api('/api/v2/models');models=result.models||[];
 const scoreOnly=isScoreOnlySelection();
 const compatible=models.filter(model=>(model.coordinate_contract?.mode||'time')===coordinateFor('test').mode&&(!scoreOnly||model.integrity==='schema_bound')&&(!evalFeatures.length ||
  (scoreOnly?modelMatchesSelectedSemantics(model,evalFeatures):
   (model.feature_keys.length===evalFeatures.length && evalFeatures.every(feature=>model.feature_keys.includes(feature))))));
 const node=$('evalModel'),old=node.value;
 node.replaceChildren(new Option(compatible.length?'请选择兼容模型':'无兼容模型',''),...compatible.map(x=>new Option(`${x.model_name} · ${x.algorithm_label||x.algorithm} · ${x.feature_count} 列${x.integrity==='schema_invalid'?' · 指纹异常':''}`,x.id)));
 if(compatible.some(x=>String(x.id)===old))node.value=old;modelChanged();}
function schemaStateLabel(model){if(!model)return '';if(model.integrity==='schema_bound')return `列定义已绑定 · ${model.schema_sha256}`;if(model.integrity==='schema_invalid')return '列定义指纹损坏，禁止评估';return '历史模型：未绑定列定义，仅校验列顺序';}
function modelChanged(){scheduleCoverage('eval');updateLabelTargetOptions();const model=models.find(x=>String(x.id)===$('evalModel').value);const source=model?.data_source_provenance;const sourceText=source?.type==='user_csv'?`用户 CSV：${source.filename||source.workspace_dataset_id}`:'历史数据库';$('evalContract').textContent=model?`固定参量与顺序：${model.feature_keys.join(' → ')}；训练源：${model.train_source_ids.join(', ')}；训练数据来源：${sourceText}；${schemaStateLabel(model)}`:'请选择 V2 已发布的运行级模型';}
async function evaluate(){
 clearTaskDiagnostic('eval');
 const model=models.find(x=>String(x.id)===$('evalModel').value);if(!model)throw Error('请选择已发布模型');
 const scoreOnly=isScoreOnlySelection();
 if(scoreOnly&&model.integrity!=='schema_bound')throw Error('仅推理需要列定义已绑定的模型');
 const config=selection('test');config.method=model.alignment_method||'exact';config.tolerance_seconds=model.tolerance_seconds||0;
 evaluationSnapshot={model_id:model.id,dataset_selection:JSON.parse(JSON.stringify(config)),data_source:dataSourcePayload('eval')};
 if(!scoreOnly)evaluationSnapshot.label_target=evaluationTarget();
 $('runEval').disabled=true;showResult('eval');workspaceNotice('eval','评估中');
 $('evalProgressBar').value=0;$('evalProgressLabel').textContent='正在提交评估任务…';
 setWorkspace('eval','progress');
 try{
  let res=scoreOnly?
   await post('/api/v2/inference/run',{model_id:model.id,dataset_selection:config,data_source:dataSourcePayload('eval')}):
   await post('/api/v2/evaluation/run',{model_id:model.id,dataset_selection:config,data_source:dataSourcePayload('eval'),label_target:evaluationSnapshot.label_target});
  if(res.status==='accepted'&&res.task_id){
   const task=await waitForTask('eval',res.task_id,'/api/task-status');
   res=taskResultOrThrow('eval',task,res.task_id);
   if(task.attention)res={...res,archive_attention:task.attention,task_id:task.task_id};
  }
  $('evalResultEmpty').hidden=true;
  message('eval',scoreOnly?{真值状态:'未知（仅推理，不计算监督指标）',
    模型报警:'见下方曲线及归档实验报告',archive_status:res.archive_status,
    task_selection:evaluationSnapshot.dataset_selection,model_id:model.id,
    input_contract:res.input_contract}:{metrics:res.metrics,input_contract:res.input_contract,
    archive_status:res.archive_status,
    task_selection:evaluationSnapshot.dataset_selection,model_id:model.id,
    评估真值目标:JSON.stringify(res.input_contract?.label_target||{scope:"run"}),
    label_target:res.input_contract?.label_target,label_coverage:res.input_contract?.label_coverage});
  if(res.archive_attention)renderTaskDiagnostic('eval',res.archive_attention,res.task_id);
  else clearTaskDiagnostic('eval');
  window.AASReportNavigation?.renderLink('eval',res);
  renderResultChart('eval',res.summary?.result);
  workspaceNotice('eval',res.archive_status==='complete'?(scoreOnly?'无标签仅推理与报告归档完成':'最近一次评估已归档'):'计算结束，报告保存失败');
  setWorkspace('eval','result');
 }catch(err){workspaceNotice('eval',activeTaskIds.eval?'评估仍在后台运行':(String(err.message||'').includes('已取消')?'评估/推理已取消':'评估失败或已中断'));throw err;}
 finally{$('runEval').disabled=Boolean(activeTaskIds.eval);}
}
function attempt(callback,target){Promise.resolve().then(callback).catch(err=>{showResult(target);if(err.failure){renderTaskDiagnostic(target,err.failure,err.task_id);}else{clearTaskDiagnostic(target);message(target,err.message,true);}setWorkspace(target,'result');});}
for(const role of ['train','eval']){
 for(const view of ['data','progress','result']){
  $(role+view[0].toUpperCase()+view.slice(1)+'Tab').addEventListener('click',()=>setWorkspace(role,view));
 }
}
$('tabTrain').addEventListener('click',()=>selectTab('train'));$('tabEval').addEventListener('click',()=>selectTab('eval'));
for(const id of ['featureSubsystem','featureDevice','featureSearch'])$(id).addEventListener('input',renderFeatures);
$('evalTargetSplit').addEventListener('change',()=>{selectedRuns.test=[];refreshEvalSourceOptions();chooseSourceModel('test');attempt(refreshModels,'eval');});
$('evalLabelScope').addEventListener('change',updateLabelTargetOptions);$('evalModel').addEventListener('change',modelChanged);$('evalFeatureSearch').addEventListener('input',renderEvalFeatures);
 for(const role of ['train','test']){
  $(role==='train'?'trainSourceModel':'evalSourceModel').addEventListener('change',()=>{chooseSourceModel(role);if(role==='test')attempt(refreshModels,'eval');});
 }
 for(const role of ['train','eval']){
  const prefix=role;
  $(prefix+'DataSource').addEventListener('change',()=>{
   $(prefix+'UserCsvDataset').disabled=$(prefix+'DataSource').value!=='user_csv';
   attempt(async()=>{await loadWorkspaceDatasets(role);await loadRoleCatalog(role);if(role==='eval')await refreshModels();},role);
  });
  $(prefix+'UserCsvDataset').addEventListener('change',()=>attempt(async()=>{await loadRoleCatalog(role);if(role==='eval')await refreshModels();},role));
  $(prefix+'UserCsvDelete').addEventListener('click',()=>attempt(()=>deleteWorkspaceDataset(role),role));
 }
 $('trainAlgorithm').addEventListener('change',()=>attempt(loadParameters,'train'));
$('trainMethod').addEventListener('change',()=>{if($('trainMethod').value==='exact')$('trainTolerance').value='0';});
$('previewTrain').addEventListener('click',()=>attempt(()=>preview('train'),'train'));$('previewEval').addEventListener('click',()=>attempt(()=>preview('test'),'eval'));
$('runTrain').addEventListener('click',()=>attempt(train,'train'));$('runEval').addEventListener('click',()=>attempt(evaluate,'eval'));
$('cancelTrainTask').addEventListener('click',()=>cancelActiveTask('train').catch(error=>message('train',error.message,true)));
$('cancelEvalTask').addEventListener('click',()=>cancelActiveTask('eval').catch(error=>message('eval',error.message,true)));
$('publishModel').addEventListener('click',()=>attempt(publish,'train'));
const EMPTY_CATALOG=()=>({runs:[],features:[],run_features:{},default_train_ids:[],default_test_ids:[],max_features:32});
async function loadWorkspaceDatasets(role,preferredId=null){
 const prefix=role==='train'?'train':'eval',select=$(prefix+'UserCsvDataset'),deleteButton=$(prefix+'UserCsvDelete'),meta=$(prefix+'UserCsvMeta');
 const result=await api('/api/v2/user-csv-workspace/list?role='+encodeURIComponent(role==='train'?'train':'test'));
 const items=(result.datasets||[]).filter(item=>item.import_status==='imported');
 const previous=preferredId||select.value;
 select.replaceChildren(new Option('请选择我的 CSV',''),...items.map(item=>{
  const count=item.import_result?.row_count??item.metadata?.row_count??'?';
  const when=(item.created_utc||'').replace('T',' ').slice(0,16);
  return new Option(`${item.filename} · ${count} 帧 · ${when}`,item.id);
 }));
 if(items.some(item=>item.id===previous))select.value=previous;
 const selected=items.find(item=>item.id===select.value);
 select.disabled=$(prefix+'DataSource').value!=='user_csv'||!items.length;
 deleteButton.disabled=!selected||Number(selected.active_refs||0)>0;
 if(selected){
  const refs=Number(selected.active_refs||0);
  meta.textContent=`${selected.filename} · ${selected.size_bytes} bytes · SHA-256 ${selected.sha256.slice(0,12)}… · ${refs?`正在被 ${refs} 个任务引用，暂不能删除`:'持久保存，只有主动删除才清理'}`;
 }else if(items.length){meta.textContent='请选择一个已保存的 CSV 数据集。';}
 else{meta.textContent='当前没有已保存的 CSV。导入后会保留在独立工作区，不写入历史数据库。';}
 return items;
}
function refreshRoleControls(role){
 const current=catalogFor(role),isTrain=role==='train';
 selectedRuns[isTrain?'train':'test']=[...(isTrain?current.default_train_ids:current.default_test_ids)];
 if(isTrain){
  const node=$('trainSourceModel'),names=[...new Set(current.runs.filter(run=>run.allowed_role==='train').map(run=>run.model_alias))];
  node.replaceChildren(new Option('全部训练型号',''),...names.map(name=>new Option(name,name)));
  selectedFeatures=($('trainDataSource').value==='user_csv'&&selectedRuns.train.length===1?
   (current.run_features?.[String(selectedRuns.train[0])]||[]).map(item=>item.feature_key):[]);
  fillFilters();renderRuns('train');renderFeatures();
 }else{
  const splits=new Set(current.runs.filter(run=>['test','score_only'].includes(run.allowed_role)).map(run=>run.split));
  const picker=$('evalTargetSplit');
  [...picker.options].forEach(option=>option.disabled=!splits.has(option.value));
  if(!splits.has(picker.value))picker.value=splits.has('validation')?'validation':([...splits][0]||'test');
  const selected=current.runs.find(run=>current.default_test_ids.includes(run.source_id));
  if(selected&&splits.has(selected.split))picker.value=selected.split;
  refreshEvalSourceOptions();renderRuns('test');
  evalFeatures=($('evalDataSource').value==='user_csv'&&selectedRuns.test.length===1?
   (current.run_features?.[String(selectedRuns.test[0])]||[]).map(item=>item.feature_key):[]);
  for(const [id,key,title] of [['evalFeatureSubsystem','subsystem','全部分系统'],['evalFeatureDevice','device','全部部件']]){
   const values=[...new Set(current.features.map(feature=>feature[key]).filter(Boolean))].sort();
   $(id).replaceChildren(new Option(title,''),...values.map(value=>new Option(value,value)));
   if(!evalFilterListenersBound)$(id).addEventListener('change',renderEvalFeatures);
  }
  evalFilterListenersBound=true;updateEvalRunMode();renderEvalFeatures();updateLabelTargetOptions();
 }
 scheduleCoverage(isTrain?'train':'eval');
}
async function loadRoleCatalog(role){
 const prefix=role==='train'?'train':'eval',sourceType=$(prefix+'DataSource').value;
 let current;
 if(sourceType==='history'){
  if(!historyCatalog)historyCatalog=await api('/api/v2/run-dataset/catalog');
  current=historyCatalog;
 }else{
  const datasetId=$(prefix+'UserCsvDataset').value;
  if(!datasetId)current=EMPTY_CATALOG();
  else current=await api('/api/v2/run-dataset/catalog?source=user_csv&dataset_id='+encodeURIComponent(datasetId));
 }
 roleCatalogs[role]=current;
 if(sourceType==='user_csv'&&role==='eval'&&current.runs.length&&current.runs[0].split){
  const split=current.runs[0].split;
  if([...$('evalTargetSplit').options].some(option=>option.value===split))$('evalTargetSplit').value=split;
 }
 refreshRoleControls(role);
 if((role==='train'&&!$('tabEval').classList.contains('active'))||(role==='eval'&&$('tabEval').classList.contains('active')))ds=current;
 $('v2Connection').textContent='历史数据库与用户 CSV 工作区连接正常；各自独立读取。';
}
async function deleteWorkspaceDataset(role){
 const prefix=role==='train'?'train':'eval',select=$(prefix+'UserCsvDataset'),datasetId=select.value;
 if(!datasetId)throw Error('请先选择要删除的 CSV');
 const item=select.selectedOptions[0]?.textContent||datasetId;
 if(!window.confirm(`确定永久删除独立工作区中的 CSV“${item}”及其 V2 逻辑来源吗？历史数据库不会受影响；已有模型文件和报告不会删除。`))return;
 await post('/api/v2/user-csv-workspace/delete',{dataset_id:datasetId,confirm:true});
 await loadWorkspaceDatasets(role);
 if($(prefix+'DataSource').value==='user_csv')await loadRoleCatalog(role);
}
async function initialize(){
 const savedDraft=readRunnerDraft();
 const initialTab=new URLSearchParams(window.location.search).get('tab')==='eval'?'eval':'train';
 historyCatalog=await api('/api/v2/run-dataset/catalog');
 roleCatalogs.train=historyCatalog;roleCatalogs.eval=historyCatalog;ds=historyCatalog;
 await Promise.all([loadWorkspaceDatasets('train'),loadWorkspaceDatasets('eval')]);
 await loadRoleCatalog('train');await loadRoleCatalog('eval');selectTab(initialTab);
 const algorithms=await api('/api/available-algorithms');
 const options=(algorithms.algorithms||[]).map(item=>typeof item==='string'?[item,item]:[(item.name||item.id||item.value),(item.label||item.name||item.id||item.value)]).filter(pair=>pair[0]);
 $('trainAlgorithm').replaceChildren(...options.map(([value,label])=>new Option(label,value)));
 await loadParameters();await refreshModels();
 restoreRunnerDraft(savedDraft);
 // Reload the catalog after restoring source/dataset preferences. This deliberately
 // invalidates any old preview or preflight state and requires a fresh selection.
 await Promise.all([loadRoleCatalog('train'),loadRoleCatalog('eval')]);
 bindRunnerDraft();saveRunnerDraft();
}
async function onCsvImported(role,imported){
 const targetRole=role==='train'?'train':'eval',prefix=targetRole;
 $(prefix+'DataSource').value='user_csv';
 await loadWorkspaceDatasets(role,imported.dataset_id);
 $(prefix+'UserCsvDataset').value=imported.dataset_id;
 await loadRoleCatalog(targetRole);
 const current=catalogFor(targetRole),sourceId=Number(imported.source_id);
 const run=current.runs.find(item=>item.source_id===sourceId&&
  (targetRole==='train'?item.allowed_role==='train':['test','score_only'].includes(item.allowed_role)));
 if(!run)throw Error('CSV 已保存，但没有合规的训练/测试来源；请检查标签、时间基准和有效数值列。');
 selectedRuns[targetRole==='train'?'train':'test']=[sourceId];
 const keys=(current.run_features?.[String(sourceId)]||[]).map(item=>item.feature_key);
 if(targetRole==='train'){
  selectedFeatures=keys;renderRuns('train');renderFeatures();describeFeatures();selectTab('train');
 }else{
  $('evalTargetSplit').value=run.split;refreshEvalSourceOptions();$('evalSourceModel').value=run.model_alias;
  evalFeatures=keys;renderRuns('test');updateEvalRunMode();renderEvalFeatures();
  await refreshModels();updateLabelTargetOptions();selectTab('eval');
 }
 scheduleCoverage(targetRole);
 workspaceNotice(targetRole,`已从独立 CSV 工作区选择 ${imported.filename||'本地 CSV'}：${imported.row_count} 帧；历史数据库未被修改。`);
}
window.AASRunnerV2=Object.freeze({onCsvImported,refreshModels});
initialize().catch(err=>{$('v2Connection').textContent='数据源连接异常：'+err.message;$('v2Connection').classList.add('error');for(const id of ['runTrain','previewTrain','previewEval','runEval'])$(id).disabled=true;});
})();
