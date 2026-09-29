"""Primary-display Cua observation and execution using native desktop targets."""
from PIL import Image

from trace2task.execution_protocol import ActionUnavailable, ObservationStale

DESKTOP_TARGET = {"kind": "desktop", "display_id": "primary"}


class CuaDesktopObservation:
    def __init__(self, driver):
        self.driver = driver

    def observe(self, target, index):
        path = (self.driver.root / f"{index:04d}.png").resolve()
        state = self.driver.call("get_desktop_state", {
            "session": self.driver.root.name, "screenshot_out_file": str(path)})
        width, height = state.get("screen_width"), state.get("screen_height")
        if (state.get("display") != "primary" or type(width) is not int
                or type(height) is not int or min(width, height) <= 0):
            raise RuntimeError("Cua 未返回有效主屏截图尺寸")
        with Image.open(path) as image:
            if image.size != (width, height):
                raise RuntimeError("Cua 桌面截图与物理屏幕尺寸不一致")
        state["session"] = self.driver.root.name
        return dict(DESKTOP_TARGET), state, path

    @staticmethod
    def context(target, state, backend):
        return None


class CuaDesktopExecutionBackend:
    default_delivery_mode = "foreground"
    allow_foreground_retry = False

    def __init__(self, driver):
        self.driver = driver

    def authorize(self, action, target):
        if target != DESKTOP_TARGET:
            raise ValueError("Cua 桌面执行仅授权主显示器")

    def check_current(self, target, state):
        self.authorize(None, target)
        current = self.driver.call("get_screen_size", {})
        if (current.get("width"), current.get("height"), current.get("scale_factor")) != (
                state.get("screen_width"), state.get("screen_height"), state.get("scale_factor")):
            raise ObservationStale("主屏尺寸或缩放变化，请重新截图")

    def prepare(self, action, state, execution_context=None):
        args = action.args
        common = {"target": dict(DESKTOP_TARGET), "session": state["session"]}

        def point(x, y):
            return {"x": min(state["screen_width"] - 1, int(x * state["screen_width"])),
                    "y": min(state["screen_height"] - 1, int(y * state["screen_height"]))}

        if action.skill == "wait":
            return None
        if action.skill in {"click", "double_click", "move_cursor"}:
            payload = {**common, **point(args["x"], args["y"])}
            if action.skill != "move_cursor":
                payload.update(button=args["button"], count=2 if action.skill == "double_click" else 1)
            return ("move_cursor" if action.skill == "move_cursor" else "click"), payload
        if action.skill == "drag":
            start, end = point(args["start_x"], args["start_y"]), point(args["end_x"], args["end_y"])
            return "drag", {**common, "from_x": start["x"], "from_y": start["y"],
                            "to_x": end["x"], "to_y": end["y"],
                            "button": args["button"], "duration_ms": args["duration_ms"]}
        if action.skill in {"scroll", "type_text"}:
            payload = {**common, **args}
            if "x" in args:
                payload.update(point(args["x"], args["y"]))
            return action.skill, payload
        if action.skill in {"press_key", "hotkey"}:
            keys = args["keys"] if action.skill == "hotkey" else [args["key"]]
            if any(key not in {"ctrl", "alt", "shift", "win"} for key in keys[:-1]):
                raise ActionUnavailable("Cua 组合键需要修饰键加普通按键")
            key = {"enter": "return", "page_up": "pageup", "page_down": "pagedown"}.get(keys[-1], keys[-1])
            return "press_key", {**common, "key": key, "modifiers": keys[:-1]}
        raise ActionUnavailable(f"Cua 桌面不支持 {action.skill}；未发送输入")

    def request_view(self, action, prepared, target, *, delivery_mode=None):
        return {"backend": "cua", "operation": prepared[0] if prepared else "wait",
                "args": prepared[1] if prepared else action.args,
                "delivery_mode": "foreground" if prepared else "local"}

    def dispatch(self, action, prepared, target, stop):
        if prepared is None:
            stop.sleep(action.args["duration_ms"] / 1000)
            return {"effect": "confirmed", "route": "local_wait"}, target
        return self.driver.call(*prepared), target

    def retry_foreground(self, action, prepared, target, stop):
        raise ActionUnavailable("桌面输入已使用系统键鼠，不能重复发送被拒绝的动作")

    def capabilities(self, state):
        return {"available_skills": ["click", "double_click", "move_cursor", "drag", "scroll",
                                     "type_text", "press_key", "hotkey", "wait"],
                "coordinate_space": "primary_desktop_screenshot", "delivery_mode": "foreground"}
