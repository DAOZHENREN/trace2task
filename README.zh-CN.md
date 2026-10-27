<p align="center">
  <img src="docs/assets/readme-hero.svg" alt="Trace2Task — 留下示范，看清每一步。" width="100%">
</p>

<p align="center">
  <a href="README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#从示范到经验">使用流程</a> ·
  <a href="#选择运行方式">模型与后端</a> ·
  <a href="#文档导航">文档导航</a>
</p>

**把人类示范变成可审查的 GUI Agent 经验，一个以 Windows 为主的本地工作台。**

录下操作，决定模型能看到什么，再检查它实际做了什么。执行时以**本次指令和当前画面**为准；录制坐标只是历史参考，不是待回放的脚本。

> [!NOTE]
> **研究预览，不是可放心无人值守的自动化。** 本文对应当前源码，旧安装包可能不同。动作送达或模型说“完成”，不等于任务已经独立验证成功。

## 工作台一览

<p align="center">
  <img src="docs/assets/workbench.png" alt="Trace2Task 工作台：任务指令、模型选择、参考经验与明确的操作范围" width="100%">
</p>

<sub>真实界面的隔离只读预览，任务文字为测试样例。截图过程没有调用模型或执行桌面操作；当前界面语言为中文。</sub>

- **看清模型输入。** 完整查看保存的经验文本，以及实际会发送的历史截图。
- **当前版本清晰可见。** 经验库与自动选择使用当前版本，旧版本保留在历史中供查看和回滚，不自动删除。
- **每个选择明确可见。** 模型来源、推理引擎、执行后端和授权目标在启动前分别确认。
- **每一步可以复盘。** 增量查看模型请求与回答、实际派发动作、执行回执和停止原因。

React / TypeScript / Fluent UI 工作台可运行在 WebView2 桌面窗口或本地浏览器中，共用 Python 后端与数据。

## 快速开始

