import json

import pytest

from trace2task.execution_protocol import COORDINATE_SPACE, PROTOCOL_VERSION, ActionUnavailable
from trace2task.local_gui_client import EXPERIENCE_CONTEXT_MAX_CHARS, validate_experience_context
from trace2task.local_gui_protocol import (
    TURN_TEMPLATE,
    adapt_capabilities,
    decode,
    prompt,
    render_turn_template,
    validate_prompt_profile,
)


def test_user_prompt_profile_requires_runtime_fields_and_preserves_user_braces():
    profile = validate_prompt_profile({'system_prompt': 'Custom system',
                                       'turn_template': TURN_TEMPLATE})
    assert profile['system_prompt'] == 'Custom system'
    rendered = render_turn_template(profile['turn_template'], task='show {{history}} literally',
                                    history='real history', cua_context_block='Cua capability')
    assert 'show {{history}} literally' in rendered
    assert 'real history' in rendered
    assert 'Cua capability' in rendered
    legacy_template = TURN_TEMPLATE.replace('execution_context_block', 'cua_context_block')
    legacy = validate_prompt_profile({'system_prompt': 'Old saved system',
                                      'turn_template': legacy_template})
    assert 'Legacy context' in render_turn_template(
        legacy['turn_template'], task='test', history='[]',
        execution_context_block='Legacy context')
    assert '{{execution_context_block}}' in TURN_TEMPLATE
    with pytest.raises(ValueError, match='缺少必要占位符'):
        validate_prompt_profile({'system_prompt': 'x', 'turn_template': '{{task}}'})


def tool(name, **args):
    return '<tool_call>'+json.dumps({'name':name,'arguments':args})+'</tool_call>'

@pytest.mark.parametrize('model,name,scale', [('gui-owl-2b','computer_use',1000),('mai-ui-2b','mobile_use',999)])
def test_coordinates(model,name,scale):
    value=decode(model,tool(name,action='click',coordinate=[scale,scale/2]))
    assert value['actions'][0]['args']=={'x':1.,'y':.5,'button':'left'}
    assert value['protocol_version'] == PROTOCOL_VERSION
    assert value['coordinate_space'] == COORDINATE_SPACE

def test_text_preserved():
    assert decode('gui-owl-2b',tool('computer_use',action='type',text='你好'))['actions'][0]['args']['text']=='你好'


@pytest.mark.parametrize('model,name,scale', [('gui-owl-2b','computer_use',1000),('mai-ui-2b','mobile_use',999)])
def test_complete_json_missing_close_requires_normal_generation_end(model, name, scale):
    text = tool(name, action='type', text='测试', coordinate=[scale/2, scale/2]).removesuffix('</tool_call>')
    with pytest.raises(ValueError):
        decode(model, text, cua=True)
    normalized = []
    value = decode(model, text, cua=True, generation_complete=True, normalizations=normalized)
    assert value['actions'][0]['args'] == {'text': '测试', 'x': .5, 'y': .5}
    assert normalized == ['complete_json_missing_tool_call_close']
    assert decode(model, text, generation_complete=True) == value


@pytest.mark.parametrize('text', [
    '<tool_call>{',
    '<tool_call>{"name":"mobile_use","arguments":{"action":"type","text":"oops"}',
    '<tool_call>{"name":"mobile_use","arguments":{"action":"type","text":"x","text":"y"}}',
    tool('mobile_use', action='type', text='test') + '<tool_call>{',
    tool('mobile_use', action='type', text='test') + '{}',
    tool('mobile_use', action='type', text='test') + 'do this too',
    tool('mobile_use', action='type', text='test') * 2,
    '<tool_call>[]',
])
def test_missing_delimiter_recovery_does_not_accept_ambiguous_or_broken_json(text):
    with pytest.raises(ValueError):
        decode('mai-ui-2b', text, cua=True, generation_complete=True)


