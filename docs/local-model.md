# Legacy standalone Qwen API / 历史独立 Qwen API 入口

The current desktop workbench integrates Qwen 8B into the shared llama-server
model registry and lifecycle. Use **模型与连接 → Qwen3-VL · 8B → llama-server**;
see [current setup and verification](llama-gui-backend.md).

The instructions below retain the manual 8081 compatibility route. To use it,
select the generic **视觉模型 API** provider and explicitly configure its local
address/model. The local 8B model selector no longer chooses this API route.
No saved cloud API configuration or existing weights were changed by the migration.

## Requirements and launch

- Windows with a CUDA-capable NVIDIA GPU and compatible driver.
- [llama.cpp Windows CUDA binaries](https://github.com/ggml-org/llama.cpp/releases), including their matching CUDA runtime libraries. Tested with b11026 / CUDA 13.4.
- [Official Qwen GGUF weights](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF): `Qwen3VL-8B-Instruct-Q4_K_M.gguf` and `mmproj-Qwen3VL-8B-Instruct-F16.gguf`.
- Store binaries and weights outside this repository. The following paths are examples; adjust for your machine.

```powershell
.\scripts\start-local-qwen.ps1 `
  -ServerPath 'D:\Tools\llama-b11026\bin\llama-server.exe' `
  -ModelDirectory 'D:\Models\Qwen3-VL-8B-Instruct'
```

Keep this terminal open. Press Ctrl+C to stop the model service.
The script uses 16,384 context tokens, Q8 K/V cache, one slot, and 1024–1280 tokens per image.
It binds only to `127.0.0.1:8081`. Do not expose or proxy this unauthenticated service to a network.

## Web preset

| Setting | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:8081/v1` |
| Model alias | `qwen3-vl-8b-instruct` |
| Client key placeholder | `local-only` (not a cloud credential) |
| Reasoning | Provider default |
| Output | Strict JSON Schema |
| Timeout | 120 seconds |

先点击“只生成计划”验证，再执行真实任务。运行网页后台的机器必须能访问这个本机端口。

## Capacity and validation limits

On one RTX 5070 Ti Laptop 12GB, a synthetic screenshot passed both button localization and the real Trace2Task action parser. A 9,652-token screenshot request completed successfully with 16K + Q8 KV. These are smoke tests, not task-success benchmarks or latency guarantees.

显存仍需留给桌面和其他软件；启动失败或显存不足时先释放其他 GPU 工作负载。上下文包括经验、截图、历史和输出，16K 也有上限。Q8 KV 与 Q4 模型权重量化是不同设置。复杂游戏、密集控件与长任务仍需单独评测。
