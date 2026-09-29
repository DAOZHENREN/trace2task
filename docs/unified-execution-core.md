# 统一动作协议与严格执行核心（v1）

当前本地 GUI 模型入口已共用同一条观察、规划、执行、回执、再观察循环。GUI-Owl、MAI-UI、Qwen 与 D-5970 的计划进入版本化动作协议；执行后端可选 Win32 全桌面前台输入或 Cua 已授权窗口输入。Trace/编译经验的上层流程未被重写。没有新增运行依赖。

```text
GUI-Owl computer_use / MAI-UI mobile_use / Qwen JSON / D-5970 结构化动作
             ↓ 各自适配器（不操作电脑）
版本化 ActionPlan {protocol_version, coordinate_space, actions}
             ↓ execution_core.ExecutionCore
整批参数校验 → 全部目标授权 → 能力预检 → 实时窗口核验 → 停止检查
             ↓
  Win32ExecutionBackend       CuaExecutionBackend
  全桌面前台输入               已授权窗口后台输入；仅明确拒绝时一次前台回退
             ↓
执行回执与新截图 → 下一轮模型
```

## 协议与边界

- 协议版本 `1`。规范计划为 `{"protocol_version":"1","coordinate_space":"observation_normalized_0_1","actions":[...]}`；旧的仅含 `actions` 的计划仍可在核心入口解析，但本地模型适配器向共享循环输出规范计划。未知版本或坐标空间会在发送输入前拒绝。沿用已有 `ActionCall` 参数约束；新增的滚动、窗口切换和启动应用也由同一协议验证，不再使用无校验的临时对象。
- 点击/拖动坐标统一为当前观察截图归一化 `0..1`。GUI-Owl 原始 `0..1000` 与 MAI-UI `0..999` 在模型适配器中转换。Cua 后端转换为窗口截图像素，不混入桌面坐标。
- 目标身份、观察 ID、实际授权和后台交付方式由宿主程序绑定，不允许模型输出覆盖。应用启动只能引用用户选中目录中的 `app_id`，不接收任意路径或命令。
- 最多 8 个动作；整批格式与授权先校验，再逐步检查目标和后端能力并依次执行。普通画面重绘不截断批次，批次结束重新观察；目标/坐标绑定改变或执行结果不确定时停止剩余动作。
- `done` 是模型停止声明，不是操作系统动作或任务成功证据。空计划、未知字段、错误坐标、非布尔 done、无效键名都拒绝。
- 执行核心消费一次观察后，不接受同一观察再次执行。失败终止后核心不可复用。最多 40 轮；不根据重复次数或像素变化认定任务无进展。

## 状态语义

| 状态 | 已送输入？ | 后续 |
|---|---|---|
| 参数错误、越权、窗口身份改变 | 否 | 停止，不放宽权限 |
| 前台/分辨率或已授权窗口几何变化 | 否 | 丢弃旧坐标，重新截图规划；连续 3 次变化停止 |
| 能力预检拒绝 | 否 | 独立反馈给模型，新截图重规划；连续 3 次拒绝停止 |
| background_unavailable | 后台路径被明确拒绝 | 保存原始回执；仅在同一已授权窗口身份/尺寸仍有效时，对同一动作前台重试一次 |
| foreground_unavailable | Win32 未确认精确目标 HWND 已前台 | `executed=false`；不调用 Cua 前台/全局输入，停止 |
| delivered / confirmed | 是 | 记录真实历史，重新观察；不等于任务成功 |
| delivered / unverifiable | 驱动已接受，实际效果未知 | 记录 `effect_pending`，重新观察交给模型判断；不等于任务成功，绝不自动重发 |
| 驱动错误、超时、partial、未知效果 | 可能部分送达 | 停止，不自动重试 |
| completion_requested | 无新增输入 | 新截图只读核验，核验结果绝不送执行器 |
| visual_completion_reviewed | 无新增输入 | 同一本地模型视觉证据支持完成；`task_complete=true, verified=false`，不是独立应用验证 |
| completion_unverifiable | 无新增输入 | 核验无法判断、格式错误或不支持；停止等待人工检查，不重试潜在提交 |
| completion_rejected | 无新增输入 | 首次明确未完成交回规划；累计两次明确未完成则停止 |

只有 `ActionUnavailable`（确定发生于分发之前）、无输入时检测到的观察几何变化，以及**顶层精确为** `background_unavailable` 的后台拒绝可恢复。后者仅可对同一动作、同一已授权窗口前台重试一次：先使用 Win32 尝试聚焦该精确 HWND，并读回前台 HWND 确认成功，才允许发送 Cua 的前台调用；Windows 拒绝聚焦时不发送任何全局输入。窗口身份变化、前台回执错误/超时/未知效果时均停止。若后台已明确拒绝，随后发现窗口仅几何变化，则不进行前台回退，而是重新截图规划。没有将所有异常一概包装成可重试错误。

