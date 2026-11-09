"""Coordinate v7 guidance and execution through the shared execution core."""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path

from trace2task.application_knowledge import knowledge_prompt
from trace2task.chat_model_adapter import _intervention_input
from trace2task.execution_protocol import ActionPlan
from trace2task.hybrid_chat_executor import conversation_tokens, trim_api_history_images
from trace2task.hybrid_v7_profile import (
    CONTRACT_FIELDS,
    MULTITURN_NOTE,
    PROTOCOL,
    SUPERVISOR_PROMPT,
    director_schema,
    resolve_controller_config,
)


class HybridModelAdapter:
    def __init__(self, reviewer, *, local_model, inference_backend, root, stop,
                 record, status_callback, prompt_profile=None, predictor=None, chat_executor=None,
                 controller_config=None, application_knowledge=None):
        from trace2task.local_gui_client import predict_gui

        self.reviewer, self.session = reviewer, reviewer.session
        self.local_model, self.inference_backend = local_model, inference_backend
        self.root, self.stop = Path(root), stop
        self.record, self.status = record, status_callback
        self.chat_executor = chat_executor
        self.prompt_profile = prompt_profile
        self.controller_config = resolve_controller_config(controller_config)
        self.application_knowledge = application_knowledge
        self.predictor = chat_executor.predict if chat_executor is not None else predictor or predict_gui
        self.current = None
        self.stage_goals, self.executed_pairs = [], []
        self.executor_conversation_id = uuid.uuid4().hex
        self.executor_history_count = 0
        self.executor_started = False
        self.director_experience = None
        self.guide_calls = self.decisions = self.stage_decisions = 0
        self.stage_start_decisions = self.stage_start_pairs = 0
        self.stage_visual_changes = 0
        self.executor_report = None
        self.repetitions, self.last_key = 0, None
        self.same_action_count, self.last_action_key = 0, None
        self.pending = None
        self.revision = None
        self.last_feedback = None
        self.reason = 'task_start'
        self.needs_guide = True
        self.teacher_actions = []
        self.director_schema = director_schema(selected_windows=reviewer.selected_windows,
                                               android=reviewer.android)

    def _handoff(self, handback_reason, **output):
        reason = handback_reason
        self.reason, self.needs_guide = reason, True
        self.record('hybrid_handoff', protocol=PROTOCOL, reason=reason,
                    local_model=self.local_model)
        return {**output, 'status': 'handoff', 'reason': reason,
                'model_role': 'executor', 'actual_model': self.local_model}

    def _stop(self, reason):
        return {'status': 'hybrid_stop', 'reason': reason, 'model_role': 'director'}

    def _receipts(self, history, image_hash):
        """Retain only delivered actions, paired with their actual pre-action image."""
        if self.pending is None:
            return
        pending, self.pending = self.pending, None
        delivered = history[pending['history_count']:]
        if len(delivered) != 1 or delivered[0]['action']['actions'] != [pending['action']]:
            self.reason, self.needs_guide = 'execution_rejected', True
            return
        self.executed_pairs.append(pending)
        if image_hash != pending['image_sha256']:
            self.stage_visual_changes += 1
        key = (pending['image_sha256'], json.dumps(pending['action'], sort_keys=True))
        if image_hash == pending['image_sha256']:
            self.repetitions = self.repetitions + 1 if key == self.last_key else 1
        else:
            self.repetitions = 0
        self.last_key = key
        # Animated screenshots are not evidence that an input made progress.
        # Ask for review of repeated executor input even when image bytes change.
        if pending['model_role'] == 'executor' and pending['action']['skill'] != 'wait':
            action_key = key[1]
            self.same_action_count = self.same_action_count + 1 if action_key == self.last_action_key else 1
            self.last_action_key = action_key
        else:
            self.same_action_count, self.last_action_key = 0, None
        if self.repetitions >= self.controller_config['repeated_no_change_limit']:
            self.reason, self.needs_guide = 'repeated_action_without_visual_change', True
        elif self.same_action_count >= self.controller_config['repeated_no_change_limit']:
            self.reason, self.needs_guide = 'repeated_action_requires_review', True

    def predict(self, task, *, screenshot, history, context=None, experience_context=None,
                execution_feedback=None, previous_screenshot=None, on_request=None):
        self.stop.raise_if_requested()
        context = dict(context or {})
        if context.get('purpose') == 'verify_completion':
            # Local product has no OSWorld scorer. Preserve the existing fresh-image
            # visual review and identify it as an adaptation, never a benchmark score.
            return {**self.reviewer.predict(task, screenshot=screenshot, history=history,
                    context=context, experience_context=experience_context, on_request=on_request),
                    'model_role': 'director'}
        data = Path(screenshot).read_bytes()
        image_hash = hashlib.sha256(data).hexdigest()
        self._receipts(history, image_hash)
        revision = (context.get('human_intervention') or {}).get('revision')
        feedback = execution_feedback or context.get('execution_feedback') or {}
        if revision != self.revision:
            self.reason, self.needs_guide = 'human_intervention', True
            self.revision = revision
        if feedback != self.last_feedback and feedback.get('status') in {'stale', 'human_intervention'}:
            self.reason, self.needs_guide = feedback['status'], True
        self.last_feedback = feedback
        if self.decisions >= self.controller_config['max_decisions']:
            return self._stop('total_decision_budget_exhausted')
        if self.stage_decisions >= self.controller_config['review_interval'] and not self.needs_guide:
            self.reason, self.needs_guide = 'periodic_review', True
        if self.needs_guide:
            if self.teacher_actions:
                self.record('hybrid_teacher_actions_discarded', reason=self.reason, actions=self.teacher_actions)
                self.teacher_actions.clear()
            return self._direct(task, screenshot, context, experience_context, on_request, history)
        if self.teacher_actions:
            return self._teacher_action(screenshot, history)

        from trace2task.local_gui_client import cancel_gui
        from trace2task.trained_model_runner import interruptible_prediction

        instruction = self.current['goal']
        instruction += '\nCompletion: ' + self.current['completion_hint']
        if self.current['notices']:
            instruction += '\nNotices: ' + json.dumps(self.current['notices'], ensure_ascii=False)
        instruction += '\nFail conditions: ' + json.dumps(self.current['fail_conditions'], ensure_ascii=False)
        pairs = self.executed_pairs[self.executor_history_count:]
        replay = []
        for index, pair in enumerate(pairs):
            if self.chat_executor is not None and pair['model_role'] != 'director':
                continue
            item = {'raw_output': pair['raw_output']}
            if (pair['model_role'] == 'executor' and self.inference_backend != 'slotread'
                    and index >= len(pairs) - 2):
                image = Path(pair['image_path']).read_bytes()
                if hashlib.sha256(image).hexdigest() != pair['image_sha256']:
                    raise ValueError('Hybrid history image changed; no request sent')
                item['image'] = base64.b64encode(image).decode('ascii')
            replay.append(item)
        conversation = {}
        if self.chat_executor is None:
            conversation = {'conversation_id': self.executor_conversation_id,
                            'conversation_start': not self.executor_started,
                            'guided_history_offset': self.executor_history_count}
        if not self.executor_started:
            reference = knowledge_prompt(self.application_knowledge)
            experience = experience_context or self.director_experience
            if experience and experience.get('model_input'):
                reference += ('\n\nTask experience (historical methods, branches and completion checks; '
                              'apply only where relevant to the current subgoal and screenshot; '
                              'do not replay historical coordinates):\n' + experience['model_input'])
            if reference:
                # Reuse the first-turn reference channel supported by both local
                # GUI conversations and Codex/API executors, preserving caching.
                conversation['guided_knowledge'] = reference
        request_id = uuid.uuid4().hex
        if on_request:
            on_request()
        self.status(f'混合模型 v7 · {self.local_model}：{self.current["goal"]}')
        self.decisions += 1
        self.stage_decisions += 1
        output = interruptible_prediction(
            self.predictor, self.stop, instruction,
            on_cancel=self.chat_executor.close if self.chat_executor else lambda: cancel_gui(request_id),
            model=self.local_model, expected_backend=self.inference_backend,
            image=base64.b64encode(data).decode('ascii'), history=[],
            step_index=self.decisions - 1,
            execution_context={k: v for k, v in context.items()
                               if k not in {'human_intervention', 'execution_feedback'}},
            request_id=request_id, audit_dir=str(self.root / 'hybrid-local' / request_id),
            guided_history=replay, prompt_profile=self.prompt_profile, **conversation)
        self.stop.raise_if_requested()
        output = {**output, 'prompt': output.get('prompt', instruction), 'model_role': 'executor',
                  'actual_model': self.local_model, 'subtask': self.current,
                  'hybrid_protocol': PROTOCOL}
        if self.chat_executor is None:
            metrics = output.get('metrics', {})
            output['tokens'] = dict(metrics.get('tokens', {}))
            prefix = metrics.get('memory', {}).get('prefix_cache')
            if prefix is not None:
                output['tokens']['cached_tokens'] = prefix.get('reused_tokens')
        if output.get('status') in {'error', 'cancelled'}:
            raise RuntimeError(output.get('error') or 'Local inference failed; no automatic retry')
        self.executor_started = True
        self.executor_history_count += len(pairs)
        self.executor_report = output.get('stage_report')
        if output.get('status') == 'model_control':
            failed = output.get('control') in {'terminate_failure', 'call_user'}
            self.executor_report = {'status': 'failed' if failed else 'needs_guidance',
                                    'reason': str(output.get('text') or output.get('raw_output', ''))[:3000]}
            return self._handoff('executor_stage_failed' if failed else 'executor_stage_report', **output)
        if output.get('status') != 'predicted':
            return self._handoff('invalid_action', **output)
        try:
            plan = ActionPlan.from_prediction(output['prediction'])
        except (ValueError, TypeError, KeyError):
            return self._handoff('invalid_action', **output)
        if plan.done and not plan.actions:
            if not self.executor_report:
                self.executor_report = {'status': 'completion_reported',
                                        'reason': str(output.get('raw_output', ''))[:3000]}
            reason = 'executor_stage_failed' if self.executor_report['status'] == 'failed' else 'executor_stage_completed'
            return self._handoff(reason, **output)
        # The frozen protocol executes one decision/action at a time. Refuse a
        # batch rather than pairing its unexecuted suffix with a history image.
        if len(plan.actions) != 1 or plan.done or not output.get('raw_output'):
            return self._handoff('invalid_action', **output)
        self.pending = {'history_count': len(history), 'action': plan.actions[0].to_payload(),
                        'raw_output': output['raw_output'], 'image_path': str(screenshot),
                        'image_sha256': image_hash, 'model_role': 'executor'}
        return output

    def _teacher_action(self, screenshot, history, output=None):
        """Propose one action; only a later execution receipt makes it history."""
        self.stop.raise_if_requested()
        action = self.teacher_actions.pop(0)
        self.decisions += 1
        self.stage_decisions += 1
        raw = 'Executed guidance action (local GUI protocol): ' + json.dumps(action, ensure_ascii=False)
        self.pending = {'history_count': len(history), 'action': action, 'raw_output': raw,
                        'model_role': 'director', 'image_path': str(screenshot),
                        'image_sha256': hashlib.sha256(Path(screenshot).read_bytes()).hexdigest()}
        return {'raw_output': 'Proposed guidance action (local GUI protocol): ' + json.dumps(action, ensure_ascii=False),
                **(output or {}), 'status': 'predicted', 'prediction': {'actions': [action]},
                'model_role': 'director', 'subtask': self.current, 'hybrid_protocol': PROTOCOL}

    def _direct(self, task, screenshot, context, experience, on_request, history):
        if self.guide_calls >= self.controller_config['max_guide_calls']:
            return self._stop('guide_call_budget_exhausted')
        if self.guide_calls == 0:
            self.session.reset_thread()
        removed_images = trim_api_history_images(self.session)
        handoff = {'handoff_reason': self.reason}
        if self.current:
            pairs = self.executed_pairs[self.stage_start_pairs:]
            handoff['stage_feedback'] = {
                'decisions': self.decisions - self.stage_start_decisions,
                'delivered_actions': len(pairs),
                'director_actions': sum(p['model_role'] == 'director' for p in pairs),
                'executor_actions': sum(p['model_role'] == 'executor' for p in pairs),
                'actions_with_visual_change': self.stage_visual_changes,
                'visual_change_note': 'Screenshot bytes changed; animation alone can cause this. It is not proof of progress.',
                'repeated_no_change': self.repetitions,
                'consecutive_identical_executor_actions': self.same_action_count,
                'last_delivered_action': pairs[-1]['action'] if pairs else None,
                'executor_report': self.executor_report,
            }
        if self.guide_calls == 0:
            handoff.update(current_subgoal=self.current, prior_subgoals=[])
            prompt = SUPERVISOR_PROMPT + MULTITURN_NOTE + '\n'
            if self.reviewer.android:
                prompt += ('Android: single touch, authorized app only. click=tap; hold_mouse=long press; '
                           'drag=swipe; escape=BACK. No text entry.\n')
            prompt += 'Task: ' + task + '\nHandoff: ' + json.dumps(handoff, ensure_ascii=False)
            prompt += knowledge_prompt(self.application_knowledge)
        else:
            if self.reason == 'periodic_review':
                handoff['current_subgoal'] = self.current
            prompt = json.dumps(handoff, ensure_ascii=False)
        extra_prompt, paths = _intervention_input(context.get('human_intervention'), first_image=2)
        if extra_prompt:
            prompt += '\n' + extra_prompt
        if experience and (experience != self.director_experience or (removed_images and experience.get('images'))):
            if experience.get('model_input'):
                prompt += ('\nHistorical demonstration (advisory only; the current task and screenshot take '
                           'priority; do not replay old coordinates):\n' + experience['model_input'])
            if experience.get('images'):
                from trace2task.trace_evidence import image_bytes

                folder = self.root / 'hybrid-evidence'
                folder.mkdir(exist_ok=True)
                evidence_paths = []
                for index, data in enumerate(image_bytes(experience['images'])):
                    path = folder / f'{index}.png'
                    path.write_bytes(data)
                    evidence_paths.append(path)
                paths = (*paths, *evidence_paths)
                prompt += '\nFinal attachments are historical experience images, not current observations.'
        self.guide_calls += 1
        self.status(f'混合模型 v7 · 指导模型 {self.guide_calls}/{self.controller_config["max_guide_calls"]}')
        if on_request:
            on_request()
        raw = self.session.run_turn(prompt=prompt, image_path=Path(screenshot),
                                    output_schema=self.director_schema, additional_image_paths=paths)
        self.stop.raise_if_requested()
        self.director_experience = experience
        value = json.loads(raw)
        # Accept previous protocol replies while exposing only the new contract.
        if isinstance(value, dict) and 'handoff_conditions' in value and 'fail_conditions' not in value:
            value['fail_conditions'] = value.pop('handoff_conditions')
        if not isinstance(value, dict) or set(value) != {'decision', 'observation', 'actions', *CONTRACT_FIELDS}:
            raise ValueError('Invalid v7 guidance fields; teacher budgets are forbidden')
        actions = value['actions']
        if not isinstance(actions, list) or len(actions) > 12:
            raise ValueError('Guidance requires at most 12 GUI actions')
        if actions and value['decision'] not in {'stage', 'continue'}:
            raise ValueError('finish/blocked require actions=[]')
        supported = {item['properties']['skill']['enum'][0]
                     for item in self.director_schema['properties']['actions']['items']['anyOf']}
        normalized = []
        for action in actions:
            plan = ActionPlan.from_prediction({'actions': [action]})
            if plan.done or len(plan.actions) != 1 or plan.actions[0].skill not in supported:
                raise ValueError('Unsupported guidance action')
            normalized.append(plan.actions[0].to_payload())
        if value['decision'] not in {'stage', 'continue', 'finish', 'blocked'}:
            raise ValueError('Invalid v7 decision')
        if any(not isinstance(value[k], str) or not value[k].strip()
               for k in ('observation', 'goal', 'completion_hint')):
            raise ValueError('Nonempty v7 textual fields required')
        if any(not isinstance(value[k], list) or any(not isinstance(x, str) or not x.strip() for x in value[k])
               for k in ('notices', 'fail_conditions')):
            raise ValueError('Invalid guidance notices or fail conditions')
        contract = {k: value[k] for k in CONTRACT_FIELDS}
        decision = value['decision']
        if decision == 'continue':
            if self.reason != 'periodic_review' or self.current is None or contract != self.current:
                raise ValueError('continue requires an unchanged contract at periodic_review')
        elif decision == 'stage':
            self.current = contract
            self.stage_start_decisions = self.decisions
            self.stage_start_pairs = len(self.executed_pairs)
            self.stage_visual_changes = 0
            self.stage_goals.append(contract['goal'])
            self.record('hybrid_history_carryover', protocol=PROTOCOL,
                        retained_actions=len(self.executed_pairs),
                        previous_subgoals=self.stage_goals[:-1])
        self.record('hybrid_directive', protocol=PROTOCOL, directive=value, screenshot=str(screenshot))
        output = {'prompt': prompt, 'raw_output': raw, 'schema': self.director_schema,
                  'model_role': 'director', 'subtask': contract, 'hybrid_protocol': PROTOCOL,
                  'system_managed_externally': self.reviewer.provider == 'codex',
                  'tokens': conversation_tokens(self.session),
                  'context_management': {'removed_images': removed_images, 'removed_text_messages': 0},
                  'response_wait_extended': getattr(self.session, 'last_turn_wait_extended', False),
                  'rate_limit_wait_seconds': getattr(self.session, 'last_rate_limit_wait_seconds', 0)}
        if decision == 'finish':
            return {**output, 'status': 'predicted', 'prediction': ActionPlan((), True).to_payload()}
        if decision == 'blocked':
            return {**output, **self._stop('guide_blocked')}
        self.teacher_actions = normalized
        self.executor_report = None
        self.stage_decisions, self.repetitions, self.last_key = 0, 0, None
        self.same_action_count, self.last_action_key = 0, None
        self.needs_guide = False
        if self.teacher_actions:
            # Dispatch through the same core and observation as a normal model
            # action. An extra handoff/capture used to reject every animated frame.
            return self._teacher_action(screenshot, history, output)
        return {**output, 'status': 'handoff', 'reason': 'director_' + decision}
