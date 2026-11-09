'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const html = fs.readFileSync('web/visualization/import.html', 'utf8');
const js = fs.readFileSync('web/visualization/telemetry-v2-batch.js', 'utf8');

for (const id of ['v2BatchTaskProgress','v2BatchTaskProgressMeta','v2BatchCancel']) {
  assert.ok(html.includes(`id="${id}"`), `missing ${id}`);
  assert.ok(js.includes(`$('${id}')`), `batch UI does not use ${id}`);
}
assert.ok(js.includes('window.AASTasks.status'), 'batch UI must read the unified task lifecycle');
assert.ok(js.includes('window.AASTasks.cancel'), 'batch UI must support confirmed task cancellation');
assert.ok(js.includes("'cancelled'"), 'batch UI must render cancelled as a terminal state');
assert.ok(js.includes("'failed'"), 'batch UI must expose failed tasks to retry controls');
console.log('Batch import unified task progress, cancellation and history contract: PASS');
