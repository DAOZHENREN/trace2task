# Windows 采集源时间验证（2026-09-28）

## 结论与边界

独立 DXGI 源探针取得了**同一次 AcquireNextFrame 返回的桌面像素和 LastPresentTime**，完成三次重新初始化、72 次定时画面变化验证，未发现前态包含本次新标记。可继续研究源时间传递，但尚未接入正式 OBS 抽帧，也没有证明任意负载下的误差上限。

LastPresentTime 是 Windows 为桌面图像更新记录的 QPC 值，不是物理显示器扫描时刻，也不是取得 GPU 纹理或编码完成的时间。静止桌面可能只有鼠标更新，此时时间为 0；探针跳过该样本，绝不拿鼠标时间冒充图像时间。

官方依据：[DXGI_OUTDUPL_FRAME_INFO](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/ns-dxgi1_2-dxgi_outdupl_frame_info)、[AcquireNextFrame](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/nf-dxgi1_2-idxgioutputduplication-acquirenextframe)。

## 实测

原始报告：`D:\Trace2Task-deps\opencua-dxgi-clock-validation\cea8f841-f601-4643-998a-1bdefcf947a2\summary.json`。

| 轮次 | 源图像数 | 匹配变化 | 前态误含新标记 | 请求到首次可见源图像的时间，中位数 / 范围 |
| --- | ---: | ---: | ---: | --- |
| 1 | 322 | 24/24 | 0 | 29.524ms / 18.901–56.928ms |
| 2 | 323 | 24/24 | 0 | 39.052ms / 22.496–69.003ms |
| 3 | 322 | 24/24 | 0 | 41.988ms / 18.906–64.234ms |

共 967 张色块区域 PNG，各自保存 SHA256 和原始 QPC 元数据；没有保存桌面其他区域。Python perf_counter_ns 与 QPC 的前后夹取核验 100/100 通过。选中前态距请求时间为 0.240–49.356ms；这不是时钟误差。

上述延迟包含 Tk 绘制、桌面合成、采样跳帧，**不能解释为同步偏差，也不能承诺小于 80ms**。所有样本 AccumulatedFrames 均大于 1：探针没有收集每次显示更新，因此不能从“首次采到可见画面”推断“画面最初变动的精确时刻”。保留此字段而不掩盖丢过更新。

三轮均无不可读标记。按“事件发生前已经复制完毕的最近帧”选择的缓冲方案也未发现后态泄漏。测试是定时标记，不是人工键鼠钩子；未测长录制、睡眠、锁屏、HDR/旋转屏及压力负载。

## 实现与复现

```powershell
& scripts/opencua/build-dxgi-probe.ps1
& D:/Trace2Task-deps/opencua-runtime/Scripts/python.exe scripts/opencua/validate_dxgi_clock.py --rounds 3 --markers 24
& D:/Trace2Task-deps/opencua-runtime/Scripts/python.exe tests/test_opencua_dxgi_clock.py
```

- `dxgi_clock_probe.cpp` 使用 Windows SDK 接口，通过 DXGI 枚举真正的主屏及其显卡；仅支持未旋转的 BGRA8 输出，格式或尺寸变化明确拒绝。
- 在持有同一帧期间复制测试矩形，遵守纹理 RowPitch；释放后才处理下一帧，退出释放 D3D/DXGI 资源。
- 50ms 有限等待，可停止；访问丢失等错误终止诊断，不悄悄重新初始化后拼接时间轴。
- Python 保存每张 PNG、哈希、源时间、获取前后时间、复制完成时间、累积帧数与保护内容标志。检查顺序、时钟和保护内容，不从文件创建时间推算。
- 6 项离线测试通过，覆盖 ABI、严格前态边界、错误时间导致的后态泄漏、缺证据与不合法源元数据。
- 构建仍复用 D 盘 Zig 0.15.2，没有安装新的全局组件。DLL SHA256：`fd49dfb2eaec32cfca115e517cc332680dbadb9ee4634a695b315bc12563b17a`。

## 尚缺什么

这是一条独立 DXGI 采集流，不是 OBS 正在使用的纹理流。**不能把这里的时间按最近邻、帧序号或固定偏移直接贴到 OBS 视频帧上。** 下一步需要从 OBS 实际采集源携带图像更新标识和源时间，经过纹理复用、合成、队列和编码后仍能对应到最终 PTS，再用同类因果测试验证。OBS 使用 WGC 时亦须独立核实其时间语义，不混用 DXGI 时间。

未修改官方 AgentNetTool、OBS 二进制或已安装 Trace2Task；没有新增生产依赖，没有替换正式抽帧规则。
