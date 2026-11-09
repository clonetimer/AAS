"""Canonical identity registry for single-measurement SQLite tables.

Legacy row-based tables remain readable. Compact tables keep only samples and
obtain their four constant hierarchy fields from this registry; never infer
identity from a table name. Database migration is an explicit offline action.
"""
from __future__ import annotations

import sqlite3
from typing import Any

CATALOG = 'aas_series_catalog'
IDENTITY = ('model', 'subsystem', 'device', 'variable')
SAMPLE_FIELDS = frozenset({'id', 'timestamp', 'value', 'label', 'created_at',
    'fault_id', 'fault_description', 'fault_severity', 'fault_log_text',
    'affected_component', 'normal_start_time', 'normal_end_time',
    'source_file', 'source_name', 'source_row', 'data_role', 'quality_flag'})


def quote(name: str) -> str:
    if not isinstance(name, str) or not name or '\x00' in name:
        raise ValueError('非法的数据库标识符')
    return '"' + name.replace('"', '""') + '"'


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info({quote(table)})')]


# Each level is unique only within its parent (the same channel name may
# legitimately exist on several devices). All private tables begin with aas_.
HIERARCHY_TABLES = ('aas_models', 'aas_subsystems', 'aas_devices', 'aas_variables')


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def is_normalized(conn: sqlite3.Connection) -> bool:
    return _table_exists(conn, CATALOG) and 'variable_id' in columns(conn, CATALOG)


