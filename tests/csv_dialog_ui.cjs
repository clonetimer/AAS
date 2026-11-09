'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('training, evaluation and simulation CSV actions open focused dialogs', () => {
  const sources = ['runner.html', 'simulation.html'].map(name =>
    fs.readFileSync(path.join(__dirname, '../web/visualization', name), 'utf8'));
  const [runner, simulation] = sources;
  assert.equal(runner.includes('为什么训练 CSV 不导入历史数据库'), false);
  assert.equal(runner.includes('为什么测试 CSV 不导入历史数据库'), false);
  for (const id of ['trainCsvDialog', 'evalCsvDialog']) assert.match(runner, new RegExp(`id="${id}"`));
  assert.match(simulation, /id="simCsvDialog"/);

  const elements = new Map();
  function element(id) {
    const listeners = {};
    const node = {id, open: false, focused: false, listeners,
      addEventListener(name, fn) { listeners[name] = fn; },
      click() { listeners.click?.({target: node}); },
      focus() { node.focused = true; },
      showModal() { node.open = true; },
      close() { node.open = false; listeners.close?.(); },
      querySelector(selector) { return selector === '[data-close-csv-dialog]' ? elements.get(id + '-close') : elements.get(id + '-file'); }};
    return node;
  }
  for (const [role, trigger] of [['train', 'openTrainCsvDialog'], ['eval', 'openEvalCsvDialog'], ['sim', 'openSimCsvDialog']]) {
    elements.set(role + 'CsvDialog', element(role + 'CsvDialog'));
    elements.set(role + 'CsvDialog-close', element(role + 'CsvDialog-close'));
    elements.set(role + 'CsvDialog-file', element(role + 'CsvDialog-file'));
    elements.set(trigger, element(trigger));
  }
  const code = fs.readFileSync(path.join(__dirname, '../web/visualization/runner-csv-dialog.js'), 'utf8');
  vm.runInNewContext(code, {document: {getElementById: id => elements.get(id)}});
  for (const [role, trigger] of [['train', 'openTrainCsvDialog'], ['eval', 'openEvalCsvDialog'], ['sim', 'openSimCsvDialog']]) {
    const dialog = elements.get(role + 'CsvDialog');
    elements.get(trigger).click();
    assert.equal(dialog.open, true, role + ' opens');
    assert.equal(elements.get(role + 'CsvDialog-file').focused, true, role + ' focuses file');
    elements.get(role + 'CsvDialog-close').click();
    assert.equal(dialog.open, false, role + ' closes');
    assert.equal(elements.get(trigger).focused, true, role + ' restores focus');
  }
});
