const state = {
  databases: [],
  tables: [],
  rows: [],
  filteredRows: [],
  datasetMeta: {},
  filters: {
    model: '__ALL__',
    subsystem: '__ALL__',
    device: '__ALL__',
    variable: '__ALL__',
  },
  selectedDb: null,
  selectedTable: null,
  selectedTimeCol: null,
  selectedLabelCol: null,
  selectedFeatures: ['value'],
  selectedTables: [],
  multiFilters: { model: [], subsystem: [], device: [], variable: [] },
  multiSeries: [],
  referenceTable: null,
  multiLoadId: 0,
  tableSearch: '',
  selectedFeatureSource: null,
  chartValueMode: 'independent',
  intervalAnnotations: [],
  intervalContextKey: '',
  intervalLoadSequence: 0,
  annotationMode: false,
  annotationDrag: null,
  annotationAxis: null,
  annotationError: '',
  selectedRecord: null,
  // 数据预处理状态
  dataView: 'raw', // 'raw' 或 'clean'
  preprocessingStatus: null, // { exists, valid, timestamp, row_count }
  // 图表只对时间轴（X 轴）做缩放/平移。offsetX 表示当前视窗
  // 在完整数据范围中的起点比例。
  chartZoom: {
    scale: 1,
    offsetX: 0,
    minScale: 1,
    maxScale: 20
  },
  isDragging: false,
  // 当前服务端查询信息。默认浏览只取前 400 条；指定时间段后最多取 5000 条，
  // matchedCount/totalCount 用于明确提示是否存在截断，避免把“已加载”误当成“全表”。
  queryMeta: { totalCount: 0, matchedCount: 0, truncated: false },
  timeRange: {
    availableStart: null,
    availableEnd: null,
    start: null,
    end: null,
    active: false,
    valueType: 'text',
    inputMode: 'text',
  }
};

const els = {
  statsGrid: document.getElementById('statsGrid'),
  dbSelect: document.getElementById('dbSelect'),
  tableSelect: document.getElementById('tableSelect'),
  deleteTableBtn: document.getElementById('deleteTableBtn'),
  manageTablesModal: document.getElementById('manageTablesModal'),
  manageTablesSearch: document.getElementById('manageTablesSearch'),
  manageTablesList: document.getElementById('manageTablesList'),
  closeManageTablesBtn: document.getElementById('closeManageTablesBtn'),
  tableActionMessage: document.getElementById('tableActionMessage'),
  deleteTableModal: document.getElementById('deleteTableModal'),
  deleteTableDbName: document.getElementById('deleteTableDbName'),
  deleteTableName: document.getElementById('deleteTableName'),
  deleteTableConfirmInput: document.getElementById('deleteTableConfirmInput'),
  deleteTableError: document.getElementById('deleteTableError'),
  confirmDeleteTableBtn: document.getElementById('confirmDeleteTableBtn'),
  cancelDeleteTableBtn: document.getElementById('cancelDeleteTableBtn'),
  closeDeleteTableModalBtn: document.getElementById('closeDeleteTableModalBtn'),
  modelFilter: document.getElementById('modelFilter'),
  subsystemFilter: document.getElementById('subsystemFilter'),
  deviceFilter: document.getElementById('deviceFilter'),
  variableFilter: document.getElementById('variableFilter'),
  datasetMeta: document.getElementById('datasetMeta'),
  timeColSelect: document.getElementById('timeColSelect'),
  labelColSelect: document.getElementById('labelColSelect'),
  timeRangeFilter: document.getElementById('timeRangeFilter'),
  timeRangeAvailable: document.getElementById('timeRangeAvailable'),
  timeRangeStart: document.getElementById('timeRangeStart'),
  timeRangeEnd: document.getElementById('timeRangeEnd'),
  applyTimeRangeBtn: document.getElementById('applyTimeRangeBtn'),
  clearTimeRangeBtn: document.getElementById('clearTimeRangeBtn'),
  timeRangeStatus: document.getElementById('timeRangeStatus'),
  featureChips: document.getElementById('featureChips'),
  featureSelectionStatus: document.getElementById('featureSelectionStatus'),
  selectAllFeaturesBtn: document.getElementById('selectAllFeaturesBtn'),
  clearFeaturesBtn: document.getElementById('clearFeaturesBtn'),
  faultList: document.getElementById('faultList'),
  chart: document.getElementById('chart'),
  dataTable: document.getElementById('dataTable'),
  tableMeta: document.getElementById('tableMeta'),
  // 数据预处理元素
  preprocessingModule: document.getElementById('preprocessingModule'),
  preprocessingStatus: document.getElementById('preprocessingStatus'),
  preprocessingConfig: document.getElementById('preprocessingConfig'),
  runPreprocessingBtn: document.getElementById('runPreprocessingBtn'),
  deleteCleanDataBtn: document.getElementById('deleteCleanDataBtn'),
  smoothWindowSize: document.getElementById('smoothWindowSize'),
  outlierThreshold: document.getElementById('outlierThreshold'),
  normalizationMethod: document.getElementById('normalizationMethod'),
  dataViewRadios: document.querySelectorAll('input[name="dataView"]'),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
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

function fmt(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(2) : String(value ?? '');
}

const DEFAULT_ROW_LIMIT = 400;
const TIME_RANGE_ROW_LIMIT = 5000;

function currentDataTableName() {
  if (!state.selectedTable) return '';
  if (state.dataView === 'clean' && state.preprocessingStatus && state.preprocessingStatus.valid) {
    return `${state.selectedTable}_clean`;
  }
  return state.selectedTable;
}

function resetTimeRangeState({ keepAvailable = false } = {}) {
  const availableStart = keepAvailable ? state.timeRange.availableStart : null;
  const availableEnd = keepAvailable ? state.timeRange.availableEnd : null;
  const valueType = keepAvailable ? state.timeRange.valueType : 'text';
  const inputMode = keepAvailable ? state.timeRange.inputMode : 'text';
  state.timeRange = {
    availableStart,
    availableEnd,
    start: availableStart,
    end: availableEnd,
    active: false,
    valueType,
    inputMode,
  };
}

function isSimpleLocalDateTime(value) {
  return /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$/.test(String(value ?? '').trim());
}

function inferTimeInputMode(payload) {
  if (payload?.value_type === 'number') return 'number';
  if (isSimpleLocalDateTime(payload?.min) && isSimpleLocalDateTime(payload?.max)) return 'datetime-local';
  return 'text';
}

function valueForTimeInput(value, mode) {
  if (value === null || value === undefined) return '';
  const text = String(value);
  if (mode === 'datetime-local') return text.replace(' ', 'T');
  return text;
}

function valueForTimeApi(value, mode) {
  const text = String(value ?? '').trim();
  if (mode === 'datetime-local') return text.replace('T', ' ');
  return text;
}

function compareTimeValues(left, right, mode) {
  if (mode === 'number') {
    const a = Number(left);
    const b = Number(right);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
    return a === b ? 0 : (a < b ? -1 : 1);
  }
  if (mode === 'datetime-local' || (isSimpleLocalDateTime(left) && isSimpleLocalDateTime(right))) {
    const a = Date.parse(String(left).replace(' ', 'T'));
    const b = Date.parse(String(right).replace(' ', 'T'));
    if (Number.isFinite(a) && Number.isFinite(b)) return a === b ? 0 : (a < b ? -1 : 1);
  }
  const a = String(left ?? '');
  const b = String(right ?? '');
  return a === b ? 0 : (a < b ? -1 : 1);
}

function configureTimeInput(input, mode, value) {
  if (!input) return;
  input.value = '';
  input.type = mode;
  if (mode === 'number') {
    input.step = 'any';
  } else if (mode === 'datetime-local') {
    input.step = '1';
  } else {
    input.removeAttribute('step');
  }
  input.value = valueForTimeInput(value, mode);
}

function renderTimeRangeControls() {
  if (!els.timeRangeFilter) return;
  const range = state.timeRange;
  const hasRange = range.availableStart !== null && range.availableEnd !== null;
  els.timeRangeFilter.classList.toggle('is-active', Boolean(range.active));
  els.timeRangeFilter.classList.toggle('is-unavailable', !hasRange);

  if (els.timeRangeAvailable) {
    els.timeRangeAvailable.textContent = hasRange
      ? `可用：${range.availableStart} → ${range.availableEnd}`
      : '当前时间列没有可用范围';
  }
  if (els.applyTimeRangeBtn) els.applyTimeRangeBtn.disabled = !hasRange;
  if (els.clearTimeRangeBtn) els.clearTimeRangeBtn.disabled = !range.active;

  if (!els.timeRangeStatus) return;
  els.timeRangeStatus.classList.remove('is-warning', 'is-error', 'is-active');
  const loaded = state.rows.length;
  const total = state.queryMeta.totalCount || 0;
  const matched = state.queryMeta.matchedCount || 0;
  if (range.active) {
    els.timeRangeStatus.classList.add('is-active');
    if (state.queryMeta.truncated) {
      els.timeRangeStatus.classList.add('is-warning');
      els.timeRangeStatus.textContent = `当前时间段 ${range.start} → ${range.end}，匹配 ${matched} 条；当前加载前 ${loaded} 条。请缩小时间范围以完整查看。`;
    } else {
      els.timeRangeStatus.textContent = `当前时间段 ${range.start} → ${range.end}，共 ${matched} 条。`;
    }
  } else if (state.queryMeta.truncated) {
    els.timeRangeStatus.classList.add('is-warning');
    els.timeRangeStatus.textContent = `未设置时间筛选；当前加载前 ${loaded} / ${total} 条。可设置起止时间查看表内任意时间段。`;
  } else {
    els.timeRangeStatus.textContent = total ? `未设置时间筛选；当前表共 ${total} 条。` : '未设置时间筛选。';
  }
}

async function loadTimeRangeMetadata({ resetSelection = true } = {}) {
  if (!state.selectedDb?.value || !state.selectedTable || !state.selectedTimeCol) {
    resetTimeRangeState();
    renderTimeRangeControls();
    return;
  }
  const tableName = currentDataTableName();
  if (!tableName) return;
  try {
    const payload = await api(`/api/sqlite-time-range?db=${encodeURIComponent(state.selectedDb.value)}&table=${encodeURIComponent(tableName)}&time_col=${encodeURIComponent(state.selectedTimeCol)}`);
    const mode = inferTimeInputMode(payload);
    state.timeRange.availableStart = payload.min;
    state.timeRange.availableEnd = payload.max;
    state.timeRange.valueType = payload.value_type || 'text';
    state.timeRange.inputMode = mode;
    if (resetSelection || !state.timeRange.active) {
      state.timeRange.start = payload.min;
      state.timeRange.end = payload.max;
      state.timeRange.active = false;
      configureTimeInput(els.timeRangeStart, mode, payload.min);
      configureTimeInput(els.timeRangeEnd, mode, payload.max);
    } else {
      configureTimeInput(els.timeRangeStart, mode, state.timeRange.start);
      configureTimeInput(els.timeRangeEnd, mode, state.timeRange.end);
    }
  } catch (error) {
    console.error('读取时间范围失败:', error);
    resetTimeRangeState();
    if (els.timeRangeStatus) {
      els.timeRangeStatus.classList.add('is-error');
      els.timeRangeStatus.textContent = `时间范围读取失败：${error.message}`;
    }
  }
  renderTimeRangeControls();
}

async function applyTimeRange() {
  const mode = state.timeRange.inputMode || 'text';
  const start = valueForTimeApi(els.timeRangeStart?.value, mode);
  const end = valueForTimeApi(els.timeRangeEnd?.value, mode);
  if (!start || !end) {
    alert('请同时填写起始时间和结束时间。');
    return;
  }
  const order = compareTimeValues(start, end, mode);
  if (order !== null && order > 0) {
    alert('起始时间不能晚于结束时间。');
    return;
  }
  const min = state.timeRange.availableStart;
  const max = state.timeRange.availableEnd;
  if (min !== null && compareTimeValues(start, min, mode) < 0) {
    alert(`起始时间早于当前数据表起始时间：${min}`);
    return;
  }
  if (max !== null && compareTimeValues(end, max, mode) > 0) {
    alert(`结束时间晚于当前数据表结束时间：${max}`);
    return;
  }

  state.timeRange.start = start;
  state.timeRange.end = end;
  state.timeRange.active = true;
  resetChartZoomState();
  await loadTableRows({ preserveTimeRange: true });
}

async function clearTimeRange() {
  resetTimeRangeState({ keepAvailable: true });
  configureTimeInput(els.timeRangeStart, state.timeRange.inputMode, state.timeRange.availableStart);
  configureTimeInput(els.timeRangeEnd, state.timeRange.inputMode, state.timeRange.availableEnd);
  resetChartZoomState();
  await loadTableRows({ preserveTimeRange: true });
}

function renderSelectOptions(select, items, selectedValue) {
  if (!select) return;
  if (!items.length) {
    select.innerHTML = '<option value="">暂无数据</option>';
    return;
  }
  select.innerHTML = items.map((item) => `<option value="${item.value}">${item.label}</option>`).join('');
  if (selectedValue !== undefined && selectedValue !== null && selectedValue !== '') {
    select.value = selectedValue;
  }
}

function uniqueValues(rows, key) {
  return [...new Set(rows.map((row) => row[key]).filter((value) => value !== undefined && value !== null && value !== ''))];
}

function currentNumericColumns(columns) {
  const excluded = new Set(['id', 'timestamp', 'label', 'fault_id', 'fault_description', 'fault_severity',
    'affected_component', 'fault_log_text', 'model', 'subsystem', 'device', 'variable',
    'normal_start_time', 'normal_end_time']);
  return columns.filter(column => !excluded.has(column.toLowerCase()) &&
    !/^(?:time|date|.*_time|.*_id)$/i.test(column) &&
    state.rows.some(row => row[column] !== null && row[column] !== '' &&
      Number.isFinite(Number(row[column]))));
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderStats() {
  const totalRows = state.filteredRows.length;
  const loadedRows = state.rows.length;
  const fullRows = state.queryMeta.totalCount
    || state.tables.find((item) => item.name === state.selectedTable)?.row_count
    || loadedRows;
  const matchedRows = state.queryMeta.matchedCount || loadedRows;
  const featureCount = state.selectedTables.length > 1 ? state.selectedTables.length : state.selectedFeatures.length;
  const faultCount = state.filteredRows.filter((row) => {
    const label = row.label;
    return label && label !== '0' && label !== 0;
  }).length;
  const faultRate = totalRows > 0 ? `${((faultCount / totalRows) * 100).toFixed(1)}%` : '0%';
  const dbLabel = state.selectedDb ? state.selectedDb.label : '未选择';
  const tableLabel = state.selectedTable || '未选择数据表';
  const featureNames = state.selectedTables.length > 1 ? state.selectedTables.join(' / ') : 'value';
  let rowNote;
  if (state.timeRange.active) {
    rowNote = state.queryMeta.truncated
      ? `时间段匹配 ${matchedRows} · 当前加载 ${loadedRows} / 全表 ${fullRows}`
      : `时间段 ${matchedRows} / 全表 ${fullRows}`;
  } else if (state.queryMeta.truncated) {
    rowNote = `当前加载 ${loadedRows} / 全表 ${fullRows}`;
  } else {
    rowNote = `总计 ${fullRows} 条`;
  }
  const tableInfo = state.tables.find((item) => item.name === state.selectedTable) || null;
  const roleLabel = tableInfo?.role_label || '未指定';
  const roleNote = tableInfo
    ? `${tableInfo.role_source === 'declared' ? '已声明' : '自动识别'} · 正常 ${tableInfo.normal_count ?? 0} / 故障 ${tableInfo.fault_count ?? 0}${tableInfo.role_valid === false ? ' · 组成不符合要求' : ''}`
    : '尚未选择数据表';

  els.statsGrid.innerHTML = [
    summaryItem('数据库', dbLabel, `当前表 · ${tableLabel}`, 'database'),
    summaryItem('数据用途', roleLabel, roleNote, tableInfo?.role_valid === false ? 'fault' : 'role'),
    summaryItem('数据量', `${totalRows} 条`, rowNote, 'rows'),
    summaryItem('特征', `${featureCount} 列`, featureNames, 'features'),
    summaryItem('故障样本', `${faultCount} / ${totalRows}`, `当前显示故障率 ${faultRate}`, faultCount > 0 ? 'fault' : 'normal'),
  ].join('');
}

function formatTableOption(item) {
  const role = item?.role_label || '未指定';
  const normal = item?.normal_count ?? 0;
  const fault = item?.fault_count ?? 0;
  const invalid = item?.role_valid === false ? ' ⚠' : '';
  return `[${role}] ${item.name} (${item.row_count} 条 · 正常 ${normal} / 故障 ${fault})${invalid}`;
}

function summaryItem(label, value, note, kind = '') {
  const safeLabel = escapeHtml(label);
  const safeValue = escapeHtml(value);
  const safeNote = escapeHtml(note);
  const safeKind = String(kind || '').replace(/[^a-z0-9_-]/gi, '');
  return `
    <article class="browser-summary-item ${safeKind ? `summary-item--${safeKind}` : ''}">
      <div class="summary-label">${safeLabel}</div>
      <div class="summary-value" title="${safeValue}">${safeValue}</div>
      <div class="summary-note" title="${safeNote}">${safeNote}</div>
    </article>
  `;
}

function applyFilters() {
  state.filteredRows = state.rows.filter((row) => {
    const passModel = !state.multiFilters.model.length || state.multiFilters.model.includes(row.model);
    const passSubsystem = !state.multiFilters.subsystem.length || state.multiFilters.subsystem.includes(row.subsystem);
    const passDevice = !state.multiFilters.device.length || state.multiFilters.device.includes(row.device);
    const passVariable = !state.multiFilters.variable.length || state.multiFilters.variable.includes(row.variable);
    return passModel && passSubsystem && passDevice && passVariable;
  });
}

function renderDatasetMeta() {
  const entries = Object.entries(state.datasetMeta || {});
  if (!entries.length) {
    els.datasetMeta.innerHTML = '<div class="meta-row"><div class="meta-key">提示</div><div class="meta-value">当前数据库没有 dataset_meta 表。</div></div>';
    return;
  }
  els.datasetMeta.innerHTML = entries.map(([key, value]) => `
    <div class="meta-row">
      <div class="meta-key">${key}</div>
      <div class="meta-value">${value}</div>
    </div>
  `).join('');
}

function renderHierarchyFilters() {
  renderSelectOptions(els.modelFilter, [{ label: '全部型号', value: '__ALL__' }, ...uniqueValues(state.rows, 'model').map((value) => ({ label: value, value }))], state.filters.model);
  renderSelectOptions(els.subsystemFilter, [{ label: '全部分系统', value: '__ALL__' }, ...uniqueValues(state.rows, 'subsystem').map((value) => ({ label: value, value }))], state.filters.subsystem);
  renderSelectOptions(els.deviceFilter, [{ label: '全部单机', value: '__ALL__' }, ...uniqueValues(state.rows, 'device').map((value) => ({ label: value, value }))], state.filters.device);
  renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }, ...uniqueValues(state.rows, 'variable').map((value) => ({ label: value, value }))], state.filters.variable);
}