@pytest.mark.parametrize('coordinate', [[True, 2], [0, 1000], [1], [float('nan'), 2]])
def test_invalid_native_text_coordinate(coordinate):
    with pytest.raises(ValueError):
        decode('mai-ui-2b', tool('mobile_use', action='type', text='test', coordinate=coordinate), cua=True)

def test_keys():
    assert decode('gui-owl-2b',tool('computer_use',action='key',keys=['CTRL','a']))['actions'][0]['skill']=='hotkey'

@pytest.mark.parametrize('text',[
    tool('mobile_use',action='open',text='Notepad'),
    tool('mobile_use',action='swipe',direction='down'),
    tool('mobile_use',action='system_button',button='home'),
    tool('mobile_use',action='click',coordinate=[1000,2]),
    tool('mobile_use',action='click',coordinate=[True,2]),
    tool('shell',action='click',coordinate=[1,2]),
    tool('mobile_use',action='wait')*2,
    '<tool_call>{',
])
def test_unsupported_fails_closed(text):
    with pytest.raises(ValueError): decode('mai-ui-2b',text)

def test_done():
    assert decode('gui-owl-2b',tool('computer_use',action='terminate',status='success')) == {
        'protocol_version': PROTOCOL_VERSION,
        'coordinate_space': COORDINATE_SPACE,
        'actions': [{'done': True}],
    }
    assert decode('mai-ui-2b', tool('mobile_use', action='terminate', status='failure')) == {
        'control': 'terminate_failure', 'text': 'Model reported task failure',
    }


def test_owl_native_pointer_actions_are_normalized_for_win32():
    move = decode('gui-owl-2b', tool('computer_use', action='mouse_move', coordinate=[200, 300]))
    assert move['actions'][0] == {'skill': 'move_cursor', 'args': {'x': .2, 'y': .3}}
    click = decode('gui-owl-2b', tool('computer_use', action='left_click'),
                   cursor_position=[.2, .3])
    assert click['actions'][0] == {'skill': 'click', 'args': {'x': .2, 'y': .3, 'button': 'left'}}
    drag = decode('gui-owl-2b', tool('computer_use', action='left_click_drag',
                                   coordinate=[700, 800]), cursor_position=[.2, .3])
    assert drag['actions'][0]['args'] == {
        'start_x': .2, 'start_y': .3, 'end_x': .7, 'end_y': .8,
        'button': 'left', 'duration_ms': 500,
    }
    scroll = decode('gui-owl-2b', tool('computer_use', action='hscroll', pixels=-3),
                    cursor_position=[.2, .3])
    assert scroll['actions'][0] == {'skill': 'scroll', 'args': {
        'direction': 'left', 'amount': 3, 'by': 'line',
    }}
    normalized = []
    triple = decode('gui-owl-2b', tool('computer_use', action='triple_click',
                                     coordinate=[100, 200]), normalizations=normalized)
    assert triple['actions'][0]['skill'] == 'double_click'
    assert normalized == ['triple_click_uses_official_double_click_fallback']


@pytest.mark.parametrize('action', [
    {'action': 'mouse_move', 'coordinate': [200, 300]},
    {'action': 'type', 'text': 'hello', 'coordinate': [200, 300]},
    {'action': 'scroll', 'pixels': -3},
])
def test_owl_native_decoding_is_independent_of_execution_backend(action):
    native = tool('computer_use', **action)
    assert decode('gui-owl-2b', native, cua=True) == decode('gui-owl-2b', native, cua=False)


def test_model_adapter_projects_backend_capabilities_without_mutating_them():
    backend = {'available_skills': ['click', 'move_cursor', 'switch_window', 'launch_app'],
               'unavailable_skills': {'drag': 'not supported'}}
    owl = adapt_capabilities('gui-owl-2b', backend)
    qwen = adapt_capabilities('qwen3-vl-2b', backend)
    mai = adapt_capabilities('mai-ui-2b', backend)
    assert owl['available_skills'] == ['click', 'move_cursor']
    assert owl['unavailable_skills'] == {'drag': 'not supported'}
    assert qwen['available_skills'] == ['click', 'move_cursor', 'switch_window', 'launch_app']
    assert mai['available_skills'] == ['click']
    assert mai['unavailable_skills'] == {'drag': 'not supported'}
    assert backend == {'available_skills': ['click', 'move_cursor', 'switch_window', 'launch_app'],
                       'unavailable_skills': {'drag': 'not supported'}}


