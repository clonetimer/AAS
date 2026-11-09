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
for (const [name, source, phrases] of [
  ['train', train, ['独立工作区', '不写入历史数据库', '只有主动删除', '正在被任务使用']],
  ['test', test, ['独立工作区', '不写入历史数据库', '未知真值', '只有主动删除']]
]) {
  check(source.includes('v2-user-csv-help'), `${name}: first-use explanation exists`);
  for (const phrase of phrases) check(source.includes(phrase), `${name}: missing ${phrase}`);
  check(/<select id="(?:train|eval)DataSource">/.test(source), `${name}: source selector exists`);
  check(/button id="(?:train|eval)UserCsvDelete"/.test(source), `${name}: explicit delete control exists`);
}
check(html.includes('id="trainCsvFormatConfirmed"'), 'train explicit format confirmation exists');
check(html.includes('id="evalCsvFormatConfirmed"'), 'test explicit format confirmation exists');
console.log('First-use CSV destination guidance and explicit lifecycle controls: PASS');
