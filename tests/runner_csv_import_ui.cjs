'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const html=fs.readFileSync('web/visualization/runner.html','utf8');
const src=fs.readFileSync('web/visualization/runner-v2.js','utf8');
for(const role of ['train','eval']){
 for(const id of ['CsvFile','CsvEncoding','CsvDelimiter','CsvFormatConfirmed','CsvModel','CsvSubsystem','CsvDevice','CsvUnit','CsvReference',
                   'CsvColumns','CsvInspect','CsvPreview','CsvCommit','CsvStatus']){
  assert.ok(html.includes(`id="${role+id}"`), `Missing in-page import ${role+id}`);
 }
}
assert.ok(html.includes('runner-csv-import.js'));
assert.ok(src.includes('onCsvImported'));
const wizard=fs.readFileSync('web/visualization/runner-csv-import.js','utf8');
for(const item of ['/api/v2/user-csv-workspace/import-source',"operation:'inspect'","operation:'preview'","operation:'commit'"]){
 assert.ok(wizard.includes(item));
}
assert.ok(wizard.includes('bind_digest_group:true'));
assert.ok(wizard.includes('all_normal_confirmed'));
assert.ok(wizard.includes('unlabeled_test_confirmed'));
assert.ok(wizard.includes('csv_encoding') && wizard.includes('csv_delimiter'));
assert.ok(wizard.includes('format_review_required'));
for(const id of ['evalModelFile','evalModelImportName','evalModelImport','evalModelExport','evalModelImportStatus']){
 assert.ok(html.includes(`id="${id}"`),`Missing model-bundle control ${id}`);
}
assert.ok(html.includes('runner-model-bundle.js'));
const modelUi=fs.readFileSync('web/visualization/runner-model-bundle.js','utf8');
assert.ok(modelUi.includes('/api/v2/models/import-bundle'));
assert.ok(modelUi.includes('/api/v2/models/export-bundle'));
console.log('Inline training/evaluation CSV import contract: PASS');
