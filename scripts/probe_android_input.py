"""Execute one operator-selected, screenshot-grounded action and archive before/after.

This is a manual diagnostic, not an autonomous game test. No model is called.
"""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

from trace2task.android_execution import AndroidDriver, AndroidExecutionBackend
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ActionPlan
from trace2task.windows_runner import Win32EmergencyStop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--server-port', type=int, default=5038)
    parser.add_argument('--output', required=True)
    parser.add_argument('--action-json', required=True)
    parser.add_argument('--execute', action='store_true', required=True)
    args = parser.parse_args()
    plan = ActionPlan.from_prediction({'actions': [json.loads(args.action_json)]})
    if len(plan.actions) != 1:
        parser.error('Exactly one input action required')
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    def record(kind, **data):
        with (root / 'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': kind, **data}) + '\n')
    stop = Win32EmergencyStop()
    driver = AndroidDriver(root, record, stop, adb=args.adb, serial=args.serial,
                           package=args.package, server_port=args.server_port)
    stop.start()
    try:
        driver.start()
        state, before = driver.observe(0)
        core = ExecutionCore(AndroidExecutionBackend(driver), stop, record, max_actions=1)
        result = core.execute(plan.to_payload(), target=driver.target, state=state,
                              observation_id=state['sha256'])
        stop.sleep(.5)
        summary = {'result': asdict(result), 'before': str(before), 'model_calls': 0}
        try:
            after_state, after = driver.observe(1)
            summary.update(after=str(after), pixels_changed=after_state['sha256'] != state['sha256'])
        except RuntimeError as error:
            # BACK may legitimately leave the bound app. Never follow it into another app.
            summary['after_capture_error'] = str(error)
        (root / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary, indent=2))
    finally:
        driver.close()
        stop.close()


if __name__ == '__main__':
    main()
