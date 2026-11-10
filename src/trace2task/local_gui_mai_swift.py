"""Adapter to the published MAI-Swift native105 reader (no generation fallback).

Reuse load/action mapping from the 2026-11-08 hybrid integration. The released
infer.py verifies weights, codec, upstream and pinned dependencies on load.
Mainline owns messages, confirmed history, permissions and execution.
"""
import importlib.util
import json
import math
import os
from pathlib import Path
from trace2task.execution_protocol import ActionPlan, ActionUnavailable

MODEL = "mai-swift-2b"
BACKEND = "mai-swift-native105"
ACTIONS = ("tap", "long_press", "swipe", "scroll_up", "scroll_down", "scroll_left", "scroll_right",
           "type_text", "open_app", "back", "home", "enter", "menu", "wait", "done", "failed")
SYSTEM = (
    "Execute the current Android instruction using the screenshot and executed-action history. "
    "Predict the 105 masked values in one forward: action, x, y, x2, y2, and 100 text slots. "
    "Action codes: " + ", ".join(f"{label}={a}" for label, a in zip("ABCDEFGHIJKLMNOP", ACTIONS)) + ". "
    "tap/long_press use x,y; swipe uses both coordinate pairs; type_text/open_app use text. "
    "Other actions have no parameters. Coordinates and historical coordinates use the same "
    "256-bin codebook; the decoder maps them to 0-1000. Scroll direction means browsing direction. "
    "Copy text exactly, at most 99 native tokens followed by EOS. Timing is an executor setting. "
    "Predict only applicable fields in the supplied <action> table; do not output explanations."
)


def deployment_paths():
    names = ("TRACE2TASK_MAI_RELEASE", "TRACE2TASK_MAI_BASE", "TRACE2TASK_MAI_BUNDLE")
    return [Path(os.environ.get(name, "UNCONFIGURED-" + name)) for name in names]


def required_paths():
    release, base, bundle = deployment_paths()
    return [release / "infer.py", release / "SHA256SUMS.json", release / "adapter_model.safetensors",
            base / "config.json", bundle / "upstream/slotread/train.py"]


def decode_native(value):
    if not isinstance(value, dict) or value.get("action") not in ACTIONS:
        raise ValueError("Invalid MAI-Swift native action")
    if value["action"] == "done":
        return ActionPlan.from_prediction({"actions": [{"done": True}]}).to_payload()
    if value["action"] == "failed":
        return {"control": "terminate_failure", "text": "MAI-Swift reports failure; not independent verification."}
    return action_plan(value)


def load_runtime(release, base_model, bundle, device="cuda:0"):
    """Load the hash-verifying published runtime alongside the product package."""
    import trace2task

    release = Path(release).resolve()
    # Product main has no Mobile105 codec; reuse the release, never duplicate it.
    codec_path = str(release / "runtime" / "trace2task")
    if codec_path not in trace2task.__path__:
        trace2task.__path__.append(codec_path)
    spec = importlib.util.spec_from_file_location("mai_swift_release_infer", release / "infer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MAISwift(base_model, bundle, device)


def action_plan(action):
    """Lossless available product routes; unavailable routes explicitly hand back."""
    name = action["action"]

    def xy(key="coordinate"):
        p = action[key]
        if len(p) != 2 or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1000 for v in p
        ):
            raise ValueError("Invalid MAI-Swift coordinates")
        return [v / 1000 for v in p]

    if name in {"tap", "long_press"}:
        x, y = xy()
        skill, args = "click", {"x": x, "y": y, "button": "left"}
        if name == "long_press":
            skill = "hold_mouse"
            args["duration_ms"] = 1000
    elif name == "swipe":
        x, y = xy()
        x2, y2 = xy("coordinate2")
        skill, args = (
            "drag",
            {
                "start_x": x,
                "start_y": y,
                "end_x": x2,
                "end_y": y2,
                "duration_ms": 800,
                "button": "left",
            },
        )
    elif name.startswith("scroll_"):
        raise ActionUnavailable(
            "Directional scroll requires the AndroidWorld native primitive adapter"
        )
    elif name == "type_text":
        skill, args = "type_text", {"text": action["text"]}
    elif name in {"back", "enter"}:
        skill, args = "press_key", {"key": "escape" if name == "back" else "enter"}
    elif name == "wait":
        skill, args = "wait", {"duration_ms": 1000}
    elif name in {"done", "failed"}:
        return None
    else:
        raise ActionUnavailable("MAI-Swift action needs an explicit Android route: " + name)
    return ActionPlan.from_prediction({"actions": [{"skill": skill, "args": args}]}).to_payload()


class NativeRuntime:
    def __init__(self):
        self.runtime = None

    def load(self):
        if self.runtime is None:
            self.runtime = load_runtime(*deployment_paths())
            from trace2task.mobile105_codec import SYSTEM as released_system
            if SYSTEM != released_system:
                raise ValueError("MAI system prompt differs from pinned release")
        return self.runtime

    def predict(self, messages, images, folder):
        import torch
        runtime = self.load()
        formatted = runtime.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = runtime.processor(text=[formatted], images=images, return_tensors="pt",
            images_kwargs={"size": {"shortest_edge": 65536, "longest_edge": 1048576}})
        built = runtime.c.build_parallel_input(inputs["input_ids"][0].tolist())
        if len(built.input_ids) > 16384:
            raise ValueError("MAI-Swift exceeds 16384 tokens; no silent history truncation")
        inputs.update(input_ids=torch.tensor([built.input_ids]),
            attention_mask=torch.ones(1, len(built.input_ids), dtype=torch.long),
            slot_positions=torch.tensor([built.slot_positions]),
            prefix_lengths=torch.tensor([built.prefix_length]), lengths=torch.tensor([len(built.input_ids)]))
        (folder / "formatted-prompt.txt").write_text(formatted, encoding="utf-8")
        torch.save(dict(inputs), folder / "inputs.pt")
        image_names = []
        for i, image in enumerate(images):
            name = f"context-image-{i:03d}.png"
            image.save(folder / name)
            image_names.append(name)
        logits = runtime.logits(inputs)
        torch.save(logits, folder / "logits.pt")
        native = runtime.c.decode(logits)
        return json.dumps(native, ensure_ascii=False), len(built.input_ids), image_names
