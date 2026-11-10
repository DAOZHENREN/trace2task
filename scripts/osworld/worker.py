"""Pipe worker reusing the pinned, previously accepted official guest lifecycle.

Runs in the existing OSWorld host environment. No model-supplied script API.
"""
import argparse
import contextlib
import hashlib
import io
import json
import sys
from dataclasses import asdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deployment', required=True)
    parser.add_argument('--output')
    parser.add_argument('--task')
    parser.add_argument('--catalog', action='store_true')
    args = parser.parse_args()
    cfg = json.loads(Path(args.deployment).read_text())
    for name, digest in cfg['source_files'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise ValueError('Pinned environment source changed: ' + name)
    sys.path.insert(0, cfg['capsule'] + '/src')
    import trace2task
    trace2task.__path__.append(cfg['fallback'] + '/src/trace2task')
    from trace2task.gui_owl_rlvr_adapter import load_rlvr_archive, load_task_config
    from trace2task.hybridcua_official_episode import OfficialEpisodeFactory
    from trace2task.hybridcua_official_guest import OfficialGuestBaseBinding
    from PIL import Image
    wire = sys.stdout
    episode = None
    with contextlib.redirect_stdout(sys.stderr):
        archive = load_rlvr_archive(cfg['archive'], expected_sha256=cfg['archive_sha256'])
        if args.catalog:
            ids = sorted({name.split('/')[0] for name in archive.members if name.endswith('/config.json')})
            rows = []
            for task_id in ids:
                task = load_task_config(archive, task_id)
                rows.append({'id': task_id, 'instruction': task.instruction, 'app': task.app_type})
            print(json.dumps({'source': 'HybridCUA RLVR / previously accepted OSWorld runtime', 'tasks': rows}, ensure_ascii=False), file=wire)
            return
        root = Path(args.output).resolve()
        (root / 'guests').mkdir()
        try:
            receipt = json.loads(Path(cfg['base_receipt']).read_text())
            if receipt['status'] != 'passed':
                raise ValueError('Missing accepted base receipt')
            base = receipt['base']
            binding = OfficialGuestBaseBinding(Path(base['path']), base['sha256'], tuple(base['identity']), base['verified_at_unix_ns'])
            factory = OfficialEpisodeFactory(archive=archive, allowed_task_ids=[args.task],
                base_binding=binding, runtime_root=root/'guests', max_slots=1, readiness_policy='upstream')
            episode = factory(args.task)
            episode.setup()
            # Mainline-only resource fix; frozen capsules and base image stay intact.
            trace2task.__path__.append(str(Path(__file__).resolve().parents[2] / 'src/trace2task'))
            from trace2task.osworld_cursor_fix import install
            from trace2task.osworld_terminal_recovery import terminal_close_action
            manifest = json.loads(Path(str(episode._runtime_dir)+'.json').read_text())
            install('http://127.0.0.1:' + str(manifest['ports'][0]), root/'cursor-repair')
            print(json.dumps({'ok': True, 'instruction': episode.config.instruction,
                'provider_directory': str(episode._runtime_dir), 'task_id': args.task}), file=wire, flush=True)
            size = None
            for line in sys.stdin:
                try:
                    req = json.loads(line)
                    op = req['op']
                    if op == 'observe':
                        png = episode.screenshot()
                        with Image.open(io.BytesIO(png)) as im:
                            size = im.size
                        path = root / ('screen-' + str(req['index']) + '.png')
                        with path.open('xb') as f:
                            f.write(png)
                        result = {'path': str(path), 'width': size[0], 'height': size[1]}
                    elif op == 'act':
                        if size is None:
                            raise ValueError('Observe before acting')
                        actions = req['action'] if isinstance(req['action'], list) else [req['action']]
                        if len(actions) not in (1, 2):
                            raise ValueError('Invalid native action count')
                        receipts = []
                        for action in actions:
                            with terminal_close_action(episode, action):
                                item = asdict(episode.act(action, *size, close_on_policy_failure=False))
                            if not item['executed'] and receipts:
                                raise RuntimeError('Partial GUI operation; no retry: ' + item['reason'])
                            receipts.append(item)
                            if not item['executed']:
                                break
                        result = {**receipts[-1], 'native_receipts': receipts}
                    elif op == 'evaluate':
                        episode.close_policy()
                        result = asdict(episode.evaluate())
                        (root/'official-evaluation.json').write_text(json.dumps(result, indent=2))
                    elif op == 'close':
                        episode.cleanup()
                        episode = None
                        print(json.dumps({'ok': True, 'result': {'closed': True}}), file=wire, flush=True)
                        break
                    else:
                        raise ValueError('Unknown environment operation')
                    print(json.dumps({'ok': True, 'result': result}), file=wire, flush=True)
                except Exception as exc:
                    print(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}), file=wire, flush=True)
        finally:
            if episode is not None:
                episode.cleanup()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}), flush=True)
        raise
