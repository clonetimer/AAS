/* V2 telemetry browsing is intentionally independent of legacy physical-table browser.
 * No cross-channel sample alignment, filling, or role blending takes place here.
 */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const widget = byId('v2Browser');
  if (!widget) return;
  const KEYS = [
    ['namespace', 'v2Namespace', '全部来源'],
    ['model', 'v2Model', '全部型号'],
    ['subsystem', 'v2Subsystem', '全部分系统'],
    ['device', 'v2Device', '全部部件'],
  ];
  const colors = ['#3868cf','#0a9b8b','#c77a15','#aa4fc3','#df575f','#417eab','#759735','#ba6862'];
  const state = {candidates:[],channelCache:new Map(),facets:null,
    catalogPage:{limit:50,offset:0,count:0,hasMore:false,nextOffset:null},
    selected:[],lines:[],hidden:new Set(),seq:0,chartRequestSeq:0,chartLoading:false,
    chartBounds:null,chartWindow:null,committedChartWindow:null,facetSeq:0,
    detail:{channelId:null,cursorStack:[],page:null,loading:false,error:null,requestSeq:0},
    writeDatabase:null,coverage:null,coverageSeq:0,exporting:false,
    faultData:null,faultLoading:false,associationData:null,associationLoading:false};
  const DATE_FIELDS = ['v2Start','v2End'];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const role = () => byId('v2Role').value;
  const showBrowseUnion = 'union';
  const status = (message,error=false) => {
    const node=byId('v2CandidateStatus');node.textContent=message;node.classList.toggle('error',error);
  };
  const plotStatus = (message,error=false) => {
    const node=byId('v2PlotStatus');node.textContent=message;node.classList.toggle('error',error);
  };
  async function request(url, options) {
    const response=await fetch(url,options);
    let body;
    try {body=await response.json();}catch {throw Error(`服务响应格式错误（${response.status}）`);}
    if (!response.ok) throw Error(body.error || `HTTP ${response.status}`);
    return body;
  }
  // datetime-local supplies a timezone-free wall-clock value such as
  // 2026-01-01T00:01. Keep the UI's display coordinate; do not reinterpret it
  // in the browser's local timezone or shift relative simulation clocks.
  const timeForApi = id => {
    const value=byId(id).value.trim();
    if (!value) return '';
    const canonical=value.replace('T',' ');
    return /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(canonical) ? canonical+':00' : canonical;
  };
  function dateTimeMicros(raw) {
    const value=String(raw??'').trim().replace(' ','T');
    if(!value)return NaN;
    const fraction=(value.match(/\.(\d+)/)||[])[1]||'';
    const micros=Number((fraction+'000000').slice(0,6));
    const hasZone=/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
    const millis=Date.parse(hasZone?value:value+'Z');
    if(!Number.isFinite(millis))return NaN;
    return Math.floor(millis/1000)*1000000+micros;
  }
  function formatDateTimeMicros(value,domain) {
    const micros=Math.round(Number(value));
    if(!Number.isFinite(micros))throw Error('时间窗口坐标无效');
    const seconds=Math.floor(micros/1000000);
    const fraction=((micros%1000000)+1000000)%1000000;
    const prefix=new Date(seconds*1000).toISOString().slice(0,19);
    return `${prefix}.${String(fraction).padStart(6,'0')}${domain==='datetime_utc'?'Z':''}`;
  }
  function timeKeyCoordinate(value,domain) {
    return domain==='numeric_unspecified'||domain==='datetime_utc'?Number(value):dateTimeMicros(value);
  }
  function coordinateToApi(value,domain) {
    return domain==='numeric_unspecified'?Number(value):formatDateTimeMicros(value,domain);
  }
  // datetime-local values are wall-clock coordinates. Append Z only for numeric
  // comparison so browser timezone and optional ".000" precision do not alter
  // the V2 display coordinate. E.g. 00:00:00 equals 00:00:00.000.
  const dateValue = raw => {
    if (!raw) return null;
    const timestamp=Date.parse(String(raw).replace(' ','T')+'Z');
    return Number.isFinite(timestamp)?timestamp:NaN;
  };
  function showTimeError(message='') {
    const element=byId('v2TimeError');
    element.textContent=message;
    element.hidden=!message;
  }
  function timeError(id,value) {
    if(!value)return '';
    const input=byId(id),instant=dateValue(value);
    if(!Number.isFinite(instant))return '时间格式无效，请重新选择。';
    const bounds=state.coverage;
    if(bounds && (instant<dateValue(bounds.min)||instant>dateValue(bounds.max)))
      return '所选时间超出当前参量的可用范围，已拒绝该选择。';
    const other=byId(id==='v2Start'?'v2End':'v2Start').value;
    if(other && (id==='v2Start'?instant>dateValue(other):instant<dateValue(other)))
      return '开始时间不能晚于结束时间，请重新选择。';
    if(Number.isFinite(dateValue(input.min))&&instant<dateValue(input.min) ||
       Number.isFinite(dateValue(input.max))&&instant>dateValue(input.max))
      return '所选时间超出当前参量的可用范围，已拒绝该选择。';
    return '';
  }
  function updateDateConstraints() {
    const data=state.coverage;
    if(!data?.has_common_window)return;
    const start=byId('v2Start'),end=byId('v2End');
    // Native picker disables out-of-range dates and values. Also constrain
    // the two pickers to each other, so the end cannot precede the start.
    start.min=data.min;start.max=end.value && dateValue(end.value)<dateValue(data.max)?end.value:data.max;
    end.min=start.value && dateValue(start.value)>dateValue(data.min)?start.value:data.min;end.max=data.max;
  }
  function dateSelectionChanged(id) {
    const input=byId(id);
    if(!state.coverage?.has_common_window)return;
    const error=timeError(id,input.value);
    if(error){
      // Reject manual out-of-range edits instead of keeping invalid values in
      // the control or allowing an invalid plot/export request.
      input.value=input.dataset.lastValid||'';
      showTimeError(error);
    } else {
      input.dataset.lastValid=input.value;
      showTimeError('');
    }
    updateDateConstraints();
    selectedFiltersChanged();
    updateExportAvailability();
  }
  function rangeIsValid() {
    if(!state.selected.length||!state.coverage?.has_common_window)return false;
    const start=byId('v2Start').value,end=byId('v2End').value;
    return !timeError('v2Start',start)&&!timeError('v2End',end)&&
      !(start&&end&&dateValue(start)>dateValue(end));
  }
  function updateExportAvailability() {
    const available=rangeIsValid()&&!state.exporting;
    byId('v2Export').disabled=!available;
    return available;
  }
  function selectedFiltersChanged() {
    state.chartRequestSeq++;
    state.chartLoading=false;
    state.chartBounds=null;state.chartWindow=null;state.committedChartWindow=null;
    resetDetailState(true);
    byId('v2Export').disabled=true;
    if(state.lines.length)byId('v2ExportStatus').textContent='筛选条件已变动；导出采用当前勾选参量与日期。曲线请重新绘制以更新。';
    updateZoomControls();
  }
  function resetTimeBounds(message='请勾选参量以选择有效日期范围。') {
    state.coverage=null;
    showTimeError('');
    for(const id of DATE_FIELDS){
      const input=byId(id);input.removeAttribute('min');input.removeAttribute('max');
      input.setCustomValidity('');input.disabled=true;
      if(!state.selected.length){input.value='';input.dataset.lastValid='';}
    }
    byId('v2TimeHelp').textContent=message;
    updateExportAvailability();renderDetailControls();
  }
  function ensureTimeBounds() {
    if(!state.selected.length)throw Error('请先勾选需要查看的参量');
    if(!state.coverage)throw Error('请等待所选参量的可用时间范围加载');
    if(!state.coverage.has_common_window)throw Error('所选参量没有共同可用时间范围，请减少参量或切换数据库');
    const start=byId('v2Start').value,end=byId('v2End').value;
    if(timeError('v2Start',start)||timeError('v2End',end)||
       (start&&end&&dateValue(start)>dateValue(end)))
      throw Error('选定日期超出参量的可用时间范围，请重新选择');
  }
  async function updateTimeBounds() {
    const seq=++state.coverageSeq;
    resetTimeBounds();
    if(!state.selected.length)return;
    byId('v2TimeHelp').textContent='正在计算所选参量的有效时间范围…';
    try {
      const result=await request('/api/v2/telemetry/series/coverage?'+makeParams({
        database:byId('v2Database').value,role:role(),channel_ids:state.selected.join(','),mode:showBrowseUnion}));
      if(seq!==state.coverageSeq)return;
      state.coverage=result;
      if(!result.has_common_window){byId('v2TimeHelp').textContent='所选参量没有可解析的浏览时间范围，请减少参量或修改来源。';return;}
      let changed=false;
      for(const id of DATE_FIELDS){
        const input=byId(id);input.min=result.min;input.max=result.max;input.step='0.001';input.disabled=false;
        if(input.value && dateValue(input.value)<dateValue(result.min)){input.value=result.min;changed=true;}
        if(input.value && dateValue(input.value)>dateValue(result.max)){input.value=result.max;changed=true;}
      }
      if(byId('v2Start').value&&byId('v2End').value&&dateValue(byId('v2Start').value)>dateValue(byId('v2End').value)){
        byId('v2End').value=byId('v2Start').value;changed=true;
      }
      updateDateConstraints();
      for(const id of DATE_FIELDS)byId(id).dataset.lastValid=byId(id).value;
      byId('v2TimeHelp').textContent=`浏览总时间：${result.min.replace('T',' ')} ～ ${result.max.replace('T',' ')}`+
        (changed?'；原日期已调整到总边界。':'；超出总边界的日期不可选。')+
        ' 不同曲线允许没有重叠时段；中间的空白时间不自动填补。';
      updateExportAvailability();renderDetailControls();
    }catch(error){if(seq===state.coverageSeq)resetTimeBounds('时间范围查询失败：'+error.message);}
  }
  function validatedTimeRange() {
    ensureTimeBounds();
    const start=timeForApi('v2Start'),end=timeForApi('v2End');
    if(start&&end&&dateValue(start)>dateValue(end))throw Error('开始时间不能晚于结束时间');
    return {start,end};
  }
  const makeParams = object => {
    const params=new URLSearchParams();
    Object.entries(object).forEach(([key,value]) => {if (value !== null && value !== undefined && String(value).trim()!=='') params.set(key,value);});
    return params.toString();
  };
  function values() {
    const result={role:role(),database:byId('v2Database').value};
    for (const [key,id] of KEYS) result[key]=byId(id).value;
    result.keyword=byId('v2Keyword').value.trim();
    return result;
  }
  const FACET_FIELDS = {namespace:'namespaces',model:'models',subsystem:'subsystems',device:'devices'};
  async function updateFacets() {
    const sequence=++state.facetSeq;
    const filters=values();
    const facets=await request('/api/v2/telemetry/filter-options?'+makeParams(filters));
    if(sequence!==state.facetSeq)return;
    state.facets=facets;
    const current=Object.fromEntries(KEYS.map(([key,id])=>[key,byId(id).value]));
    for(const [key,id,title] of KEYS){
      const options=facets[FACET_FIELDS[key]]||[];
      const select=byId(id);
      select.replaceChildren(new Option(title,''),...options.map(value=>new Option(value,value)));
      select.value=options.includes(current[key])?current[key]:'';
    }
  }
  function label(channel) {
    return `${channel.model} / ${channel.subsystem} / ${channel.device} / ${channel.variable}`;
  }
  function renderCandidates() {
    const node=byId('v2Candidates');node.replaceChildren();
    const page=state.catalogPage;
    byId('v2Count').textContent=`共 ${page.count} 项`;
    const pageNo=Math.floor(page.offset/page.limit)+1;
    const totalPages=Math.max(1,Math.ceil(page.count/page.limit));
    const first=page.count?page.offset+1:0;
    byId('v2PageInfo').textContent=`第 ${pageNo} / ${totalPages} 页 · ${first}–${page.offset+state.candidates.length} 项`;
    byId('v2PrevPage').disabled=page.offset<=0;
    byId('v2NextPage').disabled=!page.hasMore;
    if (!state.candidates.length) {
      node.textContent='当前筛选条件下没有可查询的逻辑参量。';return;
    }
    for (const channel of state.candidates) {
      const item=document.createElement('label');item.className='v2-result';
      const check=document.createElement('input');check.type='checkbox';check.value=String(channel.id);
      check.checked=state.selected.includes(channel.id);
      const text=document.createElement('span');
      const name=document.createElement('strong');name.textContent=channel.variable;
      const detail=document.createElement('small');
      detail.textContent=`${channel.model} / ${channel.subsystem} / ${channel.device} · ${channel.namespace}`+
        ` · ${channel.segment_count} 个分片 · ${channel.row_count} 行 · ${channel.time_domains.join(', ')}`+
        `${channel.unit ? ' · '+channel.unit : ''}` +
        `${channel.unresolved_role_split ? ' · ⚠ 用途待核实，禁止自动拼接' : ''}`;
      text.append(name,detail);item.append(check,text);node.append(item);
      check.addEventListener('change',() => {
        if (check.checked) {
          if (state.selected.length>=8) {
            check.checked=false;status('最多同时绘制 8 个参量，请先移除其他曲线。',true);return;
          }
          state.selected.push(channel.id);
        } else {
          state.selected=state.selected.filter(id=>id!==channel.id);
        }
        selectedFiltersChanged();renderSelection();updateTimeBounds();
      });
    }
  }
  function renderFaultFinder() {
    const node=byId('v2FaultResults');node.replaceChildren();
    const data=state.faultData;
    if(!data)return;
    const type=byId('v2FaultType').value;
    const rows=(data.items||[]).filter(item=>!type||item.fault_types.includes(type));
    byId('v2FaultStatus').textContent=rows.length
      ? `找到 ${rows.length} 个故障相关参量${data.truncated?'（列表最多显示前 500 项，可按故障类型继续缩小）':''}；按事件标注、故障源部件和变化强度排序。`
      : '没有符合条件的故障标注或采样标签。可更换测试数据库/型号，或先为数据补充故障事件。';
    for (const item of rows) {
      const wrap=document.createElement('div');wrap.className='v2-fault-item';
      const info=document.createElement('div');info.className='v2-fault-item-body';
      const name=document.createElement('strong');name.textContent=label(item.channel);
      const evidence=document.createElement('small');
      const tags=[];
      if(item.evidence.includes('channel_event'))tags.push('参量事件标注');
      if(item.is_fault_origin)tags.push('故障源部件关联');
      if(item.evidence.includes('run_annotation'))tags.push('运行级故障真值（非本参量故障认定）');
      if(item.evidence.includes('sample_label'))tags.push(`故障标签 ${item.fault_samples} 点${item.invalid_fault_samples?' / 无效 '+item.invalid_fault_samples+' 点':''}`);
      evidence.textContent=tags.join(' · ');
      const hint=document.createElement('small');
      hint.textContent=`故障类型：${item.fault_types.join('、')||'未指定'} · `+
        `变化强度：${item.shift_sigma===null?'缺少可靠正常基线':item.shift_sigma+'σ（统计线索）'}`+
        (item.first_fault_timestamp?` · 标记起点：${item.first_fault_timestamp}`:'');
      info.append(name,evidence,hint);
      const view=document.createElement('button');view.type='button';view.textContent='查看曲线';
      view.setAttribute('aria-label','查看 '+label(item.channel)+' 故障相关曲线');
      view.addEventListener('click',()=>openFaultTrace(item));
      wrap.append(info,view);node.append(wrap);
    }
  }
  async function findFaults() {
    if(state.faultLoading)return;
    state.faultLoading=true;
    byId('v2FindFaults').disabled=true;
    byId('v2FaultStatus').textContent='正在检索测试数据中的故障证据…';
    try {
      const params=makeParams({database:byId('v2Database').value,role:'test',limit:500,fault_type:byId('v2FaultType').value,
        namespace:byId('v2Namespace').value,model:byId('v2Model').value,
        subsystem:byId('v2Subsystem').value,device:byId('v2Device').value,
        keyword:byId('v2Keyword').value.trim()});
      const data=await request('/api/v2/telemetry/fault-finder?'+params);
      state.faultData=data;
      const selector=byId('v2FaultType'),old=selector.value;
      selector.replaceChildren(new Option('全部故障类型',''),...(data.fault_types||[]).map(t=>new Option(t,t)));
      selector.disabled=!(data.fault_types||[]).length;
      selector.value=(data.fault_types||[]).includes(old)?old:'';
      renderFaultFinder();
    }catch(error){state.faultData=null;state.associationData=null;byId('v2AssociationResults').replaceChildren();byId('v2AssociationStatus').textContent='筛选条件已变化，请重新分析。';byId('v2FaultResults').replaceChildren();byId('v2FaultStatus').textContent='故障速查失败：'+error.message;}
    finally{state.faultLoading=false;byId('v2FindFaults').disabled=false;}
  }
  function renderAssociations() {
    const node=byId('v2AssociationResults');node.replaceChildren();
    const data=state.associationData;
    if(!data)return;
    const kind=byId('v2AssociationClass').value;
    const rows=(data.items||[]).filter(item=>kind==='all'||item.category===kind);
    byId('v2AssociationStatus').textContent=`${data.model} · ${rows.length} 个参量（总 ${data.total} 个）`+
      '；疑似变化只供排查，不代表部件损坏。';
    for (const item of rows){
      const wrap=document.createElement('div');wrap.className='v2-fault-item v2-association-item';
      const info=document.createElement('div');info.className='v2-fault-item-body';
      const badge=document.createElement('span');badge.className='v2-association-pill';
      badge.dataset.category=item.category;badge.textContent=item.status;
      const name=document.createElement('strong');name.textContent=label(item.channel);
      const stats=document.createElement('small');
      const shift=item.stats.shift_sigma,ratio=item.stats.std_ratio;
      stats.textContent=`故障前/后有效点 ${item.stats.normal_count}/${item.stats.fault_count}`+
        ` · 均值差 ${shift===null?'无法计算':shift+'σ'}`+
        ` · 波动比 ${ratio===null?'无法计算':ratio+'×'}`;
      const details=document.createElement('small');details.textContent=item.details;
      info.append(badge,name,stats,details);
      const view=document.createElement('button');view.type='button';view.textContent='查看曲线';
      view.setAttribute('aria-label','查看 '+label(item.channel)+' 关联参量曲线');
      view.addEventListener('click',()=>openFaultTrace(item));
      wrap.append(info,view);node.append(wrap);
    }
    if (!rows.length)node.textContent='该分类下没有参量；可以切换分类或更换卫星型号。';
  }
  async function findAssociations(){
    if(state.associationLoading)return;
    const model=byId('v2Model').value;
    if(!model){byId('v2AssociationStatus').textContent='请先在主页面筛选条件中选择具体卫星型号。';return;}
    state.associationLoading=true;byId('v2FindAssociations').disabled=true;
    byId('v2AssociationStatus').textContent='正在核对故障来源与正常基线…';
    try{
      const params=makeParams({database:byId('v2Database').value,role:'test',model,
        namespace:byId('v2Namespace').value,limit:500});
      const data=await request('/api/v2/telemetry/fault-associations?'+params);
      if(byId('v2Model').value!==model)throw Error('卫星型号已切换，请重新分析');
      state.associationData=data;renderAssociations();
    }catch(error){state.associationData=null;byId('v2AssociationResults').replaceChildren();
      byId('v2AssociationStatus').textContent='关联分析失败：'+error.message;}
    finally{state.associationLoading=false;byId('v2FindAssociations').disabled=false;}
  }
  function focusFaultRange(item) {
    // Use the existing date pickers to fetch the *fault window* instead of
    // the first 3000 chronological points of a multi-million-sample run.
    const bounds=state.coverage,domains=item.channel.time_domains||[];
    if(!bounds?.has_common_window||domains.length!==1||
       !['numeric_unspecified','datetime_naive'].includes(domains[0]))return false;
    const domain=domains[0];
    const beginning=eventTime(item.first_fault_timestamp,domain);
    if(!Number.isFinite(beginning))return false;
    const ending=eventTime(item.last_fault_timestamp,domain);
    const factor=domain==='numeric_unspecified'?1000:0.001;
    const origin=domain==='numeric_unspecified'?Date.UTC(2026,0,1):0;
    const at=beginning*factor+origin;
    const last=Number.isFinite(ending)?ending*factor+origin:at;
    const startBound=dateValue(bounds.min),endBound=dateValue(bounds.max);
    if(!Number.isFinite(startBound)||!Number.isFinite(endBound)||endBound-startBound<2000)return false;
    const context=Math.max(15_000,Math.min(90_000,Math.max(0,last-at)*.2));
    const left=Math.max(startBound,at-context),right=Math.min(endBound,at+Math.max(90_000,Math.min(300_000,last-at+context)));
    if(right-left<1000)return false;
    const start=byId('v2Start'),end=byId('v2End');
    start.value=new Date(left).toISOString().slice(0,23);
    end.value=new Date(right).toISOString().slice(0,23);
    start.dataset.lastValid=start.value;end.dataset.lastValid=end.value;
    updateDateConstraints();updateExportAvailability();
    return true;
  }
  async function openFaultTrace(item) {
    try {
      if(!state.faultData&&!state.associationData)throw Error('数据库或筛选条件已变，请重新查找');
      byId('v2Role').value='test';
      byId('v2Namespace').value=item.channel.namespace||'';
      byId('v2Model').value=item.channel.model||'';
      byId('v2Subsystem').value=item.channel.subsystem||'';
      byId('v2Device').value=item.channel.device||'';
      byId('v2Keyword').value='';
      selectedFiltersChanged();state.channelCache.clear();state.catalogPage.offset=0;
      await updateFacets();await search();
      if(!channelById(item.channel.id))throw Error('所选参量不属于当前测试用途，可能已变更或被隔离');
      state.selected=[item.channel.id];state.lines=[];state.hidden.clear();
      state.chartBounds=null;state.chartWindow=null;state.committedChartWindow=null;
      byId('v2Start').value='';byId('v2End').value='';
      renderCandidates();renderSelection();await updateTimeBounds();
      const focused=focusFaultRange(item);
      await plot();
      if(byId('v2FaultDialog').open)byId('v2FaultDialog').close();
      if(focused)byId('v2FaultStatus').textContent='已定位故障标签附近的时间窗口（含故障前后数据），可继续放大或重置。';
      byId('v2Chart').scrollIntoView({behavior:'smooth',block:'nearest'});
    }catch(error){byId('v2FaultStatus').textContent='打开故障曲线失败：'+error.message;}
  }
  async function search() {
    const sequence=++state.seq;
    status('正在检索参量目录…');
    try {
      const params={...values(),limit:state.catalogPage.limit,offset:state.catalogPage.offset};
      const response=await request('/api/v2/telemetry/series/search?'+makeParams(params));
      if(sequence!==state.seq)return;
      const items=response.items||[];
      const pageLimit=response.limit||state.catalogPage.limit;
      const count=response.count??items.length;
      const offset=response.offset??0;
      if(offset>0&&count>0&&!items.length){
        state.catalogPage.offset=Math.floor((count-1)/pageLimit)*pageLimit;
        return search();
      }
      state.candidates=items;
      state.catalogPage={limit:pageLimit,offset,count,hasMore:!!response.has_more,nextOffset:response.next_offset??null};
      items.forEach(channel=>state.channelCache.set(channel.id,channel));
      const keep=new Set([...items.map(channel=>channel.id),...state.selected]);
      for(const id of state.channelCache.keys())if(!keep.has(id))state.channelCache.delete(id);
      renderCandidates();renderSelection();status('');
    }catch(error){if(sequence===state.seq){status(error.message,true);byId('v2Candidates').replaceChildren();byId('v2Count').textContent='不可用';}}
  }
  async function refreshCatalog() {
    status('正在加载 V2 参量目录…');
    state.catalogPage.offset=0;
    try {
      await updateFacets();
      await search();
    }catch(error){status(error.message,true);byId('v2Candidates').replaceChildren();}
  }
  const channelById = id => state.channelCache.get(id) || state.candidates.find(item=>item.id===id);
  function renderDetailControls() {
    const detail=state.detail;
    const hasChannel=detail.channelId!==null&&state.selected.includes(detail.channelId);
    byId('v2LoadDetail').disabled=!hasChannel||detail.loading;
    byId('v2DetailPrev').disabled=detail.loading||detail.cursorStack.length<=1;
    byId('v2DetailNext').disabled=detail.loading||!detail.page?.page_has_more||!detail.page?.next_cursor;
    byId('v2DetailChannel').disabled=!state.selected.length;
  }
  function renderDetailChannelOptions() {
    const select=byId('v2DetailChannel');
    const previous=detailChannelValue();
    const channels=state.selected.map(channelById).filter(Boolean);
    select.replaceChildren(new Option('选择已选参量',''),...channels.map(channel=>
      new Option(label(channel),String(channel.id))));
    const keep=channels.some(channel=>String(channel.id)===String(previous));
    select.value=keep?String(previous):(channels.length?String(channels[0].id):'');
    state.detail.channelId=select.value?Number(select.value):null;
    renderDetailControls();
  }
  function detailChannelValue() {
    return state.detail.channelId??(byId('v2DetailChannel').value?Number(byId('v2DetailChannel').value):null);
  }
  function renderDetailPage() {
    const body=byId('v2DetailBody');body.replaceChildren();
    const page=state.detail.page;
    if(!page){
      const row=document.createElement('tr'),cell=document.createElement('td');
      cell.colSpan=7;cell.textContent=state.detail.error||'选择一个已选参量并加载明细。';
      row.append(cell);body.append(row);
      byId('v2DetailStatus').textContent=state.detail.error||'尚未加载采样明细';
      renderDetailControls();return;
    }
    const points=page.points||[];
    for(const point of points){
      const row=document.createElement('tr');
      const sourceRow=point.source_ordinal!==null&&point.source_ordinal!==undefined&&
        Number.isFinite(Number(point.source_ordinal))?Number(point.source_ordinal)+1:null;
      const values=[point.frame_index,point.timestamp,
        point.value===null||point.value===undefined?'—':fmt(Number(point.value)),
        point.label===0||point.label===1?String(point.label):'未知',
        point.source_table||'—',point.segment_id??'—',sourceRow===null?'—':`第 ${sourceRow} 行`];
      for(const value of values){const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);}
      body.append(row);
    }
    const first=points[0]?.frame_index??0,last=points.at(-1)?.frame_index??0;
    const pageSummary=points.length
      ? `逻辑帧 ${first}–${last} / ${page.frame_count} · 本页 ${page.page_row_count} 帧 · 有效样本 ${page.valid_sample_count} · 重复记录 ${page.duplicate_count}`
      : `当前时间范围内没有采样帧 · 总逻辑帧 ${page.frame_count}`;
    byId('v2DetailStatus').textContent=state.detail.error?`${pageSummary} · 读取错误：${state.detail.error}`:pageSummary;
    renderDetailControls();
  }
  function resetDetailState(keepChannel=true) {
    const detail=state.detail;
    detail.requestSeq++;detail.cursorStack=[];detail.page=null;detail.loading=false;detail.error=null;
    if(!keepChannel||!state.selected.includes(detail.channelId))detail.channelId=null;
    renderDetailChannelOptions();renderDetailPage();
  }
  async function loadDetailPage(cursor=null,{resetStack=false,pushCursor=false,replaceIndex=null}={}) {
    const channelId=detailChannelValue();
    if(!channelId||!state.selected.includes(channelId)){
      state.detail.error='请选择一个当前已选参量。';renderDetailPage();return false;
    }
    const start=timeForApi('v2Start'),end=timeForApi('v2End');
    if(start&&timeError('v2Start',byId('v2Start').value)||end&&timeError('v2End',byId('v2End').value)){
      state.detail.error='当前时间范围无效，请调整日期后重新加载。';renderDetailPage();return false;
    }
    if(start&&end&&dateValue(start)>dateValue(end)){
      state.detail.error='开始时间不能晚于结束时间。';renderDetailPage();return false;
    }
    const sequence=++state.detail.requestSeq;
    state.detail.loading=true;state.detail.error=null;renderDetailControls();
    try{
      const body={role:role(),database:byId('v2Database').value,channel_ids:[channelId],mode:'page',limit:100};
      if(start)body.start=start;if(end)body.end=end;if(cursor)body.cursor=cursor;
      const response=await request('/api/v2/telemetry/query',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),
      });
      if(sequence!==state.detail.requestSeq)return false;
      const series=response.series||[];
      if(series.length!==1||Number(series[0].channel?.id)!==channelId)
        throw Error('服务返回的采样明细与所选参量不一致。');
      const page=series[0];
      if(!Array.isArray(page.points)||page.points.length>100||page.page_row_count>100)
        throw Error('服务返回的明细页超过 100 帧上限，已拒绝显示。');
      if(resetStack)state.detail.cursorStack=[null];
      else if(pushCursor)state.detail.cursorStack.push(cursor);
      else if(Number.isInteger(replaceIndex))state.detail.cursorStack=state.detail.cursorStack.slice(0,replaceIndex+1);
      state.detail.channelId=channelId;state.detail.page=page;state.detail.error=null;
      renderDetailPage();return true;
    }catch(error){
      if(sequence===state.detail.requestSeq){state.detail.error=error.message;renderDetailPage();}
      return false;
    }finally{
      if(sequence===state.detail.requestSeq){state.detail.loading=false;renderDetailControls();}
    }
  }
  function renderSelection() {
    const node=byId('v2Selected');node.replaceChildren();
    if (!state.selected.length) {node.textContent='尚未选择曲线';renderDetailChannelOptions();return;}
    state.selected.forEach((id,index)=>{
      const channel=channelById(id), row=document.createElement('div');row.className='v2-selected-row';
      const dot=document.createElement('i');dot.className='v2-swatch';dot.style.background=colors[index%colors.length];
      const name=document.createElement('span');name.className='v2-selected-name';
      name.textContent=channel ? label(channel) : `参量 #${id}`;
      const toggle=document.createElement('button');toggle.type='button';toggle.textContent=state.hidden.has(id)?'显示':'隐藏';
      toggle.addEventListener('click',()=>{state.hidden.has(id)?state.hidden.delete(id):state.hidden.add(id);renderSelection();renderChart();});
      const remove=document.createElement('button');remove.type='button';remove.textContent='移除';
      remove.addEventListener('click',()=>{state.selected=state.selected.filter(item=>item!==id);state.hidden.delete(id);state.lines=state.lines.filter(line=>line.channel.id!==id);selectedFiltersChanged();renderCandidates();renderSelection();renderChart();updateTimeBounds();});
      row.append(dot,name,toggle,remove);node.append(row);
    });
    renderDetailChannelOptions();
  }
  function timeValue(point,domain) {
    return timeKeyCoordinate(point.time_key,domain);
  }
  function eventTime(raw,domain) {
    return domain==='numeric_unspecified'?Number(raw):dateTimeMicros(raw);
  }
  function combineSeriesWindow(series) {
    const available=(series||[]).filter(line=>line.window&&line.window.start_key!==null&&line.window.end_key!==null);
    if(!available.length)return null;
    const domain=available[0].time_domain;
    const starts=available.map(line=>timeKeyCoordinate(line.window.start_key,domain)).filter(Number.isFinite);
    const ends=available.map(line=>timeKeyCoordinate(line.window.end_key,domain)).filter(Number.isFinite);
    if(!starts.length||!ends.length)return null;
    return {domain,start:Math.min(...starts),end:Math.max(...ends)};
  }
  function updateChartMeta() {
    const totals=(state.lines||[]).reduce((sum,line)=>({
      frames:sum.frames+(line.frame_count||0),valid:sum.valid+(line.valid_sample_count||0),
      plots:sum.plots+(line.plot_point_count||0),duplicates:sum.duplicates+(line.duplicate_count||0),
    }),{frames:0,valid:0,plots:0,duplicates:0});
    byId('v2PlotMeta').textContent=state.lines.length
      ? `${state.lines.length} 条参量合计 · 逻辑帧 ${totals.frames} · 有效样本 ${totals.valid} · 绘图点 ${totals.plots} · 重复记录 ${totals.duplicates}`
      : '请选择参量';
    byId('v2PlotMeta').title='按参量累加统计；不同参量之间不做时间戳去重。逻辑帧按时间键去重。';
  }
  function fmt(v) {
    if (!Number.isFinite(v)) return '—';
    return Math.abs(v)>1e5 || (Math.abs(v)<1e-3 && v!==0) ? v.toExponential(2) : Number(v.toPrecision(6)).toString();
  }
  function renderChart() {
    const target=byId('v2Chart');
    const lines=state.lines.filter(item=>!state.hidden.has(item.channel.id) && item.points.length);
    if (!lines.length){target.innerHTML='<div class="empty-state">选择参量并绘图；若无曲线，请检查时间范围和数据用途。</div>';updateChartMeta();updateZoomControls();return;}
    const domains=new Set(lines.map(line=>line.time_domain));
    if (domains.size>1){target.innerHTML='<div class="empty-state">选择了不同时间域的参量（数字仿真时间 / 无时区日期 / UTC），禁止混合绘制。请分开查看或先完成时间基准映射。</div>';return;}
    const domain=lines[0].time_domain;
    const timelines=lines.flatMap(line=>line.points.map(point=>({time:timeValue(point,domain),raw:point.timestamp})))
      .filter(row=>Number.isFinite(row.time)).sort((a,b)=>a.time-b.time);
    if (!timelines.length) {target.textContent='无法解析有效时间坐标';return;}
    const allMin=timelines[0].time,allMax=timelines[timelines.length-1].time;
    const minX=state.chartWindow?state.chartWindow.start:allMin;
    const maxX=state.chartWindow?state.chartWindow.end:allMax;
    if(maxX<=minX){target.textContent='当前时间窗口过窄，无法显示曲线。';updateZoomControls();return;}
    const width=1060,padL=105,padR=24,padTop=30,band=156,height=padTop+band*lines.length+38;
    const plotW=width-padL-padR;
    const xcoord=v=>padL+(maxX===minX?0.5:(v-minX)/(maxX-minX))*plotW;
    const pieces=[`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${lines.length} 条参量曲线；时间 ${esc(timelines[0].raw)} 到 ${esc(timelines.at(-1).raw)}">`];
    pieces.push(`<defs><clipPath id="v2PlotClip"><rect x="${padL}" y="${padTop}" width="${plotW}" height="${band*lines.length-29}"/></clipPath></defs>`);
    for (let j=0;j<=4;j++) {
      const x=padL+plotW*j/4;
      pieces.push(`<path d="M${x.toFixed(2)} ${padTop} V${height-34}" stroke="#e2e7ee" stroke-width="1"/>`);
      const ref=timelines.reduce((best,item)=>Math.abs(item.time-(minX+(maxX-minX)*j/4))<Math.abs(best.time-(minX+(maxX-minX)*j/4))?item:best,timelines[0]);
      pieces.push(`<text x="${x}" y="${height-16}" fill="#65718a" text-anchor="middle" font-size="10">${esc(String(ref.raw).slice(0,24))}</text>`);
    }
    lines.forEach((line,i)=>{
      const top=padTop+i*band,idx=state.selected.indexOf(line.channel.id),color=colors[idx%colors.length];
      // Render independent event annotations behind the telemetry trace. A fault
      // event is an annotation, never an algorithm decision or a sample rewrite.
      for (const event of (line.events||[])) {
        const begin=eventTime(event.start_raw,domain),end=eventTime(event.end_raw,domain);
        if(!Number.isFinite(begin)||!Number.isFinite(end)||end<=minX||begin>=maxX||end<=begin)continue;
        const left=xcoord(Math.max(minX,begin)),right=xcoord(Math.min(maxX,end));
        const fault=event.label===1,colorFill=fault?'#e35454':'#2f9c7e';
        pieces.push(`<rect x="${left.toFixed(2)}" y="${top+20}" width="${Math.max(1,right-left).toFixed(2)}" height="${band-50}" fill="${colorFill}" fill-opacity="0.12" stroke="${colorFill}" stroke-dasharray="4 3" stroke-width="1"><title>${fault?'故障事件':'正常事件'} #${esc(event.id)}&#10;${esc(event.fault_type)}&#10;[${esc(event.start_raw)}, ${esc(event.end_raw)})&#10;${esc(event.annotation_scope==='simulation_run'?'仿真运行级故障标注（非该参量自身故障）':'参量事件')}&#10;${esc(event.note||'')}</title></rect>`);
      }
      // Amber marks an inspection candidate, never an attributed failure.
      // Only use the run-specific association returned by the read-only analyzer.
      const association=line.association;
      if(association?.category==='affected' && association.first_fault_timestamp &&
         association.last_fault_timestamp){
        const begin=eventTime(association.first_fault_timestamp,domain);
        const end=eventTime(association.last_fault_timestamp,domain);
        if(Number.isFinite(begin)&&Number.isFinite(end)&&end>minX&&begin<maxX){
          const left=xcoord(Math.max(minX,begin)),right=xcoord(Math.min(maxX,end));
          pieces.push(`<rect x="${left.toFixed(2)}" y="${top+20}" width="${Math.max(1,right-left).toFixed(2)}" height="${band-50}" fill="#e4a13e" fill-opacity="0.10"><title>疑似受影响时段（仅统计线索，不代表该参量发生故障）</title></rect>`);
        }
      }
      const data=line.points.filter(point=>point.value!==null && Number.isFinite(Number(point.value)))
        .map(point=>({...point,x:timeValue(point,domain)})).filter(point=>Number.isFinite(point.x));
      if (!data.length) return;
      const allValues=data.map(point=>Number(point.value));
      const lo=Math.min(...allValues),hi=Math.max(...allValues);
      const ycoord=value=>top+band-36-(hi===lo?0.5:(value-lo)/(hi-lo))*(band-65);
      pieces.push(`<text x="8" y="${top+17}" fill="${color}" font-weight="600" font-size="12">${esc(line.channel.variable)}</text>`);
      pieces.push(`<text x="8" y="${top+34}" fill="#66768a" font-size="10">${esc(line.channel.device)} ${esc(line.channel.unit || '单位未记录')}</text>`);
      pieces.push(`<text x="${padL-10}" y="${top+28}" text-anchor="end" font-size="10" fill="#64748b">${fmt(hi)}</text>`);
      pieces.push(`<text x="${padL-10}" y="${top+band-31}" text-anchor="end" font-size="10" fill="#64748b">${fmt(lo)}</text>`);
      pieces.push(`<path d="M${padL} ${top+band-29} H${width-padR}" stroke="#adb8c6" fill="none"/>`);
      const steps=data.slice(1).map((point,k)=>point.x-data[k].x).filter(v=>v>0).sort((a,b)=>a-b);
      const median=steps.length?steps[Math.floor(steps.length/2)]:0;
      let section=[];
      const flush=()=>{if(section.length>1)pieces.push(`<polyline clip-path="url(#v2PlotClip)" points="${section.join(' ')}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`);section=[];};
      data.forEach((point,k)=>{
        if(k && median>0 && (point.x-data[k-1].x)>4*median)flush();
        const xx=xcoord(point.x),yy=ycoord(Number(point.value));
        section.push(`${xx.toFixed(2)},${yy.toFixed(2)}`);
        // SVG <title> exposes the real timestamp, source table and source row on hover.
        if(xx < padL || xx > width-padR) return;
        const inChannelEvent=(line.events||[]).some(event=>event.label===1&&
          Number.isFinite(eventTime(event.start_raw,domain))&&
          point.x>=eventTime(event.start_raw,domain)&&point.x<eventTime(event.end_raw,domain));
        pieces.push(`<circle cx="${xx.toFixed(2)}" cy="${yy.toFixed(2)}" r="${inChannelEvent?'3.5':'2.4'}" fill="${inChannelEvent?'#d84a4a':color}" opacity="${inChannelEvent?'1':'0.72'}"><title>${esc(label(line.channel))}&#10;${esc(point.timestamp)} : ${esc(point.value)}&#10;${esc(point.source_table)} 第 ${Number(point.source_ordinal)+1} 帧&#10;${esc(inChannelEvent?'参量级故障事件已标注':point.label===1?'运行/来源级故障时间标签（非本参量故障认定）':point.label===0?'采样标签 0':'标签未知')}</title></circle>`);
      });flush();
      pieces.push(`<text x="${width-padR}" y="${top+17}" font-size="10" fill="#637287" text-anchor="end">逻辑帧 ${line.frame_count} · 有效 ${line.valid_sample_count} · 绘图 ${line.plot_point_count} · 重复 ${line.duplicate_count}</text>`);
    });
    pieces.push('</svg>');target.innerHTML=pieces.join('');
    // Visible brush overlay with pointer capture; no cross-run interpolation.
    const svg=target.querySelector('svg');
    const createSvg=(tag,attributes)=>{const element=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attributes).forEach(([k,v])=>element.setAttribute(k,v));return element;};
    const brush=createSvg('rect',{class:'v2-brush-selection',x:padL,y:padTop,width:0,height:height-padTop-34,fill:'#4286d8','fill-opacity':'0.19',stroke:'#286fbd','stroke-width':'1.8','stroke-dasharray':'6 3',visibility:'hidden','pointer-events':'none'});
    const brushText=createSvg('text',{class:'v2-brush-label',x:padL,y:padTop+15,fill:'#1c4c83','font-size':'13','font-weight':'700',visibility:'hidden','pointer-events':'none'});
    svg.append(brush,brushText);
    let anchor=null,pointerId=null;
    const project=clientX=>{const rect=svg.getBoundingClientRect();return Math.max(0,Math.min(1,((clientX-rect.left)*width/rect.width-padL)/plotW));};
    function paintBrush(current){
      const left=padL+Math.min(anchor,current)*plotW,right=padL+Math.max(anchor,current)*plotW;
      brush.setAttribute('x',String(left));brush.setAttribute('width',String(right-left));brush.setAttribute('visibility','visible');
      brushText.textContent=`选中 ${(Math.abs(current-anchor)*(maxX-minX)).toFixed(2)} 时间单位`;
      brushText.setAttribute('x',String(Math.min(left+8,width-padR-200)));brushText.setAttribute('visibility','visible');
    }
    const clearBrush=()=>{brush.setAttribute('visibility','hidden');brushText.setAttribute('visibility','hidden');anchor=null;pointerId=null;};
    svg.addEventListener('pointerdown',event=>{
      if(event.button!==0||event.target.closest?.('button'))return;
      const fraction=project(event.clientX);anchor=fraction;pointerId=event.pointerId;
      svg.setPointerCapture?.(pointerId);paintBrush(fraction);
    });
    svg.addEventListener('pointermove',event=>{if(anchor!==null&&event.pointerId===pointerId)paintBrush(project(event.clientX));});
    svg.addEventListener('pointerup',event=>{
      if(anchor===null||event.pointerId!==pointerId)return;
      const end=project(event.clientX),initial=anchor;clearBrush();
      if(Math.abs(initial-end)<0.015)return;
      const start=minX+Math.min(initial,end)*(maxX-minX),finish=minX+Math.max(initial,end)*(maxX-minX);
      if(finish>start){loadChartWindow({apiStart:coordinateToApi(start,domain),apiEnd:coordinateToApi(finish,domain),
        requestedWindow:{domain,start,end:finish}});}
    });
    svg.addEventListener('pointercancel',clearBrush);
    svg.addEventListener('wheel',event=>{event.preventDefault();zoom(event.deltaY>0?1.35:0.72,project(event.clientX));},{passive:false});
    svg.addEventListener('dblclick',()=>resetChartWindow());
    updateZoomControls();
  }
  function updateZoomControls() {
    const bounds=state.chartBounds,current=state.chartWindow;
    const total=bounds?Math.max(0,bounds.end-bounds.start):0;
    const span=current?Math.max(0,current.end-current.start):total;
    const scale=total>0&&span>0?total/span:1;
    const loading=!!state.chartLoading;
    byId('v2ZoomIn').disabled=loading||!bounds||span<=total/100000;
    byId('v2ZoomOut').disabled=loading||!bounds||span>=total*.999999;
    byId('v2ZoomReset').disabled=loading||!bounds||span>=total*.999999;
    byId('v2ZoomStatus').textContent=loading?'正在读取窗口…':scale<1.000001?'全范围':`已放大 ${scale.toFixed(1)} 倍 · 服务端按窗口读取`;
  }
  async function loadChartWindow({apiStart=null,apiEnd=null,requestedWindow=null,resetBounds=false,loadEvents=false}={}) {
    const sequence=++state.chartRequestSeq;
    const ids=[...state.selected],oldRole=role();
    if(!ids.length)return false;
    if(requestedWindow)state.chartWindow={...requestedWindow};
    state.chartLoading=true;updateZoomControls();plotStatus('正在按时间窗口读取曲线…');
    try {
      const body={role:oldRole,database:byId('v2Database').value,channel_ids:ids,limit:3000,mode:'overview'};
      if(apiStart!==null&&apiStart!=='')body.start=apiStart;
      if(apiEnd!==null&&apiEnd!=='')body.end=apiEnd;
      const data=await request('/api/v2/telemetry/query',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),
      });
      if(sequence!==state.chartRequestSeq)return false;
      const nextLines=data.series||[];
      const previous=new Map(state.lines.map(line=>[line.channel.id,line]));
      if(loadEvents) {
        const overlays=await Promise.allSettled(nextLines.map(line=>request('/api/v2/telemetry/events?'+
          makeParams({channel_id:line.channel.id,role:oldRole}))));
        if(sequence!==state.chartRequestSeq)return false;
        nextLines.forEach((line,index)=>{
          const payload=overlays[index].status==='fulfilled'?overlays[index].value:{};
          line.events=payload.items||[];line.runAnnotations=payload.run_annotations||[];
          line.association=(state.associationData?.items||[]).find(item=>item.channel.id===line.channel.id)||null;
        });
      } else {
        nextLines.forEach(line=>{
          const old=previous.get(line.channel.id)||{};
          line.events=old.events||[];line.runAnnotations=old.runAnnotations||[];
          line.association=old.association||(state.associationData?.items||[]).find(item=>item.channel.id===line.channel.id)||null;
        });
      }
      if(sequence!==state.chartRequestSeq)return false;
      state.lines=nextLines;
      state.hidden=new Set([...state.hidden].filter(id=>ids.includes(id)));
      if(resetBounds) {
        state.chartBounds=combineSeriesWindow(nextLines);
        state.chartWindow=state.chartBounds?{...state.chartBounds}:null;
        state.committedChartWindow=state.chartWindow?{...state.chartWindow}:null;
      } else if(requestedWindow) {
        state.chartWindow={...requestedWindow};
        state.committedChartWindow={...requestedWindow};
      }
      updateChartMeta();
      const truncated=state.lines.filter(line=>line.truncated);
      const runOnly=state.lines.filter(line=>!line.events.length&&(line.runAnnotations||[]).length);
      if(truncated.length) {
        const counts=truncated.map(line=>`${line.channel.variable}：${line.frame_count} 帧 / ${line.valid_sample_count} 有效 / ${line.plot_point_count} 绘图点`).join('；');
        plotStatus(`当前窗口使用有界概览；可继续放大或框选读取更细数据。${counts}`);
      } else if(runOnly.length) {
        plotStatus('当前仅对参量自身故障事件绘制区间；同一次运行中的其他部件故障不会再给全部参量统一标红。');
      } else plotStatus('');
      const plotted=state.lines.reduce((sum,line)=>sum+(line.plot_point_count||0),0);
      byId('v2ExportStatus').textContent=plotted?'导出包含所选参量与时间范围内的完整数据，不受曲线概览点数上限影响。':'';
      updateExportAvailability();renderSelection();renderChart();
      return true;
    } catch(error) {
      if(sequence===state.chartRequestSeq) {
        state.chartWindow=state.committedChartWindow?{...state.committedChartWindow}:null;
        updateChartMeta();updateExportAvailability();plotStatus(error.message,true);renderChart();
      }
      return false;
    } finally {
      if(sequence===state.chartRequestSeq){state.chartLoading=false;updateZoomControls();}
    }
  }
  function zoom(factor,focus=0.5) {
    const bounds=state.chartBounds,current=state.chartWindow;
    if(!bounds||!current)return;
    const total=bounds.end-bounds.start,currentSpan=current.end-current.start;
    if(total<=0||currentSpan<=0)return;
    const normalizedFocus=Math.max(0,Math.min(1,Number(focus)||0));
    const span=Math.min(total,Math.max(total/100000,currentSpan*factor));
    const pivot=current.start+currentSpan*normalizedFocus;
    let start=pivot-span*normalizedFocus,end=start+span;
    if(start<bounds.start){end+=bounds.start-start;start=bounds.start;}
    if(end>bounds.end){start-=end-bounds.end;end=bounds.end;}
    start=Math.max(bounds.start,start);end=Math.min(bounds.end,end);
    if(end<=start)return;
    loadChartWindow({apiStart:coordinateToApi(start,bounds.domain),apiEnd:coordinateToApi(end,bounds.domain),
      requestedWindow:{domain:bounds.domain,start,end}});
  }
  function resetChartWindow() {
    const bounds=state.chartBounds;
    if(!bounds)return;
    loadChartWindow({apiStart:coordinateToApi(bounds.start,bounds.domain),
      apiEnd:coordinateToApi(bounds.end,bounds.domain),requestedWindow:{...bounds}});
  }
  async function plot() {
    if(!state.selected.length){plotStatus('请先勾选至少一个逻辑参量。',true);return;}
    try {
      const range=validatedTimeRange();
      await loadChartWindow({apiStart:range.start||null,apiEnd:range.end||null,resetBounds:true,loadEvents:true});
    } catch(error){plotStatus(error.message,true);}
  }
  async function exportCsv() {
    if(state.exporting)return;
    const button=byId('v2Export');
    try {
      const range=validatedTimeRange();
      if(!updateExportAvailability())throw Error('请先勾选参量并选择有效的日期范围');
      const selection={role:role(),database:byId('v2Database').value,
        channel_ids:[...state.selected],start:range.start||null,end:range.end||null};
      state.exporting=true;button.disabled=true;
      byId('v2ExportStatus').textContent='正在查询完整数据并生成 CSV，请勿重复点击…';
      const response=await fetch('/api/v2/telemetry/export-selection',{
        method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(selection),
      });
      if(!response.ok){
        let reason=`HTTP ${response.status}`;
        try{const detail=await response.json();reason=detail.error||reason;}catch{}
        throw Error(reason);
      }
      const blob=await response.blob();
      const url=URL.createObjectURL(blob);
      try{
        const anchor=document.createElement('a');anchor.href=url;
        anchor.download='AAS_所选遥测参量_'+new Date().toISOString().slice(0,10)+'.csv';
        document.body.append(anchor);anchor.click();anchor.remove();
      }finally{setTimeout(()=>URL.revokeObjectURL(url),1000);}
      byId('v2ExportStatus').textContent='已准备下载：导出的 CSV 包含完整匹配数据及来源信息，不受预览采样上限影响。';
    }catch(error){byId('v2ExportStatus').textContent='导出失败：'+error.message;}
    finally{state.exporting=false;updateExportAvailability();}
  }
  const faultDialog=byId('v2FaultDialog');
  byId('v2OpenFaultFinder').addEventListener('click',()=>faultDialog.showModal());
  byId('v2CloseFaultFinder').addEventListener('click',()=>faultDialog.close());
  faultDialog.addEventListener('click',event=>{if(event.target===faultDialog)faultDialog.close();});
  byId('v2FindFaults').addEventListener('click',findFaults);
  byId('v2FindAssociations').addEventListener('click',findAssociations);
  byId('v2AssociationClass').addEventListener('change',renderAssociations);
  byId('v2FaultType').addEventListener('change',findFaults);
  byId('v2Search').addEventListener('click',()=>{selectedFiltersChanged();state.catalogPage.offset=0;updateFacets().then(search).catch(error=>status(error.message,true));});
  byId('v2PrevPage').addEventListener('click',()=>{state.catalogPage.offset=Math.max(0,state.catalogPage.offset-state.catalogPage.limit);search();});
  byId('v2NextPage').addEventListener('click',()=>{if(state.catalogPage.nextOffset!==null){state.catalogPage.offset=state.catalogPage.nextOffset;search();}});
  for(const [id,factor] of [['v2ZoomIn',.72],['v2ZoomOut',1.35]])byId(id).addEventListener('click',()=>zoom(factor));
  byId('v2ZoomReset').addEventListener('click',resetChartWindow);
  byId('v2DetailChannel').addEventListener('change',()=>{state.detail.channelId=byId('v2DetailChannel').value?Number(byId('v2DetailChannel').value):null;resetDetailState(true);});
  byId('v2LoadDetail').addEventListener('click',()=>loadDetailPage(null,{resetStack:true}));
  byId('v2DetailNext').addEventListener('click',()=>{const cursor=state.detail.page?.next_cursor;if(cursor)loadDetailPage(cursor,{pushCursor:true});});
  byId('v2DetailPrev').addEventListener('click',()=>{const index=state.detail.cursorStack.length-2;if(index>=0)loadDetailPage(state.detail.cursorStack[index],{replaceIndex:index});});
  byId('v2Database').addEventListener('change',()=>{state.faultData=null;state.associationData=null;byId('v2AssociationResults').replaceChildren();byId('v2AssociationStatus').textContent='筛选条件已变化，请重新分析。';byId('v2FaultResults').replaceChildren();byId('v2FaultStatus').textContent='数据库已切换，请重新查找故障。';selectedFiltersChanged();
    state.channelCache.clear();state.catalogPage.offset=0;state.selected=[];state.lines=[];state.chartBounds=null;state.chartWindow=null;state.committedChartWindow=null;state.hidden.clear();
    renderSelection();renderChart();updateTimeBounds();refreshCatalog();
  });
  async function initializeDatabases(){
    const result=await request('/api/v2/telemetry/databases');
    const selector=byId('v2Database');
    selector.replaceChildren(...(result.databases||[]).map(name=>new Option(name,name)));
    state.writeDatabase=result.default;
    if((result.databases||[]).includes(result.default))selector.value=result.default;
    if(!selector.value){status('datasets 目录中没有可用的 V2 数据库',true);return;}
    await refreshCatalog();
  }
  byId('v2Role').addEventListener('change',()=>{state.associationData=null;byId('v2AssociationResults').replaceChildren();selectedFiltersChanged();
    state.channelCache.clear();state.catalogPage.offset=0;state.selected=[];state.lines=[];state.hidden.clear();renderSelection();renderChart();
    updateTimeBounds();refreshCatalog();
  });
  for(const [,id] of KEYS)byId(id).addEventListener('change',()=>{
    state.faultData=null;state.associationData=null;byId('v2AssociationResults').replaceChildren();byId('v2AssociationStatus').textContent='筛选条件已变化，请重新分析。';byId('v2FaultResults').replaceChildren();byId('v2FaultStatus').textContent='筛选条件已变化，请重新查找故障。';
    selectedFiltersChanged();state.catalogPage.offset=0;updateFacets().then(search).catch(error=>status(error.message,true));});
  byId('v2Plot').addEventListener('click',plot);
  byId('v2ClearSelected').addEventListener('click',()=>{selectedFiltersChanged();state.selected=[];state.lines=[];state.hidden.clear();renderCandidates();renderSelection();renderChart();updateTimeBounds();});
  byId('v2Reset').addEventListener('click',()=>{selectedFiltersChanged();
    byId('v2Keyword').value='';byId('v2Start').value='';byId('v2End').value='';state.catalogPage.offset=0;
    for(const [,id] of KEYS)byId(id).value='';updateTimeBounds();updateFacets().then(search).catch(error=>status(error.message,true));
  });
  for(const id of DATE_FIELDS){
    const input=byId(id);input.disabled=true;input.addEventListener('input',()=>dateSelectionChanged(id));
    input.addEventListener('change',()=>dateSelectionChanged(id));
  }
  byId('v2Export').addEventListener('click',exportCsv);
  document.addEventListener('aas:v2-imported',()=>{state.associationData=null;selectedFiltersChanged();state.channelCache.clear();state.catalogPage.offset=0;state.selected=[];state.lines=[];state.chartBounds=null;state.chartWindow=null;state.committedChartWindow=null;state.hidden.clear();renderSelection();renderChart();refreshCatalog();});
  const dialog=byId('v2ImportDialog'),frame=byId('v2ImportFrame');
  byId('v2OpenImport').addEventListener('click',()=>{
    if(byId('v2Database').value!==state.writeDatabase){status('导入只能写入当前活动数据库 '+state.writeDatabase+'；请先在数据库列表切换到该文件。',true);return;}
    if(!frame.src)frame.src=frame.dataset.src;
    dialog.showModal();
  });
  byId('v2CloseImport').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
  window.addEventListener('message',event=>{if(event.origin!==window.location.origin||event.source!==frame.contentWindow)return;
    if(event.data?.type==='aas:v2-imported'){state.associationData=null;selectedFiltersChanged();
      if(event.data.keepOpen!==true)dialog.close();
      state.channelCache.clear();state.catalogPage.offset=0;state.selected=[];state.lines=[];state.chartBounds=null;state.chartWindow=null;state.committedChartWindow=null;state.hidden.clear();renderSelection();renderChart();refreshCatalog();}
    if(event.data?.type==='aas:v2-import-next'){dialog.close();byId('v2Keyword').focus();}
  });
  const sourceDialog=byId('v2SourceDialog');
  let sourceDeletePreview=null;
  function resetSourcePreview(){
    sourceDeletePreview=null;
    byId('v2SourceDeletePreview').textContent='';
    byId('v2SourceDeleteStatus').textContent='';
    byId('v2SourceConfirmName').value='';
    byId('v2CommitSourceDelete').disabled=true;
  }
  async function loadSourceManagement(){
    resetSourcePreview();
    const database=byId('v2Database').value;
    if(!database)throw Error('请先选择 V2 数据库');
    const result=await request('/api/v2/telemetry/sources?'+makeParams({database,limit:10000}));
    const picker=byId('v2DeleteSource');
    picker.replaceChildren(new Option('请选择数据源',''),...(result.items||[]).map(item=>
      new Option(`#${item.source_id} · ${item.source_name} · ${item.source_row_count} 源行`,String(item.source_id))));
    byId('v2SourceDeleteStatus').textContent=`当前数据库 ${database}：${(result.items||[]).length} 个数据源。`;
  }
  byId('v2ManageSources').addEventListener('click',async()=>{
    try{sourceDialog.showModal();await loadSourceManagement();}
    catch(error){byId('v2SourceDeleteStatus').textContent=error.message;}
  });
  byId('v2CloseSources').addEventListener('click',()=>sourceDialog.close());
  byId('v2DeleteSource').addEventListener('change',resetSourcePreview);
  byId('v2SourceConfirmName').addEventListener('input',()=>{
    byId('v2CommitSourceDelete').disabled=!sourceDeletePreview || !sourceDeletePreview.deletable ||
      byId('v2SourceConfirmName').value!==sourceDeletePreview.source_name;
  });
  byId('v2PreviewSourceDelete').addEventListener('click',async()=>{
    resetSourcePreview();
    const database=byId('v2Database').value,source_id=Number(byId('v2DeleteSource').value);
    if(!source_id){byId('v2SourceDeleteStatus').textContent='请先选择数据源';return;}
    try{
      const result=await request('/api/v2/telemetry/source/delete/preview',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({database,source_id})});
      sourceDeletePreview=result;
      const blockers=(result.blockers||[]).length?JSON.stringify(result.blockers,null,2):'无';
      byId('v2SourceDeletePreview').textContent=`数据源：${result.source_name}\n来源 ID：${result.source_id}\n分片：${result.segments}\n采样：${result.samples}\n已发布模型或派生数据引用：${blockers}`;
      byId('v2SourceDeleteStatus').textContent=result.deletable?'预检通过；输入完整数据源名称后可删除。':'存在引用，禁止删除。';
    }catch(error){byId('v2SourceDeleteStatus').textContent=error.message;}
  });
  byId('v2CommitSourceDelete').addEventListener('click',async()=>{
    const item=sourceDeletePreview;
    if(!item || !item.deletable || byId('v2SourceConfirmName').value!==item.source_name ||
       byId('v2Database').value!==item.database)return;
    if(!window.confirm(`永久删除数据源“${item.source_name}”及其 ${item.samples} 条采样？`))return;
    const button=byId('v2CommitSourceDelete');button.disabled=true;
    try{
      await request('/api/v2/telemetry/source/delete/commit',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
          database:item.database,source_id:item.source_id,preview_token:item.preview_token,
          confirm_name:item.source_name,confirm:true})});
      await loadSourceManagement();
      byId('v2SourceDeleteStatus').textContent=`已删除数据源“${item.source_name}”。`;
      selectedFiltersChanged();state.channelCache.clear();state.catalogPage.offset=0;
      state.selected=[];state.lines=[];state.hidden.clear();renderSelection();renderChart();
      await refreshCatalog();
    }catch(error){byId('v2SourceDeleteStatus').textContent=`删除失败：${error.message}`;}
  });
  initializeDatabases().catch(error=>status(error.message,true));
})();
