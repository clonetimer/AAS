const fs=require('fs'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../web/visualization/reports.html'),'utf8');
const js=fs.readFileSync(path.join(__dirname,'../web/visualization/reports.js'),'utf8');
function check(ok,msg){if(!ok)throw new Error(msg);}
check(html.includes('id="exportComparisonG1Btn"'),'comparison export control missing');
check(js.includes("api(`/api/compare-experiments?left="),'must use authoritative server comparison');
check(js.includes("report_type: 'experiment_comparison'"),'comparison report type missing');
check(js.includes("authority: 'server_compare_experiments'"),'server authority provenance missing');
check(js.includes('comparison,')&&js.includes('JSON.stringify(payload, null, 2)'),'comparison result must be exported as JSON');
check(js.includes('state.lastComparison = null'),'failed comparison must invalidate old export');
check(js.includes("notify('请先完成一次实验可比性检查'"),'must prevent export before successful comparison');
console.log('G1 comparison JSON export contract: PASS');
