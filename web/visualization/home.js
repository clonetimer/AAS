(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var initialized = false;
  var authenticated = false;
  var setupMode = false;
  function nowClock() {
    var date = new Date();
    $('timeText').textContent = date.toLocaleTimeString('zh-CN', {hour12: false});
    $('dateText').textContent = date.toLocaleDateString('zh-CN', {year:'numeric', month:'2-digit', day:'2-digit', weekday:'long'});
  }
  nowClock(); setInterval(nowClock, 1000);
  async function api(url, options) {
    var response = await fetch(url, Object.assign({credentials:'same-origin',cache:'no-store'}, options || {}));
    var data = await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败');
    return data;
  }
  function text(id, value) { $(id).textContent = String(value); }
  function showLogin() { $('loginUser').focus(); $('loginPanel').scrollIntoView({behavior:'smooth',block:'center'}); }
  $('headerAction').addEventListener('click', function () { if (authenticated) location.href='/index.html'; else showLogin(); });
  $('heroEnter').addEventListener('click', function (event) { if (!authenticated) { event.preventDefault(); showLogin(); } });
  document.querySelectorAll('.module-card').forEach(function (link) { link.addEventListener('click', function (event) { if (!authenticated) { event.preventDefault(); showLogin(); } }); });
  $('passwordToggle').addEventListener('click', function () {
    var input=$('loginPassword');input.type=input.type==='password'?'text':'password';
    this.setAttribute('aria-label', input.type==='password'?'显示密码':'隐藏密码');
  });
  function renderAccount(status) {
    initialized=!!status.initialized; authenticated=!!status.authenticated; setupMode=!initialized;
    $('authForm').hidden=authenticated;
    $('signedPanel').hidden=!authenticated;
    $('headerAction').innerHTML=authenticated?'进入工作台 <span>↗</span>':'登录系统 <span>↗</span>';
    $('accessTitle').textContent=authenticated?'登录成功':setupMode?'初始化管理员':'欢迎使用系统';
    $('accessDesc').textContent=authenticated?'系统已验证您的身份，可以访问所有工作模块。':setupMode?'首次启动请在本机设置管理员账号和密码。':'使用管理员账号登录，查看数据资产与任务概况。';
    $('loginSubmit').innerHTML=setupMode?'创建管理员账号 <span>→</span>':'登录并进入系统 <span>→</span>';
    $('loginPassword').setAttribute('autocomplete',setupMode?'new-password':'current-password');
    if (authenticated) text('signedUser',status.username||'管理员');
    if (!authenticated) {
      text('lastSync','等待登录以加载数据');
      ['countDatabases','countTables','countModels','countRunning','taskTotal','taskRunning','taskCompleted','taskFailed','taskCancelled','taskInterrupted','taskAttention'].forEach(function(id){text(id,'—');});
      text('taskScopeNote','登录后查看持久化任务历史。');
      text('countClean','等待加载');text('countPublished','等待加载');text('countCompleted','等待加载');
      $('databaseChart').innerHTML='<div class="empty-message">登录后查看逻辑参量分布</div>';
      $('taskRing').style.background='conic-gradient(#365364 0 100%)';
    }
  }
  $('authForm').addEventListener('submit', async function (event) {
    event.preventDefault();
    text('formError','');
    var username=$('loginUser').value.trim(),password=$('loginPassword').value;
    var button=$('loginSubmit');button.disabled=true;
    try {
      var body=JSON.stringify({username:username,password:password});
      if (setupMode) await api('/api/auth/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:body});
      await api('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:body});
      $('loginPassword').value='';
      renderAccount(await api('/api/auth/status'));
      await refreshSummary();
    } catch (error) { text('formError',error.message); }
    finally { button.disabled=false; }
  });
  $('logoutBtn').addEventListener('click',async function(){
    text('formError','');
    try {
      await api('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      renderAccount({initialized:true,authenticated:false});
    } catch (error) { text('formError','退出失败：'+error.message); }
  });
  function renderBars(items) {
    var target=$('databaseChart');target.replaceChildren();
    var list=items.slice(0,5);
    if (!list.length) {target.innerHTML='<div class="empty-message">暂无逻辑参量</div>';return;}
    var max=Math.max(1,...list.map(function (db) {return db.tables;}));
    list.forEach(function(db){
      var row=document.createElement('div');row.className='bar-row';
      var label=document.createElement('span');label.className='bar-label';label.textContent=db.name;label.title=db.name;
      var bar=document.createElement('div');bar.className='bar-bg';
      var fill=document.createElement('div');fill.className='bar-fill';fill.style.width=(db.tables/max*100)+'%';bar.appendChild(fill);
      var n=document.createElement('span');n.className='bar-value';n.textContent=db.tables;
      row.append(label,bar,n);target.appendChild(row);
    });
  }
  async function refreshTaskSummary(){
    var ids=['taskTotal','taskRunning','taskCompleted','taskFailed','taskCancelled','taskInterrupted','taskAttention'];
    try {
      var payload=await api('/api/task-history?limit=50&include_all=1');
      var counts=payload.summary&&payload.summary.counts;
      if(!counts||payload.summary.scope!=='latest_50')throw new Error('任务统计摘要版本不兼容');
      var total=Number(payload.summary.sample_count)||0;
      text('countRunning',counts.running);
      text('taskTotal',total);text('taskRunning',counts.running);text('taskCompleted',counts.completed);
      text('taskFailed',counts.failed);text('taskCancelled',counts.cancelled);
      text('taskInterrupted',counts.interrupted);text('taskAttention',counts.attention);
      var running=total?counts.running/total*100:0;
      var completed=total?counts.completed/total*100:0;
      var failed=total?counts.failed/total*100:0;
      $('taskRing').style.background=total?'conic-gradient(#70dddc 0 '+running+'%,#7d98ee '+running+'% '+(running+completed)+'%,#e6909f '+(running+completed+failed)+'%,#a3a3a3 '+(running+completed+failed)+'% 100%)':'conic-gradient(#344b60 0 100%)';
      text('taskScopeNote','统计最近 '+total+' 项任务（全部历史 '+Number(payload.total||0)+' 项）；报告待处理属于已完成任务子集，不重复计数。');
    } catch(error) {
      ids.forEach(function(id){text(id,'—');});
      text('countRunning','—');
      $('taskRing').style.background='conic-gradient(#344b60 0 100%)';
      text('taskScopeNote','任务历史暂不可用，统计未更新；请到“任务查询与导出”重试。');
    }
  }
  async function refreshSummary(){
    if (!authenticated)return;
    try{
      var d=await api('/api/home/summary');
      text('countDatabases',d.databases);text('countTables',d.tables);text('countModels',d.models);
      text('countClean','预处理分片 '+d.clean_tables+' · 故障分片 '+d.fault_tables);
      text('countPublished','已发布 '+d.published_models);text('countCompleted','持久化任务历史');
      await refreshTaskSummary();
      renderBars(d.database_items||[]);
      text('chartFoot','按 V2 分系统统计');
      text('lastSync','已更新 · '+new Date().toLocaleTimeString('zh-CN',{hour12:false}));
    }catch(error){
      if(error.message==='需要登录') {renderAccount({initialized:true,authenticated:false});return;}
      text('lastSync','数据加载失败 · '+error.message);
      text('taskScopeNote','资产数据暂不可用；任务历史可单独重试。');
    }
  }
  (async function init(){
    try {var state=await api('/api/auth/status');renderAccount(state);await refreshSummary();}
    catch(error){text('formError','无法连接到服务器：'+error.message);}
  })();
  // 后台标签页不再空转请求；回到前台立即补一次，并在卸载时清掉定时器。
  var summaryTimer=setInterval(function(){if(!document.hidden)refreshSummary();},30000);
  document.addEventListener('visibilitychange',function(){if(!document.hidden)refreshSummary();});
  window.addEventListener('pagehide',function(){clearInterval(summaryTimer);});
})();
