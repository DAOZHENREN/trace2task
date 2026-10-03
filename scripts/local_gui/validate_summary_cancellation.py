"""Read-only GPU check: cancel a real summary, then retry the unchanged conversation."""
import base64
import hashlib
import io
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from PIL import Image

from trace2task.local_gui_client import cancel_gui, predict_gui


def main():
    data = Path(os.environ['TRACE2TASK_DATA_ROOT'])
    buffer = io.BytesIO()
    Image.new('RGB', (1920, 1080), 'blue').save(buffer, format='PNG')
    picture = base64.b64encode(buffer.getvalue()).decode()
    identity = uuid.uuid4().hex
    experience_text = '\n'.join(f'Historical example {index}: inspect the current view; do not reuse coordinates.'
                                for index in range(900))
    experience = {'kind': 'trace_sequence', 'task_id': 'summary-cancellation', 'model_input': experience_text,
                  'sha256': hashlib.sha256(experience_text.encode()).hexdigest()}
    for step in range(24):
        request_id = uuid.uuid4().hex
        kwargs = {'model': 'gui-owl-2b', 'image': picture, 'conversation_id': identity,
                  'conversation_start': step == 0, 'step_index': step, 'request_id': request_id,
                  'experience_context': experience if step == 0 else None,
                  'history': [{'executed': False, 'reason': 'Read-only validation; no input sent.'}]}
        results, failures = [], []

        def predict(kwargs=kwargs, results=results, failures=failures):
            try:
                results.append(predict_gui('Inspect the image and propose waiting briefly.', **kwargs))
            except Exception as error:  # noqa: BLE001 - surface worker failure to the validator
                failures.append(error)

        worker = threading.Thread(target=predict)
        worker.start()
        cancellation_started = None
        while worker.is_alive():
            files = list((data / 'runs/local-gui').glob(f'*-{request_id}/compaction.json'))
            if files and cancellation_started is None:
                try:
                    event = json.loads(files[0].read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    event = {}
                if event.get('stage') == 'summary_generation':
                    cancellation_started = time.perf_counter()
                    cancel_gui(request_id)
            worker.join(timeout=.05)
        assert not failures, failures
        assert results, 'No prediction result'
        result = results[0]
        if cancellation_started is None:
            assert result['status'] in {'predicted', 'model_control'}, result
            print(json.dumps({'warmup_round': step + 1, 'input_tokens': result['metrics']['tokens']['input_tokens']}), flush=True)
            continue
        elapsed = time.perf_counter() - cancellation_started
        assert result['status'] == 'cancelled', result
        event = result['context_management']['compaction']
        assert event['conversation_unchanged'] is True
        before = json.loads((Path(result['output_directory']) / 'compaction-before.json').read_text(encoding='utf-8'))
        kwargs['request_id'] = uuid.uuid4().hex
        recovered = predict_gui('Inspect the image and propose waiting briefly.', **kwargs)
        assert recovered['status'] in {'predicted', 'model_control'}, recovered
        recovered_before = json.loads((Path(recovered['output_directory']) / 'compaction-before.json').read_text(encoding='utf-8'))
        assert before == recovered_before, 'Cancelled compaction changed conversation history'
        report = {'actions_executed': 0, 'cancel_seconds': elapsed,
                  'cancel_archive': result['output_directory'], 'recovery_archive': recovered['output_directory'],
                  'history_unchanged_verified': True, 'recovery_compaction': recovered['context_management']['compaction']}
        path = data / 'runs/summary-cancellation-acceptance.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'cancel_seconds': elapsed, 'history_unchanged': True, 'report': str(path)}))
        return
    raise AssertionError('No summary was triggered')


if __name__ == '__main__':
    main()
