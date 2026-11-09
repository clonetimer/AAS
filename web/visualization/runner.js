const state = {
  databases: [],
  tables: [],
  rows: [],
  selectedFeatures: ['value'],
  extraTrainingTables: [],
  extraEvaluationTables: [],
  trainTimeColumn: 'timestamp',
  availableTrainTime: null,
  filters: {
    model: '__ALL__',
    subsystem: '__ALL__',
    device: '__ALL__',
    variable: '__ALL__',
  },
  selectedDb: null,
  selectedTable: null,
  selectedAlgorithm: null,
  evaluationMode: 'simple',
  trainRatio: 0.7,
  selectedTrainTable: null,
  selectedTestTable: null,
  // 数据预处理相关
  dataView: 'raw',
  preprocessingStatus: null,
  // 模型管理相关
  models: [],
  selectedModel: null,
  lastTrainingResult: null,
  lastEvaluationResult: null,
  lastEvaluationConfig: null,
  pluginCatalog: {},
  previewRequestId: 0,
  // 评估页独立维护数据选择与预览状态，避免覆盖训练页 selectedDb。
  evalSelectedDb: null,
  evalSelectedTable: null,
  evalTables: [],
  evalRows: [],
  evalPreviewRequestId: 0,
  evalDataView: 'raw',
  evalCleanStatus: null,
  evalInputSources: null,
  evalCandidates: [],
  evalTableRevision: 0,
  trainTableRevision: 0,
};

const els = {
  dbSelect: document.getElementById('dbSelect'),
  tableSelect: document.getElementById('tableSelect'),
  trainFeatureList: document.getElementById('trainFeatureList'),
  additionalTrainTables: document.getElementById('additionalTrainTables'),
  trainTablesSearch: document.getElementById('trainTablesSearch'),
  trainSelectionSummary: document.getElementById('trainSelectionSummary'),
  alignmentTolerance: document.getElementById('alignmentTolerance'),
  alignmentMethod: document.getElementById('alignmentMethod'),
  timelineMode: document.getElementById('timelineMode'),
  gridIntervalField: document.getElementById('gridIntervalField'),
  gridIntervalSeconds: document.getElementById('gridIntervalSeconds'),
  labelAlignmentMethod: document.getElementById('labelAlignmentMethod'),
  checkAlignmentBtn: document.getElementById('checkAlignmentBtn'),
  trainAlignmentPreview: document.getElementById('trainAlignmentPreview'),
  additionalEvalTables: document.getElementById('additionalEvalTables'),
  evalTablesSearch: document.getElementById('evalTablesSearch'),
  evalSelectionSummary: document.getElementById('evalSelectionSummary'),
  trainTimeColumn: document.getElementById('trainTimeColumn'),
  trainTimeStart: document.getElementById('trainTimeStart'),
  trainTimeEnd: document.getElementById('trainTimeEnd'),
  trainAvailableRange: document.getElementById('trainAvailableRange'),
  resetTrainTimeBtn: document.getElementById('resetTrainTimeBtn'),
  modelFilter: document.getElementById('modelFilter'),
  subsystemFilter: document.getElementById('subsystemFilter'),
  deviceFilter: document.getElementById('deviceFilter'),
  variableFilter: document.getElementById('variableFilter'),
  algorithmSelect: document.getElementById('algorithmSelect'),
  runBtn: document.getElementById('runBtn'),
  refreshBtn: document.getElementById('refreshBtn'),
  runOutput: document.getElementById('runOutput'),
  chart: document.getElementById('chart'),
  trainChartViewer: document.getElementById('trainChartViewer'),
  trainChartFullscreenBtn: document.getElementById('trainChartFullscreenBtn'),
  trainVisualizationTitle: document.getElementById('trainVisualizationTitle'),
  trainVisualizationHint: document.getElementById('trainVisualizationHint'),
  trainResultTableWrap: document.getElementById('trainResultTableWrap'),
  resultTable: document.getElementById('resultTable'),
  resultMeta: document.getElementById('resultMeta'),
  trainResultPanel: document.getElementById('trainResultPanel'),
  // 新模式选择元素
  modeRadios: document.querySelectorAll('input[name="evaluationMode"]'),
  simpleModeSection: document.getElementById('simpleModeSection'),
  rigorousModeSection: document.getElementById('rigorousModeSection'),
  trainRatioSlider: document.getElementById('trainRatioSlider'),
  trainRatioValue: document.getElementById('trainRatioValue'),
  trainTableSelect: document.getElementById('trainTableSelect'),
  testTableSelect: document.getElementById('testTableSelect'),
  // 数据预处理元素
  preprocessingModule: document.getElementById('preprocessingModule'),
  preprocessingStatus: document.getElementById('preprocessingStatus'),
  preprocessingConfig: document.getElementById('preprocessingConfig'),
  runPreprocessingBtn: document.getElementById('runPreprocessingBtn'),
  deleteCleanDataBtn: document.getElementById('deleteCleanDataBtn'),
  smoothWindowSize: document.getElementById('smoothWindowSize'),
  outlierThreshold: document.getElementById('outlierThreshold'),
  inputNormalizationMethod: document.getElementById('inputNormalizationMethod'),
  pluginPreprocessors: document.getElementById('pluginPreprocessors'),
  pluginPreprocessorList: document.getElementById('pluginPreprocessorList'),
  trainingStrategySelect: document.getElementById('trainingStrategySelect'),
  trainingStrategyHint: document.getElementById('trainingStrategyHint'),
  dataViewRadios: document.querySelectorAll('input[name="dataView"]'),
  // 训练日志
  trainingLog: document.getElementById('trainingLog'),
  // 模型管理元素
  mainTabBtns: document.querySelectorAll('.main-tab-btn'),
  trainTab: document.getElementById('trainTab'),
  evaluateTab: document.getElementById('evaluateTab'),
  // 发布模型相关
  publishModelPanel: document.getElementById('publishModelPanel'),
  modelNameInput: document.getElementById('modelNameInput'),
  modelDescInput: document.getElementById('modelDescInput'),
  modelPublishInfo: document.getElementById('modelPublishInfo'),
  publishModelBtn: document.getElementById('publishModelBtn'),
  // 评估Tab相关
  modelSelect: document.getElementById('modelSelect'),
  modelCardList: document.getElementById('modelCardList'),
  evalDbSelect: document.getElementById('evalDbSelect'),
  evalDomainHint: document.getElementById('evalDomainHint'),
  evalTableSelect: document.getElementById('evalTableSelect'),
  evalDataViewRadios: document.querySelectorAll('input[name="evalDataView"]'),
  evalCleanStatus: document.getElementById('evalCleanStatus'),
  evaluateBtn: document.getElementById('evaluateBtn'),
  refreshModelsBtn: document.getElementById('refreshModelsBtn'),
  evalOutput: document.getElementById('evalOutput'),
  evalLog: document.getElementById('evalLog'),
  evalChart: document.getElementById('evalChart'),
  evalChartViewer: document.getElementById('evalChartViewer'),
  evalChartFullscreenBtn: document.getElementById('evalChartFullscreenBtn'),
  evalVisualizationTitle: document.getElementById('evalVisualizationTitle'),
  evalVisualizationHint: document.getElementById('evalVisualizationHint'),
  evalResultTableWrap: document.getElementById('evalResultTableWrap'),
  evalResultTable: document.getElementById('evalResultTable'),
  evalResultMeta: document.getElementById('evalResultMeta'),
  evalResultPanel: document.getElementById('evalResultPanel'),
  evalModeRadios: document.querySelectorAll('input[name="evalMode"]'),
  trainingHistoryList: document.getElementById('trainingHistoryList'),
  evaluationHistoryList: document.getElementById('evaluationHistoryList'),
  // 算法参数配置元素
  algorithmParamsPanel: document.getElementById('algorithmParamsPanel'),
  algorithmParamsForm: document.getElementById('algorithmParamsForm'),
  algorithmParamsCount: document.getElementById('algorithmParamsCount'),
};


function setResultPanelVisible(panel, visible) {
  if (!panel) return;
  panel.classList.toggle('hidden', !visible);
}

function setTrainingVisualizationText(title, hint = '') {
  if (els.trainVisualizationTitle) els.trainVisualizationTitle.textContent = title;
  if (els.trainVisualizationHint) els.trainVisualizationHint.textContent = hint;
}

function resetTrainingResultView(message = '选择训练数据表后显示当前数据视图曲线。') {
  // 训练页右侧曲线区始终保留：训练前用于源数据预览，训练后原位更新结果。
  setResultPanelVisible(els.trainResultPanel, true);
  setTrainingVisualizationText(
    '训练数据预览',
    '选择训练数据表后，这里会立即显示当前数据视图曲线；切换原始/清洁数据会同步刷新，训练完成后在同一区域更新为训练结果。'
  );
  if (els.chart) els.chart.innerHTML = `<div class="empty-state">${message}</div>`;
  if (els.resultTable) els.resultTable.innerHTML = '';
  if (els.trainResultTableWrap) els.trainResultTableWrap.hidden = true;
  if (els.resultMeta) els.resultMeta.textContent = '';
}

function setEvaluationVisualizationText(title, hint = '') {
  if (els.evalVisualizationTitle) els.evalVisualizationTitle.textContent = title;
  if (els.evalVisualizationHint) els.evalVisualizationHint.textContent = hint;
}

function resetEvaluationResultView(message = '选择测试数据表后显示测试数据曲线。') {
  // 与训练页一致：评估前保留右侧曲线区用于测试数据预览，评估完成后原位更新结果。
  setResultPanelVisible(els.evalResultPanel, true);
  setEvaluationVisualizationText(
    '评估数据预览',
    '选择测试数据表后，这里会立即显示测试数据的原始物理量曲线；评估完成后在同一区域更新为评估结果。'
  );
  if (els.evalChart) {
    chartRenderCache.delete(els.evalChart);
    els.evalChart.innerHTML = `<div class="empty-state">${message}</div>`;
  }
  if (els.evalResultTable) els.evalResultTable.innerHTML = '';
  if (els.evalResultTableWrap) els.evalResultTableWrap.hidden = true;
  if (els.evalResultMeta) els.evalResultMeta.textContent = '';
}

function invalidateEvaluationResult(message = '正在加载所选测试数据的曲线…') {
  resetEvaluationResultView(message);
  state.lastEvaluationResult = null;
  state.lastEvaluationConfig = null;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function showToast(message, type = 'info', duration = 3200) {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    container.setAttribute('role', 'status');
    container.setAttribute('aria-live', 'polite');
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  const dismiss = () => {
    toast.classList.add('leaving');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
    setTimeout(() => toast.remove(), 300);
  };
  const timer = setTimeout(dismiss, duration);
  toast.addEventListener('click', () => { clearTimeout(timer); dismiss(); });
}

function showHistorySkeleton(listEl, count = 2) {
  if (!listEl) return;
  let html = '';
  for (let i = 0; i < count; i++) {
    html += `<div class="history-skeleton">
      <div class="skeleton sk-line" style="width: 40%"></div>
      <div class="skeleton sk-line" style="width: 70%"></div>
      <div class="skeleton sk-line" style="width: 55%"></div>
    </div>`;
  }
  listEl.innerHTML = html;
}

function showHistoryError(listEl, message) {
  if (!listEl) return;
  listEl.innerHTML = `<div class="state-error">${message}</div>`;
}

const actionLogger = {
  log(type, message, data = {}) {
    const logEntry = {
      timestamp: new Date().toISOString(),
      type,
      message,
      data,
    };
    console.log(`[前端日志] ${type}:`, message);
    fetch('/api/log-action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(logEntry),
    }).catch(() => {});
  },
};

function fmt(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(2) : String(value ?? '');
}

