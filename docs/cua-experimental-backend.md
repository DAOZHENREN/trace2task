# Cua experimental backend

本地 D-5970、Qwen3-VL-2B、GUI-Owl 和 MAI-UI 可在「本地模型 · 本机 GPU」中选择
**Cua Driver · 实验性后台窗口执行**。Win32 仍是默认执行后端。Cua 是受范围限制的
后台窗口实验，不是通用、无人值守的桌面控制。

## 明确授权范围

开始前在程序中勾选 1–12 个现有窗口或可启动应用，并指定初始目标。每个已存在窗口的
授权身份是 `PID + window_id`；不会因同进程、同应用名或新弹窗而扩大授权。启动项只在
成功定位到其实际窗口后加入范围。关闭、最小化或无法定位的窗口会使任务停止，绝不回退
到无关窗口或前台输入。

计算器等应用的内部窗口可能仍存在，但不再出现在 Cua 顶层窗口枚举中。此时先以 Windows
原生 API 核对原 HWND 与 PID，再沿 `GetAncestor(GA_ROOT)` 获取它的确切宿主；只有该宿主
以相同 HWND/PID 出现在当前 Cua 列表且未最小化时才绑定，并在新截图上规划。不按窗口标题
猜测，不授权同一 `ApplicationFrameHost.exe` 进程下的其他窗口。规划期间身份或尺寸变化
仍会阻止旧动作。Cua 自己的光标覆盖层不会作为可选目标。

日志从绑定前就保存 `requested_scope`；成功映射保存 `window_binding` 的原始与有效身份，
失败保存 `window_validation_failed` 的具体原因；`result.json` 保存原始选择和成功绑定记录。

每轮使用当前目标的窗口截图和归一化窗口坐标。Qwen 2B 还会收到仅由选中目标构成的
窗口/应用目录；其他模型沿用各自既有输入协议，但执行器仍独立拒绝越界的窗口切换与
启动请求。没有自动前台回退或隐式全桌面授权。

## 动作与限制

基础支持：点击、双击、向唯一可编辑控件输入文本、按键、修饰键组合键和等待。文本目标
不唯一或控件树不完整时会拒绝输入。每个模型动作组先整体校验，但只执行第一项，然后
重新截图；最多 40 个动作，连续相同动作超过三次停止。

Qwen 2B 的 Cua 专用协议额外支持在授权目标之间切换、行/页滚动和显式起终点拖动。D、
GUI-Owl、MAI 不会被强制转换成这些扩展能力；它们输出不支持的操作会显式失败。Cua
0.28.2 没有独立 key-down/key-up 接口，因此不模拟任意长按或用前台 SendInput 补齐。

The Cua executable is external, not redistributed in the desktop installer.
Default: `D:/Tools/cua-driver-probe/v0.28.2/cua-driver-rs-0.28.2-windows-x86_64/cua-driver.exe`.
Override with `TRACE2TASK_CUA_DRIVER`. Each task owns a private named-pipe daemon;
it disables telemetry and terminates its own process tree on completion.

Every CLI request, raw reply, effect status and model I/O is recorded under
`runs/*-cua/trace.jsonl`; screenshots and result.json are adjacent. Exit code zero
does not imply success. Refusals and non-JSON error messages fail closed. Unknown
outcomes/timeouts stop without replay. `unverifiable` is logged and stops the run;
the action is never promoted to confirmed or automatically retried. Model done remains unverified.

F9 / 程序停止只在驱动调用之间检查；单次调用最长可等待约 20 秒，并不承诺立即原生取消。
所有请求、原始回复、效果状态、模型 I/O、截图和结果写入 `runs/*-cua/`。驱动退出码 0
不表示任务成功；`unverifiable`、超时和未知效果均停止且不重放，模型 `done` 也未验证。

尚未完成：Cua 窗口预览、语义效果验证、任意应用/模型的端到端资格验证，以及通用的
后台桌面自动化。请只用可丢弃的测试文档，不要用于敏感或不可逆任务。

## 2026-09-22 范围验证记录

离线截图推理（未执行键鼠）从 13 个窗口 + 262 个应用、5927 输入 token，缩减为选择
计算器窗口后的 1 个窗口 + 0 个启动项、805 token。证据：
`D:/MyProject/trace2task/runs/cua-selected-scope-validation-20260922/model-io/0000/input.json`。

内部窗口修复的只读实测：原计算器 HWND `201744`（PID `65516`）仍存在；Cua 枚举只返回
宿主 HWND `789612`（PID `31728`）。原生关系核验后成功绑定宿主并截图，输入目录仍仅包含
一个计算器窗口，截图和执行前检查的像素边界一致；未发送键鼠，前台窗口未改变。证据：
`D:/MyProject/trace2task/runs/cua-window-identity-validation-20260922/result.json`。
