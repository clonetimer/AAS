'use strict';
const fs=require('node:fs');
const js=fs.readFileSync('web/visualization/runner-v2.js','utf8');
function check(ok,msg){if(!ok)throw new Error(msg);}
check(js.includes("RUNNER_DRAFT_KEY='aas.runner.safe-draft.v1'"),'versioned draft key missing');
check(js.includes('RUNNER_DRAFT_MAX=4096'),'4 KiB bound missing');
const block=js.slice(js.indexOf('const RUNNER_DRAFT_FIELDS'),js.indexOf('function readRunnerDraft'));
for(const safe of ['trainDataSource','trainUserCsvDataset','trainStart','trainEnd','trainAlgorithm','evalDataSource','evalUserCsvDataset','evalStart','evalEnd','evalSourceModel','evalTargetSplit'])check(block.includes(safe),'allowlist missing '+safe);
for(const forbidden of ['task_id','execution_receipt','raw_bytes','preview','authorization','password'])check(!block.includes(forbidden),'unsafe draft field '+forbidden);
check(js.includes('node.options'),'stale select values must be rejected');
check(js.includes('await Promise.all([loadRoleCatalog(\'train\'),loadRoleCatalog(\'eval\')])'),'catalog must reload after restore');
console.log('Runner bounded safe draft persistence contract: PASS');
