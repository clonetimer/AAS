'use strict';
(function () {
  const $ = id => document.getElementById(id);
  const bytes = n => {
    if (n === null || n === undefined || !Number.isFinite(Number(n))) return '暂不可用';
    const value = Number(n);
    const abs = Math.abs(value);
    const unit = abs >= 1073741824 ? 'GB' : abs >= 1048576 ? 'MB' : abs >= 1024 ? 'KB' : 'B';
    const div = { GB: 1073741824, MB: 1048576, KB: 1024, B: 1 }[unit];
    return `${(value / div).toFixed(unit === 'B' ? 0 : 1)} ${unit}`;
  };
  const number = (x, digits = 1) => x == null || !Number.isFinite(Number(x)) ? '采集中…' : Number(x).toFixed(digits);
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function metric(label, value, sub, percent) {
    const div = element('div', null, 'monitor-metric');
    div.append(element('div', label, 'monitor-label'), element('div', value, 'monitor-value'));
    if (percent != null && Number.isFinite(percent)) {
      const progress = element('div', null, 'monitor-progress');
      const fill = element('span');
      fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
      progress.append(fill);
      div.append(progress);
    }
    div.append(element('div', sub, 'monitor-sub'));
    return div;
  }
  function tableRows(id, rows, render, empty) {
    const tbody = $(id);tbody.replaceChildren();
    if (!rows.length) {
      const row = element('tr'); const td = element('td', empty, 'monitor-sub');
      td.colSpan = tbody.closest('table').querySelectorAll('thead th').length;
      row.append(td);tbody.append(row);return;
    }
    rows.forEach(r => {
      const tr = element('tr');render(r).forEach(text => tr.append(element('td', text)));
      tbody.append(tr);
    });
  }
  function render(data) {
    const s = data.software || {}, host = data.host || {}, tasks = data.tasks || {}, api = data.api || {};
    $('monitorUpdated').textContent = `最近更新：${new Date(data.timestamp).toLocaleString('zh-CN')} · ${s.platform || ''}`;
    $('monitorScope').textContent = s.scope_note || '仅统计当前 AAS 服务进程和存活子进程';
    const io = s.disk_io || {}, cpu = s.cpu_percent_one_core, ram = s.memory_rss_bytes;
    $('monitorSoftware').replaceChildren(
      metric('AAS CPU（单核等效）', cpu == null ? '采集中…' : `${number(cpu)}%`, `整机 CPU 份额 ${number(s.cpu_percent_machine)}% · ${s.logical_cores || '?'} 个逻辑核`, s.cpu_percent_machine),
      metric('AAS 内存 RSS', bytes(ram), `占主机物理内存约 ${number(s.host_memory_share_percent, 2)}%`, s.host_memory_share_percent),
      metric('AAS 磁盘读取', io.supported ? (io.read_rate_bytes_s == null ? '采集中…' : bytes(io.read_rate_bytes_s) + '/s') : '暂不支持', `当前存活进程累计 ${bytes(io.read_bytes)}`),
      metric('AAS 磁盘写入', io.supported ? (io.write_rate_bytes_s == null ? '采集中…' : bytes(io.write_rate_bytes_s) + '/s') : '暂不支持', `当前存活进程累计 ${bytes(io.write_bytes)}`)
    );
    $('monitorProcessCount').textContent = `${s.process_count || 0} 个进程 · ${s.child_process_count || 0} 个子进程`;
    tableRows('monitorProcesses', s.processes || [], r => [r.name, r.pid, r.cpu_percent == null ? '采集中…' : `${number(r.cpu_percent)}%`, bytes(r.rss_bytes)], '当前无可读取的进程');
    $('monitorApiTotals').replaceChildren(
      metric('API 请求次数', String(api.total_calls || 0), '不包含监控轮询与登录请求'),
      metric('平均响应耗时', `${number(api.avg_latency_ms, 2)} ms`, `错误请求 ${api.total_errors || 0} 次`)
    );
    const entries = Object.entries(api.calls || {}).sort((a, b) => b[1].count - a[1].count).slice(0, 30);
    tableRows('monitorApiRows', entries, ([name, stats]) => [name, stats.count, `${number(stats.total_latency / Math.max(1, stats.count), 2)} ms`, stats.errors || 0], '暂无接口调用记录');
    $('monitorTaskStats').replaceChildren(
      element('span', `运行中 ${tasks.running || 0}`, 'monitor-badge'),
      element('span', `已完成 ${tasks.completed || 0}`, 'monitor-badge'),
      element('span', `失败 ${tasks.failed || 0}`, 'monitor-badge')
    );
    tableRows('monitorTaskRows', tasks.items || [], t => [t.id, `${number(t.progress, 0)}%`, `${t.elapsed_seconds}s`], '当前无正在执行的训练任务');
    tableRows('monitorResourceRows', data.task_resources || [], item => [
      `${item.algorithm || item.task_id?.slice(0, 8) || '训练任务'} · ${item.status === 'running' ? '运行中' : item.status === 'completed' ? '完成' : '失败'}`,
      `${number(item.elapsed_seconds)} s`,
      item.worker_thread_cpu_seconds == null ? '不可用' : `${number(item.worker_thread_cpu_seconds, 2)} s`,
      bytes(item.observed_process_group_peak_rss_bytes),
      item.gpu?.available ? bytes(item.gpu.observed_process_memory_peak_bytes) : '不可用',
      item.gpu?.available && item.gpu.observed_device_utilization_peak_percent != null ? `${number(item.gpu.observed_device_utilization_peak_percent)}%` : '不可用'
    ], '暂无任务资源记录');
    $('monitorHost').replaceChildren(
      metric('整机 CPU', `${number(host.cpu_percent)}%`, '所有程序合计', host.cpu_percent),
      metric('整机内存', `${number(host.memory_percent)}%`, `总计 ${bytes(host.memory_total_bytes)}`, host.memory_percent)
    );
    $('monitorError').hidden = true;
  }
  let pending = false;
  async function refresh() {
    if (pending || document.hidden) return;
    pending = true;
    try {
      const response = await fetch('/api/monitor/software', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (err) {
      $('monitorError').hidden = false;
      $('monitorError').textContent = `软件性能监控更新失败：${err.message}`;
    } finally { pending = false; }
  }
  refresh();
  window.setInterval(refresh, 3000);
})();
