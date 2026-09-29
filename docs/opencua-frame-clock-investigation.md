# OBS 帧时钟接入调查（2026-09-28）

> 后续进展：构建工具及诊断插件已安装并实测；CTS 直接对齐方案未通过前态验证。以下是调查阶段记录，最新结果见 [插件验证报告](obs-frame-clock-plugin.md)。

## 当前结论

已有可用的底层接口，但**尚未实现帧时钟采集与生产抽帧接入**。现有启动确认时间仍然是近似原点，不能改名冒充首帧时间。

1. 项目冻结的 AgentNetTool `Recorder` 用 Python `time.perf_counter()` 标记键鼠回调时间。
2. Windows 下 OBS 32.2.2 的 `os_gettime_ns()` 使用 QPC；本机 Python 3.12 `perf_counter` 也使用 QPC。
3. 实际加载专用 OBS 安装的 `obs.dll`，连续 1000 次把 OBS 读数夹在两次 Python 时钟读数之间，1000 次均落入区间。首次测量的区间宽度为 100～7900 ns。这只验证同机时钟一致性，不是视频对齐误差。
4. OBS WebSocket 的录制状态接口没有每帧 CTS 与 PTS 的映射，启动通知不能替代该映射。

复现同源时钟检查：

```powershell
& D:/Trace2Task-deps/opencua-runtime/Scripts/python.exe scripts/opencua/probe_obs_clock.py
```

不启动 OBS，不录屏，不注入输入，不改变官方数据。

## 可以使用的官方接口

OBS 32.2.2 的 `obs_output_add_packet_callback` 可在输出编码包时读取 `encoder_packet` 与 `encoder_packet_time`。后者将 PTS 与 CTS（画面合成时间）关联；CTS 使用 `os_gettime_ns()`。

必须把 CTS 称为 **OBS 画面合成时间**，不能承诺它就是显示器硬件采集时间。输出回调到达时再调用时钟也不对，因为编码和排队已造成延迟。

参考固定版本源码：

- [Windows QPC 实现](https://github.com/obsproject/obs-studio/blob/32.2.2/libobs/util/platform-windows.c)
- [帧时间结构与字段含义](https://github.com/obsproject/obs-studio/blob/32.2.2/libobs/obs-encoder.h)
- [输出回调声明](https://github.com/obsproject/obs-studio/blob/32.2.2/libobs/obs.h)
- [回调调用与空时间记录处理](https://github.com/obsproject/obs-studio/blob/32.2.2/libobs/obs-output.c)
- [WebSocket 录制接口](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md#getrecordstatus)

## 所需接入，不修改官方原始文件

需要一个运行在 OBS 进程内的轻量原生模块。外部 Python 加载同名 DLL 只能验证时钟，无法访问另一个 OBS 进程中的录制对象。

模块在录制输出开始前挂接回调，复制 PTS、timebase、CTS 到有界缓冲，另行写入附加文件；不能在编码回调中做重 I/O 或修改视频包。要处理首帧遗漏、空 timing、停止/卸载并发和暂停时间轴。

抽帧前须验证编码包 PTS 与最终 MP4 解码 PTS 的对应关系，包括重排序和容器起点变化。优先按逐帧 CTS 选择事件之前的最后一帧；不要仅假设所有帧等间隔，也不要先把 DTS 当 PTS。

新信息应存入 Trace2Task 附加文件，官方 metadata、events、视频保持原样。拿不到时钟对应关系时显式标为近似；不自动套用固定毫秒补偿。

## 当前落地条件

本机 `vswhere -all -products *` 返回空，常用 PATH 中没有 cl/clang/gcc/cmake。进程内原生模块尚未构建、安装或实测，需要补充 Windows C/C++ 构建工具后继续。没有因此修改生产抽帧或升级安装版本。

验收应包含：首帧 CTS 与最终视频 PTS 完整关联、真实键鼠事件测试、重复启动、较长录制、停止后完整落盘；通过之前不能宣称已解决同步。
