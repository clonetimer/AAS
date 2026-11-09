"""Role-safe selection of physical fragments behind a virtual telemetry channel."""
from __future__ import annotations

from typing import Any

from scripts.server import series_catalog as catalog


def normalize_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError('logicalSeries 必须指定参量身份对象')
    identity = {}
    for key in catalog.IDENTITY:
        raw = value.get(key)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f'逻辑参量缺少 {key}')
        identity[key] = raw.strip()
    return identity


def select_role_tables(table_items: list[dict[str, Any]], identity: dict[str, str],
                       role: str, *, require_both_labels: bool = True) -> list[str]:
    """Explicit and strict role validation: never concatenate train and test."""
    if role not in ('train', 'test'):
        raise ValueError('不支持的数据用途')
    chosen = []
    test_normal = test_fault = 0
    for row in table_items:
        if identity not in (row.get('hierarchy') or []):
            continue
        if str(row.get('name', '')).lower().endswith('_clean'):
            continue
        if row.get('is_single_measurement') is not True:
            raise ValueError(f"{row.get('name')} 与参量匹配，但不是标准单参量表")
        if row.get('data_role') != role:
            # A physical fragment from another role must never contaminate this run.
            continue
        if not {'timestamp', 'value', 'label'} <= set(row.get('columns') or []):
            raise ValueError(f"{row.get('name')} 缺少必要字段")
        if role == 'train':
            # Training remains strictly normal-only per physical fragment.
            if row.get('role_valid') is False:
                continue
        else:
            # A time shard may contain only normal *or* only fault samples.
            # Validate the union, while still forbidding unknown labels.
            normal = int(row.get('normal_count') or 0)
            fault = int(row.get('fault_count') or 0)
            if int(row.get('unknown_label_count') or 0) or normal + fault == 0:
                continue
            test_normal += normal
            test_fault += fault
        chosen.append(row['name'])
    if not chosen:
        raise ValueError(f'没有找到可用的{role}分片')
    if role == 'test' and require_both_labels and not (test_normal > 0 and test_fault > 0):
        raise ValueError('跨表测试集整体必须同时包含正常与故障样本')
    return sorted(chosen)