function displayTrainingLog(log) {
  if (!els.trainingLog) return;
  
  const preEl = els.trainingLog.querySelector('pre');
  if (preEl) {
    preEl.textContent = log || '暂无训练日志';
  }
  els.trainingLog.style.display = 'block';
  els.trainingLog.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function displayEvalLog(log) {
  if (!els.evalLog) return;
  
  const preEl = els.evalLog.querySelector('pre');
  if (preEl) {
    preEl.textContent = log || '暂无评估日志';
  }
  els.evalLog.style.display = 'block';
  els.evalLog.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function formatRoleTableOption(item) {
  const role = item?.role_label || '未指定';
  const normal = item?.normal_count ?? 0;
  const fault = item?.fault_count ?? 0;
  const warning = item?.role_valid === false ? ' ⚠' : '';
  return `[${role}] ${item.name} (${item.row_count} 条 · 正常 ${normal} / 故障 ${fault})${warning}`;
}

function preferredRoleTable(tables, role) {
  return tables.find((item) => item.data_role === role && item.role_valid !== false)
    || tables.find((item) => item.data_role === role)
    || null;
}

function validTrainingTables(tables) {
  return (tables || []).filter((item) => item.data_role === 'train' && item.role_valid !== false
    && item.is_single_measurement !== false);
}

function validTestTables(tables) {
  return (tables || []).filter((item) => item.data_role === 'test'
    && item.role_valid !== false && item.is_single_measurement !== false);
}

const IDENTITY_FIELDS = ['model', 'subsystem', 'device', 'variable'];
function matchesSourceIdentity(table, identity) {
  if (!identity) return false;
  return (table.hierarchy || []).length === 1 && IDENTITY_FIELDS.every((field) =>
    String(table.hierarchy[0][field]) === String(identity[field]));
}


function invalidateTrainingResult() {
  resetTrainingResultView('正在加载所选训练数据的曲线…');
  state.lastTrainingResult = null;
  if (els.publishModelPanel) {
    els.publishModelPanel.classList.remove('show');
  }
}

function renderSelectOptions(select, items, selectedValue) {
  if (!select) return;
  select.replaceChildren();
  if (!items.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = '暂无数据';
    select.appendChild(option);
    return;
  }
  items.forEach((item) => {
    const option = document.createElement('option');
    option.value = String(item.value ?? '');
    option.textContent = String(item.label ?? item.value ?? '');
    select.appendChild(option);
  });
  if (selectedValue !== undefined && selectedValue !== null && selectedValue !== '') {
    select.value = selectedValue;
  }
}

function uniqueValues(rows, key) {
  return [...new Set(rows.map((row) => row[key]).filter((value) => value !== undefined && value !== null && value !== ''))];
}

const chartRenderCache = new WeakMap();

function getChartGeometry(chartEl) {
  const fallbackWidth = 1100;
  const fallbackHeight = 360;
  const width = Math.max(720, Math.round(chartEl?.clientWidth || fallbackWidth));
  const height = Math.max(320, Math.round(chartEl?.clientHeight || fallbackHeight));
  return {
    width,
    height,
    padding: { top: 40, right: 28, bottom: 54, left: 64 },
  };
}

function estimateChartTextWidth(text) {
  return Array.from(String(text || '')).reduce((total, ch) => total + (ch.charCodeAt(0) > 255 ? 12 : 7), 0);
}

function rerenderCachedChart(chartEl) {
  if (!chartEl) return;
  const cached = chartRenderCache.get(chartEl);
  if (!cached) return;
  if (cached.type === 'result') {
    renderChart(
      cached.rows,
      cached.threshold,
      cached.algorithm,
      chartEl,
      cached.decisionThreshold,
      cached.metrics,
      cached.decisionThresholds,
      cached.predLabels,
      cached.eventOverlay,
      cached.eventMetrics,
    );
  } else if (cached.type === 'preview') {
    renderSourcePreviewChart(cached.rows, chartEl, cached.dataView);
  }
}

function scheduleChartGeometryRefresh(viewer) {
  const chartEl = viewer?.querySelector('.runner-result-chart');
  if (!chartEl) return;
  requestAnimationFrame(() => requestAnimationFrame(() => rerenderCachedChart(chartEl)));
}

function chartViewerIsExpanded(viewer) {
  if (!viewer) return false;
  return document.fullscreenElement === viewer || viewer.classList.contains('chart-fullscreen-fallback');
}

function updateChartFullscreenControls() {
  [
    [els.trainChartViewer, els.trainChartFullscreenBtn],
    [els.evalChartViewer, els.evalChartFullscreenBtn],
  ].forEach(([viewer, button]) => {
    if (!viewer || !button) return;
    const expanded = chartViewerIsExpanded(viewer);
    const label = button.querySelector('.chart-fullscreen-label');
    const icon = button.querySelector('.chart-fullscreen-icon');
    if (label) label.textContent = expanded ? '退出全屏' : '全屏';
    if (icon) icon.textContent = expanded ? '⤢' : '⛶';
    button.setAttribute('aria-label', expanded ? '退出曲线全屏' : '全屏查看曲线');
    button.setAttribute('title', expanded ? '退出全屏（Esc）' : '全屏查看曲线');
  });
}

function leaveFallbackChartFullscreen() {
  const expanded = document.querySelector('.result-chart-viewer.chart-fullscreen-fallback');
  if (!expanded) return false;
  expanded.classList.remove('chart-fullscreen-fallback');
  document.body.classList.remove('chart-fullscreen-open');
  updateChartFullscreenControls();
  scheduleChartGeometryRefresh(expanded);
  return true;
}

async function toggleChartFullscreen(viewer) {
  if (!viewer) return;

  if (viewer.classList.contains('chart-fullscreen-fallback')) {
    leaveFallbackChartFullscreen();
    return;
  }

  if (document.fullscreenElement === viewer) {
    await document.exitFullscreen();
    return;
  }

  if (document.fullscreenElement && document.exitFullscreen) {
    await document.exitFullscreen();
  }

  if (typeof viewer.requestFullscreen === 'function') {
    try {
      await viewer.requestFullscreen();
      return;
    } catch (error) {
      console.warn('浏览器全屏接口不可用，切换为页面内全屏模式：', error);
    }
  }

  // 兼容 iframe 未授予 Fullscreen 权限或旧浏览器：退化为页面内全屏。
  viewer.classList.add('chart-fullscreen-fallback');
  document.body.classList.add('chart-fullscreen-open');
  updateChartFullscreenControls();
  scheduleChartGeometryRefresh(viewer);
}

function renderChart(rows, scores, algorithm = '', chartEl = els.chart, decisionThreshold = null, metrics = null, decisionThresholds = null, predLabels = null, eventOverlay = null, eventMetrics = null) {
  // A single score scale shared with the decision boundary. Raw `value` is
  // intentionally NOT plotted against the anomaly score as if it shared units.
  if (!Array.isArray(rows) || !rows.length) {
    chartEl.innerHTML = '<div class="empty-state">没有检测结果可绘制。</div>';
    return;
  }
  chartRenderCache.set(chartEl, {
    type: 'result', rows, threshold: scores, algorithm, decisionThreshold,
    decisionThresholds, predLabels, metrics, eventOverlay, eventMetrics,
  });
  const { width, height } = getChartGeometry(chartEl);
  window.AASResultChart.render(chartEl, {
    rows, threshold: scores, algorithm,
    decision_threshold: decisionThreshold,
    decision_thresholds: decisionThresholds,
    pred_labels: predLabels,
    event_overlay: eventOverlay,
    event_metrics: eventMetrics,
  }, {width, height});
}

function renderSourcePreviewChart(rows, chartEl = els.chart, dataView = state.dataView) {
  if (!chartEl) return;
  const isEvaluationPreview = chartEl === els.evalChart;
  if (!Array.isArray(rows) || !rows.length) {
    chartEl.innerHTML = `<div class="empty-state">当前${isEvaluationPreview ? '测试' : '训练'}数据表暂无可预览记录。</div>`;
    return;
  }

  const selected = isEvaluationPreview
    ? (state.selectedModel?.feature_columns || ['value'])
    : state.selectedFeatures;
  const columns = selected.filter(col => rows.some(row => row[col] !== null && row[col] !== '' && Number.isFinite(Number(row[col]))));
  const points = rows.map((row,index) => ({index,timestamp:row.timestamp ?? index+1}));
  if (!columns.length) {
    chartEl.innerHTML = '<div class="empty-state">当前数据表没有可绘制的数值列。</div>';
    return;
  }

  chartRenderCache.set(chartEl, { type: 'preview', rows, dataView });
  const { width, height, padding } = getChartGeometry(chartEl);
  const values = rows.flatMap(row => columns.map(col =>
    row[col] === null || row[col] === '' ? NaN : Number(row[col]))).filter(Number.isFinite);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const rawSpan = rawMax - rawMin;
  const pad = rawSpan > 0 ? rawSpan * 0.08 : Math.max(Math.abs(rawMax) * 0.08, 1);
  const min = rawMin - pad;
  const max = rawMax + pad;
  const yScale = (value) => height - padding.bottom - ((value - min) / (max - min || 1)) * (height - padding.top - padding.bottom);
  const xScale = (index) => padding.left + (index / Math.max(points.length - 1, 1)) * (width - padding.left - padding.right);

  const previewColors = ['#7aa2ff','#6ee7d8','#ffcf5c','#ff8a8a','#c58cff','#90b8f2'];
  const linePoints = columns.map((col, k) => {
    let paths = [], current = [];
    rows.forEach((row, index) => {
      const num = row[col] === null || row[col] === '' ? NaN : Number(row[col]);
      if (!Number.isFinite(num)) { if (current.length) paths.push(current.join(' ')); current = []; }
      else current.push(`${xScale(index)},${yScale(num)}`);
    });
    if (current.length) paths.push(current.join(' '));
    return paths.map(segment => `<polyline fill="none" stroke="${previewColors[k % previewColors.length]}" stroke-width="2" points="${segment}" />`).join('');
  }).join('');
  const grid = Array.from({ length: 5 }, (_, index) => min + (index / 4) * (max - min)).map((tick) => {
    const y = yScale(tick);
    return `
      <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="var(--line)" />
      <text x="${padding.left - 10}" y="${y + 4}" text-anchor="end" fill="var(--muted)" font-size="11">${fmt(tick)}</text>
    `;
  }).join('');

  const firstTimestamp = String(points[0].timestamp ?? '');
  const lastTimestamp = String(points[points.length - 1].timestamp ?? '');
  const seriesLabel = isEvaluationPreview
    ? '测试数据'
    : (dataView === 'clean' ? '清洁数据' : '原始数据');
  const ariaLabel = isEvaluationPreview ? '评估数据预览曲线' : '训练数据预览曲线';

  chartEl.innerHTML = `
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${ariaLabel}">
      ${grid}
      <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--line-strong)" />
      <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--line-strong)" />
      ${linePoints}
      <text x="${padding.left}" y="${height - 16}" text-anchor="start" fill="var(--muted)" font-size="11">${firstTimestamp}</text>
      <text x="${width - padding.right}" y="${height - 16}" text-anchor="end" fill="var(--muted)" font-size="11">${lastTimestamp}</text>
      ${columns.slice(0,6).map((col,k) => `<rect x="${padding.left+(k%3)*165}" y="${11+Math.floor(k/3)*17}" width="10" height="10" fill="${previewColors[k % previewColors.length]}" /><text x="${padding.left+15+(k%3)*165}" y="${16+Math.floor(k/3)*17}" dominant-baseline="middle" fill="var(--text)" font-size="11">${col}</text>`).join('')}
    </svg>
  `;
}

function cleanPreviewUnavailableMessage(status) {
  const code = status?.status;
  if (code === 'invalidated') return '清洁数据已失效（源数据已变更），请重新执行预处理后再预览。';
  if (code === 'error') return `清洁数据状态检查失败：${status?.message || '未知错误'}`;
  return '清洁数据尚未生成，请先点击“执行预处理”，完成后会自动显示清洁曲线。';
}

function renderTrainingSourcePreview() {
  setResultPanelVisible(els.trainResultPanel, true);
  const isClean = state.dataView === 'clean';
  const viewLabel = isClean ? '清洁数据' : '原始数据';
  setTrainingVisualizationText(
    `训练数据预览 · ${viewLabel}`,
    isClean
      ? '当前显示清理后的物理量曲线（未应用模型输入归一化）；训练完成后，这里会原位更新为训练结果。'
      : '当前显示原始物理量曲线（未应用模型输入归一化）；训练完成后，这里会原位更新为训练结果。'
  );
  if (els.resultTable) els.resultTable.innerHTML = '';
  if (els.trainResultTableWrap) els.trainResultTableWrap.hidden = true;

  if (!state.selectedDb || !state.selectedTable) {
    if (els.chart) els.chart.innerHTML = '<div class="empty-state">选择训练数据表后显示数据曲线。</div>';
    if (els.resultMeta) els.resultMeta.textContent = '';
    return;
  }

  const selectedInfo = state.tables.find((item) => item.name === state.selectedTable);
  const role = selectedInfo?.role_label || '训练集';
  if (isClean && state.preprocessingStatus?.status !== 'valid') {
    if (els.chart) els.chart.innerHTML = `<div class="empty-state">${cleanPreviewUnavailableMessage(state.preprocessingStatus)}</div>`;
    if (els.resultMeta) els.resultMeta.textContent = `[${role}] ${state.selectedTable} · ${viewLabel}不可用`;
    return;
  }

  const total = isClean
    ? (state.preprocessingStatus?.row_count ?? selectedInfo?.row_count)
    : selectedInfo?.row_count;
  const loaded = Array.isArray(state.rows) ? state.rows.length : 0;
  const countText = Number.isFinite(Number(total)) && Number(total) > loaded
    ? `预览 ${loaded}/${total} 条`
    : `${loaded} 条`;
  if (els.resultMeta) els.resultMeta.textContent = `[${role}] ${state.selectedTable} · ${countText} · ${viewLabel}`;

  renderSourcePreviewChart(state.rows || [], els.chart, state.dataView);
}

function showTrainingResultVisualization() {
  setResultPanelVisible(els.trainResultPanel, true);
  setTrainingVisualizationText(
    '训练结果',
    '训练结果已在数据预览区域原位更新；可点击右上角“全屏”查看曲线细节。'
  );
  if (els.trainResultTableWrap) els.trainResultTableWrap.hidden = false;
}

function renderResultTable(rows, scores, tableEl = els.resultTable, metaEl = els.resultMeta, tableName = state.selectedTable, decisionThresholds = null, predLabels = null) {
  const isEval = tableEl === els.evalResultTable;
  const panel = isEval ? els.evalResultPanel : els.trainResultPanel;
  if (!Array.isArray(rows) || !rows.length) {
    tableEl.innerHTML = '';
    metaEl.textContent = '';
    setResultPanelVisible(panel, false);
    return;
  }

  const hasModelInput = rows.some((row) => Number.isFinite(Number(row.model_input_value)));
  const hasDecisionTrace = Array.isArray(decisionThresholds) && decisionThresholds.length === rows.length;
  const hasPredLabels = Array.isArray(predLabels) && predLabels.length === rows.length;
  const columns = hasModelInput
    ? ['timestamp', 'source_value', 'model_input_value', 'anomaly_score']
    : ['timestamp', 'value', 'anomaly_score'];
  if (hasDecisionTrace) columns.push('decision_threshold');
  if (hasPredLabels) columns.push('is_anomaly');
  const head = `<thead><tr>${columns.map((column) => `<th>${column}</th>`).join('')}</tr></thead>`;
  const body = `<tbody>${rows.slice(0, 200).map((row, index) => `<tr>
    <td>${row.timestamp ?? ''}</td>
    <td>${fmt(row.value)}</td>
    ${hasModelInput ? `<td>${fmt(row.model_input_value)}</td>` : ''}
    <td>${fmt(scores[index] ?? '')}</td>
    ${hasDecisionTrace ? `<td>${fmt(decisionThresholds[index])}</td>` : ''}
    ${hasPredLabels ? `<td>${Number(predLabels[index]) ? '异常' : '正常'}</td>` : ''}
  </tr>`).join('')}</tbody>`;

  tableEl.innerHTML = head + body;
  metaEl.textContent = `${tableName || ''} · ${rows.length} 条记录`;
  setResultPanelVisible(panel, true);
}

function renderEvaluationSourcePreview() {
  setResultPanelVisible(els.evalResultPanel, true);
  setEvaluationVisualizationText(
    '评估数据预览 · 测试数据',
    `当前显示所选测试表的${state.evalDataView === 'clean' ? '清洁数据' : '原始数据'}曲线（尚未应用模型输入归一化）；开始评估后显示评分结果。`
  );
  if (els.evalResultTable) els.evalResultTable.innerHTML = '';
  if (els.evalResultTableWrap) els.evalResultTableWrap.hidden = true;

  if (!state.evalSelectedDb || !state.evalSelectedTable) {
    if (els.evalChart) els.evalChart.innerHTML = '<div class="empty-state">选择测试数据表后显示测试数据曲线。</div>';
    if (els.evalResultMeta) els.evalResultMeta.textContent = '';
    return;
  }

  const selectedInfo = state.evalTables.find((item) => item.name === state.evalSelectedTable);
  const role = selectedInfo?.role_label || '测试集';
  const total = selectedInfo?.row_count;
  const loaded = Array.isArray(state.evalRows) ? state.evalRows.length : 0;
  const countText = Number.isFinite(Number(total)) && Number(total) > loaded
    ? `预览 ${loaded}/${total} 条`
    : `${loaded} 条`;
  if (els.evalResultMeta) {
    const label = state.evalDataView === 'clean' ? '清洁数据' : '原始数据';
    els.evalResultMeta.textContent = `[${role}] ${state.evalSelectedTable} · ${countText} · ${label}`;
  }
  renderSourcePreviewChart(state.evalRows || [], els.evalChart, state.evalDataView);
}

function showEvaluationResultVisualization() {
  setResultPanelVisible(els.evalResultPanel, true);
  setEvaluationVisualizationText(
    '评估结果',
    '评估结果已在测试数据预览区域原位更新；曲线展示算法评分/阈值，可点击右上角“全屏”查看细节。'
  );
  if (els.evalResultTableWrap) els.evalResultTableWrap.hidden = false;
}

function renderEvalResultTable(rows, scores, model, tableName, decisionThresholds = null, predLabels = null) {
  if (!Array.isArray(rows) || !rows.length) {
    renderEvaluationSourcePreview();
    return;
  }

  showEvaluationResultVisualization();
  renderResultTable(rows, scores, els.evalResultTable, els.evalResultMeta, tableName, decisionThresholds, predLabels);
  const modelName = model?.model_name || '未知模型';
  const modelId = model?.id || '-';
  const tableInfo = tableName || '未知表';
  const recordCount = rows.length;

  els.evalResultMeta.innerHTML = `
    <span class="eval-meta-item">模型：${modelName} (ID: ${modelId})</span>
    <span class="eval-meta-item">数据表：${tableInfo}</span>
    <span class="eval-meta-item">记录数：${recordCount} 条</span>
  `;
  setResultPanelVisible(els.evalResultPanel, true);
}

async function loadDatabases() {
  const payload = await api('/api/databases');
  state.databases = payload.items || [];
  const preferredDb = state.databases.find((item) => item.preferred_table === 'history_data') || state.databases[0] || null;
  renderSelectOptions(els.dbSelect, state.databases, preferredDb ? preferredDb.value : null);
  renderSelectOptions(els.evalDbSelect, state.databases, preferredDb ? preferredDb.value : null);
  state.selectedDb = preferredDb;
  if (state.selectedDb) {
    await loadDbTables();
    await loadEvalTables();
  }
}

async function loadDbTables() {
  const revision = ++state.trainTableRevision;
  state.selectedDb = state.databases.find((item) => item.value === els.dbSelect.value) || state.databases[0] || null;
  if (!state.selectedDb) {
    state.tables = [];
    state.testCatalog = [];
    state.rows = [];
    state.selectedTrainTable = null;
    state.selectedTestTable = null;
    renderSelectOptions(els.trainTableSelect, [], '');
    renderSelectOptions(els.testTableSelect, [], '');
    renderSelectOptions(els.tableSelect, [], null);
    renderSelectOptions(els.modelFilter, [{ label: '全部型号', value: '__ALL__' }], '__ALL__');
    renderSelectOptions(els.subsystemFilter, [{ label: '全部分系统', value: '__ALL__' }], '__ALL__');
    renderSelectOptions(els.deviceFilter, [{ label: '全部单机', value: '__ALL__' }], '__ALL__');
    renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }], '__ALL__');
    return;
  }

  const payload = await api(`/api/sqlite-tables?db=${encodeURIComponent(state.selectedDb.value)}`);
  if (revision !== state.trainTableRevision) return;
  const allTables = payload.items || [];
  state.tables = validTrainingTables(allTables);
  state.testCatalog = validTestTables(allTables);
  updateTrainTestOptions(allTables);

  state.filters = { model: '__ALL__', subsystem: '__ALL__', device: '__ALL__', variable: '__ALL__' };

  try {
    const hierarchyPayload = await api(`/api/hierarchy-options?db=${encodeURIComponent(state.selectedDb.value)}`);
    if (revision !== state.trainTableRevision) return;
    renderSelectOptions(els.modelFilter, [{ label: '全部型号', value: '__ALL__' }, ...hierarchyPayload.models.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.subsystemFilter, [{ label: '全部分系统', value: '__ALL__' }, ...hierarchyPayload.subsystems.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.deviceFilter, [{ label: '全部单机', value: '__ALL__' }, ...hierarchyPayload.devices.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }, ...hierarchyPayload.variables.map((value) => ({ label: value, value }))], '__ALL__');
  } catch (error) {
    console.error('加载筛选选项失败:', error);
  }

  if (revision !== state.trainTableRevision) return;
  const preferredTable = state.tables[0]?.name || '';
  renderSelectOptions(els.tableSelect, state.tables.map((item) => ({ label: formatRoleTableOption(item), value: item.name })), preferredTable);
  state.selectedTable = preferredTable;
  invalidateTrainingResult();
  if (!state.tables.length && els.runOutput) {
    els.runOutput.textContent = '当前数据库没有可用于训练的数据表。训练页只显示“仅正常数据”且训练集校验通过的表。';
  }

  state.dataView = 'raw';
  els.dataViewRadios.forEach((radio) => {
    radio.checked = radio.value === 'raw';
  });
  state.preprocessingStatus = null;

  await loadTableRows();
}

