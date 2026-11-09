const els = {
  experimentList: document.getElementById('experimentList'),
  detailMeta: document.getElementById('detailMeta'),
  detailTable: document.getElementById('detailTable'),
  statsOverview: document.getElementById('statsOverview'),
  viewSwitcher: document.getElementById('viewSwitcher'),
  tableContainer: document.getElementById('tableContainer'),
  cardsContainer: document.getElementById('cardsContainer'),
  statTotal: document.getElementById('statTotal'),
  statSuccess: document.getElementById('statSuccess'),
  statError: document.getElementById('statError'),
  statAccuracy: document.getElementById('statAccuracy'),
  exportBtn: document.getElementById('exportBtn'),
  experimentTypeTabBtns: document.querySelectorAll('.experiment-type-tab-btn'),
  reportSearch: document.getElementById('reportSearch'),
  reportSearchClear: document.getElementById('reportSearchClear'),
  reportStatus: document.getElementById('reportStatus'),
  reportSearchCount: document.getElementById('reportSearchCount'),
  compareLeft: document.getElementById('compareLeft'),
  compareRight: document.getElementById('compareRight'),
  compareBtn: document.getElementById('compareBtn'),
  compareResult: document.getElementById('compareResult'),
  exportComparisonBtn: document.getElementById('exportComparisonG1Btn'),
  taskHistoryList: document.getElementById('taskHistoryList'),
  taskHistoryCount: document.getElementById('taskHistoryCount'),
  refreshTaskHistory: document.getElementById('refreshTaskHistory'),
};

