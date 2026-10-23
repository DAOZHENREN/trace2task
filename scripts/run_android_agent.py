"""Android source-build entry point. Default: read-only capture, no model call."""
import argparse
import json
from pathlib import Path

from trace2task.android_execution import AndroidDriver
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.windows_runner import Win32EmergencyStop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--server-port', type=int, default=5038)
    parser.add_argument('--output', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--instruction')
    parser.add_argument('--model')
    parser.add_argument('--reasoning-effort', default='medium')
    parser.add_argument('--max-actions', type=int, default=10)
    args = parser.parse_args()
    if args.execute and (not args.instruction or not args.model):
        parser.error('--execute requires explicit --instruction and --model')
    if not 1 <= args.max_actions <= 80:
        parser.error('--max-actions must be 1..80')
    config = {'adb': args.adb, 'serial': args.serial, 'package': args.package,
              'server_port': args.server_port}
    stop = Win32EmergencyStop()
    if args.execute:
        result = run_chat_agent(
            instruction=args.instruction, model=args.model, reasoning_effort=args.reasoning_effort,
            output_root=args.output, emergency_stop=stop, status_callback=print,
            executor_backend='android', android_config=config, max_actions=args.max_actions)
    else:
        root = Path(args.output).resolve()
        root.mkdir(parents=True, exist_ok=False)
        events = []
        driver = AndroidDriver(root, lambda kind, **kw: events.append({'type': kind, **kw}),
                               stop, **config)
        stop.start()
        try:
            driver.start()
            state, path = driver.observe(0)
            result = {'target': driver.target, 'state': state, 'screenshot': str(path),
                      'input_actions': 0, 'model_calls': 0, 'events': events}
            (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        finally:
            driver.close()
            stop.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