async function loadEvalTables() {
  const revision = ++state.evalTableRevision;
  const requestedDb = els.evalDbSelect?.value || '';
  const requestedModel = state.selectedModel?.id || null;
  state.evalSelectedDb = state.databases.find((item) => item.value === requestedDb) || null;
  state.extraEvaluationTables = [];
  state.evalInputSources = null;
  state.evalCandidates = [];
  if (!state.evalSelectedDb) {
    state.evalTables = [];
    state.evalSelectedTable = '';
    state.evalRows = [];
    renderSelectOptions(els.evalTableSelect, [], '');
    renderAdditionalEvalTables();
    resetEvaluationResultView('当前没有可用的测试数据库。');
    return;
  }

  invalidateEvaluationResult('正在匹配模型与测试数据表…');
  try {
    const tableRequest = api(`/api/sqlite-tables?db=${encodeURIComponent(requestedDb)}`);
    const sourceRequest = requestedModel
      ? api(`/api/model-input-sources?model_id=${encodeURIComponent(requestedModel)}`)
      : Promise.resolve(null);
    const [payload, sourceInfo] = await Promise.all([tableRequest, sourceRequest]);
    if (revision !== state.evalTableRevision || requestedModel !== (state.selectedModel?.id || null)) return;
    state.evalTables = validTestTables(payload.items || []);
    state.evalInputSources = sourceInfo;
    let candidates = state.evalTables;
    let preferred = [];
    let selectionMessage = '';
    if (requestedModel && sourceInfo?.complete) {
      const sources = sourceInfo.sources || [];
      const used = new Set();
      for (const source of sources) {
        const eligible = candidates.filter((table) => !used.has(table.name) && matchesSourceIdentity(table, source.identity));
        const preferredName = String(source.table || '').replace(/train/ig, 'test');
        const selected = eligible.find((table) => table.name === preferredName) || eligible[0];
        if (!selected) break;
        preferred.push(selected.name);
        used.add(selected.name);
      }
      selectionMessage = preferred.length === sources.length
        ? `已按模型参量身份匹配 ${preferred.length} 张测试表；请核对数据集和时间范围。`
        : `模型需要 ${sources.length} 张测试表，已自动匹配 ${preferred.length} 张。可手动选择其他参量，系统会记录身份差异。`;
    } else if (requestedModel) {
      selectionMessage = '旧模型缺少可核验的参量身份，暂仅按测试集标签筛选。请核对测试表。';
    }
    state.evalCandidates = candidates;
    const selectedTable = preferred[0] || (!requestedModel ? candidates[0]?.name : null) || '';
    renderSelectOptions(els.evalTableSelect, candidates.map((item) => ({
      label: formatRoleTableOption(item), value: item.name,
    })), selectedTable);
    if (!selectedTable && candidates.length) {
      // A model with no identity match must not visually imply that an
      // arbitrary first table has been deliberately selected by the user.
      els.evalTableSelect.prepend(new Option('请选择测试数据表', ''));
      els.evalTableSelect.value = '';
    }
    state.evalSelectedTable = selectedTable;
    state.extraEvaluationTables = preferred.slice(1);
    renderAdditionalEvalTables();
    if (els.evalOutput && selectionMessage) els.evalOutput.textContent = selectionMessage;
    if (sourceInfo?.complete) {
      state.evalDataView = sourceInfo.data_view || 'raw';
      els.evalDataViewRadios.forEach((radio) => { radio.checked = radio.value === state.evalDataView; });
    }
    if (!selectedTable) {
      state.evalRows = [];
      resetEvaluationResultView('请选择有效测试表，或手动配置测试特征顺序。');
      return;
    }
    await loadEvalTableRows();
  } catch (error) {
    if (revision !== state.evalTableRevision) return;
    state.evalTables = [];
    state.evalCandidates = [];
    state.evalSelectedTable = '';
    state.evalRows = [];
    renderSelectOptions(els.evalTableSelect, [], '');
    renderAdditionalEvalTables();
    if (els.evalOutput) els.evalOutput.textContent = `加载测试表失败：${error.message}`;
    resetEvaluationResultView('测试表加载失败，请重试。');
  }
}

async function loadEvalTableRows() {
  const currentTable = els.evalTableSelect?.value || '';
  if (currentTable !== state.evalSelectedTable) {
    state.evalSelectedTable = currentTable;
  }

  if (!state.evalSelectedDb || !state.evalSelectedTable) {
    state.evalRows = [];
    renderEvaluationSourcePreview();
    return;
  }

  const requestId = ++state.evalPreviewRequestId;
  const requestedDb = state.evalSelectedDb.value;
  const requestedTable = state.evalSelectedTable;
  const requestedView = state.evalDataView;
  const requestIsCurrent = () => (
    requestId === state.evalPreviewRequestId
    && state.evalSelectedDb?.value === requestedDb
    && state.evalSelectedTable === requestedTable
    && state.evalDataView === requestedView
  );

  if (els.evalChart) {
    els.evalChart.innerHTML = '<div class="empty-state">正在加载测试数据预览...</div>';
  }

  try {
    let physicalTable = requestedTable;
    if (requestedView === 'clean') {
      const status = await api(
        `/api/preprocessing/status?db=${encodeURIComponent(requestedDb)}&table=${encodeURIComponent(requestedTable)}`
      );
      if (!requestIsCurrent()) return;
      state.evalCleanStatus = status;
      if (status.status !== 'valid') {
        state.evalRows = [];
        if (els.evalCleanStatus) els.evalCleanStatus.textContent = `清洁数据不可用（${status.status}），请先执行预处理。`;
        renderEvaluationSourcePreview();
        if (els.evalChart) els.evalChart.innerHTML = `<div class="empty-state">清洁数据不可用（${status.status}），请先执行预处理。</div>`;
        return;
      }
      physicalTable = status.clean_table || `${requestedTable}_clean`;
    }
    const payload = await api(
      `/api/sqlite-rows?db=${encodeURIComponent(requestedDb)}&table=${encodeURIComponent(physicalTable)}&limit=1200`
    );
    if (!requestIsCurrent()) return;
    state.evalRows = payload.rows || [];
    if (els.evalCleanStatus) els.evalCleanStatus.textContent = requestedView === 'clean'
      ? '清洁表有效；开始评估前还将检查模型训练时的输入视图。'
      : '当前选择原始数据；评估时将检查模型训练输入契约。';
    renderEvaluationSourcePreview();
  } catch (error) {
    if (!requestIsCurrent()) return;
    state.evalRows = [];
    console.error('加载评估数据预览失败:', error);
    setResultPanelVisible(els.evalResultPanel, true);
    setEvaluationVisualizationText(
      '评估数据预览 · 测试数据',
      '测试数据预览加载失败；请检查数据库和测试表后重试。'
    );
    if (els.evalChart) {
      els.evalChart.innerHTML = `<div class="empty-state">测试数据预览加载失败：${error.message}</div>`;
    }
    if (els.evalResultMeta) {
      els.evalResultMeta.textContent = `[测试集] ${requestedTable} · 预览加载失败`;
    }
  }
}

const RUNNER_META_COLUMNS = new Set(['id','timestamp','label','model','subsystem','device','variable',
  'fault_id','fault_description','fault_severity','affected_component','fault_log_text',
  'normal_start_time','normal_end_time']);
function numericRunnerColumns(rows) {
  if (!rows?.length) return [];
  return Object.keys(rows[0]).filter(col => !RUNNER_META_COLUMNS.has(col.toLowerCase()) &&
    !/^(?:time|date|.*_time|.*_id)$/i.test(col) && rows.some(row =>
      row[col] !== null && row[col] !== '' && Number.isFinite(Number(row[col]))));
}
function renderTrainingFeatures() {
  // 每个业务表只用 value，模型维数来自不同数据表而不是同表的额外列。
  state.selectedFeatures = ['value'];
  if (els.trainFeatureList) els.trainFeatureList.replaceChildren();
  renderAdditionalTrainingTables();
}
function renderAdditionalTrainingTables() {
  if (!els.additionalTrainTables) return;
  const candidates = state.tables.filter(table => table.name !== state.selectedTable &&
    table.is_single_measurement !== false && table.data_role === 'train' && table.role_valid !== false &&
    ['timestamp','value','label'].every(col => (table.columns || []).includes(col)));
  state.extraTrainingTables = state.extraTrainingTables.filter(name => candidates.some(table => table.name === name));
  els.additionalTrainTables.replaceChildren();
  const query = els.trainTablesSearch?.value.trim().toLocaleLowerCase() || '';
  if (els.trainSelectionSummary) {
    const sources = [state.selectedTable, ...state.extraTrainingTables].filter(Boolean);
    els.trainSelectionSummary.textContent = sources.length === 1
      ? `当前为单表训练：${sources[0]}`
      : `${sources.length} 维输入 · 顺序：${sources.map((name, i) => `${i + 1}. ${name}`).join(' → ')}`;
  }
  for (const table of candidates) {
    if (query && !table.name.toLocaleLowerCase().includes(query)) continue;
    const label=document.createElement('label'), check=document.createElement('input');
    check.type='checkbox';check.value=table.name;
    check.checked=state.extraTrainingTables.includes(table.name);
    check.addEventListener('change',()=>{
      state.extraTrainingTables = check.checked ? [...state.extraTrainingTables,table.name]
        : state.extraTrainingTables.filter(name=>name!==table.name);
      invalidateTrainingResult();
      renderAdditionalTrainingTables();
    });
    label.append(check,document.createTextNode(table.name));
    els.additionalTrainTables.append(label);
  }
  if (!candidates.length) els.additionalTrainTables.textContent='没有可选的附加正常训练表';
  else if (!els.additionalTrainTables.childElementCount) els.additionalTrainTables.textContent='没有匹配的训练表；清空搜索词查看全部';
  refreshAlignmentPreview();
}
let previewRevision=0;
function alignmentQuery() {
  const params = new URLSearchParams({db: state.selectedDb.value});
  [state.selectedTable, ...state.extraTrainingTables].forEach(name => params.append('table', name));
  const start = els.trainTimeStart?.value.trim(), end = els.trainTimeEnd?.value.trim();
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  params.set('tolerance', els.alignmentTolerance?.value || '0');
  params.set('method', els.alignmentMethod?.value || 'nearest');
  params.set('timeline', els.timelineMode?.value || 'anchor');
  params.set('interval', els.timelineMode?.value === 'fixed' ? (els.gridIntervalSeconds?.value || '0') : '0');
  params.set('label_method', els.labelAlignmentMethod?.value || 'matched');
  return params;
}
function renderAlignmentQuality(result) {
  const coverage = result.coverage || {};
  const coverageNumbers = Object.values(coverage).map(Number);
  const lowest = Math.round(Math.min(...coverageNumbers) * 100);
  const tolerance = Number(result.tolerance_seconds || 0);
  const header = document.createElement('div'); header.className = 'alignment-report-head';
  header.textContent = `${result.aligned_count} / ${result.grid_count || result.aligned_count} 个时间点 · ${result.tables.length} 维 · 原始观测最低利用率 ${lowest}%`;
  const sub = document.createElement('p');
  sub.textContent = tolerance
    ? `${result.alignment_method === 'previous' ? '只用过去观测的样本保持' : '一对一最近时间匹配'}（最大偏差 ${tolerance}秒）；主表未匹配 ${result.unmatched_anchor_count || 0} 条。`
    : `严格匹配相同时间戳；主表未匹配 ${result.unmatched_anchor_count || 0} 条。`;
  sub.textContent += `；时间轴：${result.timeline_mode === 'fixed' ? `固定 ${result.grid_interval_seconds} 秒` : '第一张表'}；标签：${result.label_alignment_method === 'interval' ? '标注事件区间' : (result.label_alignment_method === 'previous' ? '上一有效标签' : '匹配观测标签')}`;
  const list = document.createElement('div'); list.className = 'alignment-report-list';
  result.tables.forEach((table, i) => {
    const row = document.createElement('div'); row.className = 'alignment-report-row';
    const text = document.createElement('span');
    text.textContent = `${i + 1}. ${table}`; text.title = table;
    const metric = document.createElement('strong');
    const max = result.offsets?.[table]?.max_seconds || 0;
    metric.textContent = `${Math.round(Number(coverage[table]) * 100)}% · ${result.source_counts[table]} 条${tolerance ? ` · 最大偏差 ${max}s` : ''}`;
    row.append(text, metric); list.append(row);
  });
  const note = document.createElement('p'); note.className = 'alignment-report-note';
  const gridCoverage = Number(result.grid_coverage ?? 0);
  note.textContent = gridCoverage < 0.8
    ? `统一时间轴有效率 ${Math.round(gridCoverage * 100)}%（低于 80%），建议调整时间范围、容差或采样周期。`
    : `统一时间轴有效率 ${Math.round(gridCoverage * 100)}%；训练时仍会重新核验。`;
  if (result.label_alignment_method === 'interval') note.textContent += ` 未标注区间排除 ${result.unknown_label_count || 0} 个已匹配采样点；请到历史故障数据库中的‘故障事件’补全标注。`;
  if (gridCoverage < 0.8) note.classList.add('alignment-warning');
  if (lowest < 80) note.textContent += ' 部分低频参量会重复使用历史观测，原始采样利用率低不代表无效。';
  if (state.dataView === 'clean') note.textContent += ' 当前预览使用原始表，清洁数据实际样本量可能不同。';
  els.trainAlignmentPreview.replaceChildren(header, sub, list, note);
  if (result.preview_rows?.length) {
    const details = document.createElement('details'); details.className='compact-details';
    const heading = document.createElement('summary'); heading.textContent='查看临时联合样本（含各表实际匹配时刻）';
    const wrap=document.createElement('div'); wrap.className='table-wrap';
    const grid=document.createElement('table');
    const columns=['timestamp', ...(result.feature_columns||[]), ...result.tables.map((_,i)=>`source_timestamp_${i+1}`), ...result.tables.map((_,i)=>`source_label_${i+1}`), 'label'];
    const thead=document.createElement('thead'), tr=document.createElement('tr');
    columns.forEach(name=>{const th=document.createElement('th');th.textContent=name;tr.append(th);});
    thead.append(tr);grid.append(thead);
    const tbody=document.createElement('tbody');
    result.preview_rows.forEach(row=>{
      const record=document.createElement('tr');
      columns.forEach(name=>{const cell=document.createElement('td');cell.textContent=String(row[name]??'—');record.append(cell);});
      tbody.append(record);
    });
    grid.append(tbody);wrap.append(grid);details.append(heading,wrap);
    els.trainAlignmentPreview.append(details);
  }
}
async function refreshAlignmentPreview() {
  if (!els.trainAlignmentPreview) return null;
  const revision = ++previewRevision;
  if (!state.selectedDb || !state.selectedTable || !state.extraTrainingTables.length) {
    els.trainAlignmentPreview.textContent = '单表训练；添加其他训练表后，可以预览时间戳对齐质量。';
    return null;
  }
  els.trainAlignmentPreview.textContent = '正在检查时间同步与各表有效样本…';
  try {
    const result = await api(`/api/train-alignment-preview?${alignmentQuery()}`);
    if (revision !== previewRevision) return null;
    renderAlignmentQuality(result);
    return result;
  } catch (error) {
    if (revision !== previewRevision) return null;
    els.trainAlignmentPreview.textContent = `无法对齐：${error.message}。请调整时间范围、容差或参量组合。`;
    return null;
  }
}
function showEvaluationDomainDifferences() {
  if (!els.evalDomainHint) return;
  const sources = state.evalInputSources?.sources || [];
  const selected = [state.evalSelectedTable, ...state.extraEvaluationTables].filter(Boolean);
  if (!sources.length || !selected.length) {
    els.evalDomainHint.textContent = '可自由选择目标参量，系统会自动标记源域与目标域的差异。';
    return;
  }
  const differences = selected.flatMap((name,index) => {
    const source = sources[index];
    if (!source) return [`第 ${index+1} 个输入超出模型维度`];
    const table = state.evalCandidates.find(item => item.name === name);
    if (!source.identity || !table?.hierarchy?.length) return [`第 ${index+1} 个输入身份未知`];
    return matchesSourceIdentity(table,source.identity) ? []
      : [`${index+1}. ${source.table} → ${name}`];
  });
  els.evalDomainHint.textContent = differences.length
    ? `源/目标身份存在差异：${differences.join('；')}。系统允许评估并自动记录差异；请按目标数据解释结果。`
    : '已选择的数据与已核验模型输入身份一致；系统仍会检查数据及预处理兼容性。';
}

