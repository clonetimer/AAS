'use strict';
// Exercise the production comparison click handler, not a rewritten renderer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/visualization/reports.js', 'utf8');
const from = source.indexOf("els.compareBtn?.addEventListener('click'");
const until = source.indexOf('async function loadExperiments()', from);
assert.ok(from > 0 && until > from, 'comparison handler must exist');
const handler = source.slice(from, until);
class FakeNode {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; this.disabled = false; this.handlers = {}; this.value = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text; }
  append(...nodes) { this.children.push(...nodes); }
  addEventListener(type, callback) { this.handlers[type] = callback; }
}
function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }
const left = new FakeNode('select'); left.value = 'eval_a';
const right = new FakeNode('select'); right.value = 'eval_b';
const output = new FakeNode('div'); const button = new FakeNode('button');
const els = {compareLeft:left, compareRight:right, compareResult:output, compareBtn:button};
let response;
const context = {els,document:{createElement:(tag)=>new FakeNode(tag)},
  api:async()=>response, encodeURIComponent};
vm.createContext(context);
vm.runInContext(handler, context);
(async()=>{
  response = {classification:'identical_model_inputs',reason:'same',scope_note:'not a ranking',
    metric_comparison:{status:'comparable',direction:'right_minus_left',rows:[
      {key:'accuracy',left:0.8,right:0.9,delta:0.1},
      {key:'f1',left:0.5,right:0.6,delta:0.1},
    ]}};
  await button.handlers.click();
  assert.equal(button.disabled,false);
  assert.ok(descendants(output).some(x=>x.tag==='table'),'equal verified inputs should render a side-by-side metrics table');
  assert.ok(descendants(output).some(x=>x.textContent.includes('0.9000')),'right metric value should be visible');
  assert.match(output.textContent,/not a ranking/);

  response = {classification:'different_or_unconfirmed_inputs',reason:'不同来源',
    metric_comparison:{status:'not_comparable',rows:[]}};
  await button.handlers.click();
  assert.ok(!descendants(output).some(x=>x.tag==='table'),'different inputs must hide metric comparison');
  assert.match(output.textContent,/不同来源/);
  assert.equal(button.disabled,false);
  console.log('Verified supervised comparison table, and non-comparable inputs blocked: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
