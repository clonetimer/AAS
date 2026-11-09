"""Browser-level event annotation regression tests (optional Chromium).

No HTTP server or persistent SQLite writes. All API responses are isolated mocks.
"""
import shutil
import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not shutil.which("chromium"), reason="Chromium not installed")

def test_adjacent_event_boundary_resizing_and_zoom():
    from playwright.sync_api import sync_playwright
    from pathlib import Path
    import json
    import re

    base=Path(__file__).resolve().parents[1]/'web'/'visualization'/'browser.html'

    mock = r'''(() => {
     window.__faultEvents=[{id:1,start_timestamp:'10',end_timestamp:'20',label:1,fault_type:'bias',notes:''},
                            {id:2,start_timestamp:'20',end_timestamp:'25',label:0,fault_type:'',notes:''}];
     window.__calls=[];
     const rows=Array.from({length:40},(_,i)=>({timestamp:i,value:5+Math.sin(i/3),label:i>=10&&i<20?1:0,model:'S',subsystem:'ADCS',device:'gyro',variable:'temp'}));
     window.fetch = async (url,opts={}) => {
      let u = new URL(String(url), 'https://local.test');
      let path=u.pathname;
      window.__calls.push({path,method:opts.method||'GET'});
      let out={};
      if(path==='/api/databases') out={items:[{value:'history_sample.db',label:'历史测试库',preferred_table:'sensor'}]};
      else if(path==='/api/sqlite-tables') out={items:[{name:'sensor',row_count:40,data_role:'test',hierarchy:[{model:'S',subsystem:'ADCS',device:'gyro',variable:'temp'}]}]};
      else if(path==='/api/hierarchy-options') out={models:['S'],subsystems:['ADCS'],devices:['gyro'],variables:['temp']};
      else if(path==='/api/sqlite-rows') out={rows, columns:Object.keys(rows[0]), matched_count:40,total_count:40,truncated:false};
      else if(path==='/api/sqlite-time-range'||path==='/api/time-range') out={min:0,max:39,value_type:'number',row_count:40};
      else if(path==='/api/sqlite-meta') out={meta:{}};
      else if(path==='/api/preprocessing/status') out={status:'none'};
      else if(path==='/api/fault-intervals') {
        if(opts.method==='POST') {
          let x=JSON.parse(opts.body);
          if(x.action==='create') { const id=Math.max(...window.__faultEvents.map(e=>e.id),0)+1; window.__faultEvents.push({id,start_timestamp:x.start,end_timestamp:x.end,label:x.label,fault_type:x.fault_type,notes:x.notes});out={status:'success',event:{id}}; }
          else if(x.action==='update') {const e=window.__faultEvents.find(e=>e.id===x.id);Object.assign(e,{start_timestamp:x.start,end_timestamp:x.end,label:x.label,fault_type:x.fault_type,notes:x.notes}); out={status:'success'};}
          else if(x.action==='delete') {window.__faultEvents=window.__faultEvents.filter(e=>e.id!==x.id);out={status:'success'};}
        } else out={items:window.__faultEvents};
      }
      return {ok:true,status:200,json:async()=>out};
     };
    })();'''
    with sync_playwright() as p:
     b=p.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox','--disable-dev-shm-usage','--allow-file-access-from-files'])
     page=b.new_page(viewport={'width':1440,'height':900})
     errors=[];page.on('pageerror',lambda x:errors.append(str(x)))
     html=base.read_text(encoding='utf-8')
     html=re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
     page.set_content(html,wait_until='domcontentloaded')
     page.add_style_tag(content=(base.parent/'styles-light.css').read_text(encoding='utf-8'))
     page.evaluate(mock)
     for script in ['resource-picker.js','fault-chart.js','browser.js','table-export.js']:
      page.add_script_tag(content=(base.parent/script).read_text(encoding='utf-8'))
     page.wait_for_timeout(800)
     print('errors:',errors)
     print('api calls:',page.evaluate('window.__calls').__str__()[:1600])
     print('chart exists',page.locator('.chart-svg').count(),'events',page.locator('.event-band').count())
     if page.locator('.chart-svg').count()==0: print('body:', page.locator('body').inner_text()[-1300:]);raise Exception('chart missing')
     assert page.locator('.event-band').count()==2
     page.locator('#chartAnnotateToggle').click();assert page.locator('#chartAnnotateToggle').get_attribute('aria-pressed')=='true'
     bounds=page.locator('.chart-svg').bounding_box()
     # Interpolate plot at 26..34 on 0..39 axis
     def xpos(t): return bounds['x']+bounds['width']*(54+(t/39)*(1100-54-24))/1100
     def ypos(): return bounds['y']+bounds['height']*0.55
     page.mouse.move(xpos(27),ypos());page.mouse.down();page.mouse.move(xpos(34),ypos(),steps=8);page.mouse.up();
     page.wait_for_timeout(400)
     print('modal',page.locator('#faultIntervalsModal').evaluate('(el)=>el.style.display'))
     start=page.locator('#faultIntervalStart').input_value();end=page.locator('#faultIntervalEnd').input_value()
     print('selection',start,end)
     assert float(start)<float(end)
     page.locator('#faultIntervalLabel').select_option('1');page.locator('#saveFaultIntervalBtn').click()
     page.wait_for_timeout(350)
     print('events after create',page.evaluate('window.__faultEvents'))
     assert len(page.evaluate('window.__faultEvents'))==3
     page.locator('#closeFaultIntervalsBtn').click()
     page.locator('[data-event-id="1"][data-event-edge="start"]').first.click(force=True)
     page.wait_for_timeout(350)
     assert page.locator('#saveFaultIntervalBtn').inner_text()=='保存修改'

     # Closing edit and dragging start boundary should populate an updated start.
     page.locator('#closeFaultIntervalsBtn').click()
     before=page.evaluate('window.__faultEvents.find(e=>e.id===1).start_timestamp')
     handle=page.locator('[data-event-id="1"][data-event-edge="start"]').first
     hb=handle.bounding_box()
     hx=hb['x']+hb['width']/2; hy=hb['y']+hb['height']/2
     page.mouse.move(hx,hy);page.mouse.down();page.mouse.move(hx+24,hy,steps=10);page.mouse.up()
     page.wait_for_timeout(250)
     print('drag start edit value',page.locator('#faultIntervalStart').input_value(),'before',before)
     assert page.locator('#saveFaultIntervalBtn').inner_text()=='保存修改'
     new_start=float(page.locator('#faultIntervalStart').input_value())
     assert new_start>float(before), (new_start,before)
     page.locator('#saveFaultIntervalBtn').click();page.wait_for_timeout(200)
     assert abs(float(page.evaluate('window.__faultEvents.find(e=>e.id===1).start_timestamp'))-new_start)<1e-4
     page.locator('#closeFaultIntervalsBtn').click()
     # Switch to ordinary view and confirm existing marks do not offer edit hit-targets.
     page.locator('#chartAnnotateToggle').click()
     assert page.locator('.event-hit').count()==0
     assert page.locator('.event-band').count()==3
     page.locator('#chartAnnotateToggle').click()
     # Zoom in and then drag end edge: editing must remain on the correct timestamp scale.
     page.locator('button[onclick="zoomIn()"]').first.click()
     edge=page.locator('[data-event-id="1"][data-event-edge="end"]').first
     eb=edge.bounding_box();ex=eb['x']+eb['width']/2;ey=eb['y']+eb['height']/2
     page.mouse.move(ex,ey);page.mouse.down();page.mouse.move(ex-12,ey,steps=6);page.mouse.up();page.wait_for_timeout(250)
     assert page.locator('#saveFaultIntervalBtn').inner_text()=='保存修改'
     after_end=float(page.locator('#faultIntervalEnd').input_value()); assert after_end<20,after_end
     page.locator('#closeFaultIntervalsBtn').click()
     print('boundary resize, zoom and cancellation PASS')
     # No artifacts are written by this test.
     print('completed, errors',errors)
     assert errors==[]
     b.close()


