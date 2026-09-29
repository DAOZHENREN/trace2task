"""Per-round live view of chat model requests and execution receipts."""

from __future__ import annotations

import json
import time
from pathlib import Path


class ChatRunAudit:
    def __init__(self, root, result, record, status, on_round=None):
        self.root, self.result = Path(root), result
        self.record, self.status, self.on_round = record, status, on_round
        result["model_io"] = []
        result["audit_path"] = str(self.root / "io-audit" / "events.jsonl")
        result["performance"] = {"capture_ms": 0.0, "model_roundtrip_ms": 0.0,
                                 "planning_ms": 0.0, "action_ms": 0.0,
                                 "explicit_wait_ms": 0.0}

    def elapsed(self, field, started):
        duration = round((time.perf_counter() - started) * 1000, 2)
        performance = self.result["performance"]
        performance[field] = round(performance.get(field, 0.0) + duration, 2)
        return duration

    def _publish(self, entry):
        path = self.root / "model-io.json"
        path.write_text(json.dumps(self.result["model_io"], ensure_ascii=False, indent=2), encoding="utf-8")
        if self.on_round is not None:
            self.on_round(entry)

    def predict(self, predictor, stop, model, task, screenshot, history, step, context=None,
                previous_screenshot=None, execution_feedback=None,
                experience_context=None, **_unused):
        entry = {"step_index": step, "status": "pending", "screenshot": str(screenshot),
                 "previous_screenshot": str(previous_screenshot) if previous_screenshot else None,
                 "purpose": (context or {}).get("purpose", "plan")}
        self.result["model_io"].append(entry)
        self._publish(entry)
        self.record("model_input", step_index=step, screenshot=str(screenshot),
                    previous_screenshot=entry["previous_screenshot"],
                    history=history, context=context, experience_context=experience_context,
                    execution_feedback=execution_feedback)
        self.status(f"[plan {step + 1}] {model} 正在返回动作；可随时停止后续输入。")
        started = time.perf_counter()
        try:
            stop.raise_if_requested()
            from trace2task.trained_model_runner import interruptible_prediction

            output = interruptible_prediction(
                predictor, stop, task,
                on_progress=lambda seconds: self.status(
                    f"[plan {step + 1}] 模型已等待 {seconds:.0f} 秒；可停止，迟到回答不会执行。"),
                screenshot=screenshot, history=history, context=context,
                previous_screenshot=previous_screenshot,
                experience_context=experience_context,
                execution_feedback=execution_feedback,
            )
            stop.raise_if_requested()
            entry.update(status=output["status"], prompt=output.get("prompt"),
                         input={"messages": output.get("input_messages") or
                                           [{"role": "user", "content": output.get("prompt", "")}],
                                "output_schema": output.get("schema"),
                                "full_wire_audit": self.result["audit_path"]},
                         system_managed_externally=output.get("system_managed_externally", False),
                         output_schema=output.get("schema"), raw_output=output.get("raw_output"),
                         prediction=output.get("prediction"), verification=output.get("verification"))
            if output.get("error"):
                entry["error"] = output["error"]
            self.record("model_output", step_index=step, output={
                key: value for key, value in output.items()
                if key not in {"prompt", "schema", "input_messages"}},
                prompt=output.get("prompt"), output_schema=output.get("schema"))
            return output
        except Exception as error:
            entry.update(status="error", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            entry["model_roundtrip_ms"] = self.elapsed("model_roundtrip_ms", started)
            self.result["performance"]["planning_ms"] = self.result["performance"]["model_roundtrip_ms"]
            self._publish(entry)
            self.status(f"[plan {step + 1}] {entry['status']} · 模型 {entry['model_roundtrip_ms']/1000:.2f} 秒")

    def execution(self, value):
        if not self.result["model_io"]:
            return
        entry = self.result["model_io"][-1]
        entry["execution"] = {"phase": "execution", **value}
        self._publish(entry)

    def finish(self, seconds):
        self.result["performance"]["total_elapsed_ms"] = round(seconds * 1000, 2)
        if self.result["model_io"]:
            self._publish(self.result["model_io"][-1])
