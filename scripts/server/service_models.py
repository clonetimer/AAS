"""Phase 34: cohesive domain services, split from the original services.py.

Import through ``scripts.server.services`` for compatibility, or use this module
directly for new code. No database schema or API change is introduced.
"""
from __future__ import annotations
from .service_shared import *  # noqa: F401,F403 - central paths and SQLite guard
# 辅助函数
# ------------------------------------------------------------------

def read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件。"""
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_model_db() -> None:
    """初始化 model.db，并保证模型表/索引始终存在。

    不能只按“数据库文件是否存在”判断是否初始化：空文件、被手工创建的
    model.db 或不完整恢复的数据库都可能存在但没有 models 表。
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(MODEL_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL UNIQUE,
                model_name TEXT NOT NULL,
                model_desc TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'published',
                algorithm TEXT NOT NULL,
                dataset TEXT NOT NULL,
                table_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published_at TEXT NOT NULL,
                last_evaluated_at TEXT,
                evaluation_count INTEGER DEFAULT 0,
                file_path TEXT NOT NULL,
                metrics_json TEXT DEFAULT '{}',
                config_json TEXT DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_models_status ON models(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_models_algorithm ON models(algorithm)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_models_experiment ON models(experiment_id)")
        conn.commit()


# ------------------------------------------------------------------
# 模型管理服务
# ------------------------------------------------------------------

def model_to_dict(row: tuple) -> dict:
    """将数据库行转换为模型字典"""
    return {
        "id": row[0],
        "experiment_id": row[1],
        "model_name": row[2],
        "model_desc": row[3],
        "status": row[4],
        "algorithm": row[5],
        "dataset": row[6],
        "table_name": row[7],
        "created_at": row[8],
        "published_at": row[9],
        "last_evaluated_at": row[10],
        "evaluation_count": row[11],
        "file_path": row[12],
        "metrics_json": row[13],
        "config_json": row[14],
    }


def get_models(status: Optional[str] = None, algorithm: Optional[str] = None, limit: int = 50) -> list[dict]:
    """获取模型列表"""
    ensure_model_db()
    with sqlite3.connect(MODEL_DB_PATH) as conn:
        query = "SELECT * FROM models WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if algorithm:
            query += " AND algorithm = ?"
            params.append(algorithm)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    return [model_to_dict(row) for row in rows]


def create_model(
    experiment_id: str,
    model_name: str,
    model_desc: str,
    algorithm: str,
    dataset: str,
    table_name: str,
    file_path: str,
    metrics: dict,
    config: dict,
) -> dict[str, Any]:
    """发布模型"""
    ensure_model_db()
    managed_model_dir = _resolve_model_dir(file_path, must_exist=True)
    validate_saved_model(managed_model_dir, algorithm, experiment_id=experiment_id)
    file_path = managed_model_dir.relative_to(_service_setting("PROJECT_ROOT")).as_posix()
    now = datetime.datetime.now().isoformat()
    with sqlite3.connect(MODEL_DB_PATH) as conn:
        conn.execute(
            """INSERT INTO models
               (experiment_id, model_name, model_desc, status, algorithm, dataset, table_name,
                created_at, published_at, file_path, metrics_json, config_json)
               VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                experiment_id, model_name, model_desc, algorithm, dataset, table_name,
                now, now, file_path, json.dumps(metrics, ensure_ascii=False),
                json.dumps(config, ensure_ascii=False),
            ),
        )
        conn.commit()

    # 更新对应的 summary.json 文件，标记模型已发布
    try:
        summary_path = _service_setting("PROJECT_ROOT") / "outputs" / f"experiment_{experiment_id}" / "reports" / "summary.json"
        if summary_path.exists():
            summary = read_json(summary_path)
            if summary.get("training"):
                summary["training"]["model_published"] = True
                summary["training"]["model_name"] = model_name
                with open(summary_path, 'w', encoding='utf-8') as f:
                    json.dump(summary, f, indent=2, ensure_ascii=False)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("[模型发布] 更新 summary.json 失败：%s", e)

    return {"status": "success", "message": "模型发布成功"}


def get_model(model_id: int) -> Optional[dict]:
    """获取模型详情"""
    ensure_model_db()
    with sqlite3.connect(MODEL_DB_PATH) as conn:
        row = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
    if not row:
        return None
    return model_to_dict(row)


def get_model_by_experiment_id(experiment_id: str) -> Optional[dict]:
    """按训练产物的 experiment_id 查询模型注册记录。"""
    ensure_model_db()
    with sqlite3.connect(MODEL_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM models WHERE experiment_id = ?",
            (str(experiment_id),),
        ).fetchone()
    return model_to_dict(row) if row else None


def _training_model_dir(training_info: dict[str, Any]) -> Path | None:
    """从新旧训练摘要中解析受管模型目录。

    早期摘要保存的是开发机绝对路径。项目迁移目录后，若路径已失效，
    仅按安全的 ``model_*`` 目录名回落到当前项目 models/ 下，避免历史模型
    因换盘符/换目录而“消失”。
    """
    ref = training_info.get("model_dir") or training_info.get("model_file_path")
    if not ref:
        return None
    try:
        return _resolve_model_dir(ref, must_exist=True)
    except (FileNotFoundError, ManagedPathError):
        raw = Path(str(ref))
        candidate_name = raw.parent.name if raw.suffix else raw.name
        if candidate_name.startswith("model_") and candidate_name.replace("_", "").replace("-", "").isalnum():
            candidate = _service_setting("PROJECT_ROOT") / "models" / candidate_name
            if candidate.is_dir():
                return candidate.resolve()
        return None


def _training_artifact_metadata(training_info: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    model_dir = _training_model_dir(training_info)
    if model_dir is None:
        return None, {}
    config_path = model_dir / "config.json"
    if not config_path.is_file():
        return model_dir, {}
    try:
        payload = read_json(config_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return model_dir, {}
    return model_dir, payload if isinstance(payload, dict) else {}


def _mark_training_summary_published(summary_path: Path, model_name: str, model_id: int | None = None) -> None:
    summary = read_json(summary_path)
    training = summary.setdefault("training", {})
    training["model_published"] = True
    training["model_name"] = model_name
    if model_id is not None:
        training["model_id"] = model_id
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def publish_training_experiment(
    relative_summary_path: str,
    model_name: str,
    model_desc: str = "",
) -> dict[str, Any]:
    """把历史训练记录中的持久化模型补发布到模型注册表。

    发布信息由 summary.json 与 models/model_*/config.json 在服务端推导，
    前端不需要也不能伪造算法、数据集、模型路径等关键字段。
    """
    name = str(model_name or "").strip()
    if not name:
        raise ValueError("模型名称不能为空")
    summary_path = resolve_managed_file(
        _service_setting("PROJECT_ROOT") / "outputs",
        relative_summary_path,
        anchor=_service_setting("PROJECT_ROOT"),
        required_name="summary.json",
        must_exist=True,
    )
    summary = read_json(summary_path)
    experiment = summary.get("experiment", {}) if isinstance(summary, dict) else {}
    if not isinstance(experiment, dict) or experiment.get("type") != "training":
        raise ValueError("只允许发布训练实验产生的模型")
    training = summary.get("training", {})
    if not isinstance(training, dict):
        raise ValueError("训练摘要缺少 training 信息")

    model_dir, artifact = _training_artifact_metadata(training)
    if model_dir is None:
        raise FileNotFoundError("该历史训练记录对应的模型文件不存在，无法发布")
    if not artifact:
        raise FileNotFoundError(f"模型目录缺少有效 config.json：{model_dir}")

    artifact_experiment_id = str(artifact.get("experiment_id") or "").strip()
    algorithm = str(artifact.get("algorithm") or training.get("algorithm") or "").strip()
    # 训练摘要中的 dataset 来自 Web 请求，旧模型 config.json 曾把 table 名误写到 dataset。
    # 因此历史补发布优先采用 summary.training.dataset。
    dataset = str(training.get("dataset") or artifact.get("dataset") or "").strip()
    table_name = str(training.get("table") or artifact.get("table_name") or "").strip()
    if not artifact_experiment_id or not algorithm or not dataset or not table_name:
        raise ValueError("模型元数据不完整，无法安全发布")

    existing = get_model_by_experiment_id(artifact_experiment_id)
    if existing:
        _mark_training_summary_published(summary_path, existing["model_name"], existing["id"])
        return {
            "status": "success",
            "message": "模型已经发布，无需重复发布",
            "already_published": True,
            "model": existing,
        }

    metrics: dict[str, Any] = {}
    for item in summary.get("results", []):
        if isinstance(item, dict) and isinstance(item.get("metrics"), dict):
            metrics = dict(item["metrics"])
            break
    config = artifact.get("config") if isinstance(artifact.get("config"), dict) else {}
    create_model(
        experiment_id=artifact_experiment_id,
        model_name=name,
        model_desc=str(model_desc or "").strip(),
        algorithm=algorithm,
        dataset=dataset,
        table_name=table_name,
        file_path=model_dir.relative_to(_service_setting("PROJECT_ROOT")).as_posix(),
        metrics=metrics,
        config=config,
    )
    created = get_model_by_experiment_id(artifact_experiment_id)
    _mark_training_summary_published(summary_path, name, created["id"] if created else None)
    return {
        "status": "success",
        "message": "历史训练模型发布成功",
        "already_published": False,
        "model": created,
    }


def update_model_evaluation(model_id: int) -> dict[str, Any]:
    """记录一次评估并将已发布模型推进到 ``evaluated`` 状态。"""
    ensure_model_db()
    now = datetime.datetime.now().isoformat()
    with sqlite3.connect(MODEL_DB_PATH) as conn:
        conn.execute(
            """UPDATE models
               SET status = 'evaluated',
                   last_evaluated_at = ?,
                   evaluation_count = evaluation_count + 1
               WHERE id = ?""",
            (now, model_id),
        )
        conn.commit()
    return {"status": "success", "message": "评估记录已更新"}


def delete_model(model_id: int) -> dict[str, Any]:
    """删除模型"""
    import shutil
    ensure_model_db()
    model = get_model(model_id)
    if not model:
        return {"status": "error", "message": "模型不存在"}
    # Reject unsafe historical rows before mutating the registry.
    model_dir = _resolve_model_dir(model["file_path"], must_exist=False)
    with sqlite3.connect(MODEL_DB_PATH) as conn:
        conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
        conn.commit()
    if model_dir.exists():
        shutil.rmtree(model_dir, ignore_errors=True)
    return {"status": "success", "message": "模型已删除"}


ALLOWED_MODEL_STATUSES = frozenset({"draft", "published", "evaluated"})


def update_model_status(model_id: int, status: str) -> dict[str, Any]:
    """更新模型状态，只接受设计约定的状态集合。"""
    ensure_model_db()
    normalized = str(status or "").strip().lower()
    if normalized not in ALLOWED_MODEL_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_MODEL_STATUSES))
        raise ValueError(f"不支持的模型状态: {status!r}; allowed: {allowed}")
    model = get_model(model_id)
    if not model:
        return {"status": "error", "message": "模型不存在"}
    with sqlite3.connect(MODEL_DB_PATH) as conn:
        if normalized == "evaluated":
            conn.execute(
                "UPDATE models SET status = ?, last_evaluated_at = ?, evaluation_count = evaluation_count + 1 WHERE id = ?",
                (normalized, datetime.datetime.now().isoformat(), model_id),
            )
        else:
            conn.execute("UPDATE models SET status = ? WHERE id = ?", (normalized, model_id))
        conn.commit()
    return {"status": "success", "message": "状态已更新"}

__all__ = [name for name in globals() if not name.startswith("__") and name not in {"_service_setting"}]
