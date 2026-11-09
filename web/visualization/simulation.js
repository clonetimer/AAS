const ORIGINAL_VIEW_MODE = '__original__';

const state = {
  simStatus: null,
  tables: [],
  faultTypes: [],
  selectedTable: null,
  selectedFaultType: null,
  faultTables: [],
  previewOriginal: [],
  previewFaulty: [],
  lifecycleBusy: false,
};

const els = {
  simStatusBar: document.getElementById('simStatusBar'),
  initSimBtn: document.getElementById('initSimBtn'),
  syncSimBtn: document.getElementById('syncSimBtn'),
  deleteSimBtn: document.getElementById('deleteSimBtn'),
  simMainArea: document.getElementById('simMainArea'),
  simTableSelect: document.getElementById('simTableSelect'),
  faultTypeGrid: document.getElementById('faultTypeGrid'),
  injectScopeField: document.getElementById('injectScopeField'),
  timeRangeFields: document.getElementById('timeRangeFields'),
  startTimeInput: document.getElementById('startTimeInput'),
  endTimeInput: document.getElementById('endTimeInput'),
  severityField: document.getElementById('severityField'),
  severityPreset: document.getElementById('severityPreset'),
  severityCustom: document.getElementById('severityCustom'),
  faultFormulaHelp: document.getElementById('faultFormulaHelp'),
  faultParamFields: document.getElementById('faultParamFields'),
  previewBtn: document.getElementById('previewBtn'),
  injectBtn: document.getElementById('injectBtn'),
  comparisonChart: document.getElementById('comparisonChart'),
  chartMeta: document.getElementById('chartMeta'),
  simLog: document.getElementById('simLog'),
  faultTableMgrBtn: document.getElementById('faultTableMgrBtn'),
  faultTableModal: document.getElementById('faultTableModal'),
  closeFaultModalBtn: document.getElementById('closeFaultModalBtn'),
  faultTableList: document.getElementById('faultTableList'),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
  return payload;
}

function debounce(fn, delay) {
  let timer;
  const wrapped = function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn.apply(this, args);
    }, delay);
  };
  wrapped.cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  return wrapped;
}

function toDateTimeLocal(value) {
  if (!value) return '';
  const normalized = String(value).trim().replace(' ', 'T');
  if (!normalized) return '';
  // datetime-local accepts seconds; trim timezone suffixes if source data ever includes them.
  const match = normalized.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2}))?/);
  if (!match) return '';
  return match[2] ? `${match[1]}:${match[2]}` : match[1];
}

function selectedTableMeta() {
  return state.tables.find(t => t.name === state.selectedTable) || null;
}

