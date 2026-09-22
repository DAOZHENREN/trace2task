"""Native window around the existing local console; no alternate agent runtime."""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

from trace2task.web_console import WebConsoleController, create_web_server

logger = logging.getLogger(__name__)

BUSY_STATUSES = {
    "queued", "running", "stopping", "awaiting_recording_start", "awaiting_narration",
}


def can_close(controller, window) -> bool:
    job = controller.active_job()
    if job and job.get("status") in BUSY_STATUSES:
        window.create_confirmation_dialog(
            "任务尚未结束",
            "请先在页面中停止任务或完成录制/讲解处理，再关闭窗口。\n"
            "现在不会关闭，避免中断记录或留下未确认的操作。",
        )
        return False
    return window.create_confirmation_dialog(
        "退出 Trace2Task",
        "退出桌面控制台及其网页后台？\n"
        "独立的本地模型服务不会关闭，仍占用显存；其他网页服务不受影响。",
    )


def run_desktop(project_root: Path, *, webview_module=None, smoke_test=False) -> None:
    root = project_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project root must be a directory")
    if webview_module is None:
        try:
            import webview as webview_module
        except ImportError as error:
            raise RuntimeError("请安装桌面依赖：uv sync --extra desktop") from error
    controller = WebConsoleController(root)
    # Own an ephemeral loopback port, never kill or attach to an unrelated web process.
    server = create_web_server(root, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="desktop-http")
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        logger.info("Desktop console %s; data root %s", url, root)
        window = webview_module.create_window(
            f"Trace2Task — {root.name}", url, width=1440, height=960,
            min_size=(960, 640), text_select=True,
        )
        window.events.closing += lambda: True if smoke_test else can_close(controller, window)
        window.events.loaded += lambda: logger.info(
            "Desktop page loaded: %s", window.evaluate_js(
                "JSON.stringify({title:document.title,ready:document.readyState,"
                "console:!!document.querySelector('#model-provider')})"
            ),
        )
        if smoke_test:
            def verify_page():
                result = window.evaluate_js(
                    "JSON.stringify({ready:document.readyState,"
                    "console:!!document.querySelector('#model-provider')})"
                )
                (root / "desktop-smoke.json").write_text(result, encoding="utf-8")
                window.destroy()
            window.events.loaded += verify_page
        # Do not expose a Python JS bridge, and do not silently fall back to legacy MSHTML.
        webview_module.start(gui="edgechromium", debug=False, private_mode=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def choose_data_root(explicit=None):
    def validate(root):
        if getattr(sys, "frozen", False) and root.is_relative_to(
            Path(sys.executable).resolve().parent
        ):
            raise ValueError("数据目录不能位于程序安装目录内，请选择单独的 D 盘文件夹")
        return root

    if explicit is not None:
        root = validate(Path(explicit).expanduser().resolve())
        root.mkdir(parents=True, exist_ok=True)
        return root
    settings = Path(os.environ["LOCALAPPDATA"]) / "Trace2Task/desktop-settings.json"
    if settings.is_file():
        try:
            saved = json.loads(settings.read_text(encoding="utf-8"))
            value = saved.get("data_root") if isinstance(saved, dict) else None
            if isinstance(value, str) and value.strip():
                root = Path(value)
                if root.is_absolute() and root.is_dir():
                    return validate(root.resolve())
            logger.warning("Saved data directory is invalid; requesting a new selection")
        except (OSError, ValueError):
            logger.warning("Cannot use saved desktop settings; requesting a new selection",
                           exc_info=True)
    # First launch selects user-owned data, never the installed program directory.
    import tkinter
    from tkinter import filedialog

    dialog = tkinter.Tk()
    dialog.withdraw()
    try:
        selected = filedialog.askdirectory(
            title="选择 Trace2Task 数据目录（可选原 D 盘项目目录）", mustexist=True,
            parent=dialog,
        )
    finally:
        dialog.destroy()
    if not selected:
        return None
    root = validate(Path(selected).resolve())
    settings.parent.mkdir(parents=True, exist_ok=True)
    # An interrupted save must not corrupt the previous working selection.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=settings.parent, delete=False,
    ) as stream:
        pending = Path(stream.name)
        json.dump({"data_root": str(root)}, stream, ensure_ascii=False)
    try:
        os.replace(pending, settings)
    finally:
        pending.unlink(missing_ok=True)
    return root


def main(argv: list[str] | None = None) -> None:
    """Windowless launcher: preserve startup errors instead of silently disappearing."""
    import argparse
    import ctypes
    import hashlib
    import traceback

    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    mutex = None
    handler = None
    root_logger = logging.getLogger()
    old_level = root_logger.level
    logs = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Trace2Task/logs"
    try:
        root = choose_data_root(args.project_root)
        if root is None:
            return
        # All entry points share the same data-directory mutex.
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        name = hashlib.sha256(str(root).casefold().encode()).hexdigest()[:24]
        mutex = kernel.CreateMutexW(None, False, "Local\\Trace2Task-" + name)
        if not mutex:
            raise ctypes.WinError()
        if kernel.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(
                0, "这个数据目录的 Trace2Task 已经运行。", "Trace2Task", 64,
            )
            return
        data_logs = root / "runs/desktop"
        data_logs.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(data_logs / "desktop.log", encoding="utf-8")
        logs = data_logs
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        os.environ["TRACE2TASK_DATA_ROOT"] = str(root)
        run_desktop(root, smoke_test=args.smoke_test)
    except Exception:
        error = traceback.format_exc()
        # Configuration errors can occur before the user-data logger is available.
        if handler is None:
            try:
                logs.mkdir(parents=True, exist_ok=True)
                with (logs / "desktop.log").open("a", encoding="utf-8") as stream:
                    stream.write(error + "\n")
            except OSError:
                logger.warning("Could not write startup error log", exc_info=True)
        logger.error(error)
        ctypes.windll.user32.MessageBoxW(
            0, f"启动失败，请查看：{logs / 'desktop.log'}\n\n{error[-1200:]}", "Trace2Task", 16,
        )
        raise
    finally:
        if mutex:
            kernel.CloseHandle(mutex)
        if handler is not None:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.setLevel(old_level)


if __name__ == "__main__":
    main()
