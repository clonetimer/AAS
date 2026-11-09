(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('prepCsvView')) return;
  let inspection = null;
  let inspectedFile = null;
  let downloadUrl = null;
  const status = (message, error = false) => {
    $('csvPrepStatus').textContent = message;
    $('csvPrepStatus').classList.toggle('error', error);
  };
  function encoded(value) {
    const bytes = new TextEncoder().encode(value);
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  }
  function clearDownload() {
    if (downloadUrl) URL.revokeObjectURL(downloadUrl);
    downloadUrl = null;
    $('csvPrepDownload').hidden = true;
    $('csvPrepDownload').removeAttribute('href');
  }
  function invalidate() {
    inspection = null;
    inspectedFile = null;
    $('csvPrepColumn').replaceChildren(new Option('请先检查文件', ''));
    $('csvPrepColumn').disabled = true;
    $('csvPrepProcess').disabled = true;
    clearDownload();
  }
  function syncReady() {
    const fitBlocked = $('csvPrepRole').value === 'test' && $('csvPrepMethod').value === 'zscore';
    $('csvPrepProcess').disabled = !inspection || !$('csvPrepColumn').value || fitBlocked;
    if (fitBlocked) status('测试数据不能重新拟合 Z-score；请选择其他方法。', true);
  }
  function methodFields() {
    const method = $('csvPrepMethod').value;
    $('csvPrepWindowField').hidden = !['hampel_forward', 'moving_average', 'median_filter'].includes(method);
    $('csvPrepWindow').min = method === 'hampel_forward' ? '3' : '2';
    $('csvPrepWindow').max = method === 'hampel_forward' ? '101' : '501';
    $('csvPrepWindow').step = method === 'hampel_forward' ? '2' : '1';
    $('csvPrepWindow').value = method === 'hampel_forward' ? '7' : '5';
    $('csvPrepMinField').hidden = method !== 'clip';
    $('csvPrepMaxField').hidden = method !== 'clip';
    clearDownload();
    syncReady();
  }
  function params() {
    const method = $('csvPrepMethod').value;
    if (['hampel_forward', 'moving_average', 'median_filter'].includes(method)) {
      const windowSize = Number($('csvPrepWindow').value);
      if (!Number.isInteger(windowSize)) throw Error('请输入有效窗口大小');
      return {window: windowSize};
    }
    if (method === 'clip') {
      if (!$('csvPrepMin').value.trim() || !$('csvPrepMax').value.trim()) throw Error('请填写裁剪上下限');
      return {min: Number($('csvPrepMin').value), max: Number($('csvPrepMax').value)};
    }
    return {};
  }
  function requestHeaders(file, config) {
    return {'Content-Type': 'application/octet-stream',
      'X-AAS-CSV-Filename-B64': encoded(file.name),
      'X-AAS-Preprocess-Config-B64': encoded(JSON.stringify(config))};
  }
  async function send(path, file, config) {
    const response = await fetch(path, {method: 'POST', headers: requestHeaders(file, config), body: file});
    if (!response.ok) {
      let message = '请求失败：HTTP ' + response.status;
      try { message = (await response.json()).error || message; } catch (_) {}
      throw Error(message);
    }
    return response;
  }
  for (const [buttonId, viewId] of [['prepHistoryTab', 'prepHistoryView'], ['prepCsvTab', 'prepCsvView']]) {
    $(buttonId).addEventListener('click', () => {
      for (const [otherButton, otherView] of [['prepHistoryTab', 'prepHistoryView'], ['prepCsvTab', 'prepCsvView']]) {
        const active = otherButton === buttonId;
        $(otherButton).setAttribute('aria-selected', String(active));
        $(otherView).hidden = !active;
      }
      $(viewId).querySelector('input,select,button')?.focus();
    });
  }
  for (const id of ['csvPrepFile', 'csvPrepEncoding', 'csvPrepDelimiter']) $(id).addEventListener('change', invalidate);
  for (const id of ['csvPrepColumn', 'csvPrepRole', 'csvPrepWindow', 'csvPrepMin', 'csvPrepMax'])
    $(id).addEventListener('change', () => { clearDownload(); syncReady(); });
  $('csvPrepMethod').addEventListener('change', methodFields);
  methodFields();
  $('csvPrepInspect').addEventListener('click', async () => {
    invalidate();
    const file = $('csvPrepFile').files?.[0];
    if (!file) { status('请选择 CSV 或 CSV.GZ 文件', true); return; }
    if (file.size > 8_000_000) { status('文件不能超过 8 MB', true); return; }
    $('csvPrepInspect').disabled = true;
    status('正在检查文件…');
    try {
      const config = {encoding: $('csvPrepEncoding').value, delimiter: $('csvPrepDelimiter').value};
      inspection = await (await send('/api/v2/telemetry/preprocess-csv/inspect', file, config)).json();
      inspectedFile = file;
      $('csvPrepEncoding').value = inspection.csv_encoding;
      $('csvPrepDelimiter').value = inspection.csv_delimiter;
      $('csvPrepColumn').replaceChildren(new Option('请选择参量', ''),
        ...inspection.numeric_columns.map(name => new Option(name, name)));
      $('csvPrepColumn').disabled = false;
      syncReady();
      status(`${inspection.rows} 行 · ${inspection.numeric_columns.length} 个数值参量 · ${inspection.coordinate_mode === 'frame' ? '帧序列' : '时间序列'}`);
    } catch (error) { status(error.message, true); }
    finally { $('csvPrepInspect').disabled = false; }
  });
  $('csvPrepProcess').addEventListener('click', async () => {
    clearDownload();
    const file = $('csvPrepFile').files?.[0];
    if (!inspection || file !== inspectedFile) { status('请重新检查 CSV 文件', true); return; }
    $('csvPrepProcess').disabled = true;
    status('正在预处理…');
    try {
      const config = {column: $('csvPrepColumn').value, method: $('csvPrepMethod').value,
        role: $('csvPrepRole').value, params: params(), sha256: inspection.sha256,
        encoding: inspection.csv_encoding, delimiter: inspection.csv_delimiter};
      const response = await send('/api/v2/telemetry/preprocess-csv/process', file, config);
      const blob = await response.blob();
      downloadUrl = URL.createObjectURL(blob);
      $('csvPrepDownload').href = downloadUrl;
      $('csvPrepDownload').hidden = false;
      status(`已处理 ${response.headers.get('X-AAS-Preprocess-Rows') || inspection.rows} 行，可导出预处理后 CSV。`);
    } catch (error) { status(error.message, true); }
    finally { $('csvPrepProcess').disabled = false; }
  });
  window.addEventListener('beforeunload', clearDownload);
})();