function applyTableTimeBounds(force = false) {
  const meta = selectedTableMeta();
  if (!meta) return;
  const start = toDateTimeLocal(meta.start_time);
  const end = toDateTimeLocal(meta.end_time);
  if (els.startTimeInput && start) {
    els.startTimeInput.min = start;
    if (end) els.startTimeInput.max = end;
    if (force || !els.startTimeInput.value) els.startTimeInput.value = start;
  }
  if (els.endTimeInput && end) {
    if (start) els.endTimeInput.min = start;
    els.endTimeInput.max = end;
    if (force || !els.endTimeInput.value) els.endTimeInput.value = end;
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function isOriginalViewMode() {
  return state.selectedFaultType === ORIGINAL_VIEW_MODE;
}

function selectedFaultMeta() {
  if (isOriginalViewMode()) {
    return {
      name: ORIGINAL_VIEW_MODE,
      label: '原始曲线',
      description: '不施加任何模拟故障，仅查看源数据表原始曲线。',
      formula: '',
      builtin: true,
      uses_severity: false,
      supports_time_range: false,
      original_view: true,
      config_schema: [],
    };
  }
  return state.faultTypes.find(item => item.name === state.selectedFaultType) || null;
}

function optionValue(option) {
  return option && typeof option === 'object' ? option.value : option;
}

function optionLabel(option) {
  return option && typeof option === 'object' ? (option.label ?? option.value) : option;
}

function renderFaultFormulaHelp() {
  if (!els.faultFormulaHelp) return;
  const info = selectedFaultMeta();
  if (!info) {
    els.faultFormulaHelp.innerHTML = '<div class="fault-help-title">参数说明</div><div class="muted">可直接查看原始曲线，或选择一种故障类型进行注入对比。</div>';
    return;
  }
  if (info.original_view) {
    els.faultFormulaHelp.innerHTML = `
      <div class="fault-help-title">原始曲线 · 不注入模拟故障</div>
      <div>当前仅展示源数据表中的原始数值，不对数据做任何故障注入。</div>
      <div class="muted">注意：如果源数据表本身包含历史故障记录（label=1），这些记录仍会保留；需要“全正常”曲线时，请选择仅正常数据表。</div>
    `;
    return;
  }

  const usesSeverity = info.uses_severity !== false;
  const severity = els.severityPreset?.value === 'custom'
    ? Number(els.severityCustom?.value)
    : Number(els.severityPreset?.value);
  const current = usesSeverity && Number.isFinite(severity)
    ? `<div class="muted">当前故障程度 s=${severity.toFixed(2)}（约 ${Math.round(severity * 100)}%）</div>`
    : '';
  const source = info.builtin ? '内置故障类型' : '自定义故障类型插件';
  const formula = info.formula
    ? `<code>${escapeHtml(info.formula)}</code>`
    : '<div class="muted">该插件未提供公式说明。</div>';
  const description = info.description
    ? `<div>${escapeHtml(info.description)}</div>`
    : '';
  els.faultFormulaHelp.innerHTML = `
    <div class="fault-help-title">${escapeHtml(info.label || info.name)} <span class="muted">· ${source}</span></div>
    ${formula}
    ${description}
    ${current}
  `;
}

function createFaultParamControl(field) {
  const wrapper = document.createElement('label');
  wrapper.className = 'field';
  const title = document.createElement('span');
  title.textContent = field.label || field.name;
  if (field.required) title.textContent += ' *';
  wrapper.appendChild(title);

  const type = String(field.type || 'str').toLowerCase();
  let input;
  if (type === 'select' || (Array.isArray(field.options) && field.options.length)) {
    input = document.createElement('select');
    (field.options || []).forEach(option => {
      const opt = document.createElement('option');
      opt.value = String(optionValue(option));
      opt.textContent = String(optionLabel(option));
      input.appendChild(opt);
    });
    if (field.default !== undefined) input.value = String(field.default);
  } else if (type === 'bool' || type === 'boolean') {
    input = document.createElement('select');
    input.innerHTML = '<option value="true">是</option><option value="false">否</option>';
    input.value = String(field.default !== undefined ? !!field.default : false);
  } else {
    input = document.createElement('input');
    if (type === 'float' || type === 'number' || type === 'int' || type === 'integer') {
      input.type = 'number';
      if (field.min !== undefined) input.min = String(field.min);
      if (field.max !== undefined) input.max = String(field.max);
      input.step = String(field.step !== undefined ? field.step : (type === 'int' || type === 'integer' ? 1 : 'any'));
    } else {
      input.type = 'text';
    }
    if (field.default !== undefined) input.value = String(field.default);
    if (field.placeholder) input.placeholder = String(field.placeholder);
  }
  input.dataset.faultParam = field.name;
  input.dataset.paramType = type;
  input.dataset.paramRequired = field.required ? 'true' : 'false';
  input.addEventListener('input', debouncedPreview);
  input.addEventListener('change', debouncedPreview);
  wrapper.appendChild(input);

  if (field.description) {
    const help = document.createElement('span');
    help.className = 'fault-param-help';
    help.textContent = field.description;
    wrapper.appendChild(help);
  }
  return wrapper;
}

function renderFaultParameters() {
  if (!els.faultParamFields) return;
  els.faultParamFields.innerHTML = '';
  const meta = selectedFaultMeta();
  if (!meta) return;
  (meta.config_schema || []).forEach(field => {
    if (!field || !field.name || field.name === 'severity') return;
    els.faultParamFields.appendChild(createFaultParamControl(field));
  });
}

function selectFaultType(name, { preview = true } = {}) {
  state.selectedFaultType = name || null;
  if (isOriginalViewMode()) {
    state.previewFaulty = [];
    if (state.previewOriginal.length) renderComparisonChart();
  }
  if (els.faultTypeGrid) {
    els.faultTypeGrid.querySelectorAll('.fault-type-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.type === state.selectedFaultType);
    });
  }
  updateFaultTypeFields();
  if (preview) debouncedPreview();
}

function renderFaultTypes() {
  if (!els.faultTypeGrid) return;
  els.faultTypeGrid.innerHTML = '';

  const originalBtn = document.createElement('button');
  originalBtn.type = 'button';
  originalBtn.className = 'fault-type-btn fault-type-original';
  originalBtn.dataset.type = ORIGINAL_VIEW_MODE;
  originalBtn.title = '不施加模拟故障，仅查看当前源数据表的原始曲线';
  const originalLabel = document.createElement('span');
  originalLabel.textContent = '原始曲线';
  originalBtn.appendChild(originalLabel);
  const originalMark = document.createElement('span');
  originalMark.className = 'fault-custom-mark';
  originalMark.textContent = '不注入';
  originalBtn.appendChild(originalMark);
  originalBtn.addEventListener('click', () => selectFaultType(ORIGINAL_VIEW_MODE));
  els.faultTypeGrid.appendChild(originalBtn);

  state.faultTypes.forEach(item => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'fault-type-btn';
    btn.dataset.type = item.name;
    btn.title = item.description || item.label || item.name;
    const label = document.createElement('span');
    label.textContent = item.label || item.name;
    btn.appendChild(label);
    if (!item.builtin) {
      const mark = document.createElement('span');
      mark.className = 'fault-custom-mark';
      mark.textContent = '自定义';
      btn.appendChild(mark);
    }
    btn.addEventListener('click', () => selectFaultType(item.name));
    els.faultTypeGrid.appendChild(btn);
  });

  const stillExists = state.selectedFaultType === ORIGINAL_VIEW_MODE
    || state.faultTypes.some(item => item.name === state.selectedFaultType);
  if (!stillExists || !state.selectedFaultType) state.selectedFaultType = ORIGINAL_VIEW_MODE;
  els.faultTypeGrid.querySelectorAll('.fault-type-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.type === state.selectedFaultType);
  });
  updateFaultTypeFields();
}
async function loadFaultTypes() {
  try {
    const result = await api('/api/simulation/fault-types');
    state.faultTypes = (result.items || []).filter(item => !item.dependencies_status || item.dependencies_status.installed);
  } catch (err) {
    state.faultTypes = [];
    showLog(`故障类型加载失败: ${err.message}`, 'error');
  }
  renderFaultTypes();
}

