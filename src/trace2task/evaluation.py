from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from trace2task.recording import make_run_dir
from trace2task.windows_runner import WindowsAgentRunFailed, run_windows_agent


@dataclass(frozen=True)
class EvaluationReset:
    reset_type: str
    options: dict[str, Any]
    suite_dir: Path


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    task_path: Path
    instruction: str | None
    repetitions: int
    reset: EvaluationReset


@dataclass(frozen=True)
class EvaluationSuite:
    suite_id: str
    cases: tuple[EvaluationCase, ...]
    source_path: Path


@dataclass(frozen=True)
class EvaluationResult:
    mode: str
    suite_id: str
    execute: bool
    total_attempts: int
    verified_attempts: int
    completed_attempts: int
    verified_rate: float
    completion_rate: float
    outcomes: dict[str, int]
    mean_elapsed_ms: float
    mean_planning_ms: float
    mean_model_turns: float
    mean_executed_actions: float
    run_dir: str
    attempts_path: str
    summary_path: str


class ResetAdapter(Protocol):
    def reset(self, config: EvaluationReset) -> dict[str, Any]: ...


class NoResetAdapter:
    def reset(self, config: EvaluationReset) -> dict[str, Any]:
        return {"status": "skipped", "type": config.reset_type, "elapsed_ms": 0.0}


class CommandResetAdapter:
    def reset(self, config: EvaluationReset) -> dict[str, Any]:
        raw_argv = config.options.get("argv")
        if (
            not isinstance(raw_argv, list)
            or not raw_argv
            or not all(isinstance(item, str) and item for item in raw_argv)
        ):
            raise ValueError("command reset requires a non-empty string list 'argv'")
        raw_timeout = config.options.get("timeout_seconds", 60)
        if not isinstance(raw_timeout, (int, float)) or isinstance(raw_timeout, bool):
            raise TypeError("command reset timeout_seconds must be a number")
        timeout_seconds = float(raw_timeout)
        if timeout_seconds <= 0:
            raise ValueError("command reset timeout_seconds must be positive")
        raw_cwd = config.options.get("cwd", ".")
        if not isinstance(raw_cwd, str) or not raw_cwd:
            raise ValueError("command reset cwd must be a non-empty path")
        cwd = (config.suite_dir / raw_cwd).resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"Evaluation reset cwd does not exist: {cwd}")
        started = time.perf_counter()
        completed = subprocess.run(
            raw_argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        result = {
            "status": "passed" if completed.returncode == 0 else "failed",
            "type": config.reset_type,
            "argv": raw_argv,
            "cwd": str(cwd),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4_000:],
            "stderr": completed.stderr[-4_000:],
            "elapsed_ms": round(elapsed_ms, 3),
        }
        if completed.returncode != 0:
            raise RuntimeError(
                f"Evaluation reset command exited with code {completed.returncode}"
            )
        return result


_RESET_ADAPTERS: dict[str, ResetAdapter] = {
    "none": NoResetAdapter(),
    "command": CommandResetAdapter(),
}


def register_reset_adapter(name: str, adapter: ResetAdapter) -> None:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Reset adapter name must not be empty")
    if normalized in _RESET_ADAPTERS:
        raise ValueError(f"Reset adapter is already registered: {normalized}")
    _RESET_ADAPTERS[normalized] = adapter


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"Evaluation suite field '{field}' must be a mapping")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Evaluation suite field '{field}' must be a non-empty string")
    return " ".join(value.split())


def _identifier(value: object, field: str) -> str:
    normalized = _string(value, field)
    if normalized in {".", ".."} or any(separator in normalized for separator in ("/", "\\")):
        raise ValueError(f"Evaluation suite field '{field}' must not contain path separators")
    if len(normalized) > 100:
        raise ValueError(f"Evaluation suite field '{field}' must not exceed 100 characters")
    return normalized


