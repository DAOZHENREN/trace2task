# Trace2Task

<p align="right">
  <a href="README.md">English</a> | <strong>简体中文</strong>
</p>

**用一次人类示范教会 Windows Agent 完成任务，再通过可审查的反馈让经验持续进化。**

Trace2Task 记录人类完成任务时的操作、画面与可选语音讲解，并把这些证据编译成带版本的任务经验。运行时 Agent 不会机械复刻旧坐标，而是观察当前画面、检索与当前状态相关的已审查经验、规划一组有界操作，通过本地受控执行层完成动作，并在画面变化或异常时重新规划。

当前版本为 **v0.18.1**。这是一个 Windows 优先、带本地网页控制台的研究原型。

## 版本演进

| 版本 | 主要变化 |
|---|---|
| v0.14.6 | 建立自适应经验控制台：可审查的语义经验、可迭代的人工 Guidance 与可视化任务详情。 |
| v0.15.x | 加入独立 Effect Verifier 和可重复的 reset-run-evaluate 协议，使经验效果可以量化，而不只依赖观察。 |
| v0.16.x | 接入 Windows Agent Arena，并支持 baseline / trace / compiled / feedback 的受控消融实验与聚合指标。 |
| v0.17.0-v0.17.3 | 加入同步 WAA 操作录制、可选人工语音讲解、任务目录驱动的验证式 reset、录制取消与残留清理。 |
| v0.17.4 | 加入轻量 Compiler 连接预检、成对编译快速失败，以及从保留录制重试而无需重新示范。 |
| v0.17.5 | 将固定 Compiler 截止时间改为进度感知流式等待：90 秒无进展超时、600 秒硬上限，并细分可重试错误。 |
| v0.18.0 | 加入论文级 WAA 研究协议：冻结制品哈希、隐藏变体验证、确定性交错调度、错误 Trace 对照、反馈学习曲线与人工成本记录。 |
| v0.18.1 | 加入首个防泄漏参数化 WAA 任务族：一个可录制 D0 示范、三个隐藏测试变体、自包含 setup/evaluator，以及审查前不可变 Compiler 快照。 |

这些版本始终遵循同一原则：不可变的人类 Trace 是第一手证据；Compiler 输出是可审查的派生知识；反馈按版本累积；运行时 Agent 必须经过独立的重置与效果验证边界。

把模型推理移出运行时关键路径的研究方案见 [Trace-Guided Low-Latency Desktop Agent](docs/research/trace-guided-low-latency-agent.md)。文档区分了已实现能力与研究假设，并为受控动作程序、紧凑运行时上下文和分层模型路由定义了可量化的后续实验。

## 为什么需要 Trace2Task？

固定宏很快，但很脆弱：按钮移动、弹窗、加载延迟或参数变化都可能让整条脚本失效。通用桌面 Agent 更灵活，却往往不知道人类已经掌握的任务诀窍。

Trace2Task 保留了两者之间最有价值的一层：

- 原始 Trace 是人类真实操作的不可变证据；
- Compiler Agent 把证据转成可审查的任务语义；
- 运行时 Agent 根据当前画面和本次指令灵活使用这些语义；
- 人工反馈以带版本规则持续累积，而不是静默覆盖旧知识。

## 当前流程

<p align="center">
  <img src="docs/research/assets/trace-guided-runtime-architecture-v2.png" alt="Trace2Task 架构：人类证据被编译为受控运行时知识，经独立验证后进入带版本的学习闭环" width="100%">
</p>

<details>
<summary><strong>查看详细控制流程</strong></summary>

~~~mermaid
flowchart LR
    A[人工示范<br/>操作 + 截图 + 可选讲解]
    B[不可变 Trace 证据]
    C[确定性动作编译器]
    D[多模态 Compiler Agent]
    E[已审查任务包<br/>片段 + 有向状态图 + 终态]
    F[本次指令 + 当前截图]
    G[当前相关 Guidance]
    H[多模态 Agent<br/>多动作计划]
    I[本地受控执行]
    J[独立 Effect Verifier]
    K[人工反馈]
    L[Guidance 或任务模型修订草稿]
    M[验证回执]
    N[可重复评测套件]

    A --> B
    B --> C
    B --> D
    C --> E
    D --> E
    E --> F
    G --> H
    F --> H
    H --> I
    I -->|视觉检查点或异常| H
    I -->|Agent 声称完成| J
    J --> M
    M --> K
    N -->|重置 + 重复| F
    M --> N
    K --> L
    L -->|人工审查并确认| G
    L -->|结构修正| E
