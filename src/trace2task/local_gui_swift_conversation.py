"""Append-only Desktop131 service, using the GUI-Swift 2B receipt/KV method."""

import copy
import hashlib
import json
import re
import time
from collections import OrderedDict
from pathlib import Path

from trace2task.local_gui_swift_vllm import VLLMReader


def guidance_instruction(text):
    """Separate the stable contract from first-turn context and transport metadata."""
    match = re.search(r"\[guidance_revision:(\d+)\]\n?", text)
    revision = match.group(1) if match else None
    text = re.sub(r"\[guidance_revision:\d+\]\n?", "", text)
    header, label, goal = text.partition("Current subgoal:")
    if label:
        text = (
            header.replace("subgoal", "goal").replace("stage outcome", "goal outcome")
            + "Current goal:"
            + goal
        )
    contract = (
        "Current goal:" + text.split("Current goal:", 1)[1] if "Current goal:" in text else text
    )
    # Discard the generic executor preamble; retain task literals and guidance.
    task = (
        contract[len("Current goal:") :].lstrip()
        if contract.startswith("Current goal:")
        else contract
    )
    return task, task, revision


def guidance_session(saved, contract, revision):
    # Only reuse context inside the same guidance revision.
    if saved and (saved.get("contract") != contract or saved.get("revision") != revision):
        return None
    return saved


