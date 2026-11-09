"""Cross-domain evaluation is inferred from data, never a user permission gate."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from scripts import visualization_server as main
from tests.http_test_handler import AuthorizedTestHandler


def test_domain_is_inferred_without_opt_in_and_does_not_disable_role_or_dimension_checks():
    class Detector:
        def score(self, samples):
            return samples
    contract = {
        'source_tables':['DEMO_train_temperature','DEMO_train_vibration'],
        'alignment':{'source_signatures':{
            'DEMO_train_temperature':{'model':'DEMO','subsystem':'ADCS','device':'demo_unit','variable':'temperature'},
            'DEMO_train_vibration':{'model':'DEMO','subsystem':'ADCS','device':'demo_unit','variable':'vibration'},
        }},
        'feature_columns':['value','source_2'],
        'model_input_scaling':{'enabled':False},
    }
    server = ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    base=f'http://127.0.0.1:{server.server_port}/api/evaluate-model'
    def post(**override):
        payload={
            'model_id': 8, 'dataset': 'datasets/history_sample.db',
            'table': 'DEMO_test_current',
            'tables': ['DEMO_test_current','DEMO_test_vibration'],
            **override,
        }
        request=Request(base,data=json.dumps(payload).encode(),method='POST',headers={'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=10) as response:
                return response.status,json.load(response)
        except HTTPError as exc:
            return exc.code,json.load(exc)
    try:
        with (patch.object(main,'get_model',return_value={'algorithm':'zscore','file_path':'models/test'}),
              patch.object(main,'resolve_managed_model_dir',return_value='models/test'),
              patch.object(main,'load_model',return_value=Detector()),
              patch.object(main,'load_model_config',return_value={'config':{'data_contract':contract}}),
              patch.object(main,'run_evaluation',return_value={'result':{'metrics':{}}}) as scorer,
              patch.object(main,'update_model_evaluation')):
            code, result = post()
            assert code == 200, result
            assert result['input_contract']['evaluation_mode'] == 'cross_domain'
            assert result['input_contract']['evaluation_mapping']['DEMO_train_temperature']=='DEMO_test_current'
            assert result['input_contract']['domain_differences'][0]['different_fields'] == ['variable']
            assert scorer.call_count == 1
            # Legacy flags do not control scientific semantics.
            code, result = post(cross_domain=False)
            assert code == 200, result
            assert result['input_contract']['evaluation_mode'] == 'cross_domain'
            assert post(tables=['DEMO_test_current'])[0] == 400
            assert post(tables=['DEMO_train_temperature','DEMO_test_vibration'],table='DEMO_train_temperature')[0] == 400
            assert post(cross_domain='true')[0] == 400
            assert scorer.call_count == 2
    finally:
        server.shutdown();worker.join(timeout=5);server.server_close()
