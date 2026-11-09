# AAS C/C++ 原生插件运行时隔离

AAS 的 C/C++ 插件不再把 DLL/SO 直接加载到长期运行的 Python 主服务进程。检测算法与评估指标由独立 **Native Plugin Host** 子进程承载，AAS 主服务通过认证的本机 IPC 调用。

## 1. 运行结构

```text
AAS Python 主服务
      │
      │ authenticated localhost IPC
      ▼
Native Plugin Host
  python -I -S
  stdlib-only + ctypes
      │
      ▼
   DLL / SO
      │
     C/C++
```

Native Plugin Host：

- 使用 `python -I -S` 启动；
- 不导入 AAS、NumPy、Torch、sklearn 等第三方 Python 模块；
- 启动环境会清理 venv/Conda/PYTHONPATH 等变量与路径；
- DLL/SO 仅在 Host 进程中由 `ctypes` 加载；
- 主 AAS 进程不加载原生插件动态库；
- Host 与主服务通过随机认证密钥保护的本机 IPC 通信；
- 原生插件发生 `abort`、非法内存访问或 Host 进程异常退出时，AAS 主服务能够继续运行并报告“原生插件宿主异常退出”。

## 2. Detector 生命周期

一个原生 Detector 的正常链路为：

```text
创建 NativeDetector
  -> 启动 Host
  -> create(config)
  -> fit(train)
  -> export_state()
  -> Python 主进程保存最近一次有效原生状态
  -> score(test)
  -> 保存 / pickle
  -> 新 Host
  -> import_state()
  -> 继续评估
```

训练成功后 AAS 会立即保存一次原生状态快照。模型发布、pickle 恢复或 Host 重建时，都通过 `aas_detector_import_state` 恢复状态，不要求原 DLL/SO 进入 Python 主服务。

## 3. Metric 生命周期

原生 Metric 使用惰性 Host：仅打开“插件能力”或加载注册表时不会启动原生进程；第一次真正计算指标时才启动 Host。后续指标计算复用该 Host，插件卸载或注册表刷新时关闭。

## 4. IPC 数据

当前 `aas_native_v1` 通过 IPC 传递：

- Detector：训练 `double[]`、评分 `double[]`、阈值输入、原生状态字节；
- Metric：`y_true int32[]`、异常分数 `double[]`、预测标签 `int32[]`、训练/测试值、JSON 配置；
- 单个二进制字段默认最大 128 MB，可通过管理员环境变量 `AAS_NATIVE_HOST_MAX_BLOB_MB` 调整；
- 默认单次调用超时 120 秒，可通过 `AAS_NATIVE_HOST_REQUEST_TIMEOUT` 调整；超时后 AAS 会终止 Host，避免主服务永久等待。

## 5. Python 环境隔离

Host 使用与 AAS 相同的 Python 可执行文件进行“进程调度”，但其解释器以 `-I -S` 方式启动：

- `-I`：启用 Python isolated mode；
- `-S`：不加载 `site`，避免虚拟环境 site-packages / sitecustomize 参与运行；
- 不导入 AAS 代码和任何第三方 Python 包；
- 清理 `VIRTUAL_ENV`、`CONDA_PREFIX`、`PYTHONPATH`、`PYTHONHOME` 等环境信息；
- Host 中的 Python 只承担 IPC 与 `ctypes` ABI 桥接，算法主体仍是 C/C++ 动态库。

因此“C/C++ 插件运行环境”与 AAS 的 NumPy/Torch/venv/Conda 依赖链解耦。

## 6. 崩溃与超时隔离

如果原生代码导致：

- `SIGSEGV` / Access Violation；
- `abort()`；
- Host 非正常退出；
- 单次调用超过运行时超时；

受影响的是 Native Plugin Host 子进程，主 AAS Python 服务不会因为 DLL/SO 与其共享地址空间而直接崩溃。下一次使用 Detector 时可基于最近一次有效状态重建 Host；导致崩溃的当次业务操作仍会返回明确失败，不会静默重试同一危险输入。

## 7. 资源限制

POSIX 系统对 Host 采用 best-effort 资源约束：关闭 core dump、限制打开文件数；管理员还可通过：

- `AAS_NATIVE_HOST_MEMORY_MB`
- `AAS_NATIVE_HOST_CPU_SECONDS`

设置地址空间和 CPU 限制。Windows 当前依赖独立进程边界与进程树终止机制，后续如需更强约束可进一步接入 Windows Job Object。

## 8. 安全边界

运行时隔离解决的是 **稳定性与环境隔离**，不是“不可信代码沙箱”。原生 Host 仍以启动 AAS 的操作系统账号运行，因此插件理论上仍能：

- 访问该账号有权限的文件；
- 建立网络连接；
- 启动其他进程；
- 调用操作系统 API。

因此仍只应编译和安装可信 C/C++ 源码或动态库。如果未来要求运行不可信插件，需要再增加低权限账号、容器/沙箱、文件系统与网络策略等 OS 级隔离。