function renderFeatureChips(columns) {
  // 单表单测量列：旧版附加数值列只为兼容读取，不作为绘图输入。
  state.selectedFeatures = columns.includes('value') ? ['value'] : [];
  if (els.featureChips) els.featureChips.replaceChildren();
}

function renderFaultList() {
  if (!state.filteredRows.length) {
    els.faultList.innerHTML = '<div class="empty-state">当前筛选条件下没有可显示的数据。</div>';
    return;
  }
  // 使用 label 字段过滤故障记录，与 renderStats 保持一致
  const rows = state.filteredRows.filter((row) => {
    const label = row.label;
    return label && label !== '0' && label !== 0;
  });
  const source = rows.length ? rows : state.filteredRows;
  const mergedSegments = mergeContinuousSegments(source);
  els.faultList.innerHTML = mergedSegments.map((segment) => `
    <button class="record-card clickable" data-fault-id="${segment.fault_id || ''}" data-segment='${JSON.stringify(segment)}'>
      <strong>${segment.fault_id || 'NORMAL'}</strong>
      <div class="desc">${segment.fault_description || '正常段/未标注'}</div>
      <div class="sub">${segment.count > 1 ? `× ${segment.count} 段` : ''} | ${segment.start_time} → ${segment.end_time}</div>
    </button>
  `).join('');
  els.faultList.querySelectorAll('.record-card.clickable').forEach((button) => {
    button.addEventListener('click', () => {
      const faultId = button.dataset.faultId;
      const segmentData = JSON.parse(button.dataset.segment || '{}');
      state.selectedRecord = {
        fault_id: faultId,
        start_time: segmentData.start_time,
        end_time: segmentData.end_time,
        ...segmentData
      };
      renderChart();
    });
  });
}

function mergeContinuousSegments(rows) {
  if (!rows.length) return [];
  const segments = [];
  let currentSegment = null;
  for (const row of rows) {
    const startTime = row.start_time || row.normal_start_time || row.timestamp;
    const endTime = row.end_time || row.normal_end_time || row.timestamp;
    const label = row.label;
    const isNormal = !label || label === '0' || label === 0;
    const labelKey = isNormal ? 'NORMAL' : `FAULT_${label}`;
    const description = row.fault_description || (isNormal ? '正常段/未标注' : `故障 (label=${label})`);
    if (!currentSegment) {
      currentSegment = {
        fault_id: labelKey,
        fault_description: description,
        start_time: startTime,
        end_time: endTime,
        count: 1,
        rows: [row]
      };
    } else if (labelKey === currentSegment.fault_id) {
      currentSegment.end_time = endTime;
      currentSegment.count++;
      currentSegment.rows.push(row);
    } else {
      segments.push(currentSegment);
      currentSegment = {
        fault_id: labelKey,
        fault_description: description,
        start_time: startTime,
        end_time: endTime,
        count: 1,
        rows: [row]
      };
    }
  }
  if (currentSegment) {
    segments.push(currentSegment);
  }
  return segments;
}

function renderTable() {
  if (!state.filteredRows.length) {
    els.dataTable.innerHTML = '';
    els.tableMeta.textContent = '暂无数据';
    return;
  }
  const columns = Object.keys(state.filteredRows[0]);
  const head = `<thead><tr>${columns.map((column) => `<th>${column}</th>`).join('')}</tr></thead>`;
  const body = `<tbody>${state.filteredRows.slice(0, 200).map((row) => `<tr>${columns.map((column) => `<td>${row[column] ?? ''}</td>`).join('')}</tr>`).join('')}</tbody>`;
  els.dataTable.innerHTML = head + body;
  const currentInfo = state.tables.find((item) => item.name === state.selectedTable);
  const rolePrefix = currentInfo?.role_label ? `[${currentInfo.role_label}] ` : '';
  const timeNote = state.timeRange.active ? ` · 时间段 ${state.timeRange.start} → ${state.timeRange.end}` : '';
  const matched = state.queryMeta.matchedCount || state.rows.length;
  const loadedNote = state.queryMeta.truncated ? `${state.rows.length}/${matched} 已加载` : `${state.filteredRows.length}/${matched}`;
  els.tableMeta.textContent = `${rolePrefix}${state.selectedTable || ''} · ${loadedNote}${timeNote}`;
}

const CHART_GEOMETRY = {
  width: 1100,
  height: 360,
  padding: { top: 26, right: 24, bottom: 46, left: 54 },
};

// Event overlays are a presentation of explicit ground-truth annotations.
// Uncovered regions are UNKNOWN; neither curve colors nor row-level labels imply normal.
function annotationKey() {
  return state.selectedDb?.value && state.selectedTable
    ? `${state.selectedDb.value}\u0000${state.selectedTable}` : '';
}
async function refreshFaultAnnotations() {
  const key = annotationKey();
  if (key !== state.intervalContextKey) state.annotationMode = false;
  const requestId = ++state.intervalLoadSequence;
  state.intervalAnnotations = [];
  state.annotationError = '';
  state.annotationAxis = null;
  state.annotationDrag = null;
  state.intervalContextKey = key;
  if (!key) { state.annotationMode = false; return; }
  try {
    const response = await api(`/api/fault-intervals?db=${encodeURIComponent(state.selectedDb.value)}&table=${encodeURIComponent(state.selectedTable)}`);
    if (requestId === state.intervalLoadSequence && key === annotationKey()) {
      state.intervalAnnotations = response.items || [];
    }
  } catch (error) {
    if (requestId === state.intervalLoadSequence && key === annotationKey()) {
      state.annotationError = error.message;
    }
  }
}
function intervalBandMarkup(axis, {width, height, padding}) {
  if (!axis || state.intervalContextKey !== annotationKey()) return {underlay:'', targets:''};
  const chart = window.AASFaultChart;
  const plotHeight = height - padding.top - padding.bottom;
  const listed = chart.bands(state.intervalAnnotations, axis, state.chartZoom, padding, width);
  const underlay = `<rect x="${padding.left}" y="${padding.top}" width="${width-padding.left-padding.right}" height="${plotHeight}" fill="#8894a8" opacity=".055" />`
    + listed.map(b => `<rect class="event-band ${b.label === 1 ? 'event-fault' : 'event-normal'}" x="${b.left}" y="${padding.top}" width="${b.right-b.left}" height="${plotHeight}" fill="${b.label === 1 ? '#e45b63' : '#27a58a'}" opacity=".16" />`).join('');
  // SVG draw order matters: draw ALL broad click targets first and ALL edge
  // handles second. Otherwise the next adjacent event hitbox covers the
  // previous event's boundary handle at their shared timestamp.
  const targets = !state.annotationMode ? '' :
    listed.map(b => `<rect class="event-hit" data-event-id="${b.id}" x="${b.left}" y="${padding.top}" width="${b.right-b.left}" height="${plotHeight}" fill="transparent" />`).join('')
    + listed.map(b => {
      // Start grips occupy the upper half, end grips the lower half. Adjacent
      // intervals can therefore independently edit their shared boundary.
      return (Math.abs(b.startX-b.left) < 0.01 ? `<rect class="event-handle event-handle-start" data-event-id="${b.id}" data-event-edge="start" x="${b.left-4}" y="${padding.top}" width="8" height="${plotHeight/2}" rx="2" />` : '')
        + (Math.abs(b.endX-b.right) < 0.01 ? `<rect class="event-handle event-handle-end" data-event-id="${b.id}" data-event-edge="end" x="${b.right-4}" y="${padding.top+plotHeight/2}" width="8" height="${plotHeight/2}" rx="2" />` : '');
    }).join('');
  return {underlay, targets};
}
function annotationToolbar() {
  if (!state.annotationAxis) return '<span class="chart-annotation-note">时间格式不适合图上标注，请使用事件表单</span>';
  return `<button type="button" id="chartAnnotateToggle" class="chart-btn${state.annotationMode ? ' annotation-enabled' : ''}" aria-pressed="${state.annotationMode}">${state.annotationMode ? '结束框选' : '框选标注'}</button>`;
}
function annotationLegend() {
  if (!state.annotationAxis) return '';
  return `<div class="event-chart-legend" aria-label="事件区间语义"><span><i class="event-legend-fault"></i>故障</span><span><i class="event-legend-normal"></i>已确认正常</span><span><i class="event-legend-unknown"></i>未知（未标注）</span><span>仅标注主表 · 左闭右开</span>${state.annotationError ? `<span role="alert">事件读取失败：${escapeHtml(state.annotationError)}</span>` : ''}</div>`;
}
function updateAnnotationDragPreview() {
  const svg = els.chart.querySelector('.chart-svg');
  const rect = svg?.querySelector('#chartAnnotationPreview');
  if (!rect || !state.annotationDrag) return;
  const drag = state.annotationDrag;
  const left = Math.min(drag.startX, drag.currentX);
  rect.setAttribute('x', String(left));
  rect.setAttribute('width', String(Math.abs(drag.currentX-drag.startX)));
  rect.setAttribute('visibility', 'visible');
}

function clampNumber(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function maxChartOffset(scale) {
  return Math.max(0, 1 - (1 / Math.max(scale, 1)));
}

function clampChartOffset(offset, scale = state.chartZoom.scale) {
  return clampNumber(Number(offset) || 0, 0, maxChartOffset(scale));
}

function resetChartZoomState() {
  state.chartZoom.scale = 1;
  state.chartZoom.offsetX = 0;
  state.isDragging = false;
}

/**
 * 以指定的“图内位置”为锚点缩放时间轴。
 * cursorRatio=0 表示绘图区最左端，1 表示最右端。
 * 缩放前后，光标下方对应的同一个数据位置保持不动。
 */
function setChartZoomAt(nextScale, cursorRatio = 0.5) {
  const { scale, offsetX, minScale, maxScale } = state.chartZoom;
  const ratio = clampNumber(Number(cursorRatio) || 0, 0, 1);
  const targetScale = clampNumber(Number(nextScale) || scale, minScale, maxScale);

  // 当前光标对应完整数据范围中的归一化位置。
  const anchor = offsetX + (ratio / Math.max(scale, 1));
  state.chartZoom.scale = targetScale;
  state.chartZoom.offsetX = targetScale <= 1
    ? 0
    : clampChartOffset(anchor - (ratio / targetScale), targetScale);
}

function chartPointerPosition(svg, clientX, clientY) {
  const rect = svg.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: ((clientX - rect.left) / rect.width) * CHART_GEOMETRY.width,
    y: ((clientY - rect.top) / rect.height) * CHART_GEOMETRY.height,
    rect,
  };
}

function chartCursorRatio(svg, clientX) {
  const point = chartPointerPosition(svg, clientX, 0);
  if (!point) return 0.5;
  const { left, right } = CHART_GEOMETRY.padding;
  const plotWidth = CHART_GEOMETRY.width - left - right;
  return clampNumber((point.x - left) / plotWidth, 0, 1);
}

function pointerInsideChartPlot(svg, clientX, clientY) {
  const point = chartPointerPosition(svg, clientX, clientY);
  if (!point) return false;
  const { top, right, bottom, left } = CHART_GEOMETRY.padding;
  return point.x >= left
    && point.x <= CHART_GEOMETRY.width - right
    && point.y >= top
    && point.y <= CHART_GEOMETRY.height - bottom;
}

