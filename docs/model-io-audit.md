# 模型与执行器原始 I/O

新的桌面和单窗口实际执行会在运行目录增加 `io-audit/events.jsonl`。
这是应用边界审计，不是 TLS 网络抓包，也不是服务端内部提示词或隐藏思维链的导出。

- `model_request`：API/本地模型完整请求 JSON，包括 system、全部 messages 历史、图片 data URL、输出 Schema、模型与显式推理设置。认证字段脱敏。
- `http_response`：默认 HTTP 传输返回的响应正文和状态码，含非 JSON、HTTP 错误；超出原有响应上限时标记 truncated。网络失败另记 model_error。响应头不保存。
- `model_response`：完整解析后的服务商响应，含其实际返回的 content、reasoning_content、usage 等；在业务校验前记录，拒绝的回答也可查看。
- `codex_request` / `codex_response`：订阅通道可见的请求及接收协议事件，包含 outputSchema、线程配置、模型回答。临时 localImage 原图按 SHA-256 复制到审计目录，并保留原路径映射。服务端未公开的内容无法归档。
- `codex_retry`：Codex 标准错误流中的 HTTP 429 重试元数据，仅保存状态码、冷却秒数和重试次数，不保存原始日志。子进程固定使用 JSON 日志并启用该重试模块的警告，避免桌面启动环境不同导致漏报；部分首次 WebSocket 重试没有对应的协议错误事件。识别到明确冷却时，界面显示倒计时，冷却不计入执行时间预算。
- `codex_turn_error`：保留当前轮次的具体错误分类和 HTTP 状态码。`systemError` 状态先到时，最多再接收 1 秒的终止错误事件；若没有具体终止事件，则注明使用最后一次匹配本轮的重试错误。HTTP 403 单独归类为 `codex_access_denied`，不以笼统的系统错误或连接中断覆盖；这段等待不会重发请求，也不会接受迟到的成功结果执行动作。
- `executor_input`：交给本地执行器的动作参数及规划序号，在安全检查之前记录。
- `executor_result` / `executor_error`：实际结果或阻止/失败原因。计划中未交给执行器的动作不是已执行动作；结合原 trace 的批次边界与模型回答查看。

输出 Schema 按当前请求发送，完成核验使用独立的核验格式。Codex 将其放在 `turn/start.params.outputSchema`；视觉 API 的 `json_schema` 模式将完整规则仅放在 `response_format.json_schema.schema`，系统消息只保留简短输出要求及用户指导。`json_object` 模式没有承载 Schema 的响应格式字段，因此仍在系统消息中提供完整规则。两种 API 模式均不把旧系统消息追加到对话历史；本地动作校验继续执行。

`model-io.json` 为界面和审计在 `output_schema` 与 `input.output_schema` 两处保存规则，这种存储重复不代表再次向模型提交。查看发送次数与实际请求内容，应以 `io-audit/events.jsonl` 中的请求记录为准。

API 图片保留完整 base64，方便还原请求，但会增加磁盘用量。日志包含截图、输入文本与完整经验，按敏感本地数据保管，不要直接上传 GitHub。
密钥与 Bearer 认证会脱敏；不记录认证 Cookie。不会读取模型服务的私有配置。
旧运行没有记录的系统消息和网络响应不能事后恢复。测试/自定义 transport 没有真实 HTTP 正文，只能记录返回对象。
