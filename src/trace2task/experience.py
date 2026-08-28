from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

MIN_ROUTE_SCORE = 4.0
AMBIGUITY_MARGIN = 1.0
MESSAGE_CUES = (
    "微信",
    "wechat",
    "发消息",
    "发送消息",
    "发给",
    "联系人",
    "群聊",
    "文件传输助手",
)


@dataclass(frozen=True)
class ExperienceMatch:
    task_path: str
    task_id: str
    score: float
    confidence: float
    matched_terms: tuple[str, ...]
    reason: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_path": self.task_path,
            "task_id": self.task_id,
            "score": self.score,
            "confidence": self.confidence,
            "matched_terms": list(self.matched_terms),
            "reason": self.reason,
        }


def _compact(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())


def _terms(value: str) -> set[str]:
    normalized = value.casefold()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_.-]+", normalized))
    for chunk in re.findall(r"[\u3400-\u9fff]+", normalized):
        if len(chunk) == 1:
            terms.add(chunk)
        else:
            terms.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return terms


def _score(instruction: str, task: Mapping[str, Any]) -> tuple[float, tuple[str, ...]]:
    compact_instruction = _compact(instruction)
    instruction_terms = _terms(instruction)
    task_id = str(task.get("task_id") or "")
    intent = str(task.get("experience_intent") or task_id)
    examples = [str(example) for example in task.get("experience_examples") or ()]
    sources = [task_id, intent, str(task.get("instruction") or ""), *examples]
    score = 0.0
    matched: list[str] = []

    for label, source in (("任务名称", task_id), ("经验意图", intent)):
        compact_source = _compact(source)
        if len(compact_source) >= 2 and compact_source in compact_instruction:
            score += 8.0
            matched.append(f"{label}:{source}")

    for example in examples:
        compact_example = _compact(example)
        if len(compact_example) >= 3 and compact_example in compact_instruction:
            score += 6.0
            matched.append(f"示例:{example}")

    fuzzy_sources = [task_id, intent, *examples]
    fuzzy_matches = [
        (SequenceMatcher(None, compact_instruction, _compact(source)).ratio(), source)
        for source in fuzzy_sources
        if len(_compact(source)) >= 4
    ]
    if fuzzy_matches:
        fuzzy_ratio, fuzzy_source = max(fuzzy_matches, key=lambda item: item[0])
        fuzzy_shared = instruction_terms & _terms(fuzzy_source)
        if fuzzy_ratio >= 0.5 and fuzzy_shared:
            score += fuzzy_ratio * 6.0
            matched.append(f"模糊表达:{fuzzy_source}")

    source_terms = _terms(" ".join(sources))
    shared_terms = sorted(instruction_terms & source_terms)
    if shared_terms:
        score += min(4.0, len(shared_terms) * 0.75)
        matched.extend(shared_terms[:4])

    process_name = str(task.get("process_name") or "").casefold()
    actions = {str(action) for action in task.get("actions") or ()}
    message_task = process_name in {"weixin.exe", "wechat.exe"} or "type_text" in actions
    message_hits = [cue for cue in MESSAGE_CUES if cue in instruction.casefold()]
    if message_task and message_hits:
        score += 5.0
        matched.append(f"消息场景:{message_hits[0]}")

    return score, tuple(dict.fromkeys(matched))


def route_experience(
    instruction: str,
    taskpacks: Sequence[Mapping[str, Any]],
) -> ExperienceMatch:
    """Select one confirmed Trace experience without a model or hidden state."""

    normalized = " ".join(instruction.split())
    if not normalized:
        raise ValueError("请输入一条任务指令")
    candidates: list[tuple[float, tuple[str, ...], Mapping[str, Any]]] = []
    for task in taskpacks:
        if not task.get("confirmed"):
            continue
        score, matched = _score(normalized, task)
        candidates.append((score, matched, task))
    if not candidates:
        raise ValueError("没有已确认的 Trace 经验可供自动选择")

    candidates.sort(key=lambda item: (-item[0], str(item[2].get("path") or "")))
    best_score, matched, best = candidates[0]
    if best_score < MIN_ROUTE_SCORE:
        raise ValueError(
            "没有找到足够匹配的已确认 Trace 经验；请手动选择或先录制一次示范"
        )
    if len(candidates) > 1 and best_score - candidates[1][0] < AMBIGUITY_MARGIN:
        raise ValueError(
            "多个 Trace 经验与指令同样接近；请手动选择经验，避免执行错误流程"
        )

    confidence = min(0.99, round(0.5 + best_score / 20.0, 2))
    matched_text = "、".join(matched) if matched else "任务元数据"
    task_id = str(best.get("task_id") or "")
    return ExperienceMatch(
        task_path=str(best.get("path") or ""),
        task_id=task_id,
        score=round(best_score, 2),
        confidence=confidence,
        matched_terms=matched,
        reason=f"指令命中 {matched_text}，选择 Trace“{task_id}”",
    )
