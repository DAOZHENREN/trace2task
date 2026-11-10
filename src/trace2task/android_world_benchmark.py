"""Explicit AndroidWorld-only GUI routes, never enabled for a physical phone."""

from copy import deepcopy

SKILLS = {
    "click",
    "hold_mouse",
    "drag",
    "press_key",
    "wait",
    "android_text",
    "android_scroll",
    "android_open_app",
    "android_key",
}
SCOPE = "androidworld-owned-emulator-v1"


def validate_action(skill, args):
    fields = {
        "android_text": "text",
        "android_scroll": "direction",
        "android_open_app": "app_name",
        "android_key": "key",
    }
    field = fields[skill]
    if set(args) != {field} or not isinstance(args[field], str):
        raise ValueError("Invalid AndroidWorld GUI action")
    value = args[field]
    if skill == "android_scroll" and value not in {"up", "down", "left", "right"}:
        raise ValueError("Invalid scroll direction")
    if skill == "android_key" and value not in {"home", "menu"}:
        raise ValueError("Invalid Android key")
    if skill in {"android_text", "android_open_app"} and (
        not value or len(value) > 4096 or "\x00" in value
    ):
        raise ValueError("Invalid Android text/app name")
    return deepcopy(args)


def schema():
    from trace2task.chat_model_adapter import _action, plan_schema

    base = plan_schema(selected_windows=False)["properties"]["actions"]["items"]["anyOf"]
    actions = [
        a
        for a in base
        if a.get("properties", {}).get("skill", {}).get("enum", [None])[0]
        in {"click", "hold_mouse", "drag", "press_key", "wait"}
    ]
    for a in actions:
        props = a["properties"]["args"]["properties"]
        if "button" in props:
            props["button"] = {"type": "string", "enum": ["left"]}
        if a["properties"]["skill"]["enum"] == ["press_key"]:
            props["key"] = {"type": "string", "enum": ["escape", "enter"]}
    for skill, field, values in [
        ("android_text", "text", None),
        ("android_open_app", "app_name", None),
        ("android_scroll", "direction", ["up", "down", "left", "right"]),
        ("android_key", "key", ["home", "menu"]),
    ]:
        prop = (
            {"type": "string", "enum": values}
            if values
            else {"type": "string", "minLength": 1, "maxLength": 4096}
        )
        actions.append(_action(skill, {field: prop}, [field]))
    return {"type": "array", "maxItems": 12, "items": {"anyOf": actions}}


def native_plan(native):
    from trace2task.execution_protocol import ActionPlan
    from trace2task.mai_swift_hybrid import action_plan

    name = native["action"]
    if name.startswith("scroll_"):
        skill, args = "android_scroll", {"direction": name[7:]}
    elif name == "type_text":
        skill, args = "android_text", {"text": native["text"]}
    elif name == "open_app":
        skill, args = "android_open_app", {"app_name": native["text"]}
    elif name in {"home", "menu"}:
        skill, args = "android_key", {"key": name}
    else:
        return action_plan(native)
    return ActionPlan.from_prediction({"actions": [{"skill": skill, "args": args}]}).to_payload()


