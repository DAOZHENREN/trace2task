# D 结构化模型：Windows 本地推理与受限执行

这是独立的 **D/5970 自定义动作头** 入口，不是 G-compact 或 OpenAI-compatible API。
为防止当前项目覆盖训练代码，加载器在独立 Python 进程中只导入已校验的冻结 `source/src`。
它既可用于安全的 **图片/主屏截图 → 预测 → 标注 → 本机归档**，也可从
Trace2Task 主网页发起受限的主屏连续执行。两种模式共用同一个本机常驻模型服务，
但预览模式不会发送任何键鼠输入。

执行模式是实验性本地 Baseline：每轮截图、预测一个最多八动作的结构化动作组、
校验后送入现有 Win32 执行器，再重新观察。它有 F9 / 网页停止、40 动作上限、
焦点/分辨率/画面变化丢弃，以及重复无进展保护；不使用 Trace 经验或 LangGraph，
`done` 也不等于任务成功。请先在临时记事本等可恢复场景测试。

## 启动

```powershell
cd D:\MyProject\trace2task
.\scripts\start-trained-model.ps1
```

加载完成后可打开 http://127.0.0.1:8767/ 做独立预览，或在 Trace2Task 主网页选择
「Trace2Task D · 5970 · 结构化动作模型」。
选择截图或点击“3 秒后截取主屏”，输入原始任务文字。请提前隐藏敏感信息。
第一次启动及每次重启都会校验权重和全部冻结源码；模型加载后可连续预览，不必每次重新加载。
关闭服务使用启动终端 Ctrl+C。先停止原 Qwen 8B 服务，避免两套模型争抢显存。

## 文件和依赖

- 模型包：`D:\Models\Trace2Task-D-5970`，不纳入 Git。
- 独立环境：包目录下 `.venv`，Windows CPython 3.11.9（Python 官方 NuGet 包）。
- 核心依赖：`scripts/trained_model/requirements.txt`；全部实装依赖（含测试工具）：
  `scripts/trained_model/requirements-lock.txt`。普通依赖本次通过清华 PyPI 镜像下载，CUDA wheel 从官方站点下载。
- PyTorch 2.7.1 + torchvision 0.22.1 的 CUDA 12.8 Windows 构建，依据
  [官方安装表](https://pytorch.org/get-started/previous-versions/#v271)。
- 与服务器不同：Python 3.11.16 → 3.11.9（本机可下载的官方发行包）；torch 2.5.1/cu124 →
  2.7.1/cu128（Blackwell 支持）。Transformers、tokenizers、PEFT、accelerate、safetensors 保持指定版本。
- 不依赖 FlashAttention，不量化，不 torch.compile，不强制把整个 bridge 转 BF16。

安装已有环境时：
```powershell
uv pip install --python D:\Models\Trace2Task-D-5970\.venv\Scripts\python.exe `
  -r scripts/trained_model/requirements.txt `
  --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match
```

## 数据契约与安全边界

- 严格校验交接 checkpoint SHA256、frozen-launch 中底座/processor 校验值、source manifest。
- 可信且固定哈希的本项目 checkpoint 才允许 `weights_only=False`；checkpoint 始终先加载 CPU。
- 严格检查全部 trainable 参数名称集合、shape、dtype、有限值及复制一致性。
- 用冻结 `build_record` 生成合法记录，使用冻结 collator 的原始提示词，不注入答案。
- target 的 `end_step` 只是 schema 占位；predictor 自行替换目标，不作为期望答案输入。
- 单次预览的历史为空；执行循环只传入最近四个真实送达的动作。预测、拒绝或因画面
  变化丢弃的动作不会写入历史。
- 固定预算：65536–1048576 pixels、4096 sequence tokens、8 actions、128 text tokens。
- end_step 只结束动作组；done 不代表完成验证。空组和超预算按冻结 decoder 显式报错。
- 无公开监听、无远程截图上传、无 eval/exec。只绑定 127.0.0.1，POST 校验 Host、Origin 和随机 token。
- 每一轮只有一张截图；同组动作之间没有新的模型观察。执行器可因明显画面变化而
  丢弃后续动作，但这不构成任务语义验证。

## 日志

`runs/trained-model-preview/`：

- `hash-verification.json`：文件哈希校验；`cuda-check.json`：实际 GPU 矩阵运算。
- `load-report.json`：全部 trainable 参数 shape/dtype、LoRA 范围、依赖版本、加载显存。
- 每次预测独立目录：`screenshot.png`、`record.json`、`prediction.json`、成功解码的 `annotated.png`。
- `emitted_actions` 保留动作头逐步输出（含边界）；失败保存 `error.log`，不会把超限当成功。
- 耗时包含整组预测；峰值显存为 PyTorch allocated/reserved，不是整个系统 GPU 总用量。

# 历史验证记录

早期交接的 A 阶段验收只覆盖预测和标注；当时尚未接入执行。现已通过独立的
`trained_model_runner.py` 将严格解码后的动作接入既有 Win32 执行循环。该变化不表示
模型已可靠，也不表示完成条件得到验证。
# Trace2Task 主网页入口

在主网页（例如 `http://127.0.0.1:8766/`）的“执行模型来源”选择
**Trace2Task D · 5970 · 本地结构化动作模型**，输入任务，点击“只生成计划”可预览。
点击“开始执行”会创建有日志的主屏连续任务。首次截图前有 3 秒切换目标窗口的时间；
之后每轮重新观察。F9 或网页停止，最多 40 个动作 / 40 轮预测。首次请使用空白记事本，
不运行敏感任务。
可选择图片；未选择时加载完成后等待 3 秒截取主屏。首次使用自动启动专用
Python 环境中的模型，后续复用。无需打开 8767 页面或另外输入启动命令。

单次预览历史为空；执行循环传入最近四个真实执行步骤，不注入经验或 LangGraph。
未执行的动作不会进入历史。点击、输入等造成显著画面变化或焦点变化后，丢弃剩余
动作重新观察；重复三次无明显进展的点击会被阻止。done 只让循环停止，不宣称成功。
原始输出、标注、耗时、显存和保存目录都显示在页面中。`done` 不代表任务成功。
截图只通过固定本机回环地址传递，不经过系统代理。支持现有执行器的 click、double_click、
hold_mouse、drag、type_text、press_key、hold_key、hotkey、wait。未映射的 wheel/scroll、
move_to、key_down/up 等动作会显式报错，不能视为成功；文本和按键也必须通过执行器校验。
日志在运行目录的 trace.jsonl / result.json，模型完整记录在后台输出目录。
旧版服务不支持历史协议，需要重启一次，此后跨任务复用模型。
