"""Feature-schema contract for V2 run-subset models.

``feature_columns`` in the data contract is positional (``value``, ``source_2``,
...), so by itself it cannot say *which* telemetry parameter sits in a column.
Every check built on those names is therefore tautological. This module adds the
missing identity layer:

* an ordered list of parameter keys,
* an explicit column -> key binding,
* the physical semantics (subsystem / device / variable / unit) of each column,
* a digest over all three, so a model can be matched to one exact input layout.

Old (R3.8) models carry no ``feature_schema``; they stay loadable and are
reported as ``legacy_unbound`` instead of being silently trusted.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from scripts.server.execution_provenance import canonical_bytes, sha256_bytes

SCHEMA_REVISION = 1
ORDER_POLICY = 'lexicographic'
ORDER_POLICY_AS_TRAINED = 'as_trained'
INTEGRITY_BOUND = 'schema_bound'
INTEGRITY_LEGACY = 'legacy_unbound'
INTEGRITY_INVALID = 'schema_invalid'


def canonical_feature_keys(keys: Iterable[str]) -> list[str]:
    """Deterministic column order, independent of the caller's click order."""
    return sorted(keys)


def column_bindings(keys: Sequence[str]) -> dict[str, str]:
    ordered = list(keys)
    if not ordered:
        raise ValueError('特征 schema 至少需要一个参量')
    return {'value': ordered[0],
            **{f'source_{index}': key for index, key in enumerate(ordered[1:], 2)}}


def build_feature_schema(keys: Sequence[str], semantics: Any = None, *,
                         order_policy: str = ORDER_POLICY) -> dict[str, Any]:
    ordered = list(keys)
    if len(set(ordered)) != len(ordered):
        raise ValueError('特征 schema 不能包含重复参量')
    if semantics is not None:
        semantics = [list(item) for item in semantics]
        if len(semantics) != len(ordered):
            raise ValueError('特征语义数量与参量数量不一致')
    return {'revision': SCHEMA_REVISION,
            'keys': ordered,
            'bindings': column_bindings(ordered),
            'semantics': semantics,
            'order_policy': order_policy,
            'min_samples_per_window': None}


def identity_core(schema: dict[str, Any]) -> dict[str, Any]:
    """Fields that define the input identity; production metadata stays out.

    ``order_policy`` / ``min_samples_per_window`` describe how the artifact was
    produced, not what the columns mean, so they must not invalidate a model.
    """
    return {'revision': schema.get('revision'), 'keys': schema.get('keys'),
            'bindings': schema.get('bindings'), 'semantics': schema.get('semantics')}


def schema_digest(schema: dict[str, Any]) -> str:
    return sha256_bytes(canonical_bytes(identity_core(schema)))


def attest_feature_schema(contract: dict[str, Any], *, keys: Sequence[str],
                          semantics: Any = None,
                          order_policy: str = ORDER_POLICY) -> dict[str, Any]:
    """Write a self-consistent schema + digest into a data contract."""
    schema = build_feature_schema(keys, semantics, order_policy=order_policy)
    contract['feature_schema'] = schema
    contract['schema_sha256'] = schema_digest(schema)
    return schema


def stored_feature_schema(contract: dict[str, Any] | None) -> tuple[dict[str, Any], str] | None:
    """Return ``(schema, digest)`` or ``None`` for legacy contracts."""
    if not isinstance(contract, dict):
        return None
    schema = contract.get('feature_schema')
    digest = contract.get('schema_sha256')
    if not isinstance(schema, dict) or not isinstance(digest, str) or len(digest) != 64:
        return None
    return schema, digest


def verify_stored_feature_schema(contract: dict[str, Any] | None) -> dict[str, Any] | None:
    """Integrity-check a persisted schema against its own digest and columns.

    Returns the schema for bound models, ``None`` for legacy models, and raises
    when a stored schema no longer matches its digest or the column layout.
    """
    stored = stored_feature_schema(contract)
    if stored is None:
        return None
    schema, digest = stored
    if schema_digest(schema) != digest:
        raise ValueError('模型特征 schema 指纹与内容不符，模型配置可能已被改动')
    columns = [str(name) for name in (contract.get('feature_columns') or [])]
    bindings = schema.get('bindings')
    keys = schema.get('keys')
    if not isinstance(bindings, dict) or not isinstance(keys, list) or not keys:
        raise ValueError('模型特征 schema 缺少列绑定或参量顺序')
    if columns and list(bindings) != columns:
        raise ValueError('模型特征 schema 的列绑定与 feature_columns 不一致')
    if bindings != column_bindings(keys):
        raise ValueError('模型特征 schema 的列绑定与参量顺序不一致')
    return schema


def describe_schema_difference(saved: dict[str, Any], live: dict[str, Any]) -> str:
    saved_keys = list(saved.get('keys') or [])
    live_keys = list(live.get('keys') or [])
    if saved_keys != live_keys:
        if len(saved_keys) != len(live_keys):
            return f'参量数量变化：模型 {len(saved_keys)} 列，本次 {len(live_keys)} 列'
        for index, (left, right) in enumerate(zip(saved_keys, live_keys), 1):
            if left != right:
                return f'第 {index} 列参量变化：模型={left}，本次={right}'
        return '参量顺序与模型训练时不一致'
    saved_semantics = saved.get('semantics')
    live_semantics = live.get('semantics')
    if isinstance(saved_semantics, list) and isinstance(live_semantics, list):
        if len(saved_semantics) != len(live_semantics):
            return '参量物理含义记录数量与模型不一致'
        for key, left, right in zip(saved_keys, saved_semantics, live_semantics):
            if list(left) != list(right):
                return (f'参量 {key} 的物理含义变化：模型={list(left)}，'
                        f'本次={list(right)}')
    return '特征 schema 与模型训练时不一致'


def assert_feature_schema_matches(saved: dict[str, Any] | None,
                                  live: dict[str, Any] | None) -> None:
    """Raise when the live input layout cannot be the layout the model was fit on."""
    if saved is None:
        return
    if not isinstance(live, dict):
        raise ValueError('无法从本次选择重建特征 schema，禁止用未核对的列序评估模型')
    if schema_digest(saved) != schema_digest(live):
        raise ValueError('评估特征与已发布模型不符：'
                         + describe_schema_difference(saved, live))
