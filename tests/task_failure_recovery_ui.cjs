'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeNode {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.handlers = {}; this.hidden = false;
    this.disabled = false; this.value = ''; this.href = ''; this.className = '';
    this.attributes = {}; this._text = ''; this._innerHTML = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent || '').join(''); }
  set innerHTML(value) { this._innerHTML = String(value); this.children = []; }
  get innerHTML() { return this._innerHTML; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  addEventListener(type, callback) { this.handlers[type] = callback; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  focus() { this.focused = true; }
}
function walk(node) { return [node, ...node.children.flatMap(walk)]; }

const runner = fs.readFileSync('web/visualization/runner-v2.js', 'utf8');
const runnerHtml = fs.readFileSync('web/visualization/runner.html', 'utf8');
assert.ok(runnerHtml.includes('id="trainFailurePanel"'), 'training panel must exist');
assert.ok(runnerHtml.includes('id="evalFailurePanel"'), 'evaluation panel must exist');
const renderStart = runner.indexOf('function clearTaskDiagnostic(');
const renderEnd = runner.indexOf('\nfunction showResult(', renderStart);
assert.ok(renderStart >= 0 && renderEnd > renderStart, 'runner diagnostic renderer must exist');
const renderSource = runner.slice(renderStart, renderEnd);
const nodes = new Map();
const document = {createElement: tag => new FakeNode(tag)};
const sandbox = vm.createContext({
  $: id => { if (!nodes.has(id)) nodes.set(id, new FakeNode()); return nodes.get(id); },
  document,
  setWorkspace: (role, view) => { sandbox.lastWorkspace = [role, view]; },
  window: {location: {href: ''}},
});
vm.runInContext(renderSource, sandbox);
vm.runInContext("renderTaskDiagnostic('train',{code:'algorithm_timeout',stage_label:'算法执行',title:'算法执行超时',message:'任务超时，输入 <img src=x onerror=alert(1)>',recovery_action:'缩小时间范围后重新提交',recovery_label:'返回训练设置'},'task-123')", sandbox);
const trainPanel = nodes.get('trainFailurePanel');
assert.equal(trainPanel.hidden, false);
assert.match(trainPanel.textContent, /算法执行/);
assert.match(trainPanel.textContent, /算法执行超时/);
assert.match(trainPanel.textContent, /<img src=x onerror=alert\(1\)>/,
  'diagnostic text must remain literal text, not interpreted HTML');
const trainButton = walk(trainPanel).find(node => node.tag === 'button');
assert.ok(trainButton);
trainButton.handlers.click();
assert.deepEqual(sandbox.lastWorkspace, ['train', 'data']);
assert.equal(trainPanel.hidden, true);

vm.runInContext("renderTaskDiagnostic('eval',{code:'archive_failed',stage_label:'实验报告归档',title:'计算完成但报告未归档',message:'报告保存失败',recovery_action:'检查 outputs 目录',recovery_label:'查看任务记录'},'task-456')", sandbox);
const evalPanel = nodes.get('evalFailurePanel');
assert.equal(evalPanel.hidden, false);
assert.match(evalPanel.textContent, /计算完成但报告未归档/);
walk(evalPanel).find(node => node.tag === 'button').handlers.click();
assert.equal(sandbox.window.location.href, 'reports.html',
  'archive-attention action should lead to task history instead of replaying the operation');

const apiStart = runner.indexOf('async function api(url,config)');
const apiEnd = runner.indexOf('\nconst post=', apiStart);
assert.ok(apiStart >= 0 && apiEnd > apiStart, 'runner API helper must exist');
const apiSandbox = vm.createContext({fetch: async () => ({ok:false,json:async()=>({error:'safe failure',failure:{code:'input_missing'},task_id:'task-sync-123'})}),Error});
vm.runInContext(runner.slice(apiStart, apiEnd) + '\nthis.testApi=api;', apiSandbox);
(async () => {
  let caught;
  try { await apiSandbox.testApi('/api/v2/evaluation/run'); } catch (error) { caught = error; }
  assert.ok(caught);
  assert.equal(caught.message, 'safe failure');
  assert.equal(caught.failure.code, 'input_missing', 'structured failure must survive the fetch boundary');
  assert.equal(caught.task_id, 'task-sync-123', 'synchronous failure task ID must survive the fetch boundary');

  const reports = fs.readFileSync('web/visualization/reports.js', 'utf8');
  const html = fs.readFileSync('web/visualization/reports.html', 'utf8');
  assert.ok(html.includes('id="taskHistoryList"'), 'reports page needs task history panel');
  assert.ok(html.includes('id="refreshTaskHistory"'), 'reports page needs explicit refresh');
  const start = reports.indexOf('function renderTaskHistory(items)');
  const end = reports.indexOf('\nasync function loadTaskHistory()', start);
  assert.ok(start >= 0 && end > start, 'report task-history renderer must exist');
  const host = new FakeNode();
  const reportContext = vm.createContext({
    els: {taskHistoryList: host, taskHistoryCount: new FakeNode()},
    document: {createElement: tag => new FakeNode(tag)},
    escapeHtml: value => String(value),
  });
  vm.runInContext(reports.slice(start, end) + '\nthis.renderTaskHistory=renderTaskHistory;', reportContext);
  reportContext.renderTaskHistory([
    {task_id:'task-fail',task_type:'training',status:'failed',stage_label:'模型保存与校验',
      failure:{title:'模型文件未保存',message:'目录没有写权限',recovery_action:'检查模型目录权限',recovery_label:'返回训练设置'}},
    {task_id:'task-restart',task_type:'training',status:'interrupted',stage_label:'算法执行',
      failure:{title:'服务重启导致任务中断',message:'原任务不会自动恢复',recovery_action:'重新提交新任务',recovery_label:'重新进入训练'}},
    {task_id:'task-archive',task_type:'training',status:'completed',stage_label:'实验报告归档',
      attention:{title:'计算完成但报告未归档',message:'报告待处理',recovery_action:'检查输出目录',recovery_label:'检查报告存储'}},
    {task_id:'task-score',task_type:'score_only',status:'failed',stage_label:'模型仅推理',
      failure:{title:'仅推理输入不兼容',message:'物理单位不一致',recovery_action:'返回评估设置',recovery_label:'返回仅推理设置'}},
  ]);
  assert.match(host.textContent, /模型文件未保存/);
  assert.match(host.textContent, /服务重启导致任务中断/);
  assert.match(host.textContent, /计算完成但报告未归档/);
  const links = walk(host).filter(node => node.tag === 'a');
  assert.equal(links.length, 4);
  assert.equal(links.filter(node => node.href === 'runner.html?tab=train').length, 3);
  assert.equal(links.filter(node => node.href === 'runner.html?tab=eval').length, 1);
  assert.ok(!host.innerHTML.includes('<img'), 'renderer must not inject task text as HTML');
  reportContext.renderTaskHistory([]);
  assert.match(host.textContent, /暂无失败|没有失败/);
  console.log('Task failure diagnostics, safe recovery actions, and report history UI: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });

// Exercise loading, empty and error states through the real task-history loader.
(async () => {
  const reports = fs.readFileSync('web/visualization/reports.js', 'utf8');
  const start = reports.indexOf('function renderTaskHistory(items)');
  const end = reports.indexOf('\n// 非阻断提示', start);
  assert.ok(start >= 0 && end > start);
  const host = new FakeNode();
  const count = new FakeNode();
  let apiImpl;
  const context = vm.createContext({
    els: {taskHistoryList: host, taskHistoryCount: count},
    document: {createElement: tag => new FakeNode(tag)},
    api: (...args) => apiImpl(...args),
  });
  vm.runInContext(reports.slice(start, end) + '\nthis.renderTaskHistory=renderTaskHistory;this.loadTaskHistory=loadTaskHistory;', context);

  let resolveRequest;
  apiImpl = () => new Promise(resolve => { resolveRequest = resolve; });
  const pending = context.loadTaskHistory();
  assert.match(host.textContent, /正在读取/);
  resolveRequest({items: [], total: 0});
  await pending;
  assert.match(host.textContent, /暂无失败/);
  assert.equal(count.textContent, '0 项');

  apiImpl = async () => { throw new Error('offline'); };
  await context.loadTaskHistory();
  assert.match(host.textContent, /任务历史暂时不可用：offline/);
  const retry = walk(host).find(node => node.tag === 'button');
  assert.ok(retry);
  apiImpl = async () => ({items: [], total: 0});
  await retry.handlers.click();
  assert.match(host.textContent, /暂无失败/);
  console.log('Task history loading, empty, error, and retry states: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
