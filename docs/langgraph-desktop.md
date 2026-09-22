# LangGraph 桌面任务状态试验

## 使用

重启 `uv run trace2task web` 后，选择“整个桌面”，在“桌面任务管理”中选择：

- 原版：保留原来的截图和最近动作循环，供对照。
- LangGraph：增加子目标工作记忆和 SQLite 检查点。支持无经验及经验指导，复用现有模型接口。

运行回答额外包含 `progress`：当前子目标、剩余子目标、当前观察到的证据和待检查事项。
这些都是模型报告，不代表独立验证通过。前台已变化的规划不会更新已接受的工作记忆。
实际执行结果另外保存为 `last_outcome`；动作送达不等于效果完成，下一轮要看新截图核对。
无需新增一次模型调用，但提示和输出会变长，速度收益需要实测。

## 恢复

在同一区域的下拉菜单选择未完成运行，原指令会自动填入。
仍需选择原来使用的经验并保持同一版本；修改任务或经验内容会拒绝恢复。
点击开始执行后，新建一个运行目录，读取旧检查点中的工作状态，重新截图规划。
旧运行不被修改；旧坐标、待执行批次、截图和模型会话不被重放。
已完成任务不能恢复；结果未知的动作会被拦截，需要人工核对后另开新任务描述现状。
这是任务级恢复，不是虚拟机快照恢复。

## 文件与边界

- `checkpoints.sqlite`：LangGraph 官方 SqliteSaver 保存的权威检查点。
- `task-state.json`：便于查看的最新状态镜像，含子目标及动作结果。
- `trace.jsonl` 和 `io-audit/`：保留已有原始输入输出审计。
- 新运行 `summary.json` 记录 orchestration、resumed_from 和最终工作状态。

首版 LangGraph 管理 observe → plan → review 规划流程与工作状态；桌面副作用仍由原有
前台循环调用安全执行器，执行前后分别提交检查点。没有用图的自动重试重放鼠标键盘。
不引入多 Agent、额外 shell 工具或新长期记忆数据库。经验库及 Compiler 状态图不迁移。
本地检查点不发送至 LangSmith；只有原本选择的模型服务收到规划输入。
最终完成仍是 model_reported_complete / verified=false，尚未新增独立桌面效果验证器。

依据：[LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、
[Graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)。
先在相同初始画面、同一模型和同一任务下分别测试两种模式，观察重复动作、模型轮次和总耗时。