function renderChart() {
  if (state.selectedTables.length > 1) {
    renderMultiTableChart();
    return;
  }
  if (!state.filteredRows.length || !state.selectedTimeCol || !state.selectedFeatures.length) {
    state.annotationAxis = null;
    els.chart.innerHTML = '<div class="empty-state">请选择时间列和特征列后查看曲线。</div>';
    return;
  }

  const { width, height, padding } = CHART_GEOMETRY;
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const { scale } = state.chartZoom;
  state.chartZoom.offsetX = clampChartOffset(state.chartZoom.offsetX, scale);
  const offsetX = state.chartZoom.offsetX;

  const rowCount = state.filteredRows.length;
  const denominator = Math.max(rowCount - 1, 1);
  const series = state.selectedFeatures.map(feature => state.filteredRows.map(row => {
    const raw = row[feature];
    return raw === null || raw === undefined || String(raw).trim() === '' ? NaN : Number(raw);
  }));
  const limits = series.map(values => {
    const finite = values.filter(Number.isFinite);
    return {
      min: finite.length ? Math.min(...finite) : NaN,
      max: finite.length ? Math.max(...finite) : NaN,
    };
  });
  const validLimits = limits.filter(item => Number.isFinite(item.min));
  if (!validLimits.length) {
    state.annotationAxis = null;
    els.chart.innerHTML = '<div class="empty-state">当前特征没有可绘制的数值数据。</div>';
    return;
  }

  const min = Math.min(...validLimits.map(item => item.min));
  const max = Math.max(...validLimits.map(item => item.max));
  const dataRange = max - min || 1;
  const independent = state.chartValueMode === 'independent' && state.selectedFeatures.length > 1;
  const yScale = (value, featureIndex) => {
    const limitsForFeature = limits[featureIndex];
    const fraction = independent
      ? (limitsForFeature.max === limitsForFeature.min
        ? 0.5
        : (value - limitsForFeature.min) / (limitsForFeature.max - limitsForFeature.min))
      : (value - min) / dataRange;
    return height - padding.bottom - fraction * plotHeight;
  };

  // Real timestamp spacing matters for accurate event overlays; index-based spacing
  // is used only for legacy tables with unparseable/mixed timestamp kinds.
  const times = state.filteredRows.map(row => row[state.selectedTimeCol]);
  const axis = window.AASFaultChart.axisFor(times);
  state.annotationAxis = axis;
  const geometry = axis && window.AASFaultChart.geometryFor(axis, state.chartZoom, padding, width);
  const xScale = index => geometry
    ? geometry.timeX(window.AASFaultChart.parseTime(times[index]).seconds)
    : padding.left + (((index / denominator) - offsetX) * scale * plotWidth);
  const annotation = intervalBandMarkup(axis, CHART_GEOMETRY);
  const colors = ['#4f78e8', '#159e8c', '#d58b22', '#d05178', '#8964ce', '#2d96b8', '#bc6b32'];
  const lines = state.selectedFeatures.map((feature, featureIndex) => {
    const points = series[featureIndex].map((value, index) => (
      Number.isFinite(value) ? `${xScale(index)},${yScale(value, featureIndex)}` : null
    )).filter(Boolean).join(' ');
    return `<polyline fill="none" stroke="${colors[featureIndex % colors.length]}" stroke-width="2.2" vector-effect="non-scaling-stroke" points="${points}" />`;
  }).join('');

  let faultBand = '';
  if (state.selectedRecord && state.selectedRecord.start_time && state.selectedRecord.end_time) {
    const startIndex = state.filteredRows.findIndex(row => row[state.selectedTimeCol] >= state.selectedRecord.start_time);
    const endIndex = state.filteredRows.findIndex(row => row[state.selectedTimeCol] > state.selectedRecord.end_time);
    const left = xScale(startIndex >= 0 ? startIndex : 0);
    const right = xScale(endIndex > 0 ? endIndex : rowCount - 1);
    faultBand = `<rect x="${left}" y="${padding.top}" width="${Math.max(12, right - left)}" height="${plotHeight}" fill="rgba(255,138,138,0.10)" stroke="rgba(255,138,138,0.28)" />`;
  }

  const grid = Array.from({ length: 5 }, (_, index) => {
    const fraction = index / 4;
    const y = height - padding.bottom - fraction * plotHeight;
    const label = independent ? `${Math.round(fraction * 100)}%` : fmt(min + fraction * dataRange);
    return `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="var(--line)" />
      <text x="12" y="${y + 4}" fill="var(--muted)" font-size="11">${label}</text>`;
  }).join('');

  const visibleStart = offsetX;
  const visibleEnd = Math.min(1, offsetX + (1 / Math.max(scale, 1)));
  const startIndex = clampNumber(Math.floor(visibleStart * denominator), 0, rowCount - 1);
  const endIndex = clampNumber(Math.ceil(visibleEnd * denominator), 0, rowCount - 1);
  const xLabelLeft = escapeHtml(axis
    ? window.AASFaultChart.toTimestamp(geometry.atX(padding.left), axis.kind)
    : state.filteredRows[startIndex][state.selectedTimeCol]);
  const xLabelRight = escapeHtml(axis
    ? window.AASFaultChart.toTimestamp(geometry.atX(width-padding.right), axis.kind)
    : state.filteredRows[endIndex][state.selectedTimeCol]);
  const formatLimit = value => Number(value).toPrecision(5).replace(/\.?0+$/, '');
  const legend = state.selectedFeatures.map((feature, index) => {
    const { min: fmin, max: fmax } = limits[index];
    const range = Number.isFinite(fmin)
      ? `${formatLimit(fmin)} ～ ${formatLimit(fmax)}` : '无有效数据';
    return `<span class="multi-series-legend-item"><i style="background:${colors[index % colors.length]}"></i><strong>${escapeHtml(feature)}</strong><small>${escapeHtml(range)}</small></span>`;
  }).join('');

  els.chart.innerHTML = `
    <div class="chart-toolbar">
      <span class="chart-interaction-hint">滚轮：以光标为中心缩放 · 拖拽左右平移 · 框选标注</span>
      ${annotationToolbar()}
      <label class="browser-scale-control">Y 轴显示
        <select id="browserChartScaleMode" aria-label="曲线纵轴尺度">
          <option value="independent" ${state.chartValueMode === 'independent' ? 'selected' : ''}>各特征独立尺度</option>
          <option value="shared" ${state.chartValueMode === 'shared' ? 'selected' : ''}>原始数值共同尺度</option>
        </select>
      </label>
      <button class="chart-btn" onclick="zoomIn()" title="以当前视窗中心放大">放大</button>
      <button class="chart-btn" onclick="zoomOut()" title="以当前视窗中心缩小">缩小</button>
      <button class="chart-btn" onclick="resetZoom()" title="恢复完整时间范围">重置</button>
      <span class="zoom-level">${(scale * 100).toFixed(0)}%</span>
    </div>
    <svg class="chart-svg${state.isDragging ? ' is-dragging' : ''}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="多特征时间序列曲线。支持鼠标滚轮缩放和拖拽平移。">
      <defs><clipPath id="browserChartPlotClip"><rect x="${padding.left}" y="${padding.top}" width="${plotWidth}" height="${plotHeight}" /></clipPath></defs>
      ${grid}
      <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--line-strong)" />
      <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--line-strong)" />
      <g clip-path="url(#browserChartPlotClip)">${annotation.underlay}${faultBand}${lines}${annotation.targets}<rect id="chartAnnotationPreview" class="event-selection-preview" x="0" y="${padding.top}" width="0" height="${plotHeight}" visibility="hidden" /></g>
      <text x="${padding.left}" y="${height - 16}" fill="var(--muted)" font-size="11" text-anchor="start">${xLabelLeft}</text>
      <text x="${width - padding.right}" y="${height - 16}" fill="var(--muted)" font-size="11" text-anchor="end">${xLabelRight}</text>
    </svg>
    <div class="multi-series-legend" aria-label="曲线图例和原始数值范围">${legend}</div>${annotationLegend()}
    ${independent ? '<div class="chart-scale-hint">各特征分别按当前查询数据的最小/最大值映射到 0–100%，只改变曲线显示，不修改原始数据。图例保留各列原始范围。</div>' : ''}
  `;
  els.chart.querySelector('#browserChartScaleMode')?.addEventListener('change', event => {
    state.chartValueMode = event.target.value;
    renderChart();
  });
  bindChartEvents();
}

function zoomIn() {
  setChartZoomAt(state.chartZoom.scale * 1.3, 0.5);
  renderChart();
}

function zoomOut() {
  setChartZoomAt(state.chartZoom.scale / 1.3, 0.5);
  renderChart();
}

function resetZoom() {
  resetChartZoomState();
  renderChart();
}

function bindChartEvents() {
  if (!els.chart || els.chart.dataset.interactionsBound === 'true') return;
  els.chart.dataset.interactionsBound = 'true';

  let dragStartX = 0;
  let dragStartOffset = 0;

  els.chart.addEventListener('pointerdown', (event) => {
    const chartSvg = event.target.closest?.('.chart-svg');
    if (!chartSvg || event.button !== 0 || !pointerInsideChartPlot(chartSvg, event.clientX, event.clientY)) return;
    if (state.annotationMode && state.annotationAxis) {
      const point = chartPointerPosition(chartSvg, event.clientX, event.clientY);
      const hit = event.target.closest?.('[data-event-id]');
      const id = Number(hit?.dataset.eventId || 0);
      if (id && !hit.dataset.eventEdge) {
        window.AASFaultEditor?.editById(id);
        event.preventDefault(); return;
      }
      const axis = state.annotationAxis;
      const existing = state.intervalAnnotations.find(item => Number(item.id) === id);
      const edge = existing && hit?.dataset.eventEdge;
      const existingTime = edge ? window.AASFaultChart.parseTime(existing[edge === 'start' ? 'start_timestamp' : 'end_timestamp']) : null;
      const geometry = window.AASFaultChart.geometryFor(axis, state.chartZoom, CHART_GEOMETRY.padding, CHART_GEOMETRY.width);
      const startX = existingTime ? geometry.timeX(existingTime.seconds) : point.x;
      state.annotationDrag = { startX, currentX: point.x, edge: edge || '', id,
        from: existing ? existing.start_timestamp : null, to: existing ? existing.end_timestamp : null,
        contextKey: annotationKey(), axis };
      updateAnnotationDragPreview();
      event.preventDefault(); return;
    }
    if (state.chartZoom.scale <= 1) return;

    state.isDragging = true;
    dragStartX = event.clientX;
    dragStartOffset = state.chartZoom.offsetX;
    chartSvg.classList.add('is-dragging');
    event.preventDefault();
  });

  window.addEventListener('pointermove', (event) => {
    if (state.annotationDrag) {
      const svg = els.chart.querySelector('.chart-svg');
      const pos = svg && chartPointerPosition(svg, event.clientX, event.clientY);
      if (pos) {
        state.annotationDrag.currentX = clampNumber(pos.x, CHART_GEOMETRY.padding.left,
          CHART_GEOMETRY.width - CHART_GEOMETRY.padding.right);
        updateAnnotationDragPreview();
      }
      return;
    }
    if (!state.isDragging) return;
    const chartSvg = els.chart.querySelector('.chart-svg');
    if (!chartSvg) return;
    const rect = chartSvg.getBoundingClientRect();
    const plotWidthPx = rect.width * ((CHART_GEOMETRY.width - CHART_GEOMETRY.padding.left - CHART_GEOMETRY.padding.right) / CHART_GEOMETRY.width);
    if (!plotWidthPx) return;

    // 向右拖动曲线 = 查看更早的数据，因此视窗起点减小。
    const deltaNormalized = (event.clientX - dragStartX) / (plotWidthPx * state.chartZoom.scale);
    state.chartZoom.offsetX = clampChartOffset(dragStartOffset - deltaNormalized);
    renderChart();
  });

  const stopDragging = () => {
    if (state.annotationDrag) {
      const drag = state.annotationDrag;
      state.annotationDrag = null;
      const preview = els.chart.querySelector('#chartAnnotationPreview');
      preview?.setAttribute('visibility', 'hidden');
      if (drag.contextKey !== annotationKey() || !state.annotationMode || !state.annotationAxis) return;
      const { atX } = window.AASFaultChart.geometryFor(drag.axis, state.chartZoom,
        CHART_GEOMETRY.padding, CHART_GEOMETRY.width);
      const chart = window.AASFaultChart;
      if (Math.abs(drag.currentX - drag.startX) < 2) {
        if (drag.edge && drag.id) window.AASFaultEditor?.editById(drag.id);
        return;
      }
      const endpoint = chart.toTimestamp(atX(drag.currentX), drag.axis.kind);
      if (drag.edge) {
        window.AASFaultEditor?.selectRange(drag.edge === 'start' ? endpoint : drag.from,
          drag.edge === 'end' ? endpoint : drag.to, drag.id);
      } else {
        const values = [atX(drag.startX), atX(drag.currentX)].sort((a,b) => a-b);
        window.AASFaultEditor?.selectRange(chart.toTimestamp(values[0], drag.axis.kind),
          chart.toTimestamp(values[1], drag.axis.kind));
      }
      return;
    }
    if (!state.isDragging) return;
    state.isDragging = false;
    const chartSvg = els.chart.querySelector('.chart-svg');
    if (chartSvg) chartSvg.classList.remove('is-dragging');
  };
  window.addEventListener('pointerup', stopDragging);
  window.addEventListener('pointercancel', stopDragging);
  window.addEventListener('blur', stopDragging);

  els.chart.addEventListener('click', event => {
    if (event.target.closest?.('#chartAnnotateToggle')) {
      state.annotationMode = !state.annotationMode;
      state.annotationDrag = null;
      renderChart();
    }
  });
  els.chart.addEventListener('wheel', (event) => {
    const chartSvg = event.target.closest?.('.chart-svg');
    if (!chartSvg || !pointerInsideChartPlot(chartSvg, event.clientX, event.clientY)) return;
    event.preventDefault();

    const cursorRatio = chartCursorRatio(chartSvg, event.clientX);
    const factor = event.deltaY < 0 ? 1.18 : (1 / 1.18);
    setChartZoomAt(state.chartZoom.scale * factor, cursorRatio);
    renderChart();
  }, { passive: false });
}

function updateSelectors(columns) {
  const timeCandidates = columns.filter((column) => /time|timestamp/i.test(column));
  const labelCandidates = columns.filter((column) => /label/i.test(column));
  if (!columns.includes(state.selectedTimeCol)) {
    state.selectedTimeCol = timeCandidates[0] || columns[0] || null;
  }
  if (!columns.includes(state.selectedLabelCol)) {
    state.selectedLabelCol = labelCandidates[0] || '';
  }
  renderSelectOptions(els.timeColSelect, columns.map((column) => ({ label: column, value: column })), state.selectedTimeCol);
  renderSelectOptions(els.labelColSelect, columns.map((column) => ({ label: column, value: column })), state.selectedLabelCol);
}

async function loadDatasetMeta() {
  if (!state.selectedDb) {
    state.datasetMeta = {};
    renderDatasetMeta();
    return;
  }
  try {
    const payload = await api(`/api/sqlite-meta?db=${encodeURIComponent(state.selectedDb.value)}`);
    state.datasetMeta = payload.meta || {};
  } catch {
    state.datasetMeta = {};
  }
  renderDatasetMeta();
}

