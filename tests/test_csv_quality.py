"""P3-B: inspect true CSV row quality without changing the source."""
from scripts.server.csv_quality import analyze_csv_rows


def test_quality_counts_and_actual_csv_line_numbers():
    rows = [
        {'采样时刻': '0', '速度': '1.5', '温度': '20', 'fault_state': '0'},
        {'采样时刻': '1', '速度': '', '温度': 'NaN', 'fault_state': '1'},
        {'采样时刻': '1', '速度': 'bad', '温度': 'Inf', 'fault_state': 'unexpected'},
        {'采样时刻': '0.5', '速度': '4', '温度': '18', 'fault_state': ''},
        {'采样时刻': '10', '速度': '5', '温度': '19', 'fault_state': '0'},
    ]
    quality = analyze_csv_rows(['采样时刻', '速度', '温度', 'fault_state'], rows,
                               time_column='采样时刻', label_column='fault_state')
    assert quality['row_count'] == 5
    assert quality['missing_by_column'] == {'速度': 1, 'fault_state': 1}
    assert quality['non_numeric_by_column'] == {'速度': 1}
    assert quality['nonfinite_by_column'] == {'温度': 2}
    assert quality['duplicate_times'] == 1
    assert quality['time_reversals'] == 1
    assert quality['label_counts'] == {'0': 2, '1': 1, 'unknown': 2}
    assert quality['gaps_gt_3x_median'] == 1
    assert any(
        x['line'] == 4 and x['frame'] == 3 and x['column'] == '采样时刻'
        and x['code'] == 'duplicate_time' for x in quality['issues'])
    assert any(x['line'] == 3 and x['frame'] == 2 and x['column'] == '温度' and x['code'] == 'nonfinite' for x in quality['issues'])
    assert '速度' not in quality['numeric_columns'], 'a column with non-numeric data must not be silently recommended'
    assert '温度' not in quality['numeric_columns'], 'infinite values must not be silently accepted'


def test_quality_has_bounded_issues_and_does_not_mutate_records():
    row = {'time_s': '0', 'temperature': '', 'label': '0'}
    rows = [dict(row) for _ in range(25)]
    before = [dict(x) for x in rows]
    quality = analyze_csv_rows(['time_s', 'temperature', 'label'], rows, max_issues=3)
    assert quality['row_count'] == 25
    assert quality['issue_count'] > 3
    assert len(quality['issues']) == 3
    assert quality['issues_truncated'] is True
    assert quality['issues'][0]['frame'] == 1 and quality['issues'][0]['line'] == 2
    assert rows == before


def test_unlabeled_and_nonstandard_roles_are_not_inferred_as_truth():
    rows = [{'time_custom': '2026-11-06T00:00:00Z', 'value': '2', 'state': '0'},
            {'time_custom': '2026-11-06T00:00:01Z', 'value': '3', 'state': '1'}]
    quality = analyze_csv_rows(['time_custom', 'value', 'state'], rows, time_column='time_custom', label_column='')
    assert quality['label_counts'] == {'0': 0, '1': 0, 'unknown': 2}
    assert quality['time_reversals'] == 0
    assert quality['sample_period_median_seconds'] == 1.0
    assert quality['numeric_columns'] == ['value', 'state']
    assert quality['label_column'] is None


def test_malformed_row_length_is_located_without_crashing():
    rows = [['0', '2'], ['1', '3', 'extra'], ['2']]
    quality = analyze_csv_rows(['time_s', 'speed'], rows)
    assert quality['row_count'] == 3
    assert sum(x['code'] == 'column_count' for x in quality['issues']) == 2
    assert quality['issue_count'] >= 2
