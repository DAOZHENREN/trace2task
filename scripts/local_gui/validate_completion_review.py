"""Read-only GPU regression on the archived false-completion screenshot; no driver."""
import argparse
import base64
import json
from pathlib import Path

from trace2task.local_gui_client import predict_gui


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    events = [json.loads(line) for line in (args.source/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    inputs = [e for e in events if e['type'] == 'model_input']
    last = inputs[-1]
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in ('gui-owl-2b', 'mai-ui-2b'):
        reply = predict_gui(last['task'], model=model,
            image=base64.b64encode(Path(last['screenshot']).read_bytes()).decode(),
            history=last['history'], cua_context={'purpose': 'verify_completion'},
            audit_dir=args.output/model)
        row = {'model': model, 'status': reply['status'], 'verification': reply.get('verification'),
               'raw_output': reply.get('raw_output'), 'error': reply.get('error'),
               'elapsed_seconds': reply.get('elapsed_seconds'), 'executed': False}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (args.output/'result.json').write_text(json.dumps({'models': rows,
        'external_actions_sent': False}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