## 模型判断进展与完成核验

GUI-Owl、MAI-UI、Qwen 的 Cua 规划收到原任务、最近四步真实执行历史，以及明确标注的“上次动作前 / 当前”截图。由模型判断任务有没有推进、是否需要改变策略；代码不使用像素变化比例、近邻坐标或相同动作次数作出这个判断。模型只能使用当前图定位。最多保留一张对照图，不逐轮累积图片；两张图及其顺序进入完整输入归档，仍受 6144 token 上限约束，不静默截断。

权限、参数、目标身份、尺寸、紧急停止、总轮数以及不支持能力的拒绝仍由代码处理；这些是执行边界，不是语义进展判断。超时/部分执行不自动恢复。D-5970 的冻结输入协议未新增双图支持。

### 回执不能只看 effect

实际失败日志曾返回 `{"code":"background_unavailable","effect":"unverifiable","verified":false}`。明确拒绝优先于效果字段：只有这个精确代码才允许一次前台回退；重试前必须重新核对 PID、窗口 ID 和尺寸，且前台调用只复用原动作及其已经验证的参数。未知错误码、错误和超时保持结果未知并停止。无拒绝/错误的 `unverifiable` 可作为“驱动已接受、效果待确认”进入真实执行历史，但永远不是效果成功或任务完成。

前台回退会短暂抢占同一目标窗口的焦点，驱动会尝试在调用后恢复仍属于原 PID 的前台 HWND；因此运行时不应同时操作键鼠。它不是任意窗口的前台授权，也不会因 `unverifiable`、`partial`、超时或泛化错误而触发第二次发送。若 Win32 未确认精确目标 HWND 已成为前台，宿主以 `foreground_unavailable` 在调用 Cua 前停止，避免全局输入落到错误窗口。这是一次调用前的时间点核验，不是原子“焦点租约”：用户若恰在短暂前台调用期间切换窗口，Windows/驱动仍可能存在竞态，因而界面必须提醒用户不要操作键鼠。回退后的 `unverifiable` 仍只生成 `effect_pending` 并重新截图，由模型决定是否改变策略；不能把它宣传为应用已收到输入。

可丢弃 Win32 Edit 的前台回退探针曾收到 Cua `effect=unverifiable`，但控件文本没有改变。这说明该回执只是驱动选择/尝试了输入路由，不能视为应用效果的证明；正式任务必须重新截图，由模型以可见证据判断进展或完成。

本机 v0.28.2 的滚动/拖动成功回执还会出现 `route=global_input, delivery.mode=unknown`。这是驱动报告的交付方式不明确，原样归档，不冒称后台交付已独立核验；宿主请求仍固定 `delivery_mode=background`。自建控件的状态读回和前后前台 HWND 比较是独立测试证据，不推广到所有应用。

### 滚动 / 拖动适配

- GUI-Owl：`scroll(pixels=-3)` → 向下 3 行；`hscroll` 正数向右。此处 `pixels` 是保留的字段名，本地明确规定为行数，不宣称等于物理像素或原版滚轮刻度。
- MAI：提供 Windows 专用 `scroll(direction,amount,by)`；不把移动端 `swipe` 猜成相反方向的滚轮。
- GUI-Owl `left_click_drag` 增加必须的 `start_coordinate`，`coordinate` 为终点；MAI `drag` 使用 `start_coordinate/end_coordinate`。默认 500ms，最大 5000ms。没有明确起点就拒绝，不借用人的鼠标位置。
- 归一化到统一协议后再转窗口像素。滚动为 Cua 的行/页消息，嵌套区域未必接收；坐标指定子区域的后台滚动明确拒绝，可由模型另选滚动条拖动。不自动点击改变焦点，不自动切前台。
- 仅扩展 GUI-Owl/MAI 的 Cua 动作适配；没有新增跨应用操作、长任务记忆或恢复。

原生测试：`runs/cua-motion-root-probe-20260922/result.json`，两种格式的滚动分别使首行从 0 到 3、0 到 12，拖动均选中字符 1–20，前后前台 HWND 相同。另一个嵌套 Edit 测试确实未响应滚动，保留在 `runs/cua-motion-probe-20260922/result.json`。因此不能宣传通用后台兼容。

双图 GPU 测试：`runs/cua-motion-gpu-feedback-20260922/result.json`（未发送键鼠）。GUI-Owl 两次都输出带 coordinate 的滚动，被纯参数预检拒绝；MAI 输出向下 30 行，格式可执行，但比“几行”激进。两者生成约 2.4 秒、1.3 秒，峰值已分配显存约 4.79、4.39 GiB；这是 622×351 测试截图，不代表完整任务速度或最大图像预算。输入、原文、解析、预检、耗时、显存均保留。

