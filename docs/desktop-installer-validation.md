# Windows 安装包验证（2026-09-21）

这是当日安装包的历史验收，不代表后续源码修复已进入该二进制。2026-09-22
源码已加入模型服务管理及发布前审查修复；重新构建并验收前，不应把旧包当作最新版本。

- 包：`Trace2Task-Setup-0.18.1-win64.exe`，未签名的试用构建。
- SHA256：`4c82981f32d2e66ea0790ec0f140630abb74b55293060cdc33685b82604b5846`。
- 工具：PyInstaller 6.16.0、pywebview 6.2.1、Inno Setup 6.7.3。
- 官方 Inno Setup 工具签名验证：Valid，Pyrsys B.V.。
- Python 运行时、网页资源和应用依赖随包分发；未打包用户任务、密钥或模型权重。
- 安装到 `D:\Apps\Trace2Task-PackagingTest` 返回 0。
- 从安装后的 EXE 启动，工作目录为系统 Temp，真实 WebView2 DOM 返回
  `{"ready":"complete","console":true}`，测试窗口正常退出。
- 卸载后测试 EXE 已移除，外部数据目录中的 sentinel 文件保留。
- 正式安装到 `D:\Apps\Trace2Task` 返回 0。
- 66 项桌面控制台、模型客户端及网页控制台测试通过，相关 lint 通过。

本次只验证应用生命周期，不等同于全部模型/录音/WAA/桌面动作的端到端验收。
独立模型服务的加载/卸载管理、代码签名、自动更新和托盘仍未实现。
卸载不停止外部模型，也不删除用户数据与设置。
