"""Shared observe-plan-execute-reobserve loop for local GUI models.

This module owns no native input and no model-specific decoding.  Observers bind
their own target/screenshot scope, model adapters return the versioned unified
action plan, and :class:`ExecutionCore` is the only execution boundary.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from trace2task.execution_protocol import ActionPlan


def _delivered_cursor(history):
    """Last pointer position from delivered actions in the current target only."""
    for entry in reversed(history):
        if not entry.get("executed"):
            continue
        actions = entry.get("action", {}).get("actions", [])
        if not actions:
            continue
        action = actions[-1]
        skill, args = action.get("skill"), action.get("args", {})
        if skill in {"switch_window", "launch_app"}:
            return None
        if skill == "drag":
            return [args["end_x"], args["end_y"]]
        if skill in {"move_cursor", "click", "double_click"} or (
            skill in {"scroll", "type_text"} and {"x", "y"} <= set(args)
        ):
            return [args["x"], args["y"]]
    return None


def _execution_audit(execution, elapsed_ms, core):
    """A backend-neutral execution record attached to the current model round."""
    return {
        "status": execution.status,
        "target": execution.target,
        "action": execution.action,
        "receipt": execution.receipt,
        "reason": execution.reason,
        "delivery_mode_requested": execution.delivery_mode,
        "background_refusal": execution.background_refusal,
        "executed": (None if execution.status == 'reobserve' and core.feedback
                     and core.feedback.get('executed') is None else execution.status == "delivered"),
        "effect": execution.receipt.get("effect") if execution.receipt else None,
        "elapsed_ms": elapsed_ms,
        "steps": list(execution.steps),
        "normalized_plan": getattr(core, "last_plan", None),
        "executor_requests": [dict(item) for item in getattr(core, "last_requests", ())],
    }


def _record_delivered_steps(steps, *, result, record, status_callback, batch_index):
    """Keep every delivered action, including the prefix of an interrupted batch."""
    for index, item in enumerate(steps, start=1):
        raw, receipt = item["action"], item["receipt"]
        action_index = result.get("actions", 0)
        result["actions"] = action_index + 1
        history_item = {
            "step_index": action_index,
            "action": {"actions": [raw]},
            "executed": True,
            "effect": receipt["effect"],
            "delivery_mode_requested": item["delivery_mode"],
        }
        if receipt.get("adapter_advisory") == "delivery_path_warning":
            history_item["delivery_advisory"] = "delivery_path_warning"
        result["history"].append(history_item)
        record("delivered", action=raw, receipt=receipt,
               elapsed_ms=item["elapsed_ms"], batch_index=batch_index,
               batch_action_index=index,
               delivery_mode_requested=item["delivery_mode"],
               background_refusal=item["background_refusal"])
        if item["background_refusal"] is not None:
            status_callback("目标明确拒绝后台输入；驱动已对同一窗口短暂切前台尝试一次，请留意焦点切换。")
        if receipt["effect"] == "unverifiable":
            record("effect_pending", step_index=action_index, action=raw, receipt=receipt)
        status_callback(
            f"[action {result['actions']}] {raw['skill']} · {item['elapsed_ms']:.0f} ms · "
            f"{receipt['effect']}；本批次后重新观察。"
        )


def _completion_review(*, observer, target, model, core, audit, predictor, instruction,
                       history, record, status_callback, experience_context=None,
                       prompt_profile=None):
    """Ask the same model to inspect a fresh screenshot without sending input."""
    review_index = len(audit.result["model_io"])
    started = time.perf_counter()
    _, _, review_path = observer.observe(target, review_index)
    elapsed_ms = audit.elapsed("capture_ms", started)
    record("completion_review_capture", screenshot=str(review_path), elapsed_ms=elapsed_ms)
    review = audit.predict(
        predictor,
        core.stop,
        model,
        instruction,
        review_path,
        history,
        review_index,
        {"purpose": "verify_completion"},
        experience_context=experience_context,
        prompt_profile=prompt_profile,
    )
    verification = review.get("verification") if review.get("status") == "reviewed" else None
    from trace2task.local_gui_protocol import decode_completion_review

    try:
        verification = decode_completion_review(json.dumps(verification))
    except (TypeError, ValueError):
        verification = {
            "verdict": "unknown",
            "evidence": "核验未返回合法的证据结果",
            "missing": review.get("error", "请人工检查最新截图"),
        }
    receipt = {
        **verification,
        "source": "same_model_visual_review",
        "independent": False,
        "screenshot": str(review_path),
        "model": model,
        "model_io_index": review_index,
    }
    record("completion_review", **receipt)
    status_callback(
        f"完成核验：{verification['verdict']} · {verification['evidence']}"
        + (f" · 尚缺：{verification['missing']}" if verification["missing"] else "")
    )
    return receipt


def run_local_agent_loop(
    *,
    observer,
    initial_target,
    core,
    audit,
    predictor,
    instruction,
    model,
    root,
    result,
    record,
    stop,
    status_callback,
    max_actions=40,
    approve=None,
    continuous=True,
    experience_context=None,
    prompt_profile=None,
):
    """Run an evidence-preserving local GUI-agent loop.

    ``observer.observe(target, index)`` returns ``(target, state, screenshot)``.
    ``observer.context(target, state, backend)`` returns trusted raw
    scope/capability information. The model adapter projects it into the
    model's native action space; ``observer`` is trusted host
    code; neither the model nor an action plan selects its initial scope.

    A delivered action is never automatically replayed. One model plan may
    contain several actions; ordinary UI changes do not cancel the batch.
    Each action still checks the bound target and stop request before dispatch.
    """
    if experience_context is not None and model == "D-5970":
        raise ValueError(
            "D-5970 的冻结输入记录不支持经验上下文；未将经验混入任务文字"
        )
    if experience_context is not None and experience_context.get("kind") != "trace_sequence":
        raise ValueError("执行仅支持 A 原始证据 / D 精简序列，不再使用状态图经验")

    target = initial_target
    previous = None
    completion_rejections = 0
    adapter_rejections = 0
    format_rejections = 0
    stale_reobservations = 0
    root_name = Path(root).name

    for step in range(max_actions):
        stop.raise_if_requested()
        if result.get("actions", 0) >= max_actions:
            break

        observation_index = len(result["model_io"])
        capture_started = time.perf_counter()
        target, state, path = observer.observe(target, observation_index)
        capture_ms = audit.elapsed("capture_ms", capture_started)
        record("capture", step_index=step, screenshot=str(path), elapsed_ms=capture_ms)

        previous_path = previous[1] if previous and previous[0] == target else None
        history = result["history"]
        cursor_position = _delivered_cursor(history) if previous_path is not None else None
        if history and history[-1].get("effect") == "unverifiable":
            record(
                "effect_reobservation",
                source_step_index=history[-1]["step_index"],
                step_index=step,
                screenshot=str(path),
                effect="unverifiable",
            )
            status_callback("已重新截图；上一动作效果待确认，由模型根据新画面规划，不自动重发。")

        context = observer.context(target, state, core.backend) if model != "D-5970" else None
        if context is not None:
            context = dict(context)
            context["execution_feedback"] = core.feedback
        turn_experience = experience_context
        output = audit.predict(
            predictor,
            stop,
            model,
            instruction,
            path,
            history[-4:] if model == "D-5970" else history,
            observation_index,
            context,
            execution_feedback=core.feedback if context is None else None,
            experience_context=turn_experience if model != "D-5970" else None,
            prompt_profile=prompt_profile if model != "D-5970" else None,
            cursor_position=cursor_position if model != "D-5970" else None,
        )
        if output.get("status") == "adapter_rejected":
            format_rejections = 0
            adapter_rejections += 1
            reason = output.get("error", "模型动作不能无损映射到当前后端")
            record("adapter_rejected", step_index=step, reason=reason, executed=False)
            if adapter_rejections >= 3:
                result["stop_reason"] = "adapter_rejection_limit"
                result["error"] = reason
                status_callback(f"模型连续三次请求当前后端无法实现的动作，已停止：{reason}")
                break
            core.feedback = {"status": "adapter_rejected", "executed": False,
                             "reason": reason,
                             "instruction": "No input was sent. Choose a different action from the fresh screenshot."}
            status_callback(f"适配器未发送动作：{reason}；重新截图交给模型选择。")
            continue
        if output.get("status") == "format_rejected":
            adapter_rejections = 0
            format_rejections += 1
            reason = output.get("error", "模型回答不符合动作格式")
            record("format_rejected", step_index=step, reason=reason, executed=False,
                   retry_index=format_rejections)
            if format_rejections >= 3:
                result["stop_reason"] = "format_rejection_limit"
                result["error"] = reason
                status_callback(f"模型连续三次返回无效动作格式，已停止且未发送这些动作：{reason}")
                break
            core.feedback = {
                "status": "format_rejected", "executed": False, "reason": reason,
                "instruction": (
                    "No input was sent. Regenerate a valid action plan "
                    "in your native output format from the fresh screenshot. "
                    "Do not repeat the malformed output or add extra tool calls."
                ),
            }
            status_callback(f"模型动作格式无效，未执行；重新截图并请求模型纠正（{format_rejections}/2）：{reason}")
            continue
        if output.get("status") == "model_control":
            control = output["control"]
            result["stop_reason"] = {
                "interact": "model_requests_user", "answer": "model_answered",
                "terminate_failure": "model_declared_failure",
            }[control]
            result["model_message"] = output["text"]
            record("model_control", step_index=step, control=control,
                   text=output["text"], executed=False)
            status_callback(f"模型结束本轮操作（{control}）：{output['text']}")
            break
        if output.get("status") != "predicted":
            raise RuntimeError(output.get("error", "模型未返回有效计划"))
        adapter_rejections = 0
        format_rejections = 0

        plan = ActionPlan.from_prediction(output["prediction"])
        if plan.actions and approve is not None and not continuous:
            approve(
                {
                    "actions": [action.to_payload() for action in plan.actions],
                    "discarded_count": 0,
                    "annotated_image": output.get("annotated_image"),
                    "inference_seconds": output.get("elapsed_seconds"),
                    "prediction_log": output.get("output_directory"),
                    "step": step,
                }
            )
            status_callback("已确认；3 秒内切回目标窗口，随后重新检查目标状态。")
            stop.sleep(3)

        action_started = time.perf_counter()
        try:
            execution = core.execute(
                output["prediction"],
                target=target,
                observation_id=f"{root_name}:{step}",
                state=state,
                execution_context={"cursor_position": cursor_position},
            )
        except Exception as error:
            partial_steps = getattr(core, "last_steps", ())
            elapsed_ms = audit.elapsed("action_ms", action_started)
            # The core may already have published a more precise no-input or
            # uncertain-delivery result; keep that classification intact.
            prior = result["model_io"][-1].get("execution", {}) if result["model_io"] else {}
            audit.execution({"status": "interrupted_or_unknown", "executed": None,
                             "steps": list(partial_steps), "elapsed_ms": elapsed_ms,
                             "normalized_plan": getattr(core, "last_plan", None),
                             "executor_requests": [dict(item) for item in getattr(core, "last_requests", ())],
                             "error": f"{type(error).__name__}: {error}", **prior})
            _record_delivered_steps(partial_steps, result=result, record=record,
                                    status_callback=status_callback, batch_index=step)
            raise
        elapsed_ms = audit.elapsed(
            "explicit_wait_ms" if execution.steps and all(
                item["action"]["skill"] == "wait" for item in execution.steps)
            else "action_ms",
            action_started,
        )
        audit.execution(_execution_audit(execution, elapsed_ms, core))
        steps = execution.steps
        if not steps and execution.status == "delivered" and execution.action:
            # Compatibility with existing single-action callers of the loop.
            steps = ({"action": execution.action, "receipt": execution.receipt,
                      "target": execution.target, "delivery_mode": execution.delivery_mode,
                      "background_refusal": execution.background_refusal,
                      "elapsed_ms": elapsed_ms},)
        _record_delivered_steps(steps, result=result, record=record,
                                status_callback=status_callback, batch_index=step)
        if steps:
            target = steps[-1]["target"]
            raw = steps[-1]["action"]
            previous = ((dict(target), path) if raw["skill"] not in
                        ("switch_window", "launch_app") else None)
            stale_reobservations = 0

        if execution.status == "completion_requested":
            if model == "D-5970":
                result["stop_reason"] = "completion_unverifiable"
                status_callback("结构化动作模型尚不支持只读完成核验；已停止，任务未确认成功。")
                break
            status_callback("模型声明完成；正在重新截图进行只读核验，不发送任何动作。")
            receipt = _completion_review(
                observer=observer,
                target=target,
                model=model,
                core=core,
                audit=audit,
                predictor=predictor,
                instruction=instruction,
                history=history,
                record=record,
                status_callback=status_callback,
                experience_context=turn_experience,
                prompt_profile=prompt_profile,
            )
            result.setdefault("completion_reviews", []).append(receipt)
            verification = receipt
            if verification["verdict"] == "complete":
                result.update(
                    task_complete=True,
                    verified=False,
                    stop_reason="visual_completion_reviewed",
                    verification_source="same_model_visual_review",
                )
                break
            if verification["verdict"] == "unknown":
                result["stop_reason"] = "completion_unverifiable"
                status_callback("完成证据不足；已停止等待人工检查，不自动重复可能已提交的操作。")
                break
            completion_rejections += 1
            if completion_rejections >= 2:
                result["stop_reason"] = "completion_rejected"
                status_callback("连续两次完成声明未通过核验；已停止，任务未完成。")
                break
            core.feedback = {
                "status": "completion_rejected",
                "executed": False,
                "evidence": verification["evidence"],
                "missing": verification["missing"],
                "instruction": (
                    "Completion was rejected by a read-only fresh screenshot review. "
                    "Reassess and address the missing result. Do not repeat delivered submissions "
                    "or claim success without visible evidence. The review sent NO actions."
                ),
            }
            status_callback("完成声明未通过；将缺失结果交给模型重新规划。")
            continue

        if execution.status == "reobserve":
            if core.feedback and core.feedback.get('executed') is None:
                record('observation_changed', step_index=step, reason=execution.reason,
                       executed=None)
                status_callback('点击后前台应用已切换，动作效果待观察；不重试点击，重新截图规划。')
                continue
            if steps:
                status_callback("批次已执行前面的动作；目标或坐标绑定改变，剩余动作未发送，重新截图规划。")
                continue
            stale_reobservations += 1
            record("observation_changed", step_index=step, reason=execution.reason,
                   executed=False)
            if stale_reobservations >= 3:
                result["stop_reason"] = "observation_changed_limit"
                status_callback("前台窗口或分辨率连续变化，已停止；没有执行旧坐标动作。")
                break
            status_callback("前台窗口或分辨率已变化；旧动作未执行，重新截图规划。")
            continue
        if execution.status == "rejected":
            status_callback(f"当前动作未执行：{execution.reason}；正在重新截图，让模型改用可用操作。")
            continue
        if execution.status != "delivered":
            result["stop_reason"] = execution.status
            status_callback(
                f"任务停止：{execution.status}"
                + (f" · {execution.reason}" if execution.reason else "")
            )
            break

        stop.sleep(0.3)

    return result
