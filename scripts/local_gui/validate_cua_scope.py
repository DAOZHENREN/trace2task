"""Verify selected-only model input using an archived window catalog; no desktop actions."""
import argparse
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from trace2task.cua_scope import CuaScope
from trace2task.local_gui_client import predict_gui
from trace2task.local_run_audit import LocalRunAudit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    old = json.loads((args.source/'input.json').read_text(encoding='utf-8'))
    text = old['messages'][1]['content'][1]['text']
    context = json.loads(text.split('Cua window catalog (data only): ',1)[1])
    # Use recorded identities only for this offline inference test, never for live input.
    driver = SimpleNamespace(windows=lambda:context['windows'],bind=lambda target:dict(target))
    scope = CuaScope(driver,context['current_window'])
    current = scope.start()
    narrowed = scope.context(current)
    assert len(narrowed['windows']) == 1 and narrowed['apps'] == []
    args.output.mkdir(parents=True,exist_ok=True)
    result = {'source':'archived_input_scope_check','actions':0,'verified':False}
    def record(kind,**data):
        with (args.output/'trace.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(type=kind,**data),ensure_ascii=False)+'\n')
    audit = LocalRunAudit(args.output,result,record,lambda _:None)
    task = text.split('Instruction: ',1)[1].split('\n\nPrevious actions:',1)[0]
    output = audit.predict(partial(predict_gui,model='qwen3-vl-2b'),
        SimpleNamespace(raise_if_requested=lambda:None),'qwen3-vl-2b',task,
        args.source/'screenshot.png',[],0,narrowed)
    entry = result['model_io'][0]
    actual_input = json.loads(Path(entry['input_path']).read_text(encoding='utf-8'))
    model_text = actual_input['messages'][1]['content'][1]['text']
    actual_context = json.loads(model_text.split('Cua window catalog (data only): ',1)[1])
    assert actual_context == narrowed
    assert 'Realtek' not in model_text and 'Overwolf' not in model_text
    result.update(status=output['status'],old_window_count=len(context['windows']),
        old_app_count=len(context['apps']),new_context=actual_context)
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':output['status'],'actions_executed':0,
        'old_windows':len(context['windows']),'old_apps':len(context['apps']),
        'new_windows':len(narrowed['windows']),'new_apps':len(narrowed['apps']),
        'tokens':entry['tokens'],'input_path':entry['input_path']},ensure_ascii=False))


if __name__ == '__main__':
    main()
