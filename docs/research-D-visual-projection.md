# D v2：官方展示动作的确定性精简投影

独立导出器：`scripts/opencua/export_d.py`，标准库实现，不调用模型、不读截图、视频、浏览器正文或验收答案。不接执行循环，不自动执行动作。

## 程序入口

桌面程序的原始录制列表，对已完成动作整理的 OpenCUA 录制提供“生成 D · 精简动作序列”。产物写入数据目录的 `trace-library/<唯一编号>/`，不会修改原录制。生成后的条目先展示 trace_name、description 和表示类型，展开“读取轨迹正文”时才请求正文。现有录制 Compiler 入口保持不变；OpenCUA 的语义 Compiler 尚未接入，不将 D 导出冒充语义编译。

- `header.json`：名称、描述、表示类型、正文哈希、生成时间与源录制身份；列表只读此文件。旧版本缺少生成时间时，明确显示文件时间参考，不改写旧文件。
- `body.json`：完整结构化正文；按需读取并核对哈希。
- `model-input.txt`：可独立查看的模型输入文本。
- `manifest.json`：生成过程的审计信息。

工作台重复生成 D 时按源录制身份检测已有可用 D 版本，必须确认后才创建新目录；取消不创建产物，已有版本和原录制不覆盖。不同录制即使同名也不按名称去重。A/B/C 的既有定义及当前实现边界见 [工作台说明](workbench.md)。

最初接入生成、目录和人工按需读取；当前工作台已允许用户手动选择 D 的完整文本用于任务执行，仍不自动选 Trace。原始证据 A 沿用独立版本、审查与使用流程；新版本向模型提供同一 D 式动作投影及最多 8 张明确选中的关联历史截图，完整原始文件另存审计归档，详见工作台说明。B/C 尚未实现，不标为可用。

## 输入与输出

只读 `trace2task.json` 的 task_id、`metadata.json` 的屏幕尺寸、`reduced_events_vis.jsonl` 和 `reduced_events_complete.jsonl`；如有 manifest，仅将未覆盖事件诊断带入审计文件。

输出必须是原录制目录以外的新目录，拒绝覆盖已有输出：

- `model-input.txt`：可以作为执行模型的历史示范输入。包含源任务名、示范画面尺寸及精简动作。当前任务与实时截图仍由执行循环另外提供。
- `manifest.json`：只用于人类审计，不默认给模型。包含输入哈希、生成规则、原始时间和事件起点引用、输入局限。不是 Procedure，也没有人工语义总结。

## 投影规则

1. 保留 vis 的顺序、id，以及可见 children 的完整嵌套关系。输出 action 直接使用官方 description（例如 Single left Click），不再重复输出动作 description。官方 action 类型仅在内部用于匹配和提取参数。
2. 起止时间换成 duration_seconds，差值四舍五入到三位小数。父持续时间含停顿和子操作，不能对子时长求和代替。
3. 不输出 depth、target、axtree 或其他未允许字段。
4. 顶层按 ID 对应完整动作，并核对动作类型和起止时间；子节点按同一父节点下的类型和起止时间唯一匹配，不能按列表位置硬凑。隐藏子节点不会被添加到 D。
5. click 补充 coordinate；多次点击保留全部 coordinates，避免丢掉双击中的不同位置。坐标缺失、匹配不唯一或时间非法则导出失败，不猜测。
6. 所有动作的官方 description 原样放入 action。例如 drag 已有起终点文字，当前不另复制轨迹点；输入法拼音不翻译为汉字，粘贴不猜剪贴板内容。

顶层 trace_name 原样取录制任务名，不称作 source_instruction。顶层 description 为“人工录制的精简动作序列：包含动作描述、持续时间、可见子动作、点击坐标；不包含截图和动作解释”。这里只借用“名称、描述、正文”的组织方式，不接入 Skill 机制，也不要求 A/B/C 使用相同描述。这是历史示范表示，不是执行器的统一动作协议。

## 局限

本版本表示官方可见动作序列，不声称覆盖每个原始事件。未覆盖事件不自动补成动作。动作间间隔、不可见子节点和拖拽中间路线被省略；主键盘操作通过官方 description 表达。
源任务名可能不含在屏幕上阅读的完整要求，不能用独立验收答案补全。当前没有对整个动作序列做 token 截断，也未测模型效果。

## 本地修正版样本

```powershell
uv run python scripts/opencua/export_d.py `
  --recording D:/MyProject/trace2task/runs/824e8f7c-46fa-4a8f-8dc3-45ae472ed8ca `
  --derived D:/MyProject/trace2task/runs/824e8f7c-46fa-4a8f-8dc3-45ae472ed8ca/derived/0e178216-0f61-44c6-9d84-dbf6b35aebf4 `
  --output D:/Trace2Task-Evaluation/meeting-handoff-D0/D-visual-v2
```

示例有 41 个主动作、46 个含子动作节点。再运行请换一个新目录，原始录制及既有派生文件不被写入。旧 v1 输出保留，不覆盖。
