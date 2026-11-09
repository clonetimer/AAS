'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync('web/visualization/result-chart.js','utf8'),context);
const target={clientWidth:800,clientHeight:320,innerHTML:''};
const result={chart_trace:{rows:[{timestamp:0,label:null},{timestamp:1,label:1},{timestamp:2,label:null}],scores:[0.1,0.9,0.2],decision_threshold:0.5,event_overlay:{status:'ok',items:[{label:1,start_timestamp:0.5,end_timestamp:1.5}]},event_metrics:{status:'ok',event_recall:1,fault_events_detected:1,fault_events_evaluable:1,median_first_alarm_delay_seconds:0.5,confirmed_normal_samples:0,confirmed_normal_false_alarms:0}}};
assert.equal(context.AASResultChart.render(target,result),true);
assert.match(target.innerHTML,/异常分数是算法输出，不等同于概率/);
assert.match(target.innerHTML,/判定阈值/);
assert.match(target.innerHTML,/未标注真值的区间保持未知/);
assert.match(target.innerHTML,/故障事件/);
assert.match(target.innerHTML,/事件级召回率/);
console.log('G1 chart render preserves score, threshold, event and unknown-truth evidence: PASS');
const noThreshold = context.AASResultChart.buildReportSnapshot({
  chart_trace: { rows: [], scores: [], decision_threshold: null,
    label_evidence: { status: 'unknown' }, event_overlay: { items: [] } }
});
assert.equal(noThreshold.threshold, null, 'null threshold must not be coerced to numeric zero');
const reportSource = fs.readFileSync('web/visualization/reports.js', 'utf8');
assert.match(reportSource, /safe\(e\.id\).*safe\(e\.type\).*safe\(e\.start\).*safe\(e\.end\)/s,
  'archived event fields must be HTML escaped before rendering');
console.log('G1 report threshold-null and event HTML-escaping guards: PASS');