本轮验证：243 项相关 Python 回归、1 项时间轴测试、Ruff 通过。新包 `dist/cua-model-progress-motion/Trace2Task` 独立启动检查通过：`runs/cua-model-progress-motion-smoke-20260922/desktop-smoke.json`。安装状态以实际部署记录为准。

GUI-Owl、MAI-UI 和 Qwen 的完成声明触发一次额外本地推理：新截图、原任务、真实历史，要求只读 JSON `verdict/evidence/missing`，不提供动作工具。complete 必须有非空证据、空缺项；incomplete/unknown 必须说明缺项。原始回答、截图、耗时、解析结果全部归档为 `purpose=verify_completion`，不进入动作历史。

这不是独立模型或确定性应用断言：视觉通过保留 `verified=false` 并标明 `same_model_visual_review`。unknown 和无效回答停止而非盲重试。D-5970 不支持该只读输出协议，完成后明确提示人工核验。当前仍为逐动作观察，不借此引入批量执行或自动前台降级。

2026-09-22 离线 GPU 验证：`D:/MyProject/trace2task/runs/cua-completion-review-20260922/result.json`。对历史微信空输入截图，GUI-Owl 返回 incomplete；MAI 返回 complete 但同时声称尚未发送并给出缺项，被严格校验拒绝。整个验证没有发送键鼠或消息。可复现脚本：`scripts/local_gui/validate_completion_review.py`。

历史版本曾通过 227 项 Python 回归、4 项界面测试和 Ruff，并验证了近邻重复点击拦截；该启发式已按用户要求撤除，旧测试结果不作为本版进展判断的依据。旧打包目录 `dist/cua-progress-review/Trace2Task` 和对应 smoke 日志仅为历史记录。

用户退出后，已将 7 个变更文件更新至 `D:/Apps/Trace2Task` 并核对哈希，无文件删除。旧文件备份：`D:/Apps/Trace2Task-update-backups/cua-progress-review-20260922`。安装版已使用原数据目录重新启动，状态接口可用，17 个任务仍可见；GUI-Owl 本地服务已从安装目录启动并通过健康检查。界面版本号仍为 0.18.1（此增量未调整版本号）。

## 控件能力

每轮给 GUI-Owl、MAI-UI 和 Qwen 提供当前能力和最近的拒绝原因，独立于“真正执行过的动作历史”。不支持的原生动作格式不因驱动具有该能力而自动启用。

完整控件树中，不带坐标的文本输入要求唯一启用的可编辑文本角色、`set_value` 能力和快照 token；窗口标题的 `set_value` 不能冒充输入框。完整树但目标不唯一时，模型必须显式输出 `type_text(text,x,y)`；GUI-Owl / MAI-UI 的对应原生格式是 `type(text,coordinate)`。坐标必须来自当前窗口截图，Cua 点击该输入位置再输入，仍为 background，不能借用旧点击坐标。控件树不完整时，`type_text(text)` 改走 Cua 原生的当前焦点文本路径：宿主不猜坐标、不伪造 UIA token，也不降级为全局桌面输入。该路径只能说明 Cua 已接受一次对同一已授权窗口的调用；当前焦点可能不是模型想要的字段，效果仍可能未知或被驱动拒绝，故必须重新截图由模型判断，不能标为任务成功。只有驱动以顶层 `background_unavailable` 明确拒绝时，才会在重新核验同一目标后前台重试该动作一次；`unverifiable`、超时和其他错误不触发重试。

本版没有让模型直接选择任意 UIA token，也没有对一般驱动拒绝自动重试。前台回退仅覆盖 Cua 明确定义的 `background_unavailable`；其他拒绝是否无副作用尚未证明。

### 工具调用分隔符与状态日志

正常 EOS 结束、唯一完整 JSON 工具调用仅遗漏 `</tool_call>` 时，允许适配并记录 `protocol_normalizations`；取消生成、达到长度上限、残缺 JSON、多调用、重复 JSON 字段和额外尾部内容仍拒绝。原文与 token 日志不改写。

模型响应使用 `phase=prediction`、`execution_status=not_executed_by_model_service`，不再放置容易误读的 `executed=false` / `task_success_verified=false`。每轮真实执行结果另存 `model-io/NNNN/execution.json`，程序时间轴显示已送达/待确认/拒绝，不能把动作效果当作整任务成功。

坐标输入实测证据：`D:/MyProject/trace2task/runs/cua-coordinate-text-probe-20260922/result.json`。自建可丢弃 Win32 Edit 控件成功读回“Trace2Task 测试”，前后 foreground HWND 相同。该检查不是连续焦点监控，也不证明微信或所有应用都支持后台输入；未向任何联系人发送消息。复现脚本：`scripts/local_gui/probe_cua_coordinate_text.py`。

微信历史截图离线 GPU 测试：`D:/MyProject/trace2task/runs/cua-wechat-text-preview-20260922/`。MAI-UI 再现缺少结束标签的完整 JSON，现已解析通过；初次仍未提供输入坐标，被预检拒绝。收到拒绝反馈后生成带 `[267,877]` 的文本动作，归一化通过。没有把这些离线预测发给微信。

