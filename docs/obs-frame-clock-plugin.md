# OBS 帧时钟插件：CTS 近似对齐

**2026-09-29 更新：用户接受 CTS 近似误差，源码中新录制已默认接入。** `worker.py` 保存时钟附加文件，`derive.py` 用 PyAV 校验 PTS 后按 CTS 选择参考帧。无严格前态承诺，未接入 DXGI。下文的“仅诊断 / 未替换”等为 9 月 28 日实验历史结论；当前行为与验证见 [录制说明](opencua-recording.md#cts-integration-2026-09-29)。已安装程序尚未重新打包替换。

后续源层独立实验见 [Windows DXGI 源时间验证](opencua-dxgi-source-clock-validation.md)：72 次标记未观察到后态泄漏，但尚未建立 OBS 视频 PTS 与源纹理的对应关系，生产规则仍未改变。

## 已实现

- Zig 0.15.2 Windows x64 构建工具位于 `D:\Tools\zig-python-0.15.2`；编译缓存也位于 D 盘。通过 PyPI 清华镜像安装固定版本，没有安装 Visual Studio 或修改全局 PATH。
- 插件 `scripts/opencua/obs_frame_clock.c` 使用 OBS 32.2.2 的公开 packet callback。仅接受该版本和 Windows x64；OBS ABI 声明来自固定版本官方头文件，并有结构体尺寸检查。
- 只有独立 OBS 子进程收到 `TRACE2TASK_OBS_FRAME_CLOCK` 才启用。默认普通录制不启用，不访问其他 OBS 实例。
- 插件不改变画面、包内容、编码参数和官方原始文件。记录存入独立 `obs-frame-clock.jsonl`。
- 每个视频包记录 PTS、DTS、timebase，以及 OBS 提供的 CTS、FER、FERC、PIR。编码回调只复制小型记录到有界缓冲；停止后才写文件。溢出、空 timing、暂停、未完成文件会被分析器拒绝。
- 最多记录 216000 帧，约 2 小时 @30FPS；达到上限明确标记无效，不冒充完整。
- `frame_clock.py` 根据实际 PTS 匹配最终视频帧，处理 B 帧乱序、停止时部分包未写入视频等情况；不按“第 N 个包就是第 N 帧”猜测。
- 诊断采用 PyAV 16.0.1 读取帧 PTS。实测 OpenCV 在一段视频上把首帧位置报告为 -33.333ms，而 PyAV 给出 0；不能将这种差异算作时钟偏移。

## 运行

从项目根目录构建：

```powershell
& scripts/opencua/build-frame-clock.ps1
```

插件已放入专用 OBS：

`D:\Apps\Trace2Task-OBS\obs-plugins\64bit\trace2task-frame-clock.dll`

DLL SHA256：`58e36a48e9d2c3792900e8c83180f40681e631f8c955cbd760c48424e573ac23`

在没有其他录制任务时运行（会录制主屏，原始视频仅本地保存）：

```powershell
& D:/Trace2Task-deps/opencua-runtime/Scripts/python.exe scripts/opencua/validate_sync.py --output D:/Trace2Task-deps/opencua-native-clock-validation --rounds 3 --markers 24 --frame-clock
```

停止验证按钮和 Escape 可取消。无需更改正式客户端；安装的 Trace2Task 程序尚未更新。插件无环境变量时不采集，也不注册录制回调。

## 已发现的关键限制

首轮短测已成功抓取首帧 PTS=0、CTS 和全部编码包。随后 24 次标记实验，经 PyAV PTS 校验：

| 按哪个时间选择前态 | 误包含本次新标记 | 首次可见帧时间减更新请求时间（中位数） |
| --- | --- | --- |
| CTS | 16/24 | -5.120 ms |
| FER | 0/24 | 61.892 ms |
| FERC | 0/24 | 62.306 ms |
| PIR | 0/24 | 583.000 ms |

证据：`D:\Trace2Task-deps\opencua-native-clock-validation\b08868e4-a0e7-487a-ad0b-baec60e0d6e2\round-01\native-clock-analysis.json`。

这不是统计可靠性承诺，但足以否定“只把启动原点换成首帧 CTS 即可精确抽前态”的方案。CTS 虽然来自相同单调时钟，却不是每张桌面图像的精确采集时刻。FER/FERC/PIR 更晚，不能偷偷改名为采集时间。

这轮录制最初因 OpenCV PTS 校验失败被拒绝，保留了原失败记录。之后用 PyAV 对同一原视频重新分析，生成上述结果；没有重写原始视频或插件时钟文件。

## 源码证据与下一步边界

### 三轮独立启动复测（PyAV）

最终证据根目录：`D:\Trace2Task-deps\opencua-native-clock-validation\842cb40b-a720-4c2f-92cd-fb7e955ccfc5`。

| 轮次 | 匹配视频帧 | CTS 误选后态 | FER 误选后态 | 现有启动原点规则误选后态 |
| --- | --- | --- | --- | --- |
| 1 | 469/469 | 12/24 | 0/24 | 0/24 |
| 2 | 469/469 | 22/24 | 0/24 | 0/24 |
| 3 | 469/469 | 21/24 | 0/24 | 0/24 |

合计 1407 帧匹配、72 次标记变化。CTS 方案误选 55/72；FER 延迟中位数分别为 67.161、53.381、54.927 ms。它能提供更晚的时间证据，但不是原始采样时刻，因此没有替换正式抽帧。

每轮有 1 个编码回调包未出现在最终视频中；分析器按 PTS 匹配并显式报告，不把包序号当帧序号。首帧均成功匹配。

测试期间还保留了一次启动建场景超时失败（目录 `21d51ebf-6fc6-43f5-afb8-78691457d975`）；重试完成三轮，其中一次 OBS 尚未 ready 的状态由已有启动重试处理。没有将失败轮次计入成功样本。

离线测试：`tests/test_opencua_frame_clock.py`、`tests/test_opencua_derivation.py`、`tests/test_opencua_recording.py` 共 23 项通过；测量逻辑独立 unittest 2 项通过。

### 原因与修改范围

- [OBS 帧 timing 定义](https://github.com/obsproject/obs-studio/blob/32.2.2/libobs/obs-encoder.h)：区分 CTS、FER、FERC、PIR。
- [OBS 视频调度](https://github.com/obsproject/obs-studio/blob/32.2.2/libobs/obs-video.c)：视频时间按帧间隔推进，并进入视频/纹理队列。这不是显示器内容的硬件采样回执。
- [OBS DXGI 采集](https://github.com/obsproject/obs-studio/blob/32.2.2/libobs-d3d11/d3d11-duplicator.cpp)：拿到 DXGI 帧信息后复制纹理，未将 LastPresentTime 作为公开的逐帧采集时间向这套输出回调传递。

真正的采集时间需要从实际使用的 Windows 采集源保留，并跟随纹理映射到最终 PTS。不同采集路径（DXGI/WGC）不能混用。将其补到源层会扩大修改范围，涉及官方采集组件，不在本次诊断插件中偷偷实现。

**当前生产抽帧仍保持原规则；插件诊断已接通，但“真实采集时间对齐”尚未完成。** 未测试真实人工键鼠钩子、长录制、设备休眠或全量异常退出。不要把诊断成功当作同步已解决。
