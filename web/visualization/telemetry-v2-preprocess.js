/* Native V2: immutable per-channel derived series. No physical table picker. */
(() => {
  'use strict';
  const node=id=>document.getElementById(id), form=node('v2PrepForm');
  if(!form)return;
  let preview=null,coverage=null,coverageSeq=0;
  const status=(text,invalid=false)=>{node('v2PrepStatus').textContent=text;node('v2PrepStatus').classList.toggle('error',invalid);};
  async function json(url,opts){
    const r=await fetch(url,opts),o=await r.json();if(!r.ok)throw new Error(o.error||`HTTP ${r.status}`);return o;
  }
  // datetime-local is a display-only calendar value. Keep the same time
  // coordinate as the V2 browser; never apply the browser's local TZ offset.
  function timeForApi(id){
    const raw=node(id).value.trim();
    if(!raw)return null;
    const calendar=raw.replace('T',' ');
    if(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(calendar))return calendar+':00';
    if(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(calendar))return calendar;
    throw Error('请选择有效的日期和时间（精确到秒）');
  }
  function resetCoverage(msg='选择逻辑参量后，自动限制可选日期。'){
    coverage=null;
    for(const id of ['v2PrepStart','v2PrepEnd']){const x=node(id);x.removeAttribute('min');x.removeAttribute('max');x.setCustomValidity('');}
    node('v2PrepTimeHint').textContent=msg;
  }
  async function updateCoverage(){
    const serial=++coverageSeq;resetCoverage();
    const cid=node('v2PrepChannel').value;
    if(!cid)return;
    node('v2PrepTimeHint').textContent='正在读取参量可选日期范围…';
    try{
      const args=new URLSearchParams({role:node('v2PrepRole').value,channel_ids:cid});
      const result=await json('/api/v2/telemetry/series/coverage?'+args);
      if(serial!==coverageSeq)return;
      coverage=result;
      if(!result.has_common_window){node('v2PrepTimeHint').textContent='该参量没有可用日期范围，请更换参量。';return;}
      let changed=false;
      for(const id of ['v2PrepStart','v2PrepEnd']){
        const x=node(id);x.min=result.min;x.max=result.max;x.step='0.001';
        if(x.value&&x.value<result.min){x.value=result.min;changed=true;}
        if(x.value&&x.value>result.max){x.value=result.max;changed=true;}
      }
      if(node('v2PrepStart').value&&node('v2PrepEnd').value&&node('v2PrepStart').value>node('v2PrepEnd').value){node('v2PrepEnd').value=node('v2PrepStart').value;changed=true;}
      node('v2PrepTimeHint').textContent=`可选范围：${result.min.replace('T',' ')} ～ ${result.max.replace('T',' ')}`+(changed?'；越界日期已自动调整。':'；留空可使用完整范围。');
    }catch(err){if(serial===coverageSeq)resetCoverage('无法读取时间范围：'+err.message);}
  }
  function recipe(){
    const method=node('v2PrepMethod').value,params={};
    if(method==='hampel_forward')params.window=Number(node('v2PrepHampelWindow').value);
    if(method==='moving_average'||method==='median_filter')params.window=Number(node('v2PrepWindow').value);
    if(method==='clip'){
      if(node('v2PrepMin').value.trim()===''||node('v2PrepMax').value.trim()==='')throw Error('请填写裁剪上下限');
      params.min=Number(node('v2PrepMin').value);params.max=Number(node('v2PrepMax').value);
    }
    if(!node('v2PrepChannel').value)throw Error('请选择逻辑参量');
    if(!coverage||!coverage.has_common_window)throw Error('请先选择参量并等待可用时间范围加载');
    for(const id of ['v2PrepStart','v2PrepEnd']){const x=node(id);if(x.value&&(x.value<x.min||x.value>x.max))throw Error('时间超出参量可用日期范围');}
    const start=timeForApi('v2PrepStart'),end=timeForApi('v2PrepEnd');
    if(start && end && start>end)throw Error('开始时间不能晚于结束时间');
    if(start && end && start===end)throw Error('开始和结束时间相同，只会命中一个时间点。请扩大时间范围或留空使用全部数据');
    return {channel_id:Number(node('v2PrepChannel').value),role:node('v2PrepRole').value,
      method,params,start,end};
  }
  function displayTimestamp(value){
    const channel=catalog.find(c=>String(c.id)===node('v2PrepChannel').value);
    if(!channel?.time_domains?.includes('numeric_unspecified'))return String(value ?? '');
    const seconds=Number(value);
    if(!Number.isFinite(seconds))return String(value ?? '');
    const date=new Date(Date.UTC(2026,0,1)+seconds*1000);
    return Number.isFinite(date.getTime())?date.toISOString().slice(0,19).replace('T',' '):String(value);
  }
  function invalidate(){preview=null;node('v2PrepCommit').disabled=true;window.aasV2QualityUI?.clear();}
  function methodFields(){
    const method=node('v2PrepMethod').value;
    node('v2PrepHampelField').hidden=method!=='hampel_forward';
    node('v2PrepLegacyWindowField').hidden=!['moving_average','median_filter'].includes(method);
    for(const id of ['v2PrepMin','v2PrepMax'])node(id).closest('label').hidden=method!=='clip';
  }
  let catalog=[];
  const facets=[['v2PrepModel','model','全部型号'],['v2PrepSubsystem','subsystem','全部分系统'],['v2PrepDevice','device','全部部件']];
  function filterCatalog(){
    const previous=node('v2PrepChannel').value;
    const selection=Object.fromEntries(facets.map(([,name])=>[name,null]));
    for(const [id,key,title] of facets){
      const select=node(id),chosen=select.value;
      const pool=catalog.filter(c=>facets.slice(0,facets.findIndex(x=>x[0]===id)).every(([ancestorId,ancestorKey])=>!node(ancestorId).value||c[ancestorKey]===node(ancestorId).value));
      const vals=[...new Set(pool.map(c=>c[key]).filter(Boolean))].sort();
      select.replaceChildren(new Option(title,''),...vals.map(v=>new Option(v,v)));
      select.value=vals.includes(chosen)?chosen:'';
      selection[key]=select.value;
    }
    const keyword=node('v2PrepKeyword').value.trim().toLowerCase();
    const results=catalog.filter(c=>facets.every(([id,key])=>!node(id).value||c[key]===node(id).value)&&
       (!keyword||[c.variable,c.device,c.subsystem,c.model].join(' ').toLowerCase().includes(keyword)));
    const list=node('v2PrepChannel');list.replaceChildren(new Option('请选择逻辑参量',''));
    for(const c of results)list.add(new Option(`${c.model} / ${c.subsystem} / ${c.device} / ${c.variable}${c.unit?' · '+c.unit:''}`,c.id));
    if(results.some(c=>String(c.id)===previous))list.value=previous;
    status(`找到 ${results.length} 个符合条件的参量，可在下方选择；生成结果不会修改源采样。`);updateCoverage();
  }
  async function reload(){
    invalidate();status('正在加载 V2 参量目录…');
    try{
      const role=node('v2PrepRole').value;
      const result=await json('/api/v2/telemetry/series/search?role='+encodeURIComponent(role));
      catalog=result.items||[];filterCatalog();
    }catch(e){status(e.message,true);}
  }
  form.addEventListener('change',invalidate);
  form.addEventListener('input',invalidate);
  node('v2PrepRole').addEventListener('change',reload);
  node('v2PrepMethod').addEventListener('change',methodFields);methodFields();
  for(const [id] of facets)node(id).addEventListener('change',()=>{invalidate();filterCatalog();});
  node('v2PrepKeyword').addEventListener('input',()=>{invalidate();filterCatalog();});
  node('v2PrepReload').addEventListener('click',reload);
  node('v2PrepChannel').addEventListener('change',()=>{
    const chosen=catalog.find(c=>String(c.id)===node('v2PrepChannel').value);
    if(chosen)status(`已选 ${chosen.variable}：${chosen.segment_count} 个有效数据分片、${chosen.row_count} 条采样。`);
    updateCoverage();
  });
  form.addEventListener('submit',async event=>{
    event.preventDefault();invalidate();
    try{
      const body=recipe();status('正在校验源参量与生成预览…');
      const data=await json('/api/v2/telemetry/preprocess/preview',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      preview={body,signature:JSON.stringify(body)};
      node('v2PrepCommit').disabled=false;
      window.aasV2QualityUI?.render(data);
      status(`预览成功：输入 ${data.input_count} 点，输出 ${data.output_count} 点；${displayTimestamp(data.first_timestamp)} ～ ${displayTimestamp(data.last_timestamp)}。不会覆盖源数据。`);
    }catch(e){status(e.message,true);}
  });
  node('v2PrepCommit').addEventListener('click',async()=>{
    try{
      if(!preview||JSON.stringify(recipe())!==preview.signature)throw Error('预览后参数已改变，请重新预览');
      if(!window.confirm('将创建独立的 V2 派生参量，不修改原始数据。确认执行？'))return;
      node('v2PrepCommit').disabled=true;
      const data=await json('/api/v2/telemetry/preprocess/commit',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...preview.body,confirm:true})});
      window.aasV2QualityUI?.jobs(json);
      invalidate();status(`已生成派生参量 #${data.output_channel_id}，${data.output_count} 点；可在相应数据用途下检索。`);
      document.dispatchEvent(new CustomEvent('aas:v2-imported'));
    }catch(e){invalidate();status(e.message,true);}
  });
  node('v2PrepJobsReload').addEventListener('click',()=>window.aasV2QualityUI?.jobs(json));
  window.aasV2QualityUI?.jobs(json);
  if(node('v2PreprocessPanel').open)reload();
  node('v2PreprocessPanel').addEventListener('toggle',()=>{
    node('v2PrepJobsReload').addEventListener('click',()=>window.aasV2QualityUI?.jobs(json));
  window.aasV2QualityUI?.jobs(json);
  if(node('v2PreprocessPanel').open)reload();
  });
})();
