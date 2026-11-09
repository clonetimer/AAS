# G0-4 follow-up — historical simulator plugin severity semantics

Date: 2026-11-08
Local source commit: c032415
Remote patch commit: 515afa0

## Change

The historical simulation page already receives plugin `formula`, `description`, `uses_severity` and `config_schema` metadata from the fault-type registry. Plugin-defined parameter controls are generated from the schema and each field's description is rendered beside its control. This increment corrects the remaining ambiguous severity explanation:

- `s` is explicitly described as a relative amplitude coefficient, not a physical engineering unit.
- The explanation says the actual delta depends on the selected fault formula and original signal value.
- The old misleading “approximately N%” rendering is removed because it can imply a universal percent-of-range interpretation that is not guaranteed by every plugin.
- Formula, fault description and schema-defined parameter help remain visible.

## Verification

- `node tests/simulation_plugin_fault_guidance.cjs` — PASS.
- `node tests/simulation_csv_fault_guidance.cjs` — PASS.
- `node --check web/visualization/simulation.js` — PASS.
- `git diff --check` — PASS.

This is a source-contract regression, not live browser/API or Windows acceptance. Third-party plugin units and parameter meaning remain the plugin author's responsibility; the UI does not invent units.

## Remaining G0

G0-5 page draft continuity and G0-6 task-center status convergence remain. G0-2 live browser route and accessibility acceptance and P3-H Windows 10/11 acceptance remain outstanding. PR stays Draft.
