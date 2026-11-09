/* Keep explanatory copy next to its task as a small, accessible help action. */
(() => {
  'use strict';
  const candidates = document.querySelectorAll(
    'p.field-hint:not([id]), p.sim-note:not([id]), p.v2-quality-note:not([id]), p.monitor-note:not([id]), .csv-wizard-instruction, .pg-dev-help, [data-context-help]');
  if (!candidates.length) return;
  const dialog = document.createElement('dialog');
  dialog.className = 'aas-help-dialog';
  dialog.setAttribute('aria-labelledby', 'aasHelpTitle');
  const head = document.createElement('div');
  head.className = 'aas-help-head';
  const title = document.createElement('h2');
  title.id = 'aasHelpTitle';
  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = '关闭';
  close.setAttribute('aria-label', '关闭帮助');
  head.append(title, close);
  const body = document.createElement('div');
  body.className = 'aas-help-body';
  dialog.append(head, body);
  document.body.append(dialog);
  let trigger = null;
  function hide() {
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
    trigger?.focus();
  }
  close.addEventListener('click', hide);
  dialog.addEventListener('click', event => { if (event.target === dialog) hide(); });
  dialog.addEventListener('close', () => trigger?.focus());
  for (const source of candidates) {
    if (source.hasAttribute('data-keep-visible') || source.closest('#v2SourceDialog')) continue;
    const destination = source.classList.contains('csv-wizard-instruction') ?
      source.parentElement.querySelector('.csv-wizard-destination') : null;
    const text = source.textContent.trim();
    if (!text) continue;
    let anchor = source.previousElementSibling;
    if (source.closest('.v2-csv-dialog, .sim-csv-dialog')) {
      anchor = source.closest('dialog')?.querySelector('.v2-csv-dialog-head h2, .sim-csv-dialog-head h2');
    } else if (source.closest('#v2FaultDialog')) {
      anchor = source.closest('dialog')?.querySelector('.v2-dialog-header strong');
    }
    const wizardInstruction = source.classList.contains('csv-wizard-instruction');
    const pluginGuide = source.classList.contains('pg-dev-help');
    const heading = anchor?.matches?.('h1,h2,h3,strong,summary') ? anchor :
      source.parentElement.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > strong, :scope > summary');
    const label = source.dataset.helpTitle || (pluginGuide ? '插件开发说明' : wizardInstruction ? '当前导入步骤' : heading?.textContent.trim()) ||
      source.closest('section,dialog,details')?.querySelector('h2,h3,summary,strong')?.textContent.trim() || '说明';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'aas-help-trigger';
    button.textContent = '?';
    button.setAttribute('aria-label', label + '帮助');
    button.setAttribute('title', label + '帮助');
    if (wizardInstruction && heading) heading.after(button);
    else if (heading) heading.append(button);
    else source.after(button);
    source.classList.add('aas-context-source');
    source.hidden = true;
    if (destination) { destination.classList.add('aas-context-source'); destination.hidden = true; }
    button.addEventListener('click', event => {
      event.stopPropagation();
      trigger = button;
      title.textContent = wizardInstruction && heading ? heading.textContent.trim() : label;
      body.textContent = pluginGuide ? [...source.querySelectorAll('li')].map(item => item.textContent.trim()).join('\n\n') :
        source.textContent.trim() + (destination ? '\n' + destination.textContent.trim() : '');
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
      close.focus();
    });
  }
})();