async function loadTableRows({ preserveTimeRange = false } = {}) {
  resetChartZoomState();
  if (!preserveTimeRange) {
    resetTimeRangeState();
    state.queryMeta = { totalCount: 0, matchedCount: 0, truncated: false };
  }
  if (!state.selectedDb || !state.selectedTable) {
    state.multiSeries = [];
    state.selectedFeatures = [];
    state.selectedFeatureSource = null;
    if (els.featureChips) els.featureChips.replaceChildren();
    if (els.featureSelectionStatus) els.featureSelectionStatus.textContent = '未选择数据表';
    state.rows = [];
    state.filteredRows = [];
    renderFaultList();
    renderTable();
    renderChart();
    renderStats();
    renderTimeRangeControls();
    return;
  }

  const tableName = currentDataTableName();
  showSkeleton(els.faultList, 2);
  let payload;
  try {
    const params = new URLSearchParams({
      db: state.selectedDb.value,
      table: tableName,
      limit: String(state.timeRange.active ? TIME_RANGE_ROW_LIMIT : DEFAULT_ROW_LIMIT),
    });
    if (state.timeRange.active && state.selectedTimeCol) {
      params.set('time_col', state.selectedTimeCol);
      params.set('start_time', state.timeRange.start);
      params.set('end_time', state.timeRange.end);
    }
    payload = await api(`/api/sqlite-rows?${params.toString()}`);
  } catch (error) {
    console.error('加载数据行失败:', error);
    showErrorState(els.faultList, '数据加载失败：' + error.message, () => loadTableRows({ preserveTimeRange }));
    if (els.timeRangeStatus) {
      els.timeRangeStatus.classList.add('is-error');
      els.timeRangeStatus.textContent = `时间段数据加载失败：${error.message}`;
    }
    return;
  }
  state.rows = payload.rows || [];
  state.queryMeta = {
    totalCount: Number(payload.total_count ?? state.rows.length),
    matchedCount: Number(payload.matched_count ?? state.rows.length),
    truncated: Boolean(payload.truncated),
  };
  state.selectedRecord = null;
  // 某些兼容接口只报告标准列；并入实际返回行中的扩展数值列。
  const columns = [...new Set([...(payload.columns || []), ...Object.keys(state.rows[0] || {})])];
  updateSelectors(columns);
  await loadTimeRangeMetadata({ resetSelection: !preserveTimeRange });
  applyFilters();
  renderFeatureChips(columns);
  await refreshFaultAnnotations();
  renderFaultList();
  renderTable();
  renderChart();
  renderStats();
  renderTimeRangeControls();
  if (state.selectedTables.length > 1) {
    try {
      await loadMultiTableSeries();
      renderChart();
    } catch (error) {
      state.multiSeries = [];
      els.chart.textContent = `多表曲线加载失败：${error.message}`;
    }
  }
}

function syncDeleteTableButton() {
  if (els.deleteTableBtn) els.deleteTableBtn.disabled = !state.selectedDb?.value;
}
function renderManageTables() {
  if (!els.manageTablesList) return;
  const query = (els.manageTablesSearch?.value || '').trim().toLowerCase();
  const tables = allManagedTables.filter(item => !query || item.name.toLowerCase().includes(query));
  els.manageTablesList.replaceChildren();
  if (!tables.length) {
    els.manageTablesList.textContent = '没有匹配的数据表。';
    return;
  }
  for (const item of tables) {
    const row = document.createElement('div');
    row.className = 'browser-manage-row';
    const detail = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = item.name;
    const note = document.createElement('small');
    note.textContent = `${Number(item.row_count || 0)} 条记录 · ${item.role_label || '未指定'}`;
    detail.append(title, note);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'danger-ghost-btn';
    button.textContent = '删除';
    button.addEventListener('click', () => {
      els.manageTablesModal.classList.remove('show');
      openDeleteTableModal(item.name);
    });
    row.append(detail, button);
    els.manageTablesList.append(row);
  }
}

function showTableActionMessage(message, error = false) {
  if (!els.tableActionMessage) return;
  els.tableActionMessage.textContent = message || '';
  els.tableActionMessage.classList.toggle('is-error', error);
}

let pendingTableDeletion = null;
let allManagedTables = [];

function closeDeleteTableModal() {
  if (els.confirmDeleteTableBtn?.disabled && els.confirmDeleteTableBtn?.textContent === '删除中…') return;
  if (els.deleteTableModal) els.deleteTableModal.style.display = 'none';
  if (els.deleteTableConfirmInput) els.deleteTableConfirmInput.value = '';
  pendingTableDeletion = null;
}

function openDeleteTableModal(tableName = state.selectedTable) {
  if (!state.selectedDb?.value || !tableName) return;
  pendingTableDeletion = { db: state.selectedDb.value, table: tableName };
  els.deleteTableDbName.textContent = state.selectedDb.label || state.selectedDb.value;
  els.deleteTableName.textContent = tableName;
  els.deleteTableConfirmInput.value = '';
  els.deleteTableError.textContent = '';
  els.confirmDeleteTableBtn.disabled = true;
  els.confirmDeleteTableBtn.textContent = '永久删除';
  els.deleteTableModal.style.display = 'flex';
  els.deleteTableConfirmInput.focus();
}

async function confirmDeleteHistoryTable() {
  const target = pendingTableDeletion;
  if (!target || els.deleteTableConfirmInput.value !== target.table) return;
  const button = els.confirmDeleteTableBtn;
  button.disabled = true;
  button.textContent = '删除中…';
  els.cancelDeleteTableBtn.disabled = true;
  els.closeDeleteTableModalBtn.disabled = true;
  try {
    const result = await api('/api/sqlite-table/delete', {
      method: 'POST',
      body: JSON.stringify(target),
    });
    button.textContent = '永久删除';
    closeDeleteTableModal();
    state.dataView = 'raw';
    document.querySelector('input[name="dataView"][value="raw"]').checked = true;
    state.preprocessingStatus = null;
    const deletedMessage = `已删除数据表 ${result.table_name}${result.deleted_clean_table ? '（含关联清洁表）' : ''}。`;
    try {
      await loadDatabases(target.db);
      showTableActionMessage(deletedMessage);
    } catch (refreshError) {
      showTableActionMessage(`${deletedMessage} 但刷新页面失败：${refreshError.message}，请手动刷新。`, true);
    }
  } catch (error) {
    els.deleteTableError.textContent = `删除失败：${error.message}`;
    showTableActionMessage(`删除失败：${error.message}`, true);
  } finally {
    button.textContent = '永久删除';
    els.cancelDeleteTableBtn.disabled = false;
    els.closeDeleteTableModalBtn.disabled = false;
    if (pendingTableDeletion) button.disabled = els.deleteTableConfirmInput.value !== pendingTableDeletion.table;
  }
}

els.deleteTableBtn?.addEventListener('click', async () => {
  if (!state.selectedDb) return;
  els.manageTablesSearch.value = '';
  els.manageTablesList.textContent = '正在读取全部数据表…';
  els.manageTablesModal.classList.add('show');
  const currentDb = state.selectedDb.value;
  try {
    const response = await api(`/api/sqlite-tables?db=${encodeURIComponent(currentDb)}`);
    if (state.selectedDb?.value !== currentDb) return;
    allManagedTables = response.items || [];
    renderManageTables();
  } catch (error) {
    els.manageTablesList.textContent = `无法加载数据表：${error.message}`;
  }
});
els.closeManageTablesBtn?.addEventListener('click', () => els.manageTablesModal.classList.remove('show'));
els.manageTablesModal?.addEventListener('click', event => {
  if (event.target === els.manageTablesModal) els.manageTablesModal.classList.remove('show');
});
els.manageTablesSearch?.addEventListener('input', renderManageTables);
els.deleteTableConfirmInput?.addEventListener('input', () => {
  els.confirmDeleteTableBtn.disabled = !pendingTableDeletion || els.deleteTableConfirmInput.value !== pendingTableDeletion.table;
});
els.confirmDeleteTableBtn?.addEventListener('click', confirmDeleteHistoryTable);
els.cancelDeleteTableBtn?.addEventListener('click', closeDeleteTableModal);
els.closeDeleteTableModalBtn?.addEventListener('click', closeDeleteTableModal);
els.deleteTableModal?.addEventListener('click', (event) => {
  if (event.target === els.deleteTableModal) closeDeleteTableModal();
});

