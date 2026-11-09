"""Phase 18: product navigation and dynamically integrated fault-type plugins."""
from pathlib import Path
import pytest
from scripts.server.v2_simulation_injection import v2_fault_type_catalog, _apply_fault_type

ROOT = Path(__file__).resolve().parents[1]


def test_product_name_and_navigation_order():
    js=(ROOT/'web/visualization/workbench.js').read_text(encoding='utf-8')
    html=(ROOT/'web/visualization/index.html').read_text(encoding='utf-8')
    assert '智能故障检测算法评估系统' in html
    names=('历史故障数据库','模拟故障数据库','数据预处理',
           '智能检测算法评估','任务查询与导出','检测算法管理','软件性能监控')
    for name in names:
        assert name in js
    assert [js.index("label: '"+name+"'") for name in names] == sorted(js.index("label: '"+name+"'") for name in names)
    for name in names:
        if name=='检测算法管理': page='plugins.html'
        elif name=='软件性能监控': page='monitor.html'
        else: continue
        assert page in js


def test_fault_plugins_discoverable_not_only_listed():
    names={x['name'] for x in v2_fault_type_catalog()}
    assert {'bias','drift','stuck','spike','break','noise','periodic'}<=names
    seq=[{'timestamp':str(i),'value':12.0} for i in range(7)]
    output=_apply_fault_type('periodic',seq,0.4,{'frequency':2.0})
    assert len(output)==7
    assert any(abs(y-12)>0.001 for y in output)
    assert _apply_fault_type('noise',seq,0.4,{})==_apply_fault_type('noise',seq,0.4,{})
    assert _apply_fault_type('break',seq,0.4,{})==[None]*7
    with pytest.raises(ValueError,match='未声明'):
        _apply_fault_type('periodic',seq,0.4,{'undocumented':1})
    with pytest.raises(ValueError,match='非负'):
        _apply_fault_type('noise',seq,-2,{})


def test_ui_and_api_expose_plugin_type_schema():
    html=(ROOT/'web/visualization/simulation.html').read_text(encoding='utf-8')
    js=(ROOT/'web/visualization/simulation-v2.js').read_text(encoding='utf-8')
    routes=(ROOT/'scripts/server/handlers.py').read_text(encoding='utf-8')
    assert 'simFaultPluginParams' in html and 'simFaultPluginHelp' in html
    assert '/api/v2/simulation/fault-types' in js and '/api/v2/simulation/fault-types' in routes
    assert 'plugin_params' in js


def test_installed_custom_fault_full_v2_lifecycle(tmp_path, monkeypatch):
    """A YAML-installed fault type actually changes data, then disappears on uninstall."""
    import sqlite3
    from src.fault_plugins import registry
    from scripts.rebuild_telemetry_v2 import CANON_SCHEMA
    from scripts.server.telemetry_v2_wide_import import import_wide_csv
    from scripts.server.v2_simulation_injection import (
        v2_source_catalog, preview_v2_injection, save_v2_injection, delete_injection, SIM_NAME,
    )
    root=tmp_path
    folder=root/'datasets';folder.mkdir()
    main_db=folder/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(main_db) as con:
        con.executescript(CANON_SCHEMA)
        con.execute("INSERT INTO v2_meta(key,value) VALUES('schema_version','2')")
    csv='time_s,label,sensor\n'+''.join(f'{i},0,100\n' for i in range(40))
    import_wide_csv(main_db,{
        'namespace':'run:demo','model':'MODEL-1','satellite_instance':'',
        'subsystem':'adcs','device':'gyro','variable':'sensor','unit':'rad/s',
        'role':'train','time_domain':'numeric_unspecified','time_reference':'run:demo',
        'time_unit':'s','filename':'demo.csv','csv':csv,
        'column_metadata':{'sensor':{'subsystem':'adcs','device':'gyro','variable':'sensor','unit':'rad/s'}},
    }, commit=True)
    cfgdir=root/'configs'/'fault_types';cfgdir.mkdir(parents=True)
    (cfgdir/'loss.py').write_text('''from src.fault_plugins.base import BaseFaultType\nclass LossFault(BaseFaultType):\n    def inject(self,data,config):\n        factor=config["factor"]\n        return self.apply_to_range(data,config,lambda row:row["value"]*(1-factor))\n''',encoding='utf-8')
    descriptor=cfgdir/'loss.yaml'
    descriptor.write_text('''name: test_loss\nlabel: 功率损失\nclass_path: loss.LossFault\nconfig_schema:\n  - name: factor\n    label: 损失比例\n    type: float\n    default: 0.25\n    min: 0\n    max: 1\n''',encoding='utf-8')
    monkeypatch.setattr(registry,'DEFAULT_CONFIG_DIR',cfgdir)
    try:
        assert 'test_loss' in {t['name'] for t in v2_fault_type_catalog()}
        catalog=v2_source_catalog(root)
        source=catalog['items'][0]
        config={'source_id':source['source_id'],'channel_id':source['channel_id'],
                'fault_type':'test_loss','onset_s':12,'max_points':30,'magnitude':0.2,
                'plugin_params':{'factor':0.25}}
        before=main_db.read_bytes()
        preview=preview_v2_injection(root,config)
        assert preview['points'][11]['value']==100
        assert preview['points'][12]['value']==75
        assert not (folder/SIM_NAME).exists()
        saved=save_v2_injection(root,{**config,'preview_sha256':preview['preview_sha256']})
        assert saved['status']=='success'
        with sqlite3.connect(folder/SIM_NAME) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()==('ok',)
            assert db.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==30
        assert delete_injection(root,saved['id'])['status']=='deleted'
        assert main_db.read_bytes()==before
        descriptor.unlink()
        assert 'test_loss' not in {t['name'] for t in v2_fault_type_catalog()}
        with pytest.raises(ValueError,match='不合法'):
            preview_v2_injection(root,config)
    finally:
        monkeypatch.undo()
        registry.discover_fault_types()


def test_fault_types_available_over_authorized_http(tmp_path,monkeypatch):
    import json
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen
    import scripts.visualization_server as main
    from tests.http_test_handler import AuthorizedTestHandler
    monkeypatch.setattr(main,'ROOT',tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/v2/simulation/fault-types',timeout=8) as response:
            assert response.status==200
            obj=json.loads(response.read())
        assert {'bias','periodic','noise','break'}<={t['name'] for t in obj['items']}
    finally:
        server.shutdown();worker.join(timeout=5);server.server_close()
