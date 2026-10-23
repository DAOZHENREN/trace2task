import hashlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from test_android_execution import phone  # noqa: F401 - shared synthetic ADB fixture
from test_chat_agent_bridge import Stop

from trace2task import mobileforge_adapter as mf
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.execution_protocol import ActionUnavailable
from trace2task.model_api import ModelAPIConfig


def response(action, **args):
    return '<tool_call>' + json.dumps({'name': 'mobile_use', 'arguments': {
        'action': action, **args}}) + '</tool_call>'


@pytest.mark.parametrize('raw,skill,args', [
    (response('click', coordinate=[500, 1000]), 'click', {'x': .5, 'y': 1., 'button': 'left'}),
    (response('long_press', coordinate=[0, 100], time=1.2), 'hold_mouse',
     {'x': 0., 'y': .1, 'button': 'left', 'duration_ms': 1200}),
    (response('swipe', coordinate=[0, 100], coordinate2=[1000, 500]), 'drag',
     {'start_x': 0., 'start_y': .1, 'end_x': 1., 'end_y': .5, 'button': 'left', 'duration_ms': 400}),
    (response('system_button', button='Back'), 'press_key', {'key': 'escape'}),
    (response('wait', time=.1), 'wait', {'duration_ms': 100}),
])
def test_action_mapping(raw, skill, args):
    result = mf.decode_mobileforge(raw)
    assert result['prediction']['actions'] == [{'skill': skill, 'args': args}]


@pytest.mark.parametrize('raw', [
    response('click', coordinate=[True, 2]), response('click', coordinate=[1001, 0]),
    response('click', coordinate=[float('nan'), 0]), response('click', coordinate=[0, 0], extra='shell'),
    response('click', coordinate=[0, 0]) * 2,
    '<tool_call>{"name":"mobile_use","name":"shell","arguments":{}}</tool_call>',
    '<tool_call>{"name":"shell","arguments":{"action":"click"}}</tool_call>',
    response('terminate', status='maybe'),
])
def test_malformed_rejected(raw):
    with pytest.raises(ValueError):
        mf.decode_mobileforge(raw)


@pytest.mark.parametrize('raw', [response('system_button', button='Home'),
    response('type', text='secret'), response('wait', time=20), response('reset'),
    response('uninstall'), response('shell', command='pm clear')])
def test_unsupported_rejected(raw):
    with pytest.raises(ActionUnavailable):
        mf.decode_mobileforge(raw)


def test_control_not_success():
    assert mf.decode_mobileforge(response('terminate', status='failure'))['control'] == 'terminate_failure'
    assert mf.decode_mobileforge(response('answer', text='blocked'))['control'] == 'answer'
    result = mf.decode_mobileforge(response('terminate', status='success'))
    assert result['prediction']['actions'] == [{'done': True}]


def test_source_hash_before_execution(tmp_path, monkeypatch):
    prompt = tmp_path / mf.PROMPT_FILE
    hint = tmp_path / mf.HINT_FILE
    prompt.parent.mkdir(parents=True)
    prompt.write_text("raise RuntimeError('must not import')\nSYSTEM_PROMPT = 'official fixture'\n", encoding='utf-8')
    hint.parent.mkdir(parents=True, exist_ok=True)
    hint.write_text('class HintManager: pass\n', encoding='utf-8')
    monkeypatch.setattr(mf, 'FILE_HASHES', {str(p.relative_to(tmp_path)).replace('\\', '/'):
        hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in (prompt, hint)})
    source = mf.MobileForgeSource(tmp_path)
    assert source.prompt == 'official fixture'
    hint.write_text("raise RuntimeError('injected')", encoding='utf-8')
    with pytest.raises(ValueError, match='pinned revision'):
        mf.MobileForgeSource(tmp_path)


def session_for(replies):
    pending = list(replies)
    calls = []
    def requester(config, payload, **kwargs):
        calls.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': pending.pop(0)}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
    session = mf.MobileForgeSession(ModelAPIConfig(api_key='test'), 'fixture', requester=requester)
    return session, calls


def test_adapter_native_wire_and_readonly_review(tmp_path):
    session, calls = session_for([response('click', coordinate=[100, 200]),
                                 '{"verdict":"unknown","evidence":"unclear","missing":"final state"}'])
    source = SimpleNamespace(prompt='official', provenance={'commit': 'fixture'})
    adapter = mf.MobileForgeAdapter(session, source, hints='previous hint')
    path = tmp_path / 'image.png'
    Image.new('RGB', (10, 10)).save(path)
    result = adapter.predict('task', screenshot=path, history=[])
    assert result['status'] == 'predicted'
    assert 'response_format' not in calls[0]
    assert 'previous hint' in calls[0]['messages'][1]['content'][0]['text']
    reviewed = adapter.predict('task', screenshot=path, history=[], context={'purpose': 'verify_completion'})
    assert reviewed['verification']['verdict'] == 'unknown'
    assert calls[1]['response_format'] == {'type': 'json_object'}
    session.close()
    with pytest.raises(RuntimeError, match='closed'):
        adapter.predict('task', screenshot=path, history=[])


def test_actual_shared_loop_with_synthetic_phone(phone, tmp_path):  # noqa: F811
    driver, data = phone
    driver.start = lambda: None
    session, calls = session_for([response('click', coordinate=[500, 500])])
    adapter = mf.MobileForgeAdapter(session, SimpleNamespace(prompt='fixture', provenance={'commit': 'fixture'}))
    result = run_chat_agent(instruction='test', model='fixture', reasoning_effort='default',
                            output_root=tmp_path / 'runs', emergency_stop=Stop(), status_callback=lambda _: None,
                            executor_backend='android', android_config={'serial': 'phone'}, android_driver=driver,
                            api_config=ModelAPIConfig(api_key='test'), session=session, model_adapter=adapter,
                            max_actions=1)
    assert result['actions'] == 1 and result['provider'] == 'api'
    assert result['model_adapter']['commit'] == 'fixture'
    inputs = [command[5:] for command in data['commands'] if command[5:7] == ['shell', 'input']]
    assert inputs == [['shell', 'input', 'tap', '640', '360']]
    assert result['final_screenshot']
    assert len(calls) == 1
