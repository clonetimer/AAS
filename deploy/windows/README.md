# Windows 离线部署工具

本目录是离线交付包的部署脚本唯一来源。构建离线包时，脚本会被复制到 ZIP 根目录。

- `Deploy-AAS.ps1`：校验介质、预检空间、安装 Python 和依赖、建立/迁移数据库。
- `Start-AAS.ps1` / `Stop-AAS.ps1`：启动服务并通过本机随机令牌执行受控停止。
- `Uninstall-AAS.ps1`：默认保留用户数据；显式选择后才允许删除。
- `*.cmd`：供普通用户双击执行的入口。

Windows PowerShell 5.1 / Windows 10-11 x64 是当前目标。所有脚本都必须在干净 Windows 虚拟机中完成实机验收后才能标记为正式发布。
