"""Replay archived screenshots for local inference only; never dispatch desktop input."""
import argparse
import base64
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    token = args.token_file.read_text().strip()
    def call(route, body, timeout=300):
        request = Request('http://127.0.0.1:8768/'+route,
            data=json.dumps(body,ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json','X-Local-Token':token})
        try:
            with build_opener(ProxyHandler({})).open(request,timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(error.read().decode('utf-8',errors='replace')) from None
    events = [json.loads(line) for line in args.trace.read_text(encoding='utf-8').splitlines()]
    inputs = [e for e in events if e['type'] == 'model_input']
    outputs = [e['output'] for e in events if e['type'] == 'model_output']
    results = []
    body = None
    for i, (event, old) in enumerate(zip(inputs[:3],outputs[:3])):
        service_input = json.loads((Path(old['output_directory'])/'input.json').read_text(encoding='utf-8'))
        text = service_input['messages'][1]['content'][1]['text']
        context = json.loads(text.split('Cua window catalog (data only): ',1)[1])
        body = {'model': 'qwen3-vl-2b','task': event['task'],'history': event['history'],'step_index': i,
            'image': base64.b64encode(Path(event['screenshot']).read_bytes()).decode(),
            'cua_context': context,'request_id': uuid.uuid4().hex}
        result = call('predict',body)
        result.pop('annotated_image',None)
        results.append(result)
        (args.output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'round':i+1,'status':result['status'],'metrics':result.get('metrics')},ensure_ascii=False),flush=True)
        if result['status'] != 'predicted':
            return
    if body is None:
        raise ValueError('No completed prediction in trace')
    body['request_id'] = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(call,'predict',body)
        time.sleep(2)
        started = time.perf_counter()
        receipt = call('cancel',{'request_id':body['request_id']},timeout=5)
        result = future.result(timeout=60)
        result.pop('annotated_image',None)
        result['cancel_receipt'] = receipt
        result['cancel_to_response_ms'] = (time.perf_counter()-started)*1000
        results.append(result)
        (args.output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'cancel_status':result['status'],'cancel_to_response_ms':result['cancel_to_response_ms'],
                          'metrics':result.get('metrics')},ensure_ascii=False),flush=True)
        if result['status'] != 'cancelled':
            raise RuntimeError('Cancellation was not confirmed')

    # Exercise the application client/stop wrapper and unified archive, still no executor.
    from trace2task.local_gui_client import predict_gui
    from trace2task.local_run_audit import LocalRunAudit
    from trace2task.windows_runner import EmergencyStopRequested
    stop_event = threading.Event()
    class Stop:
        def raise_if_requested(self):
            if stop_event.is_set():
                raise EmergencyStopRequested('acceptance test stop')
    archive = args.output/'client-cancel'
    archive.mkdir(exist_ok=True)
    result = {'actions':0,'verified':False,'trace_path':str(archive/'trace.jsonl')}
    def record(kind, **value):
        with (archive/'trace.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(type=kind,**value),ensure_ascii=False)+'\n')
    audit = LocalRunAudit(archive,result,record,print)
    timer = threading.Timer(2,stop_event.set)
    timer.start()
    started = time.perf_counter()
    try:
        audit.predict(partial(predict_gui,model='qwen3-vl-2b'),Stop(),'qwen3-vl-2b',body['task'],
            inputs[2]['screenshot'],body['history'],0,body['cua_context'])
        raise RuntimeError('Expected client stop')
    except EmergencyStopRequested:
        result['stop_reason'] = 'emergency_stop'
    finally:
        timer.cancel()
        audit.finish(time.perf_counter()-started)
        (archive/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    entry = result['model_io'][0]
    assert entry['status'] == 'cancelled'
    assert Path(entry['input_path']).is_file()
    assert json.loads(Path(entry['response_path']).read_text(encoding='utf-8'))['status'] == 'cancelled'
    assert (Path(entry['output_directory'])/'raw-output.json').is_file()
    print('Client stop + actual prompt/partial output archive: PASS',flush=True)


if __name__ == '__main__':
    main()