function renderAdditionalEvalTables() {
  if (!els.additionalEvalTables) return;
  const candidates=state.evalCandidates.filter(table=>table.name!==state.evalSelectedTable);
  state.extraEvaluationTables=state.extraEvaluationTables.filter(name=>candidates.some(table=>table.name===name));
  els.additionalEvalTables.replaceChildren();
  const query = els.evalTablesSearch?.value.trim().toLocaleLowerCase() || '';
  const tables = [state.evalSelectedTable, ...state.extraEvaluationTables].filter(Boolean);
  showEvaluationDomainDifferences();
  if (els.evalSelectionSummary) {
    els.evalSelectionSummary.textContent = tables.length > 1
      ? `${tables.length} 张测试表 · 输入顺序：${tables.map((name, i)=>`${i + 1}. ${name}`).join(' → ')}`
      : `主测试表：${tables[0] || '尚未选择'} · 多表模型还需选择其余测试表`;
  }
  for (const table of candidates) {
    if (query && !table.name.toLocaleLowerCase().includes(query)) continue;
    const label=document.createElement('label'),check=document.createElement('input');
    check.type='checkbox';check.value=table.name;
    check.checked=state.extraEvaluationTables.includes(table.name);
    check.addEventListener('change',()=>{
      state.extraEvaluationTables=check.checked?[...state.extraEvaluationTables,table.name]
        :state.extraEvaluationTables.filter(name=>name!==table.name);
      invalidateEvaluationResult('测试表组合已改变，请重新执行评估。');
      renderAdditionalEvalTables();
    });
    label.append(check,document.createTextNode(table.name));
    els.additionalEvalTables.append(label);
  }
  if (!candidates.length) els.additionalEvalTables.textContent='没有可选的附加测试表';
  else if (!els.additionalEvalTables.childElementCount) els.additionalEvalTables.textContent='没有匹配的测试表';
}
async function refreshTrainTimeRange() {
  if (!els.trainTimeColumn) return;
  const columns = Object.keys(state.rows[0] || {});
  const candidates = columns.filter(col => /^(timestamp|time|date|.*_time)$/i.test(col));
  const previous = els.trainTimeColumn.value;
  const chosen = candidates.includes(previous) ? previous : (candidates.includes('timestamp') ? 'timestamp' : candidates[0]);
  renderSelectOptions(els.trainTimeColumn, candidates.map(col => ({label:col,value:col})), chosen || '');
  state.trainTimeColumn = chosen || '';
  state.availableTrainTime = null;
  if (!state.selectedDb || !state.selectedTable || !chosen) {
    els.trainAvailableRange.textContent = '当前表缺少时间列（可按比例划分，但不能按时间筛选）';
    return;
  }
  try {
    const query = new URLSearchParams({db:state.selectedDb.value, table:state.selectedTable, time_col:chosen});
    const info = await api(`/api/sqlite-time-range?${query}`);
    if (state.selectedTable !== query.get('table')) return;
    state.availableTrainTime = info;
    els.trainAvailableRange.textContent = `完整范围：${info.min ?? '-'} → ${info.max ?? '-'}（${info.total_count || 0} 条）`;
  } catch (err) { els.trainAvailableRange.textContent = `读取时间范围失败：${err.message}`; }
}
async function loadTableRows() {
  const currentTable = els.tableSelect?.value || '';
  if (currentTable !== state.selectedTable) {
    state.selectedTable = currentTable;
  }
  actionLogger.log('TABLE_SELECT', '数据表选择', {
    db: state.selectedDb?.value,
    table: state.selectedTable,
  });

  if (!state.selectedDb || !state.selectedTable) {
    state.rows = [];
    actionLogger.log('INFO', '未选择数据表，返回空数据');
    renderTrainingSourcePreview();
    return;
  }

  actionLogger.log('PREPROCESSING_MODULE', '触发显示预处理模块', {
    table: state.selectedTable,
    visible: true,
  });
  updatePreprocessingModuleVisibility();

  const requestId = ++state.previewRequestId;
  const requestedDb = state.selectedDb.value;
  const requestedTable = state.selectedTable;
  const requestedView = state.dataView;
  const requestIsCurrent = () => (
    requestId === state.previewRequestId
    && state.selectedDb?.value === requestedDb
    && state.selectedTable === requestedTable
    && state.dataView === requestedView
  );

  if (els.chart) {
    const loadingLabel = requestedView === 'clean' ? '清洁数据' : '原始数据';
    els.chart.innerHTML = `<div class="empty-state">正在加载${loadingLabel}预览...</div>`;
  }

  try {
    if (requestedView === 'clean') {
      const status = await checkPreprocessingStatus(requestedDb, requestedTable);
      if (!requestIsCurrent()) return;
      if (status?.status !== 'valid') {
        state.rows = [];
        renderTrainingSourcePreview();
        return;
      }

      const cleanTable = status.clean_table || status.clean_table_name || `${requestedTable}_clean`;
      const payload = await api(
        `/api/sqlite-rows?db=${encodeURIComponent(requestedDb)}&table=${encodeURIComponent(cleanTable)}&limit=1200`
      );
      if (!requestIsCurrent()) return;
      state.rows = payload.rows || [];
    } else {
      const payload = await api(
        `/api/sqlite-rows?db=${encodeURIComponent(requestedDb)}&table=${encodeURIComponent(requestedTable)}&limit=1200`
      );
      if (!requestIsCurrent()) return;
      state.rows = payload.rows || [];
    }
  } catch (error) {
    if (!requestIsCurrent()) return;
    state.rows = [];
    console.error(`加载${requestedView === 'clean' ? '清洁' : '原始'}数据失败:`, error);
    if (els.chart) {
      els.chart.innerHTML = `<div class="empty-state">数据预览加载失败：${error.message}</div>`;
    }
    if (els.resultMeta) {
      const selectedInfo = state.tables.find((item) => item.name === state.selectedTable);
      const role = selectedInfo?.role_label || '训练集';
      const viewLabel = requestedView === 'clean' ? '清洁数据' : '原始数据';
      els.resultMeta.textContent = `[${role}] ${state.selectedTable} · ${viewLabel}加载失败`;
    }
    return;
  }

  renderTrainingFeatures();
  await refreshTrainTimeRange();
  renderTrainingSourcePreview();
}

function bindFilter(selectEl, key) {
  selectEl.addEventListener('change', async (event) => {
    state.filters[key] = event.target.value;

    if (state.selectedDb) {
      try {
        const model = state.filters.model === '__ALL__' ? null : state.filters.model;
        const subsystem = state.filters.subsystem === '__ALL__' ? null : state.filters.subsystem;
        const device = state.filters.device === '__ALL__' ? null : state.filters.device;
        const variable = state.filters.variable === '__ALL__' ? null : state.filters.variable;

        const payload = await api(`/api/sqlite-tables?db=${encodeURIComponent(state.selectedDb.value)}&model=${encodeURIComponent(model || '')}&subsystem=${encodeURIComponent(subsystem || '')}&device=${encodeURIComponent(device || '')}&variable=${encodeURIComponent(variable || '')}`);
        state.tables = validTrainingTables(payload.items || []);

        const preferredTable = state.tables[0]?.name || '';
        renderSelectOptions(els.tableSelect, state.tables.map((item) => ({ label: formatRoleTableOption(item), value: item.name })), preferredTable);
        state.selectedTable = preferredTable;
        renderAdditionalTrainingTables();
        invalidateTrainingResult();
        if (!state.tables.length && els.runOutput) {
          els.runOutput.textContent = '当前筛选条件下没有符合“仅正常数据”要求的训练集表。';
        }

        const hierarchyPayload = await api(`/api/hierarchy-options?db=${encodeURIComponent(state.selectedDb.value)}&model=${encodeURIComponent(model || '')}&subsystem=${encodeURIComponent(subsystem || '')}&device=${encodeURIComponent(device || '')}`);

        if (key === 'model') {
          renderSelectOptions(els.subsystemFilter, [{ label: '全部分系统', value: '__ALL__' }, ...hierarchyPayload.subsystems.map((value) => ({ label: value, value }))], '__ALL__');
          state.filters.subsystem = '__ALL__';
          renderSelectOptions(els.deviceFilter, [{ label: '全部单机', value: '__ALL__' }], '__ALL__');
          state.filters.device = '__ALL__';
          renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }], '__ALL__');
          state.filters.variable = '__ALL__';
        } else if (key === 'subsystem') {
          renderSelectOptions(els.deviceFilter, [{ label: '全部单机', value: '__ALL__' }, ...hierarchyPayload.devices.map((value) => ({ label: value, value }))], '__ALL__');
          state.filters.device = '__ALL__';
          renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }], '__ALL__');
          state.filters.variable = '__ALL__';
        } else if (key === 'device') {
          renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }, ...hierarchyPayload.variables.map((value) => ({ label: value, value }))], '__ALL__');
          state.filters.variable = '__ALL__';
        }

        await loadTableRows();
      } catch (error) {
        console.error('加载表列表失败:', error);
      }
    }
  });
}

async function runTraining() {
  // 保留当前源数据曲线，训练完成后直接在同一图表区域更新结果。
  invalidateTrainingResult();
  renderTrainingSourcePreview();

  if (!state.selectedDb) {
    els.runOutput.textContent = '请先选择训练数据库。';
    showToast('请先选择训练数据库。', 'error');
    return;
  }

  const algorithm = els.algorithmSelect.value;
  if (!algorithm) {
    els.runOutput.textContent = '请先选择检测算法。';
    showToast('请先选择检测算法。', 'error');
    return;
  }

  const currentSourceTable = els.tableSelect?.value || state.selectedTable || '';
  state.selectedTable = currentSourceTable;

  if (state.evaluationMode === 'rigorous') {
    if (!state.selectedTrainTable) {
      els.runOutput.textContent = '请先选择训练集表。';
      showToast('请先选择训练集表。', 'error');
      return;
    }
    if (!state.selectedTestTable) {
      els.runOutput.textContent = '请先选择测试集表。';
      showToast('请先选择测试集表。', 'error');
      return;
    }
  } else if (!state.selectedTable) {
    els.runOutput.textContent = '请先选择训练数据表。';
    showToast('请先选择训练数据表。', 'error');
    return;
  } else {
    const selectedInfo = state.tables.find((item) => item.name === state.selectedTable);
    if (!selectedInfo || selectedInfo.data_role !== 'train' || selectedInfo.role_valid === false) {
      const message = '当前数据表不是有效训练集。训练只能使用“仅正常数据”且校验通过的训练集表。';
      els.runOutput.textContent = message;
      showToast(message, 'error');
      return;
    }
  }

  const useCleanData = state.dataView === 'clean';
  const pipelineConfig = useCleanData ? getPipelineConfig() : null;
  const algorithmParams = getAlgorithmParams();
  
  if (!state.selectedFeatures.length) {
    els.runOutput.textContent = '请至少选择一个有效数值特征列。';
    return;
  }
  const from = els.trainTimeStart?.value.trim() || null;
  const to = els.trainTimeEnd?.value.trim() || null;
  if (state.evaluationMode === 'simple' && (from || to) && !state.trainTimeColumn) {
    els.runOutput.textContent = '所选数据表没有可用的时间戳列。';
    return;
  }
  if (state.evaluationMode === 'simple' && state.extraTrainingTables.length) {
    const preview = await refreshAlignmentPreview();
    if (!preview) {
      const message = '多表对齐检查未通过，请先调整表组合、时间范围或容差。';
      els.runOutput.textContent = message;
      showToast(message, 'error');
      return;
    }
    const gridCoverage = Number(preview.grid_coverage ?? 0);
    if (gridCoverage < 0.8 && !window.confirm(
      `统一时间轴只有 ${Math.round(gridCoverage * 100)}% 的时间点得到有效匹配（${preview.aligned_count} 条）。确认继续训练吗？`
    )) return;
  }
  const config = {
    alignmentToleranceSeconds: state.evaluationMode === 'simple' && state.extraTrainingTables.length
      ? Number(els.alignmentTolerance?.value || 0) : 0,
    alignmentMethod: els.alignmentMethod?.value || 'nearest',
    timelineMode: els.timelineMode?.value || 'anchor',
    gridIntervalSeconds: els.timelineMode?.value === 'fixed' ? Number(els.gridIntervalSeconds?.value || 0) : 0,
    labelAlignmentMethod: els.labelAlignmentMethod?.value || 'matched',
    featureColumns: ['value'],
    sourceTables: state.evaluationMode === 'simple' && state.extraTrainingTables.length
      ? [state.selectedTable, ...state.extraTrainingTables] : null,
    trainTimeColumn: state.evaluationMode === 'simple' ? state.trainTimeColumn : null,
    trainTimeStart: state.evaluationMode === 'simple' ? from : null,
    trainTimeEnd: state.evaluationMode === 'simple' ? to : null,
    dataset: state.selectedDb.value,
    table: currentSourceTable,
    algorithm: algorithm,
    algorithmParams: algorithmParams,
    trainingStrategy: els.trainingStrategySelect?.value || 'standard',
    evaluationMode: state.evaluationMode,
    trainTable: state.evaluationMode === 'rigorous' ? state.selectedTrainTable : null,
    testTable: state.evaluationMode === 'rigorous' ? state.selectedTestTable : null,
    trainRatio: state.evaluationMode === 'simple' ? parseFloat(state.trainRatio) : 1.0,
    model: state.filters.model === '__ALL__' ? null : state.filters.model,
    subsystem: state.filters.subsystem === '__ALL__' ? null : state.filters.subsystem,
    device: state.filters.device === '__ALL__' ? null : state.filters.device,
    variable: state.filters.variable === '__ALL__' ? null : state.filters.variable,
    useCleanData: useCleanData,
    pipelineConfig: pipelineConfig,
    normalizationMethod: els.inputNormalizationMethod?.value || 'min_max',
  };

  try {
    els.runOutput.textContent = '正在提交训练任务...';
    if (els.trainingLog) {
      const preEl = els.trainingLog.querySelector('pre');
      if (preEl) {
        preEl.textContent = '正在提交训练任务...\n';
      }
      els.trainingLog.style.display = 'block';
    }

    const submitPayload = await api('/api/train-model', {
      method: 'POST',
      body: JSON.stringify(config),
    });

    if (submitPayload.status !== 'accepted' || !submitPayload.task_id) {
      throw new Error('任务提交失败：' + (submitPayload.error || '未知错误'));
    }

    const taskId = submitPayload.task_id;
    console.log(`[训练] 任务已提交，task_id=${taskId}`);
    
    appendLog(`任务已提交：${taskId}`);
    
    await pollTrainingStatus(taskId, config);
    
  } catch (error) {
    console.error('训练失败:', error);
    els.runOutput.textContent = `训练失败：${error.message}`;
    appendLog(`训练失败：${error.message}`);
    showToast(`训练失败：${error.message}`, 'error');
  }
}

