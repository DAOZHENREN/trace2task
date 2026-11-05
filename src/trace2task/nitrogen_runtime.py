"""Managed entry point for the pinned MineDojo NitroGen research runtime."""

import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

REVISION = "32608444660950ffda95e1e57c79632ad65bea10"
CHECKPOINT_SHA256 = "a266f5fb9c7dbdcdf97216558d2d82075a9a994b824cda69afa9fd3280260a81"


def runtime_paths():
    root = Path(os.environ.get("TRACE2TASK_NITROGEN_ROOT", "D:/Models/NitroGen")).resolve()
    python, upstream, checkpoint = root / ".venv/Scripts/python.exe", root / "upstream", root / "ng.pt"
    if sys.platform != "win32" or not Path(__file__).is_file():
        raise ValueError("NitroGen 当前需要 Windows 源码版 Trace2Task")
    for path in (python, upstream / "scripts/serve.py", upstream / "scripts/play.py", checkpoint):
        if not path.is_file():
            raise ValueError(f"NitroGen 运行文件尚未就绪：{path}")
    return python, upstream, checkpoint


def validate_request(process, seconds):
    if not isinstance(process, str) or not re.fullmatch(r'[\w .()-]{1,120}\.exe', process, re.IGNORECASE):
        raise ValueError("请输入游戏进程的完整 .exe 文件名")
    if type(seconds) is not int or not 5 <= seconds <= 600:
        raise ValueError("NitroGen 时长须为 5–600 秒")


def validate_task_text(value):
    if not isinstance(value, str):
        raise TypeError("截图任务文字必须是字符串")
    value = value.strip()
    if len(value) > 32 or (value and not value.isprintable()):
        raise ValueError("截图任务文字最多 32 字，请使用单行可打印文字")
    return value


def make_task_overlay(text):
    """Render a small centered banner for the 256×256 model frame."""
    from PIL import Image, ImageDraw, ImageFont

    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    font_path = next((fonts / name for name in ("msyhbd.ttc", "msyh.ttc", "simsun.ttc")
                      if (fonts / name).is_file()), None)
    if font_path is None:
        raise RuntimeError("缺少用于截图任务文字的 Windows 中文字体")
    font = ImageFont.truetype(str(font_path), 14)
    lines = [""]
    for char in text:
        if font.getlength(lines[-1] + char) > 228:
            lines.append("")
        lines[-1] += char
    width = max(round(font.getlength(line)) for line in lines) + 12
    stamp = Image.new("RGBA", (width, 20 * len(lines) + 4), (10, 10, 10, 225))
    draw = ImageDraw.Draw(stamp)
    for index, line in enumerate(lines):
        draw.text((6, 3 + 20 * index), line, fill="white", font=font, anchor="lt")
    return stamp


