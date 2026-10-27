"""Bounded, explicit human handoffs for the Android chat runner.

The API only queues requests. The single execution thread acknowledges a handoff
after its current admitted action has returned. Model replies never own input.
"""
from __future__ import annotations

import copy
import re
import threading
from datetime import UTC, datetime

MAX_COMMANDS = 32
MAX_TAKEOVERS = 8
MAX_TEXT = 2000


def now():
    return datetime.now(UTC).isoformat(timespec='milliseconds')


class RunInterventions:
    def __init__(self, on_change=None):
        self.lock = threading.RLock()
        self.on_change = on_change or (lambda state: None)
        self.record = None
        self.directory = None
        self.state = {'supported': True, 'visual_supported': True, 'phase': 'starting', 'revision': 0, 'version': 0,
                      'assisted_by_user': False, 'commands': [], 'takeovers': [],
                      'message': '正在准备设备与经验；准备完成后可以干预。'}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def _publish(self):
        # Callers hold this lock. Console callbacks must never acquire this lock
        # while holding their own job lock (including when taking a snapshot).
        self.state['version'] += 1
        value = self.snapshot()
        if self.record:
            self.record('human_intervention', **value)
        self.on_change(value)

    def activate(self, record, *, directory=None):
        with self.lock:
            if self.state['phase'] != 'starting':
                raise RuntimeError('干预会话不能重新启动')
            self.record = record
            self.directory = directory
            self.state.update(phase='running', message='可以追加指令，或先暂停再接管手机。')
            self._publish()

    def request(self, *, request_id, kind, text='', visual=None):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
            raise ValueError('干预请求缺少有效的唯一标识')
        if kind not in {'instruction', 'pause', 'resume'}:
            raise ValueError('不支持的干预操作')
        if not isinstance(text, str) or len(text) > MAX_TEXT or '\x00' in text:
            raise ValueError(f'干预说明必须是最多 {MAX_TEXT} 字的文本')
        text = text.strip()
        if kind == 'instruction' and not text:
            raise ValueError('请输入补充指令')
        visual_digest = None
        if visual is not None:
            from trace2task.intervention_images import validate_visual
            if kind != 'resume':
                raise ValueError('请先暂停，再把截图与说明一起交还模型')
            visual = copy.deepcopy(visual)
            visual_digest = validate_visual(visual)
        with self.lock:
            for previous in self.state['commands']:
                if previous['id'] == request_id:
                    if (previous['kind'], previous['text'], previous.get('visual_digest')) != (kind, text, visual_digest):
                        raise ValueError('相同请求标识不能提交不同内容')
                    return self.snapshot()
            phase = self.state['phase']
            if phase in {'starting', 'closed'}:
                raise RuntimeError('任务尚未准备好或已经结束，不能提交干预')
            if len(self.state['commands']) >= MAX_COMMANDS:
                raise ValueError(f'本次运行最多接受 {MAX_COMMANDS} 次干预请求；仍可用停止按钮或 F9 终止')
            if kind == 'pause' and phase != 'running':
                raise RuntimeError('已经在交接或接管中，请等待当前交接完成')
            if kind == 'resume' and phase != 'paused':
                raise RuntimeError('只有确认暂停后才能交还模型')
            if kind == 'pause' and len(self.state['takeovers']) >= MAX_TAKEOVERS:
                raise ValueError(f'本次运行最多接管 {MAX_TAKEOVERS} 次')
            # Reserve a request slot for returning an active takeover.
            if kind != 'resume' and phase != 'running' and len(self.state['commands']) >= MAX_COMMANDS - 1:
                raise ValueError('已保留最后一个干预名额用于交还模型')
            if kind == 'pause' and len(self.state['commands']) >= MAX_COMMANDS - 1:
                raise ValueError('剩余干预名额不足以完成一次接管与交还')
            attachment = None
            if visual is not None:
                from trace2task.intervention_images import save_visual
                attachment = save_visual(visual, segment=self.state['takeovers'][-1],
                                         directory=self.directory, request_id=request_id)
            self.state['revision'] += 1
            command = {'id': request_id, 'index': len(self.state['commands']) + 1,
                       'kind': kind, 'text': text, 'requested_at': now(), 'status': 'queued',
                       'revision': self.state['revision']}
            if attachment is not None:
                command.update(visual=attachment, visual_digest=visual_digest)
            self.state['commands'].append(command)
            self.state['assisted_by_user'] = True
            if kind == 'pause':
                segment = {'id': f'takeover-{len(self.state["takeovers"]) + 1}',
                           'status': 'requested', 'requested_at': command['requested_at'],
                           'before': None, 'after': None, 'note': text,
                           'pause_command_id': request_id}
                self.state['takeovers'].append(segment)
                command['takeover_id'] = segment['id']
                self.state.update(phase='pausing', message='正在停止后续自动输入并记录接管前画面；请暂勿操作手机。')
            elif kind == 'resume':
                segment = self.state['takeovers'][-1]
                segment.update(note=text, resume_command_id=request_id)
                segment.pop('return_error', None)
                command['takeover_id'] = segment['id']
                self.state.update(phase='resuming', message='正在核对授权应用并记录交还画面；请停止手动操作。')
            else:
                self.state['message'] = ('补充指令已收到；交还后加入下一轮模型请求。' if phase == 'paused'
                                         else '补充指令已收到；尚未执行的旧动作将作废，下一轮重新规划。')
            self._publish()
            return self.snapshot()

    def current(self, revision):
        with self.lock:
            return self.state['phase'] == 'running' and self.state['revision'] == revision

    def admit_action(self, revision):
        """Linearization point: at most this single admitted action may finish.

        Only the execution thread calls this and checkpoint(); an API request
        cannot mark a takeover safe while that thread is still dispatching.
        """
        return self.current(revision)

    def checkpoint(self, capture, stop):
        """Run on the execution thread, also while it awaits a model response."""
        from trace2task.android_execution import AndroidAppNotFocused
        from trace2task.execution_protocol import ObservationStale

        while True:
            stop.raise_if_requested()
            with self.lock:
                phase = self.state['phase']
                if phase == 'running':
                    return self.state['revision']
                if phase == 'closed':
                    raise RuntimeError('干预会话已经结束')
                segment = self.state['takeovers'][-1] if self.state['takeovers'] else None
            if phase == 'paused':
                stop.sleep(.1)
                continue
            if phase not in {'pausing', 'resuming'} or segment is None:
                raise RuntimeError('干预会话尚未准备好')
            picture, error = None, None
            try:
                picture = capture(segment['id'], 'before' if phase == 'pausing' else 'after')
            except (AndroidAppNotFocused, ObservationStale) as unavailable:
                # No out-of-scope screenshot is taken. Transport/boot failures
                # remain fatal; only a recoverable focus/geometry change waits.
                error = str(unavailable)
            stop.raise_if_requested()
            with self.lock:
                if phase == 'pausing':
                    segment.update(status='active', started_at=now(), before=picture)
                    if error:
                        segment['before_error'] = error
                    self._command(segment['pause_command_id'])['status'] = 'paused'
                    self.state.update(phase='paused', message='自动输入已暂停，可以手动操作。原预算和 F9 仍然生效。')
                elif error:
                    segment['return_error'] = error
                    self._command(segment['resume_command_id'])['status'] = 'scope_rejected'
                    self.state.update(phase='paused', message='尚未交还：请回到本次授权应用，再点击交还模型。')
                else:
                    segment.update(status='returned', ended_at=now(), after=picture)
                    self.state.update(phase='running', message='交还画面已保存；旧计划作废，将从新截图重新规划。')
                self._publish()

    def _command(self, identifier):
        return next(item for item in self.state['commands'] if item['id'] == identifier)

    def model_context(self, revision):
        with self.lock:
            commands = [copy.deepcopy(c) for c in self.state['commands']
                        if c['revision'] <= revision and 'submitted_round' not in c
                        and c['status'] != 'scope_rejected']
            ids = {c['id'] for c in commands}
            segments = [copy.deepcopy(s) for s in self.state['takeovers']
                        if s['status'] == 'returned' and s.get('resume_command_id') in ids]
            return {'revision': revision, 'assisted_by_user': self.state['assisted_by_user'],
                    'commands': commands, 'takeovers': segments,
                    'limits': 'Human handoff snapshots and user descriptions are not a precise touch log '
                              'or proof of success. Only delivered model actions count as autonomous actions.'}

    def submitted(self, context, round_index):
        with self.lock:
            for item in context['commands']:
                command = self._command(item['id'])
                command.update(status='submitted', submitted_round=round_index)
            if context['commands']:
                self._publish()

    def responded(self, context, round_index):
        with self.lock:
            for item in context['commands']:
                command = self._command(item['id'])
                command.update(status='responded', responded_round=round_index)
            if context['commands']:
                self._publish()

    def seal_completion(self, revision):
        with self.lock:
            if not self.current(revision):
                return False
            # Reject a command that races with committing completion, instead
            # of accepting it and then silently ending without using it.
            self.close('visual_completion_reviewed')
            return True

    def close(self, reason):
        with self.lock:
            if self.state['phase'] == 'closed':
                return
            for segment in self.state['takeovers']:
                if segment['status'] != 'returned':
                    segment.update(status='not_returned', ended_at=now())
            self.state.update(phase='closed', closed_reason=reason,
                              message='本次运行已结束；未交还的人工段不补造结束画面，也不会自动恢复。')
            self._publish()
