import sqlite3
from pathlib import Path

# 连接到数据库
db_path = Path(__file__).resolve().parents[2] / 'datasets' / 'history_sample.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 查看所有数据表格
all_tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'GF%' AND name NOT LIKE '%_clean' ORDER BY name").fetchall()]

for table_name in all_tables:
    print(f"\n=== {table_name} ===")
    # 查看前 5 条数据
    cursor.execute(f"SELECT * FROM {table_name} LIMIT 5;")
    rows = cursor.fetchall()

    # 查看列名
    cursor.execute(f"PRAGMA table_info({table_name});")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]
    print("列名:", column_names)

    # 打印数据
    print("数据:")
    for row in rows:
        print(row)

    # 统计正常和异常的数量
    if "label" in column_names:
        cursor.execute(f"SELECT label, COUNT(*) FROM {table_name} GROUP BY label;")
        label_counts = cursor.fetchall()
        print("标签统计:")
        for label, count in label_counts:
            print(f"  标签 {label}: {count} 条")

conn.close()
print("\n验证完成！")
