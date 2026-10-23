"""Pinned MobileForge Android policy bridge. Default checks source only; no device/model calls."""
import argparse
import json
import os

from trace2task.chat_agent_runner import run_chat_agent
from trace2task.mobileforge_adapter import MobileForgeAdapter, MobileForgeSession, MobileForgeSource
from trace2task.mobileforge_codex import MobileForgeCodexSession
from trace2task.mobileforge_pipeline import require_local_endpoint
from trace2task.model_api import ModelAPIConfig
from trace2task.windows_runner import Win32EmergencyStop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--adb')
    parser.add_argument('--serial')
    parser.add_argument('--package')
    parser.add_argument('--server-port', type=int, default=5038)
    parser.add_argument('--base-url')
    parser.add_argument('--provider', choices=['local', 'codex'], default='local')
    parser.add_argument('--model')
    parser.add_argument('--key-env', default='MOBILEFORGE_POLICY_API_KEY')
    parser.add_argument('--instruction')
    parser.add_argument('--output')
    parser.add_argument('--max-actions', type=int, default=10)
    parser.add_argument('--hints-root')
    parser.add_argument('--task-id', default='task')
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    source = MobileForgeSource(args.upstream)
    if not args.execute:
        print(json.dumps({'source_verified': True, 'provenance': source.provenance,
                          'device_actions': 0, 'model_calls': 0}, indent=2))
        return
    if not all((args.adb, args.serial, args.package, args.model,
                args.instruction, args.output)) or not 1 <= args.max_actions <= 80:
        parser.error('Execution requires adb, serial, package, base-url, model, instruction, output and 1..80 actions')
    hints = source.hints(args.hints_root, args.task_id, 'Qwen3VL', args.attempt) if args.hints_root else ''
    config = None
    if args.provider == 'codex':
        if args.base_url:
            parser.error('Codex subscription does not use --base-url')
        session = MobileForgeCodexSession(args.output, args.model)
    else:
        if not args.base_url:
            parser.error('Local provider requires --base-url')
        require_local_endpoint(args.base_url)
        config = ModelAPIConfig(base_url=args.base_url, api_key=os.environ.get(args.key_env, 'local-only'),
                                api_key_env=args.key_env)
        session = MobileForgeSession(config, args.model)
    try:
        result = run_chat_agent(
            instruction=args.instruction, model=args.model, reasoning_effort='default',
            output_root=args.output, emergency_stop=Win32EmergencyStop(), status_callback=print,
            executor_backend='android', android_config={
                'adb': args.adb, 'serial': args.serial, 'package': args.package, 'server_port': args.server_port},
            api_config=config, session=session, model_adapter=MobileForgeAdapter(session, source, hints=hints),
            max_actions=args.max_actions)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        session.close()


if __name__ == '__main__':
    main()