async function pollTrainingStatus(taskId, config) {
  const maxPolls = 600;
  const pollInterval = 1000;
  let pollCount = 0;

  const progressBar = getOrCreateProgressBar();
  
  while (pollCount < maxPolls) {
    pollCount++;
    
    try {
      const statusPayload = await api(`/api/train-status?task_id=${encodeURIComponent(taskId)}`);
      
      const progress = statusPayload.progress || 0;
      const message = statusPayload.message || '执行中...';
      const status = statusPayload.status;
      
      updateProgressBar(progress, message);
      appendLog(`[进度:${progress}%] ${message}`);
      
      if (status === 'completed') {
        appendLog('训练完成！');
        await handleTrainingSuccess(statusPayload.result, config);
        return;
      }
      
      if (status === 'failed') {
        const errorMsg = statusPayload.error || '未知错误';
        appendLog(`训练失败：${errorMsg}`);
        els.runOutput.textContent = `训练失败：${errorMsg}`;
        showToast(`训练失败：${errorMsg}`, 'error', 5000);
        return;
      }
      
    } catch (error) {
      console.error('轮询状态失败:', error);
    }
    
    await sleep(pollInterval);
  }
  
  appendLog('训练超时');
  els.runOutput.textContent = '训练超时，请稍后查看结果';
}

function getOrCreateProgressBar() {
  let progressBar = document.getElementById('trainingProgressBar');
  if (!progressBar) {
    const container = document.createElement('div');
    container.id = 'progressContainer';
    container.className = 'training-progress';
    
    progressBar = document.createElement('div');
    progressBar.id = 'trainingProgressBar';
    progressBar.className = 'training-progress-bar';
    progressBar.style.width = '0%';
    
    container.appendChild(progressBar);
    
    if (els.trainingLog && els.trainingLog.parentNode) {
      els.trainingLog.parentNode.insertBefore(container, els.trainingLog);
    }
  }
  return progressBar;
}

function updateProgressBar(progress, message) {
  const progressBar = document.getElementById('trainingProgressBar');
  if (progressBar) {
    progressBar.style.width = `${progress}%`;
    progressBar.textContent = `${progress}% - ${message}`;
  }
}