function updateSimulationActionState() {
  const exists = !!state.simStatus?.exists;
  if (els.initSimBtn) {
    els.initSimBtn.textContent = exists ? '重新初始化模拟数据库' : '初始化模拟数据库';
    els.initSimBtn.title = exists
      ? '使用 history_sample.db 完全重建 simulation.db；现有故障表会被删除'
      : '从 history_sample.db 创建 simulation.db';
  }
  if (els.syncSimBtn) {
    els.syncSimBtn.disabled = !exists || state.lifecycleBusy;
    els.syncSimBtn.title = exists ? '同步 history_sample.db 中新增的原始数据' : '请先初始化模拟数据库';
  }
  if (els.initSimBtn) els.initSimBtn.disabled = state.lifecycleBusy;
  if (els.deleteSimBtn) {
    els.deleteSimBtn.style.display = exists ? '' : 'none';
    els.deleteSimBtn.disabled = state.lifecycleBusy;
  }
  if (els.previewBtn) els.previewBtn.disabled = state.lifecycleBusy || !exists || !state.selectedTable;
  if (els.injectBtn) els.injectBtn.disabled = state.lifecycleBusy || !exists || !state.selectedTable || !state.selectedFaultType || isOriginalViewMode();
  if (els.simTableSelect) els.simTableSelect.disabled = state.lifecycleBusy || !exists;
}

function renderStatusBar() {
  if (!els.simStatusBar) return;

  updateSimulationActionState();
  if (!state.simStatus || !state.simStatus.exists) {
    els.simStatusBar.innerHTML = `
      <span class="badge badge-none">未初始化</span>
      <span class="info">${state.simStatus?.message || '模拟数据库尚未创建'}</span>
    `;
    els.simMainArea.style.display = 'none';
    return;
  }

  const s = state.simStatus;
  const infoParts = [];
  if (s.original_tables !== undefined) infoParts.push(`原始表: ${s.original_tables}`);
  if (s.fault_tables !== undefined) infoParts.push(`故障表: ${s.fault_tables}`);
  if (s.size_mb !== undefined) infoParts.push(`大小: ${s.size_mb} MB`);

  els.simStatusBar.innerHTML = `
    <span class="badge badge-ok">已就绪</span>
    <span class="info">${infoParts.join(' | ')}</span>
  `;
  els.simMainArea.style.display = 'block';
}

async function loadStatus() {
  try {
    state.simStatus = await api('/api/simulation/status');
  } catch {
    state.simStatus = { exists: false, message: '无法获取模拟数据库状态' };
  }
  renderStatusBar();
}

async function initSimDb() {
  const exists = !!state.simStatus?.exists;
  const faultCount = Number(state.simStatus?.fault_tables || 0);
  const prompt = exists
    ? `模拟数据库已存在。重新初始化会使用 history_sample.db 完全重建 simulation.db${faultCount > 0 ? `，并删除当前 ${faultCount} 张故障表` : ''}。\n\n此操作不可撤销，但不会修改 history_sample.db。确定继续吗？`
    : '确定要初始化模拟数据库吗？这将从 history_sample.db 复制所有原始数据表。';
  if (!confirm(prompt)) return;

  state.lifecycleBusy = true;
  debouncedPreview.cancel?.();
  updateSimulationActionState();
  els.initSimBtn.textContent = exists ? '重新初始化中...' : '初始化中...';
  showLog(exists ? '正在重新初始化模拟数据库...' : '正在初始化...', '');

  try {
    const result = await api('/api/simulation/init', {
      method: 'POST',
      body: JSON.stringify({ force: exists }),
    });
    if (result.status !== 'success') throw new Error(result.message);
    showLog(result.message, 'success');
    await loadStatus();
    await loadTables();
  } catch (err) {
    showLog(`${exists ? '重新初始化' : '初始化'}失败: ${err.message}`, 'error');
  } finally {
    state.lifecycleBusy = false;
    updateSimulationActionState();
  }
}

