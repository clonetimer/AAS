/* ============================================================
   智能故障检测算法评估系统外壳逻辑
   - 左侧栏导航 + 内容区 iframe
   - 主题（浅色 / 深色）持久化，并同步到 iframe 内的样式表
   - 支持 URL hash 深链（如 #runner）
   ============================================================ */
(function () {
  'use strict';

  var MODULES = [
    {
      key: 'browser', group: '数据准备', label: '历史故障数据库', href: 'browser.html',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18"/></svg>'
    },
    { key: 'simulation', group: '数据准备', label: '模拟故障数据库', href: 'simulation.html', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2v5m0 10v5M2 12h5m10 0h5"/><circle cx="12" cy="12" r="5"/></svg>' },
    { key: 'preprocess', group: '数据准备', label: '数据预处理', href: 'preprocess.html', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7h18M3 17h18M8 3v8m8 2v8"/></svg>' },
    {
      key: 'runner', group: '算法评估', label: '智能检测算法评估', href: 'runner.html',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M10 8l6 4-6 4V8z" fill="currentColor" stroke="none"/></svg>'
    },
    {
      key: 'reports', group: '结果与管理', label: '任务查询与导出', href: 'reports.html',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6M9 13h6M9 17h6"/></svg>'
    },
    {
      key: 'plugins', group: '结果与管理', label: '检测算法管理', href: 'plugins.html',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3v2H6a2 2 0 0 0-2 2v3h2M15 3v2h3a2 2 0 0 1 2 2v3h-2M9 21v-2H6a2 2 0 0 1-2-2v-3h2M15 21v-2h3a2 2 0 0 0 2-2v-3h-2"/><circle cx="12" cy="12" r="2.5"/></svg>'
    },
    {
      key: 'monitor', group: '结果与管理', label: '软件性能监控', href: 'monitor.html',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2 6 4-14 2 8h6"/></svg>'
    }
  ];

  var app = document.getElementById('app');
  var nav = document.getElementById('nav');
  var iframe = document.getElementById('workspace');
  var crumbCurrent = document.getElementById('crumbCurrent');
  var openNew = document.getElementById('openNew');
  var themeToggle = document.getElementById('themeToggle');
  var themeLabel = themeToggle.querySelector('.wb-theme-label');
  var themeIcon = themeToggle.querySelector('.wb-theme-icon');
  var collapseBtn = document.getElementById('collapseBtn');

  var navEls = [];
  var currentKey = null;
  var isDark = false;

  var currentGroup = null;
  MODULES.forEach(function (m) {
    if (m.group !== currentGroup) {
      currentGroup = m.group;
      var sec = document.createElement('div');
      sec.className = 'wb-nav-section';
      sec.textContent = currentGroup;
      nav.appendChild(sec);
    }
    var a = document.createElement('a');
    a.className = 'wb-nav-item';
    a.href = m.href;
    a.dataset.key = m.key;
    a.innerHTML = '<span class="wb-nav-icon">' + m.icon + '</span><span class="wb-nav-label">' + m.label + '</span>';
    a.addEventListener('click', function (e) {
      e.preventDefault();
      activate(m.key);
    });
    nav.appendChild(a);
    navEls.push(a);
  });

  function checkDatabase() {
    var el=document.getElementById('wbDbStatus');if(!el)return;
    el.textContent='● 正在检查数据源';el.style.color='';el.title='点击可重新检查';
    fetch('/api/v2/database/status',{cache:'no-store',credentials:'same-origin'})
      .then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})
      .then(function(v){var annotated=v.has_run_annotations!==false;
        el.textContent=v.connected?(annotated?'● 数据源已连接':'● 数据源已连接（未标注）'):'● 数据源未连接';
        el.title=(v.connected?(annotated?'当前数据源：'+v.database:
          '当前数据源：'+v.database+'，该库缺少运行标注，模拟注入、预处理、训练评估都不会有参量可选'):'请检查服务端的数据源配置')+'；点击可重新检查';
        el.style.color=v.connected?(annotated?'':'#b8860b'):'#bf4f4f';})
      .catch(function(error){
        el.textContent='● 数据源状态未知（点击重试）';
        el.title='无法读取数据源状态：'+((error&&error.message)||'网络错误')+'；点击可重新检查';
        el.style.color='#bf4f4f';});
  }
  var dbStatusEl=document.getElementById('wbDbStatus');
  if(dbStatusEl){
    dbStatusEl.style.cursor='pointer';
    dbStatusEl.addEventListener('click',checkDatabase);
    dbStatusEl.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();checkDatabase();}});
  }
  checkDatabase();

  var THEME_TARGET={dark:'styles.css',light:'styles-light.css'};

  // 给目标文档打上主题标记，并按需挂载深色覆盖补丁（内联样式里写死的浅色）。
  function syncDocTheme(doc, dark) {
    try { doc.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light'); } catch (e) { return; }
    var existing=doc.getElementById('wb-dark-overrides');
    if (dark && !existing) {
      var link=doc.createElement('link');
      link.id='wb-dark-overrides';link.rel='stylesheet';link.href='theme-dark-overrides.css';
      (doc.head||doc.documentElement).appendChild(link);
    } else if (!dark && existing) {
      existing.parentNode.removeChild(existing);
    }
  }

  // 就地替换主题文件名，保留 ?layout_compact=1&intervals=2 之类的版式参数。
  function applyIframeTheme(doc, dark) {
    if (!doc) return;
    syncDocTheme(doc,dark);
    var target=THEME_TARGET[dark?'dark':'light'];
    var links=doc.querySelectorAll('link[rel="stylesheet"]');
    for (var i=0;i<links.length;i++){
      var href=links[i].getAttribute('href')||'';
      var match=/(^|\/)(styles(-light)?\.css)(\?.*)?$/i.exec(href);
      if (!match) continue;
      if (match[2]===target) continue;
      links[i].setAttribute('href',match[1]+target+(match[4]||''));
    }
    // 同源嵌套 iframe（如历史数据库页里延迟加载的导入弹窗）也要跟随主题
    var frames=doc.querySelectorAll('iframe');
    for (var j=0;j<frames.length;j++){
      var frame=frames[j];
      if (frame.dataset.themeBound!=='1') {
        frame.dataset.themeBound='1';
        frame.addEventListener('load',function(){
          applyIframeTheme(this.contentDocument,isDark);
          decorateIframe(this.contentDocument);
        });
      }
      var child=null;
      try { child=frame.contentDocument; } catch (e) { child=null; }
      // about:blank 说明真实页面还没加载，等 load 事件再处理
      if (child && child.location && child.location.href!=='about:blank') {
        applyIframeTheme(child,dark);
      }
    }
  }

  // 工作台本身已经提供导航和模块标题。嵌入页只保留真正的模块操作，避免
  // “外层面包屑 + 内层大标题 + 内层导航”三重层级造成重复和视觉噪声。
  function decorateIframe(doc) {
    if (!doc) return;
    if (!doc.getElementById('wb-injected-style')) {
      var st = doc.createElement('style');
      st.id = 'wb-injected-style';
      st.textContent =
        '.nav-links{display:none !important;}' +
        '.ambient{display:none !important;}' +
        '.shell{width:100% !important;max-width:none !important;margin:0 !important;padding:18px 20px 32px !important;}' +
        '.topbar>div:first-child{display:none !important;}' +
        '.topbar.wb-empty-module-header{display:none !important;}' +
        '.topbar.wb-module-actions{display:flex !important;flex-direction:row !important;align-items:center !important;justify-content:flex-end !important;flex-wrap:wrap !important;gap:8px !important;min-height:0 !important;padding:0 0 10px !important;margin:0 !important;border:0 !important;border-radius:0 !important;background:transparent !important;box-shadow:none !important;}' +
        '.pg{max-width:none !important;padding:18px 20px 48px !important;}' +
        '.pg-head{display:none !important;}';
      (doc.head || doc.documentElement).appendChild(st);
    }

    var topbar = doc.querySelector('.topbar');
    if (topbar) {
      var hasActions = !!topbar.querySelector('button, .icon-btn');
      topbar.classList.toggle('wb-module-actions', hasActions);
      topbar.classList.toggle('wb-empty-module-header', !hasActions);
    }
  }

  function setTheme(dark) {
    isDark = dark;
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    try { localStorage.setItem('wb-theme', dark ? 'dark' : 'light'); } catch (e) {}
    applyIframeTheme(iframe.contentDocument, dark);
    themeLabel.textContent = dark ? '浅色' : '深色';
    themeIcon.textContent = dark ? '☀' : '🌙';
  }

  function activate(key, push) {
    var mod = null;
    for (var i = 0; i < MODULES.length; i++) {
      if (MODULES[i].key === key) { mod = MODULES[i]; break; }
    }
    if (!mod) mod = MODULES[0];

    crumbCurrent.textContent = mod.label;
    openNew.href = mod.href;
    navEls.forEach(function (el) {
      el.classList.toggle('active', el.dataset.key === mod.key);
    });
    if (currentKey !== mod.key) {
      currentKey = mod.key;
      iframe.src = mod.href;
    }
    if (push !== false && (location.hash || '').slice(1) !== mod.key) {
      location.hash = mod.key;
    }
  }

  // 主题初始化
  var savedTheme = 'light';
  try { savedTheme = localStorage.getItem('wb-theme') || 'light'; } catch (e) {}
  setTheme(savedTheme === 'dark');

  // 侧栏收起（持久化）
  try {
    if (localStorage.getItem('wb-collapsed') === '1') app.classList.add('wb-collapsed');
  } catch (e) {}
  collapseBtn.addEventListener('click', function () {
    app.classList.toggle('wb-collapsed');
    try { localStorage.setItem('wb-collapsed', app.classList.contains('wb-collapsed') ? '1' : '0'); } catch (e) {}
  });

  // 主题切换
  themeToggle.addEventListener('click', function () { setTheme(!isDark); });

  // 每次 iframe 加载完成后同步主题并隐藏自带菜单
  iframe.addEventListener('load', function () {
    applyIframeTheme(iframe.contentDocument, isDark);
    decorateIframe(iframe.contentDocument);
  });

  // hash 路由
  window.addEventListener('hashchange', function () {
    activate((location.hash || '').slice(1) || MODULES[0].key, false);
  });

  document.getElementById('wbLogout').addEventListener('click', async function () {
    try {
      await fetch('/api/auth/logout', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: '{}'});
    } finally { location.replace('/'); }
  });

  // Verify actual server session before loading any embedded work module.
  fetch('/api/auth/status', {cache: 'no-store', credentials: 'same-origin'})
    .then(function (response) { return response.json(); })
    .then(function (status) {
      if (!status.authenticated) { location.replace('/'); return; }
      var initial = (location.hash || '').slice(1);
      var valid = MODULES.some(function (m) { return m.key === initial; });
      activate(valid ? initial : MODULES[0].key, false);
    })
    .catch(function () { location.replace('/'); });
})();
