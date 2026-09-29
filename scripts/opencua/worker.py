"""Isolated host for pinned, unmodified AgentNetTool input/A11y functions.

No model calls, remote uploads, audio, or automatic task success.
The upstream Qt Recorder lifecycle/OBS profile mutation is deliberately NOT used.
"""
from __future__ import annotations

import argparse
import configparser
import ctypes
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

REVISION = "05068e93ea8110694e96d62d6c6d485bb4e958f0"


def validate_capture_sources(inputs):
    """Fail closed if the dedicated OBS collection could include audio or other sources."""
    for item in inputs:
        if item.get("inputKind") != "monitor_capture":
            raise RuntimeError("Unexpected OBS source; refusing capture")


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def upstream(root):
    root = Path(root)
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).strip()
    if revision != REVISION:
        raise RuntimeError("AgentNetTool revision differs from the reviewed revision")
    # Imported source must also match that revision, not just the git HEAD.
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no",
         "--", "agentnet-annotator/api"], text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if dirty.strip():
        raise RuntimeError("AgentNetTool core has local modifications")
    sys.path.insert(0, str(root / "agentnet-annotator" / "api"))
    from core.recorder import Recorder
    return Recorder


class OBS:
    def __init__(self, root, run, *, frame_clock=False):
        self.root, self.run = Path(root), Path(run)
        self.frame_clock = frame_clock
        self.process = self.client = None
        self.event_client = None
        self.record_state_events = {}
        self.stopped_event = threading.Event()
        self.recording = False

    def start(self):
        import obsws_python as obs
        import psutil
        exe = self.root / "bin/64bit/obs64.exe"
        # Never take over an OBS instance, even one using this portable directory.
        for process in psutil.process_iter(["exe"]):
            if process.info["exe"] and Path(process.info["exe"]).resolve() == exe.resolve():
                raise RuntimeError("Trace2Task OBS is already running; close that instance first")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        password = secrets.token_urlsafe(32)
        user_config = self.root / "config/obs-studio/user.ini"
        preferences = configparser.ConfigParser(interpolation=None, strict=False)
        preferences.optionxform = str
        preferences.read(user_config, encoding="utf-8-sig")
        if not preferences.has_section("General"):
            preferences.add_section("General")
        # OBS schedules its auto-configuration wizard when FirstRun is false.
        preferences.set("General", "FirstRun", "true")
        if not preferences.has_section("Basic"):
            preferences.add_section("Basic")
        preferences.set("Basic", "Profile", "Trace2Task")
        preferences.set("Basic", "ProfileDir", "Trace2Task")
        user_config.parent.mkdir(parents=True, exist_ok=True)
        with user_config.open("w", encoding="utf-8") as stream:
            preferences.write(stream, space_around_delimiters=False)
        profile = self.root / "config/obs-studio/basic/profiles/Trace2Task/basic.ini"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            "[General]\nName=Trace2Task\n[Output]\nMode=Simple\n"
            "[SimpleOutput]\nFilePath=" + self.run.as_posix() + "\nRecFormat2=mp4\n"
            "RecQuality=Small\nRecEncoder=nvenc\nStreamEncoder=nvenc\n"
            "[Video]\nBaseCX=1920\nBaseCY=1080\nOutputCX=1920\nOutputCY=1080\n"
            "FPSType=0\nFPSCommon=30\n", encoding="utf-8")
        config = self.root / "config/obs-studio/plugin_config/obs-websocket/config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        save(config, {"server_enabled": True, "server_port": port, "auth_required": True,
                          "server_password": password, "alerts_enabled": False, "first_load": False})
        # Dedicated collection: no microphone or desktop-audio sources.
        scenes = self.root / "config/obs-studio/basic/scenes/Trace2Task.json"
        scenes.parent.mkdir(parents=True, exist_ok=True)
        save(scenes, {"name": "Trace2Task", "current_scene": "Trace2Task",
                      "current_program_scene": "Trace2Task", "sources": [],
                      "scene_order": [], "DesktopAudioDevice1": None,
                      "DesktopAudioDevice2": None, "AuxAudioDevice1": None,
                      "AuxAudioDevice2": None, "AuxAudioDevice3": None,
                      "AuxAudioDevice4": None})
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        environment = os.environ.copy()
        environment.pop("TRACE2TASK_OBS_FRAME_CLOCK", None)
        if self.frame_clock:
            plugin = self.root / "obs-plugins/64bit/trace2task-frame-clock.dll"
            if not plugin.is_file():
                raise RuntimeError("OBS frame-clock plugin is not installed")
            environment["TRACE2TASK_OBS_FRAME_CLOCK"] = str(self.run.resolve() / "obs-frame-clock.jsonl")
        self.process = subprocess.Popen(
            [str(exe), "--portable", "--multi", "--minimize-to-tray", "--disable-updater",
             "--disable-missing-files-check", "--disable-shutdown-check",
             "--collection", "Trace2Task", "--profile", "Trace2Task"],
            cwd=exe.parent, startupinfo=startup, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("OBS exited during startup")
            try:
                self.client = obs.ReqClient(host="127.0.0.1", port=port, password=password, timeout=10)
                self.client.get_record_status()
                break
            except Exception:  # noqa: BLE001 -- bounded startup retries across OBS transport errors
                if self.client:
                    self.client.disconnect()
                    self.client = None
                time.sleep(0.3)
        if self.client is None:
            raise RuntimeError("OBS WebSocket startup timed out")
        if self.client.get_record_status().output_active:
            raise RuntimeError("Unexpected active OBS recording; refusing takeover")
        scene_names = [scene["sceneName"] for scene in self.client.get_scene_list().scenes]
        if "Trace2Task" not in scene_names:
            self.client.create_scene("Trace2Task")
        self.client.set_current_program_scene("Trace2Task")
        # OBS primary display capture. The source supplies its native dimensions.
        self.client.create_input("Trace2Task", "Trace2Task Display", "monitor_capture",
                                 {"monitor": 0, "capture_cursor": True}, True)
        monitors = self.client.get_input_properties_list_property_items("Trace2Task Display", "monitor_id").property_items
        monitors = [item for item in monitors if item["itemValue"] != "DUMMY" and item.get("itemEnabled", True)]
        primary = next((item for item in monitors if " @ 0,0" in item["itemName"]), None)
        if primary is None and len(monitors) == 1:
            primary = monitors[0]
        if primary is None:
            raise RuntimeError("Cannot identify the primary OBS monitor; refusing a mismatched capture")
        self.client.set_input_settings("Trace2Task Display", {"monitor_id": primary["itemValue"]}, True)
        settings = self.client.get_input_settings("Trace2Task Display").input_settings
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        self.client.set_video_settings(base_width=width, base_height=height,
                                       out_width=width, out_height=height,
                                       numerator=30, denominator=1)
        self.client.set_profile_parameter("Output", "Mode", "Simple")
        self.client.set_profile_parameter("SimpleOutput", "FilePath", str(self.run))
        self.client.set_profile_parameter("SimpleOutput", "RecFormat2", "mp4")
        self.client.set_profile_parameter("SimpleOutput", "RecQuality", "Small")
        validate_capture_sources(self.client.get_input_list().inputs)
        self.event_client = obs.EventClient(host="127.0.0.1", port=port, password=password, timeout=10)
        def on_record_state_changed(data):
            self.record_state_events.setdefault(data.output_state, []).append(time.perf_counter())
            if data.output_state == "OBS_WEBSOCKET_OUTPUT_STOPPED":
                self.stopped_event.set()
        self.event_client.callback.register(on_record_state_changed)
        # Allow display-capture initialization before starting the recording.
        time.sleep(0.5)
        self.before_start = time.perf_counter()
        self.client.start_record()
        for _ in range(30):
            if self.client.get_record_status().output_active:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("OBS did not enter recording state")
        self.recording = True
        self.after_start = time.perf_counter()
        return {"width": width, "height": height, "fps": 30, "input_settings": settings,
                "start_request_at": self.before_start, "start_ack_at": self.after_start}

    def close(self):
        video = None
        try:
            if self.recording:
                video = self.client.stop_record().output_path
                self.stopped_event.wait(3)
                if self.frame_clock:
                    # Frontend stop and WebSocket stop may be delivered in either order.
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        clock_path = self.run / "obs-frame-clock.jsonl"
                        try:
                            if '"type":"footer"' in clock_path.read_text(encoding="utf-8")[-512:]:
                                break
                        except (FileNotFoundError, PermissionError):
                            pass
                        time.sleep(.05)
                self.recording = False
        finally:
            try:
                if self.client:
                    self.client.disconnect()
                if self.event_client:
                    self.event_client.disconnect()
            finally:
                if self.process and self.process.poll() is None:
                    # Only the child we started; never kill unrelated OBS processes.
                    self.process.terminate()
                    try:
                        self.process.wait(8)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(5)
        return video


def record(args):
    from pynput import keyboard, mouse
    ctypes.windll.user32.SetProcessDPIAware()
    run = Path(args.output).resolve()
    run.mkdir(parents=True, exist_ok=False)
    # Upstream logger writes runtime.log relative to cwd; keep it in this archive.
    os.chdir(run)
    Recorder = upstream(args.upstream)
    from contextlib import ExitStack

    from browser_ingest import BrowserIngress
    from core.a11y_listener import A11yListener
    from core.axtree_getter import KeyFrameDetector
    from core.metadata import MetadataManager
    from core.utils import init_encrpted_jsonl, write_encrypt_line
    from services.file_service import FileService
    stop = threading.Event()
    events = queue.Queue()
    reason = ["cancelled"]
    stop_clock = [None]
    # Use the actual upstream callback methods. No polling/reimplementation of
    # the native raw input schema (move/click/scroll/press/release).
    class Sink:
        _is_paused = False
        event_queue = events
        on_move = Recorder.on_move
        on_click = Recorder.on_click
        on_scroll = Recorder.on_scroll
        on_press = Recorder.on_press
        on_release = Recorder.on_release
    sink = Sink()

    def press(key):
        if key in (keyboard.Key.f8, keyboard.Key.f9):
            stop_clock[0] = time.perf_counter()
            reason[0] = "success_marked" if key == keyboard.Key.f8 else "cancelled"
            stop.set()
            return
        sink.on_press(key)

    def release(key):
        if key not in (keyboard.Key.f8, keyboard.Key.f9):
            sink.on_release(key)

    class Notifications:
        def emit(self, *_):
            pass  # No socket frontend required for the official detector.

    a11y = A11yListener(True, True)
    detector = KeyFrameDetector(Notifications())
    official_metadata = MetadataManager(str(run), natural_scrolling=False)
    browser_service = FileService()
    browser_service.set_active_recording(str(run))
    browser = None
    collectors_started = False

    # New recordings use the reviewed OBS CTS route; diagnostics may still
    # construct OBS(frame_clock=False) directly. No DXGI side capture is started.
    obs = OBS(args.obs, run, frame_clock=True)
    mouse_listener = key_listener = None
    metadata = {"source": "opencua_native", "task_id": args.task,
                "upstream_revision": REVISION, "started_at": datetime.now(UTC).isoformat(),
                "execution_scope": "desktop", "audio": False, "success": False,
                "compilation_supported": False, "capture_status": "starting"}
    metadata["archive_schema"] = "agentnet-official-v1"
    if obs.frame_clock:
        metadata["frame_clock"] = {"path": "obs-frame-clock.jsonl", "purpose": "approximate_alignment",
                                   "used_for_frame_selection": True, "timestamp_kind": "composition",
                                   "alignment": "obs_cts_approximate", "strict_prestate_guaranteed": False}
    save(run / "trace2task.json", metadata)
    video = None
    try:
        for name in ("a11y.jsonl", "element.jsonl", "top_window.jsonl", "html.jsonl"):
            init_encrpted_jsonl(run / name)
        browser = BrowserIngress(browser_service)
        browser.start()
        official_metadata.collect()
        metadata["video_clock"] = obs.start()
        official_metadata.set_video_start_timestamp(time.perf_counter())
        official_metadata.save_metadata()
        mouse_listener = mouse.Listener(on_move=sink.on_move, on_click=sink.on_click, on_scroll=sink.on_scroll)
        key_listener = keyboard.Listener(on_press=press, on_release=release)
        mouse_listener.start()
        key_listener.start()
        a11y.start()
        detector.start()
        collectors_started = True
        browser.active = True
        metadata["capture_status"] = "recording"
        metadata["browser"] = browser.receipt()
        save(run / "trace2task.json", metadata)
        print("TRACE2TASK_READY", flush=True)
        started = time.monotonic()
        count = 0
        with ExitStack() as stack:
            event_file = stack.enter_context((run / "events.jsonl").open("w", encoding="utf-8"))
            streams = [(detector.axtree_queue, stack.enter_context((run / "a11y.jsonl").open("a", encoding="utf-8"))),
                       (a11y.element_queue, stack.enter_context((run / "element.jsonl").open("a", encoding="utf-8"))),
                       (a11y.top_window_queue, stack.enter_context((run / "top_window.jsonl").open("a", encoding="utf-8")))]
            while True:
                if Path(args.stop_file).exists():
                    stop.set()
                if time.monotonic() - started >= args.max_seconds:
                    reason[0] = "timeout"
                    stop.set()
                if stop.is_set():
                    if stop_clock[0] is None:
                        stop_clock[0] = time.perf_counter()
                    mouse_listener.stop()
                    key_listener.stop()
                    mouse_listener.join(timeout=1)
                    key_listener.join(timeout=1)
                    browser.active = False
                    a11y.stop()
                    detector.stop()
                    collectors_started = False
                while not events.empty():
                    event = events.get_nowait()
                    event["event_idx"] = count
                    count += 1
                    event_file.write(json.dumps(event, ensure_ascii=False) + "\n")
                for pending, stream in streams:
                    while not pending.empty():
                        write_encrypt_line(stream, pending.get_nowait())
                    stream.flush()
                event_file.flush()
                if stop.is_set():
                    break
                time.sleep(0.02)
        metadata.update(input_event_count=count, stop_reason=reason[0], capture_status="finished",
                        success=reason[0] == "success_marked", stop_perf_counter=stop_clock[0])
    except Exception as error:
        metadata.update(capture_status="failed", stop_reason="error", error=str(error))
        raise
    finally:
        stop.set()
        if mouse_listener:
            mouse_listener.stop()
        if key_listener:
            key_listener.stop()
        if collectors_started:
            a11y.stop()
            detector.stop()
        if browser:
            browser.close()
            metadata["browser"] = browser.receipt()
        try:
            video = obs.close()
            if not video or not Path(video).is_file() or Path(video).stat().st_size == 0:
                raise RuntimeError("OBS did not finalize a nonempty video")
            destination = run / "video.mp4"
            if Path(video).resolve() != destination:
                Path(video).rename(destination)
            metadata["video_path"] = str(destination)
        except Exception as error:  # noqa: BLE001 -- persist finalization failures for UI review
            metadata.update(capture_status="failed", success=False, finalization_error=str(error))
        metadata["finished_at"] = datetime.now(UTC).isoformat()
        official_metadata.end_collect()
        official_metadata.add_obs_record_state_timings(obs.record_state_events)
        official_metadata.save_metadata()
        save(run / "trace2task.json", metadata)
    if metadata["capture_status"] == "failed":
        raise RuntimeError(metadata.get("finalization_error", "Recording failed"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--obs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--max-seconds", type=float, default=1800)
    parser.add_argument("--frame-clock", action="store_true", help="Compatibility flag; CTS capture is now enabled for recordings")
    record(parser.parse_args())
