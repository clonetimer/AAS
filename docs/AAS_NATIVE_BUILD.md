# AAS C/C++ 本机编译与环境隔离

AAS 可在“插件管理”页面直接编译 **检测算法** 与 **评估指标** 的 C/C++ 源码。编译器来自操作系统原生工具链，不把 Python 包、venv 或 Conda 作为 C/C++ 构建依赖。

## 1. 支持的工具链

AAS 自动检测：

- Linux/macOS：GCC/G++、Clang/Clang++；
- Windows：MSVC（Visual Studio Build Tools / Developer Command Prompt）；如系统安装 MinGW GCC/Clang，也可检测使用；
- 管理员可通过 `AAS_NATIVE_CC` / `AAS_NATIVE_CXX` 指定系统编译器路径。

没有检测到编译器时，仍可在外部完成编译，再使用“导入已编译库”。

## 2. 与 Python 构建环境的隔离

编译由独立子进程完成，并采用以下约束：

- 编译命令使用参数数组调用，`shell=False`；
- 构建工作区位于操作系统临时目录，不写入 Python venv；
- 移除 `VIRTUAL_ENV`、`CONDA_PREFIX`、`PYTHONPATH`、`PYTHONHOME` 等 Python 环境变量；
- 从 `PATH`、MSVC `INCLUDE/LIB/LIBPATH` 中移除 Python/venv/Conda 根目录路径；
- 忽略 `CFLAGS`、`CXXFLAGS`、`CPPFLAGS`、`LDFLAGS` 等外部注入参数；
- Web 页面不开放任意编译参数，使用 AAS 固定 C11/C++17、共享库、优化和警告选项；
- 生成的 DLL/SO 先在短生命周期 `python -I -S` 标准库校验进程中检查 ABI、插件类型和必需导出函数；校验进程不导入 AAS/NumPy/Torch。

> 这里的构建隔离不是恶意源码安全沙箱。编译器仍拥有启动 AAS 的操作系统账号权限。

## 3. 运行时也已隔离

当前运行链路已升级为：

```text
AAS Python 主服务
    -> 认证本机 IPC
Native Plugin Host（python -I -S / stdlib-only）
    -> ctypes
DLL / SO
```

因此 DLL/SO 不直接加载到 AAS 主服务地址空间。原生崩溃会终止 Host，而不是直接终止 Web 服务。详细机制见 `docs/AAS_NATIVE_RUNTIME_ISOLATION.md`。

## 4. 编译流程

```text
选择 C / C++
  -> 修改可编辑源码模板
  -> 选择系统编译器（或自动）
  -> 编译当前源码
  -> 独立校验进程检查 ABI / 导出函数
  -> 解析并校验 YAML + 动态库
  -> 安装并注册
  -> 独立 Native Plugin Host 运行
```

源码修改后，如果当前动态库由 AAS 自动编译生成，页面会自动将该动态库标记为过期并清除，要求重新编译，避免“源码与实际运行库不一致”。

## 5. 固定编译策略

GCC/Clang 默认：

- C：C11；
- C++：C++17；
- `-O2`；
- `-Wall -Wextra`；
- 共享库模式；
- Linux/macOS 使用 PIC / hidden visibility；
- Linux 增加未解析符号和基础链接加固检查。

MSVC 默认：

- `/LD /O2 /W3`；
- C++ 使用 `/EHsc /std:c++17`。

当前自动编译不开放自定义 include/lib 路径、宏定义或任意链接参数。需要复杂第三方 C/C++ 依赖时，建议先使用独立原生构建系统编译，再导入 DLL/SO。
