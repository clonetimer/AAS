import sqlite3

db_path = 'datasets/history_sample.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 获取所有表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

print(f'数据库：{db_path}')
print(f'数据表数量：{len(tables)}')
print('\n数据表列表及记录数:')

for table_name in tables:
    name = table_name[0]
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {name}")
        count = cursor.fetchone()[0]
        print(f'  - {name}: {count} 条记录')
    except sqlite3.Error as e:
        print(f'  - {name}: 无法查询 ({e})')

conn.close()
