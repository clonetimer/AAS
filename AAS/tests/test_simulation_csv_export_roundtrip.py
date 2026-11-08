from __future__ import annotations

import csv
import io

from scripts.server import v2_csv_workspace as workspace


def test_same_parameter_three_faults_export_and_reimport_roundtrip(tmp_path):
    raw = (
        b"time_s,gyro_x,voltage,label\n"
        b"0,1,28,0\n1,2,28,0\n2,3,27,1\n3,4,27,1\n"
    )
    source = workspace.import_csv(
        tmp_path, raw, "unit.csv", csv_encoding="utf-8",
        csv_delimiter=",", format_confirmed=True)
    current = source
    for fault_type, start, end, magnitude in (
        ("bias", 2, 3, 5.0),
        ("drift", 1, 4, 4.0),
        ("stuck", 3, 4, 0.0),
    ):
        config = {
            "dataset_id": current["id"], "column": "gyro_x",
            "fault_type": fault_type, "start_frame": start,
            "end_frame": end, "magnitude": magnitude,
        }
        preview = workspace.preview_fault(tmp_path, config)
        current = workspace.save_fault(
            tmp_path, {**config, "preview_sha256": preview["preview_sha256"]})

    latest = workspace.get_dataset(tmp_path, current["id"])
    assert latest["version"] == 3
    assert [event["fault_type"] for event in latest["events"]] == [
        "bias", "drift", "stuck"]
    assert {event["column"] for event in latest["events"]} == {"gyro_x"}

    for safe in (False, True):
        exported = workspace.export_csv(
            tmp_path, current["id"], spreadsheet_safe=safe)
        rows = list(csv.DictReader(io.StringIO(exported.decode("utf-8-sig"))))
        assert len(rows) == 4
        assert [int(row["aas_frame"]) for row in rows] == [1, 2, 3, 4]
        assert all(row["aas_fault_events"] for row in rows)

        # Re-import only the original logical data columns. Export audit columns
        # (frame, synthetic fault flag and event IDs) remain explicit metadata.
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["time_s", "gyro_x", "voltage", "label"])
        for row in rows:
            writer.writerow([row["time_s"], row["gyro_x"],
                             row["voltage"], row["label"]])
        restored = workspace.import_csv(
            tmp_path, stream.getvalue().encode("utf-8"),
            f"roundtrip-{safe}.csv", csv_encoding="utf-8",
            csv_delimiter=",", format_confirmed=True)
        assert restored["frame_count"] == 4
