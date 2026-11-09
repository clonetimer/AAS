"""Model registry storage and publishing workflows, decoupled from HTTP boot file.

Application runtime is passed explicitly to avoid implicit global state and cyclic imports.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any

def ensure_model_db(app):
    """初始化 model.db，并保证模型表/索引始终存在。"""
    app.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        conn.execute("\n            CREATE TABLE IF NOT EXISTS models (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                experiment_id TEXT NOT NULL UNIQUE,\n                model_name TEXT NOT NULL,\n                model_desc TEXT DEFAULT '',\n                status TEXT NOT NULL DEFAULT 'published',\n                algorithm TEXT NOT NULL,\n                dataset TEXT NOT NULL,\n                table_name TEXT NOT NULL,\n                created_at TEXT NOT NULL,\n                published_at TEXT NOT NULL,\n                last_evaluated_at TEXT,\n                evaluation_count INTEGER DEFAULT 0,\n                file_path TEXT NOT NULL,\n                metrics_json TEXT DEFAULT '{}',\n                config_json TEXT DEFAULT '{}'\n            )\n        ")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_models_status ON models(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_models_algorithm ON models(algorithm)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_models_experiment ON models(experiment_id)')
        conn.commit()

def model_to_dict(app, row):
    """将数据库行转换为兼容旧字段且可直接使用的模型记录。"""
    try:
        metrics = json.loads(row[13] or '{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        metrics = {}
    try:
        config = json.loads(row[14] or '{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        config = {}
    return {'id': row[0], 'experiment_id': row[1], 'model_name': row[2], 'model_desc': row[3],
            'status': row[4], 'algorithm': row[5], 'dataset': row[6], 'table_name': row[7],
            'created_at': row[8], 'published_at': row[9], 'last_evaluated_at': row[10],
            'evaluation_count': row[11], 'file_path': row[12], 'metrics_json': row[13],
            'config_json': row[14], 'metrics': metrics, 'config': config}

def get_models(app, status=None, algorithm=None, limit=50):
    """获取模型列表。"""
    app.ensure_model_db()
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        query = 'SELECT * FROM models WHERE 1=1'
        params = []
        if status:
            query += ' AND status = ?'
            params.append(status)
        if algorithm:
            query += ' AND algorithm = ?'
            params.append(algorithm)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    return [app.model_to_dict(row) for row in rows]

def create_model(app, experiment_id, model_name, model_desc, algorithm, dataset, table_name, file_path, metrics, config):
    """发布模型。"""
    app.ensure_model_db()
    managed_model_dir = app.resolve_managed_model_dir(file_path, must_exist=True)
    app.core_validate_saved_model(managed_model_dir, algorithm, experiment_id=experiment_id)
    file_path = managed_model_dir.relative_to(app.ROOT).as_posix()
    import datetime
    now = datetime.datetime.now().isoformat()
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        conn.execute("INSERT INTO models\n               (experiment_id, model_name, model_desc, status, algorithm, dataset, table_name,\n                created_at, published_at, file_path, metrics_json, config_json)\n               VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, ?, ?)", (experiment_id, model_name, model_desc, algorithm, dataset, table_name, now, now, file_path, json.dumps(metrics, ensure_ascii=False), json.dumps(config, ensure_ascii=False)))
        conn.commit()
    try:
        summary_path = app.ROOT / 'outputs' / f'experiment_{experiment_id}' / 'reports' / 'summary.json'
        if summary_path.exists():
            summary = app.read_json(summary_path)
            if summary.get('training'):
                summary['training']['model_published'] = True
                summary['training']['model_name'] = model_name
                with open(summary_path, 'w', encoding='utf-8') as f:
                    json.dump(summary, f, indent=2, ensure_ascii=False)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as e:
        print(f'[模型发布] 更新 summary.json 失败：{e}')
    return {'status': 'success', 'message': '模型发布成功'}

def get_model(app, model_id):
    """获取模型详情。"""
    app.ensure_model_db()
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        row = conn.execute('SELECT * FROM models WHERE id = ?', (model_id,)).fetchone()
    if not row:
        return None
    return app.model_to_dict(row)

def get_model_by_experiment_id(app, experiment_id):
    """按训练产物 experiment_id 查询模型注册记录。"""
    app.ensure_model_db()
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        row = conn.execute('SELECT * FROM models WHERE experiment_id = ?', (str(experiment_id),)).fetchone()
    return app.model_to_dict(row) if row else None

def _training_model_dir(app, training_info):
    ref = training_info.get('model_dir') or training_info.get('model_file_path')
    if not ref:
        return None
    try:
        return app.resolve_managed_model_dir(ref, must_exist=True)
    except (FileNotFoundError, app.ManagedPathError):
        raw = Path(str(ref))
        candidate_name = raw.parent.name if raw.suffix else raw.name
        if candidate_name.startswith('model_') and candidate_name.replace('_', '').replace('-', '').isalnum():
            candidate = Path(getattr(app, 'MODELS_DIR', app.ROOT / 'models')) / candidate_name
            if candidate.is_dir():
                return candidate.resolve()
        return None

def _training_artifact_metadata(app, training_info):
    model_dir = app._training_model_dir(training_info)
    if model_dir is None:
        return (None, {})
    config_path = model_dir / 'config.json'
    if not config_path.is_file():
        return (model_dir, {})
    try:
        payload = app.read_json(config_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return (model_dir, {})
    return (model_dir, payload if isinstance(payload, dict) else {})

def _mark_training_summary_published(app, summary_path, model_name, model_id=None):
    summary = app.read_json(summary_path)
    training = summary.setdefault('training', {})
    training['model_published'] = True
    training['model_name'] = model_name
    if model_id is not None:
        training['model_id'] = model_id
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')

def publish_training_experiment(app, relative_summary_path, model_name, model_desc=''):
    """发布历史训练记录中仍存在的持久化模型。"""
    name = str(model_name or '').strip()
    if not name:
        raise ValueError('模型名称不能为空')
    summary_path = app.resolve_managed_file(app.ROOT / 'outputs', relative_summary_path, anchor=app.ROOT, required_name='summary.json', must_exist=True)
    summary = app.read_json(summary_path)
    experiment = summary.get('experiment', {}) if isinstance(summary, dict) else {}
    if not isinstance(experiment, dict) or experiment.get('type') != 'training':
        raise ValueError('只允许发布训练实验产生的模型')
    training = summary.get('training', {})
    if not isinstance(training, dict):
        raise ValueError('训练摘要缺少 training 信息')
    model_dir, artifact = app._training_artifact_metadata(training)
    if model_dir is None:
        raise FileNotFoundError('该历史训练记录对应的模型文件不存在，无法发布')
    if not artifact:
        raise FileNotFoundError(f'模型目录缺少有效 config.json：{model_dir}')
    artifact_experiment_id = str(artifact.get('experiment_id') or '').strip()
    algorithm = str(artifact.get('algorithm') or training.get('algorithm') or '').strip()
    dataset = str(training.get('dataset') or artifact.get('dataset') or '').strip()
    table_name = str(training.get('table') or artifact.get('table_name') or '').strip()
    if not artifact_experiment_id or not algorithm or (not dataset) or (not table_name):
        raise ValueError('模型元数据不完整，无法安全发布')
    existing = app.get_model_by_experiment_id(artifact_experiment_id)
    if existing:
        app._mark_training_summary_published(summary_path, existing['model_name'], existing['id'])
        return {'status': 'success', 'message': '模型已经发布，无需重复发布', 'already_published': True, 'model': existing}
    metrics = {}
    for item in summary.get('results', []):
        if isinstance(item, dict) and isinstance(item.get('metrics'), dict):
            metrics = dict(item['metrics'])
            break
    config = artifact.get('config') if isinstance(artifact.get('config'), dict) else {}
    app.create_model(artifact_experiment_id, name, str(model_desc or '').strip(), algorithm, dataset, table_name, model_dir.relative_to(app.ROOT).as_posix(), metrics, config)
    created = app.get_model_by_experiment_id(artifact_experiment_id)
    app._mark_training_summary_published(summary_path, name, created['id'] if created else None)
    return {'status': 'success', 'message': '历史训练模型发布成功', 'already_published': False, 'model': created}

def update_model_evaluation(app, model_id):
    """记录一次评估并将已发布模型推进到 ``evaluated`` 状态。"""
    app.ensure_model_db()
    import datetime
    now = datetime.datetime.now().isoformat()
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        conn.execute("UPDATE models\n               SET status = 'evaluated',\n                   last_evaluated_at = ?,\n                   evaluation_count = evaluation_count + 1\n               WHERE id = ?", (now, model_id))
        conn.commit()
    return {'status': 'success', 'message': '评估记录已更新'}

def delete_model(app, model_id):
    """删除模型。"""
    app.ensure_model_db()
    import shutil
    model = app.get_model(model_id)
    if not model:
        return {'status': 'error', 'message': '模型不存在'}
    model_dir = app.resolve_managed_model_dir(model['file_path'], must_exist=False)
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        conn.execute('DELETE FROM models WHERE id = ?', (model_id,))
        conn.commit()
    if model_dir.exists():
        shutil.rmtree(model_dir, ignore_errors=True)
    return {'status': 'success', 'message': '模型已删除'}

def update_model_status(app, model_id, status):
    """更新模型状态，只接受设计约定的状态集合。"""
    app.ensure_model_db()
    import datetime
    normalized = str(status or '').strip().lower()
    if normalized not in app.ALLOWED_MODEL_STATUSES:
        allowed = ', '.join(sorted(app.ALLOWED_MODEL_STATUSES))
        raise ValueError(f'不支持的模型状态: {status!r}; allowed: {allowed}')
    model = app.get_model(model_id)
    if not model:
        return {'status': 'error', 'message': '模型不存在'}
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        if normalized == 'evaluated':
            conn.execute('UPDATE models SET status = ?, last_evaluated_at = ?, evaluation_count = evaluation_count + 1 WHERE id = ?', (normalized, datetime.datetime.now().isoformat(), model_id))
        else:
            conn.execute('UPDATE models SET status = ? WHERE id = ?', (normalized, model_id))
        conn.commit()
    return {'status': 'success', 'message': '状态已更新'}
