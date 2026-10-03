"""Strict execution boundary: no model parsing, native input, or automatic retries."""
import json
import time
from dataclasses import dataclass
from typing import Protocol

from trace2task.execution_protocol import (
    COORDINATE_SPACE,
    PROTOCOL_VERSION,
    ActionPlan,
    ActionUnavailable,
    DriverRefusal,
    ForegroundUnavailable,
    ObservationStale,
    PostActionReobserve,
)


class ExecutionBackend(Protocol):
    default_delivery_mode: str

    def authorize(self, action, target): ...
    def prepare(self, action, state, execution_context=None): ...
    def check_current(self, target, state): ...
    def dispatch(self, action, prepared, target, stop): ...
    def retry_foreground(self, action, prepared, target, stop): ...
    def capabilities(self, state): ...


@dataclass
class ExecutionResult:
    status: str
    target: dict
    action: dict | None = None
    receipt: dict | None = None
    reason: str | None = None
    delivery_mode: str | None = None
    background_refusal: dict | None = None
    steps: tuple[dict, ...] = ()


def validate_delivery_receipt(receipt):
    """Validate a backend receipt without treating delivery as task success."""
    if isinstance(receipt, dict) and receipt.get('code') == 'background_unavailable':
        raise DriverRefusal(receipt)
    if isinstance(receipt, dict) and (receipt.get('code') or receipt.get('error')
            or receipt.get('isError') or receipt.get('refusal')
            or receipt.get('status') in ('error', 'failed', 'refused')):
        raise RuntimeError('执行后端非成功回执；未确认送达：' + json.dumps(receipt, ensure_ascii=False))
    if not isinstance(receipt, dict) or receipt.get('effect') not in ('confirmed', 'unverifiable'):
        raise RuntimeError('执行后端动作效果未知；不重试：' + json.dumps(receipt, ensure_ascii=False))


def dispatch_backend_once(backend, action, prepared, target, stop):
    """Send one prepared action through a backend; never retry an uncertain call."""
    stop.raise_if_requested()
    receipt, next_target = backend.dispatch(action, prepared, target, stop)
    validate_delivery_receipt(receipt)
    return receipt, next_target