def load_evaluation_suite(path: Path) -> EvaluationSuite:
    source_path = path.expanduser().resolve()
    root = _mapping(yaml.safe_load(source_path.read_text(encoding="utf-8")), "root")
    if root.get("schema_version") != "0.1":
        raise ValueError("Evaluation suite schema_version must be '0.1'")
    suite_id = _identifier(root.get("id"), "id")
    raw_cases = root.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Evaluation suite cases must be a non-empty list")
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        case = _mapping(raw_case, f"cases[{index}]")
        case_id = _identifier(case.get("id"), f"cases[{index}].id")
        if case_id in seen_ids:
            raise ValueError(f"Evaluation suite contains duplicate case id: {case_id}")
        seen_ids.add(case_id)
        raw_task = _string(case.get("task"), f"cases[{index}].task")
        task_path = (source_path.parent / raw_task).resolve()
        if not task_path.is_file():
            raise FileNotFoundError(f"Evaluation task pack does not exist: {task_path}")
        instruction_value = case.get("instruction")
        instruction = (
            _string(instruction_value, f"cases[{index}].instruction")
            if instruction_value is not None
            else None
        )
        repetitions = case.get("repetitions", 1)
        if (
            not isinstance(repetitions, int)
            or isinstance(repetitions, bool)
            or not 1 <= repetitions <= 100
        ):
            raise ValueError("Evaluation case repetitions must be an integer between 1 and 100")
        reset_config = _mapping(case.get("reset", {"type": "none"}), f"cases[{index}].reset")
        reset_type = _string(reset_config.get("type"), f"cases[{index}].reset.type")
        if reset_type not in _RESET_ADAPTERS:
            raise ValueError(f"Unknown evaluation reset adapter: {reset_type}")
        cases.append(
            EvaluationCase(
                case_id=case_id,
                task_path=task_path,
                instruction=instruction,
                repetitions=repetitions,
                reset=EvaluationReset(
                    reset_type=reset_type,
                    options={key: value for key, value in reset_config.items() if key != "type"},
                    suite_dir=source_path.parent,
                ),
            )
        )
    return EvaluationSuite(suite_id=suite_id, cases=tuple(cases), source_path=source_path)


def _payload(result: object) -> dict[str, Any]:
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return dict(result)
    raise TypeError("Evaluation runner must return a dataclass or mapping")


def _mean(rows: list[dict[str, Any]], field: str) -> float:
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    return round(sum(values) / len(values), 3) if values else 0.0


EvaluationRunner = Callable[..., object]


def run_evaluation_suite(
    suite_path: Path,
    *,
    execute: bool = False,
    model: str = "gpt-5.6-terra",
    reasoning_effort: str = "low",
    output_root: Path = Path("evaluations"),
    runner: EvaluationRunner = run_windows_agent,
    status_callback: Callable[[str], None] = print,
) -> EvaluationResult:
    suite = load_evaluation_suite(suite_path)
    run_dir = make_run_dir(output_root, f"{suite.suite_id}-eval")
    run_dir.mkdir(parents=True, exist_ok=False)
    attempts_path = run_dir / "attempts.jsonl"
    summary_path = run_dir / "summary.json"
    rows: list[dict[str, Any]] = []
    attempt_index = 0
    for case in suite.cases:
        reset_adapter = _RESET_ADAPTERS[case.reset.reset_type]
        for repetition in range(1, case.repetitions + 1):
            attempt_index += 1
            status_callback(
                f"[eval {attempt_index}] Resetting {case.case_id} "
                f"for repetition {repetition}/{case.repetitions}..."
            )
            started = time.perf_counter()
            reset_result: dict[str, Any] | None = None
            try:
                reset_result = reset_adapter.reset(case.reset)
                attempt_output = run_dir / "agent-runs" / case.case_id / f"attempt-{repetition:03d}"
                status_callback(f"[eval {attempt_index}] Running {case.case_id}...")
                try:
                    raw_result = runner(
                        case.task_path,
                        instruction=case.instruction,
                        execute=execute,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        output_root=attempt_output,
                    )
                except WindowsAgentRunFailed as failure:
                    raw_result = failure.result
                result = _payload(raw_result)
                row = {
                    "attempt": attempt_index,
                    "case_id": case.case_id,
                    "repetition": repetition,
                    "reset": reset_result,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    **result,
                }
            except Exception as error:  # noqa: BLE001 - one failed attempt must not abort a suite
                row = {
                    "attempt": attempt_index,
                    "case_id": case.case_id,
                    "repetition": repetition,
                    "reset": reset_result,
                    "task_complete": False,
                    "verified": False,
                    "verification_outcome": "reset_or_runner_failed",
                    "stop_reason": f"failed:{type(error).__name__}",
                    "failure_message": str(error),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            rows.append(row)
            with attempts_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            status_callback(
                f"[eval {attempt_index}] {case.case_id}: "
                f"{row.get('verification_outcome', 'not_run')}."
            )

    outcomes = Counter(str(row.get("verification_outcome", "not_run")) for row in rows)
    verified_attempts = sum(row.get("verified") is True for row in rows)
    completed_attempts = sum(row.get("task_complete") is True for row in rows)
    total = len(rows)
    summary = EvaluationResult(
        mode="evaluation",
        suite_id=suite.suite_id,
        execute=execute,
        total_attempts=total,
        verified_attempts=verified_attempts,
        completed_attempts=completed_attempts,
        verified_rate=round(verified_attempts / total, 4),
        completion_rate=round(completed_attempts / total, 4),
        outcomes=dict(sorted(outcomes.items())),
        mean_elapsed_ms=_mean(rows, "elapsed_ms"),
        mean_planning_ms=_mean(rows, "planning_ms"),
        mean_model_turns=_mean(rows, "replans"),
        mean_executed_actions=_mean(rows, "executed_actions"),
        run_dir=str(run_dir),
        attempts_path=str(attempts_path),
        summary_path=str(summary_path),
    )
    summary_path.write_text(
        json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary
