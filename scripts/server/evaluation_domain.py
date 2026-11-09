"""Explain the domain relationship of selected evaluation data without gating it.

Feature position and preprocessing contracts remain mandatory; telemetry
hierarchy names express semantics, not tensor compatibility.
"""
from __future__ import annotations

from typing import Any

FIELDS = ('model', 'subsystem', 'device', 'variable')


def classify_evaluation_mapping(
    training_sources: list[dict[str, Any]],
    chosen_tables: list[str],
    test_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Classify per-slot source/target identities, including unknown identities."""
    differences = []
    mapping = {}
    for index, target in enumerate(chosen_tables):
        source = training_sources[index] if index < len(training_sources) else {}
        source_name = source.get('table')
        expected = source.get('identity')
        actual_rows = test_catalog.get(target, {}).get('hierarchy') or []
        actual = actual_rows[0] if len(actual_rows) == 1 else None
        changed = ([field for field in FIELDS if str(expected.get(field)) != str(actual.get(field))]
                   if isinstance(expected, dict) and isinstance(actual, dict) else None)
        if source_name:
            mapping[source_name] = target
        differences.append({
            'position': index + 1,
            'source_table': source_name,
            'target_table': target,
            'source_identity': expected,
            'target_identity': actual,
            'different_fields': changed,
        })
    if any(item['different_fields'] for item in differences):
        mode = 'cross_domain'
    elif any(item['different_fields'] is None for item in differences) or not differences:
        mode = 'unverified_domain'
    else:
        mode = 'same_domain'
    return {'evaluation_mode': mode, 'evaluation_mapping': mapping,
            'domain_differences': differences}
