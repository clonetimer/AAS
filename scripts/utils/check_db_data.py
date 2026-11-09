import sqlite3
from pathlib import Path

# 数据库路径
root = Path(__file__).resolve().parents[0]
db_path = root / "datasets" / "history_sample.db"

print(f"检查数据库: {db_path}")
print(f"文件是否存在: {db_path.exists()}")

try:
    # 连接数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    print("\n数据库连接成功")

    # 获取表信息
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"表数量: {len(tables)}")

    # 检查每个表的数据
    for table in tables:
        print(f"\n检查表: {table}")

        # 检查表结构
        cursor.execute(f"PRAGMA table_info({table});")
        columns = [row[1] for row in cursor.fetchall()]
        print(f"列: {columns}")

        # 检查是否有 model, subsystem, device, variable 列
        if all(col in columns for col in ['model', 'subsystem', 'device', 'variable']):
            # 获取前 10 条数据
            cursor.execute(f"SELECT model, subsystem, device, variable FROM {table} LIMIT 10;")
            rows = cursor.fetchall()
            print("前 10 条数据:")
            for row in rows:
                print(f"  model: {row[0]}, subsystem: {row[1]}, device: {row[2]}, variable: {row[3]}")

            # 检查是否存在 f1, f2, f3 这样的值
            cursor.execute(f"SELECT DISTINCT model FROM {table};")
            models = [row[0] for row in cursor.fetchall()]
            print(f"型号值: {models}")

            cursor.execute(f"SELECT DISTINCT subsystem FROM {table};")
            subsystems = [row[0] for row in cursor.fetchall()]
            print(f"分系统值: {subsystems}")

            cursor.execute(f"SELECT DISTINCT device FROM {table};")
            devices = [row[0] for row in cursor.fetchall()]
            print(f"单机值: {devices}")

            cursor.execute(f"SELECT DISTINCT variable FROM {table};")
            variables = [row[0] for row in cursor.fetchall()]
            print(f"参量值: {variables}")

    conn.close()
    print("\n数据库检查完成")

except (sqlite3.Error, OSError) as e:
    print(f"\n数据库检查失败: {e}")
