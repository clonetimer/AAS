import sqlite3
from pathlib import Path

# 数据库路径
root = Path(__file__).resolve().parents[0]
db_path = root / "datasets" / "history_sample.db"

print(f"检查数据库: {db_path}")

try:
    # 连接数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 检查 table_index 表
    print("\n=== 检查 table_index 表 ===")
    cursor.execute("SELECT * FROM table_index;")
    rows = cursor.fetchall()
    print(f"记录数: {len(rows)}")
    print("前 20 条记录:")
    for row in rows[:20]:
        print(f"  {row}")

    # 获取所有唯一的型号
    print("\n=== 所有唯一的型号 ===")
    cursor.execute("SELECT DISTINCT model FROM table_index;")
    models = [row[0] for row in cursor.fetchall()]
    print(f"型号: {models}")

    # 获取所有唯一的分系统
    print("\n=== 所有唯一的分系统 ===")
    cursor.execute("SELECT DISTINCT subsystem FROM table_index;")
    subsystems = [row[0] for row in cursor.fetchall()]
    print(f"分系统: {subsystems}")

    # 获取所有唯一的单机
    print("\n=== 所有唯一的单机 ===")
    cursor.execute("SELECT DISTINCT device FROM table_index;")
    devices = [row[0] for row in cursor.fetchall()]
    print(f"单机: {devices}")

    # 检查型号 - 分系统的关系
    print("\n=== 型号 - 分系统关系 ===")
    cursor.execute("SELECT model, subsystem FROM table_index GROUP BY model, subsystem;")
    model_subsystem = cursor.fetchall()
    for model, subsystem in model_subsystem:
        print(f"  {model} -> {subsystem}")

    # 检查分系统 - 单机的关系
    print("\n=== 分系统 - 单机关系 ===")
    cursor.execute("SELECT subsystem, device FROM table_index GROUP BY subsystem, device;")
    subsystem_device = cursor.fetchall()
    for subsystem, device in subsystem_device:
        print(f"  {subsystem} -> {device}")

    conn.close()
    print("\n检查完成")

except (sqlite3.Error, OSError) as e:
    print(f"\n检查失败: {e}")
