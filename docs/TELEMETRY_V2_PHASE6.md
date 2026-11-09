# AAS Telemetry V2 — Phase 6: dataset governance and fault events

Status: **candidate** (not stable). The public data model remains **V2 logical channels only**. There is **no legacy-table mode** in the user interface.

## Scope

- Dataset inventory grouped by `v2_channels.namespace` and confirmed/claimed `v2_segments.role`; distinguishes verified vs. unverified role provenance, quarantined segments, and unmapped historical rows.
- Quarantine/restore telemetry segments through a **soft state**; no `DROP TABLE`, `DELETE FROM v2_samples`, or silent sample-label edits.
- Store separate interval events for logical `channel_id + role + time_domain + reference + unit`, with **half-open `[start,end)` semantics**.
- Event labels `0=confirmed normal`, `1=fault`; non-overlapping intervals per channel and role. Train-role event labels must be normal.
- Independent V2 event-based metrics after evaluation, computed from the model's **actual predicted labels**. No detector thresholding/postprocessing rules are added by the platform.
- All state mutations require `confirm=true` and reasons where applicable, use a single SQLite transaction, and write an audit record with server-derived actor, timestamp, reason, prior/new revision.
- Browser standard view remains a logical parameter picker; all governance lives in an explicitly expanded advanced panel.

## Database

Three additive tables are initialized **upon the first successful governance write**, without changing `v2_meta.schema_version=2`:

| Table | Purpose |
|---|---|
| `v2_segment_governance` | `segment_id`, `active`, optimistic `revision`, `reason`, update timestamp |
| `v2_fault_events` | `channel_id`, `role`, time domain and clock, normalized interval endpoints, label, fault type, status, revision |
| `v2_governance_audit` | immutable action history with server-identified actor and request reason |

Existing rebuild databases without these extension tables remain readable in V2; reads **never auto-create schema**. An operator must back up their installed `datasets/aas_telemetry_v2.sqlite` before the first write. This is an additive schema extension, **not** a rewrite or migration of original legacy databases.

Quarantine affects V2 catalog visibility, chart reads and V2 training/evaluation loaders. Quarantined samples remain in `v2_samples`. A corrected CSV may reuse timestamps of quarantined rows. Restoring the old segment checks for conflicting timestamp values/labels and incompatible time domains or numeric clock references; on failure the segment remains quarantined. Revisions guard against concurrent stale updates.

Events are **independent expert annotations**. They do not overwrite row-wise ground truth `label_int`, do not automatically upgrade unverified data roles, and do not change how model inputs or detector decisions are produced. The event metrics only include intervals overlapping the actual evaluation window; missing normal intervals remain **unknown**, never implicitly normal. Numeric time units are converted to seconds for delay metrics.

## V2 APIs

| Method | Route | Input / result |
|---|---|---|
| GET | `/api/v2/telemetry/datasets` | Grouped active dataset summary; quarantine and unmapped counts |
| GET | `/api/v2/telemetry/segments?role=test&channel_id=7` | Includes quarantined segments and their `revision` |
| POST | `/api/v2/telemetry/segment/status` | `segment_id`, `active`, `reason`, `expected_revision`, `confirm:true` |
| GET | `/api/v2/telemetry/events?channel_id=7&role=test` | Current events |
| POST | `/api/v2/telemetry/events/create` | `channel_id`,`role`,`start`,`end`,`label`,`fault_type`,`note`,`confirm:true` |
| POST | `/api/v2/telemetry/events/retract` | `event_id`,`expected_revision`,`reason`,`confirm:true` |
| GET | `/api/v2/telemetry/governance/audit?limit=50` | Server-generated actor, UTC timestamp, old/new metadata |
| POST | `/api/v2/evaluation/run` | Existing model evaluation response plus `event_metrics` and `summary.result.event_overlay` |

An event cannot refer to an unverified data role, mixed time domains, unknown numeric clock unit/reference, an interval without real samples, or an overlapping active event. A retracted event remains in the audit trail and in the event table with `status='retracted'`.

## Verification

- `pytest -q tests/test_v2_governance_events_phase6.py` (new suite)
- `pytest -q tests/test_v2_governance_events_phase6.py tests/test_telemetry_v2_native_import.py tests/test_telemetry_v2_training_phase5b.py tests/test_telemetry_v2_browser_phase4.py tests/test_telemetry_v2_rebuild.py tests/test_v2_deprecated_routes_phase5b.py tests/test_dashboard_catalog_endpoints.py tests/test_event_scoring.py -k 'not authenticated_http_archive_freezes_event_annotations'`
- `node --check web/visualization/telemetry-v2-governance.js`
- Trial run on a **copy** of the reconstructed 150,420-row V2 database: event create, segment quarantine, catalog disappearance, integrity check, and audit read.

The old HTTP experiment/event test uses deliberately retired `/api/save-experiment` and `/api/fault-intervals` routes and is not a valid V2 success path. Keep it as an explicit migration backlog, not a hidden green test.

## Known limitations / upcoming work

- This stage does not resolve the 10,711 preserved historical records or automatically certify name-derived `train`/`test` roles.
- No destructive physical data deletion, role reassignment, stream preprocessing, or native injection workflow is provided.
- The result-report UI does not yet render the new event overlay as a graphical lane, although V2 evaluation returns snapshot and metrics.
- Browser automated end-to-end verification on a target installation and a full modernized test suite are still required.
- Updating files does not automatically alter the user's local database and nothing is merged into GitHub main.
