import sqlite3
from pathlib import Path

# 连接到数据库
db_path = Path(__file__).resolve().parents[2] / 'datasets' / 'history_sample.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 查看表结构
table_name = 'GF1_subsys1_solar_panel_current'
cursor.execute(f"PRAGMA table_info({table_name});")
columns = cursor.fetchall()

print(f"表结构: {table_name}")
print("列信息:")
for col in columns:
    print(f"  - {col[1]} ({col[2]})")

# 查看表中的一些数据
print("\n表中的示例数据:")
cursor.execute(f"SELECT * FROM {table_name} LIMIT 5;")
sample_data = cursor.fetchall()
for row in sample_data:
    print(row)

# 分析表名的含义
print("\n表名分析:")
print("GF1_subsys1_solar_panel:")
print("  - GF1: 可能表示型号 1")
print("  - subsys1: 可能表示子系统 1")
print("  - solar_panel: 可能表示太阳能板设备")

# 分析列的含义
print("\n列含义分析:")
print("  - id: 自增主键")
print("  - timestamp: 时间戳")
print("  - model: 设备型号")
print("  - subsystem: 子系统名称")
print("  - device: 设备名称")
print("  - variable: 变量名称")
print("  - value: 变量值")
print("  - label: 标签（0 或 1，可能表示正常/异常）")
print("  - created_at: 创建时间")

conn.close()
print("\n分析完成！")
