import sqlite3
from pathlib import Path

# 数据库路径
root = Path(__file__).resolve().parents[0]
db_path = root / "datasets" / "history_sample.db"

print(f"检查数据库: {db_path}")
print(f"文件是否存在: {db_path.exists()}")
print(f"文件大小: {db_path.stat().st_size / (1024 * 1024):.2f} MB")

try:
    # 连接数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    print("\n数据库连接成功")

    # 获取表信息
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"表数量: {len(tables)}")
    print("表列表:")
    for table in tables:
        # 获取表的列信息
        cursor.execute(f"PRAGMA table_info({table});")
        columns = [row[1] for row in cursor.fetchall()]
        # 获取表的记录数
        cursor.execute(f"SELECT COUNT(*) FROM {table};")
        count = cursor.fetchone()[0]
        print(f"  - {table}: {count} 条记录, 列数: {len(columns)}")

    conn.close()
    print("\n数据库检查完成")

except (sqlite3.Error, OSError) as e:
    print(f"\n数据库检查失败: {e}")

# 检查 datasets 目录下的所有数据库文件
print("\n检查 datasets 目录下的所有数据库文件:")
datasets_dir = root / "datasets"
db_files = list(datasets_dir.glob("*.db"))
print(f"找到 {len(db_files)} 个数据库文件:")
for db_file in db_files:
    print(f"  - {db_file.name}: {db_file.stat().st_size / (1024 * 1024):.2f} MB")
