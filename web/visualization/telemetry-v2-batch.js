/* Phase49: browse only administrator-allowed server-local telemetry directories.
 * Browsers never upload bulk files, pass absolute file paths or infer labels.
 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel=$('v2BatchPanel'); if(!panel)return;
  const state={taskId:null,running:false,timer:null};
  const show=(s,bad=false)=>{const e=$('v2BatchStatus');e.textContent=s;e.classList.toggle('error',bad);};
  async function api(route,payload){
    const rsp=await fetch('/api/v2/telemetry/batch/'+route,payload===undefined?{}:{
      method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await rsp.json();if(!rsp.ok)throw Error(data.error||'HTTP '+rsp.status);return data;
  }
  function options(select, items, placeholder){
    select.replaceChildren(new Option(placeholder,''),...items.map(x=>new Option(x,x)));
  }
  function clearPlan(){if(state.running)return;state.taskId=null;$('v2BatchStart').disabled=true;$('v2BatchPreviewSummary').textContent='';$('v2BatchCancel').disabled=true;$('v2BatchTaskProgress').value=0;$('v2BatchTaskProgressMeta').textContent='';}
  function renderTaskProgress(task){
    if(!task)return;
    const progress=Math.min(100,Math.max(0,Number(task.progress)||0));
    $('v2BatchTaskProgress').value=progress;
    $('v2BatchTaskProgressMeta').textContent=`统一任务状态：${task.status} · ${progress.toFixed(0)}% · ${task.message||task.stage_label||''}`;
    $('v2BatchCancel').disabled=!(task.status==='running'&&task.cancellable===true);
  }
  async function workspace(){
    const ws=await api('workspace');
    if(!ws.enabled){show(ws.reason,true);$('v2BatchPreview').disabled=true;return;}
    options($('v2BatchFolder'),ws.folders,'选择受管数据目录');
    options($('v2BatchTargets'),ws.archives,'选择 targets ZIP');
    const suggested=ws.archives.find(x=>x.toLowerCase().endsWith('targets.zip'));
    if(suggested)$('v2BatchTargets').value=suggested;
    $('v2BatchPreview').disabled=false;
    show('目录已就绪。先填写型号、补充单位后执行清单预检；不会上传大文件。');
  }
  function selectedUnits(){
    const entries={};
    for(const input of $('v2BatchUnitFields').querySelectorAll('input[data-parameter]')){
      const value=input.value.trim();if(value)entries[input.dataset.parameter]=value;
    }
    return entries;
  }
  function requestConfig(){
    return {folder:$('v2BatchFolder').value,targets_archive:$('v2BatchTargets').value,
      model:$('v2BatchModel').value.trim(),unit_overrides:selectedUnits(),
      confirm_normal:$('v2BatchNormal').checked};
  }
  function unitInputs(columns){
    const container=$('v2BatchUnitFields');container.replaceChildren();
    const cached=selectedUnits();
    for(const key of columns){
      const label=document.createElement('label');label.className='field';
      const span=document.createElement('span');span.textContent=key+' · 单位';
      const input=document.createElement('input');input.dataset.parameter=key;
      input.setAttribute('aria-label',key+' 单位');input.maxLength=32;
      input.placeholder='请根据原始参量定义确认';input.value=cached[key]||'';
      label.append(span,input);container.append(label);
      input.addEventListener('input',clearPlan);
    }
  }
  async function preview(){
    if(state.running){show('批量导入正在运行，不能更换清单或重新预检。',true);return;}
    clearPlan();$('v2BatchPreview').disabled=true;
    show('正在检索标签、运行身份和列映射…');
    try{
      const result=await api('preview',requestConfig());
      if(!result.ready){if(result.missing_units?.length)unitInputs(result.missing_units);
        show(result.message,true);return;}
      state.taskId=result.task_id;
      $('v2BatchStart').disabled=false;
      $('v2BatchPreviewSummary').textContent=`待处理 ${result.count} 个文件，已排除 train/FAULT ${result.excluded_count} 个，未匹配 ${result.unmatched_count} 个。\n`+
        `NORMAL训练数据整段正常确认：${result.normal_train_confirmed?'已显式确认':'未确认'}。\n`+
        '前 100 项：'+result.items.map(x=>x.filename+' ['+x.split+'/'+x.role+']').join('、');
      show(result.message+'。请审查列表后再点击开始。');
    }catch(e){show('预检失败：'+e.message,true);}
    finally{$('v2BatchPreview').disabled=false;}
  }
  async function refreshStatus(id=state.taskId){
    if(!id)return;
    try{
      const status=await api('status?task_id='+encodeURIComponent(id));
      if(window.AASTasks?.status){
        try{renderTaskProgress(await window.AASTasks.status(id));}
        catch(error){$('v2BatchTaskProgressMeta').textContent='统一任务状态暂时不可用：'+error.message;}
      }
      const counts=status.counts||{};
      show(`任务 ${id.slice(0,8)}：${status.status} · ${status.files_processed}/${status.files_total}；`+
        `成功 ${counts.imported||0}，已跳过 ${counts.skipped||0}，失败 ${counts.failed||0}`,
        status.status==='partial_failure'||status.status==='interrupted');
      const recent=(status.results||[]).slice(-1)[0];
      const storage=recent?.quality?.estimated_sqlite_growth_bytes;
      const diskHint=Number.isFinite(storage) && storage>0
        ? `；预估数据库增长 ${(storage/(1024**3)).toFixed(2)} GiB（保守估算，非实际占用）` : '';
      $('v2BatchQuality').textContent=recent?.quality ? ('最近完成：'+recent.filename+' · '+
        recent.row_count+' 行、'+recent.column_count+' 参量'+diskHint+'；质量统计：'+JSON.stringify(recent.quality)) : '';
      const failures=(status.results||[]).filter(r=>r.status==='failed').slice(-15);
      $('v2BatchFailures').textContent=failures.length?failures.map(x=>x.filename+'：'+x.error).join('\n'):'无失败文件';
      if(status.status==='running'){
        state.running=true;
        state.timer=setTimeout(()=>refreshStatus(id),1400);
      }else{
        state.running=false;
        $('v2BatchCancel').disabled=true;
        await history();
        if(status.status==='complete' && window.parent!==window && location.origin!=='null')
          window.parent.postMessage({type:'aas:v2-imported'},location.origin);
      }
    }catch(e){show('状态查询失败：'+e.message,true);state.running=false;}
  }
  async function cancelActive(){
    if(!state.taskId||!window.AASTasks?.cancel)return;
    try{
      const result=await window.AASTasks.cancel(state.taskId);
      if(result.accepted){$('v2BatchCancel').disabled=true;show('已请求取消；当前文件事务完成后停止后续文件。已提交文件不会回滚。');}
      else if(!result.cancelled_by_user)show(result.reason||'当前阶段不能取消',true);
    }catch(error){show('取消请求失败：'+error.message,true);}
  }
  async function launch(taskId, retry=false){
    if(state.running)return;
    state.running=true;
    if(state.timer)clearTimeout(state.timer);
    try{
      const result=await api(retry?'retry':'start',{task_id:taskId,confirm:true});
      state.taskId=result.task_id;$('v2BatchStart').disabled=true;
      $('v2BatchCancel').disabled=false;
      show('后台任务已开始，完成状态会持久保存。');
      await refreshStatus(result.task_id);
    }catch(e){state.running=false;show('启动失败：'+e.message,true);}
  }
  async function history(){
    try{
      const all=await api('history');const node=$('v2BatchHistory');node.replaceChildren();
      if(!all.items.length){node.textContent='没有批量导入任务。';return;}
      for(const item of all.items){
        const row=document.createElement('div');row.className='v2-batch-history-row';
        const summary=document.createElement('span');
        summary.textContent=item.id.slice(0,8)+' · '+item.status+' · '+item.files_processed+'/'+item.files_total+' · '+item.created_utc;
        const view=document.createElement('button');view.type='button';view.textContent='查看';
        view.addEventListener('click',()=>{state.taskId=item.id;refreshStatus(item.id);});
        row.append(summary,view);
        if(['interrupted','partial_failure','failed','cancelled'].includes(item.status)){
          const retry=document.createElement('button');retry.type='button';retry.textContent='重试失败/未完成文件';
          retry.addEventListener('click',()=>launch(item.id,true));row.append(retry);
        }
        node.append(row);
      }
    }catch(e){$('v2BatchHistory').textContent='历史读取失败：'+e.message;}
  }
  panel.addEventListener('toggle',()=>{if(panel.open){workspace().catch(e=>show(e.message,true));history();}});
  for(const id of ['v2BatchFolder','v2BatchTargets','v2BatchModel','v2BatchNormal']){
    $(id).addEventListener(id==='v2BatchNormal'?'change':'input',clearPlan);
  }
  $('v2BatchPreview').addEventListener('click',preview);
  $('v2BatchStart').addEventListener('click',()=>launch(state.taskId));
  $('v2BatchCancel').addEventListener('click',cancelActive);
  $('v2BatchRefresh').addEventListener('click',history);
})();