async function deleteSimDb() {
  if (!state.simStatus?.exists) return;
  const originalCount = Number(state.simStatus.original_tables || 0);
  const faultCount = Number(state.simStatus.fault_tables || 0);
  const prompt = `确定删除模拟数据库 simulation.db 吗？\n\n将删除 ${originalCount} 张原始表和 ${faultCount} 张故障表。此操作不可撤销，但不会影响 history_sample.db。`;
  if (!confirm(prompt)) return;

  state.lifecycleBusy = true;
  debouncedPreview.cancel?.();
  updateSimulationActionState();
  els.deleteSimBtn.textContent = '删除中...';
  try {
    const result = await api('/api/simulation/delete', { method: 'POST' });
    if (result.status !== 'success') throw new Error(result.message);
    state.tables = [];
    state.selectedTable = null;
    state.previewOriginal = [];
    state.previewFaulty = [];
    state.simStatus = { exists: false, message: result.message || '模拟数据库已删除，可重新初始化。' };
    if (els.simTableSelect) els.simTableSelect.innerHTML = '<option value="">暂无数据表</option>';
    if (els.comparisonChart) {
      els.comparisonChart.innerHTML = '<div class="empty-state">模拟数据库已删除，请先重新初始化。</div>';
    }
    renderStatusBar();
  } catch (err) {
    alert(`删除失败: ${err.message}`);
    await loadStatus();
  } finally {
    state.lifecycleBusy = false;
    els.deleteSimBtn.textContent = '删除模拟数据库';
    updateSimulationActionState();
  }
}

async function syncSimDb() {
  state.lifecycleBusy = true;
  debouncedPreview.cancel?.();
  updateSimulationActionState();
  els.syncSimBtn.textContent = '同步中...';
  showLog('正在同步...', '');

  try {
    const result = await api('/api/simulation/sync', { method: 'POST' });
    if (result.status !== 'success') throw new Error(result.message);
    showLog(result.message, 'success');
    await loadStatus();
    await loadTables();
  } catch (err) {
    showLog(`同步失败: ${err.message}`, 'error');
  } finally {
    state.lifecycleBusy = false;
    els.syncSimBtn.textContent = '更新模拟数据库';
    updateSimulationActionState();
  }
}

async function loadTables() {
  try {
    const result = await api('/api/simulation/tables');
    state.tables = result.items || [];
    if (state.tables.length > 0) {
      els.simTableSelect.innerHTML = state.tables.map(t =>
        `<option value="${t.name}">${t.name} (${t.row_count} 条)</option>`
      ).join('');
      state.selectedTable = state.tables[0].name;
      applyTableTimeBounds(true);
      updateSimulationActionState();
      debouncedPreview();
    } else {
      els.simTableSelect.innerHTML = '<option value="">暂无数据表</option>';
      state.selectedTable = null;
    }
  } catch (err) {
    els.simTableSelect.innerHTML = '<option value="">加载失败</option>';
  }
}

function readFaultParam(input) {
  const type = String(input.dataset.paramType || 'str').toLowerCase();
  const raw = input.value;
  if (input.dataset.paramRequired === 'true' && raw === '') {
    throw new Error(`请填写参数“${input.closest('.field')?.querySelector('span')?.textContent || input.dataset.faultParam}”`);
  }
  if (raw === '') return undefined;
  if (type === 'float' || type === 'number') {
    const value = Number(raw);
    if (!Number.isFinite(value)) throw new Error(`参数 ${input.dataset.faultParam} 必须是数值`);
    return value;
  }
  if (type === 'int' || type === 'integer') {
    const value = Number(raw);
    if (!Number.isInteger(value)) throw new Error(`参数 ${input.dataset.faultParam} 必须是整数`);
    return value;
  }
  if (type === 'bool' || type === 'boolean') return raw === 'true';
  return raw;
}

function buildFaultConfig() {
  const meta = selectedFaultMeta();
  if (!meta) throw new Error('请选择故障类型');
  if (meta.original_view) return {};
  const mode = document.querySelector('input[name="injectMode"]:checked')?.value || 'full';
  const config = { mode };

  if (meta.uses_severity !== false) {
    const severityVal = els.severityPreset.value;
    const severity = severityVal === 'custom' ? parseFloat(els.severityCustom.value) : parseFloat(severityVal);
    if (!Number.isFinite(severity) || severity < 0) {
      throw new Error('请输入有效的故障程度 s（必须大于或等于 0）');
    }
    config.severity = severity;
  }

  if (mode === 'time_range') {
    if (meta.supports_time_range === false) throw new Error('当前故障类型不支持指定时间段');
    const start = els.startTimeInput.value || '';
    const end = els.endTimeInput.value || '';
    if (!start || !end) throw new Error('指定时间段时必须填写起始时间和结束时间');
    if (start > end) throw new Error('起始时间不能晚于结束时间');
    config.start_time = start;
    config.end_time = end;
  }

  if (els.faultParamFields) {
    els.faultParamFields.querySelectorAll('[data-fault-param]').forEach(input => {
      const value = readFaultParam(input);
      if (value !== undefined) config[input.dataset.faultParam] = value;
    });
  }
  return config;
}

