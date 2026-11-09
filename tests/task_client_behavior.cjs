'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync('web/visualization/task-client.js', 'utf8');

function response(payload, ok=true, status=200) {
  return {ok, status, json: async () => payload};
}

async function run() {
  const statuses = [
    {task_id:'task-1',status:'running',progress:12,message:'上传中'},
    {task_id:'task-1',status:'cancelling',progress:55,message:'正在安全停止'},
    {task_id:'task-1',status:'completed',progress:100,result:{row_count:100}},
  ];
  const calls = [];
  const updates = [];
  const context = vm.createContext({
    window: {confirm: () => true},
    fetch: async (url, options={}) => {
      calls.push({url,options});
      return response(statuses.shift());
    },
    setTimeout: callback => { callback(); return 1; },
    clearTimeout: () => {},
    Date, Promise, Error, Math, Number, String, Object, Set, encodeURIComponent,
  });
  vm.runInContext(src, context);
  const terminal = await context.window.AASTasks.wait('task-1', {
    intervalMs: 1, maxWaitMs: 100, onUpdate: item => updates.push(item.status),
  });
  assert.equal(terminal.status, 'completed');
  assert.deepEqual(updates, ['running','cancelling','completed']);
  assert.equal(calls.length, 3);
  assert.ok(calls.every(call => call.url.startsWith('/api/task-status?task_id=task-1')));

  let confirmResult = false;
  let cancelCalls = 0;
  let cancelBody = null;
  const cancelContext = vm.createContext({
    window: {confirm: () => confirmResult},
    fetch: async (_url, options) => { cancelCalls++; cancelBody = options.body; return response({accepted:true,status:'cancelling'}); },
    setTimeout: callback => { callback(); return 1; }, Date, Promise, Error, Math, Number, String, Object, Set, encodeURIComponent,
  });
  vm.runInContext(src, cancelContext);
  const declined = await cancelContext.window.AASTasks.cancel('task-2');
  assert.equal(declined.cancelled_by_user, true);
  assert.equal(cancelCalls, 0, 'cancel must not be sent when user declines confirmation');
  confirmResult = true;
  const accepted = await cancelContext.window.AASTasks.cancel('task-2');
  assert.equal(accepted.accepted, true);
  assert.equal(cancelCalls, 1);
  assert.deepEqual(JSON.parse(cancelBody), {task_id:'task-2',confirm:true});

  const historyContext = vm.createContext({
    window: {confirm: () => true},
    fetch: async url => { historyContext.lastUrl = url; return response({items:[],total:0}); },
    setTimeout: callback => { callback(); return 1; }, Date, Promise, Error, Math, Number, String, Object, Set, encodeURIComponent,
  });
  vm.runInContext(src, historyContext);
  await historyContext.window.AASTasks.history(25);
  assert.equal(historyContext.lastUrl, '/api/task-history?limit=25&include_all=1');

  let clock = 0;
  let statusCalls = 0;
  let cancelRequests = 0;
  const timeoutContext = vm.createContext({
    window: {confirm: () => true},
    fetch: async url => {
      if (url.startsWith('/api/task-cancel')) cancelRequests++;
      statusCalls++;
      return response({task_id:'task-running',status:'running',progress:20});
    },
    setTimeout: callback => { clock += 5; callback(); return 1; },
    Date: {now: () => clock}, Promise, Error, Math, Number, String, Object, Set, encodeURIComponent,
  });
  vm.runInContext(src, timeoutContext);
  await assert.rejects(
    timeoutContext.window.AASTasks.wait('task-running',{intervalMs:5,maxWaitMs:10}),
    /仍在后台运行/);
  assert.ok(statusCalls >= 2);
  assert.equal(cancelRequests, 0, 'browser timeout must never cancel or replay the server task');

  console.log('Shared task client polling, cancellation confirmation, history and timeout: PASS');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
