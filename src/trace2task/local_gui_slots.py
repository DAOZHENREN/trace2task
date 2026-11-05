"""Official GUI-Decisions reader in its own persistent Python environment.

The HTTP service owns this pipe-only child. No shell or model action is run here.
"""
import atexit
import hashlib
import json
import math
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from trace2task.execution_protocol import ActionPlan, ActionUnavailable
from trace2task.local_process import stop_started_process

MODEL = 'gui-decisions-2b'
TURN = ('Please generate the next move according to the UI screenshot, instruction and previous actions.\n\n'
        'Instruction: {{task}}{{experience_block}}{{execution_feedback_block}}{{execution_context_block}}'
        '\n\nPrevious actions:\n{{history}}')
SYSTEM = ('You are a GUI agent. Given a screenshot, the task and the previous actions, answer the next step. '
          'action: click, type, scroll, swipe, press, open, back, home, wait, answer, select. '
          'For x, the code of the horizontal position of the point, from the left edge to the right edge; '
          'for y, the same vertically from the top to the bottom. x2 and y2: the end point of a swipe, '
          'written the same way. key: the key to press. sdir and samt: scroll direction and amount.')
# Files from the pinned author release, verified during deployment. Check once
# per worker lifetime, not once per task. No tokenizer/model remote code is used.
HASHES = {
    'model.safetensors': '82ea4874236ee1bcb6bb238230f0a0045289a4cb389dac4c367306e1fb0f23ab',
    'config.json': '964fb2b01df2f05b049d1b1253a16c39aada640f2602afe905994a332693da27',
    'generation_config.json': 'd60f83aa06dd00b6658dff63d4ab81e0fa5f0ba031fa932ff052fb417995bff1',
    'processor_config.json': '4d37d6c170144eab2a4f3f526b6b253e7bf1c2eb4cee8f5588be35d429a29cfd',
    'slotread_config.json': 'c922920ffc406f74655254e655f5d4b23c3597d234a15ac43846e8cdf8ec5b1b',
    'tokenizer.json': 'be75606093db2094d7cd20f3c2f385c212750648bd6ea4fb2bf507a6a4c55506',
    'tokenizer_config.json': '79a246d4ee3e09970c14b4b66a422db5f6df15837dcd78870c43bac5df3261de',
    'chat_template.jinja': '3636d0f0bd6bef02654cdffdc447b79cb2cef8ab02cc75267345946291a489e4',
}


def deployment_root():
    return Path(os.environ.get('TRACE2TASK_GUI_DECISIONS_ROOT', 'D:/Models/GUI-Decisions-2B'))


def worker_script():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2])) / 'scripts/local_gui/slot_worker.py'


def required_paths():
    root = deployment_root()
    return [root / '.venv/Scripts/python.exe', worker_script(), root / 'source/slotread/local.py',
            root / 'source/slotread/data/aguvis.py', *[root / 'model' / name for name in HASHES]]


def verify_model(root):
    for name, expected in HASHES.items():
        with (root / 'model' / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('GUI-Decisions model checksum mismatch: ' + name)


def decode_slots(value):
    """Only action-relevant slots carry meaning; unused slots are ignored."""
    if not isinstance(value, dict) or not isinstance(value.get('action'), str):
        raise ValueError('Invalid GUI-Decisions slot response')  # noqa: TRY004 - parser validation contract
    action = value['action']

    def point(x='x', y='y'):
        result = [value.get(x), value.get(y)]
        if any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in result):
            raise ValueError('Slot coordinates must be finite normalized numbers')
        return result

    if action in {'type', 'open', 'answer'}:
        return {'control': 'interact', 'text': (
            f'GUI-Decisions 返回 {action}，但合并权重的槽位输出不包含文本。'
            '请人工接续或换用可生成文本的模型；本次未发送输入，也未确认任务完成。')}
    if action in {'click', 'select'}:  # The author's browser agent maps select to click too.
        x, y = point()
        skill, args = 'click', {'x': x, 'y': y, 'button': 'left'}
    elif action == 'swipe':
        x, y = point()
        x2, y2 = point('x2', 'y2')
        skill, args = 'drag', {'start_x': x, 'start_y': y, 'end_x': x2, 'end_y': y2,
                               'button': 'left', 'duration_ms': 500}
    elif action == 'scroll':
        amount, direction = value.get('samt'), value.get('sdir')
        if type(amount) is not int or not 0 <= amount <= 9 or direction not in {'up', 'down', 'left', 'right'}:
            raise ValueError('Invalid native scroll slots')
        # Native amount is a bucket, not a line count. Windows exposes wheel
        # lines, so explicitly adapt 0..9 to 5..50 lines (not pixel equivalence).
        skill, args = 'scroll', {'direction': direction, 'amount': (amount + 1) * 5, 'by': 'line'}
    elif action == 'press':
        key = value.get('key')
        if key not in {'enter', 'tab', 'escape', 'space', 'delete', 'up', 'down', 'left', 'right'}:
            raise ActionUnavailable('GUI-Decisions key has no supported desktop mapping: ' + str(key))
        skill, args = 'press_key', {'key': key}
    elif action == 'wait':
        skill, args = 'wait', {'duration_ms': 800}
    else:
        raise ActionUnavailable('GUI-Decisions action has no unambiguous desktop mapping: ' + action)
    return ActionPlan.from_prediction({'actions': [{'skill': skill, 'args': args}]}).to_payload()


