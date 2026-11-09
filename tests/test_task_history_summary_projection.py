from scripts.server.task_runtime import build_task_history_summary


class FakeRuntime:
    def __init__(self):
        self.items = [
            {'status': 'running'}, {'status': 'cancelling'},
            {'status': 'completed'},
            {'status': 'completed', 'attention': {'message': 'archive pending'}},
            {'status': 'failed'}, {'status': 'cancelled'}, {'status': 'interrupted'},
        ]
        self.calls = []

    def list_public_task_statuses(self, limit, include_all=False):
        self.calls.append((limit, include_all))
        return self.items[:limit]

    def count_public_task_statuses(self, include_all=False):
        assert include_all is True
        return 103


def test_summary_uses_one_recent_window_and_attention_is_not_double_counted():
    runtime = FakeRuntime()
    summary = build_task_history_summary(runtime, 50)
    assert runtime.calls == [(50, True)]
    assert summary['scope'] == 'latest_50'
    assert summary['sample_count'] == 7
    assert summary['total'] == 103
    assert summary['counts'] == {
        'running': 2, 'completed': 2, 'failed': 1,
        'cancelled': 1, 'interrupted': 1, 'attention': 1,
    }
    assert summary['attention_is_completed_subset'] is True


def test_summary_clamps_window():
    runtime = FakeRuntime()
    summary = build_task_history_summary(runtime, 10000)
    assert runtime.calls == [(100, True)]
    assert summary['scope'] == 'latest_100'
