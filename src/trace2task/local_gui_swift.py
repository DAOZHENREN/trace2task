"""Mainline adapter to the hash-verified published Desktop131 causal reader."""

import importlib.util
import hashlib
import shutil
from collections import OrderedDict
import json
import os
from pathlib import Path

MODEL = "gui-swift-4b"
BACKEND = "gui-swift-native131"
SYSTEM = "You are a desktop GUI agent. Choose the next action from the task, screenshots, past observations and executed actions. Historical x/y and predicted x/y use the SAME official 256-bin token codebook: one token per axis, ordered from left/top to right/bottom. These tokens are bin labels, not decimal coordinates. Predict all 131 value slots together, including text. The executor converts predicted coordinate distributions into normalized 0..1000 coordinates. Use the <action>{JSON}</action> action structure with applicable parameters only. Actions: key(keys), type(text), mouse_move(coordinate), left_click(coordinate), left_click_drag(coordinate), right_click(coordinate), middle_click(coordinate), double_click(coordinate), triple_click(coordinate), scroll(pixels, optional coordinate), hscroll(pixels, optional coordinate), wait(time), terminate(status), answer(text), interact(text). Drag starts at the current pointer. Preserve wheel counts for scroll/hscroll. Time is seconds; status is success or failure. Text is exact, at most 99 native tokens plus EOS. Keys are a JSON array."


def deployment_paths():
    return [
        Path(os.environ.get("TRACE2TASK_SWIFT_" + key, "UNCONFIGURED-" + key))
        for key in ("RELEASE", "BASE", "UPSTREAM")
    ]


def required_paths():
    release, base, upstream = deployment_paths()
    return [
        release / "infer.py",
        release / "adapter_model.safetensors",
        release / "SHA256SUMS.json",
        base / "config.json",
        upstream / "slotread/train.py",
    ]


def history_native(raw):
    if "<tool_call>" in raw:
        return raw
    # Mainline director receipts use validated normalized unified actions.
    value = json.loads(raw[raw.index("{") :])
    from trace2task.execution_protocol import UnifiedAction

    action = UnifiedAction.from_payload(value)
    a = action.args
    def xy():
        return [round(a["x"] * 1000), round(a["y"] * 1000)]
    if action.skill == "double_click" and a["button"] != "left":
        raise ValueError("Desktop131 double-click supports left button only")
    if action.skill in {"click", "double_click", "move_cursor"}:
        name = (
            "mouse_move"
            if action.skill == "move_cursor"
            else "double_click"
            if action.skill == "double_click"
            else {"left": "left_click", "right": "right_click", "middle": "middle_click"}[
                a["button"]
            ]
        )
        native = {"action": name, "coordinate": xy()}
    elif action.skill == "type_text":
        native = {"action": "type", "text": a["text"]}
    elif action.skill in {"hotkey", "press_key"}:
        native = {"action": "key", "keys": a["keys"] if action.skill == "hotkey" else [a["key"]]}
    elif action.skill == "wait":
        native = {"action": "wait", "time": a["duration_ms"] / 1000}
    elif action.skill == "scroll":
        native = {
            "action": "scroll" if a["direction"] in {"up", "down"} else "hscroll",
            "pixels": a["amount"] * (-1 if a["direction"] in {"down", "left"} else 1),
        }
    elif action.skill == "drag":
        start = {
            "action": "mouse_move",
            "coordinate": [round(a["start_x"] * 1000), round(a["start_y"] * 1000)],
        }
        end = {
            "action": "left_click_drag",
            "coordinate": [round(a["end_x"] * 1000), round(a["end_y"] * 1000)],
        }
        return tool_call(start) + tool_call(end)
    else:
        raise ValueError("Delivered action cannot be represented by Desktop131: " + action.skill)
    if "x" in a and "coordinate" not in native:
        native["coordinate"] = xy()
    return tool_call(native)