function appendLog(message) {
  if (els.trainingLog) {
    const preEl = els.trainingLog.querySelector('pre');
    if (preEl) {
      preEl.textContent += message + '\n';
      preEl.scrollTop = preEl.scrollHeight;
    }
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function handleTrainingSuccess(result, config) {
  if (!result) {
    els.runOutput.textContent = '训练完成，但没有返回结果';
    return;
  }
  
  els.runOutput.textContent = `训练完成！实验ID：${result.experiment_id || '未知'}`;
  showToast('训练完成', 'success');
  
  const metrics = result.metrics || {};
  appendLog(`准确率: ${Number.isFinite(metrics.accuracy) ? (metrics.accuracy * 100).toFixed(2) + '%' : 'N/A'}`);
  appendLog(`总样本数: ${Number.isFinite(metrics.total_samples) ? metrics.total_samples : 'N/A'}`);
  appendLog(`异常样本: ${metrics.anomaly_count || 0}`);
  appendLog(`正常样本: ${Number.isFinite(metrics.normal_count) ? metrics.normal_count : 'N/A'}`);
  const trainContract = result.summary?.config?.data_contract || result.config?.data_contract || {};
  const trainScaling = trainContract.model_input_scaling || {};
  const trainView = trainContract.use_clean_data ? '清洁数据（清理后归一化）' : '原始数据（仅归一化）';
  if (trainScaling.enabled !== false) {
    appendLog(`输入视图: ${trainView}`);
    appendLog(`模型输入归一化: ${trainScaling.method === 'zscore' ? 'Z-Score' : 'Min-Max'}（参数仅由训练集拟合）`);
  }
  
  if (result.summary?.result) {
    const { rows, threshold, decision_threshold: decisionThreshold, decision_thresholds: decisionThresholds, pred_labels: predLabels, algorithm: resultAlgorithm } = result.summary.result;
    showTrainingResultVisualization();
    
    try {
      if (Array.isArray(rows) && rows.length) renderChart(rows, threshold, resultAlgorithm, els.chart, decisionThreshold, metrics, decisionThresholds, predLabels);
    } catch (chartError) {
      console.error('图表渲染失败:', chartError);
      appendLog(`图表渲染失败：${chartError.message}`);
    }
    
    try {
      renderResultTable(rows, threshold, els.resultTable, els.resultMeta, state.selectedTable, decisionThresholds, predLabels);
    } catch (tableError) {
      console.error('表格渲染失败:', tableError);
      appendLog(`表格渲染失败：${tableError.message}`);
    }
    
    state.lastEvaluationConfig = config;
    state.lastEvaluationResult = result.summary.result;
    state.lastTrainingResult = result;

    const extraData = {
      experiment_id: result.experiment_id,
      model_dir: result.model_dir || null,
      model_file_path: result.model_file_path || null,
      execution_receipt: result.execution_receipt,
    };
    const savedExperimentId = await saveExperimentAuto('training', extraData);
    if (!savedExperimentId) {
      appendLog('警告：训练结果已完成，但历史实验记录保存失败；仍可发布当前模型。');
      showToast('历史记录保存失败，请稍后刷新检查', 'error');
    } else {
      await loadTrainingHistory();
    }
    // 历史记录写入完成后再开放发布入口，避免“先发布、后保存”把已发布状态覆盖回未发布。
    showPublishModelPanel(result);
  }
}

function showPublishModelPanel(trainingResult) {
  if (!els.publishModelPanel) return;
  
  els.publishModelPanel.classList.add('show');
  
  if (els.modelPublishInfo) {
    const metrics = trainingResult.metrics || {};
    const modelInfo = `
      算法: ${trainingResult.algorithm || '未知'}<br>
      数据集: ${trainingResult.dataset || '未知'}<br>
      准确率: ${metrics.accuracy !== undefined ? metrics.accuracy.toFixed(4) : 'N/A'}
    `;
    els.modelPublishInfo.innerHTML = modelInfo;
  }
}

async function publishModel() {
  const modelName = els.modelNameInput.value.trim();
  if (!modelName) {
    els.runOutput.textContent = '请输入模型名称。';
    return;
  }

  if (!state.lastTrainingResult) {
    els.runOutput.textContent = '请先运行训练。';
    return;
  }

  els.publishModelBtn.textContent = '发布中...';
  els.publishModelBtn.disabled = true;

  try {
    const payload = await api('/api/models', {
      method: 'POST',
      body: JSON.stringify({
        experiment_id: state.lastTrainingResult.experiment_id,
        model_name: modelName,
        model_desc: els.modelDescInput.value.trim(),
        algorithm: state.lastTrainingResult.algorithm,
        dataset: state.lastTrainingResult.dataset,
        table_name: state.lastTrainingResult.table_name,
        file_path: state.lastTrainingResult.model_dir || state.lastTrainingResult.model_file_path,
        metrics: state.lastTrainingResult.metrics || {},
        config: state.lastTrainingResult.config || {},
      }),
    });

    els.runOutput.textContent = `模型发布成功！${payload.message}`;
    showToast('模型发布成功', 'success');
    els.publishModelPanel.classList.remove('show');
    els.modelNameInput.value = '';
    els.modelDescInput.value = '';
    
    await loadModels();
  } catch (error) {
    console.error('发布模型失败:', error);
    els.runOutput.textContent = `发布失败：${error.message}`;
    showToast(`发布失败：${error.message}`, 'error');
  } finally {
    els.publishModelBtn.textContent = '发布模型';
    els.publishModelBtn.disabled = false;
  }
}

async function loadModels() {
  try {
    const payload = await api('/api/models');
    state.models = (payload.models || []).filter((model) =>
      model.status === 'published' || model.status === 'evaluated'
    );
    
    renderModelSelect();
    renderModelCardList();
  } catch (error) {
    console.error('加载模型列表失败:', error);
    state.models = [];
  }
}

function renderModelSelect() {
  if (!els.modelSelect) return;
  
  const options = [{ label: '-- 请选择模型 --', value: '' }].concat(
    state.models.map((model) => ({
      label: `${model.model_name} (${model.algorithm})`,
      value: model.id,
    }))
  );
  
  renderSelectOptions(els.modelSelect, options, state.selectedModel?.id || '');
}

function renderModelCardList() {
  if (!els.modelCardList) return;
  
  if (state.models.length === 0) {
    els.modelCardList.innerHTML = '<div class="model-list-empty">暂无已发布的模型<br><br>请先在"训练"Tab中训练并发布模型</div>';
    els.modelCardList.style.display = 'block';
    return;
  }
  
  els.modelCardList.innerHTML = state.models.map((model) => {
    const metrics = model.metrics_json ? JSON.parse(model.metrics_json) : {};
    return `
      <div class="model-card" data-model-id="${model.id}">
        <div class="model-card-header">
          <span class="model-name">${model.model_name}</span>
          <span class="model-status ${model.status}">${model.status === 'evaluated' ? '已评估' : '已发布'}</span>
        </div>
        <div class="model-info-row">算法: ${model.algorithm}</div>
        <div class="model-info-row">数据集: ${model.dataset}</div>
        <div class="model-info-row">评估次数: ${model.evaluation_count}</div>
        <div class="model-metrics">
          ${metrics.accuracy !== undefined ? `<div class="metric-item"><span class="metric-label">准确率:</span> <span class="metric-value">${(metrics.accuracy * 100).toFixed(1)}%</span></div>` : ''}
          ${metrics.f1 !== undefined ? `<div class="metric-item"><span class="metric-label">F1:</span> <span class="metric-value">${metrics.f1.toFixed(4)}</span></div>` : ''}
        </div>
      </div>
    `;
  }).join('');
  
  els.modelCardList.style.display = 'block';
  
  els.modelCardList.querySelectorAll('.model-card').forEach((card) => {
    card.addEventListener('click', async () => {
      const modelId = parseInt(card.dataset.modelId);
      state.selectedModel = state.models.find((m) => m.id === modelId);
      els.modelSelect.value = modelId;
      state.lastEvaluationResult = null;
      state.lastEvaluationConfig = null;
      await loadEvalTables();
      
      els.modelCardList.querySelectorAll('.model-card').forEach((c) => c.classList.remove('active'));
      card.classList.add('active');
    });
  });
}

async function runEvaluation() {
  // 若上一轮已经显示评估结果，开始新一轮前恢复为当前测试数据预览。
  renderEvaluationSourcePreview();
  state.lastEvaluationResult = null;
  state.lastEvaluationConfig = null;
  const modelId = parseInt(els.modelSelect.value);
  if (!modelId) {
    els.evalOutput.textContent = '请先选择已发布的模型。';
    return;
  }

  state.selectedModel = state.models.find((m) => m.id === modelId);
  if (!state.selectedModel) {
    els.evalOutput.textContent = '选择的模型不存在。';
    return;
  }

  const selectedEvalTables = [state.evalSelectedTable, ...state.extraEvaluationTables].filter(Boolean);
  const eligibleNames = new Set(state.evalCandidates.map((item) => item.name));
  if (!selectedEvalTables.length || selectedEvalTables.some((name) => !eligibleNames.has(name))) {
    els.evalOutput.textContent = '请先选择与模型匹配的有效测试数据表。';
    return;
  }
  if (state.evalInputSources?.sources?.length &&
    selectedEvalTables.length !== state.evalInputSources.sources.length) {
    els.evalOutput.textContent = `模型需要 ${state.evalInputSources.sources.length} 张测试表；请选择相同数量并核对特征顺序。`;
    return;
  }
  showEvaluationDomainDifferences();
  els.evalOutput.textContent = '评估中，请稍候...';
  setEvaluationVisualizationText(
    '评估数据预览 · 评估中',
    '当前保留所选测试数据的原始物理量预览；评估完成后会在同一区域更新为评估结果曲线。'
  );
  if (els.evalLog) {
    const preEl = els.evalLog.querySelector('pre');
    if (preEl) {
      preEl.textContent = '正在加载测试数据并执行评估，请稍候...\n';
    }
    els.evalLog.style.display = 'block';
  }

  try {
    const config = {
      model_id: modelId,
      dataset: state.evalSelectedDb?.value || '',
      table: state.evalSelectedTable || els.evalTableSelect.value || '',
      tables: state.extraEvaluationTables.length
        ? [state.evalSelectedTable, ...state.extraEvaluationTables] : null,
      data_view: state.evalDataView,
    };

    const payload = await api('/api/evaluate-model', {
      method: 'POST',
      body: JSON.stringify(config),
    });

    els.evalOutput.textContent = `评估完成！状态：${payload.status || 'success'}`;
    const inputContract = payload.input_contract || {};
    const detectedDomain = inputContract.evaluation_mode || 'unverified_domain';
    const domainLabel = detectedDomain === 'cross_domain' ? '跨域数据组合' :
      detectedDomain === 'same_domain' ? '同源参量组合' : '参量身份待核验';
    const inputViewText = inputContract.use_clean_data ? '清洁数据（清理后归一化）' : '原始数据（仅归一化）';
    const scaling = inputContract.model_input_scaling || {};
    const scalingText = scaling.enabled === false
      ? '旧模型兼容：未启用统一输入归一化'
      : `${scaling.method === 'zscore' ? 'Z-Score' : 'Min-Max'}（复用训练期参数）`;
    displayEvalLog([
      '测试数据加载完成。',
      `输入视图：${inputViewText}`,
      `模型输入归一化：${scalingText}`,
      `评估参量关系：${domainLabel}（由系统根据实际数据自动识别）`,
      `模型评分完成：${state.selectedModel.model_name || state.selectedModel.algorithm || modelId}`,
      `测试表：${state.evalSelectedTable || els.evalTableSelect.value || '-'}`,
      `评估完成，状态：${payload.status || 'success'}`
    ].join('\n'));
    showToast('评估完成', 'success');

    if (payload.summary?.result) {
      const { rows, threshold, decision_threshold: decisionThreshold, decision_thresholds: decisionThresholds, pred_labels: predLabels, algorithm } = payload.summary.result;
      
      try {
        showEvaluationResultVisualization();
        if (Array.isArray(rows) && rows.length) renderChart(rows, threshold, algorithm, els.evalChart, decisionThreshold, payload.metrics || payload.summary?.result?.metrics || null, decisionThresholds, predLabels);
      } catch (chartError) {
        console.error('评估图表渲染失败:', chartError);
        els.evalOutput.textContent = `图表渲染失败：${chartError.message}`;
      }
      
      try {
        renderEvalResultTable(rows, threshold, state.selectedModel, state.evalSelectedTable || els.evalTableSelect.value, decisionThresholds, predLabels);
      } catch (tableError) {
        console.error('评估表格渲染失败:', tableError);
        els.evalOutput.textContent = `表格渲染失败：${tableError.message}`;
      }
      
      // Persist the server-classified domain relationship with the experiment.
      // The user no longer chooses an independent cross-domain permission mode.
      state.lastEvaluationConfig = {
        ...config,
        evaluation_mode: detectedDomain,
        evaluation_mapping: inputContract.evaluation_mapping || {},
        domain_differences: inputContract.domain_differences || [],
      };
      state.lastEvaluationResult = payload.summary.result;
      
      const extraData = {
        model_id: state.selectedModel.id,
        model_name: state.selectedModel.model_name,
        model_algorithm: state.selectedModel.algorithm,
        execution_receipt: payload.execution_receipt,
      };
      await saveExperimentAuto('evaluation', extraData);
      await loadEvaluationHistory();
      await loadModels();
    } else {
      console.error('没有返回结果数据！');
      els.evalOutput.textContent = '错误：服务器没有返回结果数据';
      renderEvaluationSourcePreview();
    }
  } catch (error) {
    console.error('评估失败:', error);
    els.evalOutput.textContent = `评估失败：${error.message}`;
    displayEvalLog(`评估失败：${error.message}`);
    renderEvaluationSourcePreview();
    showToast(`评估失败：${error.message}`, 'error');
  }
}

async function saveExperimentAuto(experimentType, extraData = {}) {
  if (!state.lastEvaluationConfig || !state.lastEvaluationResult) {
    return;
  }

  try {
    const experimentData = {
      type: experimentType,
      execution_receipt: extraData.execution_receipt,
      ...extraData,
    };

    const response = await api('/api/save-experiment', {
      method: 'POST',
      body: JSON.stringify(experimentData),
    });

    console.log(`实验已自动保存 (${experimentType}):`, response.experiment_id);
    // Display the SAME trusted, persisted event snapshot as historical cards.
    // The server reads event annotations and calculates metrics using the full
    // predictions; the browser never infers that unannotated time is normal.
    try {
      const ref = `outputs/experiment_${response.experiment_id}/reports/summary.json`;
      const archived = await api(`/api/experiment-details?path=${encodeURIComponent(ref)}`);
      const trace = archived?.rows?.[0]?.chart_trace;
      const target = experimentType === 'training' ? els.chart : els.evalChart;
      const cached = target ? chartRenderCache.get(target) : null;
      if (trace && cached?.type === 'result') {
        renderChart(cached.rows, cached.threshold, cached.algorithm, target,
          cached.decisionThreshold, cached.metrics, cached.decisionThresholds,
          cached.predLabels, trace.event_overlay, trace.event_metrics);
      }
    } catch (traceError) {
      console.warn('事件区间叠加加载失败，可在任务查询与导出查看已保存记录：', traceError);
    }
    return response.experiment_id;
    
  } catch (error) {
    console.error('自动保存失败:', error);
    return null;
  }
}

async function refreshPage() {
  try {
    await loadDatabases();
    await loadModels();
    await loadTrainingHistory();
    await loadEvaluationHistory();
  } catch (error) {
    els.runOutput.textContent = `刷新失败：${error.message}`;
  }
}

function initModeSelection() {
  els.modeRadios.forEach(radio => {
    radio.addEventListener('change', (event) => {
      const mode = event.target.value;
      state.evaluationMode = mode;
      
      els.simpleModeSection.style.display = mode === 'simple' ? 'block' : 'none';
      els.rigorousModeSection.style.display = mode === 'rigorous' ? 'block' : 'none';
      
      if (mode === 'rigorous' && state.selectedDb) {
        loadTrainTestTables();
      }
    });
  });
  
  if (els.trainRatioSlider) {
    els.trainRatioSlider.addEventListener('input', (event) => {
      const ratio = parseFloat(event.target.value);
      state.trainRatio = ratio;
      if (els.trainRatioValue) {
        els.trainRatioValue.textContent = `${Math.round(ratio * 100)}%`;
      }
    });
  }
  
  if (els.trainTableSelect) {
    els.trainTableSelect.addEventListener('change', (event) => {
      state.selectedTrainTable = event.target.value;
    });
  }
  
  if (els.testTableSelect) {
    els.testTableSelect.addEventListener('change', (event) => {
      state.selectedTestTable = event.target.value;
    });
  }
}

function updateTrainTestOptions(tables) {
  const trainTables = validTrainingTables(tables);
  const testTables = validTestTables(tables);
  const trainNames = new Set(trainTables.map((item) => item.name));
  const testNames = new Set(testTables.map((item) => item.name));
  const train = trainNames.has(state.selectedTrainTable) ? state.selectedTrainTable : (trainTables[0]?.name || '');
  const test = testNames.has(state.selectedTestTable) ? state.selectedTestTable : (testTables[0]?.name || '');
  renderSelectOptions(els.trainTableSelect, trainTables.map((item) => ({
    label: formatRoleTableOption(item), value: item.name,
  })), train);
  renderSelectOptions(els.testTableSelect, testTables.map((item) => ({
    label: formatRoleTableOption(item), value: item.name,
  })), test);
  state.selectedTrainTable = train;
  state.selectedTestTable = test;
  if (state.evaluationMode === 'rigorous' && els.runOutput) {
    if (!train) els.runOutput.textContent = '当前数据库无有效的单参量正常训练表。';
    else if (!test) els.runOutput.textContent = '当前数据库无有效的单参量测试表。';
  }
}

async function loadTrainTestTables() {
  // 与主训练表共用数据库加载入口，避免独立模式保留上一数据库的过期选项。
  if (!state.selectedDb) return updateTrainTestOptions([]);
  const db = state.selectedDb.value;
  const payload = await api(`/api/sqlite-tables?db=${encodeURIComponent(db)}`);
  if (state.selectedDb?.value !== db) return;
  updateTrainTestOptions(payload.items || []);
}

function updatePreprocessingModuleVisibility() {
  if (!els.preprocessingModule) return;
  els.preprocessingModule.style.display = state.selectedTable ? 'block' : 'none';
}

function getPipelineConfig() {
  const pluginSteps = els.pluginPreprocessorList
    ? Array.from(els.pluginPreprocessorList.querySelectorAll('input[type="checkbox"]:checked')).map((input) => input.value)
    : [];
  const config = {
    fill_missing: true,
    repair_outlier: true,
    smooth: true,
    smooth_window: parseInt(els.smoothWindowSize.value, 10) || 3,
    sigma_multiplier: parseFloat(els.outlierThreshold.value) || 3.0,
    normalize_method: 'none',
    plugin_steps: pluginSteps,
  };
  // 兼容自定义算子常见的 enabled(config) -> config.get(name, False) 写法。
  pluginSteps.forEach((name) => { config[name] = true; });
  return config;
}

function updateTrainingStrategyHint() {
  if (!els.trainingStrategySelect || !els.trainingStrategyHint) return;
  const items = state.pluginCatalog?.training_strategies?.items || [];
  const selected = items.find((item) => item.name === els.trainingStrategySelect.value);
  els.trainingStrategyHint.textContent = selected?.description || '控制算法执行、指标计算和模型保存的编排方式。';
}

async function loadPluginCapabilities() {
  try {
    const payload = await api('/api/plugins');
    state.pluginCatalog = payload.plugins || {};

    const strategies = state.pluginCatalog.training_strategies?.items || [];
    if (els.trainingStrategySelect && strategies.length) {
      const usableStrategies = strategies
        .filter((item) => item.dependencies_status?.installed !== false)
        .map((item) => ({ value: item.name, label: item.label || item.name }));
      renderSelectOptions(
        els.trainingStrategySelect,
        usableStrategies.length ? usableStrategies : [{ value: 'standard', label: '标准训练' }],
        usableStrategies.some((item) => item.value === 'standard') ? 'standard' : usableStrategies[0]?.value
      );
      updateTrainingStrategyHint();
    }

    const builtinPreprocessors = new Set(['fill_missing', 'repair_outlier', 'smooth', 'normalize']);
    const customPreprocessors = (state.pluginCatalog.preprocessors?.items || [])
      .filter((item) => !builtinPreprocessors.has(item.name));
    if (els.pluginPreprocessors && els.pluginPreprocessorList) {
      els.pluginPreprocessorList.innerHTML = '';
      customPreprocessors.forEach((item) => {
        const label = document.createElement('label');
        label.className = 'plugin-preprocessor-option';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = item.name;
        input.disabled = item.dependencies_status?.installed === false;
        const text = document.createElement('span');
        text.textContent = item.label || item.name;
        label.appendChild(input);
        label.appendChild(text);
        els.pluginPreprocessorList.appendChild(label);
      });
      els.pluginPreprocessors.hidden = customPreprocessors.length === 0;
    }
  } catch (error) {
    console.warn('插件能力加载失败，继续使用内置能力:', error);
  }
}

function renderPreprocessingStatus(status) {
  const statusEl = els.preprocessingStatus;
  if (!statusEl) return;

  const iconEl = statusEl.querySelector('.status-icon');
  const textEl = statusEl.querySelector('.status-text');
  const detailEl = statusEl.querySelector('.status-detail');
  const deleteBtn = els.deleteCleanDataBtn;
  const runBtn = els.runPreprocessingBtn;

  statusEl.classList.remove('status-none', 'status-clean', 'status-stale');

  if (state.dataView === 'raw') {
    statusEl.classList.add('status-none');
    textEl.textContent = '保留原始波动';
    detailEl.textContent = '不做填补、去野值或平滑；模型输入前仍会统一归一化';
    if (deleteBtn) deleteBtn.style.display = 'none';
    if (runBtn) runBtn.style.display = 'none';
    return;
  }

  if (!status || status.status === 'not_exist' || status.status === 'error') {
    statusEl.classList.add('status-none');
    textEl.textContent = '未处理';
    detailEl.textContent = '';
    if (deleteBtn) deleteBtn.style.display = 'none';
    if (runBtn) runBtn.style.display = state.dataView === 'clean' ? 'block' : 'none';
  } else if (status.status === 'valid') {
    statusEl.classList.add('status-clean');
    textEl.textContent = '已清洁';
    const timeStr = status.timestamp ? formatTimestamp(status.timestamp) : '';
    const rowCountStr = status.row_count ? ` · ${status.row_count} 条记录` : '';
    detailEl.textContent = timeStr + rowCountStr;
    if (deleteBtn) deleteBtn.style.display = 'inline-flex';
    if (runBtn) runBtn.style.display = 'none';
  } else if (status.status === 'invalidated') {
    statusEl.classList.add('status-stale');
    textEl.textContent = '已失效';
    detailEl.textContent = '源数据已变更，需要重新生成';
    if (deleteBtn) deleteBtn.style.display = 'none';
    if (runBtn) runBtn.style.display = 'block';
  } else {
    statusEl.classList.add('status-none');
    textEl.textContent = '未处理';
    detailEl.textContent = '';
    if (deleteBtn) deleteBtn.style.display = 'none';
    if (runBtn) runBtn.style.display = state.dataView === 'clean' ? 'block' : 'none';
  }
}

function formatTimestamp(timestamp) {
  if (!timestamp) return '';
  try {
    const date = typeof timestamp === 'string' 
      ? new Date(timestamp) 
      : new Date(timestamp * 1000);
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

async function checkPreprocessingStatus(dbValue = state.selectedDb?.value, tableName = state.selectedTable) {
  if (!dbValue || !tableName) {
    state.preprocessingStatus = null;
    renderPreprocessingStatus(null);
    return null;
  }

  const stillCurrent = () => state.selectedDb?.value === dbValue && state.selectedTable === tableName;
  try {
    const payload = await api(
      `/api/preprocessing/status?db=${encodeURIComponent(dbValue)}&table=${encodeURIComponent(tableName)}`
    );
    if (stillCurrent()) {
      state.preprocessingStatus = payload;
      renderPreprocessingStatus(payload);
    }
    return payload;
  } catch (error) {
    console.error('检查预处理状态失败:', error);
    const failure = { status: 'error', message: error.message };
    if (stillCurrent()) {
      state.preprocessingStatus = failure;
      renderPreprocessingStatus(failure);
    }
    return failure;
  }
}

async function runPreprocessing() {
  if (!state.selectedDb || !state.selectedTable) {
    els.runOutput.textContent = '请先选择数据表。';
    return;
  }

  els.runPreprocessingBtn.textContent = '处理中...';
  els.runPreprocessingBtn.disabled = true;

  try {
    const config = getPipelineConfig();
    const payload = await api('/api/preprocessing/run', {
      method: 'POST',
      body: JSON.stringify({
        db: state.selectedDb.value,
        table: state.selectedTable,
        pipelineConfig: config,
      }),
    });

    els.runOutput.textContent = `预处理完成！生成 ${payload.row_count || '未知'} 条清洁记录。`;

    await checkPreprocessingStatus();
    await loadTableRows();
  } catch (error) {
    console.error('预处理失败:', error);
    els.runOutput.textContent = `预处理失败：${error.message}`;
  } finally {
    els.runPreprocessingBtn.textContent = '执行预处理';
    els.runPreprocessingBtn.disabled = false;
  }
}

async function deleteCleanData() {
  if (!state.selectedDb || !state.selectedTable) return;

  if (!confirm('确定要删除该数据表的清洁数据吗？')) return;

  try {
    await api('/api/preprocessing/delete', {
      method: 'POST',
      body: JSON.stringify({
        db: state.selectedDb.value,
        table: state.selectedTable,
      }),
    });

    state.dataView = 'raw';
    els.dataViewRadios.forEach((radio) => {
      radio.checked = radio.value === 'raw';
    });

    els.runOutput.textContent = '清洁数据已删除。';
    await checkPreprocessingStatus();
    await loadTableRows();
  } catch (error) {
    console.error('删除清洁数据失败:', error);
    els.runOutput.textContent = `删除失败：${error.message}`;
  }
}

async function onDataViewChanged() {
  const selectedRadio = document.querySelector('input[name="dataView"]:checked');
  if (!selectedRadio) return;

  state.dataView = selectedRadio.value;
  invalidateTrainingResult();
  if (state.dataView === 'raw') {
    renderPreprocessingStatus(state.preprocessingStatus);
  }
  await loadTableRows();
}

async function initAlgorithmOptions() {
  try {
    const response = await fetch('/api/available-algorithms');
    const payload = await response.json();
    
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    
    state.algorithmsWithDeps = payload.algorithms.map(algo => ({
      label: algo.label,
      value: algo.name,
      description: algo.description || '',
      minTrainingSamples: Number(algo.min_training_samples) || 2,
      dependencies: algo.dependencies || { installed: true, missing: [] }
    }));
    
    if (state.algorithmsWithDeps.length === 0) {
      console.warn('没有可用的算法');
      return;
    }
    
    renderAlgorithmOptions(state.algorithmsWithDeps, state.algorithmsWithDeps[0]?.value);
    state.selectedAlgorithm = state.algorithmsWithDeps[0]?.value;
    
    updateDependencyStatus(state.algorithmsWithDeps[0]);
    await loadAlgorithmParams(state.algorithmsWithDeps[0]?.value);
    
    console.log(`已加载 ${state.algorithmsWithDeps.length} 个算法`);
  } catch (error) {
    console.error('加载算法列表失败:', error);
    state.algorithmsWithDeps = [
      { label: '3-Sigma', value: 'three_sigma', description: '基于 3-Sigma 原则的异常检测', minTrainingSamples: 2, dependencies: { installed: true, missing: [] } },
      { label: 'Z-Score', value: 'zscore', description: '基于 Z-Score 的异常检测', minTrainingSamples: 2, dependencies: { installed: true, missing: [] } },
      { label: 'IQR', value: 'iqr', description: '基于四分位距的异常检测', minTrainingSamples: 4, dependencies: { installed: true, missing: [] } },
      { label: 'Isolation Forest', value: 'isolation_forest_detector', description: '孤立森林异常检测', minTrainingSamples: 2, dependencies: { installed: true, missing: [] } },
    ];
    renderAlgorithmOptions(state.algorithmsWithDeps, state.algorithmsWithDeps[0]?.value);
    state.selectedAlgorithm = state.algorithmsWithDeps[0]?.value;
    updateDependencyStatus(state.algorithmsWithDeps[0]);
    await loadAlgorithmParams(state.algorithmsWithDeps[0]?.value);
  }
}

function renderAlgorithmOptions(algorithms, selectedValue) {
  if (!els.algorithmSelect) return;
  if (!algorithms.length) {
    els.algorithmSelect.innerHTML = '<option value="">暂无算法</option>';
    return;
  }
  
  els.algorithmSelect.innerHTML = algorithms.map((algo) => {
    const dependencyText = algo.dependencies?.installed
      ? '依赖已安装'
      : '缺少依赖: ' + (algo.dependencies?.missing?.join(', ') || '');
    const minimumText = `最低训练样本数: ${algo.minTrainingSamples || 2}`;
    return `<option value="${algo.value}" title="${dependencyText}；${minimumText}">${algo.label}</option>`;
  }).join('');
  
  if (selectedValue !== undefined && selectedValue !== null && selectedValue !== '') {
    els.algorithmSelect.value = selectedValue;
  }
}

function updateDependencyStatus(algo) {
  if (!els.dependencyStatus) {
    const statusDiv = document.createElement('div');
    statusDiv.id = 'dependencyStatus';
    statusDiv.className = 'dependency-status';
    els.algorithmSelect.parentNode.appendChild(statusDiv);
    els.dependencyStatus = statusDiv;
  }
  
  if (!algo || !algo.dependencies) {
    els.dependencyStatus.innerHTML = '';
    els.dependencyStatus.style.display = 'none';
    return;
  }
  
  els.dependencyStatus.style.display = 'block';
  
  const minimumText = `最低训练样本数：${algo.minTrainingSamples || 2}`;

  if (algo.dependencies.installed) {
    els.dependencyStatus.innerHTML = `
      <span class="status-success">所有依赖已安装</span>
      <div class="install-hint">${minimumText}</div>
    `;
    els.dependencyStatus.className = 'dependency-status status-ok';
  } else {
    const missing = algo.dependencies.missing || [];
    els.dependencyStatus.innerHTML = `
      <span class="status-warning">缺少依赖: ${missing.join(', ')}</span>
      <div class="install-hint">${minimumText}</div>
      <div class="install-hint">安装命令: <code>pip install ${missing.join(' ')}</code></div>
    `;
    els.dependencyStatus.className = 'dependency-status status-warning';
  }
}

async function loadAlgorithmParams(algorithmName) {
  if (!algorithmName) {
    renderAlgorithmParams([]);
    return;
  }
  
  try {
    const response = await fetch(`/api/algorithm-config-schema?algorithm=${encodeURIComponent(algorithmName)}`);
    const payload = await response.json();
    
    if (response.ok && payload.schema) {
      renderAlgorithmParams(payload.schema.parameters || []);
    } else {
      renderAlgorithmParams([]);
    }
  } catch (error) {
    console.error('加载算法参数配置失败:', error);
    renderAlgorithmParams([]);
  }
}

function renderAlgorithmParams(parameters) {
  if (!els.algorithmParamsForm) return;
  
  if (!parameters || parameters.length === 0) {
    els.algorithmParamsForm.innerHTML = '<div class="params-empty">该算法无需配置参数</div>';
    els.algorithmParamsCount.textContent = '';
    return;
  }
  
  els.algorithmParamsCount.textContent = `(${parameters.length}项)`;
  
  els.algorithmParamsForm.innerHTML = parameters.map(param => {
    const paramId = `param_${param.name}`;
    const minAttr = param.min !== undefined && param.min !== null ? `min="${param.min}"` : '';
    const maxAttr = param.max !== undefined && param.max !== null ? `max="${param.max}"` : '';
    const stepAttr = param.step !== undefined && param.step !== null ? `step="${param.step}"` : '';
    const defaultValue = param.default ?? '';
    let inputHtml = '';
    
    switch (param.type) {
      case 'int':
        inputHtml = `
          <input 
            type="number" 
            id="${paramId}" 
            ${minAttr}
            ${maxAttr}
            ${stepAttr || 'step="1"'}
            value="${defaultValue}"
          />
        `;
        break;
      case 'float':
        inputHtml = `
          <input 
            type="number" 
            id="${paramId}" 
            ${minAttr}
            ${maxAttr}
            ${stepAttr || 'step="any"'}
            value="${defaultValue}"
          />
        `;
        break;
      case 'bool':
        inputHtml = `
          <label class="checkbox-label">
            <input type="checkbox" id="${paramId}" ${param.default ? 'checked' : ''} />
            <span>${param.default ? '是' : '否'}</span>
          </label>
        `;
        break;
      case 'list':
        inputHtml = `
          <input 
            type="text" 
            class="list-input"
            id="${paramId}" 
            placeholder="${param.description || '用逗号分隔，如: 64,32,16'}" 
            value="${Array.isArray(param.default) ? param.default.join(', ') : param.default || ''}"
          />
        `;
        break;
      case 'str':
        if (param.options && param.options.length > 0) {
          inputHtml = `
            <select id="${paramId}">
              ${param.options.map(opt => `
                <option value="${opt}" ${opt === param.default ? 'selected' : ''}>${opt}</option>
              `).join('')}
            </select>
          `;
        } else {
          inputHtml = `
            <input 
              type="text" 
              id="${paramId}" 
              value="${defaultValue}"
            />
          `;
        }
        break;
      default:
        inputHtml = `
          <input 
            type="text" 
            id="${paramId}" 
            value="${defaultValue}"
          />
        `;
    }
    
    const visibleWhen = param.visible_when || null;
    const visibilityAttrs = visibleWhen && visibleWhen.name
      ? ` data-visible-when-name="${visibleWhen.name}" data-visible-when-value="${visibleWhen.value}"`
      : '';
    return `
      <label class="field param-field"${visibilityAttrs}>
        <span>${param.label || param.name}</span>
        ${inputHtml}
        ${param.description ? `<span class="param-description">${param.description}</span>` : ''}
      </label>
    `;
  }).join('');

  bindAlgorithmParamVisibility();
}

function applyAlgorithmParamVisibility() {
  if (!els.algorithmParamsForm) return;
  const fields = Array.from(els.algorithmParamsForm.querySelectorAll('.param-field'));
  fields.forEach(field => {
    const controllerName = field.dataset.visibleWhenName;
    if (!controllerName) {
      field.hidden = false;
      return;
    }
    const controller = els.algorithmParamsForm.querySelector(`#param_${controllerName}`);
    const expected = String(field.dataset.visibleWhenValue ?? '');
    const actual = controller
      ? (controller.type === 'checkbox' ? String(controller.checked) : String(controller.value))
      : '';
    field.hidden = actual !== expected;
  });
  if (els.algorithmParamsCount) {
    const visibleCount = fields.filter(field => !field.hidden).length;
    els.algorithmParamsCount.textContent = visibleCount ? `(${visibleCount}项)` : '';
  }
}

function bindAlgorithmParamVisibility() {
  if (!els.algorithmParamsForm) return;
  const controllers = new Set();
  els.algorithmParamsForm.querySelectorAll('.param-field[data-visible-when-name]').forEach(field => {
    if (field.dataset.visibleWhenName) controllers.add(field.dataset.visibleWhenName);
  });
  controllers.forEach(name => {
    const input = els.algorithmParamsForm.querySelector(`#param_${name}`);
    if (input) input.addEventListener('change', applyAlgorithmParamVisibility);
  });
  applyAlgorithmParamVisibility();
}

function getAlgorithmParams() {
  if (!els.algorithmParamsForm) return {};
  
  const params = {};
  const paramInputs = els.algorithmParamsForm.querySelectorAll('input, select');
  
  paramInputs.forEach(input => {
    const field = input.closest('.param-field');
    if (field && field.hidden) return;
    const paramName = input.id.replace('param_', '');
    let value;
    
    if (input.type === 'checkbox') {
      value = input.checked;
    } else if (input.type === 'number') {
      const val = parseFloat(input.value);
      value = Number.isInteger(val) ? Math.round(val) : val;
    } else if (input.classList.contains('list-input')) {
      value = input.value.split(',').map(item => {
        const trimmed = item.trim();
        const num = parseFloat(trimmed);
        return Number.isNaN(num) ? trimmed : (Number.isInteger(num) ? Math.round(num) : num);
      }).filter(item => item !== '');
    } else {
      value = input.value;
    }
    
    params[paramName] = value;
  });
  
  return params;
}

async function loadTrainingHistory() {
  if (els.trainingHistoryList) showHistorySkeleton(els.trainingHistoryList);
  try {
    const response = await api('/api/experiments?type=training');
    const experiments = response.items || [];
    renderTrainingHistory(experiments.slice(0, 3));
  } catch (error) {
    console.error('加载训练历史失败:', error);
    showHistoryError(els.trainingHistoryList, '训练历史加载失败，请刷新重试');
  }
}

async function loadEvaluationHistory() {
  if (els.evaluationHistoryList) showHistorySkeleton(els.evaluationHistoryList);
  try {
    const response = await api('/api/experiments?type=evaluation');
    const experiments = response.items || [];
    renderEvaluationHistory(experiments.slice(0, 3));
  } catch (error) {
    console.error('加载评估历史失败:', error);
    showHistoryError(els.evaluationHistoryList, '评估历史加载失败，请刷新重试');
  }
}

function renderTrainingHistory(experiments) {
  if (!els.trainingHistoryList) return;
  
  if (!experiments.length) {
    els.trainingHistoryList.innerHTML = '<div class="history-empty">暂无训练记录</div>';
    return;
  }
  
  els.trainingHistoryList.innerHTML = experiments.map(exp => {
    const isPublished = exp.model_published;
    const modelName = exp.model_name || '未发布';
    const table = exp.table || '未知';
    const accuracy = Number.isFinite(exp.metrics_avg?.accuracy) ? (exp.metrics_avg.accuracy * 100).toFixed(2) + '%' : 'N/A';
    const timestamp = exp.experiment_id.replace('_', ' ');
    
    return `
      <div class="history-item">
        <div class="history-header">
          <span class="history-id">${timestamp}</span>
          ${isPublished ? `<span class="badge-published">已发布</span>` : ''}
        </div>
        <div class="history-meta">
          <span class="history-algorithm">${exp.algorithm}</span>
          <span class="history-table">${table}</span>
        </div>
        <div class="history-metrics">
          <span>准确率: ${accuracy}</span>
          ${isPublished
            ? `<span class="history-model-name">${modelName}</span>`
            : (exp.model_available ? `<button type="button" class="history-publish-btn" data-path="${exp.path}">发布模型</button>` : `<span class="history-model-missing">模型文件不可用</span>`)}
        </div>
      </div>
    `;
  }).join('');

  els.trainingHistoryList.querySelectorAll('.history-publish-btn').forEach((button) => {
    button.addEventListener('click', async () => {
      const exp = experiments.find((item) => item.path === button.dataset.path);
      if (!exp) return;
      await publishHistoricalTraining(exp);
    });
  });
}

async function publishHistoricalTraining(exp) {
  const defaultName = `${exp.algorithm || 'model'}-${exp.model_artifact_experiment_id || exp.experiment_id}`;
  const modelName = window.prompt('请输入模型名称', defaultName);
  if (modelName === null) return;
  if (!modelName.trim()) {
    showToast('模型名称不能为空', 'error');
    return;
  }
  const modelDesc = window.prompt('模型描述（可选）', '') ?? '';
  try {
    const result = await api('/api/experiments/publish-model', {
      method: 'POST',
      body: JSON.stringify({ path: exp.path, model_name: modelName.trim(), model_desc: modelDesc.trim() }),
    });
    showToast(result.message || '模型发布成功', 'success');
    await Promise.all([loadTrainingHistory(), loadModels()]);
  } catch (error) {
    showToast(`发布失败：${error.message}`, 'error');
  }
}

function renderEvaluationHistory(experiments) {
  if (!els.evaluationHistoryList) return;
  
  if (!experiments.length) {
    els.evaluationHistoryList.innerHTML = '<div class="history-empty">暂无评估记录</div>';
    return;
  }
  
  els.evaluationHistoryList.innerHTML = experiments.map(exp => {
    const modelName = exp.model_name || '未知模型';
    const evalTable = exp.eval_table || '未知表';
    const accuracy = Number.isFinite(exp.metrics_avg?.accuracy) ? (exp.metrics_avg.accuracy * 100).toFixed(2) + '%' : 'N/A';
    const timestamp = exp.experiment_id.replace('_', ' ');
    
    return `
      <div class="history-item">
        <div class="history-header">
          <span class="history-id">${timestamp}</span>
        </div>
        <div class="history-meta">
          <span class="history-model">${modelName}</span>
          <span class="history-table">${evalTable}</span>
        </div>
        <div class="history-metrics">
          <span>准确率: ${accuracy}</span>
        </div>
      </div>
    `;
  }).join('');
}

function initTabSwitch() {
  els.mainTabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      
      els.mainTabBtns.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      
      if (tab === 'train') {
        els.trainTab.classList.add('active');
        els.evaluateTab.classList.remove('active');
      } else {
        els.evaluateTab.classList.add('active');
        els.trainTab.classList.remove('active');
        loadModels();
        renderEvaluationSourcePreview();
        scheduleChartGeometryRefresh(els.evalChartViewer);
      }
    });
  });
}

