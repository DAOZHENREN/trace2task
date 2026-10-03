"""Read-only live cancellation/reload check against an idle llama GUI service."""
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
    text = '\n'.join(f'Historical example {index}: inspect the current view; do not reuse coordinates.'
                     for index in range(800))
    experience = {'kind': 'trace_sequence', 'task_id': 'read-only-cancellation-check',
                  'model_input': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
    results = []

    def predict():
        results.append(predict_gui('Inspect the image and propose waiting briefly.',
            model='gui-owl-2b', image=picture, request_id=identity, experience_context=experience))

    worker = threading.Thread(target=predict)
    worker.start()
    deadline = time.monotonic() + 30
    while worker.is_alive() and time.monotonic() < deadline:
        files = list((data / 'runs/local-gui').glob(f'*-{identity}/input.json'))
        if files:
            try:
                payload = json.loads(files[0].read_text(encoding='utf-8'))
                if payload.get('input_stage') == 'inference_started':
                    break
            except (OSError, ValueError):
                pass
        time.sleep(.05)
    time.sleep(.2)
    started = time.perf_counter()
    receipt = cancel_gui(identity)
    worker.join(timeout=20)
    elapsed = time.perf_counter() - started
    assert not worker.is_alive(), 'Cancellation did not finish promptly'
    assert results and results[0]['status'] == 'cancelled', results
    assert results[0].get('backend') == 'llama-server', results
    recovered = predict_gui('Inspect the image and propose waiting briefly.', model='gui-owl-2b', image=picture)
    assert recovered['status'] == 'predicted', recovered
    report = {'cancel_receipt': receipt, 'cancel_elapsed_seconds': elapsed,
              'cancelled_request': results[0], 'recovery_archive': recovered['output_directory'],
              'recovery_status': recovered['status'], 'actions_executed': 0}
    path = data / 'runs/llama-cancellation-acceptance.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'cancel_status': results[0]['status'], 'cancel_seconds': elapsed,
                      'recovery_status': recovered['status'], 'report': str(path)}))


if __name__ == '__main__':
    main()
