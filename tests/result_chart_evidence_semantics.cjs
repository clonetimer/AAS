'use strict';
const fs=require('node:fs');
const src=fs.readFileSync('web/visualization/result-chart.js','utf8');
function check(ok,msg){if(!ok)throw new Error(msg);}
check(src.includes('异常分数是算法输出，不等同于概率'),'score must not be presented as probability');
check(src.includes('判定阈值仅在算法提供时绘制'),'threshold availability must be explicit');
check(src.includes('未标注真值的区间保持未知'),'unknown truth must remain unknown');
check(src.includes('trace.decision_thresholds'),'dynamic threshold must come from algorithm evidence');
check(src.includes('trace.decision_threshold)'),'fixed threshold must come from algorithm evidence');
check(src.includes('trace.event_overlay'),'event overlay must be sourced from result evidence');
console.log('G1 result evidence semantics: PASS');
