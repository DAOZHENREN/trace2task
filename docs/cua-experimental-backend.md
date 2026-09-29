# Cua experimental backend

程序入口在模型旁显示执行后端选择。目前支持全桌面 + Win32 前台输入，或指定窗口 / 应用 +
Cua 后台优先、明确拒绝时前台重试；不兼容的组合会提示并禁止启动，不会暗中改选。Codex、模型 API 和本地模型均可
在已授权窗口范围内运行；已确认的语义经验也能作为模型指导，但经验不是窗口授权。
这是尽力而为的窗口后台输入，不是“整个桌面后台执行”。本机 Cua 0.28.2 已验证
`get_desktop_state` 可只读获取主显示器截图，但桌面输入仍需前台，尚未替换全桌面
Win32 路径。

## 明确授权范围

开始前在程序中勾选 1–12 个现有窗口或可启动应用，并指定初始目标。可按窗口标题、程序名或
启动路径搜索；搜索只筛选列表，已勾选目标始终显示，也不会改变授权。每个已存在窗口的
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

每轮使用当前目标的窗口截图和归一化窗口坐标。Qwen 2B、GUI-Owl 和 MAI-UI 都会收到仅由选中目标构成的
窗口/应用目录、当前可用能力与未执行动作的拒绝反馈；模型沿用各自既有输出协议，执行器独立拒绝越界的窗口切换与
启动请求。没有隐式全桌面授权；只有 Cua 以 `background_unavailable` 明确拒绝后台输入，
才重新核验同一个已授权目标，短暂切前台对同一动作重试一次；成功后本次任务对该
窗口的后续动作直接走前台，不再重复探测后台。每次动作后尽可能恢复原焦点；切换
到另一个已授权窗口时，那个窗口仍先尝试后台。
若目标身份、位置或尺寸已变化，不使用旧坐标执行，而是重新观察。

## 动作与限制

基础支持：点击、双击、文本、按键、修饰键组合键和等待。完整控件树中只有唯一可编辑控件时，文本使用该快照的
token；完整树中目标不唯一或没有可编辑控件时，要求模型明确给出当前输入框坐标，走 Cua 后台坐标文本输入。
控件树不完整时，未带坐标的 `type_text(text)` 会原样交给 Cua 的当前焦点文本路径；Trace2Task 不猜测坐标、
不借用旧点击位置，也不把该调用当作效果已确认。这个路径可能写入该已授权窗口中当前获得焦点的错误字段，或被
Cua/应用明确拒绝，因此每次都重新截图并让模型判断效果。每个模型动作组先整体校验，再按顺序逐项执行；
目标变化、拒绝、不确定回执或停止请求会中断剩余动作，随后重新截图。是否无进展交给模型判断，
不按重复次数或像素变化认定失败。

所有 Cua 模型统一经过 [动作协议与执行核心](unified-execution-core.md)。确定尚未发送输入的
能力拒绝会反馈给模型重新规划，不加入执行历史，连续三次拒绝后停止。窗口标题不是文本
输入框，即使暴露 `set_value` 也不会作为输入目标。权限、身份、参数错误仍直接停止。

Qwen 2B 的 Cua 专用协议已有授权目标切换。本次为 GUI-Owl、MAI 适配行/页滚动和显式起终点拖动，
具体格式及后台嵌套区域限制见统一协议文档；不把移动端 swipe 或隐式鼠标起点悄悄转换。Cua
0.28.2 没有独立 key-down/key-up 接口，因此不模拟任意长按或用前台 SendInput 补齐。

The Cua executable is external, not redistributed in the desktop installer.
Default: `D:/Tools/cua-driver-probe/v0.28.2/cua-driver-rs-0.28.2-windows-x86_64/cua-driver.exe`.
Override with `TRACE2TASK_CUA_DRIVER`. Each task owns a private named-pipe daemon;
it disables telemetry and terminates its own process tree on completion.

Every CLI request, raw reply, effect status and model I/O is recorded under
`runs/*-cua/trace.jsonl`; screenshots and result.json are adjacent. Exit code zero
does not imply success. Refusals and non-JSON error messages fail closed. Unknown
outcomes/timeouts stop without replay. Only a successful receipt without any error/refusal/code
can be treated as an accepted driver action. `unverifiable` alone never proves delivery;
`background_unavailable` is an explicit refusal even if accompanied by `unverifiable`.
For an accepted driver action with `unverifiable`, the driver cannot confirm its effect:
record it, discard the rest of the batch, take a new
screenshot and ask the model for its next decision. Never promote that receipt to confirmed or
automatically resend the action. Model done remains unverified.

F9 / 程序停止只在驱动调用之间检查；单次调用最长可等待约 20 秒，并不承诺立即原生取消。
所有请求、原始回复、效果状态、模型 I/O、截图和结果写入 `runs/*-cua/`。驱动退出码 0
不表示任务成功。先分类拒绝与错误，再处理无拒绝的 `unverifiable` 成功回执，保留效果待确认的历史，丢弃本批
剩余动作，重新截图让模型判断下一步，不会自动重发。生成式模型的输入中会明确说明该状态
既不等于输入失败，也不等于任务完成。截图本身不会把效果自动标为“已确认”。超时、拒绝、
驱动错误及其他未知回执仍会停止；窗口身份/尺寸校验不变。`background_unavailable`
会记录后台拒绝，再对相同的已授权窗口进行一次前台尝试；若 Windows 不允许聚焦，
则不发送前台输入。`unverifiable` 本身、超时、partial 或泛化错误不会触发回退或
重复发送。前台尝试可能短暂占用用户焦点，恢复原焦点也是尽力而为。可丢弃 Edit 控件探针证明
`effect=unverifiable` 也可能没有实际写入文本，因此只能重新截图由模型判断，
不能作为应用效果或任务成功。模型 `done` 触发新截图只读完成核验。
进展由模型结合操作前/当前截图和真实历史判断，不再使用重复动作启发式；完成视觉核验仍不是独立应用验证。

时间轴使用 `effect_pending` 记录待确认动作，`effect_reobservation` 关联之后的新截图和
下一轮规划；不再因 `unverifiable` 本身生成终止事件。旧日志中的 `effect_unverifiable`
停止原因仍可查看。

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
# Cua 全桌面接入（2026-09-28）

在“操作范围”选择全桌面、“执行后端”选择 Cua，Codex、兼容 API 和本地模型均通过共同循环执行。
主显示器使用 `get_desktop_state` 原始物理像素截图，动作使用
`target={kind: desktop, display_id: primary}`。指定窗口模式保持原有后台优先策略。
桌面模式使用系统键鼠，可跨应用；支持点击、双击、移动/悬停、文本、按键、组合键、滚动、拖动和等待。
未提供独立按住/松开及应用 ID 启动接口；未支持动作明确返回拒绝，不能冒充送达。
屏幕尺寸/缩放改变后重新观察，普通画面变化不会丢弃模型给出的多步动作。
模型原始输出、统一动作、驱动请求与逐动作回执沿用现有日志。

本机真实验证：2560×1600、150% 缩放，临时 Tk 输入框点击并输入 `Cua desktop OK` 成功。
运行 `scripts/local_gui/validate_cua_desktop.py <输出目录>` 可复核，测试会短暂占用键鼠。
这证明驱动和坐标接入，不代表模型在任意任务中的成功率。