def run_game(paths, process, seconds, root, stop, status, *, on_round=None, task_text=""):
    python, upstream, checkpoint = paths
    root.mkdir(parents=True, exist_ok=True)
    stop_file = root / "stop"
    upstream_revision = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).strip()
    if upstream_revision != REVISION:
        raise ValueError("NitroGen 上游运行版本与已验证版本不一致")
    with checkpoint.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != CHECKPOINT_SHA256:
            raise ValueError("NitroGen 权重 SHA-256 校验失败")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    worker = [str(python), "-u", str(Path(__file__).resolve())]
    env = {**os.environ, "HF_HOME": str(upstream.parent / "hf-cache"), "PYTHONIOENCODING": "utf-8"}
    cached_backbone = upstream.parent / "hf-cache/hub/models--google--siglip2-large-patch16-256/refs/main"
    if cached_backbone.is_file():
        env["HF_HUB_OFFLINE"] = "1"
    server_log, player_log = root / "serve.log", root / "play.log"
    with server_log.open("wb") as server_output, player_log.open("wb") as player_output:
        stop.raise_if_requested()
        server = subprocess.Popen(
            [*worker, "serve", str(upstream), str(port), str(checkpoint), str(os.getpid())],
            cwd=root, stdout=server_output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        player = None
        messages = {
            b"NITROGEN_WAIT_FOCUS": "请切换到游戏窗口，NitroGen 最多等待 20 秒。",
            b"NITROGEN_REALTIME": "NitroGen 实时模式已启用，游戏在推理期间继续运行。",
            b"NITROGEN_FOCUS_PAUSED": "游戏已失去焦点，手柄已释放。请在 20 秒内切回游戏。",
            b"NITROGEN_FOCUS_RESUMED": "已切回游戏，NitroGen 将根据新截图继续。",
            b"NITROGEN_BIND_CONTROLLER": "以撒手柄绑定：在游戏前台按 F5 下移、F6 确认、F7 返回。用 F6 从菜单继续游戏，F8 测试右移，看到角色移动后按 F10 开始。",
        }
        reason = "time_limit"
        log_offset = 0

        def read_player_log():
            nonlocal log_offset, reason
            with player_log.open("rb") as log:
                log.seek(log_offset)
                while (line := log.readline()).endswith(b"\n"):
                    log_offset = log.tell()
                    marker = line.split(maxsplit=1)
                    if marker and marker[0] in messages:
                        status(messages[marker[0]])
                    if line.strip() == b"NITROGEN_FOCUS_TIMEOUT":
                        reason = "focus_timeout"
                    if line.strip() == b"NITROGEN_BIND_TIMEOUT":
                        reason = "controller_setup_timeout"
                    if on_round and marker and marker[0] == b"NITROGEN_ROUND":
                        on_round(json.loads(marker[1]))

        try:
            status("正在加载 NitroGen CUDA 权重")
            deadline = time.monotonic() + 300
            ready = f"Server running on port {port}".encode()
            while ready not in server_log.read_bytes():
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"NitroGen 推理服务启动失败，请查看 {server_log}")
                stop.sleep(.25)
            stop.raise_if_requested()
            player = subprocess.Popen(
                [*worker, "play", str(upstream), str(port), process, str(seconds),
                 str(stop_file), str(os.getpid())],
                cwd=root, stdout=player_output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                env={**env, "TRACE2TASK_NITROGEN_TASK_TEXT": task_text},
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            status("NitroGen 已启动官方游戏执行器，F9 可停止")
            while player.poll() is None:
                read_player_log()
                if server.poll() is not None:
                    raise RuntimeError(f"NitroGen 推理服务退出，请查看 {server_log}")
                stop.sleep(.1)
            read_player_log()
            if player.returncode != 0:
                raise RuntimeError(f"NitroGen 游戏执行器退出，请查看 {player_log}")
            return reason
        finally:
            stop_file.touch()
            if server.poll() is None:
                server.terminate()
            try:
                server.wait(timeout=10)
            finally:
                if player is not None:
                    player.wait(timeout=40)
                    read_player_log()


class GameplayEnded(Exception):
    pass


class FocusInterrupted(Exception):
    pass


def _play_source(source):
    """Add recovery at the pinned upstream rollout boundary."""
    start = "            while True:\n"
    end = "                step_count += 1\n"
    action_log = '                with open(PATH_ACTIONS, "a") as f:\n'
    isaac = 'if args.process == "isaac-ng.exe":\n'
    cuphead = 'if args.process == "Cuphead.exe":\n'
    if any(source.count(anchor) != 1 for anchor in (start, end, action_log, isaac, cuphead)):
        raise RuntimeError("Unexpected upstream NitroGen gameplay loop")
    before_setup, setup = source.split(isaac)
    _, after_setup = setup.split(cuphead)
    source = (before_setup + 'if args.process.casefold() == "isaac-ng.exe":\n'
              + "    env.bind_isaac_controller()\n\n" + cuphead + after_setup)
    before, body = source.split(start)
    body, after = body.split(end)
    body = body.replace(action_log, "                env.finish_action_batch(obs)\n" + action_log)
    wrapped = "".join("    " + line if line.strip() else line for line in (body + end).splitlines(keepends=True))
    return (before + start + "                try:\n                    env.ensure_active()\n" + wrapped
            + "                except FocusInterrupted:\n"
            + "                    obs = env.resume_observation()\n" + after)


class RealtimeClock:
    """Implement GamepadEnv's clock interface without changing game speed."""

    def set_speed(self, _speed):
        pass


def _play(upstream, port, process, seconds, stop_file, parent, task_text=""):
    import psutil
    import win32api
    import win32gui
    import win32process
    from nitrogen import game_env, inference_client
    from nitrogen.shared import BUTTON_ACTION_TOKENS
    from xspeedhack.client import RESOURCES

    targets = [item for item in psutil.process_iter(["name"])
               if (item.info["name"] or "").casefold() == process.casefold()]
    if len(targets) != 1:
        raise RuntimeError("请先打开一个且仅一个指定的 Windows 游戏进程")
    target = targets[0]
    environment = None
    deadline = time.monotonic() + (180 if process.casefold() == "isaac-ng.exe" else 60)
    started_input = False
    focus_interrupted = False
    official_clock = game_env.xsh.Client
    round_entry = None
    round_index = 0
    delivered = []
    overlay = make_task_overlay(task_text) if task_text else None

    def publish_round():
        print("NITROGEN_ROUND " + json.dumps(round_entry, ensure_ascii=True), flush=True)

    def finish_round(status, observation=None):
        nonlocal round_entry
        if round_entry is None:
            return
        execution = {"status": status, "executed": bool(delivered), "submitted_steps": len(delivered),
                     "actions": delivered,
                     "effect": "unverified"}
        if observation is not None:
            path = Path.cwd() / "round-inputs" / f"{round_entry['step_index']:05d}-after.png"
            observation.resize((256, 256)).save(path)
            execution["after_screenshot"] = str(path)
        round_entry["execution"] = execution
        publish_round()
        round_entry = None

    def focused():
        hwnd = win32gui.GetForegroundWindow()
        return bool(hwnd and win32process.GetWindowThreadProcessId(hwnd)[1] == target.pid)

    def check(*, require_focus=True):
        if stop_file.exists() or not psutil.pid_exists(parent) or time.monotonic() >= deadline:
            raise GameplayEnded
        if not target.is_running():
            raise RuntimeError("游戏进程已退出")
        if require_focus and environment is not None and (focus_interrupted or not focused()):
            if isinstance(getattr(environment, "speedhack_client", None), RealtimeClock):
                raise FocusInterrupted
            raise RuntimeError("游戏已失去前台焦点，停止手柄输入")

    class InterruptibleSocket:
        def __init__(self, socket):
            self.socket = socket

        def __getattr__(self, name):
            return getattr(self.socket, name)

        def recv(self):
            nonlocal focus_interrupted
            timeout = time.monotonic() + 30
            while time.monotonic() < timeout:
                if environment is not None and isinstance(environment.speedhack_client, RealtimeClock):
                    check(require_focus=False)
                    focus_interrupted |= not focused()
                else:
                    check()
                if self.socket.poll(100):
                    return self.socket.recv()
            raise TimeoutError("NitroGen 推理服务超过 30 秒未响应")

    class ScopedModelClient(inference_client.ModelClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.socket = InterruptibleSocket(self.socket)

        def predict(self, image):
            nonlocal round_entry, round_index, delivered
            if overlay is not None:
                image = image.copy()
                image.paste(overlay, ((image.width - overlay.width) // 2, 4), overlay)
            path = Path.cwd() / "round-inputs" / f"{round_index:05d}.png"
            path.parent.mkdir(exist_ok=True)
            image.save(path)
            round_entry = {
                "step_index": round_index, "status": "pending", "actual_model": "nvidia/NitroGen",
                "screenshot": str(path), "input": {
                    "messages": [{"role": "user", "content": [{"type": "localImage", "path": str(path)}]}],
                    "configuration": {"backend": "NitroGen PyTorch", "image_size": list(image.size)},
                },
            }
            if task_text:
                round_entry["input"]["configuration"]["screenshot_overlay_text"] = task_text
            round_index += 1
            delivered = []
            publish_round()
            started = time.perf_counter()
            try:
                prediction = super().predict(image)
            except GameplayEnded:
                round_entry["status"] = "cancelled"
                raise
            except Exception as error:
                round_entry.update(status="error", error=str(error))
                raise
            round_entry.update(status="predicted", model_roundtrip_ms=round((time.perf_counter() - started) * 1000, 2),
                               button_order=list(BUTTON_ACTION_TOKENS),
                               raw_output=json.dumps({key: value.tolist() for key, value in prediction.items()}, indent=2))
            publish_round()
            return prediction

    class WindowCapture(game_env.DxcamScreenshotBackend):
        def __init__(self, bbox, fps):
            # GamepadEnv passes x/y/width/height. DXcam expects corner coordinates.
            left, top, width, height = bbox
            super().__init__((left, top, left + width, top + height), fps)
            self.bbox = bbox

    def open_game_clock(*, process_id, arch):
        check(require_focus=False)
        if process_id != target.pid:
            raise RuntimeError("NitroGen 游戏进程在启动期间发生变化")
        if not focused():
            print("NITROGEN_WAIT_FOCUS", flush=True)
            focus_deadline = time.monotonic() + 20
            while not focused():
                check(require_focus=False)
                if time.monotonic() >= focus_deadline:
                    raise RuntimeError("游戏未获得前台焦点，请切到游戏后再试")
                time.sleep(.1)
        missing = [path.name for path in RESOURCES[arch].values() if not path.is_file()]
        if missing:
            print(f"NITROGEN_REALTIME {arch}: {', '.join(missing)}", flush=True)
            return RealtimeClock()
        return official_clock(process_id=process_id, arch=arch)

    def before_input():
        nonlocal deadline, started_input
        check()
        if not started_input:
            deadline = time.monotonic() + int(seconds)
            started_input = True

    class ScopedGamepadEnv(game_env.GamepadEnv):
        def __init__(self, **kwargs):
            nonlocal environment
            check()
            environment = self
            super().__init__(**kwargs)
            check()
            self.setup_press_button = self.gamepad_emulator.press_button

            def guarded_press(button):
                before_input()
                return self.setup_press_button(button)

            self.gamepad_emulator.press_button = guarded_press

        def perform_action(self, action, duration):
            before_input()
            result = super().perform_action(action, duration)
            if round_entry is not None:
                delivered.append({key: value.tolist() if hasattr(value, "tolist") else value
                                  for key, value in action.items()})
            return result

        def finish_action_batch(self, observation):
            # Keep consecutive joystick and button states continuous across frames.
            # Release before inference so the last prediction cannot hold input.
            if isinstance(self.speedhack_client, RealtimeClock):
                self.gamepad_emulator.reset()
            finish_round("delivered", observation)

        def reset(self, *args, **kwargs):
            before_input()
            return super().reset(*args, **kwargs)

        def ensure_active(self):
            check()

        def bind_isaac_controller(self):
            # Keep this exact controller alive from menu navigation into rollout.
            pad = self.gamepad_emulator
            keys = {0x74: "DPAD_DOWN", 0x75: "SOUTH", 0x76: "EAST", 0x77: "test", 0x79: "confirm"}
            pressed = set()
            tested = False
            print("NITROGEN_BIND_CONTROLLER", flush=True)
            try:
                while time.monotonic() < deadline:
                    check(require_focus=False)
                    down = {key for key in keys if win32api.GetAsyncKeyState(key) & 0x8000}
                    new = down - pressed
                    pressed = down
                    if focused() and len(new) == 1:
                        action = keys[new.pop()]
                        if action == "confirm":
                            if tested:
                                return
                        else:
                            test = action == "test"
                            try:
                                if test:
                                    pad.set_joystick("AXIS_LEFTX", 30397)
                                else:
                                    self.setup_press_button(action)
                                pad.gamepad.update()
                                until = time.monotonic() + (1.0 if test else .12)
                                while focused() and time.monotonic() < until:
                                    check(require_focus=False)
                                    time.sleep(.01)
                                tested |= test and focused()
                            finally:
                                pad.reset()
                    time.sleep(.02)
            finally:
                pad.reset()
            print("NITROGEN_BIND_TIMEOUT", flush=True)
            raise GameplayEnded

        def resume_observation(self):
            nonlocal focus_interrupted
            self.gamepad_emulator.reset()
            finish_round("focus_interrupted")
            print("NITROGEN_FOCUS_PAUSED", flush=True)
            resume_by = time.monotonic() + 20
            while not focused():
                check(require_focus=False)
                if time.monotonic() >= resume_by:
                    print("NITROGEN_FOCUS_TIMEOUT", flush=True)
                    raise GameplayEnded
                time.sleep(.1)
            check(require_focus=False)
            focus_interrupted = False
            print("NITROGEN_FOCUS_RESUMED", flush=True)
            return self.render()

    game_env.GamepadEnv = ScopedGamepadEnv
    game_env.DxcamScreenshotBackend = WindowCapture
    game_env.xsh.Client = open_game_clock
    inference_client.ModelClient = ScopedModelClient
    # Preserve upstream preprocessing, policy requests, action mapping and timing.
    script = upstream / "scripts/play.py"
    sys.argv = [str(script), "--process", process, "--port", port]
    sys.stdin = io.StringIO("\n")
    try:
        exec(compile(_play_source(script.read_text(encoding="utf-8")), str(script), "exec"),  # noqa: S102 - pinned upstream entry point
             {"__name__": "__main__", "__file__": str(script), "FocusInterrupted": FocusInterrupted})
    except GameplayEnded:
        pass
    except FocusInterrupted:
        print("NITROGEN_FOCUS_TIMEOUT", flush=True)
    finally:
        finish_round("interrupted")
        if environment is not None:
            try:
                if hasattr(environment, "gamepad_emulator"):
                    environment.gamepad_emulator.reset()
            finally:
                try:
                    if hasattr(environment, "speedhack_client"):
                        environment.unpause()
                finally:
                    capture = getattr(environment, "screenshot_backend", None)
                    if capture is not None:
                        capture.camera.release()
                    environment.close()


def _main():
    mode, root, port, *args = sys.argv[1:]
    upstream = Path(root)
    from nitrogen import shared
    # Keep upstream recording and per-frame artifacts out of the code checkout.
    if mode == "play":
        game, seconds, stop_file, parent = args
        # play.py reads PATH_REPO for its output only. The server keeps its own.
        shared.PATH_REPO = Path.cwd()
        task_text = validate_task_text(os.environ.get("TRACE2TASK_NITROGEN_TASK_TEXT", ""))
        _play(upstream, port, game, seconds, Path(stop_file), int(parent), task_text)
    elif mode == "serve":
        import threading

        checkpoint, parent = args
        script = upstream / "scripts/serve.py"
        source = script.read_text(encoding="utf-8")
        bind = 'socket.bind(f"tcp://*:{args.port}")'
        if source.count(bind) != 1:
            raise RuntimeError("Unexpected upstream NitroGen listener")
        sys.argv = [str(script), checkpoint, "--port", port]
        sys.stdin = io.StringIO("\n")

        def watch_parent():
            import psutil
            while psutil.pid_exists(int(parent)):
                time.sleep(.5)
            os._exit(1)

        threading.Thread(target=watch_parent, daemon=True).start()
        exec(compile(source.replace(bind, 'socket.bind(f"tcp://127.0.0.1:{args.port}")'),  # noqa: S102 - pinned upstream entry point
                     str(script), "exec"), {"__name__": "__main__", "__file__": str(script)})
    else:
        raise ValueError("Unknown NitroGen worker")


if __name__ == "__main__":
    _main()