def tool_call(native):
    return (
        "<tool_call>"
        + json.dumps({"name": "computer_use", "arguments": native}, ensure_ascii=False)
        + "</tool_call>"
    )


class NativeRuntime:
    def __init__(self):
        self.runtime = None
        self._image_archive = OrderedDict()

    def load(self):
        if self.runtime is None:
            if model := os.environ.get("TRACE2TASK_SWIFT_VLLM_MODEL"):
                if os.environ.get("TRACE2TASK_SWIFT_INPUT_MODE") == "append-only":
                    from trace2task.local_gui_swift_conversation import ConversationReader as Reader
                else:
                    from trace2task.local_gui_swift_vllm import VLLMReader as Reader
                self.runtime = Reader(model)
                return self.runtime
            import trace2task

            release, base, upstream = deployment_paths()
            trace2task.__path__.append(str(release / "frozen/trace2task"))
            spec = importlib.util.spec_from_file_location(
                "gui_swift_published_infer", release / "infer.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.runtime = module.GUISwift(str(base), str(upstream))
            self.runtime.processor = self.runtime.proc
        return self.runtime

    def predict(self, messages, images, folder):
        runtime = self.load()
        if hasattr(runtime, "predict_messages"):
            if getattr(self, "conversation_start", False):
                runtime.sessions.pop(getattr(self, "session_id", None), None)
            return runtime.predict_messages(
                messages, images, folder, getattr(self, "session_id", None)
            )
        names = []
        for i, image in enumerate(images):
            name = f"context-image-{i:03d}.png"
            target = folder / name
            digest = hashlib.sha256(
                str((image.mode, image.size)).encode() + image.tobytes()
            ).hexdigest()
            source = self._image_archive.pop(digest, None)
            if source is not None and source.is_file():
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copyfile(source, target)
            else:
                image.save(target, compress_level=1)
            self._image_archive[digest] = target
            while len(self._image_archive) > 256:
                self._image_archive.popitem(last=False)
            names.append(name)
        cursor = 0
        previous = None
        history = []
        text = []
        for message in messages:
            if message["role"] == "system":
                continue
            content = message["content"]
            if message["role"] == "assistant":
                history.append(
                    {
                        "raw_output": history_native(content),
                        "image": {"path": str(folder / previous)} if previous else None,
                    }
                )
                previous = None
                continue
            parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
            for part in parts:
                if part["type"] == "image":
                    previous = names[cursor]
                    cursor += 1
                elif part.get("text") and part["text"] != "[Historical screenshot omitted.]":
                    text.append(part["text"])
        if cursor != len(images) or previous is None:
            raise ValueError("Screenshot ordering mismatch")
        for i, turn in enumerate(history):
            if turn["image"] is None:
                if i >= len(history) - 2:
                    raise ValueError("Desktop131 requires last two executed-action screenshots")
                turn["image"] = {
                    "path": str(folder / previous)
                }  # Older paths are never read by the release.
        admitted = {
            "instruction": "\n".join(text),
            "image": {"path": str(folder / previous)},
            "history": history,
        }
        (folder / "native-request.json").write_text(
            json.dumps(admitted, ensure_ascii=False, indent=2)
        )
        original = runtime.inputs

        def audited(request):
            import torch

            inputs, row, budget = original(request)
            torch.save(inputs, folder / "inputs.pt")
            from gui_owl_desktop131 import messages as released_messages

            formatted = runtime.proc.apply_chat_template(
                released_messages(row), tokenize=False, add_generation_prompt=True
            )
            (folder / "formatted-prompt.txt").write_text(formatted)
            (folder / "tokenized-input.json").write_text(
                json.dumps({"input_ids": inputs["input_ids"][0].tolist(), "image_budget": budget})
            )
            return inputs, row, budget

        runtime.inputs = audited
        try:
            result = runtime.predict(admitted)
        finally:
            runtime.inputs = original
        (folder / "native-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if result["parse_error"]:
            raise ValueError(result["parse_error"])
        return tool_call(result["action"]), result["input_tokens"], names
