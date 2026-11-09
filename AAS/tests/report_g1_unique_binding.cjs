const fs=require('fs'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../web/visualization/reports.html'),'utf8');
const js=fs.readFileSync(path.join(__dirname,'../web/visualization/reports.js'),'utf8');
function count(text,pattern){return (text.match(pattern)||[]).length;}
if(count(html,/id="exportComparisonG1Btn"/g)!==1)throw new Error('comparison export button ID must be unique');
if(count(html,/id="g1EvidenceCard"/g)!==1)throw new Error('evidence card ID must be unique');
if(count(html,/id="g1EventEvidence"/g)!==1)throw new Error('event evidence ID must be unique');
if(count(js,/exportComparisonBtn: document\.getElementById\('exportComparisonG1Btn'\)/g)!==1)throw new Error('comparison export DOM binding must be unique');
if(count(js,/function exportG1ComparisonJSON\(/g)!==1)throw new Error('comparison export handler must be unique');
if(count(js,/lastComparison: null/g)!==1)throw new Error('lastComparison state must be unique');
console.log('G1 report unique DOM IDs and handlers: PASS');