该增量修复通过 202 项 Python 回归测试、4 项界面测试及打包启动检查；已替换安装目录的 8 个变更文件并逐个核对哈希，重新启动原数据目录的程序及 MAI-UI 服务。旧文件备份：`D:/Apps/Trace2Task-update-backups/cua-text-20260922-184018`。

## 日志与验证

`trace.jsonl` 新增 `execution_plan`（协议、坐标空间、目标、观察 ID）、`execution_result` 和 `execution_stopped`；保留模型原文、实际输入、驱动请求/回执及各阶段耗时。拒绝动作不会加入 `executed=true` 历史。

### 2026-09-23 共用循环增量

`local_agent_loop.py` 现在承载 Win32 和 Cua 两条本地模型执行路径；观察、执行分别由 `local_observation.py` 和两个后端实现。Win32 仅对截图时绑定的前台 HWND 和主屏幕尺寸发送输入，回执标记 `effect=unverifiable`；焦点或尺寸变动时旧计划不执行。Cua 保持精确窗口授权和后台拒绝时的受限前台回退；窗口位置/大小改变但身份未变时重新截图，身份改变仍拒绝。

GUI-Owl 与 MAI-UI 两种原生工具格式 × Win32 与 Cua 两种后端的四组假驱动集成测试已通过：同一归一化点击只分发一次、未确认效果进入真实历史、下一轮重新观察、完成声明须只读核验。完整 Python 回归和修改文件的 Ruff 检查也通过。

已确认的 Trace/编译经验现在也可作为**可选的上层模型输入**用于 GUI-Owl、MAI-UI、Qwen 的本地桌面任务及截图预览。任务选择器保留“无经验 Baseline”；选择经验时传入任务状态图和人工规则，并在每轮请求、服务端输入归档中记录。经验只是当前画面的解释线索，不授予额外窗口或动作权限，也不把示范坐标当作待执行脚本。Cua 目标仍由用户单独授权；执行核心和两种后端不读取经验。单次经验上下文限定为 JSON 和 12,000 字符，超出时显式拒绝，不截断成可能误导模型的半份规则。D-5970 的冻结 record 不支持此字段，仍须使用无经验入口；没有偷偷拼入任务文字。当前是静态状态图/规则投影，尚未实现运行时按状态检索，也不表示模型必定会正确使用经验。

2026-09-23 安装验收：`dist/goal-unified-loop-20260923-v3/Trace2Task` 的独立启动检查返回 `{"ready":"complete","console":true}`；更新到 `D:/Apps/Trace2Task` 后同样启动成功，原数据目录中的 17 份任务经验仍可见。更新前的完整安装备份在 `D:/Apps/Trace2Task-update-backups/goal-unified-loop-20260923`，最后一轮三个文件的备份在相邻的 `goal-unified-loop-20260923-v3`。GUI-Owl 常驻服务已恢复并通过健康检查。仅用自建临时文本框截图的无键鼠预测确认经验字段进入服务端实际提示与归档：`runs/goal-experience-preview-20260923/`；这不证明该模型能完成真实任务。安装版 UI 版本号仍为 0.18.1，本增量未单独发布新版本号。

进一步用 `scripts/local_gui/validate_native_agent_loop.py` 在**自建、一次性销毁的 Win32 Edit 文本框**上完成四组原生闭环。Cua 两组记录于 `runs/native-loop-cua-dpi-probe-20260923/summary.json`，Win32 两组记录于 `runs/native-loop-win32-focus-probe-20260923/summary.json`；四组均执行 2 个动作、重新截图、读回精确文本 `Trace2Task fixture`，完成请求经只读复查后保留 `verified=false`。脚本使用两种模型的**固定原生工具调用文本**并经过真实适配器解码，不调用模型/GPU；它验证适配器、循环、驱动和结果归档，不证明模型理解任务或任意应用的后台兼容性。Win32 只在临时窗口确实取得前台时运行，否则在输入前停止。首次探针因 DPI 坐标混用而未命中 Cua 文本框，已改为在物理 DPI 上下文读取控件矩形后重测通过，失败证据仍在 `runs/native-loop-probe-20260923/`。

早期版本每轮只执行计划的第一步，剩余动作记录为丢弃；现已改为模型输出几步就顺序尝试几步。旧逻辑按整屏像素相似度判断能否继续，动态壁纸和正常 UI 跳转都可能误触发；新逻辑不把普通画面变化当中断条件，但每步仍核对目标身份、几何、动作能力和停止信号。模型须自行决定哪些后续动作无需新截图即可定位。

### 2026-09-23 模型格式与后端能力进一步解耦

