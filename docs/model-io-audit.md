# 模型与执行器原始 I/O

新的桌面和单窗口实际执行会在运行目录增加 `io-audit/events.jsonl`。
这是应用边界审计，不是 TLS 网络抓包，也不是服务端内部提示词或隐藏思维链的导出。

- `model_request`：API/本地模型完整请求 JSON，包括 system、全部 messages 历史、图片 data URL、输出 Schema、模型与显式推理设置。认证字段脱敏。
- `http_response`：默认 HTTP 传输返回的响应正文和状态码，含非 JSON、HTTP 错误；超出原有响应上限时标记 truncated。网络失败另记 model_error。响应头不保存。
- `model_response`：完整解析后的服务商响应，含其实际返回的 content、reasoning_content、usage 等；在业务校验前记录，拒绝的回答也可查看。
- `codex_request` / `codex_response`：订阅通道可见的请求及接收协议事件，包含 outputSchema、线程配置、模型回答。临时 localImage 原图按 SHA-256 复制到审计目录，并保留原路径映射。服务端未公开的内容无法归档。
- `executor_input`：交给本地执行器的动作参数及规划序号，在安全检查之前记录。
- `executor_result` / `executor_error`：实际结果或阻止/失败原因。计划中未交给执行器的动作不是已执行动作；结合原 trace 的批次边界与模型回答查看。

API 图片保留完整 base64，方便还原请求，但会增加磁盘用量。日志包含截图、输入文本与完整经验，按敏感本地数据保管，不要直接上传 GitHub。
密钥与 Bearer 认证会脱敏；不记录认证 Cookie。不会读取模型服务的私有配置。
旧运行没有记录的系统消息和网络响应不能事后恢复。测试/自定义 transport 没有真实 HTTP 正文，只能记录返回对象。
