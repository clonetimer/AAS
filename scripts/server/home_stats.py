"""Read-only overview of local application assets for the home dashboard."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def snapshot(root: Path, tasks: dict, tasks_lock) -> dict:
    """Compute actual asset counts; never invent monitoring/accuracy figures."""
    root = Path(root).resolve()
    # Single active telemetry asset: V2 logical channels, not user-created
    # physical tables. Never enumerate *.db as selectable business resources.
    from scripts.server.v2_database_config import active_path
    v2 = active_path(root)
    databases=[]
    all_tables=0
    clean_tables=0
    fault_tables=0
    if v2.is_file() and not v2.is_symlink():
        entry={'name':'V2 统一遥测库','tables':0,
               'size_mb':round(v2.stat().st_size / 1048576,2)}
        try:
            with sqlite3.connect(v2.as_uri()+'?mode=ro',uri=True,timeout=1) as conn:
                schema=conn.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
                if schema is None or str(schema[0])!='2':
                    raise sqlite3.DatabaseError('V2 schema version invalid')
                all_tables=conn.execute('SELECT COUNT(*) FROM v2_channels').fetchone()[0]
                clean_tables=conn.execute("SELECT COUNT(*) FROM v2_segments WHERE role='processed'").fetchone()[0]
                fault_tables=conn.execute("SELECT COUNT(*) FROM v2_segments WHERE role='simulated_fault'").fetchone()[0]
                departments=conn.execute("""SELECT COALESCE(NULLIF(TRIM(subsystem),''),'未分类'),COUNT(*)
                     FROM v2_channels GROUP BY 1 ORDER BY COUNT(*) DESC""").fetchall()
                databases=[{'name':label,'tables':count} for label,count in departments]
                entry['tables']=all_tables
        except (sqlite3.Error,OSError) as exc:
            entry['error']=str(exc)
            databases=[entry]
    datasets_installed=1 if v2.is_file() else 0
    models = 0
    published = 0
    registered_paths: set[Path] = set()
    model_db = root / 'models' / 'model.db'
    if model_db.is_file():
        try:
            with sqlite3.connect(model_db.as_uri() + '?mode=ro', uri=True, timeout=0.35) as conn:
                model_rows = conn.execute('SELECT file_path, status FROM models').fetchall()
                models = len(model_rows)
                published = sum(row[1] == 'published' for row in model_rows)
                for filename, _ in model_rows:
                    path = Path(filename)
                    absolute = path if path.is_absolute() else root / path
                    registered_paths.add((absolute if absolute.is_dir() else absolute.parent).resolve())
        except (sqlite3.Error, OSError):
            pass
    # Some trained models are saved to local directories without registration
    # in model.db. Count each such artifact once, but do not mark it published.
    for directory in (root / 'models').glob('model_*'):
        if directory.is_dir() and not directory.is_symlink() and (directory / 'config.json').is_file():
            if directory.resolve() not in registered_paths:
                models += 1
    with tasks_lock:
        states = [task.get('status') for task in tasks.values()]
    return {
        'databases': datasets_installed, 'tables': all_tables, 'clean_tables': clean_tables,
        'fault_tables': fault_tables, 'models': models, 'published_models': published,
        'tasks': {'running': states.count('running'), 'completed': states.count('completed'),
                  'failed': states.count('failed')},
        'database_items': sorted(databases, key=lambda db: db['tables'], reverse=True),
    }
