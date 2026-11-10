"""MAI-Swift native slot reader plugged into the existing hybrid predictor seam.

The supervisor/controller stays upstream. User turns are composed by Trace2Task;
only the system action codec and final parallel MASK scaffold are model-specific.
This is a distinct prompt condition from the released native offline baseline.
"""

from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import math
from pathlib import Path

from trace2task.execution_protocol import ActionPlan, ActionUnavailable
from trace2task.guided_history import GuidedTaskConversation, append_guided_history
from trace2task.local_gui_protocol import TURN_TEMPLATE, render_turn_template

MODEL = "mai-swift-2b"
BACKEND = "mai-swift-native105"


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


class MaiSwiftHybridPredictor:
    """Inject as HybridModelAdapter(predictor=..., inference_backend=BACKEND).

    Only history confirmed by that controller is appended. Prediction itself is
    never committed as executed history. Scope-specific action mapping is injected
    by the AndroidWorld runner; no model-generated command is executed here.
    """

    def __init__(self, runtime, *, system_prompt=None, mapper=action_plan):
        self.runtime, self.mapper = runtime, mapper
        if system_prompt is None:
            from trace2task.mobile105_codec import SYSTEM

            system_prompt = SYSTEM
        self.system = system_prompt
        self.conversation = None

    def compose(self, instruction, request):
        from PIL import Image

        if request["model"] != MODEL or request["expected_backend"] != BACKEND:
            raise ValueError("MAI-Swift model/backend mismatch")
        if request.get("prompt_profile") is not None:
            raise ValueError("MAI-Swift slot system cannot use a free-generation prompt profile")
        identity = request["conversation_id"]
        if request.get("conversation_start"):
            conversation = GuidedTaskConversation(
                identity, MODEL, None, request.get("guided_knowledge", "")
            )
        else:
            if self.conversation is None or self.conversation.identity != identity:
                raise ValueError("Unknown MAI-Swift conversation")
            conversation = copy.deepcopy(self.conversation)
        if request.get("guided_history_offset") != conversation.total_turns:
            raise ValueError("Confirmed history offset mismatch")
        append_guided_history(
            conversation, request.get("guided_history", []), images_supported=True
        )
        image = Image.open(io.BytesIO(base64.b64decode(request["image"], validate=True)))
        if image.width * image.height > 32_000_000:
            raise ValueError("Image too large")
        image = image.convert("RGB")
        text = render_turn_template(
            TURN_TEMPLATE,
            task=instruction,
            history="No previous action.",
            experience_block="",
            execution_feedback_block="",
            execution_context_block="",
        )
        content = [{"type": "text", "text": text}, {"type": "image"}]
        messages, images = conversation.compose(self.system, content, [image])
        return conversation, messages, images

    def __call__(self, instruction, **request):
        import torch

        conversation, messages, images = self.compose(instruction, request)
        runtime = self.runtime
        content, current_images = messages[-1]["content"], [images[-1]]
        removed = []
        while True:
            messages, images = conversation.compose(self.system, content, current_images)
            prompt = runtime.processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = runtime.processor(
                text=[prompt], images=images, return_tensors="pt",
                images_kwargs={"size": {"shortest_edge": 65536, "longest_edge": 1048576}},
            )
            built = runtime.c.build_parallel_input(inputs["input_ids"][0].tolist())
            if len(built.input_ids) <= 16384:
                break
            if not conversation.turns:
                raise ValueError("Current MAI-Swift observation alone exceeds 16384 tokens")
            removed.append(conversation.step_indices.pop(0))
            conversation.turns.pop(0)
        # Keep total_turns unchanged: controller offsets count all executed actions.
        inputs.update(
            input_ids=torch.tensor([built.input_ids]),
            attention_mask=torch.ones(1, len(built.input_ids), dtype=torch.long),
            slot_positions=torch.tensor([built.slot_positions]),
            prefix_lengths=torch.tensor([built.prefix_length]),
            lengths=torch.tensor([len(built.input_ids)]),
        )
        audit = Path(request["audit_dir"])
        audit.mkdir(parents=True, exist_ok=False)
        (audit / "input.json").write_text(
            json.dumps(
                {
                    "messages": messages,
                    "prompt": prompt,
                    "instruction": instruction,
                    "protocol": "trace2task-hybrid-mai-swift-native105-v1",
                    "input_tokens": len(built.input_ids),
                    "context_window": {"policy": "oldest-complete-turns-v1", "removed_step_indices": removed, "total_executed_turns": conversation.total_turns},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        for i, image in enumerate(images):
            image.save(audit / f"image-{i}.png")
        torch.save(inputs, audit / "inputs.pt")
        logits = runtime.logits(inputs)
        native = runtime.c.decode(logits)
        raw = json.dumps(native, ensure_ascii=False)
        torch.save(logits, audit / "logits.pt")
        (audit / "native-output.json").write_text(raw)
        self.conversation = conversation
        result = {"raw_output": raw, "prompt": prompt, "native_action": native}
        if native["action"] in {"done", "failed"}:
            return dict(result, status="model_control", control=native["action"])
        try:
            plan = self.mapper(native)
        except ActionUnavailable as error:
            return dict(result, status="model_control", control="interact", reason=str(error))
        return dict(result, status="predicted", prediction=plan)