class ConversationReader(VLLMReader):
    max_images = 24

    def __init__(self, model):
        super().__init__(model)
        self.sessions = OrderedDict()
        self.image_cache = {}

    def predict_messages(self, messages, images, folder, session_id):
        import torch
        from vllm import PoolingParams
        from trace2task.desktop131_conversation import Conversation, PROMPT
        from trace2task.local_gui_swift import history_native, tool_call

        started = time.perf_counter()
        session_id = session_id or str(folder)
        raw_history = [history_native(m["content"]) for m in messages if m["role"] == "assistant"]
        latest = messages[-1]["content"]
        instruction = "\n".join(p["text"] for p in latest if p.get("type") == "text")
        instruction, contract, revision = guidance_instruction(instruction)
        previous = self.sessions.get(session_id)
        saved = guidance_session(previous, contract, revision)
        if saved is None:
            session = Conversation(self.c, session_id=session_id, max_tokens=24576)
            old_history = raw_history
            # First-turn application knowledge remains available, once.
            seed = messages[1]["content"]
            seed_text = "\n".join(p["text"] for p in seed if p.get("type") == "text")
            seed_text, _, _ = guidance_instruction(seed_text)
            if previous is None and seed_text and seed_text != instruction:
                instruction = seed_text + "\n" + instruction
        else:
            if raw_history[: len(saved["history"])] != saved["history"]:
                raise ValueError("Confirmed action history was rewritten; start a new conversation")
            session = copy.copy(saved["session"])
            session._messages = saved["session"].messages
            session.images = copy.deepcopy(session.images)
            session.turn_instructions = list(session.turn_instructions)
            session.turn_bins = copy.deepcopy(session.turn_bins)
            old_history = saved["history"]
            if not instruction:
                instruction = saved["instruction"]
            if contract == saved.get("contract") and revision == saved.get("revision"):
                instruction = None
        actions = []
        for raw in raw_history[len(old_history) :]:
            actions.extend(
                json.loads(x)["arguments"]
                for x in re.findall(r"<tool_call>(.*?)</tool_call>", raw, re.S)
            )
        receipt = {"executed_actions": actions}
        # Guidance turns contain the new task, not action receipts.
        if instruction is not None or not session.images:
            receipt = None
        picture = images[-1]
        path = folder / "current-observation.png"
        picture.save(path, compress_level=1)
        info = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "width": picture.width,
            "height": picture.height,
        }
        session.observe(image=info, instruction=instruction, receipt=receipt)
        expected_prefix = session.previous_ids
        dropped_before = session.dropped_turns
        built, grids = session.fit_window(self.proc)
        if session.dropped_turns != dropped_before:
            expected_prefix = ()
        formatted = self.proc.apply_chat_template(
            session.messages, tokenize=False, add_generation_prompt=True
        )
        ids = session.bind_prefix(
            self.c.tokenizer.encode(formatted, add_special_tokens=False).ids
        ) + list(self.c.input_ids)
        pics = []
        for im in session.images:
            key = (im["path"], im["sha256"])
            if key not in self.image_cache:
                from PIL import Image

                if hashlib.sha256(Path(im["path"]).read_bytes()).hexdigest() != im["sha256"]:
                    raise ValueError("Conversation screenshot changed")
                with Image.open(im["path"]) as f:
                    self.image_cache[key] = f.convert("RGB")
            pics.append(self.image_cache[key])
        request = {
            "prompt_token_ids": ids,
            "multi_modal_data": {"image": pics},
            "cache_salt": session.cache_salt,
        }
        t = time.perf_counter()
        output = self.engine.encode(
            request,
            pooling_params=PoolingParams(
                extra_kwargs={"swift131_table_ids": list(self.c.input_ids)}
            ),
            pooling_task="encode",
            use_tqdm=False,
        )[0]
        engine_seconds = time.perf_counter() - t
        expanded = tuple(output.prompt_token_ids)
        if expanded != tuple(built.input_ids):
            raise ValueError("vLLM/append-only native input IDs differ")
        if expanded[: len(expected_prefix)] != tuple(expected_prefix):
            raise ValueError("Append-only KV prefix changed")
        logits = torch.as_tensor(output.outputs.data).float()
        if logits.shape[0] != 131 or not torch.isfinite(logits).all():
            raise ValueError("Invalid 131-slot logits")
        try:
            action, error = self.c.decode(logits, key_sequences=self.keys), None
        except (ValueError, KeyError) as exc:
            action, error = None, str(exc)
        result = dict(
            action=action,
            parse_error=error,
            input_tokens=len(expanded),
            output_slots=131,
            model_seconds=engine_seconds,
            total_seconds=time.perf_counter() - started,
            inference_engine="vllm-0.11.0-pooling",
            input_mode=PROMPT,
            retained_observations=len(pics),
            image_budget=1048576,
            dropped_history_turns=session.dropped_turns,
            prefix_verified_tokens=len(expected_prefix),
            input_token_parity=True,
        )
        (folder / "formatted-prompt.txt").write_text(
            self.c.tokenizer.decode(ids[: -len(self.c.input_ids)], skip_special_tokens=False)
        )
        (folder / "tokenized-input.json").write_text(
            json.dumps({"input_ids": list(expanded), "input_mode": PROMPT})
        )
        native = {
            "instruction": session.turn_instructions[-1],
            "receipt": receipt,
            "image": info,
            "images": session.images,
            "history": raw_history,
            "messages": session.readable_messages(),
            "new_guidance_session": saved is None,
            "cache_salt": session.cache_salt,
            "message_representation": "decoded exact history-bin IDs; not for retokenization",
            "input_mode": PROMPT,
            "image_grid_thw": grids,
        }
        (folder / "native-request.json").write_text(
            json.dumps(native, ensure_ascii=False, indent=2)
        )
        (folder / "native-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if error:
            raise ValueError(error)
        session.previous_ids = expanded
        session.commit_table()
        self.sessions.pop(session_id, None)
        self.sessions[session_id] = {
            "session": session,
            "history": raw_history,
            "instruction": contract,
            "contract": contract,
            "revision": revision,
        }
        while len(self.sessions) > 4:
            self.sessions.popitem(last=False)
        retained = {
            (im["path"], im["sha256"]) for x in self.sessions.values() for im in x["session"].images
        }
        self.image_cache = {k: v for k, v in self.image_cache.items() if k in retained}
        # Old images remain immutable references; no per-turn pixel hashing or
        # duplicate PNG copies for every previous observation.
        return tool_call(action), len(expanded), [im["path"] for im in session.images]
