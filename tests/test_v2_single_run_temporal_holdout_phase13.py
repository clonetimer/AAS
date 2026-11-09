"""Single healthy run supports train/validation chronology with a held-out gap."""
from scripts.core.data_loader import DataLoader
from scripts.server.v2_run_dataset import validate_training_selection
from tests.test_v2_run_subset_phase8 import labeled, choose


def test_temporal_holdout_when_only_one_run(labeled):
    import sqlite3
    with sqlite3.connect(labeled) as c:
        for cid in [1, 2]:
            for t in range(6,40):
                v=20+t+(cid-1)
                c.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,
                    timestamp_raw,time_domain,time_sort_num,value_num,value_raw_json,label_int,
                    label_raw_json,raw_row_json) VALUES (?,?,?,?,'numeric_unspecified',?,?,?,?,?,?)''',
                    (cid,cid,t,str(t),float(t),float(v),str(v),0,'0','{}'))
    select=choose([1])
    m=validate_training_selection(labeled,{'dataset_selection':select,'train_ratio':.75})
    assert m['normal_count']==40
    loader=DataLoader(labeled,train_ratio=.75,feature_columns=['value','source_2'])
    loader.v2_run_selection=select
    loader.v2_role='train'
    bundle=loader.load_and_split()
    assert bundle.metadata['v2_run_manifest']['internal_validation_type']=='temporal_holdout_not_independent_run'
    assert bundle.metadata['v2_run_manifest']['embargo_samples']>=1
    assert bundle.mode=='v2_single_run_temporal_embargo'
