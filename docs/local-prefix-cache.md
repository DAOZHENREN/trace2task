# 本地 Transformers 前缀 KV 缓存

适用：GUI-Owl 2B、MAI-UI 2B、Qwen3-VL 2B，当前固定 Transformers 4.57.1 服务。

服务跨请求保留首张图片之前的精确文本前缀 KV，缓存条目最多 6144 Token、一个条目。这只是前缀缓存容量，不是输入上下文上限；完整模型输入不因此截断。不预分配满额缓存，只存实际可复用的文本前缀，图片及生成尾部不计入保留条目。输入改按实时显存准入估计和模型自身窗口边界管理，显存压力下只移除历史图片；详见 task-conversations.md。
完整 Token 前缀一致才命中；模型切换、异常、取消时清空。不同文本前缀会替换缓存。
每次新截图都重新计算视觉特征和完整的多模态 RoPE；请求的后缀 KV 不保留。
副本用于生成，避免修改保留条目。模型服务的锁仍串行保护 GPU 及 RoPE 状态。

这不是持久会话或短期记忆：客户端仍发送完整有效输入，服务只跳过匹配前缀的重复计算。
图片之后的任务/经验/历史目前不会被此策略缓存，不能声称整个历史都已缓存。
不修改模型提示词或图片顺序来制造命中。固定缓存会占用一部分显存。

每次预测目录的 `prefix-cache.json` 记录命中、复用 Token 数及剩余预填充 Token 数。
运行指标 `phase_ms.prefill_ms` 包含前缀命中检查、缓存复制及当前后缀预填充；总 generate_ms 包含这段时间，不能相加。
内存指标同时保留 prefix_cache 信息。

离线 GPU 验证（无键鼠输入、不联网）：

```powershell
& D:/Models/Trace2Task-D-5970/.venv/Scripts/python.exe scripts/local_gui/validate_prefix_cache.py D:/Models/Trace2Task-GUI/qwen3-vl-2b
```

比较红/蓝图冷生成与缓存生成的完整输出 Token，另用不同文本前缀验证失效。
短例速度仅为冒烟数据，不代表实际 GUI 任务提速比例。修改后需要重启本地模型服务，单独重启桌面界面不足以加载服务代码。

参考：https://huggingface.co/docs/transformers/v4.57.1/kv_cache
