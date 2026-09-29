"""Small, safe UI projection of the append-only Codex/API I/O audit.

The audit remains the source of truth. In particular, image data and API
credentials must never be copied into the browser job snapshot.
"""

import json
from datetime import datetime
from pathlib import Path


def _browser_safe(value):
    if isinstance(value, dict):
        return {
            key: _browser_safe(item)
            for key, item in value.items()
            if key.lower() not in {"authorization", "api_key"}
        }
    if isinstance(value, list):
        return [_browser_safe(item) for item in value]
    if isinstance(value, str) and (value.startswith("data:image/") or len(value) > 100_000):
        return "[image or oversized payload archived in events.jsonl]"
    return value


def _elapsed_ms(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return round(
            (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000, 2
        )
    except ValueError:
        return None


def project_model_io(run_dir: Path) -> list[dict]:
    """Return one display round per real model request, with delivery receipts."""
    audit = Path(run_dir) / "io-audit" / "events.jsonl"
    if not audit.is_file():
        return []
    rounds: list[dict] = []
    with audit.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # A process interrupted during an append leaves at most one partial line.
            if not isinstance(entry, dict):
                continue
            kind = entry.get("type")
            payload = entry.get("payload") or {}
            if kind == "model_request" and isinstance(payload, dict):
                frame = Path(run_dir) / "frames" / f"{len(rounds):04d}.png"
                rounds.append(
                    {
                        "step_index": len(rounds),
                        "status": "pending",
                        "provider": "api",
                        "request_id": str(entry.get("seq", "")),
                        "started_at": entry.get("timestamp"),
                        "screenshot": str(frame) if frame.is_file() else None,
                        "input": {"messages": _browser_safe(payload.get("messages", []))},
                        "request": {
                            "model": payload.get("model"),
                            "response_format": _browser_safe(payload.get("response_format")),
                        },
                    }
                )
            elif (
                kind == "codex_request"
                and isinstance(payload, dict)
                and payload.get("method") == "turn/start"
            ):
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                parts = params.get("input", [])
                if not isinstance(parts, list):
                    parts = []
                text = "\n\n".join(
                    part.get("text", "")
                    for part in parts
                    if isinstance(part, dict) and part.get("type") == "text"
                )
                assets = entry.get("image_assets") or {}
                image = next(iter(assets.values()), {}).get("file") if assets else None
                rounds.append(
                    {
                        "step_index": len(rounds),
                        "status": "pending",
                        "provider": "codex",
                        "request_id": str(entry.get("seq", "")),
                        "started_at": entry.get("timestamp"),
                        "input": {"messages": [{"role": "user", "content": text}]},
                        "screenshot": str(audit.parent / image) if image else None,
                        "request": {"model": params.get("model"), "effort": params.get("effort")},
                    }
                )
            elif kind == "model_response" and rounds:
                current = rounds[-1]
                choices = payload.get("choices") if isinstance(payload, dict) else None
                choice = (
                    choices[0]
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict)
                    else {}
                )
                message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                current.update(
                    status="completed",
                    raw_output=_browser_safe(message.get("content", "")),
                    response={"finish_reason": choice.get("finish_reason")},
                    tokens=_browser_safe(payload.get("usage", {}))
                    if isinstance(payload, dict)
                    else {},
                    model_roundtrip_ms=_elapsed_ms(
                        current.get("started_at"), entry.get("timestamp")
                    ),
                )
            elif (
                kind == "codex_response"
                and rounds
                and isinstance(payload, dict)
                and payload.get("method") == "turn/completed"
            ):
                current = rounds[-1]
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                texts = [
                    item.get("text", "")
                    for item in turn.get("items", [])
                    if isinstance(item, dict)
                    and item.get("type") == "agentMessage"
                    and item.get("phase") != "commentary"
                ]
                current.update(
                    status="completed" if turn.get("status") == "completed" else "failed",
                    raw_output="\n\n".join(texts),
                    response={"turn_status": turn.get("status"), "error": turn.get("error")},
                    model_roundtrip_ms=_elapsed_ms(
                        current.get("started_at"), entry.get("timestamp")
                    ),
                )
            elif kind in {"model_error", "executor_error"} and rounds:
                current = rounds[-1]
                current["error"] = (
                    entry.get("message") or entry.get("error_type") or "Unknown error"
                )
                if kind == "model_error":
                    current["status"] = "failed"
            elif kind == "executor_result" and rounds:
                current = rounds[-1]
                execution = current.setdefault("execution", {"status": "delivered", "receipts": []})
                execution["receipts"].append(_browser_safe(entry.get("result", {})))
    for current in rounds:
        current.pop("started_at", None)
    return rounds
