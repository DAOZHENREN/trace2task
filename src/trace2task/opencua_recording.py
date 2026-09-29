"""Optional native OpenCUA recording component; never invokes a model."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from trace2task.local_process import stop_started_process


def component_paths():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return {
        "python": Path(os.environ.get("TRACE2TASK_OPENCUA_PYTHON",
                        "D:/Trace2Task-deps/opencua-runtime/Scripts/python.exe")),
        "upstream": Path(os.environ.get("TRACE2TASK_OPENCUA_SOURCE", "D:/Trace2Task-deps/AgentNetTool")),
        "obs": Path(os.environ.get("TRACE2TASK_OPENCUA_OBS", "D:/Apps/Trace2Task-OBS")),
        "worker": root / "scripts/opencua/worker.py",
        "derive": root / "scripts/opencua/derive.py",
    }


def record_opencua(*, task_id, output_root, stop_event, status_callback, max_seconds=1800):
    paths = component_paths()
    required = [paths["python"], paths["worker"], paths["obs"] / "bin/64bit/obs64.exe",
                paths["obs"] / "obs-plugins/64bit/trace2task-frame-clock.dll",
                paths["upstream"] / "agentnet-annotator/api/core/recorder.py"]
    if any(not path.is_file() for path in required):
        raise RuntimeError("OpenCUA 本地组件不完整，请安装 OBS 32.2.2、CTS 插件、官方源码及独立 Python 环境。")
    run = Path(output_root) / str(uuid.uuid4())
    # Worker creates the run atomically. Control file lives beside it until then.
    control = run.with_suffix(".stop")
    log_path = run.with_suffix(".log")
    args = [str(paths["python"]), str(paths["worker"]), "--upstream", str(paths["upstream"]),
            "--obs", str(paths["obs"]), "--output", str(run), "--task", task_id,
            "--stop-file", str(control), "--max-seconds", str(max_seconds)]
    run.parent.mkdir(parents=True, exist_ok=True)
    process = None
    status_callback("OpenCUA 正在启动独立 OBS；就绪后才开始操作。F8 完成，F9 或停止按钮取消。")
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ready = False
            started = time.monotonic()
            cancel_at = None
            while process.poll() is None:
                if stop_event.is_set() and cancel_at is None:
                    control.touch()
                    cancel_at = time.monotonic()
                    status_callback("正在结束录制并保存视频，请稍候。")
                if cancel_at is not None and time.monotonic() - cancel_at > 50:
                    raise RuntimeError("OpenCUA 停止超时；保留已有归档，视频可能尚未完整封装。")
                metadata_path = run / "trace2task.json"
                if not ready and metadata_path.is_file():
                    try:
                        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        metadata = {}
                    if metadata.get("capture_status") == "recording":
                        ready = True
                        status_callback("OpenCUA 已就绪：正在录屏并采集键鼠、窗口和控件树，已启用 OBS CTS 近似对齐。未启用语音。")
                if not ready and time.monotonic() - started > 90:
                    raise RuntimeError(f"OpenCUA 启动超时，请查看 {log_path}")
                if time.monotonic() - started > max_seconds + 120:
                    raise RuntimeError("OpenCUA 录制超过时间上限")
                time.sleep(0.15)
        if process.returncode:
            raise RuntimeError(f"OpenCUA 录制进程失败（{process.returncode}），日志：{log_path}")
        metadata = json.loads((run / "trace2task.json").read_text(encoding="utf-8"))
        browser = metadata.get("browser", {})
        if browser.get("status") != "received":
            status_callback("本次未收到浏览器 HTML；请确认已加载官方扩展并刷新网页。空文件不代表采集成功。")
        else:
            counts = browser.get("counts", {})
            status_callback(f"官方浏览器采集已保存：{counts.get('html', 0)} 条网页、{counts.get('element', 0)} 条点击控件；错误 {counts.get('errors', 0)} 条。")
        derivation = {"status": "skipped", "reason": "录制取消或未标记完成"}
        if metadata.get("success") and not stop_event.is_set():
            status_callback("正在复用官方 Reducer 整理动作，并按 OBS CTS 抽取近似参考图；不会上传数据。")
            derivation = derive_opencua(run, stop_event=stop_event, paths=paths)
            if derivation["status"] == "completed":
                status_callback(f"已生成 {derivation['action_count']} 个动作组及截图；在原始录制中打开动作图文目录，再打开 index.html。")
            else:
                status_callback("动作截图整理未完成，原始录制已保留：" + derivation.get("error", "已停止"))
        return {"mode": "opencua_record", "task_id": task_id,
                "success": metadata.get("success", False), "stop_reason": metadata.get("stop_reason"),
                "input_events": metadata.get("input_event_count", 0),
                "recording_directory": str(run), "video_path": metadata.get("video_path"),
                "events_path": str(run / "events.jsonl"), "log_path": str(log_path),
                "browser_capture": browser,
                "derivation": derivation,
                "compilation": {"status": "not_supported", "reason": "原生录像已归档；关键帧与 Compiler 适配尚未启用。"}}
    finally:
        if process and process.poll() is None:
            stop_started_process(process)
        control.unlink(missing_ok=True)


def derive_opencua(run, *, stop_event, paths=None):
    """Isolate optional dependencies and bound offline processing without losing raw data."""
    paths = paths or component_paths()
    run = Path(run).resolve()
    log_path = run.with_suffix(".derive.log")
    process = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(paths["python"]), str(paths["derive"]),
                "--upstream", str(paths["upstream"]), "--recording", str(run)],
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = time.monotonic()
            while process.poll() is None:
                if stop_event.is_set():
                    raise RuntimeError("动作截图整理已停止")
                if time.monotonic() - started > 300:
                    raise RuntimeError("动作截图整理超过 300 秒；原始录制保留")
                time.sleep(0.1)
        if process.returncode:
            raise RuntimeError(f"整理进程退出码 {process.returncode}；日志：{log_path}")
        return json.loads((run / "trace2task.json").read_text(encoding="utf-8"))["derivation"]
    except Exception as error:  # noqa: BLE001 -- return worker failure without losing raw recording
        return {"status": "failed", "error": str(error), "log_path": str(log_path)}
    finally:
        if process and process.poll() is None:
            stop_started_process(process)
