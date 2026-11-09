'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeNode {
  constructor() {
    this.children = [];
    this.className = '';
    this.value = '';
    this.textContent = '';
    this.innerHTML = '';
    this.style = {};
    this.handlers = {};
    this.classList = {
      contains: key => String(this.className).split(' ').includes(key),
      add: key => {if (!this.classList.contains(key)) this.className += ' ' + key;},
      remove: key => {this.className = this.className.split(' ').filter(x => x !== key).join(' ');},
      toggle: () => {},
    };
  }
  addEventListener(type, callback) {this.handlers[type] = callback;}
  append(...children) {for (const child of children) {child.parent = this;this.children.push(child);}}
  querySelector(selector) {
    return this.children.find(child => selector === '.aas-report-action' && child.className === 'aas-report-action') || null;
  }
  querySelectorAll() {return [];}
  setAttribute(name, value) {this[name] = value;}
  remove() {if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this);}
  replaceChildren(...children) {this.children = [];this.append(...children);}
  focus() {}
}

const html = fs.readFileSync('web/visualization/reports.html', 'utf8');
const tabElements = [...html.matchAll(/<button class="([^"]+)" data-type="(training|evaluation|all)">/g)]
  .map(([, cls, type]) => Object.assign(new FakeNode(), {className: cls, dataset: {type}}));
assert.equal(tabElements.length, 3, 'Report page should show three report type tabs');
assert.equal(tabElements.find(tab => tab.classList.contains('active'))?.dataset.type, 'all',
  'The visible default report tab must show training and evaluation together');

const nodes = new Map();
const fetchedPaths = [];
const document = {
  getElementById(id) {if (!nodes.has(id)) nodes.set(id, new FakeNode());return nodes.get(id);},
  querySelectorAll(selector) {return selector === '.experiment-type-tab-btn' ? tabElements : [];},
  createElement() {return new FakeNode();},
};
const sandbox = {
  document, window: {}, location: {hash: ''}, decodeURIComponent, encodeURIComponent,
  console: {error() {}},
  fetch: async path => {fetchedPaths.push(path);return {ok: true, json: async () => ({items: []})};},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('web/visualization/reports.js', 'utf8'), sandbox);
assert.equal(vm.runInContext('state.selectedExperimentType', sandbox), 'all');
assert.equal(fetchedPaths[0], '/api/experiments', 'Default report API must request all task types');

const actions = new Map();
const actionDocument = {
  getElementById(id) {if (!actions.has(id)) actions.set(id, new FakeNode());return actions.get(id);},
  createElement() {return new FakeNode();},
};
const navScope = {document: actionDocument, window: {}};
vm.createContext(navScope);
vm.runInContext(fs.readFileSync('web/visualization/aas-report-navigation.js', 'utf8'), navScope);
const navigation = navScope.window.AASReportNavigation;
assert.ok(navigation);
const completed = navigation.renderLink('train', {
  archive_status: 'complete',
  summary_path: 'outputs/experiment_training_20261105/reports/summary.json',
});
assert.ok(completed);
assert.equal(completed.href, 'reports.html#outputs%2Fexperiment_training_20261105%2Freports%2Fsummary.json');
assert.equal(completed.textContent, '查看本次实验报表');

navigation.renderLink('train', {archive_status: 'failed', archive_error: '磁盘空间不足'});
assert.ok(!actions.get('trainOutput').children.some(x => x.href),
  'An archive failure must not keep a stale link to a previous report');
assert.ok(actions.get('trainOutput').children.some(x => x.textContent.includes('磁盘空间不足')));
assert.equal(navigation.renderLink('eval', {
  archive_status: 'complete', summary_path: '../../not-a-managed-report',
}), null, 'Unexpected paths must not become report links');
console.log('V2 report default tab, fetch-all, immutable report navigation: PASS');
