/* Unified detection-result plot for training, evaluation and historical reports.
 * The legacy `threshold` array contains anomaly scores, not decision limits.
 */
(function (scope) {
  'use strict';
  const MAX_POINTS = 1800;
  const finite = (value) => value === null || value === undefined || value === '' ? null :
    (Number.isFinite(Number(value)) ? Number(value) : null);
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
  const fmt = (value) => Number.isFinite(value) ? String(Number(Number(value).toPrecision(4))) : '—';

  function extract(result) {
    const trace = result?.chart_trace || result || {};
    const rows = Array.isArray(trace.rows) ? trace.rows : [];
    const legacyScores = Array.isArray(trace.threshold) ? trace.threshold : [];
    const rawScores = Array.isArray(trace.scores) ? trace.scores : legacyScores;
    const n = rows.length || rawScores.length;
    const time = rows.length ? rows.map((row, index) => row.frame_index ?? row.timestamp ?? index + 1) : Array.from({length: n}, (_, i) => i + 1);
    const scores = Array.from({length: n}, (_, i) => finite(rawScores[i]));
    const rawDynamic = trace.decision_thresholds;
    const dynamic = Array.isArray(rawDynamic) && rawDynamic.length === n ? rawDynamic.map(finite) : null;
    const fixed = finite(trace.decision_threshold);
    const limits = dynamic && dynamic.some(Number.isFinite) ? dynamic : Array(n).fill(fixed);
    const predictions = Array.isArray(trace.pred_labels) && trace.pred_labels.length === n ? trace.pred_labels : [];
    const actual = rows.map((row) => row.label === 1 || row.label === '1' ? 1 : (row.label === 0 || row.label === '0' ? 0 : null));
    const total = Number.isInteger(trace.total_samples) && trace.total_samples >= n ? trace.total_samples : n;
    return {time, coordinateMode:rows.some(row=>row.frame_index!=null)?'frame':'time', scores, limits, predictions, actual, total, n,
      overlay: trace.event_overlay || null, eventMetrics: trace.event_metrics || null,
      hasLimits: limits.some(Number.isFinite), hasScores: scores.some(Number.isFinite)};
  }

  function chooseIndices(scores, limits, cap = MAX_POINTS) {
    const n = scores.length;
    if (n <= cap) return Array.from({length: n}, (_, i) => i);
    // Include local extrema from each bucket; timestamps remain in source order.
    const indices = new Set([0, n - 1]);
    const buckets = Math.max(1, Math.floor((cap - 2) / 4));
    for (let b = 0; b < buckets; b++) {
      const start = Math.floor(b * n / buckets);
      const end = Math.floor((b + 1) * n / buckets);
      for (const series of [scores, limits]) {
        let low = null, high = null;
        for (let i = start; i < end; i++) {
          if (!Number.isFinite(series[i])) continue;
          if (low === null || series[i] < series[low]) low = i;
          if (high === null || series[i] > series[high]) high = i;
        }
        if (low !== null) indices.add(low);
        if (high !== null) indices.add(high);
      }
    }
    return Array.from(indices).sort((a, b) => a - b);
  }

  function render(target, result, options = {}) {
    if (!target) return false;
    const series = extract(result);
    if (!series.n || !series.hasScores) {
      target.innerHTML = '<div class="empty-state">本次结果没有可绘制的异常分数。标签型算法请查看明细记录。</div>';
      return false;
    }
    const width = Math.max(380, Math.round(options.width || target.clientWidth || 780));
    const height = Math.max(200, Math.round(options.height || target.clientHeight || 340));
    const small = width < 520;
    const pad = {left: small ? 52 : 66, right: 20, top: 48, bottom: 45};
    let low = Infinity, high = -Infinity;
    for (const values of [series.scores, series.limits]) {
      for (const value of values) {
        if (!Number.isFinite(value)) continue;
        low = Math.min(low, value);
        high = Math.max(high, value);
      }
    }
    const margin = high > low ? (high - low) * 0.08 : Math.max(Math.abs(high) * 0.08, 0.01);
    const min = low - margin, max = high + margin;
    const indices = chooseIndices(series.scores, series.limits);
    // Preserve actual spacing on a parseable, monotonic time axis. If the data
    // use arbitrary tick IDs, fall back to sample order instead of guessing.
    const parseTime = (value) => {
      if (typeof value === 'number') return Number.isFinite(value) ? value : null;
      if (typeof value !== 'string') return null;
      if (/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(value.trim())) return Number(value);
      const parsed = Date.parse(value.trim().replace(' ', 'T'));
      return Number.isFinite(parsed) ? parsed : null;
    };
    const times = series.time.map(parseTime);
    const chronological = times.every((value, index) => value !== null && (index === 0 || value >= times[index - 1]));
    const useTime = chronological && times[times.length - 1] > times[0];
    const x = (i) => pad.left + (useTime ? (times[i] - times[0]) / (times[times.length-1] - times[0]) :
      i / Math.max(series.n - 1, 1)) * (width - pad.left - pad.right);
    const y = (v) => height - pad.bottom - (v - min) / (max - min) * (height - pad.top - pad.bottom);
    const parseEventTime = (value) => parseTime(value);
    let overlayMarkup = '';
    let eventCounts = {fault: 0, normal: 0};
    if (useTime && series.overlay?.status === 'ok' && Array.isArray(series.overlay.items)) {
      for (const event of series.overlay.items) {
        if (event.label !== 0 && event.label !== 1) continue;
        const a = parseEventTime(event.start_timestamp), b = parseEventTime(event.end_timestamp);
        if (a === null || b === null || b <= a || b <= times[0] || a >= times[times.length - 1]) continue;
        // Clip interval [start,end) to the actual drawn time window.
        const left = pad.left + (Math.max(a, times[0]) - times[0]) / (times[times.length - 1] - times[0]) * (width - pad.left - pad.right);
        const right = pad.left + (Math.min(b, times[times.length - 1]) - times[0]) / (times[times.length - 1] - times[0]) * (width - pad.left - pad.right);
        if (right <= left) continue;
        const isFault = event.label === 1;
        eventCounts[isFault ? 'fault' : 'normal'] += 1;
        overlayMarkup += `<rect class="detector-event-band" x="${left.toFixed(2)}" y="${pad.top}" width="${(right-left).toFixed(2)}" height="${height-pad.top-pad.bottom}" fill="${isFault ? '#dc6262' : '#199e78'}" fill-opacity="0.15"><title>${escape(isFault ? '故障事件' : '已确认正常')} · ${escape(event.source_table || '')} · ${escape(event.start_timestamp)} → ${escape(event.end_timestamp)}</title></rect>`;
      }
    }
    const truncatedNotice = series.overlay?.truncated
      ? `<span class="muted">仅展示前 ${Array.isArray(series.overlay.items) ? series.overlay.items.length : 0} 个事件色带（共 ${Number(series.overlay.total_intervals) || 0} 个；指标仍基于完整事件集合）</span>` : '';
    const metric = series.eventMetrics;
    const metricLine = metric?.status === 'ok' || metric?.status === 'no_evaluable_events'
      ? `<div class="event-metric-strip" role="status">事件级召回率：${metric.event_recall === null ? '不可计算' : fmt(metric.event_recall * 100)+'%'}（${metric.fault_events_detected}/${metric.fault_events_evaluable} 可评价事件） · 首次报警延迟中位数：${metric.median_first_alarm_delay_seconds === null ? '不可计算' : fmt(metric.median_first_alarm_delay_seconds)+' 秒'} · 已确认正常区间误报：${metric.confirmed_normal_samples ? `${metric.confirmed_normal_false_alarms}/${metric.confirmed_normal_samples}` : '不可计算'} <span class="muted">（未标注时间不视为正常）</span></div>`
      : (metric?.reason ? `<div class="event-metric-strip muted">${escape(metric.reason)}</div>` : '');
    const eventLegend = (eventCounts.fault || eventCounts.normal)
      ? `<div class="event-chart-legend">${eventCounts.fault ? '<span style="color:#bd514e">■ 故障事件</span> ' : ''}${eventCounts.normal ? '<span style="color:#199e78">■ 已确认正常</span>' : ''} <span class="muted">灰白区域：未知；区间按真实时间显示</span>${truncatedNotice}</div>` : (truncatedNotice ? `<div class="event-chart-legend">${truncatedNotice}</div>` : '');
    const path = (array) => indices.map((i, position) => {
      if (!Number.isFinite(array[i])) return null;
      const previous = position ? indices[position - 1] : null;
      return `${previous === null || !Number.isFinite(array[previous]) ? 'M' : 'L'}${x(i).toFixed(2)} ${y(array[i]).toFixed(2)}`;
    }).filter(Boolean).join(' ');
    const grids = Array.from({length: 5}, (_, i) => min + i * (max - min) / 4).map((tick) =>
      `<line x1="${pad.left}" y1="${y(tick)}" x2="${width-pad.right}" y2="${y(tick)}" stroke="var(--line,#e1e6f0)"/>` +
      `<text x="${pad.left-8}" y="${y(tick)+4}" text-anchor="end" fill="var(--muted,#78849a)" font-size="11">${fmt(tick)}</text>`
    ).join('');
    const annotated = indices.filter((i) => Number(series.predictions[i]) === 1).slice(0, 100).map((i) =>
      `<circle cx="${x(i)}" cy="${y(series.scores[i] ?? min)}" r="3.1" fill="#e5764e"><title>预测异常 · ${escape(series.time[i])}</title></circle>`
    ).join('');
    const trueFaults = indices.filter(i => series.actual[i] === 1).slice(0, 110).map(i =>
      `<circle cx="${x(i)}" cy="${height-pad.bottom+2}" r="2.8" fill="#de9851"><title>真实故障 · ${escape(series.time[i])}</title></circle>`
    ).join('');
    const title = series.hasLimits ? '异常分数与判定阈值对比' : '异常分数（未提供判定阈值）';
    const evidenceNote = '<div class="event-chart-legend">说明：异常分数是算法输出，不等同于概率；判定阈值仅在算法提供时绘制。未标注真值的区间保持未知，不按正常样本计入监督指标。</div>';
    target.innerHTML = `<svg class="chart-svg detection-score-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${title}">
      ${grids}
      ${overlayMarkup}
      <line x1="${pad.left}" y1="${height-pad.bottom}" x2="${width-pad.right}" y2="${height-pad.bottom}" stroke="var(--line-strong,#a1acbc)" />
      <path d="${path(series.scores)}" fill="none" stroke="#507de9" stroke-width="2.3" vector-effect="non-scaling-stroke" />
      ${series.hasLimits ? `<path d="${path(series.limits)}" fill="none" stroke="#e57364" stroke-width="2" stroke-dasharray="6,4" vector-effect="non-scaling-stroke"/>` : ''}
      ${annotated}
      ${trueFaults}
      <g font-size="11" fill="var(--text,#202737)">
        <rect x="${pad.left}" y="14" width="11" height="8" rx="2" fill="#507de9"/><text x="${pad.left+16}" y="22">异常分数</text>
        ${series.hasLimits ? `<rect x="${pad.left+98}" y="14" width="11" height="8" rx="2" fill="#e57364"/><text x="${pad.left+115}" y="22">判定阈值</text>` : `<text x="${pad.left+98}" y="22">阈值不可用</text>`}
        ${series.actual.includes(1) && !small ? `<circle cx="${pad.left+206}" cy="18" r="4" fill="#de9851"/><text x="${pad.left+218}" y="22">真实故障</text>` : ''}
      </g>
      <text x="${pad.left}" y="${height-14}" font-size="11" fill="var(--muted,#78849a)" text-anchor="start">${series.coordinateMode==='frame'?'帧 ':''}${escape(series.time[0])}</text>
      <text x="${width-pad.right}" y="${height-14}" font-size="11" fill="var(--muted,#78849a)" text-anchor="end">${escape(series.time[series.n-1])}</text>
    </svg>${eventLegend}${metricLine}${evidenceNote}`;
    return true;
  }

  function evidenceSummary(result, provenance = {}) {
    const trace = result?.chart_trace || result || {};
    const evidence = trace.input_evidence || provenance.input_evidence || {};
    const schema = trace.feature_schema || provenance.feature_schema || {};
    const label = trace.label_evidence || provenance.label_evidence || {};
    return {
      algorithm: trace.algorithm || provenance.algorithm || '不可用',
      source: evidence.filename || evidence.source_name || provenance.source_name || '不可用',
      source_type: evidence.source_type || provenance.source_type || '不可用',
      source_sha256: evidence.sha256 || provenance.source_sha256 || null,
      features: Array.isArray(schema.features) ? schema.features : [],
      label_status: label.status || (result?.score_only || provenance.evaluation_mode === 'score_only' ? 'unknown' : 'unverified'),
      truth_verified: label.status === 'verified'
    };
  }

  function eventEvidence(result) {
    const trace = result?.chart_trace || result || {};
    const rows = Array.isArray(trace.rows) ? trace.rows : [];
    const metrics = trace.event_metrics || result?.event_metrics || {};
    const items = trace.event_overlay?.items || result?.events || [];
    return items.map((event, index) => ({
      id: event.event_id || event.id || String(index + 1),
      type: event.fault_type || (event.label === 1 ? '故障事件' : event.label === 0 ? '已确认正常' : '未分类事件'),
      start: event.start_timestamp ?? event.start,
      end: event.end_timestamp ?? event.end,
      label: event.label === 0 || event.label === 1 ? event.label : null,
      interval: '[start,end)',
      truth_status: event.label === 0 || event.label === 1 ? 'known' : 'unknown'
    }));
  }

  function buildReportSnapshot(result, provenance = {}) {
    const evidence = evidenceSummary(result, provenance);
    const trace = result?.chart_trace || result || {};
    const metrics = evidence.truth_verified ? (trace.metrics || result?.metrics || null) : null;
    return {
      schema_version: 1, created_at: new Date().toISOString(), evidence,
      score_semantics: 'algorithm_score_not_probability',
      threshold: trace.decision_threshold !== null && trace.decision_threshold !== undefined && Number.isFinite(Number(trace.decision_threshold)) ? Number(trace.decision_threshold) : null,
      metrics, events: eventEvidence(result),
      unknown_truth_preserved: !evidence.truth_verified,
      result_sha256: provenance.result_sha256 || result?.result_sha256 || null
    };
  }

  scope.AASResultChart = {render, extract, chooseIndices, evidenceSummary, eventEvidence, buildReportSnapshot};
})(typeof window === 'undefined' ? globalThis : window);
