import json

import pytest

from trace2task.local_gui_protocol import decode, prompt


def tool(name, **args):
    return '<tool_call>'+json.dumps({'name':name,'arguments':args})+'</tool_call>'

@pytest.mark.parametrize('model,name,scale', [('gui-owl-2b','computer_use',1000),('mai-ui-2b','mobile_use',999)])
def test_coordinates(model,name,scale):
    value=decode(model,tool(name,action='click',coordinate=[scale,scale/2]))
    assert value['actions'][0]['args']=={'x':1.,'y':.5,'button':'left'}

def test_text_preserved():
    assert decode('gui-owl-2b',tool('computer_use',action='type',text='你好'))['actions'][0]['args']['text']=='你好'

def test_keys():
    assert decode('gui-owl-2b',tool('computer_use',action='key',keys=['CTRL','a']))['actions'][0]['skill']=='hotkey'

@pytest.mark.parametrize('text',[
    tool('mobile_use',action='open',text='Notepad'),
    tool('mobile_use',action='swipe',direction='down'),
    tool('mobile_use',action='system_button',button='home'),
    tool('mobile_use',action='click',coordinate=[1000,2]),
    tool('mobile_use',action='click',coordinate=[True,2]),
    tool('mobile_use',action='terminate',status='failure'),
    tool('shell',action='click',coordinate=[1,2]),
    tool('mobile_use',action='wait')*2,
    '<tool_call>{',
])
def test_unsupported_fails_closed(text):
    with pytest.raises(ValueError): decode('mai-ui-2b',text)

def test_done():
    assert decode('gui-owl-2b',tool('computer_use',action='terminate',status='success'))=={'actions':[{'done':True}]}


def test_non_object_tool_arguments_fail_closed():
    with pytest.raises(ValueError, match='Invalid tool arguments'):
        decode('gui-owl-2b', '<tool_call>{"name":"computer_use","arguments":[]}</tool_call>')

def test_qwen_schema():
    assert decode('qwen3-vl-2b','{"actions":[{"skill":"type_text","args":{"text":"你好"}}]}')['actions'][0]['args']['text']=='你好'
    with pytest.raises(ValueError): decode('qwen3-vl-2b','{"actions":[]}')
    with pytest.raises(ValueError): decode('qwen3-vl-2b','{"actions":[{"skill":"exec","args":{}}]}')

def test_model_allowlist():
    with pytest.raises(ValueError): prompt('../../unknown')
    with pytest.raises(ValueError): decode('../../unknown','{}')

def test_download_never_uses_proxy(monkeypatch):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('local_download',Path(__file__).parents[1]/'scripts/local_gui/download.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured=[]
    monkeypatch.setattr(module.subprocess,'run',lambda args,**kw:captured.append(args))
    module.curl('https://hf-mirror.com/test')
    command=captured[0]
    assert command[command.index('--noproxy')+1]=='*'
    assert command[command.index('--proto-redir')+1]=='=https'
    assert all(revision!='main' for repo,revision in module.REPOS.values())

@pytest.mark.parametrize('model',['qwen3-vl-2b','gui-owl-2b','mai-ui-2b'])
def test_controller_keeps_selected_model(tmp_path,monkeypatch,model):
    from trace2task.web_console import WebConsoleController
    controller=WebConsoleController(tmp_path)
    monkeypatch.setattr(controller,'_run_job',lambda *args:None)
    job=controller.start_job(task_path='',instruction='test',execute=True,model=model,
        provider='trained_d',execution_scope='desktop',use_experience=False,continuous=True)
    assert job['model']==model

@pytest.mark.parametrize('model',['qwen3-vl-2b','gui-owl-2b','mai-ui-2b'])
def test_runner_routes_model_without_real_input(tmp_path,monkeypatch,model):
    from types import SimpleNamespace

    import pygame

    from trace2task import local_gui_client, trained_model_runner
    calls=[]
    def predict(task, **kwargs):
        calls.append(kwargs)
        return {'status':'predicted','prediction':{'actions':[{'done':True}]}}
    monkeypatch.setattr(local_gui_client,'predict_gui',predict)
    monkeypatch.setattr(trained_model_runner,'WindowsMotorExecutor',lambda *a,**k:None)
    stop=SimpleNamespace(start=lambda:None,close=lambda:None,
                         raise_if_requested=lambda:None,sleep=lambda seconds:None)
    result=trained_model_runner.run_trained_desktop(instruction='test',model=model,
        output_root=tmp_path, emergency_stop=stop,status_callback=lambda _:None,
        approve=lambda _:None,continuous=True,
        backend=SimpleNamespace(foreground_handle=lambda:1),
        capture=SimpleNamespace(capture=lambda _:pygame.Surface((100,100))),size=lambda:(100,100))
    assert calls[0]['model']==model and calls[0]['history']==[]
    assert result['actions']==0 and not result['verified']
    assert model in result['trace_path']
