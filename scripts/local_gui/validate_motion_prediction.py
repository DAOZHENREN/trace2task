"""Read-only local GPU test of comparison images and motion prompts; no executor."""
import argparse
import base64
import json
from pathlib import Path

from PIL import Image
from server import Engine

from trace2task.cua_backend import action_request
from trace2task.execution_protocol import ActionUnavailable, UnifiedAction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    engine = Engine(Path('D:/Models/Trace2Task-GUI'), args.output)
    results = []
    for model in ('gui-owl-2b', 'mai-ui-2b'):
        request = {'model': model,
            'task': 'Scroll down a few lines in this test text window.',
            'image': base64.b64encode(args.current.read_bytes()).decode(),
            'previous_image': base64.b64encode(args.before.read_bytes()).decode(),
            'history': [{'step_index': 0, 'executed': True, 'effect': 'unverifiable',
                         'action': {'actions': [{'skill': 'scroll', 'args': {
                             'direction': 'down', 'amount': 3, 'by': 'line'}}]}}],
            'cua_context': {'capabilities': {'available_skills': ['scroll', 'drag', 'wait'],
                                           'delivery_mode': 'background'}}, 'step_index': 1}
        result = engine.predict(request)
        width, height = Image.open(args.current).size
        state = {'pid': 1, 'window_id': 1, 'session': 'read-only-preflight',
                 'screenshot_width': width, 'screenshot_height': height}
        # Pure parameter preparation only; never call a driver or execute the reply.
        attempts = []
        for attempt in range(2):
            attempt_row = {'raw_output': result.get('raw_output'), 'status': result['status']}
            attempts.append(attempt_row)
            if result['status'] != 'predicted':
                break
            try:
                native = result['prediction']['actions'][0]
                prepared = action_request(UnifiedAction.from_payload(native), state)
                attempt_row['preflight'] = {'status': 'accepted', 'prepared': prepared}
                break
            except ActionUnavailable as error:
                attempt_row['preflight'] = {'status': 'rejected', 'reason': str(error)}
                if attempt == 0:
                    request['cua_context']['execution_feedback'] = {
                        'status': 'rejected', 'executed': False, 'reason': str(error)}
                    result = engine.predict(request)
        row = {k: result.get(k) for k in ('model', 'status', 'error', 'raw_output', 'prediction',
               'metrics', 'output_directory')}
        row['external_actions_sent'] = False
        row['attempts'] = attempts
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (args.output/'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
