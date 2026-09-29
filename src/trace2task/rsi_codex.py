"""Subscription transport for pinned RSIAgent; never executes generated programs.

The official transcript remains authoritative, including durable images. A fresh
ephemeral Codex thread receives each complete transcript, so concurrent roles and
recovered branches cannot inherit another role's private context. A resident
App Server process can still be reused by the supplied session factory.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from trace2task.codex_app_server import CodexTurnTimeoutError

UPSTREAM_REVISION = "a9e56263f6deaa493496ad6b155fe24bf131bc12"
CODEX_AUTHENTICATION_REQUIRED = "codex_authentication_required"

# This is deliberately an additive envelope.  The exact official role system
# prompt remains the first part of the base instructions; this only prevents a
# model from treating Codex's own intentionally read-only/no-tool runtime as a
# restriction on the distinct, host-controlled OSWorld guest.
MODEL_EXECUTION_BOUNDARY = """\
\n\nTrace2Task execution boundary (additive; do not change the role instructions above):
You are a text-only planning role in a read-only, tool-free Codex container. Any
filesystem or sandbox policy reported by native Codex applies only to that
container and its disabled native tools. It does not describe the execution
semantics of program text required by the RSIAgent role prompt. Your response is
returned as text to the trusted official RSIAgent host, which may validate and
submit compatible program text to a separate resettable, isolated guest with
its own policy. You have no direct access to that guest or host filesystem and
must not claim to have executed an action. Do not invoke or request native
Codex tools; return only the response required by the official role prompt.
"""

# This addition is used only by the one-turn program-boundary smoke.  It keeps
# the subscription container read-only while testing that the Actor can still
# emit program *text* for the separately governed guest.  It must never be
# used for a real practice turn, where the official loop controls submission.
PROGRAM_BOUNDARY_PREFLIGHT = """\
\n\nTrace2Task program-boundary preflight (additive; applies only to this one turn):
Return one official Actor JSON Program proposal for the described isolated guest.
The trusted preflight will parse your text only. It will not run, upload, or
otherwise submit the proposed program, and no virtual machine is started. Do
not claim that the program has executed or that its checks have passed.
"""


def model_only_base_instructions(official_system: str) -> str:
    """Keep official role text verbatim while clarifying execution topology."""
    if not isinstance(official_system, str):
        raise TypeError("Official RSIAgent system instructions must be text")
    return official_system + MODEL_EXECUTION_BOUNDARY


def program_boundary_preflight_instructions() -> str:
    """Return the smoke-only scope clarification without changing Actor text."""
    return PROGRAM_BOUNDARY_PREFLIGHT


def classify_codex_authentication_failure(error: BaseException) -> str | None:
    """Return a stable code only for explicit Codex login/refresh failures.

    This deliberately does *not* treat generic network failures, timeouts, or
    server errors as authentication failures.  The value is persisted through
    the upstream transport error's detail string, so the controller can retain
    its existing error boundary while the UI gives an actionable manual-login
    instruction without exposing a provider response.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).casefold()
        refresh_failure = (
            re.search(r"refresh[ _-]?token", text) is not None
            and re.search(r"failed|failure|invalid|expired|error|rejected|denied", text) is not None
        )
        if (
            re.search(r"\b401\b", text) is not None
            or "unauthorized" in text
            or "invalid_grant" in text
            or "login required" in text
            or "not logged in" in text
            or re.search(r"(?:access[ _-]?)?token(?:\s+has)?\s+expired", text) is not None
            or "session expired" in text
            or refresh_failure
        ):
            return CODEX_AUTHENTICATION_REQUIRED
        current = current.__cause__ or current.__context__
    return None


def safe_codex_failure_text(error: BaseException) -> str:
    """Keep explicit authorization failures actionable without archiving provider text."""
    if classify_codex_authentication_failure(error) is not None:
        return f"{CODEX_AUTHENTICATION_REQUIRED}; manual login required"
    return str(error)


class PracticeBudgetExceeded(RuntimeError):
    """Practice stopped by its controller budget, not a task FAIL verdict."""