~~~

</details>

## 当前版本可以做什么

### 录制人类示范

- 发现并选择可见的 Windows 应用窗口。
- 在独立输入线程中记录键盘与鼠标的 down/up 边沿。
- 在输入事件后截取目标窗口，并保存物理像素与 DPI 安全坐标。
- 按 F8 标记示范成功，按 F9 取消。
- 在示范操作旁同步录制可选的人类讲解。
- 使用本地 Whisper Turbo 转写，并允许用户在编译前修正文稿。

### 把 Trace 证据编译为经验

- 将原始输入区间转为经过校验的动作证据，包括点击、按键、快捷键、文本输入、长按、拖动和有界等待。
- 使用可单独选择的教师模型推断规范任务说明、Trace 支撑的任务片段、动作意图、可见前置条件、预期效果、不确定性和完成条件。
- 构建包含分支、循环、回退恢复边和独立终态的有向状态图；运行时不必线性照搬示范顺序。
- 将证据图精确绑定到 Compiler 选中的 Trace 动作区间。
- 不把录制坐标写入运行时语义 Guidance，避免新任务复制过期位置。

### 使用多模态 Agent 执行

- 只输入一句自然语言指令，任务参数从句子中解析。
- 自动选择兼容且已确认的本地经验，也可以手动指定。
- 运行 Agent 的模型和思考强度可与教师模型独立选择。
- 当后续画面可预测时，要求模型返回有序的多动作计划。
- 本地校验并执行动作，使用自适应视觉等待；预期画面没有出现时立即中断剩余批次。
- 遇到可恢复异常时，基于最新截图在同一任务会话中重新规划。
- 有运行 Trace 时，成功、失败与人工停止的运行都可以保留为反馈证据。
- 支持前台执行，并为兼容应用提供尽力而为的后台模式。

### 通过多轮运行改进经验

Trace2Task 将修正分成两类：

1. **Guidance 修订**：改善任务诀窍，但不改变任务图。Revision Agent 针对稳定规则 ID 提议 keep、add、update、deprecate 或 conflict 操作。已确认规则跨轮累积；未解决冲突不能激活。
2. **Task Model Revision**：当 Compiler 对任务结构的理解有误时，修正状态、转换、分支、恢复路径或终止条件。不可变原始 Trace 不受影响，每个已确认图版本都会保留。

Guidance 可以作用于全局，也可以只绑定某个状态、转换或终态。第一次运行规划会收到合并摘要与当前生效规则；后续只检索全局规则以及当前状态图邻域相关规则。旧修订、原始反馈和融合说明保留为审计历史，不会每轮重复塞给模型。

### 独立验证效果并进行可重复评测

运行 Agent 可以判断任务可能已经完成，但不再独占最终结论。独立 Effect Verifier 为每次执行写入 verification.json，并明确标记：

- verified：配置的独立验证器接受了任务效果；
- completed_unverified：Agent 从画面判断完成，但没有配置独立验证器；
- reconciliation_required：Agent 判断与独立证据冲突，停止执行并等待核对；
- failed_execution 或 canceled：执行未到达可接受完成状态。

旧任务包继续兼容 reviewed_reference_frame，但会如实标记为 completed_unverified。任务也可选择带审查阈值的确定性 pixel_reference 验证。验证器注册表刻意保持简单，后续 UIA、API、文件、数据库或 OSWorld 验证器可以实现同一接口，而无需替换运行时 Agent。

评测套件定义任务、reset 适配器、指令和重复次数。每次尝试保留 Agent Trace 与验证回执；最终输出 attempts.jsonl 和 summary.json，包含验证率、完成率、结果分布、延迟、模型轮次与动作数。

### 控制台中的语音输入

每个自然语言字段都可以使用统一的“语音输入”控件，包括运行指令、经验名称、讲解修正、合并经验摘要、运行反馈和任务图反馈。所有入口复用同一个本地 Whisper Turbo 缓存。

普通听写音频只写入临时文件，转写后删除。人类示范讲解使用独立、明确的 Trace 归档流程。

## 环境要求