def native_history(outputs):
    """Match the author's numbered action history, not unused slot logits/metrics.

    These are previous model decisions, not proof that the UI accepted them.
    Keep unfamiliar output verbatim so a rejected step is never hidden.
    """
    lines = []
    for index, raw in enumerate(outputs, 1):
        text = raw
        try:
            value = json.loads(raw)
            decode_slots(value)
        except (ValueError, TypeError, ActionUnavailable):
            pass
        else:
            action = value['action']
            if action in {'click', 'select'}:
                text = f"click(x={value['x']:.3f}, y={value['y']:.3f})"
            elif action == 'swipe':
                text = (f"swipe(from=({value['x']:.2f},{value['y']:.2f}), "
                        f"to=({value['x2']:.2f},{value['y2']:.2f}))")
            elif action == 'scroll':
                text = f"scroll(direction='{value['sdir']}', amount={value['samt']})"
            elif action == 'press':
                text = f"press(keys='{value['key']}')"
            elif action == 'wait':
                text = 'wait()'
        lines.append(f'{index}. {text}')
    return '\n'.join(lines) or 'None'


class SlotRuntime:
    def __init__(self, output):
        self.output = Path(output)
        self.process = None
        self.info = {}
        self.responses = None
        atexit.register(self.close)

    @property
    def ready(self):
        return bool(self.info and self.process is not None and self.process.poll() is None)

    def close(self):
        process = self.process
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()  # EOF releases an idle reader without forced termination.
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                stop_started_process(process)
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        self.process, self.info = None, {}

    @staticmethod
    def _read(stream, responses):
        try:
            for line in stream:
                responses.put(json.loads(line))
        except (OSError, ValueError) as error:
            responses.put({'error': 'Invalid worker response: ' + str(error)})
        finally:
            responses.put({'error': 'GUI-Decisions worker exited; see slot-worker.log'})

    def _receive(self, timeout):
        try:
            result = self.responses.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise TimeoutError('GUI-Decisions worker timed out; its process was stopped') from None
        if not isinstance(result, dict) or result.get('error'):
            self.close()
            raise RuntimeError(result.get('error') if isinstance(result, dict) else 'Invalid slot worker reply')
        return result

    def load(self):
        if self.ready:
            return
        self.close()
        missing = [str(p) for p in required_paths() if not p.is_file()]
        if missing:
            raise RuntimeError('GUI-Decisions 独立部署缺少文件：' + missing[0])
        root = deployment_root()
        verify_model(root)
        self.output.mkdir(parents=True, exist_ok=True)
        self.responses = queue.Queue()
        env = {**os.environ, 'PYTHONUTF8': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
        with (self.output / 'slot-worker.log').open('ab') as log:
            self.process = subprocess.Popen(
                [str(root / '.venv/Scripts/python.exe'), '-u', str(worker_script()), '--deployment', str(root)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                text=True, encoding='utf-8', env=env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, args=(self.process.stdout, self.responses), daemon=True).start()
        reply = self._receive(180)
        if reply.get('status') != 'ready' or reply.get('model') != MODEL:
            self.close()
            raise RuntimeError('GUI-Decisions worker identity mismatch')
        self.info = reply

    def predict(self, image, instruction, previous, request_id):
        self.load()
        self.process.stdin.write(json.dumps({'image': str(image), 'instruction': instruction,
            'previous': previous, 'request_id': request_id}, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        result = self._receive(120)
        if result.get('request_id') != request_id:
            self.close()
            raise RuntimeError('GUI-Decisions response identity mismatch')
        return result