const debouncedPreview = debounce(async () => {
  if (state.lifecycleBusy || !state.selectedTable) return;
  if (!state.selectedFaultType) state.selectedFaultType = ORIGINAL_VIEW_MODE;

  els.previewBtn.disabled = true;
  els.previewBtn.textContent = isOriginalViewMode() ? '加载原始曲线...' : '预览中...';

  try {
    const config = buildFaultConfig();
    const result = await api('/api/simulation/preview', {
      method: 'POST',
      body: JSON.stringify({
        db: 'datasets/simulation.db',
        table: state.selectedTable,
        faultType: state.selectedFaultType,
        config,
      }),
    });

    state.previewOriginal = result.original || [];
    state.previewFaulty = result.faulty || [];
    renderComparisonChart();
    showLog(
      isOriginalViewMode()
        ? `原始曲线加载成功: ${result.row_count} 条数据`
        : `预览成功: ${result.row_count} 条数据`,
      'success'
    );
  } catch (err) {
    showLog(`${isOriginalViewMode() ? '原始曲线加载' : '预览'}失败: ${err.message}`, 'error');
  } finally {
    // Preview completion only needs to restore button/action state. Re-rendering
    // the dynamic fault-parameter controls here would recreate every input from
    // its schema default (for example periodic.frequency -> 1.0), discarding
    // the value the user just entered.
    updateFaultTypeFields({ renderParameters: false });
    updateSimulationActionState();
  }
}, 500);

async function executeInjection() {
  if (state.lifecycleBusy) {
    showLog('模拟数据库正在执行初始化、同步或删除，请稍后再试。', 'error');
    return;
  }
  if (!state.selectedTable) {
    showLog('请先选择数据表', 'error');
    return;
  }
  if (!state.selectedFaultType || isOriginalViewMode()) {
    showLog('当前为原始曲线模式；如需执行注入，请先选择一种故障类型。', 'error');
    return;
  }
  const faultLabel = selectedFaultMeta()?.label || state.selectedFaultType;
  if (!confirm(`确定要对表 "${state.selectedTable}" 执行“${faultLabel}”故障注入吗？`)) return;

  els.injectBtn.disabled = true;
  els.injectBtn.textContent = '注入中...';

  try {
    const config = buildFaultConfig();
    const result = await api('/api/simulation/inject', {
      method: 'POST',
      body: JSON.stringify({
        table: state.selectedTable,
        faultType: state.selectedFaultType,
        config,
      }),
    });

    if (result.status === 'error') throw new Error(result.message);
    showLog(result.message, 'success');
    await loadStatus();
    await loadFaultTables();
  } catch (err) {
    showLog(`注入失败: ${err.message}`, 'error');
  } finally {
    els.injectBtn.disabled = false;
    els.injectBtn.textContent = '执行注入';
  }
}