els.dbSelect.addEventListener('change', async () => {
  state.selectedFeatures = ['value'];
  state.extraTrainingTables = [];
  els.trainTimeStart.value = '';
  els.trainTimeEnd.value = '';
  invalidateTrainingResult();
  await loadDbTables();
});
els.tableSelect.addEventListener('change', async (event) => {
  state.selectedTable = event.target.value || '';
  state.selectedFeatures = ['value'];
  renderAdditionalTrainingTables();
  els.trainTimeStart.value = '';
  els.trainTimeEnd.value = '';
  invalidateTrainingResult();
  await loadTableRows();
});
els.trainTimeColumn?.addEventListener('change', async () => {
  state.trainTimeColumn = els.trainTimeColumn.value;
  els.trainTimeStart.value = '';
  els.trainTimeEnd.value = '';
  await refreshTrainTimeRange();
});
els.resetTrainTimeBtn?.addEventListener('click', () => {
  els.trainTimeStart.value = '';
  els.trainTimeEnd.value = '';
  refreshAlignmentPreview();
});
for (const input of [els.trainTimeStart, els.trainTimeEnd]) {
  input?.addEventListener('change', refreshAlignmentPreview);
}
els.evalDbSelect.addEventListener('change', async () => {
  await loadEvalTables();
});
if (els.evalTableSelect) {
  els.evalTableSelect.addEventListener('change', async (event) => {
    state.evalSelectedTable = event.target.value || '';
    renderAdditionalEvalTables();
    invalidateEvaluationResult('正在加载所选测试数据的曲线…');
    await loadEvalTableRows();
  });
}
bindFilter(els.modelFilter, 'model');
bindFilter(els.subsystemFilter, 'subsystem');
bindFilter(els.deviceFilter, 'device');
bindFilter(els.variableFilter, 'variable');
els.algorithmSelect.addEventListener('change', async (event) => {
  invalidateTrainingResult();
  renderTrainingSourcePreview();
  state.selectedAlgorithm = event.target.value;
  await loadAlgorithmParams(event.target.value);
  
  const selectedAlgo = state.algorithmsWithDeps?.find(a => a.value === event.target.value);
  updateDependencyStatus(selectedAlgo);
});
els.trainTablesSearch?.addEventListener('input', renderAdditionalTrainingTables);
els.evalTablesSearch?.addEventListener('input', renderAdditionalEvalTables);
els.alignmentMethod?.addEventListener('change', () => { refreshAlignmentPreview(); invalidateTrainingResult(); });
function updateTimelineControls() {
  if (els.gridIntervalField) els.gridIntervalField.hidden = els.timelineMode?.value !== 'fixed';
}
els.timelineMode?.addEventListener('change', () => { updateTimelineControls(); refreshAlignmentPreview(); invalidateTrainingResult(); });
els.gridIntervalSeconds?.addEventListener('change', () => { refreshAlignmentPreview(); invalidateTrainingResult(); });
els.labelAlignmentMethod?.addEventListener('change', () => { refreshAlignmentPreview(); invalidateTrainingResult(); });
updateTimelineControls();
els.alignmentTolerance?.addEventListener('change', () => {
  invalidateTrainingResult();
  refreshAlignmentPreview();
});
els.checkAlignmentBtn?.addEventListener('click', refreshAlignmentPreview);
els.runBtn.addEventListener('click', runTraining);
els.refreshBtn.addEventListener('click', refreshPage);

els.dataViewRadios.forEach((radio) => {
  radio.addEventListener('change', onDataViewChanged);
});

if (els.runPreprocessingBtn) {
  els.runPreprocessingBtn.addEventListener('click', runPreprocessing);
}

if (els.deleteCleanDataBtn) {
  els.deleteCleanDataBtn.addEventListener('click', deleteCleanData);
}

