const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '../web/visualization/simulation.html'), 'utf8');
const js = fs.readFileSync(path.join(__dirname, '../web/visualization/simulation-csv-workspace.js'), 'utf8');
const backend = fs.readFileSync(path.join(__dirname, '../scripts/server/v2_csv_workspace.py'), 'utf8');
function check(ok, message) { if (!ok) throw new Error(message); }
for (const id of ['csvMagnitudeLabel', 'csvFaultHelp']) check(html.includes(`id="${id}"`), `${id} missing`);
for (const kind of ['bias', 'drift', 'stuck', 'spike']) check(js.includes(`${kind}:{label:`), `${kind} guidance missing`);
check(js.includes('每隔 8 帧产生一次尖峰'), 'spike periodicity must match backend');
check(backend.includes("elif (frame - start) % 8 == 0:"), 'backend spike implementation changed; review guidance');
check(js.includes("on('csvFaultType','change',()=>{updateCsvFaultGuidance();invalidate();})"), 'fault type change must refresh help');
console.log('CSV fault parameter explanations match implemented fault equations: PASS');
