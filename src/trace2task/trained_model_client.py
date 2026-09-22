"""Local-only bridge to the isolated, frozen D-model Python environment."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from trace2task.local_process import stop_started_process

_lock = threading.Lock()
_origin = "http://127.0.0.1:8767"


class NoRedirect(HTTPRedirectHandler):
    """A local-only model endpoint must never turn into a remote request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def predict_local(task: str, *, image: str | None = None, history=None, step_index=0) -> dict:
    if not isinstance(task, str) or not task.strip() or len(task) > 10000:
        raise ValueError("请输入任务指令（最多 10000 字符）")
    if image is not None and (not isinstance(image, str) or len(image) > 20_000_000):
        raise ValueError("图片过大或格式不正确")
    if not _lock.acquire(blocking=False):
        raise RuntimeError("D 模型正在预测，请等待本次结束")
    try:
        # Never send screenshots through system/cloud proxies.
        opener = build_opener(ProxyHandler({}), NoRedirect())
        process = None
        try:
            with socket.create_connection(("127.0.0.1", 8767), timeout=1):
                pass
        except OSError:
            root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
            data_root = Path(os.environ.get("TRACE2TASK_DATA_ROOT", str(root)))
            bundle = Path(os.environ.get("TRACE2TASK_D_BUNDLE", "D:/Models/Trace2Task-D-5970"))
            python = bundle / ".venv/Scripts/python.exe"
            if not python.is_file():
                raise RuntimeError("未找到 D 模型环境；请设置 TRACE2TASK_D_BUNDLE") from None
            logs = data_root / "runs/trained-model-preview"
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / "server.log").open("ab") as out, (logs / "server-error.log").open("ab") as err:
                process = subprocess.Popen(
                    [str(python), str(root / "scripts/trained_model/preview.py"),
                     "--bundle", str(bundle), "--output", str(logs), "--serve"],
                    cwd=root, stdout=out, stderr=err,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    env={**os.environ, "PYTHONUTF8": "1"},
                )
        deadline = time.monotonic() + 180
        try:
            while True:
                try:
                    with opener.open(_origin + "/health", timeout=3) as response:
                        health = json.load(response)
                    if health.get("protocol") == 2 and health.get("status") == "ready":
                        with opener.open(_origin + "/", timeout=3) as response:
                            html = response.read().decode("utf-8")
                        break
                    if process is None and health.get("protocol") != 2:
                        raise RuntimeError("8767 已被不兼容的本地服务占用；未发送截图")
                except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                    pass
                if process is not None and process.poll() is not None:
                    raise RuntimeError("D 模型服务启动失败，请查看 runs/trained-model-preview/server-error.log")
                if time.monotonic() >= deadline:
                    raise RuntimeError("D 模型加载超时，请查看 runs/trained-model-preview/server-error.log")
                time.sleep(1)
        except Exception:
            # Only clean up the child this call started.  An already-running
            # service belongs to its original owner and must be left alone.
            stop_started_process(process)
            raise
        token = re.search(r"'X-Preview-Token':'([a-f0-9]{32})'", html)
        if not token:
            stop_started_process(process)
            raise RuntimeError("8767 不是兼容的 D 模型服务；未发送截图")
        body = {"task": task, "capture": image is None}
        if history is not None:
            with opener.open(_origin + "/health", timeout=3) as response:
                if json.load(response).get("protocol") != 2:
                    raise RuntimeError("请重启旧 D 模型服务以启用真实执行历史")
            body.update(history=history, step_index=step_index)
        if image is not None:
            body["image"] = image
        request = Request(_origin + "/predict", data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "Origin": _origin,
            "X-Preview-Token": token.group(1),
        })
        try:
            with opener.open(request, timeout=180) as response:
                return json.load(response)
        except (URLError, TimeoutError, OSError) as error:
            raise RuntimeError("D 模型预测未返回；没有执行任何操作，请查看本地服务日志") from error
    finally:
        _lock.release()