const state = {
  experiments: [],
  selectedPath: null,
  currentView: 'table',
  currentData: null,
  selectedExperimentType: 'all',
  searchText: '',
  statusFilter: 'all',
  selectedTrace: null,
  listSequence: 0,
  detailsSequence: 0,
  taskHistoryTimer: null,
  lastComparison: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function showErrorState(container, message, retryFn) {
  if (!container) return;
  container.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'state-error';
  const msg = document.createElement('span');
  msg.className = 'state-error-msg';
  msg.textContent = message;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'retry-btn';
  btn.textContent = '重试';
  wrap.appendChild(msg);
  wrap.appendChild(btn);
  container.appendChild(wrap);
  if (retryFn) btn.addEventListener('click', () => retryFn());
}

function taskStatusLabel(item) {
  const status = item?.status;
  if (status === 'running') return '运行中';
  if (status === 'cancelling') return '正在取消';
  if (status === 'completed') return item.attention ? '已完成 · 报告待处理' : '已完成';
  if (status === 'failed') return '执行失败';
  if (status === 'cancelled') return '已取消';
  if (status === 'interrupted') return '已中断';
  return '状态未知';
}

function taskWorkflow(item) {
  if (item?.task_type === 'evaluation' || item?.task_type === 'score_only') {
    return {href:'runner.html?tab=eval', label:'返回评估工作区'};
  }
  if (item?.task_type === 'batch_import') {
    return {href:'import.html#v2BatchPanel', label:'查看批量导入工作区'};
  }
  if (item?.task_type === 'csv_import') {
    return {href:'runner.html', label:'返回 CSV 导入工作区'};
  }
  if (item?.task_type === 'preprocess') {
    return {href:'browser.html', label:'返回数据浏览工作区'};
  }
  return {href:'runner.html?tab=train', label:'返回训练工作区'};
}

function renderTaskHistory(items) {
  const host = els.taskHistoryList;
  if (!host || typeof host.replaceChildren !== 'function') return;
  host.replaceChildren();
  const rows = Array.isArray(items) ? items : [];
  if (els.taskHistoryCount) els.taskHistoryCount.textContent = `${rows.length} 项`;
  if (!rows.length) {
    const empty = document.createElement('p');
    empty.className = 'task-history-empty';
    empty.textContent = '暂无任务记录';
    host.append(empty);
    return;
  }
  for (const item of rows) {
    const diagnostic = item?.failure || item?.attention || {};
    const card = document.createElement('article');
    card.className = 'task-history-card';
    const head = document.createElement('div');
    head.className = 'task-history-card-head';
    const title = document.createElement('h3');
    title.textContent = diagnostic.title || (item.task_type === 'training' ? '训练任务' :
      item.task_type === 'evaluation' ? '监督评估任务' :
      item.task_type === 'score_only' ? '无标签推理任务' :
      item.task_type === 'batch_import' ? '批量导入任务' :
      item.task_type === 'csv_import' ? 'CSV 导入任务' :
      item.task_type === 'preprocess' ? '数据预处理任务' : '后台任务');
    const status = document.createElement('span');
    status.className = `task-history-status task-history-status-${item.status || 'unknown'}`;
    status.textContent = taskStatusLabel(item);
    head.append(title, status);

    const progress = document.createElement('progress');
    progress.className = 'task-history-progress';
    progress.max = 100;
    progress.value = Math.min(100, Math.max(0, Number(item.progress) || 0));
    progress.setAttribute('aria-label', `任务进度 ${progress.value}%`);

    const stage = document.createElement('p');
    stage.className = 'task-history-stage';
    stage.textContent = item.stage_label || diagnostic.stage_label || '任务执行';
    const message = document.createElement('p');
    message.className = 'task-history-message';
    message.textContent = diagnostic.message || item.message || '暂无任务说明。';
    const action = document.createElement('p');
    action.className = 'task-history-action';
    action.textContent = diagnostic.recovery_action || (item.status === 'interrupted'
      ? '服务重启后任务不会自动重放，请检查数据后手动重新提交。'
      : item.status === 'cancelled' ? '任务已停止；如需再次运行，请返回工作区重新设置并提交。' : '');
    const meta = document.createElement('small');
    meta.textContent = `任务编号：${String(item.task_id || '未知').slice(0, 40)} · ${item.task_type || 'unknown'}`;
    const actions = document.createElement('div');
    actions.className = 'task-history-actions';
    const workflow = taskWorkflow(item);
    const link = document.createElement('a');
    link.href = workflow.href;
    link.textContent = diagnostic.recovery_label || workflow.label;
    actions.append(link);

    if (item.status === 'running' && item.cancellable === true) {
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'task-history-cancel';
      cancel.textContent = '取消任务';
      cancel.addEventListener('click', async () => {
        cancel.disabled = true;
        try {
          if (!window.AASTasks?.cancel) throw new Error('任务控制模块尚未加载');
          const result = await window.AASTasks.cancel(item.task_id);
          if (result.cancelled_by_user) {
            cancel.disabled = false;
            return;
          }
          if (result.accepted) message.textContent = '已请求取消，正在等待当前安全步骤结束…';
          else {
            message.textContent = result.reason || '当前阶段不能取消';
            cancel.disabled = false;
          }
          await loadTaskHistory();
        } catch (error) {
          message.textContent = `取消请求失败：${error.message}`;
          cancel.disabled = false;
        }
      });
      actions.append(cancel);
    } else if (item.status === 'cancelling') {
      const pending = document.createElement('small');
      pending.className = 'task-history-cancel-pending';
      pending.textContent = '取消请求已提交，等待安全停止';
      actions.append(pending);
    }

    card.append(head, progress, stage, message);
    if (action.textContent) card.append(action);
    card.append(meta, actions);
    host.append(card);
  }
}

async function loadTaskHistory() {
  const host = els.taskHistoryList;
  if (!host || typeof host.replaceChildren !== 'function') return;
  if (state.taskHistoryTimer) window.clearTimeout(state.taskHistoryTimer);
  state.taskHistoryTimer = null;
  host.replaceChildren();
  const loading = document.createElement('p');
  loading.className = 'task-history-loading';
  loading.textContent = '正在读取任务历史…';
  host.append(loading);
  if (els.taskHistoryCount) els.taskHistoryCount.textContent = '读取中';
  try {
    const payload = window.AASTasks?.history
      ? await window.AASTasks.history(50)
      : await api('/api/task-history?limit=50&include_all=1');
    const items = payload.items || [];
    renderTaskHistory(items);
    const summary = payload.summary;
    const summaryNode = typeof document.getElementById === 'function' ? document.getElementById('taskHistorySummary') : null;
    if (summaryNode) {
      if (summary && summary.scope === 'latest_50' && summary.counts) {
        const c = summary.counts;
        summaryNode.textContent = `最近 ${summary.sample_count} 项：运行中 ${c.running} · 已完成 ${c.completed} · 失败 ${c.failed} · 已取消 ${c.cancelled} · 中断 ${c.interrupted} · 报告待处理 ${c.attention}（已完成子集，不重复计数）；全部历史 ${Number(payload.total) || 0} 项。`;
      } else {
        summaryNode.textContent = '任务统计摘要暂不可用；任务列表仍可查看。';
      }
    }
    if (els.taskHistoryCount) els.taskHistoryCount.textContent = `${Number(payload.total) || items.length} 项`;
    if (items.some(item => item.status === 'running' || item.status === 'cancelling')) {
      state.taskHistoryTimer = window.setTimeout(loadTaskHistory, 1500);
    }
  } catch (error) {
    host.replaceChildren();
    const message = document.createElement('p');
    message.className = 'task-history-error';
    message.textContent = `任务历史暂时不可用：${error.message}`;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = '重试';
    retry.addEventListener('click', loadTaskHistory);
    host.append(message, retry);
    if (els.taskHistoryCount) els.taskHistoryCount.textContent = '读取失败';
  }
}

// 非阻断提示：替代 alert()，不打断当前页面与已填表单。
function notify(message, kind = 'info') {
  let host = document.getElementById('aasToastHost');
  if (!host) {
    host = document.createElement('div');
    host.id = 'aasToastHost';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    host.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:min(420px,90vw)';
    document.body.appendChild(host);
  }
  const colors = kind === 'error' ? ['#fef2f2', '#dc2626']
    : kind === 'success' ? ['#f0fdf4', '#16a34a']
    : ['#f8fafc', '#0891b2'];
  const item = document.createElement('div');
  item.style.cssText = 'background:' + colors[0] + ';color:#1e293b;border:1px solid ' + colors[1]
    + '55;border-left:4px solid ' + colors[1]
    + ';border-radius:10px;padding:10px 14px;font-size:13px;line-height:1.5;box-shadow:0 8px 24px rgba(15,23,42,.14);cursor:pointer';
  item.textContent = message;
  item.title = '点击关闭';
  item.addEventListener('click', () => item.remove());
  host.appendChild(item);
  window.setTimeout(() => item.remove(), kind === 'error' ? 8000 : 4500);
  return item;
}

function showSkeleton(container, count = 2) {
  if (!container) return;
  let html = '';
  for (let i = 0; i < count; i++) {
    html += `<div class="history-skeleton">
      <div class="skeleton sk-line" style="width: 40%"></div>
      <div class="skeleton sk-line" style="width: 70%"></div>
      <div class="skeleton sk-line" style="width: 55%"></div>
    </div>`;
  }
  container.innerHTML = html;
}


function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function getFilteredExperiments() {
  const query = state.searchText.trim().toLocaleLowerCase();
  return state.experiments.filter(item => {
    const count = Number(item.success_count || 0);
    const total = Number(item.result_count || 0);
    const isSuccess = total > 0 && count === total;
    if (state.statusFilter === 'success' && !isSuccess) return false;
    if (state.statusFilter === 'error' && isSuccess) return false;
    if (!query) return true;
    const terms = [item.experiment_id, item.algorithm, item.model_name,
      item.model_algorithm, item.table, item.eval_table, item.dataset,
      item.eval_dataset, item.type, ...(item.detectors || []), ...(item.feature_keys || []), item.path];
    return terms.some(term => String(term ?? '').toLocaleLowerCase().includes(query));
  });
}

function clearSelection(message = '请选择任务查看结果') {
  state.selectedPath = null;
  state.currentData = null;
  state.selectedTrace = null;
  state.detailsSequence += 1;
  if (els.detailMeta) els.detailMeta.textContent = message;
  if (els.detailTable) els.detailTable.innerHTML = '';
  if (els.cardsContainer) els.cardsContainer.innerHTML = '';
  if (els.statsOverview) els.statsOverview.style.display = 'none';
  if (els.viewSwitcher) els.viewSwitcher.style.display = 'none';
}

function renderExperiments() {
  const visible = getFilteredExperiments();
  if (els.reportSearchCount) els.reportSearchCount.textContent = `找到 ${visible.length} / ${state.experiments.length} 项`;
  if (!visible.length) {
    if (els.experimentList) els.experimentList.innerHTML = '<div class="empty-state">暂无实验记录。请先在运行页执行一次评估。</div>';
    if (els.detailMeta) els.detailMeta.textContent = '暂无数据';
    if (els.detailTable) els.detailTable.innerHTML = '';
    if (els.statsOverview) els.statsOverview.style.display = 'none';
    if (els.viewSwitcher) els.viewSwitcher.style.display = 'none';
    return;
  }
  els.experimentList.innerHTML = visible.map((item) => {
    // 根据实验类型显示不同的元数据
    let metaLines = '';
    let badge = '';
    
    if (item.type === 'evaluation') {
      // 评估实验：显示模型和数据表
      const scoreOnly = item.evaluation_mode === 'score_only';
      badge = (scoreOnly ? '<span class="badge badge-model">仅推理（无真实标签）</span>' : '') +
        (item.model_name ? `<span class="badge badge-model">${escapeHtml(item.model_name)}</span>` : '');
      metaLines = `
        <div class="meta-line">数据源: ${escapeHtml(item.eval_dataset || item.dataset || 'V2 遥测')}</div>
        <div class="meta-line">执行结果: ${item.success_count || 0} / ${item.result_count || 0} 成功</div>
        <div class="meta-line">${scoreOnly ? '监督指标: 不适用（真值未知）' : '准确率: ' + (Number.isFinite(item.metrics_avg?.accuracy) ? (item.metrics_avg.accuracy * 100).toFixed(2) + '%' : '未计算')}</div>
      `;
    } else {
      // 训练实验：显示算法、数据表、发布状态
      badge = item.model_published ? `<span class="badge badge-published">已发布</span>` : '';
      metaLines = `
        <div class="meta-line">算法: ${escapeHtml(item.algorithm || '未知')}</div>
        <div class="meta-line">数据源: ${escapeHtml(item.dataset || 'V2 遥测')}</div>
        <div class="meta-line">执行结果: ${item.success_count || 0} / ${item.result_count || 0} 成功</div>
        <div class="meta-line">准确率: ${Number.isFinite(item.metrics_avg?.accuracy) ? (item.metrics_avg.accuracy * 100).toFixed(2) + '%' : '未计算'}</div>
      `;
    }
    
    const publishAction = item.type === 'training' && !item.model_published
      ? (item.model_available
        ? `<button type="button" class="summary-publish-btn" data-publish-path="${escapeHtml(item.path)}">发布模型</button>`
        : `<span class="summary-model-missing">模型文件不可用</span>`)
      : '';
    return `
    <div class="summary-card-wrapper ${state.selectedPath === item.path ? 'active' : ''}">
      <button class="summary-card" data-path="${escapeHtml(item.path)}">
        <strong>${escapeHtml(item.experiment_id)}</strong>
        ${badge}
        ${metaLines}
      </button>
      ${publishAction}
    </div>
  `}).join('');
  
  els.experimentList.querySelectorAll('.summary-card').forEach((button) => {
    button.addEventListener('click', () => loadDetails(button.dataset.path));
  });
  els.experimentList.querySelectorAll('.summary-publish-btn').forEach((button) => {
    button.addEventListener('click', async (event) => {
      event.stopPropagation();
      const exp = state.experiments.find((item) => item.path === button.dataset.publishPath);
      if (exp) await publishHistoricalTraining(exp);
    });
  });
}

async function publishHistoricalTraining(exp) {
  // 发布走 POST /api/models（与训练页一致）。旧的 /api/experiments/publish-model 已退役为 410。
  // 后端契约（post_models.handle_model_post）要求 experiment_id / model_name / algorithm，
  // 且 dataset 必须等于当前活动库、table_name 必须是 v2_channels、file_path 必须是已存在的模型目录。
  if (!exp.model_available) {
    notify('该实验没有可发布的模型产物：需先在训练页完成训练并生成模型目录', 'error');
    return;
  }
  const missing = ['algorithm', 'dataset', 'model_dir'].filter((key) => !exp[key]);
  if (missing.length) {
    notify(`实验记录缺少发布所需字段：${missing.join('、')}；请在训练页发布`, 'error');
    return;
  }
  const defaultName = `${exp.algorithm}-${exp.model_artifact_experiment_id || exp.experiment_id}`;
  const modelName = window.prompt('请输入模型名称', defaultName);
  if (modelName === null) return;
  if (!modelName.trim()) {
    notify('模型名称不能为空', 'error');
    return;
  }
  const modelDesc = window.prompt('模型描述（可选）', '') ?? '';
  try {
    const result = await api('/api/models', {
      method: 'POST',
      body: JSON.stringify({
        experiment_id: exp.model_artifact_experiment_id || exp.experiment_id,
        model_name: modelName.trim(),
        model_desc: modelDesc.trim(),
        algorithm: exp.algorithm,
        dataset: exp.dataset,
        table_name: 'v2_channels',
        file_path: exp.model_dir,
        metrics: exp.metrics_avg || {},
        config: {},
      }),
    });
    notify(result.message || '模型发布成功', 'success');
    await loadExperiments();
  } catch (error) {
    notify(`发布失败：${error.message}`, 'error');
  }
}

function calculateStats(rows) {
  const total = rows.length;
  const success = rows.filter(r => r.status === 'success').length;
  const error = rows.filter(r => r.status === 'error').length;
  
  const accuracies = rows
    .filter(r => r.metrics && typeof r.metrics.accuracy === 'number')
    .map(r => r.metrics.accuracy);
  const avgAccuracy = accuracies.length > 0 
    ? (accuracies.reduce((a, b) => a + b, 0) / accuracies.length * 100).toFixed(1) 
    : '0';
  
  return { total, success, error, avgAccuracy };
}

function updateStats(rows) {
  const stats = calculateStats(rows);
  if (els.statTotal) els.statTotal.textContent = stats.total;
  if (els.statSuccess) els.statSuccess.textContent = stats.success;
  if (els.statError) els.statError.textContent = stats.error;
  if (els.statAccuracy) els.statAccuracy.textContent = `${stats.avgAccuracy}%`;
  if (els.statsOverview) els.statsOverview.style.display = 'grid';
}

function getStatusClass(status) {
  switch (status) {
    case 'success': return 'success';
    case 'error': return 'error';
    case 'running': return 'running';
    default: return 'unknown';
  }
}

function formatMetricValue(value, metricName) {
  if (typeof value === 'number') {
    if (metricName === 'accuracy' || metricName === 'precision' || metricName === 'recall') {
      return (value * 100).toFixed(2) + '%';
    }
    return value.toFixed(4);
  }
  return String(value);
}

function renderTableView(rows) {
  const allMetrics = new Set();
  rows.forEach(row => {
    Object.keys(row.metrics || {}).forEach(key => allMetrics.add(key));
  });
  
  const metricKeys = Array.from(allMetrics);
  
  const headers = ['detector', 'status', 'error_code', ...metricKeys];
  const headerHtml = headers.map(h => `<th>${h}</th>`).join('');
  
  const body = rows.map((row) => {
    const statusClass = getStatusClass(row.status);
    const statusHtml = `<span class="status-pill ${statusClass}">${row.status || 'unknown'}</span>`;
    
    const metricsHtml = metricKeys.map(key => {
      const value = row.metrics?.[key];
      if (value === undefined || value === null) {
        return '<td>-</td>';
      }
      const formatted = formatMetricValue(value, key);
      let valueClass = 'metric-value';
      if (key === 'accuracy' && typeof value === 'number') {
        valueClass += value >= 0.8 ? ' high' : value < 0.6 ? ' low' : '';
      }
      return `<td><span class="${valueClass}">${formatted}</span></td>`;
    }).join('');
    
    return `
      <tr>
        <td><strong>${row.detector || ''}</strong></td>
        <td>${statusHtml}</td>
        <td>${row.error_code || '-'}</td>
        ${metricsHtml}
      </tr>
    `;
  }).join('');
  
  els.detailTable.innerHTML = `<thead><tr>${headerHtml}</tr></thead><tbody>${body}</tbody>`;
}

function renderCardsView(rows) {
  if (!els.cardsContainer) return;
  els.cardsContainer.innerHTML = rows.map((row, index) => {
    const trace = row.chart_trace;
    const hasTrace = trace && Array.isArray(trace.scores) && trace.scores.length > 0;
    const threshold = trace && Array.isArray(trace.decision_thresholds)
      ? trace.decision_thresholds.filter(v => v !== null && Number.isFinite(Number(v)))
      : [];
    const limit = trace?.decision_threshold === null || trace?.decision_threshold === undefined
      ? null : Number(trace.decision_threshold);
    let thresholdDesc = '未提供判定阈值';
    if (threshold.length) {
      thresholdDesc = `动态阈值：${formatMetricValue(Math.min(...threshold), 'threshold')} ～ ${formatMetricValue(Math.max(...threshold), 'threshold')}`;
    } else if (Number.isFinite(limit)) {
      thresholdDesc = `判定阈值：${formatMetricValue(limit, 'threshold')}`;
    }
    const message = !hasTrace
      ? '<div class="report-legacy-note">该历史任务未保存异常分数和阈值数据，无法还原比较曲线。新任务将自动保存曲线；请重新运行任务以生成历史曲线。</div>'
      : `<div class="report-curve" data-trace-index="${index}" role="group" aria-label="异常分数与阈值曲线"></div>
         <div class="report-trace-meta">${escapeHtml(thresholdDesc)} · ${trace.total_samples ?? trace.scores.length} 条样本${trace.sampled ? ` · 选取 ${trace.scores.length} 个原始点展示` : ''}</div>`;
    return `<section class="detail-card">
        <div class="card-header">
          <span class="card-title">${escapeHtml(row.detector || '未知算法')}</span>
          <span class="status-pill ${getStatusClass(row.status)}">${escapeHtml(row.status || 'unknown')}</span>
        </div>
        ${message}
        ${row.error_code ? `<div class="error-info">错误：${escapeHtml(row.error_code)}</div>` : ''}
      </section>`;
  }).join('');
  rows.forEach((row, index) => {
    if (!row.chart_trace) return;
    const panel = els.cardsContainer.querySelector(`[data-trace-index="${index}"]`);
    if (panel) window.AASResultChart.render(panel, row.chart_trace, {height: 230});
  });
}

function renderG1Evidence(payload) {
  const card = document.getElementById('g1EvidenceCard');
  const eventHost = document.getElementById('g1EventEvidence');
  if (!card || !eventHost) return;
  const result = payload?.result || payload?.summary?.result || payload?.rows?.find(row=>row.chart_trace)?.chart_trace || {};
  const provenance = payload?.provenance || {};
  const api = window.AASResultChart;
  if (!api?.evidenceSummary) { card.hidden = true; eventHost.hidden = true; return; }
  const evidence = api.evidenceSummary(result, provenance);
  const item = (label, value) => `<div class="g1-evidence-item">${label}<strong>${String(value ?? '不可用').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</strong></div>`;
  card.innerHTML = `<h3>实验输入证据</h3><div class="g1-evidence-grid">${item('算法',evidence.algorithm)}${item('数据来源',evidence.source)}${item('来源类型',evidence.source_type)}${item('来源 SHA-256',evidence.source_sha256 || '不可用')}${item('标签可信度',evidence.label_status)}${item('输入参量',evidence.features.join('、') || '不可用')}</div>`;
  card.hidden = false;
  const events = api.eventEvidence(result);
  const safe = value => escapeHtml(String(value ?? '不可用'));
  eventHost.innerHTML = events.length ? `<h3>故障事件证据</h3><table class="g1-event-table"><thead><tr><th>事件</th><th>类型</th><th>开始</th><th>结束</th><th>真值</th></tr></thead><tbody>${events.map(e=>`<tr><td>${safe(e.id)}</td><td>${safe(e.type)}</td><td>${safe(e.start)}</td><td>${safe(e.end)}</td><td>${e.truth_status==='known'?'已标注':'未知'}</td></tr>`).join('')}</tbody></table>` : '<p>本次归档没有可展示的事件区间。</p>';
  eventHost.hidden = false;
}

function renderDetails(payload) {
  const rows = payload?.rows || [];
  const exp = payload?.experiment || {};
  const expId = exp.experiment_id || '未选择';
  const expType = exp.type || 'training';
  
  // 根据实验类型显示不同的元数据
  let metaInfo = `experiment_id: ${expId}`;
  if (expType === 'evaluation') {
    const evalInfo = payload?.evaluation || {};
    metaInfo += ` | 模型: ${evalInfo.model_name || '未知'} | 数据表: ${evalInfo.eval_table || '未知'}`;
    if(evalInfo.evaluation_mode==='score_only')metaInfo+=' | 仅推理（无真实标签） · 不计算监督指标';
  } else if (expType === 'training') {
    const trainingInfo = payload?.training || {};
    metaInfo += ` | 算法: ${trainingInfo.algorithm || '未知'} | 数据表: ${trainingInfo.table || '未知'}`;
  }
  const provenance = payload?.provenance;
  if (provenance?.authority === 'server_execution') {
    const hash = String(provenance.result_sha256 || '').slice(0, 12);
    metaInfo += ` | 服务端执行记录 · SHA-256 ${hash}…（可核对归档）`;
    if (provenance.input_changed_during_execution) metaInfo += ' | 注意：执行期间源数据发生变化';
  } else {
    metaInfo += ' | 旧记录：无服务端执行溯源信息';
  }
  const resources = payload?.resource_metrics;
  if (resources) {
    const memory = resources.observed_process_group_peak_rss_bytes;
    const ramText = Number.isFinite(Number(memory)) ? `${(Number(memory) / (1024 * 1024)).toFixed(1)} MB` : '不可用';
    const threadCpu = resources.worker_thread_cpu_seconds == null ? '不可用' : `${Number(resources.worker_thread_cpu_seconds).toFixed(2)} 秒`;
    metaInfo += ` | 任务资源：耗时 ${Number(resources.elapsed_seconds).toFixed(2)} 秒，工作线程 CPU ${threadCpu}，AAS 进程峰值 RSS ${ramText}（共享口径）`;
    if (resources.gpu?.available && resources.gpu.observed_process_memory_peak_bytes != null) {
      metaInfo += `，GPU 进程显存峰值 ${(Number(resources.gpu.observed_process_memory_peak_bytes) / 1048576).toFixed(1)} MB`;
    }
  }
  if (els.detailMeta) els.detailMeta.textContent = metaInfo;
  renderG1Evidence(payload);

  
  state.currentData = rows;
  state.selectedTrace = rows.map(row => row.chart_trace);
  
  if (!rows.length) {
    if (els.detailTable) els.detailTable.innerHTML = '<thead><tr><th>detector</th><th>status</th><th>error_code</th></tr></thead><tbody><tr><td colspan="3">暂无明细</td></tr></tbody>';
    if (els.cardsContainer) els.cardsContainer.innerHTML = '<div style="text-align: center; padding: 40px;">暂无明细数据</div>';
    if (els.statsOverview) els.statsOverview.style.display = 'none';
    if (els.viewSwitcher) els.viewSwitcher.style.display = 'none';
    return;
  }
  
  updateStats(rows);
  if (els.viewSwitcher) els.viewSwitcher.style.display = 'flex';
  
  if (state.currentView === 'table') {
    if (els.tableContainer) els.tableContainer.style.display = 'block';
    if (els.cardsContainer) els.cardsContainer.style.display = 'none';
    renderTableView(rows);
  } else {
    if (els.tableContainer) els.tableContainer.style.display = 'none';
    if (els.cardsContainer) els.cardsContainer.style.display = 'grid';
    renderCardsView(rows);
  }
}

function switchView(view) {
  state.currentView = view;
  
  if (els.viewSwitcher) {
    els.viewSwitcher.querySelectorAll('.view-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.view === view);
    });
  }
  
  if (state.currentData) {
    if (view === 'table') {
      if (els.tableContainer) els.tableContainer.style.display = 'block';
      if (els.cardsContainer) els.cardsContainer.style.display = 'none';
      renderTableView(state.currentData);
    } else {
      if (els.tableContainer) els.tableContainer.style.display = 'none';
      if (els.cardsContainer) els.cardsContainer.style.display = 'grid';
      renderCardsView(state.currentData);
    }
  }
}

