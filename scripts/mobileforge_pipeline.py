"""Explicit local-model or Codex-subscription proposal/critic/export stages (no device input)."""
import argparse
import json
from pathlib import Path

from trace2task.mobileforge_adapter import MobileForgeSession
from trace2task.mobileforge_codex import MobileForgeCodexSession
from trace2task.mobileforge_pipeline import (
    PipelinePrompts,
    _save,
    evaluate_run,
    export_attempt,
    propose_tasks,
    require_local_endpoint,
)
from trace2task.model_api import ModelAPIConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='stage', required=True)
    for stage in ('propose', 'critic'):
        command = sub.add_parser(stage)
        command.add_argument('--upstream', required=True)
        command.add_argument('--provider', choices=['local', 'codex'], default='local')
        command.add_argument('--base-url', help='Required for local provider; loopback endpoint only')
        command.add_argument('--model', required=True)
        command.add_argument('--output', required=True)
        if stage == 'propose':
            command.add_argument('--screenshot', required=True)
            command.add_argument('--package', required=True)
        else:
            command.add_argument('--run', required=True)
            command.add_argument('--task', required=True)
    command = sub.add_parser('export')
    for name in ('run', 'feedback', 'destination', 'task-id'):
        command.add_argument('--' + name, required=True)
    command.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    if args.stage == 'export':
        print(export_attempt(args.run, args.feedback, args.destination, args.task_id, args.attempt))
        return
    prompts = PipelinePrompts(args.upstream)
    if args.provider == 'codex':
        if args.base_url:
            parser.error('Codex subscription does not use --base-url')
        session = MobileForgeCodexSession(Path(args.output).resolve().parent, args.model)
    else:
        if not args.base_url:
            parser.error('Local provider requires --base-url')
        require_local_endpoint(args.base_url)
        session = MobileForgeSession(ModelAPIConfig(base_url=args.base_url, api_key='local-only'), args.model)
    try:
        if args.stage == 'propose':
            result = propose_tasks(prompts, session, args.screenshot, args.package)
            _save(args.output, result)
        else:
            result = evaluate_run(prompts, session, args.run, args.task, args.output)
        print(json.dumps({'output': args.output, 'training_eligible': result.get('training_eligible'),
                          'device_actions': 0}, ensure_ascii=False))
    finally:
        session.close()


if __name__ == '__main__':
    main()
