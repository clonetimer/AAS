/* Shared presentation for CSV import. Business adapters own every HTTP request. */
(() => {
  'use strict';
  const steps = ['file', 'fields', 'review', 'done'];
  const titles = ['检查文件', '核对字段与用途', '确认导入', '使用结果'];
  const purposes = {
    history: {name: '历史故障数据库', destination: '历史故障数据库中的新来源',
      why: '将文件登记为可筛选的遥测参量，之后可用于浏览、训练或测试。'},
    simulation: {name: '模拟故障数据库', destination: '独立模拟库 · 原始版本 v0',
      why: '先保存完整原始数据，再选择参量和帧区间添加故障。'},
    train: {name: '训练数据', destination: '我的训练 CSV',
      why: '为异常检测算法准备正常基线；标签用于确认样本状态。'},
    eval: {name: '测试数据', destination: '我的测试 CSV',
      why: '使用已有模型检查数据；真实标签决定能否计算评估指标。'}
  };
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function focusTarget(node) {
    if (!node) return;
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    node.focus({preventScroll: true});
    node.scrollIntoView?.({block: 'nearest', behavior: 'auto'});
  }
  function create(options) {
    const root = document.getElementById(options.rootId);
    if (!root) return null;
    const purpose = purposes[options.purpose];
    if (!purpose) throw Error('未知的导入用途');
    const panels = Object.fromEntries(steps.map(step => [step, root.querySelector(`[data-csv-stage="${step}"]`)]));
    if (steps.some(step => !panels[step])) throw Error('CSV 导入步骤不完整');
    root.classList.add('csv-wizard');
    const state = {stage: 'file', checked: false, approved: false, imported: false, busy: false, summary: {}};
    const header = element('header', undefined, 'csv-wizard-header');
    const purposeLine = element('p', `${purpose.name} · CSV 导入`, 'csv-wizard-purpose');
    const nav = element('nav');
    nav.setAttribute('aria-label', purpose.name + '导入步骤');
    const list = element('ol', undefined, 'csv-wizard-steps');
    const buttons = {};
    for (const [index, step] of steps.entries()) {
      const item = element('li');
      const button = element('button', `${index + 1} · ${titles[index]}`);
      button.type = 'button'; button.dataset.csvGo = step;
      button.setAttribute('aria-controls', options.rootId + '-' + step);
      button.addEventListener('click', () => go(step));
      item.append(button); list.append(item); buttons[step] = button;
      const panel = panels[step];
      panel.id = options.rootId + '-' + step;
      if (!panel.querySelector('legend')) panel.prepend(element('legend', titles[index], 'csv-wizard-legend'));
    }
    nav.append(list);
    const title = element('h3'); title.id = options.rootId + 'StepTitle'; title.tabIndex = -1;
    const instruction = element('p', undefined, 'csv-wizard-instruction');
    const destination = element('p', undefined, 'csv-wizard-destination');
    const notice = element('p', '', 'csv-wizard-notice');
    notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
    notice.setAttribute('aria-atomic', 'true'); notice.hidden = true;
    header.append(purposeLine, nav, title, instruction, destination, notice);
    root.prepend(header);
    const reviewSummary = element('div', undefined, 'csv-wizard-summary');
    reviewSummary.dataset.csvSummary = 'review'; panels.review.append(reviewSummary);
    // Keep the existing commit control after the receipt, so review precedes writing.
    const commitControls = [...panels.review.children].filter(node => node !== reviewSummary && node.tagName !== 'LEGEND');
    panels.review.append(...commitControls);
    const doneTitle = element('h4', '导入完成');
    const doneSummary = element('div', undefined, 'csv-wizard-summary'); doneSummary.dataset.csvSummary = 'done';
    const next = element('button', options.nextLabel || '继续使用导入数据', 'primary');
    next.type = 'button'; next.dataset.csvNextAction = '';
    next.addEventListener('click', () => {
      if (!state.imported || state.busy) return;
      if (options.onNext) options.onNext();
      if (options.nextTarget) focusTarget(document.getElementById(options.nextTarget));
    });
    const again = element('button', '导入另一个文件'); again.type = 'button'; again.dataset.csvStartAgain = '';
    again.addEventListener('click', () => { if (!state.busy) reset('请选择新文件并重新检查。'); });
    const doneActions = element('div', undefined, 'csv-wizard-actions'); doneActions.append(next, again);
    panels.done.append(doneTitle, doneSummary, doneActions);
    const footer = element('div', undefined, 'csv-wizard-actions');
    const back = element('button', '上一步'); back.type = 'button'; back.dataset.csvBack = '';
    back.addEventListener('click', () => go(steps[steps.indexOf(state.stage) - 1]));
    const forward = element('button', '下一步：核对字段与用途'); forward.type = 'button'; forward.dataset.csvContinue = '';
    forward.addEventListener('click', () => go('fields'));
    footer.append(back, forward); root.append(footer);

    function allowed(step) {
      return step === 'file' || (step === 'done' ? state.imported : !state.imported &&
        (step === 'fields' ? state.checked : step === 'review' && state.approved));
    }
    function labelMeaning(summary) {
      if (summary.label_status === 'confirmed_normal') return '已由用户确认全部正常（标签 0）';
      if (summary.label_status === 'unknown') return options.purpose === 'simulation'
        ? '保留未知标签；后续故障事件单独记录'
        : '真值未知；导入后仅推理，展示异常分数与报警';
      if (summary.label_status) return '保留源文件真实标签；标签作用范围按来源声明';
      return '以完整预检结果和标签确认为准';
    }
    function summaryInto(host) {
      host.replaceChildren();
      const s = state.summary, dl = element('dl');
      const count = value => Number.isFinite(value) ? String(value) : '尚未提供，等待完整校验';
      const rows = [
        ['源文件', s.filename || '待选择'],
        ['数据去向', destinationText()],
        ['帧数', Number.isFinite(s.row_count) ? `${s.row_count} 帧` : count(s.row_count)],
        ['数值参量', Number.isFinite(s.column_count) ? `${s.column_count} 个` : count(s.column_count)],
        ['标签含义', labelMeaning(s)]
      ];
      if (s.csv_encoding) rows.push(['文件格式', `${s.csv_encoding} / ${s.csv_delimiter === '\t' ? 'TAB' : s.csv_delimiter === ';' ? '分号' : s.csv_delimiter === ',' ? '逗号' : '已确认'}`]);
      if (s.time_start !== undefined && s.time_end !== undefined) rows.push(['时间覆盖', `${s.time_start} ～ ${s.time_end}`]);
      if (s.columns?.length) rows.push(['参量列', s.columns.join('、')]);
      if (s.source_id !== undefined) rows.push(['已保存来源', String(s.source_id)]);
      for (const [name, value] of rows) dl.append(element('dt', name), element('dd', value));
      host.append(dl);
      if (s.column_notice) host.append(element('p', s.column_notice, 'csv-wizard-instruction'));
      if (s.followup_error) host.append(element('p', `数据已保存。${s.followup_error}`, 'csv-wizard-followup'));
    }
    function destinationText() {
      if (options.purpose !== 'history') return purpose.destination;
      const role = state.summary.role;
      return purpose.destination + (role === 'train' ? ' · 训练用途' : role === 'test' ? ' · 测试用途' : ' · 下一步选择训练或测试用途');
    }
    function render() {
      root.setAttribute('aria-busy', String(state.busy));
      for (const step of steps) {
        const active = state.stage === step;
        panels[step].hidden = !active;
        panels[step].disabled = state.busy || !active;
        buttons[step].disabled = state.busy || !allowed(step);
        if (active) buttons[step].setAttribute('aria-current', 'step');
        else buttons[step].removeAttribute('aria-current');
      }
      title.textContent = `${steps.indexOf(state.stage) + 1} · ${titles[steps.indexOf(state.stage)]}`;
      const text = {
        file: '选择文件并检查列名，核对编码与分隔符。正确读取文字和数值后，再继续下一步。',
        fields: options.purpose === 'simulation'
          ? '核对样例、可注入的数值参量及质量提示。完整文件会按原帧顺序保存。'
          : '核对数值参量、单位、运行时间基准和标签。完成后点击“预检导入”，检查数据能否按当前用途使用。',
        review: options.purpose === 'simulation'
          ? '核对检查结果和保存位置。点击“确认导入模拟库”后，系统完整校验并保存原始版本。'
          : '预检通过。核对下方摘要，点击确认导入后写入新来源。',
        done: purpose.why + ' 使用下方按钮继续；算法运行和故障注入由你另行启动。'
      };
      instruction.textContent = text[state.stage];
      destination.textContent = '数据去向：' + destinationText();
      forward.hidden = state.stage !== 'file'; forward.disabled = state.busy || !state.checked;
      back.hidden = state.stage === 'file' || state.stage === 'done'; back.disabled = state.busy;
      next.disabled = state.busy || !state.imported; again.disabled = state.busy;
      summaryInto(reviewSummary); summaryInto(doneSummary);
    }
    function message(text, isError = false) {
      notice.textContent = text || ''; notice.hidden = !text;
      notice.setAttribute('role', isError ? 'alert' : 'status');
      notice.setAttribute('aria-live', isError ? 'assertive' : 'polite');
      notice.classList.toggle('error', isError);
    }
    function move(step, focus = true) {
      state.stage = step; render();
      if (focus) focusTarget(title);
    }
    function go(step) {
      if (!state.busy && steps.includes(step) && allowed(step)) { message(''); move(step); }
    }
    function reset(text) {
      state.checked = false; state.approved = false; state.imported = false; state.summary = {};
      message(text || ''); move('file', false);
    }
    function fileReady(ready, details = {}) {
      state.checked = Boolean(ready); state.summary = {...state.summary, ...details};
      if (!ready) { state.approved = false; state.imported = false; state.stage = 'file'; }
      message(ready ? '文件格式已确认，可以核对字段与用途。' : '请先检查文件并确认识别的格式。'); render();
    }
    function invalidate() {
      state.approved = false; state.imported = false;
      if (['review', 'done'].includes(state.stage)) state.stage = state.checked ? 'fields' : 'file';
      message('设置已调整，请重新预检后再确认导入。'); render();
    }
    function review(summary) {
      if (!state.checked) return;
      state.approved = true; state.summary = {...state.summary, ...summary};
      message(''); move('review');
    }
    function complete(summary) {
      state.approved = false; state.imported = true;
      state.summary = {...state.summary, ...summary}; message('导入完成，可以继续下一步。'); move('done');
    }
    render();
    return Object.freeze({reset, fileReady, invalidate, review, complete, go,
      setBusy(value) { state.busy = Boolean(value); render(); },
      error(text) { message(text, true); }});
  }
  window.AASCsvImportWizard = Object.freeze({create});
})();
