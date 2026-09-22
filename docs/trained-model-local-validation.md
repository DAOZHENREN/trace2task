# 本机 D/5970 验收记录（2026-09-21）

## 已通过：A 预测预览

- Windows RTX 5070 Ti Laptop GPU，12227 MiB，驱动 610.88。
- 已停止用户授权的旧 Qwen llama-server；未停止游戏或修改服务器文件。
- 从用户指定 SSH 主机只读复制模型文件；无训练数据、历史截图或中间 checkpoint。
- 204 项模型包文件校验通过，含全部冻结源码；checkpoint SHA256：
  `4c07d912b881ccc69a28477aedf6c2b349f91965dc5dca89ebba8995987d02d3`。
- 官方 torch Windows CUDA 12.8 wheel SHA256：
  `138c66dcd0ed2f07aafba3ed8b7958e2bed893694990e0b4b55b6b2b4a336aa6`。
- Python 3.11.9、torch 2.7.1+cu128、torchvision 0.22.1+cu128；其他核心训练依赖保持交接版本。
- CUDA 实际 BF16 矩阵乘法通过，不只是识别显卡。
- 261 个 trainable tensors 的名称集合、shape、dtype、有限值、复制结果逐项严格通过。
- 单 GPU BF16 底座 + 原 dtype LoRA/heads + SDPA；原预算，无量化、无 torch.compile。

## 首张真实截图

本机主屏：2560×1600 物理像素。任务：`打开记事本，输入 hello。`，历史为空。

原始输出：
```json
[
  {"skill":"click","args":{"x":0.01507568359375,"y":0.984375,"button":"left"}},
  {"skill":"end_step","args":{}}
]
```

- 预测耗时：1.85555 秒（不含模型加载，含整组动作解码）。
- 峰值 allocated：4,617,631,232 bytes，约 4.30 GiB。
- 峰值 reserved：4,924,112,896 bytes，约 4.59 GiB。
- 原图、record、预测和标注：
  `runs/trained-model-preview/20260921-144916-41952e76/`。
- GPU/加载/哈希证据：`runs/trained-model-preview/{cuda-check,load-report,hash-verification}.json`。
- 完整环境固定清单：`scripts/trained_model/requirements-lock.txt`。
- 本地 HTTP 预览接口复测：1.64832 秒，返回标注图，`executed=false`；无 token POST 返回 403。
- 3 项独立预览测试和 57 项原网页回归测试通过；源代码静态检查通过。

这是工程通路 smoke test，**不是任务成功率或定位正确性验证**。未执行该点击，不宣称已经打开记事本。
截图和细节日志只存本地，未上传给外部模型服务，也不加入 Git。

## 未完成：B 确认执行

尚未接入鼠标键盘执行、确认后动作组执行、紧急停止热键、执行后的真实历史。
因此“确认执行测试”和“紧急停止测试”均尚未通过，不可宣称可以可靠操控桌面。
A 预览由用户验收后再进入 B，不将未经确认的预测作为已执行动作。