本地 GUI 模型服务现在只把 GUI-Owl / MAI-UI 原生工具调用及 Qwen JSON 解析成同一份 `ActionPlan`；解码不再因为选择 Cua 或 Win32 而拒绝 `mouse_move`、带坐标文本或改变滚动坐标。旧 `decode(..., cua=...)` 参数仅为已有调用兼容保留，不影响结果；正式服务不再传它。模型原始回答照旧归档，统一动作只是中间表示。

能力判断移至执行器适配器：Cua 窗口模式的 `move_cursor` 在发输入前明确拒绝，因为它只能移动指针叠层，不能产生真实后台悬停；Win32 前台模式仍可移动真实鼠标。带坐标文本可在 Cua 窗口模式转换为其原生输入请求，在当前 Win32 执行路径会明确拒绝，不误报成模型格式错误。模型没有给滚动坐标时，中间表示保留“未指定目标”；Win32 适配器仅在本次授权目标内有真实已送达的指针位置时才补坐标，否则拒绝，Cua 则保持其当前焦点区域的滚动语义。模型提示词保留原生工具格式，不以删掉 `mouse_move` 来隐藏后端能力缺口。

后端的 `capabilities()` 只报告执行器本身的能力，不按模型名称改变。旧版会把模型/后端能力交集注入每轮模型输入；当前版不再注入后端技能列表、交付模式或执行器名称。只有授权窗口/应用标识作为中立任务数据供能表示目标切换的模型引用，能力拒绝由执行核心在发送前明确反馈。完成核验的 `purpose` 独立识别，不受后端种类影响。

进一步的边界整理：`local_observation` 现在只提供可信目标、截图及**后端原始能力**，不接收模型名称；`local_gui_protocol.adapt_execution_context` 在规划前统一求模型原生动作与后端能力的交集，并只向能表达 `switch_window` / `launch_app` 的模型保留对应目录。执行后端及 `ExecutionCore` 不解析模型格式，也不读取模型名称。模型原文、统一计划和驱动回执仍分别归档，可按同一轮索引核对。

回归：完整 `tests` 套件（4 项跳过）与修改文件的 Ruff 检查通过。一次性 Edit 实机复测保存在 `runs/native-loop-adapter-separated-20260923/summary.json`：GUI-Owl、MAI-UI 经 Cua 两组均成功读回精确文本；两组模型输入均不再含其原生格式无法使用的窗口/应用目录。Win32 两组在发送输入前因 Windows 拒绝聚焦自建测试窗口而停止，此次记录**不是**通过证据；前一轮 Win32 探针通过，仍需在允许前台焦点的交互桌面复测本次边界整理后的版本。探针使用固定原生工具调用文本，不是 GPU 模型能力测试。

这一版另行构建到 `dist/adapter-boundary-20260923/Trace2Task`，独立启动检查 `runs/adapter-boundary-smoke-20260923/desktop-smoke.json` 返回 `{"ready":"complete","console":true}`。它尚未替换 `D:/Apps/Trace2Task` 中正在运行的安装版；构建和启动检查不能替代上述 Win32 焦点条件下的动作验证。

回执反馈补充：Cua 若在 `effect=unverifiable` 的回执中同时报告 `escalation.reason=delivery_failed`，适配器保留完整原回执，并附加固定的 `delivery_path_warning`；真实执行历史只增加这条警告，下一轮提示模型看新截图，不把它判为明确失败或自动补发。`runs/native-loop-receipt-advisory-20260923/summary.json` 的两种原生工具格式均在自建 Edit 中读回精确文本；警告出现在第二个动作的回执和随后模型轮次的历史中，说明警告与实际效果不能混为一谈。该证据不推广为任意应用的后台输入成功率。

含本次回执修正的新独立包位于 `dist/adapter-feedback-20260923/Trace2Task`；`runs/adapter-feedback-smoke-20260923/desktop-smoke.json` 返回 `{"ready":"complete","console":true}`。检查安装前，`D:/Apps/Trace2Task` 的 `/api/state` 报告一个 `running` 执行任务，因此没有停止程序或模型服务、没有覆盖安装目录；等待任务结束后再更新。

2026-09-23 后续安装：模型服务请求中的执行上下文首选通用字段 `execution_context`。新服务在 `/health` 声明 `generic_execution_context`；客户端在旧服务上仍发送兼容字段 `cua_context`，服务端同时接受这两种单独形式、拒绝同时传入，避免旧服务默默丢弃桌面范围。已添加新旧服务协商和两种请求形态的测试。完整 Python `tests` 套件第二次运行通过（4 项跳过），修改文件 Ruff 和模型 I/O 界面测试通过；首轮完整测试有一项 Windows HTTP 连接中止，单项与完整套件重跑均通过。

