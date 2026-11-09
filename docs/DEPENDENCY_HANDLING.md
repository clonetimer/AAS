# 检测器依赖安装与状态检查

检测器可能依赖 NumPy、scikit-learn 或 PyTorch 等第三方包。安装项目依赖后，可在「算法运行 → 依赖管理」查看当前环境中各检测器的依赖状态。

## 安装项目依赖

在项目根目录执行：

```bash
python -m pip install -r requirements.txt
```

如果只需要 CPU 上的深度学习推断或训练，可以参考 PyTorch 官方安装说明选择匹配的环境构建；使用 GPU 时须确保驱动及计算平台版本匹配。

## 检查状态

启动 Web 服务后访问：

```bash
curl "http://127.0.0.1:8765/api/dependencies/status"
curl "http://127.0.0.1:8765/api/available-algorithms"
```

无法加载某个检测器时，先确认选择的 Python 解释器与安装依赖所用的解释器相同，然后检查服务端返回的缺失包信息。原生插件还须满足编译器、动态库及 ABI 要求，参见 [本机编译指南](AAS_NATIVE_BUILD.md)。