if (els.publishModelBtn) {
  els.publishModelBtn.addEventListener('click', publishModel);
}

if (els.evaluateBtn) {
  els.evaluateBtn.addEventListener('click', runEvaluation);
}

if (els.refreshModelsBtn) {
  els.refreshModelsBtn.addEventListener('click', loadModels);
}

if (els.modelSelect) {
  els.modelSelect.addEventListener('change', async (event) => {
    const modelId = parseInt(event.target.value);
    state.selectedModel = state.models.find((m) => m.id === modelId);
    state.lastEvaluationResult = null;
    state.lastEvaluationConfig = null;
    await loadEvalTables();
    
    els.modelCardList.querySelectorAll('.model-card').forEach((card) => {
      card.classList.remove('active');
      if (parseInt(card.dataset.modelId) === modelId) {
        card.classList.add('active');
      }
    });
  });
}

// ==================== 检测算法导入入口 ====================
// 算法安装统一由“检测算法管理”负责。这里仅负责跳转，并携带“检测算法 / Python”
// 的导入意图，让检测算法管理页自动展开安装区域并定位到对应类型。
const importAlgorithmBtn = document.getElementById('importAlgorithmBtn');
const PLUGIN_IMPORT_INTENT_KEY = 'aas.plugin-import-intent.v1';

function navigateToDetectorPluginImport() {
  const intent = {
    type: 'detectors',
    language: 'python',
    openInstall: true,
    source: 'runner',
    createdAt: Date.now(),
  };

  try {
    window.sessionStorage.setItem(PLUGIN_IMPORT_INTENT_KEY, JSON.stringify(intent));
  } catch (error) {
    console.warn('无法保存插件导入跳转状态，将使用 URL 参数继续跳转。', error);
  }

  // 工作台中 runner.html 运行在同源 iframe 内，切换外层 hash 可保留统一侧栏、
  // 面包屑和主题状态。独立打开 runner.html 时则直接进入 plugins.html。
  try {
    if (window.parent && window.parent !== window && window.parent.location) {
      window.parent.location.hash = 'plugins';
      return;
    }
  } catch (error) {
    console.warn('无法通知工作台切换模块，将直接打开检测算法管理页。', error);
  }

  window.location.href = 'plugins.html?install=detectors&language=python&source=runner';
}

if (importAlgorithmBtn) {
  importAlgorithmBtn.addEventListener('click', navigateToDetectorPluginImport);
}

// ==================== 依赖管理 ====================

const dependencyEls = {
  dependencyMgmtBtn: document.getElementById('dependencyMgmtBtn'),
  dependencyMgmtModal: document.getElementById('dependencyMgmtModal'),
  closeDependencyModalBtn: document.getElementById('closeDependencyModalBtn'),
  installAllDepsBtn: document.getElementById('installAllDepsBtn'),
  refreshDepsBtn: document.getElementById('refreshDepsBtn'),
  depsTableBody: document.getElementById('depsTableBody'),
  dependencyInstallProgress: document.getElementById('dependencyInstallProgress'),
  depProgressBar: document.getElementById('depProgressBar'),
  depProgressText: document.getElementById('depProgressText'),
  dependencyMessage: document.getElementById('dependencyMessage'),
};

let dependencyStatus = null;

async function loadDependencies() {
  if (!dependencyEls.depsTableBody) return;
  dependencyEls.depsTableBody.innerHTML = '<tr><td colspan="4" class="loading-cell">加载中...</td></tr>';
  clearDependencyMessage();

  try {
    const resp = await api('/api/dependencies');
    dependencyStatus = resp.detectors || {};
    renderDependenciesTable(dependencyStatus);
  } catch (error) {
    dependencyEls.depsTableBody.innerHTML = `<tr><td colspan="4" class="error-cell">加载失败：${error.message}</td></tr>`;
  }
}

function renderDependenciesTable(detectors) {
  if (!dependencyEls.depsTableBody) return;

  const entries = Object.entries(detectors);
  if (entries.length === 0) {
    dependencyEls.depsTableBody.innerHTML = '<tr><td colspan="4" class="empty-cell">暂无检测器数据</td></tr>';
    return;
  }

  dependencyEls.depsTableBody.innerHTML = entries.map(([name, info]) => {
    const isInstalled = info.installed;
    const deps = info.dependencies || [];
    const missing = info.missing || [];
    const depsDisplay = deps.length > 0 ? deps.join(', ') : '无外部依赖';
    const statusHtml = isInstalled
      ? '<span class="dep-status installed">已安装</span>'
      : `<span class="dep-status missing">缺失: ${missing.join(', ')}</span>`;
    const actionHtml = isInstalled
      ? '<span class="dep-action-none">-</span>'
      : `<button class="dep-install-btn" data-detector="${name}" ${isInstallingGlobal ? 'disabled' : ''}>安装依赖</button>`;

    return `<tr class="dep-row ${isInstalled ? 'dep-ok' : 'dep-warn'}">
      <td class="dep-name">${name}</td>
      <td class="dep-deps">${depsDisplay}</td>
      <td class="dep-status-cell">${statusHtml}</td>
      <td class="dep-action-cell">${actionHtml}</td>
    </tr>`;
  }).join('');

  // 绑定安装按钮事件
  dependencyEls.depsTableBody.querySelectorAll('.dep-install-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const detectorName = btn.dataset.detector;
      await installDependency(detectorName, btn);
    });
  });
}

let isInstallingGlobal = false;

async function installDependency(detectorName, btnEl) {
  if (isInstallingGlobal) return;
  isInstallingGlobal = true;

  if (btnEl) {
    btnEl.textContent = '安装中...';
    btnEl.disabled = true;
  }

  showDependencyMessage('info', `正在安装 ${detectorName} 的依赖...`);

  try {
    const resp = await api('/api/install-dependency', {
      method: 'POST',
      body: JSON.stringify({ detector: detectorName }),
    });

    if (resp.status === 'success') {
      showDependencyMessage('success', `${detectorName}: 依赖安装成功`);
      await loadDependencies(); // 刷新状态
    } else {
      showDependencyMessage('error', `${detectorName}: ${resp.message || '安装失败'}`);
    }
  } catch (error) {
    showDependencyMessage('error', `${detectorName}: 安装出错 - ${error.message}`);
  } finally {
    isInstallingGlobal = false;
    if (btnEl) {
      btnEl.textContent = '安装依赖';
      btnEl.disabled = false;
    }
  }
}

async function installAllDependencies() {
  if (isInstallingGlobal) return;
  isInstallingGlobal = true;

  const progressEl = dependencyEls.dependencyInstallProgress;
  const progressBar = dependencyEls.depProgressBar;
  const progressText = dependencyEls.depProgressText;
  const installBtn = dependencyEls.installAllDepsBtn;

  progressEl.style.display = 'flex';
  progressBar.style.width = '0%';
  progressText.textContent = '正在准备安装...';
  installBtn.disabled = true;
  installBtn.textContent = '安装中...';
  clearDependencyMessage();

  try {
    const resp = await api('/api/install-all-dependencies', {
      method: 'POST',
    });

    const results = resp.results || {};
    const entries = Object.entries(results);
    let successCount = 0;
    let failCount = 0;

    for (let i = 0; i < entries.length; i++) {
      const [detectorName, result] = entries[i];
      const progress = Math.round(((i + 1) / entries.length) * 100);
      progressBar.style.width = `${progress}%`;
      progressText.textContent = `正在安装 ${detectorName}... (${i + 1}/${entries.length})`;

      if (result.success) {
        successCount++;
      } else {
        failCount++;
      }
    }

    progressBar.style.width = '100%';

    if (failCount === 0) {
      progressText.textContent = `全部安装成功！共 ${successCount} 个检测器`;
      showDependencyMessage('success', `所有依赖安装成功！`);
    } else {
      progressText.textContent = `安装完成：${successCount} 成功，${failCount} 失败`;
      const failDetails = entries
        .filter(([, r]) => !r.success)
        .map(([name, r]) => `${name}: ${r.message}`)
        .join('<br>');
      showDependencyMessage('warning', `${failCount} 个检测器安装失败:<br>${failDetails}`);
    }

    await loadDependencies(); // 刷新状态

    // 3秒后隐藏进度条
    setTimeout(() => {
      progressEl.style.display = 'none';
    }, 3000);

  } catch (error) {
    progressText.textContent = '安装出错';
    showDependencyMessage('error', `安装失败：${error.message}`);
  } finally {
    isInstallingGlobal = false;
    installBtn.disabled = false;
    installBtn.textContent = '一键安装所有缺失依赖';
  }
}

function showDependencyMessage(type, message) {
  if (!dependencyEls.dependencyMessage) return;
  dependencyEls.dependencyMessage.className = `dependency-message ${type}`;
  dependencyEls.dependencyMessage.innerHTML = message;
  dependencyEls.dependencyMessage.style.display = 'block';
}

function clearDependencyMessage() {
  if (!dependencyEls.dependencyMessage) return;
  dependencyEls.dependencyMessage.style.display = 'none';
  dependencyEls.dependencyMessage.innerHTML = '';
}

// 依赖管理按钮事件
if (dependencyEls.dependencyMgmtBtn) {
  dependencyEls.dependencyMgmtBtn.addEventListener('click', async () => {
    dependencyEls.dependencyMgmtModal.style.display = 'flex';
    await loadDependencies();
  });
}

if (dependencyEls.closeDependencyModalBtn) {
  dependencyEls.closeDependencyModalBtn.addEventListener('click', () => {
    dependencyEls.dependencyMgmtModal.style.display = 'none';
  });
}

if (dependencyEls.dependencyMgmtModal) {
  dependencyEls.dependencyMgmtModal.addEventListener('click', (event) => {
    if (event.target === dependencyEls.dependencyMgmtModal) {
      dependencyEls.dependencyMgmtModal.style.display = 'none';
    }
  });
}

if (dependencyEls.installAllDepsBtn) {
  dependencyEls.installAllDepsBtn.addEventListener('click', installAllDependencies);
}

if (dependencyEls.refreshDepsBtn) {
  dependencyEls.refreshDepsBtn.addEventListener('click', loadDependencies);
}

// ==================== 结束依赖管理 ====================

if (els.trainingStrategySelect) {
  els.trainingStrategySelect.addEventListener('change', updateTrainingStrategyHint);
}

if (els.trainChartFullscreenBtn) {
  els.trainChartFullscreenBtn.addEventListener('click', () => toggleChartFullscreen(els.trainChartViewer));
}
if (els.evalChartFullscreenBtn) {
  els.evalChartFullscreenBtn.addEventListener('click', () => toggleChartFullscreen(els.evalChartViewer));
}
document.addEventListener('fullscreenchange', () => {
  updateChartFullscreenControls();
  scheduleChartGeometryRefresh(els.trainChartViewer);
  scheduleChartGeometryRefresh(els.evalChartViewer);
});

let chartResizeTimer = null;
window.addEventListener('resize', () => {
  window.clearTimeout(chartResizeTimer);
  chartResizeTimer = window.setTimeout(() => {
    rerenderCachedChart(els.chart);
    rerenderCachedChart(els.evalChart);
  }, 120);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !document.fullscreenElement) {
    leaveFallbackChartFullscreen();
  }
});
updateChartFullscreenControls();

els.evalDataViewRadios.forEach((radio) => {
  radio.addEventListener('change', () => {
    if (!radio.checked) return;
    state.evalDataView = radio.value;
    state.evalCleanStatus = null;
    invalidateEvaluationResult('正在切换评估数据视图…');
    loadEvalTableRows();
  });
});
initModeSelection();
initAlgorithmOptions();
loadPluginCapabilities();
initTabSwitch();
refreshPage();

// One resource-selection workflow for training, independent datasets and evaluation.
if (window.AASResourcePicker) {
  document.body.classList.add('resource-picker-ready');
  const trainingSummary = document.getElementById('trainResourceSummary');
  const evaluationSummary = document.getElementById('evalResourceSummary');
  const reflect = () => {
    const names=[state.selectedTable,...state.extraTrainingTables].filter(Boolean);
    if(trainingSummary)trainingSummary.textContent=state.evaluationMode==='rigorous'
      ? `训练表：${state.selectedTrainTable || '未选择'} · 测试表：${state.selectedTestTable || '未选择'}`
      : (names.length?`${names.length} 维 · ${names.join(' → ')}`:'当前数据库没有可用的训练数据');
    const evalNames=[state.evalSelectedTable,...state.extraEvaluationTables].filter(Boolean);
    if(evaluationSummary)evaluationSummary.textContent=evalNames.length?`${evalNames.length} 张 · ${evalNames.join(' → ')}`:'请选择模型及测试数据库';
  };
  document.getElementById('chooseTrainResources')?.addEventListener('click',()=>{
    const rigorous=state.evaluationMode==='rigorous';
    const items=rigorous?[]:state.tables;
    if(rigorous){
      // Independent tables have different roles. The same picker is reused for
      // each independent group without coupling their filtering state.
      const trainItems=state.tables.filter(x=>x.data_role==='train');
      const current = state.selectedTrainTable;
      window.AASResourcePicker.open({purpose:'train',title:'独立训练集 · 选择训练表',items:trainItems,selected:[current].filter(Boolean),max:1,min:1,
        onApply: names=>{state.selectedTrainTable=names[0];els.trainTableSelect.value=names[0];reflect();invalidateTrainingResult();}});
      return;
    }
    window.AASResourcePicker.open({purpose:'train',title:'训练数据 · 按顺序选择参量',items,selected:[state.selectedTable,...state.extraTrainingTables].filter(Boolean),max:16,min:1,
      onApply:async names=>{
        const changed=state.selectedTable!==names[0];
        state.selectedTable=names[0];state.extraTrainingTables=names.slice(1);
        els.tableSelect.value=names[0];
        invalidateTrainingResult();
        if(changed)await loadTableRows();
        renderAdditionalTrainingTables();reflect();
      }});
  });
  document.getElementById('chooseRigorousTestResources')?.addEventListener('click',()=>{
    const allOptions = [...els.testTableSelect.options].filter(item=>item.value);
    const items = allOptions.map(item=>(state.testCatalog || []).find(row=>row.name===item.value)
      || {name:item.value,hierarchy:[],role_label:'测试集'});
    window.AASResourcePicker.open({purpose:'evaluate',title:'独立测试集 · 选择测试表',items,
      selected:[state.selectedTestTable].filter(Boolean),max:1,min:1,
      onApply: names=>{state.selectedTestTable=names[0];els.testTableSelect.value=names[0];reflect();invalidateTrainingResult();}});
  });
  const refreshIndependentButtons = () => {
    const testBtn=document.getElementById('chooseRigorousTestResources');
    if(testBtn)testBtn.hidden=state.evaluationMode!=='rigorous';
    const trainBtn=document.getElementById('chooseTrainResources');
    if(trainBtn)trainBtn.textContent=state.evaluationMode==='rigorous'?'选择独立训练表':'选择训练数据表';
  };
  document.querySelectorAll('input[name="evaluationMode"]').forEach(r=>r.addEventListener('change',()=>{refreshIndependentButtons();reflect();}));
  refreshIndependentButtons();
  document.getElementById('chooseEvalResources')?.addEventListener('click',()=>{
    window.AASResourcePicker.open({purpose:'evaluate',title:'评估数据 · 顺序对应模型输入',items:state.evalCandidates,
      selected:[state.evalSelectedTable,...state.extraEvaluationTables].filter(Boolean),max:16,min:1,
      onApply:async names=>{
        const changed=state.evalSelectedTable!==names[0];
        state.evalSelectedTable=names[0];state.extraEvaluationTables=names.slice(1);
        els.evalTableSelect.value=names[0];renderAdditionalEvalTables();
        invalidateEvaluationResult('评估数据组合已更新。');
        if(changed)await loadEvalTableRows();reflect();
      }});
  });
  [els.dbSelect,els.evalDbSelect,els.tableSelect,els.evalTableSelect,els.modelSelect].filter(Boolean)
    .forEach(el=>el.addEventListener('change',()=>queueMicrotask(reflect)));
  new MutationObserver(reflect).observe(document.getElementById('trainSelectionSummary'),{childList:true});
  new MutationObserver(reflect).observe(document.getElementById('evalSelectionSummary'),{childList:true});
  reflect();
}
