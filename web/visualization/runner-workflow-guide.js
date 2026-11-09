(function () {
  'use strict';
  var guide = document.querySelector('.runner-workflow-guide');
  if (!guide) return;
  var hint = document.getElementById('runnerWorkflowHint');
  var buttons = Array.prototype.slice.call(guide.querySelectorAll('[data-runner-step]'));
  function activateMode(id) {
    var button = document.getElementById(id);
    if (button) button.click();
  }
  function focusTarget(target, message, mode) {
    if (mode) activateMode(mode);
    var node = document.getElementById(target);
    if (!node) return;
    if (node.tagName === 'DETAILS') node.open = true;
    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (node.focus) node.focus({ preventScroll: true });
    hint.textContent = message;
  }
  var actions = {
    data: function () { focusTarget('trainDataSource', '选择训练数据来源', 'tabTrain'); },
    check: function () { focusTarget('previewTrain', '检查训练数据后再继续'); },
    train: function () { focusTarget('trainAlgorithm', '确认算法和参数，再由你决定是否开始训练'); },
    test: function () { focusTarget('evalDataSource', '选择测试数据来源', 'tabEval'); },
    evaluate: function () { focusTarget('evalModel', '选择已发布模型并检查真值；无标签来源只进行推理'); },
    result: function () {
      var evalView = document.getElementById('evalView');
      var isEval = !!(evalView && !evalView.hidden && getComputedStyle(evalView).display !== 'none');
      var tab = document.getElementById(isEval ? 'evalResultTab' : 'trainResultTab');
      if (tab) tab.click();
      var target = document.getElementById(isEval ? 'evalResultPanel' : 'trainResultPanel');
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      hint.textContent = '查看最近一次训练或评估结果；可从任务查询与导出查看历史记录';
    }
  };
  buttons.forEach(function (button) {
    button.addEventListener('click', function () {
      var action = actions[button.dataset.runnerStep];
      if (action) action();
      buttons.forEach(function (item) {
        var active = item === button;
        item.classList.toggle('active', active);
        item.setAttribute('aria-current', active ? 'step' : 'false');
      });
    });
  });
})();
