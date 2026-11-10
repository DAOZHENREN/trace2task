"""Append-only Desktop131 (adapted from the pinned Mobile105) conversation shared by SFT preparation and inference.

Historical assistant inputs remain MASK tables. Executed actions appear once,
in the following user receipt. No generated action is substituted into old KV.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path

from trace2task.local_gui_swift import SYSTEM

PROMPT = "swift-desktop131-append-only-v9-guidance-reset-task-every-three"
CONVERSATION_SYSTEM = SYSTEM


def clean(text):
    return str(text).replace("<|", "[special:").replace("<image>", "[image]")


class Conversation:
    def __init__(self, codec, *, session_id, max_tokens=24576):
        self.codec = codec
        self.session_id = session_id
        self.cache_salt = uuid.uuid4().hex
        self.max_tokens = max_tokens
        self._messages = [{"role": "system", "content": CONVERSATION_SYSTEM}]
        self.images = []
        self.turn_instructions = []
        self.turn_bins = []
        self.current_instruction = None
        self.guidance_turn = 0
        self.previous_ids = ()
        self.pending = False
        self.dropped_turns = 0
        self.table = codec.tokenizer.decode(list(codec.input_ids), skip_special_tokens=False)
        if tuple(codec.tokenizer.encode(self.table, add_special_tokens=False).ids) != tuple(
            codec.input_ids
        ):
            raise ValueError("mask_table_text_roundtrip_mismatch")

    @property
    def messages(self):
        return copy.deepcopy(self._messages)

    def observe(self, *, image, instruction=None, receipt=None):
        if self.pending:
            raise ValueError("commit_current_table_before_next_observation")
        if self.images and receipt is None and instruction is None:
            raise ValueError("next_turn_requires_execution_receipt")
        if not self.images and (
            (receipt is not None and not self.dropped_turns) or instruction is None
        ):
            raise ValueError("first_turn_requires_instruction_without_receipt")
        parts = []
        bindings = []
        if receipt is not None and instruction is None:
            if (
                not isinstance(receipt, dict)
                or not ("executed_actions" in receipt or {"executed_action", "outcome"} <= receipt.keys())
            ):
                raise ValueError("receipt_requires_actual_action_and_outcome")
            actions = receipt.get("executed_actions", receipt.get("executed_action"))
            actions = actions if isinstance(actions, list) else [actions]
            from gui_owl_desktop131 import history_coordinate_tokens
            bindings = history_coordinate_tokens(
                {'platform': 'desktop', 'history': [{'actions': actions}]}, self.codec)
            rendered = []
            for action in actions:
                fields = []
                for key, value in action.items():
                    payload = ('[<|fim_pad|>,<|fim_pad|>]' if key == 'coordinate'
                               else clean(json.dumps(value, ensure_ascii=False, separators=(',', ':'))))
                    fields.append(json.dumps(key) + ':' + payload)
                rendered.append('<action>{' + ','.join(fields) + '}</action>')
            parts.append('Executed actions: ' + ' '.join(rendered))
        if instruction is not None:
            self.current_instruction = instruction
            self.guidance_turn = 0
        if self.guidance_turn % 3 == 0:
            instruction = self.current_instruction
            parts.append("Platform: desktop\nTask: " + clean(instruction))
        else:
            parts.append("Continue completing the task based on the current screenshot.")
        self.guidance_turn += 1
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "\n".join(parts) + "\nCurrent screenshot:"},
                    {"type": "image", "image": image["path"]},
                ],
            }
        )
        self.images.append(copy.deepcopy(image))
        self.turn_instructions.append(instruction)
        self.turn_bins.append(bindings)
        self.pending = True

    def commit_table(self):
        if not self.pending:
            raise ValueError("no_pending_turn")
        self._messages.append({"role": "assistant", "content": self.table})
        self.pending = False

    def readable_messages(self):
        """Audit-only decoded bin view; inference binds exact IDs separately."""
        messages = self.messages
        turn = 0
        for message in messages:
            if message['role'] != 'user':
                continue
            bindings = iter(self.turn_bins[turn])
            turn += 1
            for part in message['content']:
                if part['type'] != 'text':
                    continue
                fragments = part['text'].split('<|fim_pad|>')
                part['text'] = fragments[0] + ''.join(
                    self.codec.tokenizer.decode([next(bindings)], skip_special_tokens=False) + fragment
                    for fragment in fragments[1:])
        return messages

    def bind_prefix(self, ids):
        """Insert exact training codebook IDs, never retokenize bin spellings."""
        bindings = []
        turn = 0
        for message in self._messages:
            if message['role'] == 'user':
                bindings.extend(self.turn_bins[turn])
                turn += 1
            elif message['role'] == 'assistant':
                bindings.extend([self.codec.mask_id] * list(self.codec.input_ids).count(self.codec.mask_id))
        ids = list(ids)
        positions = [i for i, token in enumerate(ids) if token == self.codec.mask_id]
        if len(positions) != len(bindings):
            raise ValueError('history_bin_binding_count_mismatch')
        for position, token in zip(positions, bindings):
            ids[position] = token
        return ids

    def parallel_input(self, processor):
        """Expand image placeholders with the pinned HF smart_resize rule.

        No pixel processing or image-size changes as history grows. Native HF
        processing remains the authority; parity is checked on real images.
        """
        from transformers.models.qwen2_vl.image_processing_qwen2_vl import smart_resize

        if not self.pending:
            raise ValueError("no_pending_turn")
        prompt = processor.apply_chat_template(
            self._messages, tokenize=False, add_generation_prompt=True
        )
        prefix = self.bind_prefix(self.codec.tokenizer.encode(prompt, add_special_tokens=False).ids)
        grids = []
        for im in self.images:
            h, w = smart_resize(
                im["height"], im["width"], factor=32, min_pixels=65536, max_pixels=1048576
            )
            grids.append([1, h // 16, w // 16])
        counts = iter(g[1] * g[2] // 4 for g in grids)
        image_id = self.codec.tokenizer.token_to_id("<|image_pad|>")
        expanded = []
        for token in prefix:
            expanded.extend([token] * next(counts) if token == image_id else [token])
        if next(counts, None) is not None:
            raise ValueError("conversation_image_placeholder_mismatch")
        return __import__("gui_owl_desktop131", fromlist=["build_parallel_input"]).build_parallel_input(self.codec.tokenizer, expanded), grids

    def fit_window(self, processor):
        """Drop oldest complete user/assistant pairs only when over budget.

        The first retained receipt stays verbatim, even when its action's older
        screenshot was removed. A changed prefix invalidates the session's old
        prefix assertion; vLLM still uses its normal content-addressed cache.
        """
        target = self.max_tokens
        image_target = 24
        while True:
            built, grids = self.parallel_input(processor)
            if len(built.input_ids) <= target and len(self.images) <= image_target:
                return built, grids
            # Refill in whole-turn segments: dropping one oldest turn on every
            # later call would invalidate the full prefix on every call.
            target = self.max_tokens // 2
            image_target = 12
            if len(self.images) == 1 and len(built.input_ids) <= self.max_tokens:
                return built, grids
            if len(self.images) <= 1:
                raise ValueError("current_observation_exceeds_context_limit")
            if [m["role"] for m in self._messages[1:3]] != ["user", "assistant"]:
                raise ValueError("cannot_drop_incomplete_history_turn")
            carried = self.turn_instructions.pop(0)
            self.turn_bins.pop(0)
            del self._messages[1:3]
            if carried is not None and self.turn_instructions[0] is None:
                # Restore the last effective instruction only at a window boundary.
                self._messages[1]['content'][0]['text'] = (
                    'Platform: desktop\nTask: ' + clean(carried) + '\n'
                    + self._messages[1]['content'][0]['text'])
                self.turn_instructions[0] = carried
            del self.images[0]
            self.dropped_turns += 1
            self.previous_ids = ()

    def prepare(self, processor, image_loader, *, verify_images=True):
        import torch

        if not self.pending:
            raise ValueError("no_pending_turn")
        images = []
        for im in self.images:
            if (
                verify_images
                and hashlib.sha256(Path(im["path"]).read_bytes()).hexdigest() != im["sha256"]
            ):
                raise ValueError("conversation_image_changed:" + im["path"])
            images.append(image_loader(im["path"]))
        prompt = processor.apply_chat_template(
            self._messages, tokenize=False, add_generation_prompt=True
        )
        enc = processor(text=[prompt], images=images, return_tensors="pt")
        prefix = self.bind_prefix(enc["input_ids"][0].tolist())
        built = __import__("gui_owl_desktop131", fromlist=["build_parallel_input"]).build_parallel_input(self.codec.tokenizer, prefix)
        ids = tuple(built.input_ids)
        if len(ids) > self.max_tokens:
            raise ValueError("conversation_context_limit_no_silent_truncation")
        if ids[: len(self.previous_ids)] != self.previous_ids:
            raise ValueError("conversation_prefix_changed")
        self.previous_ids = ids
        enc.update(
            input_ids=torch.tensor([ids]),
            attention_mask=torch.ones(1, len(ids), dtype=torch.long),
            slot_positions=torch.tensor([built.slot_positions]),
            prefix_lengths=torch.tensor([len(prefix)]),
            lengths=torch.tensor([len(ids)]),
        )
        return enc