def test_primary_table_event_ownership_and_switch():
    from playwright.sync_api import sync_playwright
    from pathlib import Path
    import json
    import re

    base=Path(__file__).resolve().parents[1]/'web'/'visualization'/'browser.html'

    mock = r'''(() => {
     window.__faultEvents=[{id:1,start_timestamp:'10',end_timestamp:'20',label:1,fault_type:'bias',notes:''},
                            {id:2,start_timestamp:'20',end_timestamp:'25',label:0,fault_type:'',notes:''}];
     window.__calls=[]; window.__otherEvents=[{id:17,start_timestamp:'30',end_timestamp:'35',label:1,fault_type:'other',notes:''}];
     const rows=Array.from({length:40},(_,i)=>({timestamp:i,value:5+Math.sin(i/3),label:i>=10&&i<20?1:0,model:'S',subsystem:'ADCS',device:'gyro',variable:'temp'}));
     window.fetch = async (url,opts={}) => {
      let u = new URL(String(url), 'https://local.test');
      let path=u.pathname;
      window.__calls.push({path,method:opts.method||'GET'});
      let out={};
      if(path==='/api/databases') out={items:[{value:'history_sample.db',label:'历史测试库',preferred_table:'sensor'}]};
      else if(path==='/api/sqlite-tables') out={items:[{name:'sensor',row_count:40,data_role:'test',hierarchy:[{model:'S',subsystem:'ADCS',device:'gyro',variable:'temp'}]},{name:'sensor_b',row_count:40,data_role:'test',hierarchy:[{model:'S',subsystem:'ADCS',device:'gyro',variable:'voltage'}]}]};
      else if(path==='/api/hierarchy-options') out={models:['S'],subsystems:['ADCS'],devices:['gyro'],variables:['temp','voltage']};
      else if(path==='/api/sqlite-rows') out={rows, columns:Object.keys(rows[0]), matched_count:40,total_count:40,truncated:false};
      else if(path==='/api/sqlite-time-range'||path==='/api/time-range') out={min:0,max:39,value_type:'number',row_count:40};
      else if(path==='/api/sqlite-meta') out={meta:{}};
      else if(path==='/api/preprocessing/status') out={status:'none'};
      else if(path==='/api/fault-intervals') {
        if(opts.method==='POST') {
          let x=JSON.parse(opts.body);
          if(x.action==='create') { const id=Math.max(...window.__faultEvents.map(e=>e.id),0)+1; window.__faultEvents.push({id,start_timestamp:x.start,end_timestamp:x.end,label:x.label,fault_type:x.fault_type,notes:x.notes});out={status:'success',event:{id}}; }
          else if(x.action==='update') {const e=window.__faultEvents.find(e=>e.id===x.id);Object.assign(e,{start_timestamp:x.start,end_timestamp:x.end,label:x.label,fault_type:x.fault_type,notes:x.notes}); out={status:'success'};}
          else if(x.action==='delete') {window.__faultEvents=window.__faultEvents.filter(e=>e.id!==x.id);out={status:'success'};}
        } else out={items:u.searchParams.get('table')==='sensor_b'?window.__otherEvents:window.__faultEvents};
      }
      return {ok:true,status:200,json:async()=>out};
     };
    })();'''
    with sync_playwright() as p:
     b=p.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox','--disable-dev-shm-usage','--allow-file-access-from-files'])
     page=b.new_page(viewport={'width':1440,'height':900})
     errors=[];page.on('pageerror',lambda x:errors.append(str(x)))
     html=base.read_text(encoding='utf-8')
     html=re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
     page.set_content(html,wait_until='domcontentloaded')
     page.add_style_tag(content=(base.parent/'styles-light.css').read_text(encoding='utf-8'))
     page.evaluate(mock)
     for script in ['resource-picker.js','fault-chart.js','browser.js','table-export.js']:
      page.add_script_tag(content=(base.parent/script).read_text(encoding='utf-8'))
     page.wait_for_timeout(800)
     page.wait_for_timeout(500)
     assert page.locator('.event-band').count()==2
     # Do not mutate database when switching the primary table.
     page.evaluate("state.selectedTables=['sensor','sensor_b']; activateMultiTableSelection()")
     page.wait_for_timeout(600)
     assert page.locator('.event-band').count()==2
     assert page.locator('.multi-series-legend').count()==1
     # Primary A event ids 1/2; event B id 17 must not appear.
     page.locator('#chartAnnotateToggle').click()
     assert page.locator('[data-event-id="1"]').count()>0
     assert page.locator('[data-event-id="17"]').count()==0
     page.locator('#chooseBrowserResources').click()
     page.locator('.aas-picker-panel').nth(1).locator('.aas-picker-entry').nth(1).get_by_role('button',name='↑').click()
     page.locator('.aas-picker-apply').click()
     page.wait_for_timeout(800)
     assert page.locator('.event-band').count()==1, page.locator('.event-band').count()
     page.locator('#chartAnnotateToggle').click()
     assert page.locator('[data-event-id="17"]').count()>0
     assert page.locator('[data-event-id="1"]').count()==0
     assert page.locator('text=仅标注主表').count()>=1
     print('multi table primary switch and event ownership PASS; errors',errors)
     assert not errors, errors
     b.close()

