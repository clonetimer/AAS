"""Phase 28: hard browser datetime bounds and removal of the duplicate catalog checkbox."""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'web'/'visualization'


def test_form_has_native_picklists_but_not_coverage_toggle():
    html=(WEB/'browser.html').read_text(encoding='utf-8')
    script=(WEB/'telemetry-v2-browser.js').read_text(encoding='utf-8')
    for field in ('v2Start','v2End'):
        assert f'id="{field}" type="datetime-local"' in html
        assert f"'{field}'" in script
    assert 'id="v2CoverageOnly"' not in html
    assert 'v2CoverageOnly' not in script
    assert 'id="v2TimeError"' in html
    assert 'role="alert"' in html
    assert 'input.min=result.min' in script and 'input.max=result.max' in script
    assert 'input.disabled=true' in script
    assert 'updateDateConstraints()' in script


def test_browser_rejects_invalid_time_without_modifying_previous_choice():
    node=shutil.which('node')
    if not node:
        import pytest
        pytest.skip('Node.js unavailable')
    program=r'''
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const text=fs.readFileSync(process.argv[1],'utf8');
const start=text.indexOf('  const timeForApi = id => {');
const stop=text.indexOf('  const makeParams = object => {',start);
const time={min:'2026-01-01T00:00:02.000',max:'2026-01-01T00:00:07.000'};
const input=value=>({value,min:time.min,max:time.max,dataset:{lastValid:value}});
const fields={v2Start:input('2026-01-01T00:00:03'),v2End:input('2026-01-01T00:00:06'),
    v2TimeError:{hidden:true,textContent:''},v2Export:{disabled:false},v2ExportStatus:{textContent:''}};
const state={selected:[1,2],coverage:{has_common_window:true,...time},exporting:false,lines:[]};
const context={state,Date,Error,byId:id=>fields[id]};
vm.runInNewContext(text.slice(start,stop)+'\nthis.handle=dateSelectionChanged;this.validate=validatedTimeRange;this.dateValue=dateValue;',context);
for (const [id,value,previous] of [
    ['v2Start','2025-12-31T23:59:59','2026-01-01T00:00:03'],
    ['v2End','2026-01-01T00:00:08','2026-01-01T00:00:06'],
    ['v2Start','2026-01-01T00:00:07','2026-01-01T00:00:03']]) {
    fields[id].value=value;
    context.handle(id);
    assert.equal(fields[id].value,previous);
    assert.equal(fields.v2TimeError.hidden,false);
}
// Date string with no fractional part represents the same exact instant.
fields.v2Start.value='2026-01-01T00:00:02';context.handle('v2Start');
fields.v2End.value='2026-01-01T00:00:07';context.handle('v2End');
assert.equal(fields.v2TimeError.hidden,true);
assert.equal(fields.v2Export.disabled,false);
assert.equal(context.dateValue(fields.v2Start.max),context.dateValue('2026-01-01T00:00:07'));
assert.equal(context.dateValue(fields.v2End.min),context.dateValue('2026-01-01T00:00:02'));
assert.deepEqual(JSON.parse(JSON.stringify(context.validate())),{
  start:'2026-01-01 00:00:02',end:'2026-01-01 00:00:07'});
'''
    completed=subprocess.run([node,'-e',program,str(WEB/'telemetry-v2-browser.js')],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr
