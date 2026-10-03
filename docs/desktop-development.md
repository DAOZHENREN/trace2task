# 桌面开发版

双击仓库根目录 `Start Trace2Task Dev.vbs`，或桌面的“Trace2Task 开发版”快捷方式。
它使用本仓库 `.venv` 和 `src`，打开独立 WebView2 桌面窗口，不启动外部浏览器，不运行安装目录里的旧程序。

默认沿用已保存的数据目录（当前为 `D:\MyProject\trace2task`）；没有设置时弹出目录选择。
安装版和开发版共用数据目录的单实例保护：先退出原程序再启动，不会自动强杀正在运行的任务。
数据会真实保存到所选目录，这不是沙盒；需要隔离试验时可指定另外的数据目录。

默认启动会请求 Windows 管理员权限（UAC）；取消授权则停止，不降级运行。
开发版快捷方式、脚本和 `python -m trace2task.desktop_app` 共用此入口；重新构建的安装版 EXE 使用管理员清单。
Cua 子进程继承管理员权限，但这不保证游戏支持后台输入或独立窗口截图。
旧进程的权限不能原地提升：先停止任务并退出旧窗口，再启动；不会自动强杀旧实例。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-desktop.ps1 -Development
# 可选：独立测试数据目录
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-desktop.ps1 -Development -ProjectRoot D:\Trace2Task-dev-data
```

新工作台使用 React + TypeScript + Fluent UI，详见 [工作台说明](workbench.md)。修改 `frontend/src` 后先运行 `npm run build --prefix frontend`，再重新加载页面；修改 Python 后退出并重新打开开发版。不需要重新打包或安装，不自动热重载正在运行的任务。旧录制与高级编辑器保留在兼容工具中。
增加依赖时才需要在仓库运行 `uv sync --extra desktop`；普通启动不联网安装依赖。
启动错误日志：`%LOCALAPPDATA%\Trace2Task\logs\source-launch-*.stderr.log`。
运行日志：数据目录下 `runs\desktop\desktop.log`。

快捷方式依赖此源码目录，不要删除或归档该工作树。正式发布时仍保留安装版打包流程。