function renderComparisonChart() {
  if (!els.comparisonChart) return;

  const original = state.previewOriginal;
  const faulty = state.previewFaulty;
  const hasFaultComparison = faulty.length > 0 && !isOriginalViewMode();

  if (!original.length) {
    els.comparisonChart.innerHTML = '<div class="empty-state">无数据可显示</div>';
    return;
  }

  const width = 800;
  const height = 300;
  const padding = { top: 30, right: 20, bottom: 50, left: 60 };

  const origValues = original.map(r => Number(r.value)).filter(v => isFinite(v));
  const faultValues = hasFaultComparison ? faulty.map(r => Number(r.value)).filter(v => isFinite(v)) : [];
  const allValues = [...origValues, ...faultValues].filter(v => isFinite(v));

  if (!allValues.length) {
    els.comparisonChart.innerHTML = '<div class="empty-state">没有有效的数值数据</div>';
    return;
  }

  const minVal = Math.min(...allValues);
  const maxVal = Math.max(...allValues);
  const range = maxVal - minVal || 1;

  const yScale = v => height - padding.bottom - ((v - minVal) / range) * (height - padding.top - padding.bottom);
  const xScale = i => padding.left + (i / Math.max(original.length - 1, 1)) * (width - padding.left - padding.right);

  const origPoints = original.map((r, i) => {
    const v = Number(r.value);
    return isFinite(v) ? `${xScale(i)},${yScale(v)}` : null;
  }).filter(Boolean).join(' ');

  const faultPoints = hasFaultComparison ? faulty.map((r, i) => {
    const v = Number(r.value);
    return isFinite(v) ? `${xScale(i)},${yScale(v)}` : null;
  }).filter(Boolean).join(' ') : '';

  const grid = Array.from({ length: 5 }, (_, i) => minVal + (i / 4) * range).map(tick => {
    const y = yScale(tick);
    return `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="var(--line)" />
            <text x="8" y="${y + 4}" fill="var(--muted)" font-size="10">${tick.toFixed(2)}</text>`;
  }).join('');

  const timestamps = original.map(r => r.timestamp || '');
  const tsLeft = timestamps[0] || '';
  const tsRight = timestamps[timestamps.length - 1] || '';

  els.comparisonChart.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" style="width: 100%; height: 100%;">
      ${grid}
      <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--line-strong)" />
      <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--line-strong)" />
      <polyline fill="none" stroke="#7aa2ff" stroke-width="2" points="${origPoints}" />
      ${hasFaultComparison ? `<polyline fill="none" stroke="#ff8a8a" stroke-width="2" stroke-dasharray="6,3" points="${faultPoints}" />` : ''}
      <text x="${padding.left}" y="${height - 16}" fill="var(--muted)" font-size="10">${tsLeft}</text>
      <text x="${width - padding.right - 160}" y="${height - 16}" fill="var(--muted)" font-size="10">${tsRight}</text>
      <rect x="${padding.left}" y="8" width="10" height="10" rx="3" fill="#7aa2ff" />
      <text x="${padding.left + 14}" y="17" fill="var(--text)" font-size="11">${hasFaultComparison ? '原始数据' : '原始数据（未注入）'}</text>
      ${hasFaultComparison ? `
        <rect x="${padding.left + 80}" y="8" width="10" height="10" rx="3" fill="#ff8a8a" />
        <text x="${padding.left + 94}" y="17" fill="var(--text)" font-size="11">故障数据</text>
      ` : ''}
    </svg>
  `;

  if (els.chartMeta) {
    const modeLabel = hasFaultComparison
      ? (selectedFaultMeta()?.label || state.selectedFaultType || '')
      : '原始曲线 · 未注入模拟故障';
    els.chartMeta.textContent = `${state.selectedTable} | ${original.length} 条 | ${modeLabel}`;
  }
}

function showLog(message, type) {
  if (!els.simLog) return;
  els.simLog.style.display = 'block';
  els.simLog.className = `log-box ${type}`;
  els.simLog.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
}

async function loadFaultTables() {
  try {
    const result = await api('/api/simulation/fault-tables');
    state.faultTables = result.items || [];
  } catch {
    state.faultTables = [];
  }
}

function renderFaultTableList() {
  if (!els.faultTableList) return;

  if (!state.faultTables.length) {
    els.faultTableList.innerHTML = '<div class="empty-state">暂无故障表</div>';
    return;
  }

  const typeMap = Object.fromEntries(state.faultTypes.map(item => [item.name, item.label || item.name]));

  els.faultTableList.innerHTML = state.faultTables.map(ft => `
    <div class="fault-table-item">
      <div class="ft-name">
        <strong>${ft.name}</strong>
        <div class="ft-meta">
          原表: ${ft.original_table} | 类型: ${typeMap[ft.fault_type] || ft.fault_type} | 记录: ${ft.row_count}
        </div>
      </div>
      <div class="ft-actions">
        <button class="ghost" onclick="previewFaultTable('${ft.name}')">预览</button>
        <button class="ghost" onclick="deleteFaultTable('${ft.name}')" style="color: var(--danger);">删除</button>
      </div>
    </div>
  `).join('');
}

async function deleteFaultTable(tableName) {
  if (!confirm(`确定要删除故障表 "${tableName}" 吗？此操作不可撤销。`)) return;

  try {
    const result = await api('/api/simulation/fault-table/delete', {
      method: 'POST',
      body: JSON.stringify({ tableName }),
    });
    if (result.status === 'error') throw new Error(result.message);
    await loadFaultTables();
    renderFaultTableList();
    await loadStatus();
  } catch (err) {
    alert(`删除失败: ${err.message}`);
  }
}

const FAULT_TABLE_PREVIEW_PAGE_SIZE = 5000;
const FAULT_TABLE_PREVIEW_MAX_POINTS = 1200;

function faultPreviewTimeColumn(columns = []) {
  const preferred = ['timestamp', 'time', 'datetime', 'date'];
  return preferred.find(name => columns.includes(name)) || null;
}

async function loadAllFaultPreviewRows(tableName) {
  const db = 'datasets/simulation.db';
  const metaParams = new URLSearchParams({
    db,
    table: tableName,
    limit: '1',
    offset: '0',
  });
  const meta = await api(`/api/sqlite-rows?${metaParams.toString()}`);
  const matchedCount = Number(meta.matched_count ?? meta.total_count ?? 0);
  if (!matchedCount) {
    return { ...meta, rows: [], preview_time_col: null };
  }

  const timeCol = faultPreviewTimeColumn(meta.columns || []);
  const allRows = [];
  let offset = 0;

  while (offset < matchedCount) {
    const params = new URLSearchParams({
      db,
      table: tableName,
      limit: String(Math.min(FAULT_TABLE_PREVIEW_PAGE_SIZE, matchedCount - offset)),
      offset: String(offset),
    });
    if (timeCol) params.set('time_col', timeCol);

    const page = await api(`/api/sqlite-rows?${params.toString()}`);
    const pageRows = Array.isArray(page.rows) ? page.rows : [];
    if (!pageRows.length) break;
    allRows.push(...pageRows);
    offset += pageRows.length;
    if (!page.truncated) break;
  }

  if (allRows.length < matchedCount) {
    throw new Error(`故障表应有 ${matchedCount} 条记录，但只加载到 ${allRows.length} 条，请重试`);
  }

  return {
    ...meta,
    rows: allRows,
    total_count: Number(meta.total_count ?? matchedCount),
    matched_count: matchedCount,
    truncated: false,
    preview_time_col: timeCol,
  };
}

function downsampleFaultPreviewRows(rows, maxPoints = FAULT_TABLE_PREVIEW_MAX_POINTS) {
  const valid = [];
  rows.forEach((row, index) => {
    const value = Number(row.value);
    if (Number.isFinite(value)) valid.push({ row, index, value });
  });
  if (valid.length <= maxPoints || maxPoints < 4) return valid;

  // Each bucket keeps both its local minimum and maximum (in original order).
  // Compared with a simple stride this preserves short spikes and dips, which
  // are exactly the features a fault preview must not hide.
  const first = valid[0];
  const last = valid[valid.length - 1];
  const interior = valid.slice(1, -1);
  const bucketCount = Math.max(1, Math.floor((maxPoints - 2) / 2));
  const sampled = [first];

  for (let bucket = 0; bucket < bucketCount; bucket += 1) {
    const start = Math.floor((bucket * interior.length) / bucketCount);
    const end = Math.floor(((bucket + 1) * interior.length) / bucketCount);
    if (start >= end) continue;

    let minPoint = interior[start];
    let maxPoint = interior[start];
    for (let i = start + 1; i < end; i += 1) {
      const point = interior[i];
      if (point.value < minPoint.value) minPoint = point;
      if (point.value > maxPoint.value) maxPoint = point;
    }

    if (minPoint.index === maxPoint.index) {
      sampled.push(minPoint);
    } else if (minPoint.index < maxPoint.index) {
      sampled.push(minPoint, maxPoint);
    } else {
      sampled.push(maxPoint, minPoint);
    }
  }

  sampled.push(last);
  return sampled;
}

function formatFaultPreviewTime(value) {
  if (value === null || value === undefined || value === '') return '';
  return String(value).replace('T', ' ');
}

async function previewFaultTable(tableName) {
  try {
    const payload = await loadAllFaultPreviewRows(tableName);
    const allRows = payload.rows || [];
    if (!allRows.length) {
      alert('该表没有数据');
      return;
    }

    const width = 700;
    const height = 290;
    const padding = { top: 20, right: 20, bottom: 52, left: 60 };
    const plotted = downsampleFaultPreviewRows(allRows);
    if (!plotted.length) {
      alert('没有有效的数值数据');
      return;
    }

    let minVal = Infinity;
    let maxVal = -Infinity;
    allRows.forEach(row => {
      const value = Number(row.value);
      if (!Number.isFinite(value)) return;
      if (value < minVal) minVal = value;
      if (value > maxVal) maxVal = value;
    });
    if (!Number.isFinite(minVal) || !Number.isFinite(maxVal)) {
      alert('没有有效的数值数据');
      return;
    }

    const range = maxVal - minVal || 1;
    const yScale = value => height - padding.bottom - ((value - minVal) / range) * (height - padding.top - padding.bottom);
    // Use the original row index, not the sampled-point index, so downsampling
    // never shortens or distorts the full-table time axis.
    const xScale = index => padding.left + (index / Math.max(allRows.length - 1, 1)) * (width - padding.left - padding.right);
    const points = plotted.map(point => `${xScale(point.index)},${yScale(point.value)}`).join(' ');

    const timeCol = payload.preview_time_col;
    const firstTime = timeCol ? formatFaultPreviewTime(allRows[0]?.[timeCol]) : '';
    const lastTime = timeCol ? formatFaultPreviewTime(allRows[allRows.length - 1]?.[timeCol]) : '';
    const totalCount = Number(payload.total_count ?? allRows.length);
    const content = `
      <div style="margin-top: 12px;">
        <h4>故障表预览: ${escapeHtml(tableName)}</h4>
        <svg viewBox="0 0 ${width} ${height}" style="width: 100%; height: 220px;">
          <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--line-strong)" />
          <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--line-strong)" />
          <polyline fill="none" stroke="#ff8a8a" stroke-width="2" points="${points}" />
          ${firstTime ? `<text x="${padding.left}" y="${height - 18}" text-anchor="start" font-size="11" fill="var(--muted)">${escapeHtml(firstTime)}</text>` : ''}
          ${lastTime ? `<text x="${width - padding.right}" y="${height - 18}" text-anchor="end" font-size="11" fill="var(--muted)">${escapeHtml(lastTime)}</text>` : ''}
        </svg>
        <p style="color: var(--muted); font-size: 13px;">总计 ${totalCount} 条记录 · 实际绘制 ${plotted.length} 个点${plotted.length < allRows.length ? '（保峰值降采样）' : ''}</p>
      </div>
    `;

    const modalContent = els.faultTableModal.querySelector('.modal-content');
    const existingPreview = modalContent.querySelector('.fault-preview');
    if (existingPreview) existingPreview.remove();

    const previewDiv = document.createElement('div');
    previewDiv.className = 'fault-preview';
    previewDiv.innerHTML = content;
    modalContent.appendChild(previewDiv);
  } catch (err) {
    alert(`预览失败: ${err.message}`);
  }
}

// Event bindings
if (els.initSimBtn) els.initSimBtn.addEventListener('click', initSimDb);
if (els.syncSimBtn) els.syncSimBtn.addEventListener('click', syncSimDb);
if (els.deleteSimBtn) els.deleteSimBtn.addEventListener('click', deleteSimDb);

if (els.simTableSelect) {
  els.simTableSelect.addEventListener('change', (e) => {
    state.selectedTable = e.target.value;
    applyTableTimeBounds(true);
    debouncedPreview();
  });
}

document.querySelectorAll('input[name="injectMode"]').forEach(radio => {
  radio.addEventListener('change', (e) => {
    const isRange = e.target.value === 'time_range';
    els.timeRangeFields.style.display = isRange ? 'grid' : 'none';
    if (isRange) applyTableTimeBounds(false);
    debouncedPreview();
  });
});

if (els.severityPreset) {
  els.severityPreset.addEventListener('change', () => {
    els.severityCustom.style.display = els.severityPreset.value === 'custom' ? 'block' : 'none';
    renderFaultFormulaHelp();
    debouncedPreview();
  });
}

if (els.severityCustom) {
  els.severityCustom.addEventListener('input', () => {
    renderFaultFormulaHelp();
    debouncedPreview();
  });
}

if (els.previewBtn) els.previewBtn.addEventListener('click', () => debouncedPreview());
if (els.injectBtn) els.injectBtn.addEventListener('click', executeInjection);

if (els.faultTableMgrBtn) {
  els.faultTableMgrBtn.addEventListener('click', async () => {
    await loadFaultTables();
    renderFaultTableList();
    els.faultTableModal.classList.add('show');
  });
}

if (els.closeFaultModalBtn) {
  els.closeFaultModalBtn.addEventListener('click', () => {
    els.faultTableModal.classList.remove('show');
    const preview = els.faultTableModal.querySelector('.modal-content .fault-preview');
    if (preview) preview.remove();
  });
}

if (els.faultTableModal) {
  els.faultTableModal.addEventListener('click', (e) => {
    if (e.target === els.faultTableModal) {
      els.faultTableModal.classList.remove('show');
      const preview = els.faultTableModal.querySelector('.modal-content .fault-preview');
      if (preview) preview.remove();
    }
  });
}

function updateFaultTypeFields({ renderParameters = true } = {}) {
  const meta = selectedFaultMeta();
  const originalMode = !!meta?.original_view;
  const usesSeverity = !!meta && !originalMode && meta.uses_severity !== false;
  if (els.severityField) els.severityField.style.display = usesSeverity ? 'block' : 'none';
  if (els.injectScopeField) els.injectScopeField.style.display = originalMode ? 'none' : 'block';

  const rangeRadio = document.querySelector('input[name="injectMode"][value="time_range"]');
  const fullRadio = document.querySelector('input[name="injectMode"][value="full"]');
  const supportsRange = !originalMode && (!meta || meta.supports_time_range !== false);
  if (rangeRadio) {
    rangeRadio.disabled = !supportsRange;
    rangeRadio.closest('label')?.classList.toggle('disabled', !supportsRange);
  }
  if ((originalMode || !supportsRange) && rangeRadio?.checked && fullRadio) {
    fullRadio.checked = true;
  }
  if (els.timeRangeFields) {
    const showRange = !originalMode && supportsRange && !!rangeRadio?.checked;
    els.timeRangeFields.style.display = showRange ? 'grid' : 'none';
  }

  if (els.previewBtn) {
    els.previewBtn.textContent = originalMode ? '查看原始曲线' : '预览故障效果';
    els.previewBtn.title = originalMode ? '查看源数据表原始曲线，不施加模拟故障' : '预览当前故障类型的注入效果';
  }
  if (els.injectBtn) {
    els.injectBtn.textContent = originalMode ? '无需注入' : '执行注入';
    els.injectBtn.title = originalMode ? '原始曲线模式不会创建故障表；请选择故障类型后再执行注入' : '创建模拟故障数据表';
  }

  if (renderParameters) renderFaultParameters();
  renderFaultFormulaHelp();
  updateSimulationActionState();
}
async function init() {
  renderFaultFormulaHelp();
  await Promise.all([loadStatus(), loadFaultTypes()]);
  if (state.simStatus && state.simStatus.exists) {
    await loadTables();
  }
}

init();
