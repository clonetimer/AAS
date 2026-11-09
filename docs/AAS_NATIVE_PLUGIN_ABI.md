# AAS C/C++ 原生插件 ABI（aas_native_v1）

AAS 支持 Python 插件，并在首期为 **检测算法（detectors）** 与 **评估指标（metrics）** 增加 C/C++ 原生插件。C 与 C++ 共用稳定的 C ABI；C++ 导出函数必须使用 `extern "C"`。

> 原生 DLL/SO 由独立 Native Plugin Host 进程加载，AAS Python 主服务仅通过认证本机 IPC 调用。该机制隔离原生崩溃，但不是恶意代码权限沙箱，仍只安装可信插件。

## 1. 文件与 YAML

Windows 使用 `.dll`，Linux 使用 `.so`。安装页面允许直接编辑/导入 `.c/.cpp` 源码，并可调用系统 GCC/Clang/MSVC **本机隔离编译**生成动态库；也继续支持用户在外部编译后直接导入 DLL/SO。编译环境隔离说明见 `docs/AAS_NATIVE_BUILD.md`。

检测算法示例：

```yaml
name: my_native_detector
label: 我的 C++ 检测算法
language: cpp
abi: aas_native_v1
library: my_native_detector.dll
type: native
dependencies: []
min_training_samples: 2
config:
  threshold: 0.5
```

评估指标示例：

```yaml
name: my_native_metric
label: 我的 C++ 指标
language: cpp
abi: aas_native_v1
library: my_native_metric.dll
requires: [labels, preds]
dependencies: []
```

安装后 `library` 会自动改写为 AAS 管理的相对路径。

## 2. 公共 ABI

头文件：`native_plugins/aas_native_v1.h`。

所有插件必须导出：

```c
const char* aas_plugin_abi(void);       // 返回 "aas_native_v1"
const char* aas_plugin_kind(void);      // detector 或 metric
const char* aas_plugin_last_error(void);// 推荐，返回最近错误
```

## 3. Detector ABI

必需：`create/destroy/fit/score/state_size/export_state/import_state`。`threshold` 可选；未导出时 AAS 使用配置中的 `threshold`。

状态导出/导入是必需的，因为 AAS 的训练流程会保存模型，之后还要支持模型发布和评估复用。

## 4. Metric ABI

指标通过 `aas_metric_compute(...)` 接收标签、异常分数、预测标签、训练/测试值和 JSON 配置。缺失的输入会以空指针 + 长度 0 传入。成功时把 `*ok` 设为 1。

## 5. 外部编译示例

Linux：

```bash
gcc -shared -fPIC my_native_metric.c -o libmy_native_metric.so
g++ -shared -fPIC my_native_detector.cpp -o libmy_native_detector.so
```

Windows（Visual Studio Developer Command Prompt）：

```bat
cl /LD my_native_metric.c /Fe:my_native_metric.dll
cl /LD /EHsc my_native_detector.cpp /Fe:my_native_detector.dll
```

模板已经包含 `__declspec(dllexport)` / GCC visibility 宏。

## 6. 当前范围

首期支持：

- 检测算法：C / C++
- 评估指标：C / C++
- DLL/SO ABI 隔离校验、运行期注册、使用、卸载；
- Detector 原生状态持久化，支持训练后保存/发布/评估；
- AAS 内部调用系统 MSVC/GCC/Clang 编译 C/C++ 源码，并与 Python/venv/Conda 构建环境隔离；
- 运行时使用独立 Native Plugin Host，DLL/SO 不进入 AAS 主服务进程，原生崩溃可被进程边界隔离。

暂不支持：其他五类插件的 C/C++ 实现，以及自动管理复杂第三方 C/C++ include/lib 依赖。运行时隔离的详细边界见 `AAS_NATIVE_RUNTIME_ISOLATION.md`。