def prepare_transcript(system: str, user: str, history: list | None, image: Any,
                       directory: Path) -> tuple[str, tuple[Path, ...]]:
    """Preserve role order and exact image bytes with explicit attachment IDs."""
    messages = list(history or [])
    current: dict[str, Any] = {"role": "user", "content": user}
    if image is not None:
        images = image if isinstance(image, list) else [image]
        current["_rsiagent_images"] = [
            {"data": base64.b64encode(bytes(value)).decode("ascii")} for value in images
        ]
    messages.append(current)
    transcript = []
    paths = []
    for message in messages:
        if message.get("role") not in {"user", "assistant", "system"}:
            raise ValueError("Unsupported RSIAgent transcript role")
        entry = dict(message)
        attachments = entry.pop("_rsiagent_images", [])
        references = []
        for attachment in attachments:
            data = base64.b64decode(attachment["data"], validate=True)
            digest = hashlib.sha256(data).hexdigest()
            if attachment.get("sha256", digest) != digest:
                raise ValueError("RSIAgent archived image digest mismatch")
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                suffix = ".png"
            elif data.startswith(b"\xff\xd8"):
                suffix = ".jpg"
            elif data.startswith((b"GIF87a", b"GIF89a")):
                suffix = ".gif"
            elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
                suffix = ".webp"
            else:
                raise ValueError("Unsupported RSIAgent image encoding")
            path = directory / f"image-{len(paths) + 1:04d}{suffix}"
            path.write_bytes(data)
            paths.append(path)
            references.append({"attachment": len(paths), "sha256": digest})
        if references:
            entry["images"] = references
        transcript.append(entry)
    prompt = (
        "Continue the following RSIAgent conversation. The role instructions are "
        "provided separately. Attached images are numbered from 1 in attachment "
        "order; each transcript message identifies its own images. Respond only "
        "as the assistant to the last user message. Do not invoke native Codex "
        "tools: they are unavailable in this read-only model container. Any "
        "proposed program is text for the separate, host-controlled isolated "
        "guest harness, not a command for this container.\n"
        + json.dumps(transcript, ensure_ascii=False)
    )
    return prompt, tuple(paths)