async function loadDbTables(preferredTableName = null) {
  state.selectedDb = state.databases.find((item) => item.value === els.dbSelect.value) || state.databases[0] || null;
  if (!state.selectedDb) {
    state.tables = [];
    state.rows = [];
    state.filteredRows = [];
    state.selectedTable = null;
    renderSelectOptions(els.tableSelect, [], null);
    syncDeleteTableButton();
    updatePreprocessingModuleVisibility();
    renderSelectOptions(els.modelFilter, [{ label: '全部型号', value: '__ALL__' }], '__ALL__');
    renderSelectOptions(els.subsystemFilter, [{ label: '全部分系统', value: '__ALL__' }], '__ALL__');
    renderSelectOptions(els.deviceFilter, [{ label: '全部单机', value: '__ALL__' }], '__ALL__');
    renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }], '__ALL__');
    await loadDatasetMeta();
    renderFaultList();
    renderTable();
    renderChart();
    renderStats();
    return;
  }
  
  // 先加载所有表，获取所有可能的筛选选项
  let payload;
  try {
    payload = await api(`/api/sqlite-tables?db=${encodeURIComponent(state.selectedDb.value)}`);
  } catch (error) {
    console.error('加载数据表失败:', error);
    showErrorState(els.faultList, '数据表加载失败：' + error.message, loadDbTables);
    return;
  }
  state.tables = payload.items || [];
  state.multiFilters = {model:[],subsystem:[],device:[],variable:[]};
  state.selectedTables = [];
  refreshMultipleBrowserFilters();
  
  // 重置筛选条件
  state.filters = { model: '__ALL__', subsystem: '__ALL__', device: '__ALL__', variable: '__ALL__' };
  
  // 从后端获取级联筛选选项
  try {
    const hierarchyPayload = await api(`/api/hierarchy-options?db=${encodeURIComponent(state.selectedDb.value)}`);
    
    // 渲染筛选选项
    renderSelectOptions(els.modelFilter, [{ label: '全部型号', value: '__ALL__' }, ...hierarchyPayload.models.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.subsystemFilter, [{ label: '全部分系统', value: '__ALL__' }, ...hierarchyPayload.subsystems.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.deviceFilter, [{ label: '全部单机', value: '__ALL__' }, ...hierarchyPayload.devices.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }, ...hierarchyPayload.variables.map((value) => ({ label: value, value }))], '__ALL__');
  } catch (error) {
    console.error('加载筛选选项失败:', error);
    // 如果失败，使用旧的方法
    let allRows = [];
    for (const table of state.tables.slice(0, 10)) {
      try {
        const tablePayload = await api(`/api/sqlite-rows?db=${encodeURIComponent(state.selectedDb.value)}&table=${encodeURIComponent(table.name)}&limit=100`);
        const rows = tablePayload.rows || [];
        if (rows.length > 0) {
          allRows = [...allRows, ...rows];
          if (allRows.length >= 500) {
            break;
          }
        }
      } catch (error) {
        console.error(`加载表 ${table.name} 数据失败:`, error);
      }
    }
    
    const models = uniqueValues(allRows, 'model');
    const subsystems = uniqueValues(allRows, 'subsystem');
    const devices = uniqueValues(allRows, 'device');
    const variables = uniqueValues(allRows, 'variable');
    
    renderSelectOptions(els.modelFilter, [{ label: '全部型号', value: '__ALL__' }, ...models.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.subsystemFilter, [{ label: '全部分系统', value: '__ALL__' }, ...subsystems.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.deviceFilter, [{ label: '全部单机', value: '__ALL__' }, ...devices.map((value) => ({ label: value, value }))], '__ALL__');
    renderSelectOptions(els.variableFilter, [{ label: '全部参量', value: '__ALL__' }, ...variables.map((value) => ({ label: value, value }))], '__ALL__');
  }
  
  // 渲染表列表（初始时显示所有表）。导入完成后的刷新会优先选中新表，
  // 让用户能立即确认“导入成功”的数据确实已经进入目标数据库。
  const importedPreferredTable = typeof preferredTableName === 'string'
    && state.tables.some((item) => item.name === preferredTableName)
    ? preferredTableName
    : null;
  const preferredTable = importedPreferredTable
    || state.selectedDb.preferred_table
    || state.tables.find((item) => item.name === 'history_data')?.name
    || state.tables[0]?.name
    || '';
  renderSelectOptions(els.tableSelect, state.tables.map((item) => ({ label: formatTableOption(item), value: item.name })), preferredTable);
  state.selectedTable = preferredTable;
  state.selectedTables = preferredTable ? [preferredTable] : [];
  refreshMultipleBrowserTables();
  syncDeleteTableButton();
  updatePreprocessingModuleVisibility();

  await loadDatasetMeta();
  await loadTableRows();
}

async function loadDatabases(preferredDbValue = null, preferredTableName = null) {
  const payload = await api('/api/databases');
  state.databases = payload.items || [];

  const requestedDb = typeof preferredDbValue === 'string'
    ? state.databases.find((item) => item.value === preferredDbValue)
    : null;
  const currentDb = state.selectedDb
    ? state.databases.find((item) => item.value === state.selectedDb.value)
    : null;
  const preferredDb = requestedDb
    || currentDb
    || state.databases.find((item) => item.preferred_table === 'history_data')
    || state.databases[0]
    || null;

  renderSelectOptions(els.dbSelect, state.databases, preferredDb ? preferredDb.value : null);
  state.selectedDb = preferredDb;
  if (state.selectedDb) {
    await loadDbTables(preferredTableName);
  }
}

function resetDownstreamFilters(key) {
  // 级联筛选的父级变化后，旧的子级选择可能已经无效。先重置状态，
  // 再按新的父级条件重新获取候选项，避免拿旧子级条件去请求后端。
  if (key === 'model') {
    state.filters.subsystem = '__ALL__';
    state.filters.device = '__ALL__';
    state.filters.variable = '__ALL__';
  } else if (key === 'subsystem') {
    state.filters.device = '__ALL__';
    state.filters.variable = '__ALL__';
  } else if (key === 'device') {
    state.filters.variable = '__ALL__';
  }
}

function currentHierarchyParams() {
  return {
    model: state.filters.model === '__ALL__' ? null : state.filters.model,
    subsystem: state.filters.subsystem === '__ALL__' ? null : state.filters.subsystem,
    device: state.filters.device === '__ALL__' ? null : state.filters.device,
    variable: state.filters.variable === '__ALL__' ? null : state.filters.variable,
  };
}

function renderDownstreamHierarchyOptions(key, hierarchyPayload) {
  // “全部”不是“没有候选项”。父级为“全部”时，也应展示当前范围内所有
  // 可用的分系统/单机/参量，方便用户直接从任一级筛选。原实现会把下级
  // 下拉框只重置成“全部参量”，造成截图中的参量候选项消失。
  if (key === 'model') {
    renderSelectOptions(els.subsystemFilter, [
      { label: '全部分系统', value: '__ALL__' },
      ...hierarchyPayload.subsystems.map((value) => ({ label: value, value })),
    ], '__ALL__');
    renderSelectOptions(els.deviceFilter, [
      { label: '全部单机', value: '__ALL__' },
      ...hierarchyPayload.devices.map((value) => ({ label: value, value })),
    ], '__ALL__');
    renderSelectOptions(els.variableFilter, [
      { label: '全部参量', value: '__ALL__' },
      ...hierarchyPayload.variables.map((value) => ({ label: value, value })),
    ], '__ALL__');
  } else if (key === 'subsystem') {
    renderSelectOptions(els.deviceFilter, [
      { label: '全部单机', value: '__ALL__' },
      ...hierarchyPayload.devices.map((value) => ({ label: value, value })),
    ], '__ALL__');
    renderSelectOptions(els.variableFilter, [
      { label: '全部参量', value: '__ALL__' },
      ...hierarchyPayload.variables.map((value) => ({ label: value, value })),
    ], '__ALL__');
  } else if (key === 'device') {
    renderSelectOptions(els.variableFilter, [
      { label: '全部参量', value: '__ALL__' },
      ...hierarchyPayload.variables.map((value) => ({ label: value, value })),
    ], '__ALL__');
  }
}

// 所有多选层级都由后端返回的实际 table.hierarchy 关联计算。
const primaryTableSelect = document.getElementById('primaryTableSelect');
const selectedTablesCount = document.getElementById('selectedTablesCount');
const resetBrowserFiltersBtn = document.getElementById('resetBrowserFiltersBtn');
function updateTableSelectionBar() {
  if (selectedTablesCount) selectedTablesCount.textContent = `已选 ${state.selectedTables.length} / 8 张`;
  if (!primaryTableSelect) return;
  primaryTableSelect.replaceChildren();
  for (const name of state.selectedTables) {
    const option = new Option(name, name);
    primaryTableSelect.append(option);
  }
  primaryTableSelect.disabled = !state.selectedTables.length;
  if (state.selectedTable && state.selectedTables.includes(state.selectedTable)) {
    primaryTableSelect.value = state.selectedTable;
  }
}
primaryTableSelect?.addEventListener('change', () => {
  const primary = primaryTableSelect.value;
  if (!state.selectedTables.includes(primary)) return;
  // Put the explicitly selected primary first. Other curve identities stay stable.
  state.selectedTables = [primary, ...state.selectedTables.filter(name => name !== primary)];
  activateMultiTableSelection();
});
resetBrowserFiltersBtn?.addEventListener('click', async () => {
  for (const key of ['model','subsystem','device','variable']) state.multiFilters[key] = [];
  state.selectedTables = [];
  state.selectedTable = null;
  state.tableSearch = '';
  refreshMultipleBrowserFilters();
  refreshMultipleBrowserTables();
  await activateMultiTableSelection();
});
const TABLE_LAYER_KEYS = ['model','subsystem','device','variable'];
function tableMatches(table, through = 'variable') {
  const keys = TABLE_LAYER_KEYS.slice(0, TABLE_LAYER_KEYS.indexOf(through) + 1);
  return (table.hierarchy || []).some(row => keys.every(key =>
    !state.multiFilters[key].length || state.multiFilters[key].includes(row[key])));
}
function drawMultiPicker(name, candidates, chosen, onChange) {
  const root = document.getElementById(`multi_${name}`);
  if (!root) return;
  const options = root.querySelector('.multi-picker-options');
  options.replaceChildren();
  if (name === 'tableSelect') {
    const tools = document.createElement('div'); tools.className = 'picker-tools';
    const search = document.createElement('input'); search.type = 'search';
    search.placeholder = '搜索数据表…'; search.value = state.tableSearch;
    search.setAttribute('aria-label', '搜索数据表');
    const shown = document.createElement('span'); shown.className = 'picker-count';
    const selectVisible = document.createElement('button'); selectVisible.type = 'button';
    selectVisible.className = 'mini-action'; selectVisible.textContent = '选中可见';
    const clear = document.createElement('button'); clear.type = 'button';
    clear.className = 'mini-action'; clear.textContent = '清空已选';
    tools.append(search, selectVisible, clear, shown); options.append(tools);
    search.addEventListener('input', () => {
      state.tableSearch = search.value;
      const lower = search.value.trim().toLocaleLowerCase();
      let shownCount = 0;
      options.querySelectorAll('label.picker-option').forEach(label => {
        const visible = label.textContent.toLocaleLowerCase().includes(lower);
        label.hidden = !visible;
        if (visible) shownCount += 1;
      });
      shown.textContent = `${shownCount} / ${candidates.length}`;
    });
    selectVisible.addEventListener('click', async () => {
      const visibleNames = [...options.querySelectorAll('label.picker-option:not([hidden]) input')]
        .map(input => input.value);
      const available = [...state.selectedTables];
      for (const value of visibleNames) {
        if (!available.includes(value) && available.length < 8) available.push(value);
      }
      state.selectedTables = available;
      await activateMultiTableSelection();
      refreshMultipleBrowserTables();
      if (visibleNames.length > 8) showTableActionMessage('最多同时展示 8 张表，已选择可见列表中的前 8 张。');
    });
    clear.addEventListener('click', async () => {
      state.selectedTables = [];
      await activateMultiTableSelection();
      refreshMultipleBrowserTables();
    });
  }
  for (const value of candidates) {
    const label = document.createElement('label');
    label.className = 'picker-option';
    if (name === 'tableSelect' && !value.toLocaleLowerCase().includes(state.tableSearch.trim().toLocaleLowerCase())) label.hidden = true;
    const check = document.createElement('input');
    check.type = 'checkbox'; check.value = value; check.checked = chosen.includes(value);
    check.addEventListener('change', () => onChange(value, check.checked));
    label.append(check, document.createTextNode(value));
    options.append(label);
  }
  if (!candidates.length) options.textContent = '无可选项';
  if (name === 'tableSelect') { const count = options.querySelector('.picker-count'); if (count) count.textContent = `${options.querySelectorAll('label.picker-option:not([hidden])').length} / ${candidates.length}`; }
  const summary = root.querySelector('summary');
  summary.textContent = chosen.length ? `已选 ${chosen.length} 项：${chosen.slice(0,2).join('、')}${chosen.length > 2 ? '…' : ''}` : '全部（点击多选）';
}
function refreshMultipleBrowserFilters() {
  for (const [idx, key] of TABLE_LAYER_KEYS.entries()) {
    const upstream = TABLE_LAYER_KEYS.slice(0, idx);
    const options = [...new Set(state.tables.flatMap(table => (table.hierarchy || [])
      .filter(row => upstream.every(field => !state.multiFilters[field].length || state.multiFilters[field].includes(row[field])))
      .map(row => row[key])).filter(Boolean))].sort();
    state.multiFilters[key] = state.multiFilters[key].filter(item => options.includes(item));
    drawMultiPicker(key+'Filter', options, state.multiFilters[key], (value, checked) => {
      state.multiFilters[key] = checked
        ? [...state.multiFilters[key], value] : state.multiFilters[key].filter(item => item !== value);
      // Keep valid selections on other levels; refresh removes only no-longer-valid items.
      refreshMultipleBrowserFilters();
      refreshMultipleBrowserTables();
    });
    const root = document.getElementById(`multi_${key}Filter`);
    if (root && root.parentElement) {
      // Single available hierarchy values are information, not a decision.
      root.parentElement.style.display = options.length === 1 ? 'none' : '';
    }
  }
  const fixed = document.getElementById('fixedHierarchyPath');
  if (fixed) {
    const labels = {model:'型号', subsystem:'分系统', device:'部件', variable:'参量'};
    const fixedValues = TABLE_LAYER_KEYS.map(key => {
      const options = [...new Set(state.tables.flatMap(table =>
        (table.hierarchy || []).map(item => item[key]).filter(Boolean)))];
      return options.length === 1 ? `${labels[key]}：${options[0]}` : null;
    }).filter(Boolean);
    fixed.textContent = fixedValues.length ? `固定归属 · ${fixedValues.join(' / ')}` : '';
    fixed.hidden = !fixedValues.length;
  }
  // Hide redundant empty grid rows after fixed hierarchy fields collapse.
  for (const group of document.querySelectorAll('.browser-page aside .subgrid')) {
    if (group.querySelector('[id^="multi_"]')) {
      group.style.display = [...group.children].every(child => child.style.display === 'none') ? 'none' : '';
    }
  }
}
function refreshMultipleBrowserTables() {
  const visible = state.tables.filter(table => tableMatches(table));
  state.selectedTables = state.selectedTables.filter(name => visible.some(item => item.name === name));
  drawMultiPicker('tableSelect', visible.map(item => item.name), state.selectedTables,
    async (name, checked) => {
      if (checked && state.selectedTables.length >= 8) {
        const input = document.querySelector(`#multi_tableSelect input[value="${CSS.escape(name)}"]`);
        if (input) input.checked = false;
        window.alert('单次最多显示 8 条曲线，请先取消其他数据表。');
        return;
      }
      state.selectedTables = checked ? [...state.selectedTables, name]
        : state.selectedTables.filter(item => item !== name);
      await activateMultiTableSelection();
    });
  updateTableSelectionBar();
  if (!state.selectedTables.length) {
    state.selectedTable = null;
    state.rows = []; state.filteredRows = []; state.multiSeries = [];
    renderChart(); renderTable(); renderStats();
  }
}
async function activateMultiTableSelection() {
  const primary = state.selectedTables[0] || null;
  state.selectedTable = primary;
  updateTableSelectionBar();
  if (primary) els.tableSelect.value = primary;
  const summary = document.querySelector('#multi_tableSelect summary');
  if (summary) summary.textContent = state.selectedTables.length
    ? `已选 ${state.selectedTables.length} 张：${state.selectedTables.slice(0,2).join('、')}`
    : '未选择数据表（点击多选）';
  state.multiSeries = [];
  syncDeleteTableButton(); updatePreprocessingModuleVisibility();
  await loadTableRows();
}
async function loadMultiTableSeries() {
  const id = ++state.multiLoadId;
  if (!state.selectedDb || state.selectedTables.length <= 1) {
    state.multiSeries = []; return;
  }
  const selected = [...state.selectedTables];
  const loaded = await Promise.all(selected.map(async table => {
    let physical = table;
    if (state.dataView === 'clean') {
      const status = await api(`/api/preprocessing/status?db=${encodeURIComponent(state.selectedDb.value)}&table=${encodeURIComponent(table)}`);
      if (status.status !== 'valid') throw new Error(`${table} 没有有效的清洁数据`);
      physical = status.clean_table || `${table}_clean`;
    }
    const params = new URLSearchParams({db:state.selectedDb.value,table:physical,
      limit:String(state.timeRange.active ? TIME_RANGE_ROW_LIMIT : DEFAULT_ROW_LIMIT)});
    if (state.timeRange.active) {
      params.set('time_col', 'timestamp');
      params.set('start_time', state.timeRange.start);
      params.set('end_time', state.timeRange.end);
    }
    const payload = await api(`/api/sqlite-rows?${params}`);
    return {table, rows:payload.rows || [], truncated:Boolean(payload.truncated), count:payload.matched_count};
  }));
  if (id === state.multiLoadId) {
    state.multiSeries = loaded;
    if (!selected.includes(state.referenceTable)) state.referenceTable = selected[0];
  }
}
function browserTimestamp(value) {
  if (value === null || value === undefined || value === '') return NaN;
  const point = window.AASFaultChart.parseTime(value);
  return point ? point.seconds : NaN;
}
function renderMultiTableChart() {
  if (!state.multiSeries.length) {
    state.annotationAxis = null;
    els.chart.innerHTML = '<div class="empty-state">正在加载多表曲线…</div>'; return;
  }
  const {width,height,padding} = CHART_GEOMETRY;
  const usableW = width-padding.left-padding.right, usableH=height-padding.top-padding.bottom;
  const palettes = ['#457cf6','#19a38f','#d38c28','#d4557a','#825bcc','#238eb1','#bc6732','#688a42'];
  const sets = state.multiSeries.map((data,index) => {
    const points = data.rows.map(row => ({time:browserTimestamp(row.timestamp),value:Number(row.value),rawTimestamp:row.timestamp}))
      .filter(p => Number.isFinite(p.time) && Number.isFinite(p.value));
    points.sort((a,b)=>a.time-b.time);
    const values=points.map(p=>p.value);
    return {...data,index,points,min:Math.min(...values),max:Math.max(...values)};
  });
  const valid = sets.filter(d => d.points.length > 0);
  if (!valid.length) {state.annotationAxis=null;els.chart.innerHTML='<div class="empty-state">选中表没有有效的 timestamp/value 数据。</div>';return;}
  const minT=Math.min(...valid.map(d=>d.points[0].time));
  const maxT=Math.max(...valid.map(d=>d.points[d.points.length-1].time));
  const primary = valid.find(d=>d.table===state.selectedTable) || valid[0];
  const primaryAxis = window.AASFaultChart.axisFor(primary.points.map(p => p.rawTimestamp));
  // Multiple sensors can have different sampling grids, but a shared timestamp kind
  // is required to place the primary table's annotated events on the chart.
  const eventAxis = primaryAxis ? {...primaryAxis, min:minT, max:maxT} : null;
  state.annotationAxis = eventAxis;
  const annotation = intervalBandMarkup(eventAxis, CHART_GEOMETRY);
  const ref=valid.find(d=>d.table===state.referenceTable) || valid[0];
  const {scale}=state.chartZoom;
  const off=clampChartOffset(state.chartZoom.offsetX,scale);state.chartZoom.offsetX=off;
  const x=t=>padding.left+(((t-minT)/(maxT-minT||1)-off)*scale*usableW);
  const y=(value,d)=>height-padding.bottom-(d.max===d.min?0.5:(value-d.min)/(d.max-d.min))*usableH;
  const path=valid.map(d=>{
    const pts=d.points.map(point=>`${x(point.time)},${y(point.value,d)}`).join(' ');
    return `<polyline fill="none" stroke="${palettes[d.index%palettes.length]}" stroke-width="2.2" points="${pts}" vector-effect="non-scaling-stroke" />`;
  }).join('');
  const ticks=Array.from({length:5},(_,i)=>{
    const frac=i/4, yy=height-padding.bottom-frac*usableH;
    return `<line x1="${padding.left}" x2="${width-padding.right}" y1="${yy}" y2="${yy}" stroke="var(--line)" />
    <text x="12" y="${yy+4}" fill="var(--muted)" font-size="11">${fmt(ref.min+frac*(ref.max-ref.min))}</text>`;
  }).join('');
  const choices=valid.map(d=>`<option value="${escapeHtml(d.table)}" ${d.table===ref.table?'selected':''}>${escapeHtml(d.table)}</option>`).join('');
  const legend=valid.map(d=>`<span class="multi-series-legend-item"><i style="background:${palettes[d.index%palettes.length]}"></i><strong>${escapeHtml(d.table)}${d.table===state.selectedTable?'（主表）':''}</strong><small>${fmt(d.min)} ～ ${fmt(d.max)}${d.truncated?' · 已截断':''}</small></span>`).join('');
  const isDates = Number.isNaN(Number(state.multiSeries[0]?.rows[0]?.timestamp));
  const fmtTime=t=>isDates?window.AASFaultChart.toTimestamp(t, eventAxis?.kind || 'utc'):String(t);
  const t1=minT+off*(maxT-minT),t2=minT+Math.min(1,off+1/scale)*(maxT-minT);
  els.chart.innerHTML=`<div class="chart-toolbar">
    <span class="chart-interaction-hint">滚轮：以光标为中心缩放 · 拖拽左右平移 · 框选标注</span>${annotationToolbar()}
    <label class="chart-reference">Y轴参考表 <select id="referenceTableSelect">${choices}</select></label>
    <button class="chart-btn" onclick="zoomIn()">放大</button><button class="chart-btn" onclick="zoomOut()">缩小</button>
    <button class="chart-btn" onclick="resetZoom()">重置</button><span class="zoom-level">${Math.round(scale*100)}%</span></div>
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="按时间戳对齐横轴的多表独立尺度曲线">
      <defs><clipPath id="browserChartPlotClip"><rect x="${padding.left}" y="${padding.top}" width="${usableW}" height="${usableH}" /></clipPath></defs>
      ${ticks}<g clip-path="url(#browserChartPlotClip)">${annotation.underlay}${path}${annotation.targets}<rect id="chartAnnotationPreview" class="event-selection-preview" x="0" y="${padding.top}" width="0" height="${usableH}" visibility="hidden" /></g>
      <text x="${padding.left}" y="${height-15}" fill="var(--muted)" font-size="11">${escapeHtml(fmtTime(t1))}</text>
      <text x="${width-padding.right}" y="${height-15}" text-anchor="end" fill="var(--muted)" font-size="11">${escapeHtml(fmtTime(t2))}</text>
    </svg><div class="multi-series-legend">${legend}</div>${annotationLegend()}
    <div class="chart-scale-hint">曲线各自归一化到绘图区以便比较波形；Y轴数字显示所选参考表的原始量纲，切换参考表不改变任何曲线位置。横轴采用真实时间戳，不按行号匹配。${valid.some(d=>d.truncated)?'部分表仅显示抽样范围内的记录，请缩小时间窗口。':''}</div>`;
  els.chart.querySelector('#referenceTableSelect').addEventListener('change',event=>{
    state.referenceTable=event.target.value;renderMultiTableChart();
  });
  bindChartEvents();
}

els.dbSelect.addEventListener('change', () => { showTableActionMessage(''); loadDbTables(); });
els.tableSelect.addEventListener('change', () => {
  state.selectedTables = els.tableSelect.value ? [els.tableSelect.value] : [];
  activateMultiTableSelection();
});

// 数据预处理事件
if (els.dataViewRadios) {
  els.dataViewRadios.forEach((radio) => {
    radio.addEventListener('change', (event) => {
      state.dataView = event.target.value;
      if (state.selectedTable) {
        loadTableRows();
      }
    });
  });
}

if (els.runPreprocessingBtn) {
  els.runPreprocessingBtn.addEventListener('click', runPreprocessing);
}

if (els.deleteCleanDataBtn) {
  els.deleteCleanDataBtn.addEventListener('click', deleteCleanData);
}

els.timeColSelect.addEventListener('change', async (event) => {
  state.selectedTimeCol = event.target.value;
  resetChartZoomState();
  resetTimeRangeState();
  await loadTimeRangeMetadata({ resetSelection: true });
  renderChart();
  renderTimeRangeControls();
});
if (els.applyTimeRangeBtn) {
  els.applyTimeRangeBtn.addEventListener('click', applyTimeRange);
}
if (els.clearTimeRangeBtn) {
  els.clearTimeRangeBtn.addEventListener('click', clearTimeRange);
}
[els.timeRangeStart, els.timeRangeEnd].filter(Boolean).forEach((input) => {
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      applyTimeRange();
    }
  });
});
els.labelColSelect.addEventListener('change', (event) => {
  state.selectedLabelCol = event.target.value;
});