def _create_hierarchy(conn: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS aas_models (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) > 0))""",
        """CREATE TABLE IF NOT EXISTS aas_subsystems (
        id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL REFERENCES aas_models(id) ON DELETE RESTRICT,
        name TEXT NOT NULL CHECK(length(trim(name)) > 0), UNIQUE(model_id,name))""",
        """CREATE TABLE IF NOT EXISTS aas_devices (
        id INTEGER PRIMARY KEY, subsystem_id INTEGER NOT NULL REFERENCES aas_subsystems(id) ON DELETE RESTRICT,
        name TEXT NOT NULL CHECK(length(trim(name)) > 0), UNIQUE(subsystem_id,name))""",
        """CREATE TABLE IF NOT EXISTS aas_variables (
        id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES aas_devices(id) ON DELETE RESTRICT,
        name TEXT NOT NULL CHECK(length(trim(name)) > 0), UNIQUE(device_id,name))""",
    )
    for sql in statements:
        conn.execute(sql)


def _create_catalog(conn: sqlite3.Connection, name: str = CATALOG) -> None:
    conn.execute(f'''CREATE TABLE {quote(name)} (
        table_name TEXT PRIMARY KEY,
        variable_id INTEGER NOT NULL REFERENCES aas_variables(id) ON DELETE RESTRICT,
        schema_kind TEXT NOT NULL CHECK(schema_kind IN ('compact','legacy'))
    )''')
    if name == CATALOG:
        conn.execute('CREATE INDEX aas_catalog_variable_idx ON aas_series_catalog(variable_id)')


def ensure(conn: sqlite3.Connection) -> None:
    """Create new schema on empty DB; do not silently migrate existing v1 DB."""
    if _table_exists(conn, CATALOG) and not is_normalized(conn):
        return
    _create_hierarchy(conn)
    if not _table_exists(conn, CATALOG):
        _create_catalog(conn)


def _insert_level(conn: sqlite3.Connection, table: str, name: str,
                  parent_key: str | None = None, parent_id: int | None = None) -> int:
    if parent_key is None:
        conn.execute(f'INSERT OR IGNORE INTO {table}(name) VALUES (?)', (name,))
        row = conn.execute(f'SELECT id FROM {table} WHERE name=?', (name,)).fetchone()
    else:
        conn.execute(f'INSERT OR IGNORE INTO {table}({parent_key},name) VALUES (?,?)',
                     (parent_id, name))
        row = conn.execute(f'SELECT id FROM {table} WHERE {parent_key}=? AND name=?',
                           (parent_id, name)).fetchone()
    if not row:
        raise ValueError(f'无法登记参量层级：{table}')
    return int(row[0])


def _variable_id(conn: sqlite3.Connection, identity: dict[str, str]) -> int:
    model_id = _insert_level(conn, 'aas_models', identity['model'])
    sub_id = _insert_level(conn, 'aas_subsystems', identity['subsystem'], 'model_id', model_id)
    device_id = _insert_level(conn, 'aas_devices', identity['device'], 'subsystem_id', sub_id)
    return _insert_level(conn, 'aas_variables', identity['variable'], 'device_id', device_id)


def get(conn: sqlite3.Connection, table: str) -> dict[str, str] | None:
    if not _table_exists(conn, CATALOG):
        return None
    if not is_normalized(conn):
        row = conn.execute(f'SELECT model,subsystem,device,variable,schema_kind '
                           f'FROM {CATALOG} WHERE table_name=?', (table,)).fetchone()
    else:
        row = conn.execute('''SELECT m.name,s.name,d.name,v.name,c.schema_kind
            FROM aas_series_catalog c
            LEFT JOIN aas_variables v ON v.id=c.variable_id
            LEFT JOIN aas_devices d ON d.id=v.device_id
            LEFT JOIN aas_subsystems s ON s.id=d.subsystem_id
            LEFT JOIN aas_models m ON m.id=s.model_id
            WHERE c.table_name=?''', (table,)).fetchone()
        if row and any(item is None for item in row):
            raise ValueError(f'{table} 的参量目录存在失效的层级引用')
    return dict(zip((*IDENTITY, 'schema_kind'), row)) if row else None


def validate_hierarchy(conn: sqlite3.Connection) -> None:
    """Check both foreign keys and all registered table identities."""
    if not is_normalized(conn) or any(not _table_exists(conn, t) for t in HIERARCHY_TABLES):
        raise ValueError('数据库尚未启用完整层级目录')
    problems = conn.execute('PRAGMA foreign_key_check').fetchall()
    if problems:
        raise ValueError(f'层级目录外键损坏：{problems[:3]}')
    for (table_name,) in conn.execute(f'SELECT table_name FROM {CATALOG}'):
        if not _table_exists(conn, table_name):
            raise ValueError(f'层级目录引用了不存在的表：{table_name}')
        signature(conn, table_name)


def normalize_hierarchy(conn: sqlite3.Connection, *, dry_run: bool = True) -> dict[str, Any]:
    """Explicit transactional upgrade of the catalog; never changes sample rows."""
    if not _table_exists(conn, CATALOG):
        if not dry_run:
            ensure(conn)
        return {'dry_run': dry_run, 'converted': 0, 'already_normalized': False}
    if is_normalized(conn):
        validate_hierarchy(conn)
        return {'dry_run': dry_run, 'converted': 0, 'already_normalized': True}
    originals = []
    for (table_name,) in conn.execute(f'SELECT table_name FROM {CATALOG} ORDER BY table_name'):
        item = get(conn, table_name)
        if not _table_exists(conn, table_name) or not item or signature(conn, table_name) is None:
            raise ValueError(f'存在无效的旧参量目录记录：{table_name}')
        originals.append((table_name, item))
    if dry_run:
        return {'dry_run': True, 'converted': len(originals), 'already_normalized': False}
    conn.execute('SAVEPOINT normalize_hierarchy')
    try:
        _create_hierarchy(conn)
        temp = 'aas_series_catalog_pending'
        if _table_exists(conn, temp):
            raise ValueError('发现遗留的临时目录表，拒绝迁移')
        _create_catalog(conn, temp)
        for name, item in originals:
            channel = _variable_id(conn, {key: item[key] for key in IDENTITY})
            conn.execute(f'INSERT INTO {temp}(table_name,variable_id,schema_kind) VALUES (?,?,?)',
                         (name, channel, item['schema_kind']))
        conn.execute(f'DROP TABLE {CATALOG}')
        conn.execute(f'ALTER TABLE {temp} RENAME TO {CATALOG}')
        conn.execute('CREATE INDEX aas_catalog_variable_idx ON aas_series_catalog(variable_id)')
        validate_hierarchy(conn)
        for name, item in originals:
            if get(conn, name) != item:
                raise ValueError(f'{name} 的目录身份发生变化')
        conn.execute('RELEASE SAVEPOINT normalize_hierarchy')
    except BaseException:
        conn.execute('ROLLBACK TO SAVEPOINT normalize_hierarchy')
        conn.execute('RELEASE SAVEPOINT normalize_hierarchy')
        raise
    return {'dry_run': False, 'converted': len(originals), 'already_normalized': False}


def prune_unused(conn: sqlite3.Connection) -> None:
    """Remove hierarchy nodes no longer referenced by any registered table."""
    if not is_normalized(conn):
        return
    conn.execute('DELETE FROM aas_variables WHERE id NOT IN '
                 '(SELECT variable_id FROM aas_series_catalog)')
    conn.execute('DELETE FROM aas_devices WHERE id NOT IN '
                 '(SELECT device_id FROM aas_variables)')
    conn.execute('DELETE FROM aas_subsystems WHERE id NOT IN '
                 '(SELECT subsystem_id FROM aas_devices)')
    conn.execute('DELETE FROM aas_models WHERE id NOT IN '
                 '(SELECT model_id FROM aas_subsystems)')


def identity_from_legacy(conn: sqlite3.Connection, table: str) -> dict[str, str] | None:
    fields = columns(conn, table)
    if not set(IDENTITY).issubset(fields):
        return None
    qtable = quote(table)
    identities = conn.execute(
        f'SELECT DISTINCT model, subsystem, device, variable FROM {qtable} LIMIT 2'
    ).fetchall()
    if len(identities) != 1 or any(value is None or not str(value).strip() for value in identities[0]):
        return None
    return dict(zip(IDENTITY, map(str, identities[0])))


def signature(conn: sqlite3.Connection, table: str) -> dict[str, str] | None:
    """Return verified identity, rejecting stale or incomplete registry records."""
    entry = get(conn, table)
    if entry:
        if not _table_exists(conn, table):
            raise ValueError(f"目录关联的数据表不存在：{table}")
        fields = set(columns(conn, table))
        if entry['schema_kind'] == 'compact' and not set(IDENTITY).isdisjoint(fields):
            raise ValueError(f'{table} 的参量目录与实体表结构不一致')
        if entry['schema_kind'] == 'legacy':
            actual = identity_from_legacy(conn, table)
            if actual != {key: entry[key] for key in IDENTITY}:
                raise ValueError(f'{table} 的参量目录与原始表内容不一致')
        return {key: entry[key] for key in IDENTITY}
    return identity_from_legacy(conn, table)


def register(conn: sqlite3.Connection, table: str, identity: dict[str, Any], kind: str) -> None:
    if kind not in ('compact', 'legacy'):
        raise ValueError('不支持的数据表类型')
    if table.startswith('aas_') or not _table_exists(conn, table):
        raise ValueError('只能为已存在的业务数据表注册参量身份')
    normalized = {key: str(identity.get(key) or '').strip() for key in IDENTITY}
    if not all(normalized.values()):
        raise ValueError('型号、分系统、部件、参量必须完整且非空')
    ensure(conn)
    existing = get(conn, table)
    if existing:
        if ({key: existing[key] for key in IDENTITY} != normalized
                or existing['schema_kind'] != kind):
            raise ValueError('目标表已有不同的归属元信息，禁止混入其他参量')
        return
    if is_normalized(conn):
        variable_id = _variable_id(conn, normalized)
        conn.execute(f'INSERT INTO {CATALOG}(table_name,variable_id,schema_kind) VALUES (?,?,?)',
                     (table, variable_id, kind))
    else:
        conn.execute(f'''INSERT INTO {CATALOG}
            (table_name,model,subsystem,device,variable,schema_kind)
            VALUES (?,?,?,?,?,?)''', (table, *(normalized[key] for key in IDENTITY), kind))


def is_compact(conn: sqlite3.Connection, table: str) -> bool:
    entry = get(conn, table)
    return bool(entry and entry['schema_kind'] == 'compact')


def logical_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    physical = columns(conn, table)
    if is_compact(conn, table):
        result = []
        for col in physical:
            if col == 'value':
                result.extend(key for key in IDENTITY if key not in result)
            result.append(col)
        return result
    return physical


def expand(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> dict[str, Any]:
    if not is_compact(conn, table):
        return data
    ident = signature(conn, table)
    if ident is None:
        raise ValueError(f'{table} 缺少参量归属元信息')
    return expand_known(data, ident, logical_columns(conn, table))


def expand_known(data: dict[str, Any], identity: dict[str, str], fields: list[str]) -> dict[str, Any]:
    """Fast projection when identity and fields are already resolved per query."""
    return {field: identity[field] if field in identity else data.get(field)
            for field in fields}


def replace_derived_table(conn: sqlite3.Connection, source: str, target: str,
                          rows: list[dict[str, Any]]) -> None:
    """Atomically refresh a clean table, retaining its source channel identity."""
    if not rows:
        raise ValueError('清洁数据不能为空')
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (source,)).fetchone()
    if not schema:
        raise ValueError(f'原始数据表不存在：{source}')
    source_columns = columns(conn, source)
    missing = set(source_columns) - set(rows[0])
    if missing:
        raise ValueError(f'预处理丢失必需字段：{sorted(missing)}')
    # Existing project table names are validated by the browser/import layer;
    # use verified schema SQL rather than interpolating arbitrary user content.
    statement = schema[0].replace(quote(source), quote(target), 1)
    if statement == schema[0]:
        statement = schema[0].replace(source, target, 1)
    conn.execute(f'DROP TABLE IF EXISTS {quote(target)}')
    if get(conn, target):
        conn.execute(f'DELETE FROM {CATALOG} WHERE table_name=?', (target,))
        prune_unused(conn)
    conn.execute(statement)
    placeholders = ', '.join('?' for _ in source_columns)
    destinations = ', '.join(quote(field) for field in source_columns)
    conn.executemany(f'INSERT INTO {quote(target)} ({destinations}) VALUES ({placeholders})',
                     [[row.get(field) for field in source_columns] for row in rows])
    if is_compact(conn, source):
        identity = signature(conn, source)
        if identity is None:
            raise ValueError('源表缺少参量身份')
        register(conn, target, identity, 'compact')


def migrate_eligible(conn: sqlite3.Connection, *, dry_run: bool = True) -> dict[str, Any]:
    """Compact strictly single-channel legacy tables, preserving row labels.

    This is deliberately NOT automatic on HTTP requests. Call on a backup copy
    with all application writers stopped, then validate before publishing.
    """
    names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    converted, cataloged, skipped = [], [], {}
    for name in names:
        if name.startswith(('sqlite_', 'aas_')) or name in {'table_index', 'device_table_index', 'dataset_meta'}:
            continue
        if name.endswith('_clean'):
            skipped[name] = 'derived clean table: preserve existing preprocessing contract'
            continue
        physical = set(columns(conn, name))
        ident = identity_from_legacy(conn, name)
        if not ident:
            skipped[name] = 'missing, mixed or incomplete channel identity'
            continue
        if not {'timestamp', 'value', 'label'}.issubset(physical):
            skipped[name] = 'required timestamp/value/label not available'
            continue
        if physical - set(IDENTITY) - SAMPLE_FIELDS:
            skipped[name] = 'additional measurement fields or unsupported legacy schema'
            continue
        if dry_run:
            converted.append(name)
            continue
        # Per-table atomicity, including catalog registration. No partial DDL.
        conn.execute('SAVEPOINT compact_series')
        try:
            for col in IDENTITY:
                conn.execute(f'ALTER TABLE {quote(name)} DROP COLUMN {quote(col)}')
            register(conn, name, ident, 'compact')
            conn.execute('RELEASE SAVEPOINT compact_series')
            converted.append(name)
        except (sqlite3.Error, ValueError) as exc:
            conn.execute('ROLLBACK TO SAVEPOINT compact_series')
            conn.execute('RELEASE SAVEPOINT compact_series')
            skipped[name] = str(exc)
    return {'converted': converted, 'cataloged': cataloged, 'skipped': skipped, 'dry_run': dry_run}