新包 `dist/execution-context-20260923/Trace2Task` 独立启动检查为 `runs/execution-context-smoke-20260923/desktop-smoke.json`。原安装程序退出且旧模型服务无活动连接后，将它停下并替换 `D:/Apps/Trace2Task`；旧安装完整移至 `D:/Apps/Trace2Task-update-backups/execution-context-before-20260923`。安装版 `runs/execution-context-installed-smoke-20260923/desktop-smoke.json` 返回 `{"ready":"complete","console":true}`，安装包中的客户端与服务端源码也核对到新字段。这仍是程序启动验证，不是 Windows 桌面前台焦点、任意应用后台输入或实际 GPU 任务成功的证据。

同日模板兼容更新：新默认模板改用 `{{execution_context_block}}`，已保存的 `{{cua_context_block}}` 模板继续通过校验并填充同一执行上下文；网页说明同步标明两者关系。最终安装包为 `dist/execution-context-template-20260923/Trace2Task`，安装版冒烟结果在 `runs/execution-context-template-installed-smoke-20260923/desktop-smoke.json`；替换前版本完整保存在 `D:/Apps/Trace2Task-update-backups/execution-context-before-template-20260923`。最后一次完整 Python 回归通过（4 项跳过），修改文件 Ruff 检查通过。安装版未自动启动真实任务；模型服务在更新前停止，下一次任务由程序按需启动。

受控 Win32 实机验收：`runs/native-loop-win32-final-20260923/summary.json` 里 GUI-Owl、MAI 两种原生 `tool_call` 分别经过解码、统一核心、Win32 发送和重新观察，均用 2 次动作将固定文本送入一次性自建 Edit 控件，读回全文相同，随后只读完成核验通过。随后 `runs/native-loop-cross-backend-final-20260923/summary.json` 中 Cua 两组也通过；同次 Win32 两组被 Windows 拒绝聚焦，动作数为 0，均在输入前停止。成功和拒绝两种结果都保留，不把一次通过外推为任意焦点条件下都可用。该探针使用固定原生响应文本，不是 GPU 模型能力或真实用户任务的成功率测试。

边界审查后的剩余范围：本节的共享 `ExecutionCore` 路径目前覆盖本地 GUI 模型的 Cua、Win32 路径，D-5970 也进入共享本地循环；Codex / Model API 桌面路径仍在 `desktop_runner.py` 使用 `parse_plan → ExecutionRuntime → WindowsMotorExecutor`。它保留最多四步批次和可选 LangGraph 状态，不能将单动作重观察核心直接替换进去而不改变现有语义与速度。后续若要实现**所有**模型共享同一执行边界，应先为该路径定义等价的批次边界和状态回执，再迁移；当前不宣称全项目执行链已经统一。

随后将 Codex / Model API 桌面的**实际动作发送**接入 `DesktopExecutionAdapter → Win32ExecutionBackend`，不修改模型原生 JSON Schema、原有最多四步批次边界或 LangGraph 状态转换。每个动作在发送前由后端重新校验前台句柄和屏幕尺寸，发送后用与 `ExecutionCore` 相同的回执验证函数判定 `confirmed` / `unverifiable` / 错误；旧的 `ExecutionRuntime` 仍负责该路径的批次、限额和规划等待。回执归档到动作日志，下一轮只附最近一次小型传输回执，并明确它不是任务结果。错误目标回执被拒绝且不重试。完整 Python 回归通过（4 项跳过），修改文件 Ruff 通过；测试验证了同一计划的两个动作仍被顺序发送、回执可见以及错误目标只尝试一次。新独立包 `dist/desktop-adapter-20260923/Trace2Task` 和安装版均通过 `{"ready":"complete","console":true}` 启动检查；旧安装完整备份在 `D:/Apps/Trace2Task-update-backups/before-desktop-adapter-20260923`。安装后恢复了可见程序，模型服务未预加载。该路径尚未用真实 Codex/API 模型任务做端到端效果验收，批次编排也仍不同于本地模型的单动作共享循环。

单窗口 Codex/API 路径再接入 `WindowExecutionAdapter → WindowExecutionBackend → WindowsMotorExecutor`：模型的阶段计划、前后台选择、视觉检查点和批次恢复逻辑都没有移到后端；每个实际动作增加绑定窗口身份与客户区尺寸检查、独立能力预检和统一回执验证。`tests/test_window_execution.py` 覆盖背景路线、窗口变化输入前拒绝、错误目标回执不重试及背景模式禁用聚焦；完整 Python 回归通过（4 项跳过）。受控实机探针 `runs/window-execution-native-poll-20260923/summary.json` 曾在自建 Edit 上前台读回完整固定文本，后台虽返回两次 `effect=unverifiable` 回执却读回空文本；`runs/window-execution-native-final-20260923/summary.json` 再现后台无效果，前台被 Windows 拒绝聚焦且动作数为 0。两种结果均保留，说明驱动路线存在不等于应用接受输入，后台需要真实效果核验；不应盲目重发。