async function init() {
  try {
    await loadDatabases();
  } catch (error) {
    els.statsGrid.innerHTML = `<div class="empty-state">初始化失败：${error.message}</div>`;
  }
}

/**
 * 更新预处理模块可见性
 */
function updatePreprocessingModuleVisibility() {
  if (!els.preprocessingModule) return;
  els.preprocessingModule.style.display = state.selectedTable ? 'block' : 'none';
  if (state.selectedTable) {
    checkCleanTableStatus();
  }
}

/**
 * 检查清洁表状态
 */
async function checkCleanTableStatus() {
  if (!state.selectedDb?.value || !state.selectedTable) return;

  try {
    const payload = await api(
      `/api/preprocessing/status?db=${encodeURIComponent(state.selectedDb.value)}&table=${encodeURIComponent(state.selectedTable)}`
    );

    // 将后端返回的状态转换为前端期望的格式
    const backendStatus = payload.status; // "valid", "not_exist", "invalidated", "error"
    let statusObj;

    if (backendStatus === 'valid') {
      statusObj = {
        exists: true,
        valid: true,
        row_count: payload.row_count || 0,
        timestamp: payload.timestamp || '',
      };
    } else if (backendStatus === 'invalidated') {
      statusObj = {
        exists: true,
        valid: false,
        row_count: payload.row_count || 0,
        timestamp: payload.timestamp || '',
      };
    } else {
      // not_exist 或 error
      statusObj = {
        exists: false,
        valid: false,
      };
    }

    state.preprocessingStatus = statusObj;
    renderPreprocessingStatus(statusObj);
  } catch (error) {
    console.error('检查清洁表状态失败:', error);
  }
}

/**
 * 渲染预处理状态指示器
 */
function renderPreprocessingStatus(status) {
  const statusEl = els.preprocessingStatus;
  if (!statusEl) return;

  const iconEl = statusEl.querySelector('.status-icon');
  const textEl = statusEl.querySelector('.status-text');
  const detailEl = statusEl.querySelector('.status-detail');
  const deleteBtn = els.deleteCleanDataBtn;
  const runBtn = els.runPreprocessingBtn;

  statusEl.classList.remove('status-none', 'status-clean', 'status-stale');

  if (!status || !status.exists) {
    statusEl.classList.add('status-none');
    if (iconEl) iconEl.style.background = '#9e9e9e';
    if (textEl) textEl.textContent = '未处理';
    if (detailEl) detailEl.textContent = '尚未执行预处理';
    if (deleteBtn) deleteBtn.style.display = 'none';
    if (runBtn) runBtn.style.display = 'block';
  } else if (status.valid) {
    statusEl.classList.add('status-clean');
    if (iconEl) iconEl.style.background = '#4caf50';
    if (textEl) textEl.textContent = '已清洁';
    if (detailEl) detailEl.textContent = `${status.row_count || 0} 条记录 | ${status.timestamp || ''}`;
    if (deleteBtn) deleteBtn.style.display = 'inline-block';
    if (runBtn) runBtn.style.display = 'none';
  } else {
    statusEl.classList.add('status-stale');
    if (iconEl) iconEl.style.background = '#ff9800';
    if (textEl) textEl.textContent = '已过期';
    if (detailEl) detailEl.textContent = '原始数据已变更，建议重新处理';
    if (deleteBtn) deleteBtn.style.display = 'inline-block';
    if (runBtn) runBtn.style.display = 'block';
  }
}

/**
 * 执行预处理
 */
async function runPreprocessing() {
  if (!state.selectedDb?.value || !state.selectedTable) return;

  const config = {
    smoothWindowSize: parseInt(els.smoothWindowSize.value, 10) || 3,
    outlierThreshold: parseFloat(els.outlierThreshold.value) || 3.0,
    normalizationMethod: els.normalizationMethod.value || 'none',
  };

  if (els.runPreprocessingBtn) {
    els.runPreprocessingBtn.disabled = true;
    els.runPreprocessingBtn.textContent = '处理中...';
  }

  try {
    const payload = await api('/api/preprocessing/run', {
      method: 'POST',
      body: JSON.stringify({
        db: state.selectedDb.value,
        table: state.selectedTable,
        config,
      }),
    });

    if (payload.status === 'success') {
      // 重新检查状态并更新 UI
      await checkCleanTableStatus();
      // 如果当前选择清洁视图，重新加载数据
      if (state.dataView === 'clean') {
        loadTableRows();
      }
    }
  } catch (error) {
    console.error('执行预处理失败:', error);
    alert(`预处理失败：${error.message}`);
  } finally {
    if (els.runPreprocessingBtn) {
      els.runPreprocessingBtn.disabled = false;
      els.runPreprocessingBtn.textContent = '执行预处理';
    }
  }
}

/**
 * 删除清洁表
 */
async function deleteCleanData() {
  if (!state.selectedDb?.value || !state.selectedTable) return;
  if (!confirm(`确定要删除 ${state.selectedTable}_clean 吗？`)) return;

  try {
    const payload = await api('/api/preprocessing/delete', {
      method: 'POST',
      body: JSON.stringify({
        db: state.selectedDb.value,
        table: state.selectedTable,
      }),
    });

    if (payload.status === 'success') {
      state.preprocessingStatus = null;
      renderPreprocessingStatus(null);
      // 如果当前选择清洁视图，切换到原始数据
      if (state.dataView === 'clean') {
        state.dataView = 'raw';
        const rawRadio = document.querySelector('input[name="dataView"][value="raw"]');
        if (rawRadio) rawRadio.checked = true;
        loadTableRows();
      }
    }
  } catch (error) {
    console.error('删除清洁表失败:', error);
    alert(`删除失败：${error.message}`);
  }
}

// 导入数据功能：单表导入与批量导入明确分流。
// - 单表导入：单文件、单目标表；自动识别后允许手工修正字段映射。
// - 批量导入：多文件/宽表自动识别；不提供单表字段覆盖，规范检查失败时直接阻止导入。
const importEls = {
  importBtn: document.getElementById('importBtn'),
  importModal: document.getElementById('importModal'),
  closeModalBtn: document.getElementById('closeModalBtn'),
  modeRadios: Array.from(document.querySelectorAll('input[name="importMode"]')),
  singleModeTab: document.getElementById('singleModeTab'),
  batchModeTab: document.getElementById('batchModeTab'),
  fileSelectHelp: document.getElementById('fileSelectHelp'),
  fileInput: document.getElementById('fileInput'),
  fileInfo: document.getElementById('fileInfo'),
  targetDbSelect: document.getElementById('targetDbSelect'),
  dataRoleSelect: document.getElementById('dataRoleSelect'),
  fillMissingInput: document.getElementById('fillMissingInput'),
  defaultModel: document.getElementById('defaultModel'),
  expandTelemetry: document.getElementById('expandTelemetry'),
  singleImportOptions: document.getElementById('singleImportOptions'),
  batchImportOptions: document.getElementById('batchImportOptions'),
  tablePrefix: document.getElementById('tablePrefix'),
  tableName: document.getElementById('tableName'),
  mappingSectionTitle: document.getElementById('mappingSectionTitle'),
  mappingSectionHelp: document.getElementById('mappingSectionHelp'),
  autoMapSummary: document.getElementById('autoMapSummary'),
  batchValidation: document.getElementById('batchValidation'),
  batchPlan: document.getElementById('batchPlan'),
  fieldMapping: document.getElementById('fieldMapping'),
  dataPreview: document.getElementById('dataPreview'),
  previewBtn: document.getElementById('previewBtn'),
  importConfirmBtn: document.getElementById('importConfirmBtn'),
  importProgress: document.getElementById('importProgress'),
  importMessage: document.getElementById('importMessage'),
};

let importMode = 'single';
let selectedFiles = [];
let importPreviews = [];
let csvColumns = [];
const disabledBatchJobs = new Set();

const dbFields = [
  { name: 'timestamp', label: '时间戳', required: true },
  { name: 'model', label: '型号', required: true },
  { name: 'subsystem', label: '分系统', required: true },
  { name: 'device', label: '单机', required: true },
  { name: 'variable', label: '参量', required: true },
  { name: 'value', label: '值', required: true },
  { name: 'label', label: '标签', required: false },
];

function setImportMessage(message, type = '') {
  if (!importEls.importMessage) return;
  importEls.importMessage.textContent = message || '';
  importEls.importMessage.className = `import-message${type ? ` ${type}` : ''}`;
}