**Windows 10/11 · Python 3.11+ · [uv](https://docs.astral.sh/uv/) · 桌面窗口另需 WebView2 Runtime**

~~~powershell
git clone https://github.com/DAOZHENREN/trace2task.git
cd trace2task
uv sync --locked --extra desktop
uv run --extra desktop trace2task desktop
~~~

桌面启动器会请求 Windows 管理员授权（UAC），取消则停止启动。请选择安装目录以外的数据目录。仓库已包含构建后的工作台资源，仅启动程序不需要 Node.js。

也可以使用浏览器：

~~~powershell
uv run trace2task web
~~~

打开 [localhost:8765](http://127.0.0.1:8765/)，端口占用时加 <code>--port 8766</code>。不要从多个实例同时执行桌面控制任务。

**先试一个小任务：** 打开空白记事本 → 选择模型与「不使用经验」→ 输入无破坏性的指令 → 核对范围和数据去向 → 确认执行。需要时使用 **停止 / F9**。模型凭据、本地权重，以及可选录制/驱动组件均需单独配置。

<details>
<summary>复用数据、开发版启动与安装包构建</summary>

不移动或覆盖数据，也可以复用已有目录：

~~~powershell
uv run --extra desktop trace2task desktop --project-root "D:\Trace2TaskData"
~~~

首次配置依赖后，可双击 <code>Start Trace2Task Dev.vbs</code> 启动源码版。修改 Python 后重启程序，修改前端后先重新构建，详见[桌面开发版](docs/desktop-development.md)。

仓库提供[安装包构建脚本](docs/desktop-app.md)，不内置模型权重，也不代表已经发布安装包下载。构建还需要 Node.js、PyInstaller 和 Inno Setup；程序、录制与权重分开存放。

</details>

## 从示范到经验

**录制 → 生成 → 审查 → 选择 → 执行 → 复盘**

1. **录制示范。** 用 OpenCUA 记录主显示器上的操作，**F8** 完成、**F9** 取消。所需组件及采集边界见[录制说明](docs/opencua-recording.md)。
2. **生成经验。** 本地生成只有动作的 **D** 或包含视觉证据的 **A**；由所选 Codex 编译模型生成待审查的方法 **B**，也可将 B 与其冻结证据组合为 **C**，组合不再次调用模型。
3. **完整审查。** 点击「查看模型原文」，阅读实际保存的全部文本；A 还展示每张选中图片。原始文件和审计信息单独保留。
4. **选择参考方式。** 可由执行 Agent 检索当前已确认 B 的摘要、按需加载一份方法，也可手动指定当前版本或不使用经验。未保存草稿不会进入自动选择。
5. **执行并复盘。** 根据当前画面，在所选范围内规划、执行、重新观察。检查运行证据，不把停止信号当作成功证明。

运行结束后，可把证据提炼成按阶段、条件和反馈组织的方法，审阅或编辑草稿，再选择**成为已有经验的新版本**或**成为新的独立经验**。经验库与自动选择只使用当前版本；旧版在「版本历史」中查看和回滚，不自动删除。独立经验拥有自己的版本历史，可分别参与自动检索，同时保留原始来源证据。详见[工作台说明](docs/workbench.md)。

### 模型到底会收到什么？

| 表示 | 交给模型的经验内容 | 状态 |
| --- | --- | --- |
| **N · 无经验** | 不提供示范，只使用本次任务与当前观察 | 已实现 |
| **D · 动作序列** | 按顺序排列的动作描述、持续时间、可见子动作与点击坐标 | 已实现 |
| **A · 动作与视觉证据** | 与 D 相同的动作结构，加上**最多 8 张**历史截图及关联引用 | 已实现 |
| **B · Procedure** | 编译后冻结、使用前审查的 Markdown 方法 | 已实现 |
| **C · 证据 + Procedure** | 所选 B 的冻结 A 证据，加上该 B 的完整原文 | 已实现 |
| **R · 固定改进器候选** | 保留父版本谱系的独立修订，需审查后允许试用 | 实验功能 |

A 按时间顺序等距选取可用截图，包含首尾；不足 8 张全部选择。文件路径、哈希、视频时钟和原始事件日志只留在**审计归档**，不再塞入新版 A 的模型文本。图片仅近似对齐，不保证是严格前态，也不证明动作成功。

原始录制与编译版本彼此独立。同一录制再次生成相同表示时，需要确认，旧版本保留；旧版 A 不会自动改写。

已完成或停止的运行也可提炼为 B 草稿。若运行使用了经验，编译输入包含实际父方法与本次证据；审查保存后形成保留谱系的子版本，不覆盖原方法。人工干预明确标注来源，原样输出不宣称改进。

**边界要分清：** 旧 Windows/WAA 状态图、带讲解编译和 Guidance 独立保留，不混入当前 A/B/C/D 执行入口。自动选择首版支持 Codex/视觉 API 通用代理，不支持本地 GUI 专用模型或 MobileForge。[表示与版本管理细节 →](docs/workbench.md)

## 选择运行方式

| 路径 | 已接入能力 | 主要边界 |
| --- | --- | --- |
| **Codex CLI / 订阅** | 视觉执行、B 编译、运行提炼与修订 | 需独立安装 CLI 并登录 |
| **视觉模型 API** | 自选 Chat Completions 服务 | 必须支持图片及所请求的 JSON 格式 |
| **Qwen3-VL 8B** | 官方 Q4_K_M + F16 视觉投影，llama-server 推理 | 权重需单独准备并校验 |
| **Qwen3-VL 2B / GUI-Owl 1.5 2B** | llama-server 或 Transformers | llama 路径需完成 GGUF 转换与配置 |
| **MAI-UI 2B** | Transformers | 手机协议的 Windows 实验适配 |
| **D-5970 研究模型** | 保留冻结协议与历史记录，当前模型选择已隐藏 | 需独立可信模型包，**不接收经验** |

**D 经验表示**与 **D-5970 模型**不是同一个概念。

没有保存明确设置时，本地推理默认使用 llama-server；已有选择保留。不兼容的模型/引擎组合会报错，不静默切换。已注册 GUI 模型共用常驻服务与任务会话。[本地模型配置 →](docs/llama-gui-backend.md)

**实际操作：** 桌面/指定窗口使用 Cua；单个授权 Android 应用使用 ADB；Ancient Beast 可使用实验性独立浏览器。Win32 启动入口及其检查点恢复已停用，历史数据保留。后台输入并非通用能力，模型选择和参考经验都不会扩大授权范围。[Cua 限制](docs/cua-experimental-backend.md) · [Android 后端](docs/android-backend.md) · [独立浏览器](docs/ancientbeast-background-validation.md)

## 证据、隐私与限制

- **先审查，再分享。** 截图、输入文字、Trace 和模型日志都可能包含隐私；认证字段脱敏不代表整份运行记录可以公开。
- **知道数据发给谁。** Codex 与远程 API 会收到任务、截图和所选经验。本地推理在本机完成，首次下载依赖与权重仍需联网。
- **把操作当作真实操作。** 先用可丢弃文件验证。停止能取消后续工作，不能撤销已经送达的动作；响应延迟取决于后端。
- **结论不越界。** 单元测试不是模型效果 benchmark；应用层审计也不能显示服务商隐藏的提示词或推理过程。
- **运行数据不进 Git。** 不要提交录制、生成经验、检查点、权重、API Key 或未脱敏运行产物。

### 从桌面控制 Android

[ADB 后端](docs/android-backend.md)可发现已有连接、绑定单台设备与授权应用，支持单指操作、由模型选择时长的摇杆保持及连续转镜头滑动。小地图纠偏仍是默认关闭的内部实验。通用视觉代理支持追加指令、截图标注指导及确认后的人工接管；停止/F9、设备身份、应用范围、动作和时间预算继续生效。这不是完整 3D 寻路，也不是游戏任务成功证明。[MobileForge](docs/mobileforge.md) 是能力范围更窄的独立固定版本策略接入。

### 独立 Android 开发预览

独立的 [Android 工程](android/README.md) 支持录制无障碍事件与抽样截图，通过自配云端视觉 API 编译经验，并操作**一个选定应用**；保留反馈版本与急停功能。

这仍是开发预览：不是完整原始触摸采集，尚未实现电脑配对。JVM 测试和 APK 构建不能代替真机与服务商联调验收。

### 研究扩展

[Windows Agent Arena](integrations/windows_agent_arena/) 分离任务重置、执行与评估；[RSIAgent](docs/rsi-integration.md) 用于单独配置的 Linux 虚拟机练习。旧 [LangGraph 检查点](docs/langgraph-desktop.md) 只属于其文档说明的执行路径，不是每个模型后端都有。

报告实验时需要明确冻结资产与实际验证方法。任务级重置不等于整机快照恢复；只点确认也不构成独立的“Reviewed compile”方法。这些接入是研究工具，不是已经获得的 benchmark 成绩。

## 文档导航

| 从这里开始 | 进一步了解 |
| --- | --- |
| [工作台与经验生命周期](docs/workbench.md) | [动作投影契约](docs/research-D-visual-projection.md) |
| [桌面安装与打包](docs/desktop-app.md) | [源码开发](docs/desktop-development.md) |
| [llama-server 模型配置](docs/llama-gui-backend.md) | [原生模型](docs/local-gui-models.md) · [D-5970](docs/trained-model-local.md) |
| [OpenCUA 录制](docs/opencua-recording.md) | [讲解与证据](docs/narration-evidence.md) |
| [运行记录与模型审计](docs/model-io-audit.md) | [任务会话](docs/task-conversations.md) · [上下文管理](docs/local-prefix-cache.md) |
| [桌面连接 Android](docs/android-backend.md) · [MobileForge](docs/mobileforge.md) | [独立 Android 预览](android/README.md) |
| [WAA 报告](docs/waa-report-format.md) | [Compiler 快照](docs/compiler-snapshots.md) |

## 开发与测试

前端开发使用 Node.js 24，与 CI 一致。

~~~powershell
uv sync --locked --extra desktop --extra dev
npm ci --prefix frontend
npm run build --prefix frontend

uv run --extra dev pytest
uv run --extra dev ruff check src tests scripts
node --test tests/*.test.cjs scripts/*.test.mjs

# 浏览器回归使用隔离、禁止写入的预览实例
cd frontend
npx playwright install
npm test
~~~

离线测试不调用真实模型，也不控制桌面。JDK 17+ 可运行 Android 辅助程序的模拟框架协议测试，不使用 ADB，也不等于真机兼容性验证；缺少特定平台能力时，相关测试可能跳过。工作台构建资源随源码提交，修改前端后需同步重新构建。`artifacts/` 和可选的本机 `android-model-profile.json` 不应进入 Git。

## 致谢与许可

项目使用 Python、React、Fluent UI、pywebview，以及各指南中列明的模型与运行时。感谢 [OpenCUA](https://github.com/xlang-ai/OpenCUA)、[OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt)、[Windows Agent Arena](https://github.com/microsoft/WindowsAgentArena) 与 [OSWorld](https://github.com/xlang-ai/OSWorld) 的开放研究和工具。

代码采用 [Apache-2.0](LICENSE)。外部模型、驱动和工具保留各自的许可证及分发要求。
