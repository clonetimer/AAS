/* Chart-only timestamp and event helpers. Mirrors the backend's numeric/local/UTC
 * timestamp classes; never assumes that local wall-clock timestamps are UTC instants.
 * No sampling label is rewritten: events are immutable overlays until Save is clicked.
 */
(function (root) {
  'use strict';
  function parseTime(value) {
    if (value === null || value === undefined || String(value).trim() === '') return null;
    const text = String(value).trim();
    const number = Number(text);
    if (Number.isFinite(number)) return { kind: 'numeric', seconds: number };
    const iso = text.replace(' ', 'T');
    const local = /^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d{1,6})?)?$/.test(iso);
    const utc = /^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d{1,6})?)?(?:Z|[+-]\d\d:\d\d)$/i.test(iso);
    if (!local && !utc) return null;
    // Treat a local timestamp as a naive wall clock, not browser-local timezone.
    const date = new Date(local ? iso + 'Z' : iso);
    if (!Number.isFinite(date.getTime())) return null;
    // Date stores milliseconds. Restore the remaining 1–3 fractional digits so
    // microsecond timestamps retain the same precision as the Python backend.
    const fraction = iso.match(/\.(\d{4,6})(?=Z|[+-]\d\d:\d\d|$)/i);
    const micros = fraction ? Number(fraction[1].padEnd(6, '0')) : 0;
    return { kind: local ? 'local' : 'utc', seconds: date.getTime() / 1000 + (micros % 1000) / 1e6 };
  }
  function axisFor(values) {
    const entries = values.map(parseTime).filter(Boolean);
    if (!entries.length || entries.length !== values.length ||
        entries.some(item => item.kind !== entries[0].kind)) return null;
    const seconds = entries.map(item => item.seconds);
    const min = Math.min(...seconds), max = Math.max(...seconds);
    if (!Number.isFinite(min) || !Number.isFinite(max) || min >= max) return null;
    return { kind: entries[0].kind, min, max };
  }
  function toTimestamp(seconds, kind) {
    if (kind === 'numeric') return String(Number(seconds.toPrecision(15)));
    const wholeMicroseconds = Math.round(seconds * 1e6);
    const wholeMillis = Math.floor(wholeMicroseconds / 1000);
    const remainder = ((wholeMicroseconds % 1_000_000) + 1_000_000) % 1_000_000;
    const iso = new Date(wholeMillis).toISOString().split('.')[0]
      + `.${String(remainder).padStart(6, '0')}Z`;
    return kind === 'local' ? iso.replace('Z', '').replace('T', ' ') : iso;
  }
  function geometryFor(axis, zoom, padding, width) {
    const scale = Math.max(1, zoom.scale || 1), offset = zoom.offsetX || 0;
    const plotWidth = width - padding.left - padding.right;
    const timeX = seconds => padding.left +
      ((seconds - axis.min) / (axis.max - axis.min) - offset) * scale * plotWidth;
    const atX = x => axis.min + (offset + (x - padding.left) / (plotWidth * scale)) * (axis.max - axis.min);
    return { timeX, atX, plotWidth };
  }
  function bands(events, axis, zoom, padding, width) {
    if (!axis) return [];
    const { timeX } = geometryFor(axis, zoom, padding, width);
    const left = padding.left, right = width - padding.right;
    return (events || []).flatMap(event => {
      const a = parseTime(event.start_timestamp), b = parseTime(event.end_timestamp);
      if (!a || !b || a.kind !== axis.kind || b.kind !== axis.kind || a.seconds >= b.seconds) return [];
      const x1 = timeX(a.seconds), x2 = timeX(b.seconds);
      if (x2 <= left || x1 >= right) return [];
      return [{ id: Number(event.id), label: Number(event.label),
        left: Math.max(left, x1), right: Math.min(right, x2),
        startX: x1, endX: x2, start: a.seconds, end: b.seconds }];
    });
  }
  const api = { parseTime, axisFor, toTimestamp, geometryFor, bands };
  root.AASFaultChart = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
