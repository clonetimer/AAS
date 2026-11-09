"""Phase 35 POST domain: handle_extension_post; HTTP output semantics preserved."""
from __future__ import annotations
import json
from scripts.server.handler_plugins import preview_plugin, install_plugin, uninstall_plugin

def handle_extension_post(self, parsed, main, body):
    if parsed.path == "/api/preview-algorithm":
        try:
            # Handle multipart form data
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' in content_type:
                # Parse multipart form data
                boundary = content_type.split('boundary=')[1]
                form_data = main.parse_multipart_form(body, boundary)
                file_content = form_data.get('file', b'').decode('utf-8-sig')
            else:
                raise ValueError("Invalid content type")

            result = main.preview_algorithm(file_content)
            return main.json_response(self, result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    if parsed.path == "/api/dependencies/install":
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
            detector_name = payload.get("detector_name")

            if detector_name:
                # 安装指定检测器的依赖
                success, message = main.install_detector_dependencies(detector_name)
                return main.json_response(self, {
                    "status": "success" if success else "error",
                    "detector": detector_name,
                    "message": message
                })
            else:
                # 安装所有缺失依赖
                results = main.install_all_missing_dependencies()
                return main.json_response(self, {"results": results})
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    # 依赖管理 - 安装指定检测器的依赖
    if parsed.path == "/api/install-dependency":
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
            detector_name = payload.get("detector", "")
            if not detector_name:
                return main.json_response(self, {"status": "error", "message": "detector 参数不能为空"}, 400)
            success, message = main.install_detector_dependencies(detector_name)
            return main.json_response(self, {
                "status": "success" if success else "error",
                "message": message
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    # 依赖管理 - 一键安装所有缺失依赖
    if parsed.path == "/api/install-all-dependencies":
        try:
            results = main.install_all_missing_dependencies()
            return main.json_response(self, {
                "status": "success",
                "results": {k: {"success": v[0], "message": v[1]} for k, v in results.items()}
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    if parsed.path == "/api/log-action":
        try:
            import datetime
            log_data = json.loads(body.decode("utf-8") or "{}")
            log_dir = main.ROOT / "outputs" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            # 按日期生成日志文件
            today = datetime.datetime.now().strftime("%Y%m%d")
            log_file = log_dir / f"frontend_actions_{today}.log"

            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"[{log_data.get('timestamp', '')}] {log_data.get('type', '')}: {log_data.get('message', '')}\n")
                if log_data.get('data'):
                    f.write(f"  数据: {json.dumps(log_data['data'], ensure_ascii=False)}\n")

            return main.json_response(self, {"status": "success"})
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    if parsed.path == "/api/import-algorithm":
        try:
            content_type = self.headers.get('Content-Type', '')

            # 处理 JSON 请求（只有文件名的情况）
            if 'application/json' in content_type:
                data = json.loads(body.decode('utf-8-sig'))
                config_filename = data.get('config_filename', '')
                code_filename = data.get('code_filename', '')

                # 验证至少有一个文件名
                if not config_filename and not code_filename:
                    return main.json_response(self, {"error": "配置文件或代码文件至少需要一个"}, 400)

                # 导入配置和代码（只有文件名，没有文件内容）
                result = main.import_algorithm_separate(b'', config_filename, b'', code_filename)
                return main.json_response(self, result)

            # 处理 multipart 表单（有文件上传的情况）
            elif 'multipart/form-data' in content_type:
                # Parse multipart form data
                boundary = content_type.split('boundary=')[1]
                form_data = main.parse_multipart_form(body, boundary)

                # 获取配置和代码文件
                config_file = form_data.get('config_file', b'')
                config_filename = form_data.get('config_filename', '')
                code_file = form_data.get('code_file', b'')
                code_filename = form_data.get('code_filename', '')

                # 验证至少有一个文件
                if not config_file and not code_file and not config_filename and not code_filename:
                    return main.json_response(self, {"error": "配置文件或代码文件至少需要一个"}, 400)

                # 导入配置和代码
                result = main.import_algorithm_separate(config_file, config_filename, code_file, code_filename)
                return main.json_response(self, result)
            else:
                raise ValueError("Invalid content type")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    if parsed.path == "/api/plugins/native-build":
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
            from core.native_build import compile_native_source
            result = compile_native_source(
                main.ROOT,
                ptype=payload.get("type"),
                language=payload.get("language"),
                source_filename=payload.get("source_filename", ""),
                source_content=payload.get("source_content", ""),
                toolchain=payload.get("toolchain", "auto"),
            )
            return main.json_response(self, result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    if parsed.path == "/api/plugins/preview":
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
            result = preview_plugin(
                main.ROOT,
                payload.get("type"),
                payload.get("yaml_filename", ""),
                payload.get("yaml_content", ""),
                payload.get("code_filename"),
                payload.get("code_content"),
                language=payload.get("language"),
                library_filename=payload.get("library_filename"),
                library_content_base64=payload.get("library_content_base64"),
                source_filename=payload.get("source_filename"),
                source_content=payload.get("source_content"),
            )
            return main.json_response(self, result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    if parsed.path == "/api/plugins/install":
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
            result = install_plugin(
                main.ROOT,
                payload.get("type"),
                payload.get("yaml_filename", ""),
                payload.get("yaml_content", ""),
                payload.get("code_filename"),
                payload.get("code_content"),
                language=payload.get("language"),
                library_filename=payload.get("library_filename"),
                library_content_base64=payload.get("library_content_base64"),
                source_filename=payload.get("source_filename"),
                source_content=payload.get("source_content"),
            )
            return main.json_response(self, result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

    if parsed.path == "/api/plugins/uninstall":
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
            result = uninstall_plugin(
                main.ROOT,
                payload.get("type"),
                payload.get("name", ""),
                force=payload.get("force", False),
            )
            return main.json_response(self, result)
        except FileNotFoundError as exc:
            return main.json_response(self, {"error": str(exc)}, 404)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)