function sanitizeTableToken(value, fallback = 'data') {
  const text = String(value ?? '').trim()
    .replace(/[^A-Za-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_');
  return text || fallback;
}

function roleLabel(role) {
  return ({ train: '训练集', test: '测试集', unspecified: '未指定', auto: '自动识别' })[role] || '未指定';
}

function effectivePreviewRole(data) {
  const selected = importEls.dataRoleSelect?.value || 'auto';
  return selected === 'auto' ? (data?.suggested_role || 'unspecified') : selected;
}

function roleCompositionText(data) {
  const counts = data?.label_summary || {};
  return `正常 ${counts.normal ?? 0} / 故障 ${counts.fault ?? 0}${counts.unknown ? ` / 未识别 ${counts.unknown}` : ''}`;
}

function roleIsCompatible(role, data) {
  const counts = data?.label_summary || {};
  if (role === 'train') return (counts.normal || 0) > 0 && (counts.fault || 0) === 0 && (counts.unknown || 0) === 0;
  if (role === 'test') return (counts.normal || 0) > 0 && (counts.fault || 0) > 0 && (counts.unknown || 0) === 0;
  return true;
}

function mappingClone(mapping) {
  return JSON.parse(JSON.stringify(mapping || {}));
}

function withDefaultModel(mapping, telemetry = null) {
  const next = mappingClone(mapping);
  if (telemetry?.model) {
    next.model = { type: 'manual', value: telemetry.model };
  } else if ((!next.model || (next.model.type === 'manual' && !next.model.value)) && importEls.defaultModel?.value.trim()) {
    next.model = { type: 'manual', value: importEls.defaultModel.value.trim() };
  }
  return next;
}

function missingRequiredMappings(mapping) {
  return dbFields
    .filter((field) => field.required)
    .filter((field) => {
      const item = mapping?.[field.name];
      return !item || item.value === undefined || item.value === null || String(item.value).trim() === '';
    })
    .map((field) => field.label);
}

function autoTableName(data, mapping, telemetry, fileIndex, jobIndex) {
  const modelMapping = mapping?.model;
  const model = modelMapping?.type === 'manual' ? modelMapping.value : '';
  const baseModel = sanitizeTableToken(model || importEls.defaultModel?.value || importEls.tablePrefix?.value || 'DF', 'DF');
  const subsystem = telemetry?.subsystem || (mapping?.subsystem?.type === 'manual' ? mapping.subsystem.value : 'subsystem');
  const device = telemetry?.device || (mapping?.device?.type === 'manual' ? mapping.device.value : 'device');
  const variable = telemetry?.variable || (mapping?.variable?.type === 'manual' ? mapping.variable.value : `param_${jobIndex + 1}`);
  const tag = sanitizeTableToken(data?.dataset_tag || `data_${fileIndex + 1}`, `data_${fileIndex + 1}`);
  return [baseModel, subsystem, device, variable, tag].map((part) => sanitizeTableToken(part)).join('_');
}

function currentManualMapping() {
  const fieldMappings = {};
  for (const field of dbFields) {
    const selectEl = document.getElementById(`field_${field.name}`);
    const manualEl = document.getElementById(`field_${field.name}_manual`);
    if (selectEl?.value) {
      fieldMappings[field.name] = { type: 'column', value: selectEl.value };
    } else if (manualEl?.value.trim()) {
      fieldMappings[field.name] = { type: 'manual', value: manualEl.value.trim() };
    }
  }
  return fieldMappings;
}

function singleMappingFor(data) {
  const fromForm = currentManualMapping();
  const mapping = Object.keys(fromForm).length ? fromForm : mappingClone(data?.suggested_mappings || {});
  return withDefaultModel(mapping);
}

function buildImportJobs(item, fileIndex) {
  const data = item.data;
  const role = effectivePreviewRole(data);
  const jobs = [];

  if (importMode === 'batch') {
    const telemetry = data.telemetry_columns || [];
    if (telemetry.length) {
      telemetry.forEach((tele, jobIndex) => {
        let mapping = withDefaultModel(data.suggested_mappings || {}, tele);
        mapping.subsystem = { type: 'manual', value: tele.subsystem };
        mapping.device = { type: 'manual', value: tele.device };
        mapping.variable = { type: 'manual', value: tele.variable };
        mapping.value = { type: 'column', value: tele.column };
        const tableName = autoTableName(data, mapping, tele, fileIndex, jobIndex);
        jobs.push({
          id: `${fileIndex}:${jobIndex}`,
          table_prefix: '',
          table_name: tableName,
          data_role: role,
          field_mappings: mapping,
          telemetry: tele,
          missing: missingRequiredMappings(mapping),
        });
      });
    } else {
      const mapping = withDefaultModel(data.suggested_mappings || {});
      jobs.push({
        id: `${fileIndex}:0`,
        table_prefix: '',
        table_name: autoTableName(data, mapping, null, fileIndex, 0),
        data_role: role,
        field_mappings: mapping,
        telemetry: null,
        missing: missingRequiredMappings(mapping),
      });
    }
    return jobs;
  }

  const mapping = singleMappingFor(data);
  const manualName = importEls.tableName?.value.trim();
  const prefix = sanitizeTableToken(importEls.tablePrefix?.value || 'DF', 'DF');
  jobs.push({
    id: `${fileIndex}:0`,
    table_prefix: manualName ? prefix : '',
    table_name: manualName ? sanitizeTableToken(manualName, 'imported_data') : autoTableName(data, mapping, null, fileIndex, 0),
    data_role: role,
    field_mappings: mapping,
    telemetry: null,
    missing: missingRequiredMappings(mapping),
  });
  return jobs;
}

function batchValidationFor(item, fileIndex) {
  const jobs = buildImportJobs(item, fileIndex);
  const role = effectivePreviewRole(item.data);
  const issues = [];
  const warnings = [];

  if (!jobs.length) issues.push('未生成可导入数据表');
  for (const job of jobs) {
    if (job.missing.length) {
      issues.push(`${job.table_name} 缺少必填字段：${job.missing.join('、')}`);
    }
  }
  if (!roleIsCompatible(role, item.data) && role !== 'unspecified') {
    issues.push(`数据用途“${roleLabel(role)}”与标签组成（${roleCompositionText(item.data)}）不匹配`);
  }
  if (role === 'unspecified') {
    warnings.push('未识别为训练集或测试集；将按“未指定”用途导入');
  }
  if (!(item.data.auto_mapped_fields || []).includes('label')) {
    warnings.push('未自动识别标签列，导入后无法依据标签确认训练/测试数据组成');
  }
  return { jobs, role, issues, warnings, valid: issues.length === 0 };
}

function collectBatchValidation() {
  const files = importPreviews.map((item, fileIndex) => ({ item, fileIndex, ...batchValidationFor(item, fileIndex) }));
  return {
    files,
    valid: files.every((entry) => entry.valid),
    issueCount: files.reduce((sum, entry) => sum + entry.issues.length, 0),
    warningCount: files.reduce((sum, entry) => sum + entry.warnings.length, 0),
  };
}

function renderBatchValidation() {
  if (!importEls.batchValidation) return;
  if (importMode !== 'batch' || !importPreviews.length) {
    importEls.batchValidation.hidden = true;
    importEls.batchValidation.innerHTML = '';
    return;
  }
  const validation = collectBatchValidation();
  importEls.batchValidation.hidden = false;
  if (validation.valid) {
    importEls.batchValidation.className = `batch-validation ${validation.warningCount ? 'warning' : 'success'}`;
    importEls.batchValidation.innerHTML = validation.warningCount
      ? `<strong>自动识别通过，可批量导入。</strong> 另有 ${validation.warningCount} 项提示，请确认下方文件状态。`
      : `<strong>自动识别与规范检查通过。</strong> ${validation.files.length} 个文件均满足当前批量导入规则。`;
  } else {
    importEls.batchValidation.className = 'batch-validation error';
    importEls.batchValidation.innerHTML = `<strong>批量导入检查未通过。</strong> 共发现 ${validation.issueCount} 项阻断问题。请修正列名识别配置、补充“默认型号”，或切换到“单表导入”手工映射。`;
  }
}

function renderImportPlan() {
  if (!importPreviews.length) {
    importEls.autoMapSummary.innerHTML = '<span class="help-text">选择文件后将自动解析列名、数据用途和可拆分参量。</span>';
    importEls.batchPlan.innerHTML = '';
    renderBatchValidation();
    return;
  }

  if (importMode === 'single') {
    const data = importPreviews[0].data;
    const role = effectivePreviewRole(data);
    const mapping = singleMappingFor(data);
    const missing = missingRequiredMappings(mapping);
    importEls.autoMapSummary.innerHTML = `
      <div class="auto-map-strip">
        <span><strong>1</strong> 个文件</span>
        <span>自动匹配字段 <strong>${data.auto_mapped_fields?.length || 0}</strong> 项</span>
        <span>用途 <span class="role-badge role-${escapeHtml(role)}">${escapeHtml(roleLabel(role))}</span></span>
        <span>${escapeHtml(roleCompositionText(data))}</span>
        ${missing.length ? `<span class="batch-warning">仍需补充：${escapeHtml(missing.join('、'))}</span>` : '<span class="single-ready">字段完整，可继续检查或手工修正</span>'}
      </div>`;
    importEls.batchPlan.innerHTML = '';
    renderBatchValidation();
    return;
  }

  const totalJobs = importPreviews.reduce((sum, item, index) => sum + buildImportJobs(item, index).length, 0);
  const autoMapped = importPreviews.reduce((sum, item) => sum + (item.data.auto_mapped_fields?.length || 0), 0);
  importEls.autoMapSummary.innerHTML = `
    <div class="auto-map-strip">
      <span><strong>${importPreviews.length}</strong> 个文件</span>
      <span>预计生成 <strong>${totalJobs}</strong> 张数据表</span>
      <span>自动匹配字段 <strong>${autoMapped}</strong> 项</span>
      <span class="auto-map-actions"><button type="button" id="batchSelectAll" class="mini-action">全选</button><button type="button" id="batchClearAll" class="mini-action">清空</button></span>
    </div>`;

  importEls.batchPlan.innerHTML = importPreviews.map((item, fileIndex) => {
    const check = batchValidationFor(item, fileIndex);
    const rows = item.data.total_rows ?? 0;
    return `
      <details class="batch-file-card ${check.valid ? '' : 'has-error'}" ${importPreviews.length <= 3 || !check.valid ? 'open' : ''}>
        <summary>
          <span class="batch-file-name">${escapeHtml(item.file.name)}</span>
          <span class="role-badge role-${escapeHtml(check.role)}">${escapeHtml(roleLabel(check.role))}</span>
          <span class="batch-file-meta">${rows} 行 · ${escapeHtml(roleCompositionText(item.data))} · ${check.jobs.length} 张表</span>
          ${check.valid ? '<span class="batch-ok">自动识别通过</span>' : '<span class="batch-warning">识别不符合规范</span>'}
        </summary>
        ${check.issues.length ? `<div class="batch-issue-list">${check.issues.map((text) => `<div>⚠ ${escapeHtml(text)}</div>`).join('')}</div>` : ''}
        ${check.warnings.length ? `<div class="batch-hint-list">${check.warnings.map((text) => `<div>提示：${escapeHtml(text)}</div>`).join('')}</div>` : ''}
        <div class="batch-job-list">
          ${check.jobs.map((job) => {
            const disabled = disabledBatchJobs.has(job.id);
            const issue = job.missing.length ? `缺少：${job.missing.join('、')}` : '';
            return `
              <label class="batch-job-row ${issue ? 'has-issue' : ''}">
                <input type="checkbox" class="batch-job-check" data-job-id="${job.id}" ${disabled ? '' : 'checked'} ${issue ? 'disabled' : ''} />
                <span class="batch-job-table">${escapeHtml(job.table_name)}</span>
                <span class="batch-job-source">${escapeHtml(job.telemetry?.column || job.field_mappings?.value?.value || '自动单表映射')}</span>
                <span class="batch-job-status">${escapeHtml(issue || '可导入')}</span>
              </label>`;
          }).join('')}
        </div>
      </details>`;
  }).join('');

  importEls.batchPlan.querySelectorAll('.batch-job-check').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      const id = checkbox.dataset.jobId;
      if (!checkbox.checked) disabledBatchJobs.add(id);
      else disabledBatchJobs.delete(id);
    });
  });
  document.getElementById('batchSelectAll')?.addEventListener('click', () => {
    disabledBatchJobs.clear();
    renderImportPlan();
  });
  document.getElementById('batchClearAll')?.addEventListener('click', () => {
    importPreviews.forEach((item, fileIndex) => {
      buildImportJobs(item, fileIndex).forEach((job) => disabledBatchJobs.add(job.id));
    });
    renderImportPlan();
  });
  renderBatchValidation();
}

function renderFieldMapping(data = null) {
  if (!importEls.fieldMapping) return;
  if (importMode === 'batch') {
    importEls.fieldMapping.innerHTML = '';
    importEls.fieldMapping.hidden = true;
    return;
  }
  importEls.fieldMapping.hidden = false;
  if (!data?.columns?.length) {
    importEls.fieldMapping.innerHTML = '<p class="help-text">请先选择并解析数据文件。</p>';
    return;
  }

  csvColumns = data.columns;
  const suggested = data.suggested_mappings || {};
  const mappingHtml = dbFields.map((field) => {
    const auto = suggested[field.name] || {};
    const selectedColumn = auto.type === 'column' ? String(auto.value || '') : '';
    let manualValue = auto.type === 'manual' ? String(auto.value || '') : '';
    if (field.name === 'model' && !manualValue && importEls.defaultModel?.value.trim()) {
      manualValue = importEls.defaultModel.value.trim();
    }
    return `
      <div class="mapping-row">
        <label class="mapping-label">
          <span class="field-name">${escapeHtml(field.label)}</span>
          ${field.required ? '<span class="required">*</span>' : ''}
          ${auto.value ? '<span class="auto-tag">自动</span>' : ''}
        </label>
        <div class="mapping-inputs">
          <select id="field_${field.name}" data-field="${field.name}" class="mapping-select">
            <option value="">-- 手动输入 --</option>
            ${csvColumns.map((col) => `<option value="${escapeHtml(col)}" ${col === selectedColumn ? 'selected' : ''}>${escapeHtml(col)}</option>`).join('')}
          </select>
          <input type="text" id="field_${field.name}_manual" class="mapping-input"
                 value="${escapeHtml(manualValue)}"
                 placeholder="固定值（该列所有行使用此值）" />
        </div>
      </div>`;
  }).join('');
  importEls.fieldMapping.innerHTML = `<div class="field-mapping-container">${mappingHtml}</div>`;

  importEls.fieldMapping.querySelectorAll('select, input').forEach((control) => {
    control.addEventListener('change', renderImportPlan);
    if (control.tagName === 'INPUT') control.addEventListener('input', renderImportPlan);
  });
}