- Windows 10 或 Windows 11。
- Python 3.11 或更高版本。
- [uv](https://docs.astral.sh/uv/)。
- 本地安装并登录 Codex；模型编译和执行可复用 ChatGPT 订阅登录，不要求 OpenAI API Key。
- 只有使用讲解或语音输入时才需要麦克风。

Whisper Turbo 首次使用时下载到 .cache/faster-whisper/，之后复用缓存，模型约 1.6 GB。兼容 CUDA 12/cuDNN 9 时优先使用 CUDA FP16，否则回退到 CPU INT8。

## 快速开始

~~~powershell
git clone https://github.com/DAOZHENREN/trace2task.git
cd trace2task
uv sync --extra dev
codex login status
uv run trace2task web
~~~

控制台地址为 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)，且只监听本机回环地址。

如果 Codex 尚未登录：

~~~powershell
codex login
~~~

当终端 PATH 中找不到 Codex 时，Trace2Task 也会发现 ChatGPT/Codex Windows 桌面应用内置的版本化 codex.exe。

## 推荐的网页控制台流程

### 1. 录制经验

1. 打开“录制经验”。
2. 选择本地目标窗口或“Windows Agent Arena VM”，输入不重复的经验名称。
3. 选择 Compiler Agent 模型与思考强度。
4. 如果口头意图或诀窍有助于理解，保留“人工讲解录制”。
5. 开始录制，像平时一样完成任务，在成功状态按 F8。
6. 审查或修正 Turbo 转写，然后开始编译。

WAA 讲解录制会等待 VM 录制器返回 READY，再启动浏览器麦克风，最后发送 GO，使语音和 Trace 共用同一时间轴。F8 会在 WAA evaluator 确认成功后停止两端。完成转写审查后，同一份不可变 Trace 会生成“纯Trace”和“人工讲解”两个任务包。

录制与语义编译是两个独立结果。即使编译失败，原始 Trace 仍会保留，可从“本地经验”再次编译，无需重新录制。

### 2. 人工审查并确认

进入生成任务的详情页，检查：

- 目标进程和窗口；
- 允许的动作能力；
- 规范任务说明与完成条件；
- Trace 支撑的片段及其前后证据图；
- 有向状态、合法转换、恢复边和终态；
- 语音讲解中的说法是否得到视觉或动作证据支持。

生成的任务包默认为草稿，只有明确确认后才能成为正式经验。

### 3. 规划并执行

1. 打开“执行任务”。
2. 自动选择经验，或手动选择一个已确认经验。
3. 输入一句自然语言指令，例如：

   ~~~text
   给文件传输助手发送：Trace2Task 当前版本测试完成
   ~~~

4. 选择运行 Agent 模型、思考强度与输入模式。
5. 使用“只生成计划”检查 Agent 的理解，不注入任何输入。
6. 使用“开始执行”并确认目标。运行期间随时可按 F9 紧急停止。

### 4. 把运行结果反馈给经验

所有保存了 Trace 的运行都会出现在“可反馈运行”。输入具体的行为反馈，生成 Guidance 融合草稿，检查规则级差异，确认无误后再让它生效。

如果问题属于任务结构，例如缺少回退边，或把成功界面错误建模为普通编号阶段，请使用独立的任务图反馈，并审查 Task Model Revision 草稿。

## 运行 Agent 如何使用任务包

运行模型不会在每一轮收到完整原始录制。

第一次规划会收到一份紧凑任务契约：

- 本次用户指令；
- 当前截图；
- 规范任务说明和完成策略；
- 有向状态图与当前候选状态邻域；
- 选中的 Trace 证据图和去坐标化动作类型；
- 合并后的 Guidance 摘要与当前适用规则；
- 系统级多动作规划和安全契约。

每批动作后，本地视觉检查点决定继续执行、继续等待，还是返回模型。后续轮次只使用最新截图、已观察结果、紧凑会话历史，以及与当前状态、可用出边和候选终态相关的 Guidance。

这种拆分既让人类 Trace 保持权威，又避免把它退化成字面坐标回放脚本。

## 任务包文件

一个当前版本的 Windows 任务包可能包含：

