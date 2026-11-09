const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(require('path').join(__dirname, '../web/visualization/result-chart.js'), 'utf8');
const context = { console, Date, JSON, Number, Array };
vm.createContext(context);
vm.runInContext(source, context);
const card = context.buildExperimentEvidenceCard({
  algorithm: 'cusum', score_only: true,
  input_evidence: { filename: 'field.csv', sha256: 'abc', source_type: 'user_csv' },
  feature_schema: { features: ['gyro_x'] },
  label_evidence: { status: 'unknown', coverage: 0 }
});
if (card.truth_is_supervised !== false || card.label_status !== 'unknown') throw new Error('unknown truth must remain unknown');
const events = context.buildEventEvidenceRows(
  [{id:'e1',fault_type:'bias',start:2,end:4}],
  [0,0,0,1,1],
  [null,null,null,null,null],
  [0,1,2,3,4]
);
if (events[0].status !== 'unknown_truth' || events[0].detection_delay !== 1) throw new Error('event timeline projection incorrect');
const a = {algorithm:'cusum',source_sha256:'abc',source_type:'user_csv',label_status:'verified',features:['x']};
const b = {...a,source_sha256:'def'};
const comparison = context.classifyExperimentEvidence(a,b);
if (comparison.comparable || !comparison.reasons.includes('source_sha256')) throw new Error('non-comparable sources must be explained');
const report = context.buildG1ReportSnapshot({score_only:true,label_evidence:{status:'unknown'},time:[0,1],predicted_labels:[0,1],true_labels:[null,null]});
if (report.metrics !== null || report.unknown_truth_preserved !== true || report.score_semantics !== 'algorithm_score_not_probability') throw new Error('report evidence contract incorrect');
console.log('G1 evidence/event/comparability/report behavior: PASS');