async function loadDetails(path) {
  const requestSequence = ++state.detailsSequence;
  state.selectedPath = path;
  renderExperiments();
  if (els.detailTable) showSkeleton(els.detailTable, 2);
  try {
    const payload = await api(`/api/experiment-details?path=${encodeURIComponent(path)}`);
    if (requestSequence !== state.detailsSequence) return;
    renderDetails(payload);
  } catch (error) {
    if (requestSequence !== state.detailsSequence) return;
    console.error('加载实验详情失败:', error);
    if (els.statsOverview) els.statsOverview.style.display = 'none';
    if (els.viewSwitcher) els.viewSwitcher.style.display = 'none';
    showErrorState(els.detailTable, '实验详情加载失败：' + error.message, () => loadDetails(path));
  }
}

function updateComparisonChoices() {
  if (!els.compareLeft || !els.compareRight) return;
  const experiments = state.experiments.filter(item => item.experiment_id);
  [els.compareLeft, els.compareRight].forEach((select, index) => {
    const current = select.value;
    select.replaceChildren();
    experiments.forEach(item => {
      const option = document.createElement('option');
      option.value = String(item.experiment_id);
      option.textContent = `${item.experiment_id} · ${item.algorithm || item.model_name || item.type || ''}`;
      select.append(option);
    });
    if (experiments.some(item => String(item.experiment_id) === current)) {
      select.value = current;
    } else if (experiments[index]) {
      select.value = String(experiments[index].experiment_id);
    }
  });
}