~~~text
<task-pack>/
├── task.yaml                    # 目标、动作、限制、审查状态
├── demonstration.json          # 带来源的确定性动作证据
├── compiler-report.json        # Compiler 决策与来源审计
├── experience.yaml             # 当前语义片段与有向状态图
├── experience-revisions/       # 已确认任务模型历史
├── guidance.yaml               # 当前合并人工 Guidance
├── guidance-revisions/         # 已确认 Guidance 历史
└── reference/
    ├── metadata.json
    ├── trace.jsonl              # 不可变原始人类 Trace
    ├── narration.json           # 可选已审查转写与时间对齐
    └── frames/*.png             # 保留的 Trace 视觉证据
~~~

生成任务包、运行 Trace、模型缓存和已删除项目的回收站属于本地数据；位于默认生成目录时会被 Git 忽略。

## 高级命令行

推荐优先使用网页控制台。底层命令适合诊断与自动化。

~~~powershell
# 列出或截图 Windows 目标
uv run trace2task windows list
uv run trace2task windows list --process "Weixin.exe"
uv run trace2task windows capture --process "Weixin.exe" --focus --output runs\capture.png

# 录制、编译并确认
uv run trace2task windows record --process "Weixin.exe" --task-id "wechat-send-message"
uv run trace2task compile runs\<recording>\trace.jsonl
uv run trace2task confirm taskpacks\generated\<task-pack>\task.yaml

# 只生成计划
uv run trace2task windows agent --task taskpacks\generated\<task-pack>\task.yaml

# 执行已确认任务包
uv run trace2task windows agent --task taskpacks\generated\<task-pack>\task.yaml --model gpt-5.6-terra --reasoning-effort low --execute
~~~

可重复评测套件可以声明任务、指令、重复次数与 reset 命令：

~~~yaml
# evaluations/fgo-smoke.yaml
schema_version: "0.1"
id: fgo-smoke
cases:
  - id: one-battle
    task: ../taskpacks/generated/<task-pack>/task.yaml
    instruction: 完成一次当前副本
    repetitions: 5
    reset:
      type: command
      argv: ["powershell", "-NoProfile", "-File", "reset-fgo.ps1"]
      timeout_seconds: 60
~~~

~~~powershell
uv run trace2task eval run --suite evaluations\fgo-smoke.yaml --model gpt-5.6-terra --reasoning-effort low --execute
~~~

只有在应用自身可以可靠回到初始状态，或 dry-run 不会产生修改时，才使用 reset: {type: none}。command reset 会执行声明的本地程序，因此只应运行已经审查并信任的评测套件。

## Windows Agent Arena 受控消融实验（实验性）

WAA 适配器把 benchmark 和 Trace2Task 的职责分开：

- Windows Agent Arena 负责重置 Windows VM、提供任务和截图、执行 pyautogui 动作，并拥有独立 evaluator。
- Trace2Task 在 Windows 主机上复用现有 Codex 订阅登录，并只注入一种受控证据条件：baseline、trace、compiled、narrated_compiled 或 feedback。
- 所有条件使用同一套通用动作策略。Trace 派生的允许技能列表不会泄露给 baseline。

建议将 WAA 放在独立目录，因为它包含大型 VM 镜像和生成结果。使用 D:\MyProject\WindowsAgentArena 可避免占用系统盘。先从 [Microsoft Evaluation Center](https://www.microsoft.com/en-us/evalcenter/download-windows-11-enterprise) 下载 Windows 11 Enterprise Evaluation x64 ISO，将其重命名为 setup.iso，放到 src\win-arena-container\vm\image，再安装 Trace2Task overlay：

~~~powershell
uv run python integrations\windows_agent_arena\install_overlay.py D:\MyProject\WindowsAgentArena
~~~

首次安装需要构建开发容器并创建 Windows golden image。这一步只执行一次，但需要较多磁盘和网络资源：

~~~powershell
wsl -d Trace2Task-WAA -- bash -lc "cd /mnt/d/MyProject/WindowsAgentArena/scripts && export TRACE2TASK_DOCKER_BUILD_ARGS='--network=host' && ./run.sh --mode dev --prepare-image true --start-client false --openai-api-key trace2task-bridge-unused"
~~~

如果 WSL 构建需要使用 Windows 主机代理，可在 TRACE2TASK_DOCKER_BUILD_ARGS 中加入 HTTP_PROXY 和 HTTPS_PROXY。若代理无法处理 Debian 软件源，则不要传代理，改用可访问的 TRACE2TASK_DEBIAN_MIRROR 与 TRACE2TASK_DEBIAN_SECURITY_MIRROR。

等待准备容器正常结束。后续启动已准备好的 WAA VM：

~~~powershell
wsl -d Trace2Task-WAA -- bash -lc "cd /mnt/d/MyProject/WindowsAgentArena/scripts && ./run.sh --mode dev --skip-build true --start-client false --openai-api-key trace2task-bridge-unused"
~~~

打开 http://localhost:8006。推荐从 Trace2Task 网页控制台录制：

1. 进入“录制经验”，选择“Windows Agent Arena VM”。
2. 使用默认 WAA 根目录，或选择另一份 checkout；从 client 目录选择 WAA example JSON。
3. 确保任务 ID 已被 integrations/windows_agent_arena/reset_specs 中某个 JSON 覆盖。缺少确定性 reset 契约时会禁止录制。
4. 开启讲解并开始录制。READY 之前，系统会关闭任务应用、应用 reset spec、验证全部不变量并写入 reset-receipt.json；之后完成 READY → microphone → GO 握手。
5. 在 VM 内操作并按 F8。编译前先审查 Turbo 转写。

narration.json 保存 audio_start_trace_elapsed_ms，使每个语音片段在 Compiler 对齐前映射到 Trace 时间轴。系统使用最多四秒的前向窗口，处理“先解释、后操作”的常见讲解方式。

F8 会先运行独立 WAA evaluator：验证失败时，同一 Trace 会保持录制状态，让人类修正任务后再次按 F8。原始输入边沿和对应 VM 截图保存在 client\trace2task_recordings，只有 WAA 分数为 1.0 才标记成功。

同一不可变 Trace 可以生成两个任务包。纯 Trace 编译显式忽略讲解，人工讲解编译使用已审查转写：

~~~powershell
uv run trace2task windows compile-experience --task taskpacks\generated\<plain-task-pack>\task.yaml --ignore-narration --model gpt-5.6-sol --reasoning-effort high
uv run trace2task windows compile-experience --task taskpacks\generated\<narrated-task-pack>\task.yaml --model gpt-5.6-sol --reasoning-effort high
~~~

narrated_compiled 只接受真实人类讲解；录制器合成的 waa_task_instruction 会单独标记，不能冒充人工讲解。

推荐的一条命令实验会让同一任务在每个条件下重复三次：

~~~powershell
uv run trace2task waa experiment --waa-root D:\MyProject\WindowsAgentArena --task taskpacks\generated\<plain-task-pack>\task.yaml --narrated-task taskpacks\generated\<narrated-task-pack>\task.yaml --feedback-task taskpacks\generated\<narrated-task-pack>\task.yaml --reset-spec integrations\windows_agent_arena\reset_specs\notepad.json --conditions baseline trace compiled narrated_compiled feedback --repetitions 3 --model gpt-5.6-terra --reasoning-effort low
~~~

narrated_compiled 与 feedback 可以指向同一个讲解任务包。前者忽略 guidance.yaml，只使用讲解编译经验；后者在相同经验上额外注入已审查 Guidance，因此第五个条件可以单独测量人工反馈的增益。

WAA VM 和 winarena 容器需要先运行，但不再需要第二个桥接终端。runner 固定模型、思考强度、任务列表、动作策略和计划长度。每次重复前会关闭 benchmark 应用，将声明的输出文件移入 Windows 回收站，重置 WAA，并验证 reset 不变量。reset 失败会在任何模型动作之前终止该轮。

WAA 将 result.txt、轨迹截图、动作和时间戳写入：

~~~text
client\results\trace2task-experiments\<experiment-id>
~~~

Trace2Task 将聚合 JSON 与 Markdown 报告写入：

~~~text
evaluations\windows-agent-arena\<experiment-id>
~~~

因此 baseline、Trace 和 compiled 的比较使用 WAA evaluator，而不是模型自报结果。

已有结果可以重新聚合：

~~~powershell
uv run trace2task waa report --results-root D:\MyProject\WindowsAgentArena\src\win-arena-container\client\results --output evaluations\windows-agent-arena
~~~

报告包含每种条件的 evaluator 成功率、相对 baseline 的成功率增量、执行动作数、模型规划次数、模型往返时间和总任务时间。

## Stage 1 论文实验协议

waa experiment 适合单任务的多条件小实验。论文实验还需要在观察结果前冻结研究问题。Stage 1 协议位于 integrations\windows_agent_arena\studies\stage1.yaml，当前定义：

- 跨 Notepad、File Explorer、LibreOffice Writer、LibreOffice Calc 和 Paint 的 22 个任务槽位；
- 每个任务上的 baseline、Raw Trace、Trace Compile 和 Narrated Trace Compile 匹配实验臂；
- 五个任务子集上的四轮累积反馈；
- 同一子集上的错误 Trace 负对照；
- 三次重复、确定性随机交错，以及 evaluator、延迟、规划、动作、恢复和人工成本指标。

不运行 Agent，只生成并审计实验计划：

~~~powershell
uv run trace2task waa study-plan --spec integrations\windows_agent_arena\studies\stage1.yaml --waa-root D:\MyProject\WindowsAgentArena
~~~

命令会在 evaluations\windows-agent-arena\studies\trace2task-stage1 下生成冻结的 study-manifest.json、episode-schedule.csv、human-costs.csv、就绪报告和 run-ready-episodes.ps1。任务包、reset 声明、WAA task JSON、example JSON 与研究规范都会记录内容哈希，同时保存源码 Git commit、dirty 状态和 tracked diff 哈希。

首个参数化任务族为 count-token-occurrences：只公开一个可录制 D0 示范，并提供三个隐藏 E1-E3 评测变体。隐藏变体使用不同文件名、搜索词、文档内容、输出名和正确计数；它们不会出现在网页录制器中，服务端也会拒绝直接录制。

第二个参数化任务族为 find-file-by-content：D0 要求示范者检查若干不透明文本文件，找出包含指定标记的文件，将其复制到另一个 Documents 子目录并重命名，同时保留源文件。E1-E3 改变目录、标记、源位置和输出名。

Compiler 快照用于来源审计；单纯“确认”属于质量门，而不是一种独立实验方法。当前研究统一报告 Trace Compile。只有人工确实修改了语义图或运行 Guidance 时，才应建立单独的人类编辑条件。

当前 Stage 1 文件是研究计划，而不是伪造的完整数据集。count-token 任务族已完成首轮正式实验；find-file 的三个评测行仍为 planned，直到 D0 录制、已确认 Compiler 制品与人工成本齐备。CI 或正式实验前使用 --strict，可让任何剩余缺口直接失败。

当 manifest 变为 READY 后，应检查哈希和调度，在干净 commit 上按顺序运行生成的 PowerShell 脚本。不要手动把相同条件重新分组；交错顺序是协议的一部分，用来降低时间漂移与模型漂移偏差。

## 安全模型与限制

- 模型输出受任务专用 JSON Schema 约束，执行前还会再次校验。
- 只有已审查任务包声明的动作可以运行。
- 前台执行在每个输入动作前验证目标窗口。
- F9 是紧急停止键；中断时 cleanup 会释放所有按住的按键和鼠标按钮。
- 后台模式要求窗口可见且未最小化，并依赖具体应用。Raw Input、DirectInput、提权进程、纯 GPU 渲染或主动拒绝模拟输入的应用可能无法使用。
- Trace2Task 不尝试绕过反作弊，也不绕过主动拒绝合成输入的软件。
- 模型规划截图会通过已登录 Codex 服务处理，不要在不希望模型处理的敏感内容上运行。
- reviewed_reference_frame 属于模型辅助判断，会明确报告为未独立验证；pixel_reference 独立于模型，但只能证明像素相似，不能证明后端业务事务。高影响任务需要应用专用 Effect Verifier。
- 新任务包需要人工监督，直到其状态图、Guidance 和 verifier 在多个 reset 变体上得到审查。

## 旧版小游戏演示

最初的 WASD 每日奖励小游戏只作为 record/replay/replan 回归测试，不再是 Trace2Task 的主要用例。

~~~powershell
uv run trace2task demo --show
~~~

## 开发

~~~powershell
uv run pytest
uv run ruff check .
node --check src\trace2task\web\app.js
~~~

测试覆盖确定性编译器、语义经验加载、有向任务图、Guidance 融合与迁移、Windows 动作校验、运行时恢复、效果回执、可重复评测聚合、网页 API 和语音转写集成。

## 项目状态

Trace2Task 仍是持续演进的研究原型。v0.18.1 加入首个 D0 到隐藏变体的 WAA 任务族，并在人工审查前冻结自动 Compiler 输出；v0.18.0 加入可审计的 WAA 研究设计层，用于测量匹配 Trace 与迭代经验是否提高成功率或效率。在计划中的隐藏任务完成录制且 readiness gate 通过之前，项目不宣称已经获得完整论文证据。

验证契约受到 [OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt) 启发；reset-run-evaluate 分离受到 [Windows Agent Arena](https://github.com/microsoft/WindowsAgentArena) 与 [OSWorld](https://github.com/xlang-ai/OSWorld) 启发。Trace2Task 围绕自己的 Trace-to-experience 运行时重新实现这些边界，而不是嵌入它们的控制循环。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