function renderDataPreview(data) {
  if (!data?.rows?.length) {
    importEls.dataPreview.innerHTML = '<p>无数据可预览。</p>';
    return;
  }
  const columns = data.columns || Object.keys(data.rows[0]);
  importEls.dataPreview.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr>${columns.map((col) => `<th>${escapeHtml(col)}</th>`).join('')}</tr></thead>
        <tbody>
          ${data.rows.slice(0, 10).map((row) => `<tr>${columns.map((col) => `<td>${escapeHtml(row[col] ?? '')}</td>`).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>
    <p class="help-text">显示前 ${Math.min(10, data.rows.length)} 行，共 ${data.total_rows ?? data.rows.length} 行</p>`;
}

function resetImportSelection(message = '') {
  selectedFiles = [];
  importPreviews = [];
  csvColumns = [];
  disabledBatchJobs.clear();
  if (importEls.fileInput) importEls.fileInput.value = '';
  if (importEls.fileInfo) importEls.fileInfo.textContent = '';
  renderFieldMapping(null);
  renderImportPlan();
  renderDataPreview(null);
  if (message) setImportMessage(message, 'error');
}

function applyImportMode(mode, { keepSelection = true } = {}) {
  importMode = mode === 'batch' ? 'batch' : 'single';
  importEls.modeRadios.forEach((radio) => { radio.checked = radio.value === importMode; });
  importEls.singleModeTab?.classList.toggle('is-active', importMode === 'single');
  importEls.batchModeTab?.classList.toggle('is-active', importMode === 'batch');
  if (importEls.fileInput) importEls.fileInput.multiple = importMode === 'batch';
  if (importEls.singleImportOptions) importEls.singleImportOptions.hidden = importMode !== 'single';
  if (importEls.batchImportOptions) importEls.batchImportOptions.hidden = importMode !== 'batch';
  if (importEls.expandTelemetry) importEls.expandTelemetry.checked = importMode === 'batch';
  if (importEls.mappingSectionTitle) {
    importEls.mappingSectionTitle.textContent = importMode === 'batch' ? '4. 自动识别与规范检查' : '4. 自动识别与字段映射';
  }
  if (importEls.mappingSectionHelp) {
    importEls.mappingSectionHelp.textContent = importMode === 'batch'
      ? '批量模式完全使用自动识别结果：系统逐文件检查必填字段、训练/测试标签组成和多参量拆分规则；不符合规范时会直接警告并阻止导入。'
      : '系统会先自动识别常见中英文列名和标签；单表模式下可在下方手工修正任何字段映射。';
  }
  if (importEls.fileSelectHelp) {
    importEls.fileSelectHelp.textContent = importMode === 'batch'
      ? '批量导入可一次选择多个 CSV/JSON 文件；宽表中的多参量列会自动拆分为独立数据表。'
      : '单表导入一次选择 1 个 CSV 或 JSON 文件，最终只生成 1 张数据表。';
  }
  if (importEls.importConfirmBtn) importEls.importConfirmBtn.textContent = importMode === 'batch' ? '开始批量导入' : '开始单表导入';

  if (!keepSelection || (importMode === 'single' && selectedFiles.length > 1)) {
    resetImportSelection(importMode === 'single' ? '已切换到单表导入。单表模式一次只能选择 1 个文件，请重新选择。' : '');
    return;
  }
  if (importPreviews[0]) renderFieldMapping(importPreviews[0].data);
  renderImportPlan();
}

async function previewOneFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch('/api/preview-import', { method: 'POST', body: formData });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return { file, data: payload.data };
}

async function analyzeSelectedFiles() {
  if (!selectedFiles.length) {
    setImportMessage('请先选择文件。', 'error');
    return;
  }
  if (importMode === 'single' && selectedFiles.length !== 1) {
    setImportMessage('单表导入一次只能处理 1 个文件。请重新选择。', 'error');
    return;
  }
  setImportMessage(`正在解析 ${selectedFiles.length} 个文件并自动识别字段...`);
  importEls.previewBtn.disabled = true;
  importEls.importConfirmBtn.disabled = true;
  const previews = [];
  try {
    // 顺序解析可避免一次性把大量 CSV 同时复制到内存。
    for (let i = 0; i < selectedFiles.length; i += 1) {
      previews.push(await previewOneFile(selectedFiles[i]));
      setImportMessage(`正在解析 ${i + 1}/${selectedFiles.length}：${selectedFiles[i].name}`);
    }
    importPreviews = previews;
    disabledBatchJobs.clear();
    const first = importPreviews[0]?.data;
    if (first) {
      if (importMode === 'single' && !importEls.tableName.value.trim()) {
        const mapping = withDefaultModel(first.suggested_mappings || {});
        importEls.tableName.value = autoTableName(first, mapping, null, 0, 0).replace(/^DF_/, '');
      }
      renderFieldMapping(first);
      renderDataPreview(first);
    }
    renderImportPlan();
    if (importMode === 'batch') {
      const validation = collectBatchValidation();
      if (validation.valid) {
        setImportMessage(`解析完成：${selectedFiles.length} 个文件自动识别通过，请检查批量计划后导入。`, validation.warningCount ? '' : 'success');
      } else {
        setImportMessage(`解析完成，但有 ${validation.issueCount} 项不符合批量导入规范；请按红色提示修正后再导入。`, 'error');
      }
    } else {
      setImportMessage('解析完成：请检查自动识别结果，必要时手工修正字段映射。', 'success');
    }
  } catch (error) {
    importPreviews = [];
    renderImportPlan();
    setImportMessage(`解析失败：${error.message}`, 'error');
  } finally {
    importEls.previewBtn.disabled = false;
    importEls.importConfirmBtn.disabled = false;
  }
}

async function inferDefaultModelFromTargetDb() {
  if (!importEls.targetDbSelect?.value || importEls.defaultModel?.value.trim()) return;
  try {
    const hierarchy = await api(`/api/hierarchy-options?db=${encodeURIComponent(importEls.targetDbSelect.value)}`);
    if (hierarchy.models?.length === 1) {
      importEls.defaultModel.placeholder = `例如：GFSIM1；当前库已有型号 ${hierarchy.models[0]}（请确认后填写）`;
    } else {
      importEls.defaultModel.placeholder = '例如：GFSIM1；文件缺少型号列时使用';
    }
  } catch (error) {
    console.debug('无法读取目标数据库型号候选:', error);
  }
}

if (importEls.importBtn) {
  importEls.importBtn.addEventListener('click', async () => {
    importEls.importModal.style.display = 'flex';
    try {
      const payload = await api('/api/databases');
      const databases = payload.items || [];
      const currentDbValue = state.selectedDb?.value || databases[0]?.value;
      renderSelectOptions(importEls.targetDbSelect, databases.map((db) => ({ label: db.label, value: db.value })), currentDbValue);
      await inferDefaultModelFromTargetDb();
    } catch (error) {
      console.error('加载数据库列表失败:', error);
    }
    applyImportMode(importMode);
  });
}

for (const radio of importEls.modeRadios) {
  radio.addEventListener('change', () => {
    if (!radio.checked) return;
    applyImportMode(radio.value);
  });
}

if (importEls.closeModalBtn) {
  importEls.closeModalBtn.addEventListener('click', () => { importEls.importModal.style.display = 'none'; });
}
if (importEls.importModal) {
  importEls.importModal.addEventListener('click', (event) => {
    if (event.target === importEls.importModal) importEls.importModal.style.display = 'none';
  });
}

if (importEls.fileInput) {
  importEls.fileInput.addEventListener('change', async (event) => {
    selectedFiles = Array.from(event.target.files || []);
    importPreviews = [];
    csvColumns = [];
    disabledBatchJobs.clear();
    if (!selectedFiles.length) {
      importEls.fileInfo.textContent = '';
      renderFieldMapping(null);
      renderImportPlan();
      return;
    }
    if (importMode === 'single' && selectedFiles.length > 1) {
      resetImportSelection('单表导入一次只能选择 1 个文件；如需一次导入多个文件，请切换到“批量导入”。');
      return;
    }
    const totalBytes = selectedFiles.reduce((sum, file) => sum + file.size, 0);
    importEls.fileInfo.textContent = importMode === 'batch'
      ? `已选择 ${selectedFiles.length} 个文件，共 ${(totalBytes / 1024 / 1024).toFixed(2)} MB：${selectedFiles.slice(0, 3).map((file) => file.name).join('、')}${selectedFiles.length > 3 ? '…' : ''}`
      : `已选择：${selectedFiles[0].name}（${(selectedFiles[0].size / 1024 / 1024).toFixed(2)} MB）`;
    await analyzeSelectedFiles();
  });
}

if (importEls.previewBtn) importEls.previewBtn.addEventListener('click', analyzeSelectedFiles);
if (importEls.targetDbSelect) importEls.targetDbSelect.addEventListener('change', inferDefaultModelFromTargetDb);
for (const control of [importEls.dataRoleSelect, importEls.defaultModel, importEls.tablePrefix, importEls.tableName]) {
  if (!control) continue;
  control.addEventListener('change', () => renderImportPlan());
  if (control.tagName === 'INPUT') control.addEventListener('input', () => renderImportPlan());
}

async function importSingleTable(targetDb) {
  const item = importPreviews[0];
  const job = buildImportJobs(item, 0)[0];
  if (job.missing.length) {
    throw new Error(`缺少必填映射：${job.missing.join('、')}`);
  }
  if (!roleIsCompatible(job.data_role, item.data) && job.data_role !== 'unspecified') {
    throw new Error(`数据用途“${roleLabel(job.data_role)}”与标签组成 ${roleCompositionText(item.data)} 不匹配。`);
  }
  const formData = new FormData();
  formData.append('file', item.file);
  formData.append('target_db', targetDb);
  formData.append('table_prefix', job.table_prefix || '');
  formData.append('table_name', job.table_name);
  formData.append('field_mappings', JSON.stringify(job.field_mappings));
  formData.append('data_role', job.data_role);
  formData.append('fill_missing', String(importEls.fillMissingInput.checked));
  const response = await fetch('/api/import-data', { method: 'POST', body: formData });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return {
    tables_created: 1,
    imported_rows_total: payload.imported_rows || payload.requested_rows || 0,
    filled_values_total: payload.filled_values || 0,
    unfilled_values_total: payload.unfilled_values || 0,
    last_table_name: payload.table_name,
  };
}

async function importBatchTables(targetDb, progressBar, progressText) {
  const validation = collectBatchValidation();
  if (!validation.valid) {
    throw new Error(`批量导入规范检查未通过（${validation.issueCount} 项问题）。请根据红色提示修正后重试。`);
  }
  const batches = importPreviews.map((item, fileIndex) => ({
    item,
    jobs: buildImportJobs(item, fileIndex).filter((job) => !disabledBatchJobs.has(job.id)),
  }));
  if (batches.every((batch) => !batch.jobs.length)) {
    throw new Error('至少选择一个要导入的数据表。');
  }

  let completedFiles = 0;
  let tableCount = 0;
  let importedRows = 0;
  let filledValues = 0;
  let unfilledValues = 0;
  let lastTableName = null;
  const activeBatchCount = batches.filter((item) => item.jobs.length).length;
  for (const batch of batches) {
    if (!batch.jobs.length) continue;
    progressText.textContent = `正在导入 ${batch.item.file.name}...`;
    const formData = new FormData();
    formData.append('file', batch.item.file);
    formData.append('target_db', targetDb);
    formData.append('jobs', JSON.stringify(batch.jobs.map(({ id, telemetry, missing, ...job }) => job)));
    formData.append('fill_missing', String(importEls.fillMissingInput.checked));
    const response = await fetch('/api/import-data-multi', { method: 'POST', body: formData });
    const payload = await response.json();
    if (!response.ok) throw new Error(`${batch.item.file.name}: ${payload.error || `HTTP ${response.status}`}`);
    tableCount += payload.tables_created || 0;
    importedRows += payload.imported_rows_total || 0;
    filledValues += payload.filled_values_total || 0;
    unfilledValues += payload.unfilled_values_total || 0;
    lastTableName = payload.results?.[payload.results.length - 1]?.table_name || lastTableName;
    completedFiles += 1;
    const pct = Math.round((completedFiles / Math.max(1, activeBatchCount)) * 100);
    progressBar.style.width = `${pct}%`;
    progressText.textContent = `已完成 ${completedFiles} 个文件，生成 ${tableCount} 张表`;
  }
  return { completedFiles, tables_created: tableCount, imported_rows_total: importedRows, filled_values_total: filledValues, unfilled_values_total: unfilledValues, last_table_name: lastTableName };
}

if (importEls.importConfirmBtn) {
  importEls.importConfirmBtn.addEventListener('click', async () => {
    if (!selectedFiles.length) {
      setImportMessage('请先选择文件。', 'error');
      return;
    }
    if (!importPreviews.length) await analyzeSelectedFiles();
    if (!importPreviews.length) return;
    const targetDb = importEls.targetDbSelect.value;
    if (!targetDb) {
      setImportMessage('请选择目标数据库。', 'error');
      return;
    }

    importEls.importProgress.style.display = 'block';
    importEls.importConfirmBtn.disabled = true;
    const progressBar = importEls.importProgress.querySelector('.progress-bar');
    const progressText = importEls.importProgress.querySelector('.progress-text');
    progressBar.style.width = '0%';
    try {
      if (importMode === 'single') {
        progressText.textContent = `正在导入 ${selectedFiles[0].name}...`;
        const result = await importSingleTable(targetDb);
        progressBar.style.width = '100%';
        progressText.textContent = '单表导入完成！';
        setImportMessage(`导入成功：生成/写入 1 张数据表，共写入 ${result.imported_rows_total} 条记录。${importEls.fillMissingInput.checked ? `已填充 ${result.filled_values_total} 个缺失测量值，仍有 ${result.unfilled_values_total} 个未填充值（序列起始或缺少完整分组信息）。` : '未启用缺失值填充。'}`, 'success');
        setTimeout(async () => {
          importEls.importModal.style.display = 'none';
          await loadDatabases(targetDb, result.last_table_name);
        }, 1000);
      } else {
        const result = await importBatchTables(targetDb, progressBar, progressText);
        progressBar.style.width = '100%';
        progressText.textContent = '批量导入完成！';
        setImportMessage(`批量导入成功：处理 ${result.completedFiles} 个文件，生成/写入 ${result.tables_created} 张数据表，共写入 ${result.imported_rows_total} 条参数记录。${importEls.fillMissingInput.checked ? `已填充 ${result.filled_values_total} 个缺失测量值，仍有 ${result.unfilled_values_total} 个未填充值（序列起始或缺少完整分组信息）。` : '未启用缺失值填充。'}`, 'success');
        setTimeout(async () => {
          importEls.importModal.style.display = 'none';
          await loadDatabases(targetDb, result.last_table_name);
        }, 1000);
      }
    } catch (error) {
      setImportMessage(`导入失败：${error.message}`, 'error');
      progressText.textContent = '导入中止';
    } finally {
      importEls.importConfirmBtn.disabled = false;
    }
  });
}

applyImportMode('single');

// 刷新页面
async function refreshPage() {
  try {
    await loadDatabases();
  } catch (error) {
    console.error('刷新失败:', error);
  }
}

window.zoomIn = zoomIn;
window.zoomOut = zoomOut;
window.resetZoom = resetZoom;

init();

// Unified resource picker. The old filter widgets remain available as advanced controls.
if (window.AASResourcePicker) {
  document.body.classList.add('resource-picker-ready');
  const button = document.getElementById('chooseBrowserResources');
  const label = document.getElementById('browserResourceSummary');
  const reflect = () => {
    if (label) label.textContent = state.selectedTables.length
      ? `已选 ${state.selectedTables.length} 张 · 主表：${state.selectedTables[0]}`
      : '请选择用于对比的参量表';
  };
  button?.addEventListener('click', () => {
    window.AASResourcePicker.open({
      purpose:'browse',title:'选择数据表 · 曲线对比', items:state.tables,
      selected:state.selectedTables,max:8,
      onApply: async selection => {
        for (const key of ['model','subsystem','device','variable']) state.multiFilters[key] = [];
        refreshMultipleBrowserFilters();
        state.selectedTables = selection;
        state.selectedTable = selection[0] || null;
        refreshMultipleBrowserTables();
        await activateMultiTableSelection();
        reflect();
      },
    });
  });
  els.dbSelect?.addEventListener('change', () => queueMicrotask(reflect));
  document.getElementById('primaryTableSelect')?.addEventListener('change', () => queueMicrotask(reflect));
  const observer = new MutationObserver(reflect);
  if (document.getElementById('selectedTablesCount')) observer.observe(document.getElementById('selectedTablesCount'),{childList:true});
  reflect();
}

// Per-table ground truth events. Unannotated time remains UNKNOWN.
(() => {
  const byId = id => document.getElementById(id);
  const modal = byId('faultIntervalsModal');
  const status = byId('faultIntervalsStatus');
  let context = null;
  async function reload() {
    if (!context) return;
    const response = await api(`/api/fault-intervals?db=${encodeURIComponent(context.db)}&table=${encodeURIComponent(context.table)}`);
    if (context.db === state.selectedDb?.value && context.table === state.selectedTable) {
      state.intervalAnnotations = response.items || [];
      state.intervalContextKey = annotationKey();
      state.annotationError = '';
      renderChart();
    }
    const list = byId('faultIntervalsList');
    list.replaceChildren();
    for (const record of response.items || []) {
      const row = document.createElement('div');
      row.className = 'selection-workbar';
      const description = document.createElement('span');
      description.textContent = `${record.start_timestamp} ≤ t < ${record.end_timestamp} · ${record.label === 1 ? '故障' : '已确认正常'}${record.fault_type ? ' · '+record.fault_type : ''}`;
      const edit = document.createElement('button');
      edit.type = 'button'; edit.className = 'mini-action'; edit.textContent = '编辑';
      edit.addEventListener('click', () => {
        byId('faultIntervalStart').value = record.start_timestamp;
        byId('faultIntervalEnd').value = record.end_timestamp;
        byId('faultIntervalLabel').value = String(record.label);
        byId('faultIntervalType').value = record.fault_type || '';
        byId('faultIntervalNotes').value = record.notes || '';
        byId('faultIntervalType').disabled = record.label === 0;
        byId('saveFaultIntervalBtn').dataset.editId = record.id;
        byId('saveFaultIntervalBtn').textContent = '保存修改';
      });
      const remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'mini-action'; remove.textContent = '删除';
      remove.addEventListener('click', async () => {
        if (!window.confirm('确定删除该时间区间的标签吗？')) return;
        try {
          await api('/api/fault-intervals', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({db:context.db,table:context.table,action:'delete',id:record.id})});
          await reload(); status.textContent = '区间已删除';
        } catch (error) { status.textContent = `删除失败：${error.message}`; }
      });
      row.append(description, edit, remove); list.appendChild(row);
    }
    if (!response.items?.length) list.textContent = '尚无事件标注。请先明确正常与故障区间，再选择事件标签进行训练。';
  }
  async function open() {
    if (!state.selectedDb?.value || !state.selectedTable) {
      alert('请先选择数据库和一张主数据表'); return false;
    }
    context = {db:state.selectedDb.value,table:state.selectedTable};
    byId('faultIntervalsContext').textContent = `${context.db} / ${context.table}`;
    delete byId('saveFaultIntervalBtn').dataset.editId;
    byId('saveFaultIntervalBtn').textContent = '添加区间';
    status.textContent = ''; modal.style.display = 'flex';
    try { await reload(); } catch (error) { status.textContent = error.message; }
    return true;
  }
  byId('faultIntervalsBtn')?.addEventListener('click', open);
  window.AASFaultEditor = {
    open,
    async selectRange(start, end, id = 0) {
      if (!await open()) return;
      const selected = state.intervalAnnotations.find(item => Number(item.id) === Number(id));
      byId('faultIntervalStart').value = start;
      byId('faultIntervalEnd').value = end;
      byId('faultIntervalLabel').value = String(selected?.label ?? 1);
      byId('faultIntervalType').value = selected?.fault_type || '';
      byId('faultIntervalNotes').value = selected?.notes || '';
      byId('faultIntervalType').disabled = byId('faultIntervalLabel').value === '0';
      if (selected) {
        byId('saveFaultIntervalBtn').dataset.editId = String(selected.id);
        byId('saveFaultIntervalBtn').textContent = '保存修改';
      }
      if (window.AASFaultChart.parseTime(start)?.seconds >= window.AASFaultChart.parseTime(end)?.seconds) {
        status.textContent = '结束时刻须晚于开始时刻，请调整边界后保存';
      } else {
        status.textContent = '已填入图上选定时间，确认状态和边界后点击保存';
      }
      byId('faultIntervalStart').focus();
    },
    async editById(id) {
      const selected = state.intervalAnnotations.find(item => Number(item.id) === Number(id));
      if (selected) await this.selectRange(selected.start_timestamp,selected.end_timestamp,selected.id);
    },
  };
  byId('closeFaultIntervalsBtn')?.addEventListener('click', () => { modal.style.display = 'none'; context = null; });
  byId('faultIntervalLabel')?.addEventListener('change', () => {
    byId('faultIntervalType').disabled = byId('faultIntervalLabel').value === '0';
    if (byId('faultIntervalType').disabled) byId('faultIntervalType').value = '';
  });
  byId('saveFaultIntervalBtn')?.addEventListener('click', async () => {
    if (!context) return;
    try {
      await api('/api/fault-intervals', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        db:context.db,table:context.table,action:byId('saveFaultIntervalBtn').dataset.editId ? 'update' : 'create',
        id:Number(byId('saveFaultIntervalBtn').dataset.editId || 0),
        start:byId('faultIntervalStart').value,end:byId('faultIntervalEnd').value,
        label:Number(byId('faultIntervalLabel').value),fault_type:byId('faultIntervalType').value,
        notes:byId('faultIntervalNotes').value,
      })});
      status.textContent = '区间已保存';
      delete byId('saveFaultIntervalBtn').dataset.editId; byId('saveFaultIntervalBtn').textContent = '添加区间';
      await reload();
    } catch (error) { status.textContent = `保存失败：${error.message}`; }
  });
})();