class ExecutionCore:
    def __init__(self, backend: ExecutionBackend, stop, record, *, max_actions=40):
        self.backend, self.stop, self.record = backend, stop, record
        self.max_actions = max_actions
        self.attempts = self.rejections = 0
        self.consumed = set()
        self.feedback = None
        self.stopped = False
        self.last_steps = []
        self.last_plan = None
        self.last_requests = []

    def execute(self, prediction, *, target, observation_id, state, execution_context=None):
        if self.stopped:
            raise RuntimeError('Execution core is stopped; create a new explicitly authorized run')
        try:
            result = self._execute(prediction, target=target, observation_id=observation_id,
                                   state=state, execution_context=execution_context)
        except Exception:
            self.stopped = True
            raise
        if result.status not in ('delivered', 'rejected', 'reobserve', 'completion_requested'):
            self.stopped = True
            self.record('execution_stopped', status=result.status, reason=result.reason,
                        observation_id=observation_id)
        return result

    def _execute(self, prediction, *, target, observation_id, state, execution_context=None):
        self.stop.raise_if_requested()
        self.last_steps = []
        self.last_plan = None
        self.last_requests = []
        execution_context = dict(execution_context or {})
        # Reparse at the execution boundary even if the adapter already validated it.
        plan = ActionPlan.from_prediction(prediction)
        self.last_plan = plan.to_payload()
        if not isinstance(observation_id, str) or not observation_id or observation_id in self.consumed:
            raise ValueError('Observation already consumed or invalid; no input sent')
        self.consumed.add(observation_id)
        self.record('execution_plan', protocol_version=PROTOCOL_VERSION,
                    coordinate_space=COORDINATE_SPACE, observation_id=observation_id,
                    target=target, actions=[a.to_payload() for a in plan.actions], done=plan.done)
        if not plan.actions:
            return ExecutionResult('completion_requested', target)
        # Reject an unauthorized later action before sending the first one.
        for action in plan.actions:
            self.backend.authorize(action, target)
        for index, action in enumerate(plan.actions, start=1):
            payload = action.to_payload()
            if self.attempts >= self.max_actions:
                return ExecutionResult('action_limit', target, payload,
                                       steps=tuple(self.last_steps))
            try:
                self.backend.check_current(target, state)
            except ObservationStale as error:
                self.feedback = {'status': 'reobserve', 'executed': False, 'reason': str(error),
                                 'instruction': 'This action was not sent. Replan from a fresh screenshot.'}
                self.record('execution_result', observation_id=observation_id, action=payload,
                            batch_action_index=index, **self.feedback)
                return ExecutionResult('reobserve', target, payload, reason=str(error),
                                       steps=tuple(self.last_steps))
            try:
                prepared = self.backend.prepare(action, state, execution_context)
            except ActionUnavailable as error:
                self.rejections += 1
                self.feedback = {'status': 'rejected', 'executed': False, 'reason': str(error),
                                 'action': payload, 'instruction':
                                 'This action was not sent. Replan from the new screenshot.'}
                self.record('execution_result', observation_id=observation_id,
                            batch_action_index=index, **self.feedback)
                status = 'capability_rejection_limit' if self.rejections >= 3 else 'rejected'
                return ExecutionResult(status, target, payload, reason=str(error),
                                       steps=tuple(self.last_steps))
            self.stop.raise_if_requested()
            self.attempts += 1
            request = self._request(action, prepared, target, index)
            self.record('dispatch', action=payload, observation_id=observation_id,
                        batch_action_index=index, target=target, executor_request=request['request'])
            background_refusal = None
            retry_request = None
            started = time.perf_counter()
            try:
                receipt, next_target = dispatch_backend_once(
                    self.backend, action, prepared, target, self.stop,
                )
            except DriverRefusal as error:
                request['status'] = 'refused'
                background_refusal = error.receipt
                self.record('background_refused', observation_id=observation_id, action=payload,
                            batch_action_index=index, target=target,
                            receipt=background_refusal, executed=False)
                try:
                    self.stop.raise_if_requested()
                    self.backend.check_current(target, state)
                    self.stop.raise_if_requested()
                    if getattr(self.backend, 'allow_foreground_retry', False):
                        self.record('foreground_retry', observation_id=observation_id, action=payload,
                                    target=target, reason='background_unavailable')
                    retry_request = self._request(action, prepared, target, index,
                                                  delivery_mode='foreground')
                    self.record('dispatch', action=payload, observation_id=observation_id,
                                batch_action_index=index, target=target,
                                executor_request=retry_request['request'],
                                retry='explicit_background_refusal')
                    receipt, next_target = self.backend.retry_foreground(
                        action, prepared, target, self.stop)
                    validate_delivery_receipt(receipt)
                except ObservationStale as retry_error:
                    self.feedback = {'status': 'reobserve', 'executed': False,
                                     'reason': str(retry_error), 'instruction':
                                     'No input was sent after explicit background refusal. Re-observe before planning.'}
                    self.record('execution_result', observation_id=observation_id, action=payload,
                                batch_action_index=index, background_refusal=background_refusal,
                                **self.feedback)
                    return ExecutionResult('reobserve', target, payload, reason=str(retry_error),
                                           background_refusal=background_refusal,
                                           steps=tuple(self.last_steps))
                except ForegroundUnavailable as retry_error:
                    if retry_request is not None:
                        retry_request['status'] = 'not_sent'
                    self.record('execution_result', observation_id=observation_id, action=payload,
                                batch_action_index=index, status='foreground_unavailable',
                                executed=False, background_refusal=background_refusal,
                                delivery_mode_requested=('foreground' if getattr(
                                    self.backend, 'allow_foreground_retry', False) else 'background'),
                                error=str(retry_error))
                    raise
                except Exception as retry_error:
                    if retry_request is not None:
                        retry_request['status'] = 'unknown'
                    # A foreground call may have taken effect before an error or timeout.
                    self.record('execution_result', observation_id=observation_id, action=payload,
                                batch_action_index=index, status='interrupted_or_unknown',
                                executed=None, background_refusal=background_refusal,
                                delivery_mode_requested='foreground', error=str(retry_error))
                    raise
            except PostActionReobserve as error:
                request['status'] = 'unknown'
                self.feedback = {'status': 'reobserve', 'executed': None,
                                 'reason': str(error), 'action': payload,
                                 'instruction': 'The click may already have taken effect and changed the foreground app. '
                                 'Inspect the new screenshot before planning; do not blindly repeat the click.'}
                self.record('execution_result', observation_id=observation_id,
                            batch_action_index=index, **self.feedback)
                self.record('batch_boundary', observation_id=observation_id,
                            reason='post_click_foreground_changed', remaining=len(plan.actions)-index)
                return ExecutionResult('reobserve', target, payload, reason=str(error),
                                       steps=tuple(self.last_steps))
            except Exception as error:
                request['status'] = 'unknown'
                # An admitted call may already have had an effect. Never retry it here.
                self.record('execution_result', observation_id=observation_id, action=payload,
                            batch_action_index=index, status='interrupted_or_unknown',
                            executed=None, error=str(error))
                raise
            self.rejections = 0
            self.feedback = None
            (retry_request if background_refusal is not None else request)['status'] = 'delivered'
            delivery_mode = self._delivery_mode(receipt, background_refusal)
            self.record('execution_result', observation_id=observation_id, action=payload,
                        batch_action_index=index, status='delivered', executed=True,
                        effect=receipt['effect'], receipt=receipt,
                        delivery_mode_requested=delivery_mode,
                        background_refusal=background_refusal)
            self.last_steps.append({'action': payload, 'receipt': receipt,
                                    'target': next_target, 'delivery_mode': delivery_mode,
                                    'background_refusal': background_refusal,
                                    'elapsed_ms': round((time.perf_counter()-started)*1000, 2)})
            if action.skill in ('click', 'double_click', 'move_cursor'):
                execution_context['cursor_position'] = [action.args['x'], action.args['y']]
            elif action.skill == 'drag':
                execution_context['cursor_position'] = [action.args['end_x'], action.args['end_y']]
            elif action.skill in ('switch_window', 'launch_app'):
                execution_context.pop('cursor_position', None)
            target = next_target
            if action.skill in ('switch_window', 'launch_app') and index < len(plan.actions):
                self.feedback = {'status': 'reobserve', 'executed': False,
                                 'reason': 'Action changed the coordinate target; remaining actions need a new screenshot.'}
                self.record('batch_boundary', observation_id=observation_id,
                            reason='target_changed', remaining=len(plan.actions)-index)
                return ExecutionResult('reobserve', target, payload, receipt,
                                       reason=self.feedback['reason'],
                                       delivery_mode=delivery_mode,
                                       background_refusal=background_refusal,
                                       steps=tuple(self.last_steps))
        status = 'completion_requested' if plan.done else 'delivered'
        return ExecutionResult(status, target, payload, receipt,
                               delivery_mode=delivery_mode,
                               background_refusal=background_refusal,
                               steps=tuple(self.last_steps))

    def _delivery_mode(self, receipt, background_refusal):
        if background_refusal is not None:
            return 'foreground'
        delivery = receipt.get('delivery') if isinstance(receipt, dict) else None
        mode = delivery.get('mode') if isinstance(delivery, dict) else None
        return mode or self.backend.default_delivery_mode

    def _request(self, action, prepared, target, index, *, delivery_mode=None):
        describe = getattr(self.backend, 'request_view', None)
        view = (describe(action, prepared, target, delivery_mode=delivery_mode)
                if callable(describe) else {'backend': type(self.backend).__name__,
                                           'action': action.to_payload()})
        request = {'action_index': index, 'request': view, 'status': 'attempted'}
        self.last_requests.append(request)
        return request