单窗口适配版随后构建到 `dist/window-adapter-20260923/Trace2Task`，独立包与安装包分别在 `runs/window-adapter-smoke-20260923/desktop-smoke.json`、`runs/window-adapter-installed-smoke-20260923/desktop-smoke.json` 返回 `{"ready":"complete","console":true}`。安装文件 `D:/Apps/Trace2Task/Trace2Task.exe` 的 SHA256 为 `9E7FD0475935B90FAD5BACBDCB309FCD3F88A06479D30017CA47580302D3E3A5`；旧安装完整保存在 `D:/Apps/Trace2Task-update-backups/before-window-adapter-20260923`。可见程序已重新从原数据目录启动，页面 HTTP 返回 200。打包和启动不证明后台输入在任意应用生效，也不证明真实模型任务完成；单窗口后台仍需要观察目标应用的实际效果。

后续解耦：GUI-Owl、MAI-UI、Qwen 的默认系统提示词不随 Cua/Win32 选择变化；其中 GUI-Owl 默认采用官方桌面原文，Qwen 基座没有官方 GUI 动作协议，MAI 官方导航面向移动端，后两者仍需项目定义的 Windows 动作格式。既有用户自定义提示词按原样保留，不自动改写。单窗口 Codex/API 的最近动作历史不再把驱动回执称为 `applied`：成功送达记录为 `delivered_effect_unverified`，异常记录为 `interrupted_or_unknown`，因为后者可能已部分发送。本地 `ExecutionCore`、桌面批次适配器和单窗口批次适配器的实际一次派发共用 `dispatch_backend_once`，在派发后统一校验回执，且不自动重发不确定输入；各自仍保留原有目标预检与编排策略。

当前本地 Agent 循环已按模型计划长度顺序执行；每一步单独记录回执与真实历史，普通视觉变化不丢弃后续动作。驱动拒绝、目标身份/几何失效、动作限额、停止请求或不确定输入会阻断剩余动作。Codex/API 的阶段批次仍由原有 `ExecutionRuntime` 编排，尚未合并成同一个上层 Agent 类；统一的是协议与实际派发边界，不应宣称所有路径的编排已完全相同。

本次验证：完整 Python `tests` 套件通过（4 项跳过），修改文件 Ruff 通过。新增回归覆盖 Qwen 原生多动作 JSON 在 Cua/Win32 两种后端的相同执行协议、每步回执、普通视觉变化不截断、未知效果不重试、授权目标变化后的批次边界。独立包与安装版的启动检查都返回 `{"ready":"complete","console":true}`；更新前安装完整保存在 `D:/Apps/Trace2Task-update-backups/before-agent-batch-decoupled-20260923`。安装版已按原数据目录重新启动，HTTP 返回 200、17 个任务包仍可见。没有执行真实 GPU 模型任务，因此这些检查不证明模型会可靠地产生多步计划或目标应用必然响应输入。

完整 Python 回归通过（4 项跳过），相关 Ruff 检查通过。新包 `dist/model-executor-decouple-20260923/Trace2Task` 和安装版分别在 `runs/model-executor-decouple-smoke-20260923/desktop-smoke.json`、`runs/model-executor-decouple-installed-smoke-20260923/desktop-smoke.json` 返回 `{"ready":"complete","console":true}`。安装文件 `D:/Apps/Trace2Task/Trace2Task.exe` 的 SHA256 为 `B9BE0A29236F968D16EB0D5C75700ECC63B9DE2EDEFCBC947DEF2B106841980E`；旧版完整备份在 `D:/Apps/Trace2Task-update-backups/before-model-executor-decouple-20260923`。原数据目录的可见程序已重启，页面返回 HTTP 200。没有启动真实模型任务，以上证据不代表任意应用的后台输入或任务效果已通过验收。

安装版 `/api/local-prompts` 读回：GUI-Owl 的 Cua/Win32 **默认**系统提示词完全一致，且不含旧的后端范围硬编码；用户已有 Win32 自定义提示词仍标为 `customized=true`，因此其**生效**文本与默认文本不同。这是保留用户配置的预期行为，并非默认模板变更未生效。