class CodexRSITransport:
    """Bounded, auditable replacement for upstream llm.client.chat.

    session_factory receives (system, model, effort, call_directory). It must supply an isolated
    model-only App Server session, not a host-authorized coding agent. Installing
    this adapter alone does NOT establish VM or host filesystem isolation.
    """

    def __init__(self, *, session_factory: Callable, model: str,
                 reasoning_effort: str, audit_dir: Path, max_calls: int,
                 wall_seconds: float, stop_event: threading.Event | None = None,
                 reserve_call: Callable[[], int] | None = None):
        if not model or max_calls < 1 or wall_seconds <= 0:
            raise ValueError("An explicit model and positive practice budgets are required")
        self.session_factory = session_factory
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.audit_dir = audit_dir.resolve()
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.max_calls = max_calls
        self.deadline = time.monotonic() + wall_seconds
        self.stop_event = stop_event or threading.Event()
        self._lock = threading.Lock()
        self._calls = 0
        self.reserve_call = reserve_call

    def chat(self, model: str, system: str, user: str, max_tokens: int = 10000,
             temperature: float = 0.0, reasoning_effort: str | None = None,
             history: list | None = None, image: Any = None,
             reasoning_max_tokens: int = 0, top_p: float = -1.0,
             provider_order: Any = None, provider_allow_fallbacks: bool = True,
             provider_require_parameters: bool = False, json_object: bool = False) -> str:
        with self._lock:
            if self.stop_event.is_set() or time.monotonic() >= self.deadline:
                raise PracticeBudgetExceeded("Practice stopped or wall-clock budget exhausted")
            if self._calls >= self.max_calls:
                raise PracticeBudgetExceeded("Practice model-call budget exhausted")
            self._calls += 1
            call = self.reserve_call() if self.reserve_call is not None else self._calls
        # Exclusive creation prevents an accidental restart from overwriting audit evidence.
        call_dir = self.audit_dir / f"call-{call:06d}"
        call_dir.mkdir(exist_ok=False)
        receipt = {
            "status": "started", "upstream_revision": UPSTREAM_REVISION,
            "model": self.model, "reasoning_effort": self.reasoning_effort,
            "upstream_model": model, "system": system, "user": user,
            "history": history or [],
            "unsupported_sampling_controls": {
                "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p,
                "reasoning_max_tokens": reasoning_max_tokens,
                "upstream_reasoning_effort": reasoning_effort,
                "provider_order": provider_order,
                "provider_allow_fallbacks": provider_allow_fallbacks,
                "provider_require_parameters": provider_require_parameters,
            },
            "json_object_requested": json_object,
        }
        started = time.monotonic()
        session = None
        finished = threading.Event()
        watchdog = None
        try:
            # Images persist alongside the exact request; no screenshots leave this
            # server except as inputs to the explicitly selected Codex service.
            prompt, paths = prepare_transcript(system, user, history, image, call_dir)
            model_system = model_only_base_instructions(system)
            receipt["prompt"] = prompt
            # Persist the precise additive system envelope sent to Codex.  This
            # is audit evidence, not a replacement or mutation of upstream's
            # official role text retained in ``system`` above.
            receipt["model_system"] = model_system
            receipt["images"] = [path.name for path in paths]
            (call_dir / "request.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            session = self.session_factory(model_system, self.model, self.reasoning_effort, call_dir)
            def watch_budget():
                while not finished.wait(0.1):
                    if self.stop_event.is_set() or time.monotonic() >= self.deadline:
                        session.close()
                        return

            watchdog = threading.Thread(target=watch_budget, daemon=True)
            watchdog.start()
            answer = session.run_turn(
                prompt=prompt, image_path=paths[0] if paths else None,
                additional_image_paths=paths[1:], output_schema=None,
            )
            if self.stop_event.is_set() or time.monotonic() >= self.deadline:
                raise PracticeBudgetExceeded("Response discarded after stop or budget expiry")
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("Codex returned no assistant response; no action created")
            receipt.update(status="completed", answer=answer)
            return answer
        except Exception as error:
            if self.stop_event.is_set() or time.monotonic() >= self.deadline:
                stopped = PracticeBudgetExceeded("Practice stopped or wall-clock budget exhausted")
                receipt.update(status="error", error_type=type(stopped).__name__, error=str(stopped))
                raise stopped from error
            receipt.update(
                status="error", error_type=type(error).__name__, error=safe_codex_failure_text(error)
            )
            raise
        finally:
            finished.set()
            if watchdog is not None:
                watchdog.join(timeout=2)
            try:
                if session is not None:
                    session.close()
            finally:
                receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
                (call_dir / "result.json").write_text(
                    json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")


def install_upstream_transport(client: Any, transport: CodexRSITransport) -> None:
    """Call before importing Actor/Verifier modules (which bind chat on import)."""
    def chat(*args: Any, **kwargs: Any) -> str:
        client._LAST_REASONING.value = ""  # Codex does not expose private reasoning.
        try:
            return transport.chat(*args, **kwargs)
        except (PracticeBudgetExceeded, ValueError, TypeError):
            raise
        except Exception as error:
            # An isolation guard, malformed protocol, filesystem failure or
            # unknown RuntimeError is not evidence of a temporary network fault.
            # Fail closed; only concrete transport timeouts/disconnects may be
            # marked recoverable by upstream's infrastructure-error handling.
            category = classify_codex_authentication_failure(error)
            recoverable = category is None and isinstance(
                error, (CodexTurnTimeoutError, TimeoutError, ConnectionError)
            )
            detail = (
                f"Codex subscription transport: {CODEX_AUTHENTICATION_REQUIRED}; "
                "manual login required"
                if category is not None
                else f"Codex subscription transport: {type(error).__name__}"
            )
            raise client.LLMTransportError(
                transport.model, error, recoverable=recoverable,
                detail=detail,
            ) from error

    client.chat = chat
