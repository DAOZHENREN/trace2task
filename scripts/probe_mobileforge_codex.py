"""Test subscription policy protocol on a saved screenshot; never instantiate a device driver."""
import argparse
import json
from pathlib import Path

from trace2task.mobileforge_adapter import MobileForgeAdapter, MobileForgeSource
from trace2task.mobileforge_codex import MobileForgeCodexSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('upstream', 'screenshot', 'instruction', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--model', default='gpt-6-luna')
    args = parser.parse_args()
    path = Path(args.output).resolve()
    if path.exists():
        parser.error('Output must be new')
    source = MobileForgeSource(args.upstream)
    session = MobileForgeCodexSession(path.parent, args.model)
    try:
        adapter = MobileForgeAdapter(session, source)
        result = adapter.predict(args.instruction, screenshot=args.screenshot, history=[],
                                 context={'purpose': 'policy_prediction', 'delivery': 'prediction_only'})
        result.update(model=args.model, provider=session.provider, device_actions=0,
                      executed=False, subscription_auth_verified=session.auth_checked)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'output': str(path), 'status': result['status'],
                          'prediction': result.get('prediction'), 'tokens': result['tokens'],
                          'device_actions': 0}, ensure_ascii=False))
    finally:
        session.close()


if __name__ == '__main__':
    main()