els.compareBtn?.addEventListener('click', async () => {
  const left = els.compareLeft?.value;
  const right = els.compareRight?.value;
  if (!left || !right || left === right) {
    els.compareResult.textContent = '请从当前任务列表选择两个不同的实验。';
    return;
  }
  els.compareResult.textContent = '正在核验归档与数据输入…';
  els.compareBtn.disabled = true;
  try {
    const result = await api(`/api/compare-experiments?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
    const labels = {
      identical_model_inputs: '模型实际输入完全一致',
      same_raw_inputs_different_preprocessing: '同一原始输入，但预处理不同',
      same_samples_different_values: '样本时间和标签一致，输入值不同',
      different_or_unconfirmed_inputs: '输入条件不相同或无法确认',
      insufficient_evidence: '缺少模型输入证据，无法确认',
      unverified_archives: '归档完整性校验未通过',
      different_task_types: '训练与评估任务类型不同',
    };
    state.lastComparison = { schema_version: 1, created_at: new Date().toISOString(), left_experiment_id: left, right_experiment_id: right, ...result };
    if (els.exportComparisonBtn) els.exportComparisonBtn.disabled = false;
    const controls = result.repeatability_controls;
    const yesNo = (value) => value === true ? '一致' : value === false ? '不同' : '证据不足';
    const controlText = controls ?
      `\n算法：${yesNo(controls.same_algorithm)}；算法参数：${yesNo(controls.same_algorithm_parameters)}；运行环境：${yesNo(controls.same_runtime)}；实现摘要：${yesNo(controls.same_implementation_digest)}；随机性记录：${yesNo(controls.same_recorded_randomness)}` : '';
    els.compareResult.textContent = `${labels[result.classification] || '未分类'}
${result.reason || ''}${controlText}
${result.scope_note || ''}`;
    const comparison = result.metric_comparison;
    if (comparison?.status === 'comparable' && Array.isArray(comparison.rows) && comparison.rows.length) {
      const table = document.createElement('table');
      table.className = 'report-metrics-table';
      const caption = document.createElement('caption');
      caption.textContent = '同一数据条件下的监督指标（差值＝第二个实验－第一个实验；正数不一定更好）';
      table.append(caption);
      const thead = document.createElement('thead');
      const head = document.createElement('tr');
      for (const label of ['指标', '实验一', '实验二', '差值']) {
        const th = document.createElement('th'); th.textContent = label; head.append(th);
      }
      thead.append(head); table.append(thead);
      const tbody = document.createElement('tbody');
      const names = {accuracy:'准确率',precision:'精确率',recall:'召回率',f1:'F1',
        f1_score:'F1',roc_auc:'ROC AUC',false_positive_rate:'假阳性率',
        false_alarm_rate:'误报警率',event_recall:'事件级召回率',
        median_first_alarm_delay_seconds:'首次报警延迟中位数（秒）'};
      for (const item of comparison.rows) {
        if (![item.left,item.right,item.delta].every(Number.isFinite)) continue;
        const tr = document.createElement('tr');
        for (const value of [names[item.key] || item.key, item.left.toFixed(4),
          item.right.toFixed(4), (item.delta > 0 ? '+' : '') + item.delta.toFixed(4)]) {
          const td = document.createElement('td'); td.textContent = String(value); tr.append(td);
        }
        tbody.append(tr);
      }
      table.append(tbody);
      els.compareResult.append(table);
    } else if (comparison?.reason) {
      const hint = document.createElement('p');
      hint.textContent = comparison.reason;
      els.compareResult.append(hint);
    }
  } catch (error) {
    state.lastComparison = null;
    if (els.exportComparisonBtn) els.exportComparisonBtn.disabled = true;
    els.compareResult.textContent = `比较失败：${error.message}`;
  } finally {
    els.compareBtn.disabled = false;
  }
});

async function loadExperiments() {
  const sequence = ++state.listSequence;
  if (els.experimentList) showSkeleton(els.experimentList, 2);
  let payload;
  try {
    payload = await api(`/api/experiments${state.selectedExperimentType === 'all' ? '' : `?type=${state.selectedExperimentType}`}`);
    if (sequence !== state.listSequence) return;
  } catch (error) {
    if (sequence !== state.listSequence) return;
    console.error('加载实验列表失败:', error);
    showErrorState(els.experimentList, '实验列表加载失败：' + error.message, loadExperiments);
    return;
  }
  state.experiments = payload.items || [];
  updateComparisonChoices();
  renderExperiments();
  const initialPath = decodeURIComponent(location.hash.replace(/^#/, ''));
  const filtered = getFilteredExperiments();
  const preferredPath = filtered.find(item => item.path === state.selectedPath)?.path ||
    filtered.find(item => item.path === initialPath)?.path || filtered[0]?.path;
  if (preferredPath) {
    await loadDetails(preferredPath);
  } else clearSelection('暂无匹配任务');
}

function initExperimentTypeTabs() {
  els.experimentTypeTabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const type = btn.dataset.type;
      
      els.experimentTypeTabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      
      state.selectedExperimentType = type;
      clearSelection();
      loadExperiments();
      
      if (els.detailMeta) els.detailMeta.textContent = '选择实验查看';
      if (els.detailTable) els.detailTable.innerHTML = '';
      if (els.statsOverview) els.statsOverview.style.display = 'none';
      if (els.viewSwitcher) els.viewSwitcher.style.display = 'none';
    });
  });
}

function applyReportSearch() {
  renderExperiments();
  const visible = getFilteredExperiments();
  if (visible.some(item => item.path === state.selectedPath)) return;
  clearSelection(visible.length ? '正在加载符合条件的任务' : '没有匹配的任务');
  if (visible.length) loadDetails(visible[0].path);
}

els.reportSearch?.addEventListener('input', () => {
  state.searchText = els.reportSearch.value;
  applyReportSearch();
});
els.reportSearchClear?.addEventListener('click', () => {
  state.searchText = '';
  if (els.reportSearch) {els.reportSearch.value = ''; els.reportSearch.focus();}
  applyReportSearch();
});
els.reportStatus?.addEventListener('change', () => {
  state.statusFilter = els.reportStatus.value;
  applyReportSearch();
});

function exportToCSV() {
  if (!state.currentData || state.currentData.length === 0) {
    notify('没有可导出的数据', 'error');
    return;
  }

  const rows = state.currentData;
  
  const allMetrics = new Set();
  rows.forEach(row => {
    Object.keys(row.metrics || {}).forEach(key => allMetrics.add(key));
  });
  const metricKeys = Array.from(allMetrics);
  
  const headers = ['detector', 'status', 'error_code', ...metricKeys];
  const csvCell = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;
  
  const csvRows = [];
  csvRows.push(headers.map(csvCell).join(','));
  
  rows.forEach(row => {
    const values = [
      csvCell(row.detector || ''),
      csvCell(row.status || ''),
      csvCell(row.error_code || ''),
      ...metricKeys.map(key => {
        const value = row.metrics?.[key];
        let formatted = '';
        if (value !== undefined && value !== null) {
          if (typeof value === 'number') {
            if (key === 'accuracy' || key === 'precision' || key === 'recall') {
              formatted = (value * 100).toFixed(2) + '%';
            } else {
              formatted = value.toFixed(4);
            }
          } else {
            formatted = String(value);
          }
        }
        return csvCell(formatted);
      })
    ];
    csvRows.push(values.join(','));
  });
  
  const csvContent = '\uFEFF' + csvRows.join('\n');
  
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const link = document.createElement('a');
  const url = URL.createObjectURL(blob);
  
  const expId = state.experiments.find(e => e.path === state.selectedPath)?.experiment_id || 'report';
  const filename = `experiment_${expId}_${new Date().toISOString().split('T')[0]}.csv`;
  
  link.setAttribute('href', url);
  link.setAttribute('download', filename);
  link.style.visibility = 'hidden';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

els.viewSwitcher?.querySelectorAll('.view-btn[data-view]').forEach(btn => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

els.exportBtn?.addEventListener('click', exportToCSV);

function exportG1ComparisonJSON() {
  const comparison = state.lastComparison;
  if (!comparison) { notify('请先完成一次实验可比性检查', 'error'); return; }
  const payload = {
    schema_version: 1,
    report_type: 'experiment_comparison',
    exported_at: new Date().toISOString(),
    comparison,
    authority: 'server_compare_experiments',
    note: '比较分类及指标由服务端生成；导出不重新计算或改变可比性结论。'
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json;charset=utf-8'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = `AAS_G1_comparison_${comparison.left_experiment_id}_${comparison.right_experiment_id}.json`;
  document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
}
els.exportComparisonBtn?.addEventListener('click', exportG1ComparisonJSON);

function exportG1EvidenceJSON() {
  if (!state.selectedPath) { notify('请先选择实验', 'error'); return; }
  api(`/api/experiment-details?path=${encodeURIComponent(state.selectedPath)}`).then(payload => {
    const result = payload?.result || payload?.summary?.result || payload?.rows?.find(row=>row.chart_trace)?.chart_trace || {};
    const snapshot = window.AASResultChart?.buildReportSnapshot?.(result, payload?.provenance || {});
    if (!snapshot) throw new Error('当前页面缺少结果证据组件');
    const blob = new Blob([JSON.stringify(snapshot,null,2)], {type:'application/json;charset=utf-8'});
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href=url; link.download=`AAS_G1_evidence_${String(payload?.experiment?.experiment_id || 'report')}.json`;
    link.click(); URL.revokeObjectURL(url);
  }).catch(error => notify('证据报告导出失败：'+error.message,'error'));
}
document.getElementById('exportG1EvidenceBtn')?.addEventListener('click', exportG1EvidenceJSON);
els.refreshTaskHistory?.addEventListener('click', loadTaskHistory);

initExperimentTypeTabs();

loadExperiments();
loadTaskHistory();
