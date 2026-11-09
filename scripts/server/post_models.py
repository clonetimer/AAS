"""Phase 35 POST domain: handle_model_post; HTTP output semantics preserved."""
from __future__ import annotations
import json
from scripts.server.v2_database_config import active_name
from scripts.server.v2_user_csv_workspace import database_reference

def handle_model_post(self, parsed, main, body):
    if parsed.path == "/api/models":
        try:
            import datetime
            payload = json.loads(body.decode("utf-8") or "{}")

            experiment_id = payload.get("experiment_id")
            model_name = payload.get("model_name")
            model_desc = payload.get("model_desc", "")
            algorithm = payload.get("algorithm")
            dataset = payload.get("dataset")
            table_name = payload.get("table_name")
            file_path = payload.get("file_path") or ""
            metrics = payload.get("metrics", {})
            config = payload.get("config", {})

            if not experiment_id or not model_name or not algorithm:
                return main.json_response(self, {"error": "experiment_id, model_name, and algorithm are required"}, 400)

            # 确保 file_path 不为 null
            if not file_path:
                file_path = f"models/model_{experiment_id}"
            managed_model_dir = main.resolve_managed_model_dir(file_path, must_exist=True)
            saved = main.load_model_config(str(managed_model_dir))
            saved_contract = (saved.get('config') or {}).get('data_contract') or {}
            provenance=saved_contract.get('data_source_provenance') or {}
            allowed_dataset=(dataset == active_name() and provenance.get('type','history')=='history') or (
                dataset == database_reference() and provenance.get('type')=='user_csv'
                and provenance.get('database_ref')==database_reference())
            if (not (saved_contract.get('version') == 7 and saved_contract.get('v2_run_selection'))
                    or not allowed_dataset or table_name != 'v2_channels'):
                return main.json_response(self, {'error':'仅支持发布 V2 逻辑参量训练模型，不能发布旧物理表模型'},410)
            file_path = managed_model_dir.relative_to(main.ROOT).as_posix()

            result = main.create_model(
                experiment_id=experiment_id,
                model_name=model_name,
                model_desc=model_desc,
                algorithm=algorithm,
                dataset=dataset,
                table_name=table_name,
                file_path=file_path,
                metrics=metrics,
                config=config,
            )
            return main.json_response(self, result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)
