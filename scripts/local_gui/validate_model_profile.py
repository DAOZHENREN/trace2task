"""One synthetic screenshot through a registered engine; never imports an executor.

Owns an isolated worker/port and always stops it. Leaves a small JSON report and
the normal model-service artifacts for manual inspection, not a benchmark claim.
"""
import argparse
import base64
import importlib.util
import io
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from PIL import Image, ImageDraw

from trace2task.model_registry import GUI_MODELS, profile_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=GUI_MODELS, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8789)
    args = parser.parse_args()
    profile_for(args.model, 'llama-server')
    args.output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('profile_smoke_server', Path(__file__).with_name('server.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = module.Engine(args.root, args.output, backend='llama-server')
    engine.llama.port = args.port
    image = Image.new('RGB', (800, 500), '#eeeeee')
    draw = ImageDraw.Draw(image)
    draw.rectangle((480, 320, 680, 400), fill='#237352')
    draw.text((550, 350), 'SAVE', fill='white', font_size=24)
    draw.rectangle((150, 320, 350, 400), fill='#cccccc')
    draw.text((190, 350), 'CANCEL', fill='black', font_size=24)
    image.save(args.output / 'synthetic.png')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    try:
        outcome = engine.predict({'model': args.model, 'expected_backend': 'llama-server',
            'request_id': uuid.uuid4().hex, 'task': 'Click the center of the green SAVE button.',
            'image': base64.b64encode(buffer.getvalue()).decode(), 'step_index': 0})
        report = {key: outcome.get(key) for key in ('model', 'backend', 'status', 'prediction', 'raw_output', 'metrics', 'error')}
        report['input_was_synthetic'] = True
        report['desktop_input_sent'] = False
        (args.output / 'profile-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report['status'] != 'predicted':
            raise RuntimeError('Model did not return a valid action; inspect the report')
    finally:
        engine.llama.stop()


if __name__ == '__main__':
    main()
