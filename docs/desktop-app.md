# Windows 桌面控制台

## 独立安装版（Windows x64 试用）

安装包位于 `dist/installer/Trace2Task-Setup-0.18.1-win64.exe`。
运行安装程序可改选 D 盘，不需要管理员权限。程序自带 Python 和应用依赖，
不需要源码、uv 或系统 Python；使用系统 WebView2 Runtime。
此包尚未签名，Windows 可能提示未知发布者，请核对来源，不要关闭系统安全保护。

首次打开选择一个独立的数据目录，可直接选择 `D:\MyProject\trace2task` 复用旧数据。
选择会保存到 `%LOCALAPPDATA%\Trace2Task\desktop-settings.json`。
不要选安装目录；程序会拒绝将数据置于安装目录内。
需要换目录可使用 `Trace2Task.exe --project-root "D:\你的数据目录"`；这次覆盖不修改保存的选择。

可从 Windows“已安装的应用”卸载。卸载只删除程序及快捷方式，不删除上述设置、
任务、经验、运行日志、API 密钥存储和模型目录。重复启动同一数据目录会提示已运行。
关闭本软件不会自动退出独立模型服务。网页的「关闭本地模型服务」可在没有活动任务时
关闭本项目识别到的 D、Qwen 和 GUI 模型服务以释放显存；不会删除权重或日志。

API 模式仍需用户配置密钥，Codex 模式仍需单独安装登录 Codex CLI，
Qwen/D 模型及其推理环境是可选外部服务，不包含在安装包内。
打包不代表已验证所有云服务、录音、WAA 或真实桌面执行场景。

构建：安装项目 desktop/dev 依赖及 `packaging/windows/requirements-build.txt`，
然后运行 `scripts/build-desktop.ps1 -Iscc "D:\Tools\InnoSetup\ISCC.exe"`。
PyInstaller 将程序打成目录，Inno Setup 6 将其制作为安装包；构建产物不进入 Git。
参考：[PyInstaller](https://pyinstaller.org/en/stable/usage.html)、
[Inno Setup](https://jrsoftware.org/isinfo.php)。

桌面壳复用现有网页、HTTP 后端、任务执行器和数据格式；没有增加第二套 Agent。
使用 pywebview + Microsoft Edge WebView2，模型仍在独立服务中常驻。

## 安装与启动

首次在源码目录运行 `uv sync --extra desktop`。Windows 需要安装 Edge WebView2 Runtime。
之后双击根目录 `Start Trace2Task.vbs`，无需浏览器或终端。
启动错误记录在所选数据目录 `runs/desktop/desktop.log`。

命令行入口：`uv run --extra desktop trace2task desktop`。
需要桌面快捷方式时运行 `scripts/install-desktop-shortcut.ps1`；脚本不会覆盖已有快捷方式。
两个启动脚本都接受 `-ProjectRoot` 来选择原项目数据目录。
如在 worktree 中开发，但要读取原来的任务和运行记录：

```powershell
uv run --extra desktop trace2task desktop --project-root D:\MyProject\trace2task
```

默认数据目录是启动时的当前目录；双击入口使用源码目录。
不要同时从网页和桌面控制台运行桌面任务，两者不共享任务锁。

## 退出行为

- 运行、录制或待处理讲解时阻止关闭，请先在页面停止/处理任务。
- 空闲时确认退出，关闭本窗口拥有的 HTTP 服务；不会停止其他网页服务。
- 本地 D/Qwen/GUI 模型是独立服务，关闭窗口本身不卸载模型，不释放其显存；请在关闭前
  使用网页的「关闭本地模型服务」主动释放。
- 当前没有托盘或“退出后继续后台运行”模式。服务控制只会处理本项目已识别的模型进程，
  不会按端口或泛用 Python 进程强行终止未知程序。

后台仅监听随机分配的 `127.0.0.1` 端口，无远程接口；没有向页面暴露通用 Python 桥。
源代码模式安装依赖需要网络；启动不会重新下载模型或自动运行任务。

实现参考：[pywebview 官方 API](https://pywebview.flowrl.com/api/)。

本机验证：pywebview 6.2.1，WebView2 153.0.4234.48；真实窗口 DOM 加载完成，
自动关闭测试窗口后服务生命周期正常返回；62 项桌面壳及现有控制台测试通过。
没有在验证过程中运行 Agent 或向桌面发送预测动作。尚未逐项验收原网页所有录音/文件对话框功能。
