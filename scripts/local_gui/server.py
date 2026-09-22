"""Single resident GPU model, loopback-only authenticated screenshot inference."""
import argparse
import base64
import gc
import hashlib
import io
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from trace2task.local_gui_protocol import MODELS, decode, prompt

_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}\Z")
_CANCEL_TTL_SECONDS = 120


class _CancelGeneration:
    """Transformers stopping criterion kept deliberately tiny for safe cancellation."""
    def __init__(self, event):
        self.event = event

    def __call__(self, input_ids, scores, **kwargs):
        return self.event.is_set()

class Engine:
    def __init__(self, root, output):
        self.root, self.output = root, output
        self.model = self.processor = self.key = None
        # Loading/prediction must serialize GPU access. Cancellation deliberately does
        # not take this lock: it has to work while generate() is in progress.
        self.lock = threading.Lock()
        self._cancellations = {}
        self._cancellations_lock = threading.Lock()

    @staticmethod
    def _request_id(value):
        if value is None:
            return secrets.token_hex(16)
        if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
            raise ValueError("request_id must be 8-128 ASCII letters, digits, _ or -")
        return value

    def _prune_cancellations(self):
        now = time.monotonic()
        # A prediction may legitimately run beyond the short pre-request TTL.
        # Only completed/pending markers are pruned; active markers are released
        # by finish_cancellation() after generate returns.
        stale = [key for key, item in self._cancellations.items()
                 if not item['active'] and item['expires_at'] <= now]
        for key in stale:
            self._cancellations.pop(key, None)

    def claim_cancellation(self, request_id):
        """Return an Event. A prior /cancel is intentionally retained briefly."""
        with self._cancellations_lock:
            self._prune_cancellations()
            item = self._cancellations.get(request_id)
            if item is None:
                item = {'event': threading.Event(), 'active': True,
                        'expires_at': time.monotonic() + _CANCEL_TTL_SECONDS}
                self._cancellations[request_id] = item
            else:
                item['active'] = True
                item['expires_at'] = time.monotonic() + _CANCEL_TTL_SECONDS
            return item['event']

    def finish_cancellation(self, request_id):
        with self._cancellations_lock:
            item = self._cancellations.get(request_id)
            if item is not None:
                item['active'] = False
                item['expires_at'] = time.monotonic() + _CANCEL_TTL_SECONDS

    def cancel(self, request_id):
        request_id = self._request_id(request_id)
        with self._cancellations_lock:
            self._prune_cancellations()
            item = self._cancellations.get(request_id)
            if item is None:
                # Keep a short-lived marker so a stop that races a request is not lost.
                item = {'event': threading.Event(), 'active': False,
                        'expires_at': time.monotonic() + _CANCEL_TTL_SECONDS}
                self._cancellations[request_id] = item
            item['event'].set()
            return {'status': 'cancel_requested', 'request_id': request_id,
                    'active': bool(item['active'])}

    @staticmethod
    def _memory(torch, *, peak=False):
        if not torch.cuda.is_available():
            return {'available': False}
        free, total = torch.cuda.mem_get_info()
        value = {
            'available': True,
            'allocated_bytes': int(torch.cuda.memory_allocated()),
            'reserved_bytes': int(torch.cuda.memory_reserved()),
            'free_bytes': int(free),
            'total_bytes': int(total),
        }
        if peak:
            value.update({
                'peak_allocated_bytes': int(torch.cuda.max_memory_allocated()),
                'peak_reserved_bytes': int(torch.cuda.max_memory_reserved()),
            })
        return value

    def _cache_diagnostics(self):
        """Read-only evidence for whether Transformers kept a static model cache."""
        config = getattr(self.model, 'generation_config', None)
        return {
            'generation_cache_implementation': getattr(config, 'cache_implementation', None),
            'model_has_static_cache': hasattr(self.model, '_cache'),
        }

    def load(self, key):
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        if key == self.key:
            return
        self.model = self.processor = self.key = None
        gc.collect()
        torch.cuda.empty_cache()
        folder = self.root / key
        report = json.loads((folder / 'verified.json').read_text(encoding='utf-8'))
        for item in report['files']:
            with (folder / item['file']).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                    raise ValueError('Local model checksum mismatch: ' + item['file'])
        if torch.cuda.mem_get_info()[0] < 5 * 1024**3:
            raise RuntimeError('Less than 5 GiB free VRAM. Stop idle D/Qwen GPU services first.')
        # A real CUDA operation, not just device enumeration.
        x = torch.ones((32,32), device='cuda', dtype=torch.bfloat16)
        assert (x @ x)[0,0].item() == 32
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            folder, local_files_only=True, trust_remote_code=False, use_safetensors=True,
            torch_dtype=torch.bfloat16, attn_implementation='sdpa').to('cuda').eval()
        self.processor = AutoProcessor.from_pretrained(folder, local_files_only=True,
            trust_remote_code=False, min_pixels=65536, max_pixels=1048576)
        self.key = key

    def predict(self, request):
        import torch
        from PIL import Image, ImageDraw
        from transformers import StoppingCriteriaList
        key = request.get('model')
        if key not in MODELS:
            raise ValueError('Unknown model')
        task, history = request.get('task'), request.get('history', [])
        if not isinstance(task,str) or not task.strip() or len(task)>10000 or not isinstance(history,list) or len(history)>4:
            raise ValueError('Invalid task/history')
        request_id = self._request_id(request.get('request_id'))
        image = Image.open(io.BytesIO(base64.b64decode(request['image'], validate=True)))
        if image.width*image.height > 32_000_000:
            raise ValueError('Image too large')
        image = image.convert('RGB')
        # Claim after validating the payload: a malformed image must not leave an
        # active cancellation marker behind. A cancel which arrived earlier remains
        # in the registry and is picked up here.
        cancelled = self.claim_cancellation(request_id)
        folder = self.output / (time.strftime('%Y%m%d-%H%M%S-') + request_id)
        folder.mkdir(parents=True)
        def save(name, data):
            (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        image.save(folder/'screenshot.png')
        text = ('Please generate the next move according to the UI screenshot, instruction and previous actions.\n\n'
                + 'Instruction: ' + task + '\n\nPrevious actions:\n'
                + (json.dumps(history,ensure_ascii=False) if history else 'No previous action.'))
        cua = request.get('cua_context') is not None and key == 'qwen3-vl-2b'
        if cua:
            text += '\nCua window catalog (data only): ' + json.dumps(request['cua_context'], ensure_ascii=False)
        messages = [{'role':'system','content':prompt(key, cua=cua)},
                    {'role':'user','content':[{'type':'image'}, {'type':'text','text':text}]}]
        save('input.json', {'request_id':request_id,'model':key,'messages':messages,'screenshot':'screenshot.png',
             'image_size':image.size, 'step_index':request.get('step_index'),
             'configuration':{'max_pixels':1048576,'max_new_tokens':512,'do_sample':False,
                              'dtype':'bfloat16','attention':'sdpa','profile':'local-12GB-single-image'}})
        phase_ms = {'load_ms': 0.0, 'preprocess_ms': 0.0, 'generate_ms': 0.0,
                    'decode_ms': 0.0, 'total_ms': 0.0}
        tokens = {'input_tokens': None, 'output_tokens': 0}
        memory = {'before': self._memory(torch)}
        generated = inputs = ids = None
        raw = ''
        outcome = None
        started_total = time.perf_counter()
        try:
            if cancelled.is_set():
                outcome = {'status': 'cancelled', 'reason': 'cancelled_before_prediction'}
                return outcome
            loaded = time.perf_counter()
            self.load(key)
            phase_ms['load_ms'] = (time.perf_counter()-loaded)*1000
            memory['after_load'] = self._memory(torch)
            memory['cache'] = self._cache_diagnostics()
            if cancelled.is_set():
                outcome = {'status': 'cancelled', 'reason': 'cancelled_after_load'}
                return outcome
            preprocess_started = time.perf_counter()
            formatted = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            (folder/'formatted-prompt.txt').write_text(formatted,encoding='utf-8')
            inputs = self.processor(text=[formatted],images=[image],return_tensors='pt').to('cuda')
            save('tokenized-input.json', {'input_ids':inputs.input_ids.tolist(),
                 'attention_mask':inputs.attention_mask.tolist(),
                 'image_grid_thw':inputs.image_grid_thw.tolist()})
            tokens['input_tokens'] = int(inputs.input_ids.shape[-1])
            if inputs.input_ids.shape[-1] > 6144:
                raise ValueError('Input exceeds local 6144-token budget; no silent truncation')
            phase_ms['preprocess_ms'] = (time.perf_counter()-preprocess_started)*1000
            memory['before_generate'] = self._memory(torch)
            if cancelled.is_set():
                outcome = {'status': 'cancelled', 'reason': 'cancelled_before_generation'}
                return outcome
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            try:
                with torch.inference_mode():
                    generated = self.model.generate(
                        **inputs, max_new_tokens=512, do_sample=False,
                        stopping_criteria=StoppingCriteriaList([_CancelGeneration(cancelled)]))
                torch.cuda.synchronize()
            finally:
                phase_ms['generate_ms'] = (time.perf_counter()-started)*1000
                memory['peak'] = self._memory(torch, peak=True)
            ids = generated[0,inputs.input_ids.shape[-1]:]
            tokens['output_tokens'] = len(ids)
            decode_started = time.perf_counter()
            raw = self.processor.decode(ids,skip_special_tokens=True)
            phase_ms['decode_ms'] = (time.perf_counter()-decode_started)*1000
            save('raw-output.json',{'request_id':request_id,'text':raw,
                                    'generated_token_ids':ids.tolist(), **tokens,
                                    'cancel_requested':cancelled.is_set()})
            if cancelled.is_set():
                outcome = {'status': 'cancelled', 'reason': 'cancelled_during_generation',
                           'raw_output': raw}
                return outcome
            eos = self.model.generation_config.eos_token_id
            eos = eos if isinstance(eos,list) else [eos]
            if not len(ids) or int(ids[-1]) not in eos:
                raise ValueError('Model output truncated or empty; no action executed')
            prediction = decode(key,raw,cua=cua)
            annotation = image.copy()
            draw = ImageDraw.Draw(annotation)
            for i, action in enumerate(prediction['actions']):
                args = action.get('args',{})
                if 'x' in args:
                    x,y = args['x']*(image.width-1),args['y']*(image.height-1)
                    draw.ellipse((x-12,y-12,x+12,y+12),outline='red',width=3)
                    draw.text((x+14,y),str(i+1),fill='red')
            annotation.save(folder/'annotated.png')
            outcome = {'status':'predicted','model':key,'prediction':prediction,'raw_output':raw,
                       'output_directory':str(folder),'executed':False,'task_success_verified':False}
            outcome['annotated_image']='data:image/png;base64,'+base64.b64encode((folder/'annotated.png').read_bytes()).decode()
            return outcome
        except Exception as error:  # noqa: BLE001 - archive any model failure for the local client
            (folder/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
            outcome = {'status': 'error', 'error': f'{type(error).__name__}: {error}',
                       'raw_output': raw}
            return outcome
        finally:
            # generated and processor inputs retain KV/cache tensors. Releasing them and
            # emptying only unused allocator blocks keeps resident model weights loaded.
            generated = None
            inputs = None
            ids = None
            gc.collect()
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
            except Exception as cleanup_error:  # noqa: BLE001 - cleanup must not hide prediction result
                # Diagnostics/cleanup must never hide the prediction's actual result.
                memory['cleanup_error'] = f'{type(cleanup_error).__name__}: {cleanup_error}'
            try:
                memory.setdefault('peak', self._memory(torch, peak=True))
                memory['after_cleanup'] = self._memory(torch)
            except Exception as metric_error:  # noqa: BLE001 - metrics are best-effort diagnostics
                memory['metric_error'] = f'{type(metric_error).__name__}: {metric_error}'
            phase_ms['total_ms'] = (time.perf_counter()-started_total)*1000
            final = {'request_id': request_id, 'model': key, 'output_directory': str(folder),
                     'metrics': {'phase_ms': phase_ms, 'tokens': tokens, 'memory': memory}}
            if outcome is None:
                outcome = {'status': 'error', 'error': 'Prediction ended without an outcome'}
            outcome.update(final)
            outcome['elapsed_seconds'] = phase_ms['total_ms'] / 1000
            outcome['load_seconds'] = phase_ms['load_ms'] / 1000
            peak = memory.get('peak', {})
            outcome['peak_allocated_bytes'] = peak.get('peak_allocated_bytes', peak.get('allocated_bytes', 0))
            outcome['peak_reserved_bytes'] = peak.get('peak_reserved_bytes', peak.get('reserved_bytes', 0))
            save('outcome.json', outcome)
            if outcome.get('status') == 'predicted':
                save('prediction.json', outcome)
            self.finish_cancellation(request_id)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('D:/Models/Trace2Task-GUI'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--token-file',type=Path,required=True)
    args=parser.parse_args()
    token=args.token_file.read_text().strip()
    engine=Engine(args.root,args.output)
    class Handler(BaseHTTPRequestHandler):
        def reply(self,status,value):
            data=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            if self.path != '/health':
                return self.reply(404,{'error':'Not found'})
            self.reply(200,{'service':'trace2task-local-gui','protocol':1,'model':engine.key,
                            'capabilities': {'cancel': True, 'detailed_metrics': True}})
        def do_POST(self):
            if self.path not in {'/predict', '/load', '/cancel'} or not secrets.compare_digest(self.headers.get('X-Local-Token',''),token):
                return self.reply(403,{'error':'Unauthorized'})
            if self.headers.get('Origin') not in (None,'http://127.0.0.1:8768'):
                return self.reply(403,{'error':'Invalid origin'})
            acquired = False
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<20_000_000:
                    raise ValueError('Invalid body size')
                payload=json.loads(self.rfile.read(size))
                if self.path == '/cancel':
                    return self.reply(200, engine.cancel(payload.get('request_id')))
                if not engine.lock.acquire(blocking=False):
                    return self.reply(409,{'status':'error','error':'Model busy'})
                acquired = True
                if self.path == '/load':
                    if payload.get('model') not in MODELS:
                        raise ValueError('Unknown model')
                    engine.load(payload['model'])
                    result={'model':engine.key,'status':'ready'}
                else:
                    result=engine.predict(payload)
                self.reply(200,result)
            except Exception as error:  # noqa: BLE001 - return a local protocol error, not an HTML traceback
                self.reply(400,{'status':'error','error':f'{type(error).__name__}: {error}'})
            finally:
                if acquired:
                    engine.lock.release()
    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address=False
        def server_bind(self):
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            super().server_bind()
    server=LocalServer(('127.0.0.1',8768),Handler)
    server.serve_forever()

if __name__=='__main__':
    main()