def test_mai_windows_prompt_keeps_native_scroll_and_drag_for_both_backends():
    for cua in (False, True):
        text = prompt('mai-ui-2b', cua=cua)
        assert 'Windows scroll extension' in text
        assert 'Native drag:' in text
        assert '"action":"type"' in text


@pytest.mark.parametrize('model', ['gui-owl-2b', 'mai-ui-2b', 'qwen3-vl-2b'])
def test_model_action_format_prompt_does_not_change_with_execution_backend(model):
    assert prompt(model, cua=True) == prompt(model, cua=False)


@pytest.mark.parametrize('action', ['left_click', 'left_click_drag'])
def test_owl_implicit_cursor_is_never_guessed(action):
    args = {'action': action}
    if action == 'left_click_drag':
        args['coordinate'] = [700, 800]
    with pytest.raises(ActionUnavailable, match='cursor position'):
        decode('gui-owl-2b', tool('computer_use', **args))


@pytest.mark.parametrize(('action', 'text', 'control'), [
    ('interact', 'Please close the dialog', 'interact'),
    ('answer', 'The result is 3', 'answer'),
])
def test_owl_non_input_control_is_preserved(action, text, control):
    assert decode('gui-owl-2b', tool('computer_use', action=action, text=text)) == {
        'control': control, 'text': text,
    }
    assert decode('gui-owl-2b', tool('computer_use', action='terminate', status='failure')) == {
        'control': 'terminate_failure', 'text': 'Model reported task failure',
    }


def test_non_object_tool_arguments_fail_closed():
    with pytest.raises(ValueError, match='Invalid tool arguments'):
        decode('gui-owl-2b', '<tool_call>{"name":"computer_use","arguments":[]}</tool_call>')

def test_qwen_schema():
    assert decode('qwen3-vl-2b','{"actions":[{"skill":"type_text","args":{"text":"你好"}}]}')['actions'][0]['args']['text']=='你好'
    batch = decode('qwen3-vl-2b', '{"actions":['
        '{"skill":"click","args":{"x":0.2,"y":0.3}},'
        '{"skill":"type_text","args":{"text":"你好"}}]}')
    assert [action['skill'] for action in batch['actions']] == ['click', 'type_text']
    with pytest.raises(ValueError): decode('qwen3-vl-2b','{"actions":[]}')
    with pytest.raises(ValueError): decode('qwen3-vl-2b','{"actions":[{"skill":"exec","args":{}}]}')
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        decode('qwen3-vl-2b', '{"actions":[{"skill":"wait","skill":"click",'
                              '"args":{"duration_ms":100}}]}')

def test_model_allowlist():
    with pytest.raises(ValueError): prompt('../../unknown')
    with pytest.raises(ValueError): decode('../../unknown','{}')


def test_experience_context_validation_and_budget():
    context = {
        "task_id": "Notepad draft",
        "goal": "Type the requested text.",
        "completion": {"mode": "state"},
        "state_index": [{"id": "editing", "name": "Editing"}],
        "candidate_state": {"id": "editing", "name": "Editing"},
        "candidate_is_unverified": True,
        "nearby_states": [],
        "outgoing_transitions": [],
        "candidate_terminals": [],
        "human_guidance": None,
    }
    assert validate_experience_context(context) == context
    oversized = {**context, "goal": "x" * EXPERIENCE_CONTEXT_MAX_CHARS}
    with pytest.raises(ValueError, match="字符预算"):
        validate_experience_context(oversized)

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
