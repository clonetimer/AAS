/* Shared independent data-table export UI for historical and simulation databases. */
(() => {
  const context = window.AAS_EXPORT_CONTEXT;
  const modal = document.getElementById('exportTablesModal');
  const openButton = document.getElementById('exportTablesBtn');
  const closeButton = document.getElementById('closeExportTablesBtn');
  const list = document.getElementById('exportTableList');
  const search = document.getElementById('exportTableSearch');
  const status = document.getElementById('exportTableStatus');
  const submit = document.getElementById('exportSelectedTablesBtn');
  if (!modal || !openButton || !submit) return;

  let available = [];
  let db = '';
  let busy = false;
  const selected = new Set();

  function databaseReference() {
    if (context === 'simulation') return 'datasets/simulation.db';
    return document.getElementById('dbSelect')?.value || '';
  }

  function render() {
    list.replaceChildren();
    const query = search.value.trim().toLowerCase();
    const filtered = available.filter((item) => item.name.toLowerCase().includes(query));
    for (const item of filtered) {
      const label = document.createElement('label');
      label.className = 'browser-manage-row';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = selected.has(item.name);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) selected.add(item.name);
        else selected.delete(item.name);
        updateStatus();
      });
      const text = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = item.name;
      const detail = document.createElement('small');
      detail.textContent = `${item.kind} · ${Number(item.row_count || 0)} 行`;
      text.append(title, detail);
      label.append(checkbox, text);
      list.append(label);
    }
    if (!filtered.length) list.textContent = '没有可导出的数据表。';
    updateStatus();
  }

  function updateStatus() {
    status.textContent = `已选择 ${selected.size} 张表${selected.size > 40 ? '（最多 40 张）' : ''}`;
    submit.disabled = busy || selected.size === 0 || selected.size > 40;
  }

  function close() { if (!busy) modal.classList.remove('show'); }

  openButton.addEventListener('click', async () => {
    db = databaseReference();
    if (!db) {
      window.alert('请先选择数据库。');
      return;
    }
    modal.classList.add('show');
    selected.clear();
    search.value = '';
    list.textContent = '正在加载可导出的数据表…';
    status.textContent = '';
    submit.disabled = true;
    try {
      const response = await fetch(`/api/export-tables/list?db=${encodeURIComponent(db)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || '无法加载数据表');
      if (db !== databaseReference() || !modal.classList.contains('show')) return;
      available = payload.items || [];
      render();
    } catch (error) {
      list.textContent = `读取失败：${error.message}`;
    }
  });
  closeButton?.addEventListener('click', close);
  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
  search.addEventListener('input', render);

  submit.addEventListener('click', async () => {
    if (busy || !selected.size || selected.size > 40) return;
    busy = true;
    updateStatus();
    status.textContent = '正在导出完整数据，请勿关闭弹窗…';
    try {
      const response = await fetch('/api/export-tables', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ db, tables: Array.from(selected) }),
      });
      if (!response.ok) {
        let message = `HTTP ${response.status}`;
        try { const detail = await response.json(); message = detail.error || message; } catch (_) { /* server error */ }
        throw new Error(message);
      }
      const blob = await response.blob();
      const fileUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      const single = selected.size === 1;
      const tableName = Array.from(selected)[0] || 'data';
      const safeTableName = tableName.replace(/[^\p{L}\p{N}_-]+/gu, '_').slice(0, 80);
      anchor.href = fileUrl;
      anchor.download = single ? `${safeTableName || 'data'}.csv` : 'tables_export.zip';
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(fileUrl), 60000);
      status.textContent = `已生成 ${single ? 'CSV' : 'ZIP'} 下载文件（${selected.size} 张表）。`;
    } catch (error) {
      status.textContent = `导出失败：${error.message}`;
    } finally {
      busy = false;
      updateStatus();
    }
  });
})();
