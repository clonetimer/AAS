(function () {
  'use strict';

  const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
  const DEFAULT_INTERVAL_MS = 750;
  const DEFAULT_TIMEOUT_MS = 2 * 60 * 60 * 1000;
  const MAX_CONSECUTIVE_ERRORS = 8;

  function taskIdValue(taskId) {
    if (typeof taskId !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(taskId)) {
      throw new Error('任务编号无效');
    }
    return taskId;
  }

  async function requestJson(url, options) {
    let response;
    try {
      response = await fetch(url, options);
    } catch (error) {
      throw new Error('任务服务暂时不可访问：' + (error && error.message ? error.message : '网络错误'));
    }
    let payload;
    try {
      payload = await response.json();
    } catch (_) {
      throw new Error('任务服务返回了无法识别的响应');
    }
    if (!response.ok) {
      throw new Error((payload && (payload.error || payload.reason || payload.message)) ||
        ('任务请求失败（HTTP ' + response.status + '）'));
    }
    return payload;
  }

  function status(taskId) {
    const id = taskIdValue(taskId);
    return requestJson('/api/task-status?task_id=' + encodeURIComponent(id), {
      method: 'GET', cache: 'no-store', headers: {'Accept': 'application/json'},
    });
  }

  async function cancel(taskId, options) {
    const id = taskIdValue(taskId);
    const settings = options || {};
    const confirmFn = settings.confirmFn || (typeof window !== 'undefined' && window.confirm);
    if (typeof confirmFn !== 'function' || !confirmFn(
      '确定取消任务 ' + id + ' 吗？已完成的数据库事务或模型归档不会被回滚。')) {
      return {accepted: false, cancelled_by_user: true};
    }
    return requestJson('/api/task-cancel', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: JSON.stringify({task_id: id, confirm: true}),
    });
  }

  function pause(milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
  }

  async function wait(taskId, options) {
    const id = taskIdValue(taskId);
    const settings = options || {};
    const intervalMs = Math.max(250, Math.min(5000, Number(settings.intervalMs) || DEFAULT_INTERVAL_MS));
    const timeoutMs = Math.max(1000, Math.min(24 * 60 * 60 * 1000,
      Number(settings.maxWaitMs) || DEFAULT_TIMEOUT_MS));
    const started = Date.now();
    let consecutiveErrors = 0;
    let lastError = null;

    while (Date.now() - started < timeoutMs) {
      let task = null;
      try {
        task = await status(id);
        consecutiveErrors = 0;
        lastError = null;
      } catch (error) {
        consecutiveErrors += 1;
        lastError = error;
        if (typeof settings.onError === 'function') settings.onError(error, consecutiveErrors);
        if (consecutiveErrors > MAX_CONSECUTIVE_ERRORS) throw error;
      }
      if (task) {
        if (typeof settings.onUpdate === 'function') settings.onUpdate(task);
        if (TERMINAL.has(task.status)) return task;
      }
      await pause(intervalMs);
    }
    const suffix = lastError && lastError.message ? '；最近一次查询错误：' + lastError.message : '';
    throw new Error('任务仍在后台运行，可在任务历史中继续查看；本页等待超时不会取消或重新提交任务' + suffix);
  }

  function history(limit) {
    const bounded = Math.max(1, Math.min(100, Number(limit) || 50));
    return requestJson('/api/task-history?limit=' + bounded + '&include_all=1', {
      method: 'GET', cache: 'no-store', headers: {'Accept': 'application/json'},
    });
  }

  if (typeof window !== 'undefined') {
    window.AASTasks = Object.freeze({status, cancel, wait, history, isTerminal: statusValue => TERMINAL.has(statusValue)});
  }
})();
