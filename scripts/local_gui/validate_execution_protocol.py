"""Real GPU predictions on a recorded window screenshot; NEVER dispatch input."""
import argparse
import base64
import json
from pathlib import Path

from trace2task.cua_execution import CuaExecutionBackend
from trace2task.execution_protocol import ActionPlan, ActionUnavailable
from trace2task.local_gui_client import predict_gui
from trace2task.local_gui_protocol import adapt_capabilities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    events = [json.loads(line) for line in (args.source/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    observed = next(e for e in reversed(events) if e.get('tool') == 'get_window_state')
    state = json.loads(observed['stdout'])
    state['session'] = 'offline-validation-only'
    task = next(e['task'] for e in events if e['type'] == 'model_input')
    image = Path(next(e['screenshot'] for e in reversed(events) if e['type'] == 'capture'))
    target = {k: state[k] for k in ('pid', 'window_id')}
    backend = CuaExecutionBackend(None, None)  # Deliberately cannot dispatch or bind.
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for model in ('gui-owl-2b', 'mai-ui-2b'):
        context = {'current_window': target, 'execution_scope': 'cua_window',
                   'capabilities': adapt_capabilities(model, backend.capabilities(state)),
                   'execution_feedback': None}
        output = predict_gui(task, model=model, image=base64.b64encode(image.read_bytes()).decode(),
                             cua_context=context, audit_dir=args.output/model)
        row = {'model': model, 'status': output['status'], 'executed': False,
               'raw_output': output.get('raw_output'), 'metrics': output.get('metrics'),
               'error': output.get('error')}
        if output['status'] == 'predicted':
            plan = ActionPlan.from_prediction(output['prediction'])
            row['normalized_actions'] = [a.to_payload() for a in plan.actions]
            try:
                row['driver_requests_preview'] = [backend.prepare(action, state) for action in plan.actions]
                row['preflight'] = 'accepted' if plan.actions else 'model_done_unverified'
            except ActionUnavailable as error:
                row.update(preflight='rejected_without_input', reason=str(error))
        results.append(row)
        print(json.dumps(row, ensure_ascii=False))
    (args.output/'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