def validate_gui(payload, *, scope):
    """Pure preflight; no screenshot, device call, or side effect."""
    from android_world.env import adb_utils
    from trace2task.execution_protocol import UnifiedAction
    import re
    if scope != SCOPE:
        raise ValueError('AndroidWorld scope required')
    action = UnifiedAction.from_payload(payload)
    if action.skill not in SKILLS:
        raise ValueError('Unsupported benchmark GUI route')
    a = action.args
    if action.skill == 'android_open_app':
        name = a['app_name']
        if (adb_utils.get_adb_activity(name) is None and name not in adb_utils._DEFAULT_URIS
                and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', name) is None):
            raise ValueError('Unknown Android app label or invalid package name: ' + name)
    if action.skill == 'press_key' and a['key'] not in {'escape', 'enter'}:
        raise ValueError('Unsupported AndroidWorld key')
    if action.skill in {'click', 'hold_mouse', 'drag'} and a.get('button', 'left') != 'left':
        raise ValueError('Single touch only')
    return action


def execute_gui(env, payload, *, scope):
    """Reuse pinned official primitives; text never appends Enter or clears text."""
    from android_world.env import actuation, adb_utils, json_action

    action = validate_gui(payload, scope=scope)
    a = action.args
    raw = env.controller.env
    state = env.get_state(wait_to_stabilize=False)
    height, width = state.pixels.shape[:2]

    def point(x, y):
        return min(width - 1, int(x * width)), min(height - 1, int(y * height))

    if action.skill == "android_text":
        adb_utils.type_text(a["text"], raw, timeout_sec=10)
    elif action.skill == "android_open_app":
        import re

        name = a["app_name"]
        # Upstream falls back to `adb shell monkey -p`; do not pass arbitrary
        # model text to that fallback. Known labels resolve to fixed activities.
        if (
            adb_utils.get_adb_activity(name) is None
            and name not in adb_utils._DEFAULT_URIS
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", name) is None
        ):
            raise ValueError("Unknown Android app label or invalid package name")
        adb_utils.launch_app(name, raw)
    elif action.skill == "android_scroll":
        actuation.execute_adb_action(
            json_action.JSONAction(action_type="scroll", direction=a["direction"]),
            [],
            (width, height),
            raw,
        )
    elif action.skill == "android_key":
        if a["key"] == "home":
            adb_utils.press_home_button(raw)
        else:
            adb_utils.press_keyboard_generic("KEYCODE_MENU", raw)
    elif action.skill == "press_key":
        if a["key"] == "escape":
            adb_utils.press_back_button(raw)
        elif a["key"] == "enter":
            adb_utils.press_enter_button(raw)
        else:
            raise ValueError("Unsupported AndroidWorld key")
    elif action.skill in {"click", "hold_mouse"}:
        if a.get("button", "left") != "left":
            raise ValueError("Single touch only")
        x, y = point(a["x"], a["y"])
        if action.skill == "click":
            adb_utils.tap_screen(x, y, raw)
        else:
            adb_utils.issue_generic_request(
                adb_utils.generate_swipe_command(x, y, x, y, a["duration_ms"]), raw
            )
    elif action.skill == "drag":
        if a.get("button", "left") != "left":
            raise ValueError("Single touch only")
        x, y = point(a["start_x"], a["start_y"])
        x2, y2 = point(a["end_x"], a["end_y"])
        adb_utils.issue_generic_request(
            adb_utils.generate_swipe_command(x, y, x2, y2, a["duration_ms"]), raw
        )
    elif action.skill == "wait":
        import time

        time.sleep(a["duration_ms"] / 1000)
    return {"effect": "unverifiable", "scope": SCOPE, "executed": True}


class WorldClient:
    """Loopback-only client for the owned emulator service, no proxy or retries."""

    def __init__(self, port, token):
        from urllib.request import ProxyHandler, build_opener

        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid service port")
        self.base = f"http://127.0.0.1:{port}"
        self.token = token
        self.opener = build_opener(ProxyHandler({}))

    def screenshot(self, *, wait_to_stabilize=True, expected_instance=None):
        import io
        import hashlib
        import numpy as np
        from PIL import Image
        from urllib.request import Request
        suffix = 'true' if wait_to_stabilize else 'false'
        req = Request(self.base + '/trace2task/screenshot.png?wait_to_stabilize=' + suffix,
                      headers={'X-Trace2Task-Token': self.token})
        with self.opener.open(req, timeout=180) as response:
            if response.headers.get_content_type() != 'image/png':
                raise ValueError('Expected PNG screenshot response')
            if expected_instance and response.headers.get('X-Screenshot-Instance') != expected_instance:
                raise ValueError('Screenshot emulator instance changed')
            digest = response.headers.get('X-Pixels-SHA256')
            data = response.read(32_000_001)
        if len(data) > 32_000_000:
            raise ValueError('Screenshot response too large')
        with Image.open(io.BytesIO(data)) as image:
            if image.mode != 'RGB' or image.width * image.height > 32_000_000:
                raise ValueError('Invalid screenshot dimensions or mode')
            pixels = np.asarray(image).copy()
        if hashlib.sha256(pixels.tobytes()).hexdigest() != digest:
            raise ValueError('Decoded screenshot pixel hash mismatch')
        return pixels

    def request(self, path, *, data=None):
        import json
        from urllib.request import Request

        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Invalid service path")
        req = Request(
            self.base + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "X-Trace2Task-Token": self.token},
        )
        from urllib.error import HTTPError
        try:
            with self.opener.open(req, timeout=180) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read(8192).decode("utf-8", errors="replace")
            raise RuntimeError(f"Service {path} HTTP {error.code}: {detail}") from error


class WorldBackend:
    default_delivery_mode = "foreground"
    allow_foreground_retry = False

    def __init__(self, client, instance):
        self.client, self.instance = client, instance

    def authorize(self, action, target):
        if (
            target != {"kind": "androidworld", "instance": self.instance}
            or action.skill not in SKILLS
        ):
            raise ValueError("Action outside the owned AndroidWorld emulator scope")

    def prepare(self, action, state, execution_context=None):
        return action.to_payload()

    def check_current(self, target, state):
        if state.get("instance") != self.instance or target.get("instance") != self.instance:
            raise ValueError("AndroidWorld instance mismatch")

    def dispatch(self, action, prepared, target, stop):
        stop.raise_if_requested()
        reply = self.client.request(
            "/trace2task/action", data={"instance": self.instance, "action": prepared}
        )
        return reply, target

    def retry_foreground(self, *args, **kwargs):
        raise RuntimeError("AndroidWorld actions are never automatically retried")

    def capabilities(self, state):
        return {"available_skills": sorted(SKILLS), "scope": SCOPE}
