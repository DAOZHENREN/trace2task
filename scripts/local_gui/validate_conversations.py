"""Read-only GPU smoke: synthetic images, no executor or desktop capture."""
import argparse
import base64
import hashlib
import io
import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from PIL import Image

from trace2task.local_gui_client import predict_gui


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('models', nargs='*', default=['qwen3-vl-2b'])
    parser.add_argument('--turns', type=int, default=2)
    parser.add_argument('--width', type=int, default=256)
    parser.add_argument('--height', type=int, default=256)
    parser.add_argument('--experience-file', type=Path)
    parser.add_argument('--report', type=Path, help='Save round metrics and sampled whole-GPU memory (not allocator peak)')
    args = parser.parse_args()
    samples, records = [], []
    stop = threading.Event()

    def sample_gpu():
        while not stop.is_set():
            result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.used,memory.total',
                                     '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=5, check=False)
            if result.returncode == 0:
                samples.append({'time': time.time(), 'gpus': result.stdout.strip()})
            stop.wait(.2)

    sampler = None
    if args.report:
        sampler = threading.Thread(target=sample_gpu, daemon=True)
        sampler.start()
    for model in args.models:
        identity = uuid.uuid4().hex
        text = (args.experience_file.read_bytes().decode('utf-8') if args.experience_file else
                'DEMO_REFERENCE_7301\nHistorical demonstration: inspect the image, then wait briefly.')
        experience = {'kind': 'trace_sequence', 'task_id': 'conversation-smoke',
                      'model_input': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
        replies = []
        pinned_text = None
        for index in range(args.turns):
            color = ('red', 'blue', 'green')[index % 3]
            buffer = io.BytesIO()
            Image.new('RGB', (args.width, args.height), color).save(buffer, format='PNG')
            result = predict_gui('Inspect the current solid-color image. Propose waiting briefly.',
                model=model, image=base64.b64encode(buffer.getvalue()).decode(),
                conversation_id=identity, conversation_start=index == 0,
                step_index=index,
                experience_context=experience if index == 0 else None,
                history=[] if index == 0 else [{'executed': False, 'reason': 'Read-only smoke; no input sent.'}])
            assert result['status'] in {'predicted', 'format_rejected', 'adapter_rejected', 'model_control'}, result
            saved = json.loads((Path(result['output_directory'])/'input.json').read_text(encoding='utf-8'))
            assert saved['messages'][0]['role'] == 'system' and saved['messages'][-1]['role'] == 'user'
            assert text in saved['messages'][1]['content'][0]['text']
            current_task_text = saved['messages'][1]['content'][0]['text']
            if pinned_text is None:
                pinned_text = current_task_text
            assert current_task_text == pinned_text
            assert text not in saved['messages'][0]['content']
            assert all(text not in json.dumps(m, ensure_ascii=False) for m in saved['messages'][2:])
            if index:
                assert saved['messages'][-1]['content'] == [
                    {'type': 'text', 'text': 'Instruction: Inspect the current solid-color image. Propose waiting briefly.'},
                    {'type': 'image'}]
            assert saved['new_messages'] == [saved['messages'][-1]]
            retained_replies = [m['content'] for m in saved['messages'] if m['role'] == 'assistant']
            assert retained_replies == (replies[-len(retained_replies):] if retained_replies else [])
            replies.append(result['raw_output'])
            assert saved['context_management']['new_image_count'] == 1
            compaction = saved['context_management'].get('compaction')
            assert saved['context_management']['removed_text_messages'] == (compaction['replaced_text_messages'] if compaction else 0)
            assert saved['configuration']['image_count'] == saved['context_management']['history_images_after'] + 1
            record = {'model': model, 'turn': index + 1, 'status': result['status'],
                              'conversation_id': identity, 'input_tokens': result['metrics']['tokens']['input_tokens'],
                              'history_images': saved['context_management']['history_images_after'],
                              'removed_tokens': saved['context_management']['removed_tokens'],
                              'peak_allocated_bytes': result['peak_allocated_bytes'],
                              'cached_tokens': result['metrics']['tokens'].get('cached_tokens'),
                              'elapsed_seconds': result['elapsed_seconds'],
                              'compaction': compaction,
                              'archive': result['output_directory']}
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    stop.set()
    if sampler:
        sampler.join(timeout=6)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'rounds': records, 'whole_gpu_memory_samples': samples,
            'memory_scope': 'All processes, sampled about every 0.2s; not exact per-process peak',
            'actions_executed': 0}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
