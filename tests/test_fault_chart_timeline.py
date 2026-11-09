"""Chart event mapping tests: labels remain separate from measurements."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / 'web' / 'visualization' / 'fault-chart.js'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js unavailable')
def test_fault_chart_timestamp_projection_and_interval_boundaries():
    script = r'''
const assert = require('node:assert/strict');
const chart = require(process.argv[1]);
const numeric = chart.axisFor([0, 3, 10, 35]);
assert.deepEqual(numeric, {kind:'numeric',min:0,max:35});
assert.equal(chart.axisFor([10,'2026-10-19T00:00:00Z']),null);
assert.equal(chart.parseTime('2026-10-19 00:00:01').kind,'local');
assert.equal(chart.parseTime('2026-10-19T00:00:01Z').kind,'utc');
assert.equal(chart.parseTime('none'),null);
assert.equal(chart.toTimestamp(0,'numeric'),'0');
assert.equal(chart.toTimestamp(1.25,'numeric'),'1.25');
assert.equal(chart.toTimestamp(1.25,'local'),'1970-01-01 00:00:01.250000');
const utcAxis = chart.axisFor(['2026-10-19T00:00:00Z','2026-10-19T00:01:00Z']);
const localAxis = chart.axisFor(['2026-10-19 00:00:00','2026-10-19 00:01:00']);
assert.equal(utcAxis.max-utcAxis.min,60);
assert.equal(localAxis.max-localAxis.min,60);
const us=chart.parseTime('2026-10-19T00:00:00.123456Z');
assert.ok(Math.abs(us.seconds - Date.parse('2026-10-19T00:00:00.123Z')/1000 - 0.000456) < 0.0000004);
assert.ok(chart.toTimestamp(us.seconds,'utc').endsWith('.123456Z') || chart.toTimestamp(us.seconds,'utc').endsWith('.123455Z'));
const padding={left:50,right:20,top:24,bottom:40};
const zoom={scale:1,offsetX:0};
const geometry=chart.geometryFor(numeric,zoom,padding,1000);
assert.equal(geometry.timeX(0),50);
assert.equal(geometry.timeX(35),980);
for (const x of [50,300,770,980]) assert.ok(Math.abs(geometry.timeX(geometry.atX(x))-x)<1e-8);
const events=[{id:1,start_timestamp:5,end_timestamp:15,label:1},
 {id:2,start_timestamp:15,end_timestamp:24,label:0},
 {id:3,start_timestamp:-10,end_timestamp:2,label:1},
 {id:4,start_timestamp:40,end_timestamp:50,label:1}];
const bands=chart.bands(events,numeric,zoom,padding,1000);
assert.equal(bands.length,3);
assert.equal(bands[0].label,1);
assert.equal(bands[1].label,0);
assert.equal(bands[0].right,bands[1].left); // half-open boundary, no forced gap
assert.equal(bands[2].left,50); // clipping
const zoom2={scale:3,offsetX:0.25};
const g2=chart.geometryFor(numeric,zoom2,padding,1000);
assert.ok(Math.abs(g2.atX(g2.timeX(10))-10)<1e-9);
assert.deepEqual(chart.bands([{id:1,start_timestamp:'2026-10-19 00:00:01',end_timestamp:'2026-10-19 00:00:02',label:1}],numeric,zoom,padding,1000),[]);
console.log('fault chart axis/projection/interval cases PASS');
'''
    result = subprocess.run([shutil.which('node'), '-e', script, str(JS)],
                            capture_output=True, text=True, check=False, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
