'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync('web/visualization/reports.js', 'utf8');
const html = fs.readFileSync('web/visualization/reports.html', 'utf8');
assert.ok(html.indexOf('task-client.js?v=p3d6') < html.indexOf('reports.js?'),
  'reports page must load the shared task client before reports.js');
const start = src.indexOf('function taskStatusLabel(');
const end = src.indexOf('// 非阻断提示', start);
assert.ok(start >= 0 && end > start, 'task history renderer and loader must be present');

class Element {
  constructor(tagName='div') {
    this.tagName = tagName.toUpperCase(); this.children = []; this.listeners = {};
    this.textContent = ''; this.className = ''; this.disabled = false; this.value = 0;
    this.href = ''; this.title = ''; this.attributes = {};
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
}
function walk(node) { return [node, ...(node.children || []).flatMap(walk)]; }

const items = [
  {task_id:'task-train',task_type:'training',status:'running',progress:42,cancellable:true,
   stage_label:'算法执行',message:'训练正在运行'},
  {task_id:'task-eval',task_type:'evaluation',status:'cancelling',progress:63,cancellable:false,
   stage_label:'模型评估',message:'正在安全停止'},
  {task_id:'task-score',task_type:'score_only',status:'completed',progress:100,message:'仅推理完成'},
  {task_id:'task-batch',task_type:'batch_import',status:'failed',progress:50,
   failure:{title:'批量导入失败',message:'第 2 个文件失败',recovery_action:'检查失败文件'}},
  {task_id:'task-csv',task_type:'csv_import',status:'cancelled',progress:20,message:'CSV 导入已取消'},
  {task_id:'task-pre',task_type:'preprocess',status:'interrupted',progress:15,message:'服务重启后中断'},
  {task_id:'task-attention',task_type:'evaluation',status:'completed',progress:100,
   attention:{title:'报告待处理',message:'计算完成，报告归档失败'}},
];
const host = new Element('div');
const count = new Element('span');
const calls = {history:[],cancel:[]};
const timers = [];
const context = vm.createContext({
  els:{taskHistoryList:host,taskHistoryCount:count},
  state:{taskHistoryTimer:null},
  document:{createElement:tag=>new Element(tag)},
  window:{
    AASTasks:{
      history:async limit=>{calls.history.push(limit);return {items,total:items.length};},
      cancel:async id=>{calls.cancel.push(id);return {accepted:true,status:'cancelling'};},
    },
    setTimeout:callback=>{timers.push(callback);return timers.length;},
    clearTimeout:()=>{},
  },
  api:async path=>{calls.fallback=path;return {items,total:items.length};},
  Error, String, Number, Math, Promise,
});
vm.runInContext(src.slice(start,end), context);

(async()=>{
  await context.loadTaskHistory();
  assert.deepEqual(calls.history,[50], 'history must request the all-task page through the shared client');
  assert.equal(count.textContent, `${items.length} 项`);
  const nodes=walk(host);
  const cards=nodes.filter(node=>node.className==='task-history-card');
  assert.equal(cards.length,items.length);
  const links=nodes.filter(node=>node.tagName==='A');
  assert.ok(links.some(link=>link.href==='runner.html?tab=train'));
  assert.ok(links.some(link=>link.href==='runner.html?tab=eval'));
  assert.ok(links.some(link=>link.href==='import.html#v2BatchPanel'));
  assert.ok(links.some(link=>link.href==='runner.html'));
  assert.ok(links.some(link=>link.href==='browser.html'));
  const buttons=nodes.filter(node=>node.tagName==='BUTTON');
  assert.equal(buttons.filter(button=>button.textContent==='取消任务').length,1,
    'only cancellable running tasks expose a cancel action');
  assert.equal(buttons.filter(button=>/重试/.test(button.textContent)).length,0,
    'history never automatically or implicitly retries a task');
  const cancelButton=buttons.find(button=>button.textContent==='取消任务');
  await cancelButton.listeners.click();
  assert.deepEqual(calls.cancel,['task-train']);
  assert.equal(cancelButton.disabled,true);
  assert.ok(timers.length>=1,'active tasks should refresh automatically');
  const allText=nodes.map(node=>node.textContent).join(' ');
  for(const label of ['运行中','正在取消','已完成','执行失败','已取消','已中断','报告待处理']) {
    assert.ok(allText.includes(label),`missing status label: ${label}`);
  }
  console.log('Cross-page task history, workflow navigation, progress and confirmed cancellation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
