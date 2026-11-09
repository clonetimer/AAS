"""Validate parameters for the V2 training API against the detector's registered schema.

Parameters belong to the detector implementation, not to the evaluation system.
This layer validates request shape and published schema constraints only.
"""
from __future__ import annotations

import math

from detectors.registry import get_available_algorithms, get_detector_config_schema


def validate_algorithm_configuration(algorithm, params):
    if not isinstance(algorithm, str) or not algorithm.strip():
        raise ValueError('必须选择检测算法')
    options = {item['name']: item for item in get_available_algorithms()}
    if algorithm not in options:
        raise ValueError('未知检测算法，必须在已注册算法中选择')
    deps = options[algorithm].get('dependencies') or {}
    if isinstance(deps, dict) and not deps.get('installed', True):
        raise ValueError('检测算法依赖未安装：' + ', '.join(deps.get('missing') or []))
    if not isinstance(params, dict):
        raise ValueError('algorithm_params 必须为对象')
    schema = get_detector_config_schema(algorithm)
    definitions = {p['name']: p for p in schema.get('parameters', [])}
    unknown = set(params) - definitions.keys()
    if unknown:
        raise ValueError('未知模型参数：' + ', '.join(sorted(str(v) for v in unknown)))
    validated = {}
    for name, value in params.items():
        definition = definitions[name]
        kind = definition.get('type', 'str')
        if kind == 'int':
            valid = type(value) is int
        elif kind == 'float':
            valid = type(value) in (int, float) and math.isfinite(value)
        elif kind == 'bool':
            valid = type(value) is bool
        elif kind == 'str':
            valid = isinstance(value, str)
        elif kind == 'list':
            valid = isinstance(value, list) and len(value) <= 128 and all(
                isinstance(item, (str, int, float)) and not isinstance(item, bool) and
                (not isinstance(item, (int, float)) or math.isfinite(item)) for item in value)
        else:
            # Custom detector type declarations must be extended explicitly,
            # never coerced silently into a different parameter type.
            raise ValueError(f'不支持的参数类型：{name} ({kind})')
        if not valid:
            raise ValueError(f'模型参数 {name} 的类型不符合 {kind}')
        if kind in ('int', 'float'):
            if 'min' in definition and value < definition['min']:
                raise ValueError(f'模型参数 {name} 低于最小值 {definition["min"]}')
            if 'max' in definition and value > definition['max']:
                raise ValueError(f'模型参数 {name} 高于最大值 {definition["max"]}')
        if definition.get('options') and value not in definition['options']:
            raise ValueError(f'模型参数 {name} 不在算法允许的选项中')
        if name == 'hidden_sizes' and (not value or any(type(v) is not int or v < 1 for v in value)):
            raise ValueError('隐藏层尺寸必须是正整数列表')
        validated[name] = value
    # Inapplicable decision/threshold options should not be accepted unseen.
    effective = {**schema.get('defaults', {}), **validated}
    for name, value in validated.items():
        guard = definitions[name].get('visible_when')
        if guard and effective.get(guard.get('name')) != guard.get('value'):
            raise ValueError(f'模型参数 {name} 与当前算法判定模式不兼容')
    return validated
