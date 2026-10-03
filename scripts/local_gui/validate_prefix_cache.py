"""Offline GPU correctness/latency check; no desktop input or network."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import torch
from PIL import Image
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

from trace2task.local_gui_prefix_cache import TextPrefixCache

root = Path(sys.argv[1])
model = Qwen3VLForConditionalGeneration.from_pretrained(root, local_files_only=True,
    torch_dtype=torch.bfloat16, attn_implementation='sdpa').to('cuda').eval()
processor = AutoProcessor.from_pretrained(root, local_files_only=True, min_pixels=65536, max_pixels=65536)
cache = TextPrefixCache()
reports = []
for color in ('red', 'blue', 'green'):
    messages = [{'role':'system','content':('Identify the color. ' if color == 'green' else 'Describe the image briefly. ')*100},
                {'role':'user','content':[{'type':'image'}, {'type':'text','text':'What color is this?'}]}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=[Image.new('RGB',(256,256),color)],return_tensors='pt').to('cuda')
    with torch.inference_mode():
        torch.cuda.synchronize()
        started = time.perf_counter()
        baseline = model.generate(**inputs, max_new_tokens=24, do_sample=False)
        torch.cuda.synchronize()
        cold_seconds = time.perf_counter()-started
        started = time.perf_counter()
        past, report = cache.prepare(model, inputs)
        actual = model.generate(**inputs, past_key_values=past, max_new_tokens=24, do_sample=False)
        torch.cuda.synchronize()
        report.update(color=color, exact_tokens=bool(torch.equal(baseline,actual)),
            baseline_seconds=cold_seconds,cached_seconds=time.perf_counter()-started,
            output=processor.decode(actual[0,inputs.input_ids.shape[-1]:],skip_special_tokens=True))
        reports.append(report)
        del past, actual, baseline
print(json.dumps(reports,ensure_ascii=False,indent=2))
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
assert all(row['exact_tokens'] for row in reports)
assert reports[1]['status'] == 'hit'
assert reports[2]['status'] == 'miss'
