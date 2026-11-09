(() => {
  'use strict';
  for (const [role, triggerId] of [['train', 'openTrainCsvDialog'], ['eval', 'openEvalCsvDialog'], ['sim', 'openSimCsvDialog']]) {
    const dialog = document.getElementById(role + 'CsvDialog');
    const trigger = document.getElementById(triggerId);
    const close = dialog?.querySelector('[data-close-csv-dialog]');
    if (!dialog || !trigger || !close) continue;
    trigger.addEventListener('click', () => {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
      dialog.querySelector('input[type="file"]')?.focus();
    });
    close.addEventListener('click', () => {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    });
    dialog.addEventListener('click', event => {
      if (event.target === dialog) close.click();
    });
    dialog.addEventListener('close', () => trigger.focus());
  }
})();