核心、驱动、适配器回归测试：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_execution_core.py tests/test_cua_scope.py tests/test_cua_backend.py tests/test_cua_window_identity.py tests/test_local_gui.py tests/test_local_gui_service.py
```

测试使用真实模型输出解析器和模拟驱动，不能替代真实应用的后台兼容性验收。

### 2026-09-22 验证结果

- 180 项相关回归测试通过；修改文件 Ruff 检查通过。
- 打包程序启动检查：`{"ready":"complete","console":true}`。
- 在历史计算器截图上进行了两种模型的真实本机 GPU 推理，不发送键鼠：GUI-Owl 生成点击，预检通过，生成约 2.79 秒；MAI-UI 生成文本输入，预检拒绝，生成约 1.28 秒。两个模型都正确解析进入统一协议，但 MAI-UI 仍可能忽略可用能力说明，不宣称模型已可靠。
- 真实输入、输出、预检结果和耗时：`D:/MyProject/trace2task/runs/unified-execution-validation-20260922/`。这里是离线截图预测，不是完整任务成功记录。
- 新程序：`dist/unified-execution-core/Trace2Task/Trace2Task.exe`。用户退出旧程序后，已更新 `D:/Apps/Trace2Task` 的 7 个变更文件并逐个核对 SHA256；安装版启动检查通过，使用原数据目录重新启动。旧文件备份：`D:/Apps/Trace2Task-update-backups/unified-core-20260922-181505`。

## 参考与取舍

- [GUI-Owl PC 操作实现](https://github.com/X-PLUG/MobileAgent/blob/main/Mobile-Agent-v3.5/computer_use/utils.py)：原始拖动使用当前鼠标位置；本地后台适配要求显式起点，属于协议扩展，不冒充原版完全兼容。
- [MAI-UI 原生动作说明](https://github.com/Tongyi-MAI/MAI-UI/blob/main/MAI-UI/src/prompt.py)：沿用显式起终点拖动，移动端 swipe 不等同 Windows 滚动。
- [Cua Windows 动作接口](https://github.com/trycua/cua/blob/main/docs/content/docs/reference/cua-driver/mcp-tools-windows.mdx)：复用 background scroll/drag；不启用坐标滚动的前台路径。

- [UI-TARS SDK 的 Model / Operator 分层与 ACTION_SPACES](https://github.com/bytedance/UI-TARS-desktop/blob/main/packages/ui-tars/sdk/README.md)：借鉴接口职责与能力告知，没有引入其 Node 执行栈或复制实现。
- [Cua Driver 接口契约](https://github.com/trycua/cua/blob/main/docs/content/docs/reference/cua-driver/contracts.mdx)：遵守精确窗口目标、交付与效果分离、拒绝不代表成功。线上主分支契约可能新于本机 v0.28.2，因此保留本地已验证的 flat pid/window_id + session 参数，不盲目升级 wire 格式。

这不是重新实现底层驱动；窗口操作继续复用 Cua。新增的是项目自己的授权、动作约束和执行生命周期边界。

## 程序入口与审计展示（2026-09-23）

桌面程序使用同一执行页面；执行后端选择现在对所有模型可见。D-5970、Qwen3-VL-2B、GUI-Owl-2B 和 MAI-UI-2B 可以选择 Win32 或实验性 Cua。Codex、外部模型 API 与本地 Qwen3-VL-8B 的**无经验桌面执行**现也走 `ChatModelAdapter → ActionPlan → ExecutionCore → Win32/Cua`，其中 Cua 仅操作用户明确选定的窗口或应用。两类聊天模型共用一种 JSON 计划适配器；模型会话保留各自原生 HTTP/App Server 请求，不按 Codex 原型改造本地 GUI 模型输出。

带经验桌面任务、单窗口任务包仍走原有阶段规划、视觉检查点、循环任务判定和批次恢复；LangGraph 桌面恢复也仍走原有路径。这些语义尚未无损迁入共用循环，因此此三类入口仍只提供原来的 Win32 后端，不能仅打开 Cua 下拉选项冒充已支持。共用循环的完成声明需要新截图与同一模型只读核验，标记 `verified=false`；驱动回执不等于应用效果。Codex/API 共用循环的完整请求与响应存于 `io-audit/events.jsonl`，程序轮次视图实时展示提示词、模型输出、回执和耗时；未经真实应用端到端任务验证，不宣称所有应用的后台交付可靠。

本地 GUI 模型仍实时写入轮次日志。未迁移的 Codex/API 路径继续在运行结束后从原有 `io-audit/events.jsonl` 投影同形状轮次摘要；无经验桌面共用循环则在每轮请求、响应和执行回执出现时直接发布。图片 Base64 不复制到页面状态，原始审计仍在本地归档。程序不再提供“只生成计划”按钮；原有预测 API 暂保留给脚本和测试。

本次交付验证：完整 Python 测试通过（4 项按条件跳过），前端相关测试 5/5、Ruff 通过。Codex/API 的假驱动集成测试覆盖 Win32 与 Cua 共用循环、两步计划顺序派发、无效回答不发送输入、完成声明后的只读核验，以及真实 Model API 请求中的系统消息、图片和 JSON Schema。独立安装包启动检查返回 `{"ready":"complete","console":true}`。已更新本机 `D:/Apps/Trace2Task`，可执行文件 SHA256 为 `993D9665B8D83743CD3C26789BF1F3E1E0B23450F76DDFEF84762414D19B51B4`；更新前安装备份在 `D:/Apps/Trace2Task-update-backups/before-chat-shared-loop-final-20260923`。安装版进程与本地接口已重新启动，但本轮没有向真实应用发送 Codex/API 键鼠任务，不能把集成测试当作真实任务成功率。
