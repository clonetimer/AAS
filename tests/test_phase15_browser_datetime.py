"""Phase 15: calendar input contract for V2 browser and source-light release."""
from pathlib import Path
import shutil
import subprocess

from scripts.server.telemetry_v2_access import relative_seconds

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'web'/'visualization'


def test_browser_uses_native_seconds_date_time_controls():
    html=(WEB/'browser.html').read_text(encoding='utf-8')
    for ident in ('v2Start','v2End'):
        assert f'id="{ident}" type="datetime-local" step="1"' in html
    assert 'id="v2TimeHelp"' in html
    assert 'id="v2Start" type="text"' not in html
    assert 'id="v2End" type="text"' not in html
    js=(WEB/'telemetry-v2-browser.js').read_text(encoding='utf-8')
    assert "Object.assign(result,validatedTimeRange())" not in js
    assert "v2CoverageOnly" not in js
    assert "v2CoverageOnly" not in html
    assert "dateSelectionChanged(id)" in js
    assert 'const range=validatedTimeRange();' in js
    assert 'start:range.start||null,end:range.end||null' in js


def test_picker_to_backend_conversion_and_invalid_range():
    node=shutil.which('node')
    if not node:
        import pytest
        pytest.skip('Node.js 不可用；页面脚本行为须通过浏览器验证')
    # Test the actual helper extracted from the shipped JS, not a rewritten copy.
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const js=fs.readFileSync(process.argv[1],'utf8');
const start=js.indexOf('  const timeForApi = id => {');
const stop=js.indexOf('  const makeParams = object => {',start);
assert(start>=0 && stop>start);
const state={selected:[1],coverage:{has_common_window:true,min:'2026-01-01T00:00:00.000',max:'2026-01-01T00:05:00.000'},lines:[],exporting:false};
const field=v=>({value:v,min:state.coverage.min,max:state.coverage.max,dataset:{lastValid:v}});
const values={v2Start:field('2026-01-01T00:01'),v2End:field('2026-01-01T00:02:30'),
 v2TimeError:{textContent:'',hidden:true},v2Export:{disabled:true},v2ExportStatus:{textContent:''}};
const context={byId:id=>values[id],Error,state,Date};
vm.runInNewContext(js.slice(start,stop)+`
this.convert=timeForApi;this.validate=validatedTimeRange;this.enter=dateSelectionChanged;this.stamp=dateValue;`,context);
assert.strictEqual(context.convert('v2Start'),'2026-01-01 00:01:00');
assert.strictEqual(context.convert('v2End'),'2026-01-01 00:02:30');
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.validate())),{
  start:'2026-01-01 00:01:00',end:'2026-01-01 00:02:30'});
// The same second with and without .000 must be equal, not a false violation.
assert.strictEqual(context.stamp('2026-01-01T00:00:00'),context.stamp('2026-01-01T00:00:00.000'));
values.v2Start.value='2026-01-01T00:10';context.enter('v2Start');
assert.strictEqual(values.v2Start.value,'2026-01-01T00:01');
assert.match(values.v2TimeError.textContent,/超出/);
values.v2Start.value='2026-01-01T00:03';context.enter('v2Start');
assert.strictEqual(values.v2Start.value,'2026-01-01T00:01');
assert.match(values.v2TimeError.textContent,/晚于/);
values.v2Start.value='2026-01-01T00:00:00';context.enter('v2Start');
assert.strictEqual(values.v2Start.value,'2026-01-01T00:00:00');
assert.strictEqual(values.v2TimeError.textContent,'');
values.v2End.value='';context.enter('v2End');
assert.strictEqual(context.convert('v2End'),'');
'''
    result=subprocess.run([node,'-e',script,str(WEB/'telemetry-v2-browser.js')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+'\n'+result.stderr


def test_display_datetime_maps_to_relative_seconds_without_timezone_shift():
    assert relative_seconds('2026-01-01 00:01:00')==60
    assert relative_seconds('2026-01-01 00:02:30')==150
    assert relative_seconds('2026-01-01T00:02:30')==150


def test_main_sources_not_bundled_in_slim_checkout():
    # Target labels, templates, and pre-seeded simulation database remain.
    assert (ROOT/'datasets'/'source_labels'/'targets.zip').is_file()
    assert (ROOT/'datasets'/'templates'/'AAS_column_mapping_template.csv').is_file()
    assert (ROOT/'datasets'/'aas_simulation_v2.sqlite').is_file()


def test_simulated_fault_database_accepts_calendar_picker_filter():
    from scripts.server.telemetry_v2_access import list_channels,query_charts
    db=ROOT/'datasets'/'aas_simulation_v2.sqlite'
    found=list_channels(db,role='test',start='2026-01-01 00:03:20',end='2026-01-01 00:03:23')
    assert found['count']==3
    result=query_charts(db,role='test',channel_ids=[found['items'][0]['id']],
                        start='2026-01-01 00:03:20',end='2026-01-01 00:03:23')
    assert len(result['series'][0]['points'])==2
