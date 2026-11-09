const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '../web/visualization/runner.html'), 'utf8');
function check(condition, message) { if (!condition) throw new Error(message); }
function section(label) {
  const marker = `aria-label="${label}"`;
  const start = html.indexOf(marker);
  check(start >= 0, `${label}: source section exists`);
  const end = html.indexOf('</section>', start);
  check(end > start, `${label}: source section closes`);
  return html.slice(start, end);
}
const train = section('训练数据来源');
const test = section('测试数据来源');
for (const [name, source, role] of [
  ['train', train, 'Train'],
  ['test', test, 'Eval']
]) {
  check(source.includes(`id="open${role}CsvDialog"`), `${name}: CSV import action exists`);
  check(html.includes(`id="${name === 'train' ? 'train' : 'eval'}CsvDialog"`), `${name}: import dialog exists`);
  check(!source.includes('为什么'), `${name}: implementation explanation removed`);
  check(/<select id="(?:train|eval)DataSource">/.test(source), `${name}: source selector exists`);
  check(/button id="(?:train|eval)UserCsvDelete"/.test(source), `${name}: explicit delete control exists`);
}
check(html.includes('id="trainCsvFormatConfirmed"'), 'train explicit format confirmation exists');
check(html.includes('id="evalCsvFormatConfirmed"'), 'test explicit format confirmation exists');
console.log('CSV import dialogs and concise source controls: PASS');
