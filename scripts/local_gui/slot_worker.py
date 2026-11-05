"""Pipe-only resident official reader, launched in the deployment's environment."""
import argparse
import json
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path


def predict_step(model, image, instruction, previous, cuda):
    """Keep weights resident, but release unused workspaces between changing shapes.

    The reader returns CPU scalars and does not reuse a KV cache. Growing text
    history changes temporary tensor sizes; retaining all those cached blocks
    can push Windows CUDA allocations into shared system memory.
    """
    cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    try:
        step = model.decide(image, instruction, previous=previous)
        metrics = {'inference_ms': (time.perf_counter() - started) * 1000,
                   'peak_allocated_bytes': cuda.max_memory_allocated(),
                   'peak_reserved_bytes': cuda.max_memory_reserved()}
    finally:
        release_started = time.perf_counter()
        # empty_cache frees unoccupied allocator blocks, never live model weights.
        cuda.empty_cache()
        release_ms = (time.perf_counter() - release_started) * 1000
    return step, {**metrics, 'cache_release_ms': release_ms,
                  'allocated_after_bytes': cuda.memory_allocated(),
                  'reserved_after_bytes': cuda.memory_reserved()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deployment', type=Path, required=True)
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.path.insert(0, str(args.deployment / 'source'))
    # Library diagnostics never share the protocol stream.
    protocol = sys.stdout
    def reply(value):
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), file=protocol, flush=True)

    with redirect_stdout(sys.stderr):
        import torch
        from PIL import Image
        from slotread.client import agent_user
        from slotread.local import LocalSlotModel

        started = time.perf_counter()
        model = LocalSlotModel.from_pretrained(str(args.deployment / 'model'))
        torch.cuda.empty_cache()
        load_ms = (time.perf_counter() - started) * 1000
        cfg = model.cfg['tasks']['agent']
        reply({'status': 'ready', 'model': 'gui-decisions-2b', 'worker_pid': os.getpid(),
               'model_loads': 1, 'load_ms': load_ms})
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                with Image.open(request['image']) as source:
                    image = source.convert('RGB')
                step, metrics = predict_step(model, image, request['instruction'], request['previous'], torch.cuda)
                # Same system, user and assistant slot layout as LocalSlotModel.read.
                messages = [
                    {'role': 'system', 'content': cfg['system']},
                    {'role': 'user', 'content': [{'type': 'image'}, {'type': 'text',
                        'text': model.tasks['agent'].user({'user': agent_user(request['instruction'], request['previous'])})}]},
                    {'role': 'assistant', 'content': ' '.join(f'{s}: ___' for s in cfg['slots'])},
                ]
                reply({'request_id': request['request_id'], 'step': step, 'messages': messages,
                       'worker_pid': os.getpid(), 'model_loads': 1, **metrics})
            except Exception as error:  # noqa: BLE001 - transport an inference error, never an action
                reply({'request_id': request.get('request_id'), 'error': f'{type(error).__name__}: {error}'})


if __name__ == '__main__':
    main()
