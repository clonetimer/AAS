'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');

function fixture(purpose='eval') {
  const path='web/visualization/csv-import-wizard.js';
  assert.ok(fs.existsSync(path), 'shared CSV wizard must exist');
  const dom=new JSDOM(`<main><section id="wizard"><fieldset data-csv-stage="file"><input id="file"></fieldset><fieldset data-csv-stage="fields"><input id="mapping" required><button id="preview" type="button">预检</button></fieldset><fieldset data-csv-stage="review"><button id="commit" type="button">确认导入</button></fieldset><fieldset data-csv-stage="done"></fieldset></section><button id="cancel">取消</button><details id="nextDetails"><summary>数据</summary><input id="nextTarget"></details></main>`, {url:'http://localhost/',runScripts:'outside-only'});
  const w=dom.window, d=w.document;
  w.HTMLElement.prototype.scrollIntoView=function(){};
  let writes=0;
  w.fetch=()=>{throw Error('navigation must not perform HTTP requests');};
  d.getElementById('commit').addEventListener('click',()=>writes++);
  w.eval(fs.readFileSync(path,'utf8'));
  const wizard=w.AASCsvImportWizard.create({rootId:'wizard',purpose,nextLabel:'检查导入数据',nextTarget:'nextTarget'});
  const panel=stage=>d.querySelector(`[data-csv-stage="${stage}"]`);
  const click=selector=>d.querySelector(selector).click();
  const current=()=>d.querySelector('[data-csv-go][aria-current="step"]').dataset.csvGo;
  return {dom,w,d,wizard,panel,click,current,writes:()=>writes};
}

test('navigation requires inspected format and a successful preflight; navigation never writes',()=>{
  const f=fixture();
  assert.equal(f.current(),'file');
  assert.equal(f.panel('fields').hidden,true);
  f.click('[data-csv-go="review"]');
  assert.equal(f.current(),'file');
  f.wizard.fileReady(true,{filename:'run.csv'});
  f.click('[data-csv-continue]');
  assert.equal(f.current(),'fields');
  f.click('[data-csv-go="review"]');
  assert.equal(f.current(),'fields');
  f.wizard.review({filename:'run.csv',row_count:12,column_count:1,columns:['speed'],label_status:'unknown'});
  assert.equal(f.current(),'review');
  assert.match(f.panel('review').textContent,/12/);
  assert.match(f.panel('review').textContent,/仅推理|只进行推理/);
  assert.equal(f.writes(),0);
  f.dom.window.close();
});

test('mapping changes revoke preflight; file changes also revoke format readiness',()=>{
  const f=fixture('train');
  f.wizard.fileReady(true,{filename:'normal.csv'});
  f.wizard.review({row_count:8,column_count:2,label_status:'confirmed_normal'});
  f.wizard.invalidate();
  assert.equal(f.current(),'fields');
  assert.equal(f.d.querySelector('[data-csv-go="review"]').disabled,true);
  f.wizard.review({row_count:8,column_count:2,label_status:'confirmed_normal'});
  f.wizard.reset('文件已更换，请重新检查');
  assert.equal(f.current(),'file');
  assert.equal(f.d.querySelector('[data-csv-continue]').disabled,true);
  assert.equal(f.d.querySelector('[data-csv-go="review"]').disabled,true);
  f.dom.window.close();
});

test('busy wizard locks editing and navigation but leaves cancellation usable',()=>{
  const f=fixture();
  f.wizard.fileReady(true);
  f.click('[data-csv-continue]');
  f.wizard.setBusy(true);
  assert.equal(f.d.getElementById('wizard').getAttribute('aria-busy'),'true');
  assert.equal(f.panel('fields').disabled,true);
  assert.equal(f.d.getElementById('cancel').disabled,false);
  f.click('[data-csv-go="file"]');
  assert.equal(f.current(),'fields');
  f.wizard.setBusy(false);
  assert.equal(f.panel('fields').disabled,false);
  assert.equal(f.panel('file').disabled,true,'hidden required fields must not block native form validation');
  f.dom.window.close();
});

test('successful import keeps its receipt when follow-up fails; next action only moves focus',()=>{
  const f=fixture();
  f.wizard.fileReady(true);
  f.wizard.review({filename:'run.csv',row_count:3,column_count:1,label_status:'unknown'});
  f.wizard.complete({filename:'run.csv',row_count:3,column_count:1,label_status:'unknown',followup_error:'自动选择失败，请手动选择已导入来源'});
  assert.equal(f.current(),'done');
  assert.match(f.panel('done').textContent,/导入完成/);
  assert.match(f.panel('done').textContent,/自动选择失败/);
  assert.equal(f.d.querySelector('[data-csv-go="review"]').disabled,true);
  f.click('[data-csv-next-action]');
  assert.equal(f.d.activeElement.id,'nextTarget');
  assert.equal(f.d.getElementById('nextDetails').open,true);
  assert.equal(f.writes(),0);
  f.dom.window.close();
});

test('summaries treat names as text and absent counts as unknown rather than zero',()=>{
  const f=fixture('simulation');
  f.wizard.fileReady(true);
  f.wizard.review({filename:'<img src=x onerror=alert(1)>.csv',columns:['<b>speed</b>'],label_status:'unknown'});
  assert.equal(f.panel('review').querySelector('img'),null);
  assert.equal(f.panel('review').querySelector('b'),null);
  assert.match(f.panel('review').textContent,/<img/);
  assert.match(f.panel('review').textContent,/待.*校验|尚未提供/);
  assert.doesNotMatch(f.panel('review').textContent,/0 帧/);
  f.dom.window.close();
});

test('keyboard step controls announce the active step and focus its heading',()=>{
  const f=fixture('history');
  f.wizard.fileReady(true);
  f.click('[data-csv-continue]');
  assert.equal(f.d.activeElement.id,'wizardStepTitle');
  assert.match(f.d.activeElement.textContent,/核对字段/);
  const current=f.d.querySelectorAll('[aria-current="step"]');
  assert.equal(current.length,1);
  assert.equal(f.d.querySelectorAll('[data-csv-go]').length,4);
  for(const button of f.d.querySelectorAll('#wizard button')) assert.notEqual(button.type,'submit');
  f.wizard.error('请填写单位后再次预检');
  assert.match(f.d.querySelector('[role="alert"]').textContent,/填写单位/);
  assert.equal(f.current(),'fields');
  f.dom.window.close();
});
