"""Browser integration tests with synthetic images/receipts, never desktop or model input."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from test_chat_agent_bridge import Session, Stop, _reply
from test_trace_evidence import recording
from test_trace_procedure import compile_fake, wait_job

from trace2task import browser_execution as browser
from trace2task.browser_execution import (
    BROWSER_TARGET,
    BrowserDriver,
    BrowserExecutionBackend,
    BrowserObservation,
)
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.chat_model_adapter import ChatModelAdapter, plan_schema
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ActionUnavailable, ObservationStale, UnifiedAction
from trace2task.procedure_evolution import prepare_evolution
from trace2task.web_console import WebConsoleController
from trace2task.windows_runner import EmergencyStopRequested


class Driver:
    def __init__(self, root, *args):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.calls = []
        self.closed = False
        self.state = {'url': browser.GAME_URL, 'generation': 1, 'width': 1280, 'height': 800}
        self.number = 0

    def start(self):
        pass

    def close(self):
        self.closed = True

    def call(self, op, **args):
        self.calls.append((op, args))
        if op == 'state':
            return dict(self.state)
        if op == 'observe':
            path = self.root / f'browser-{self.number:04d}.png'
            self.number += 1
            Image.new('RGB', (1280, 800), (self.number, 0, 0)).save(path)
            return {**self.state, 'filename': path.name,
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if op == 'action':
            return {'effect': 'unverifiable', 'delivery': {'mode': 'isolated_browser'},
                    'route': 'playwright_pixel_input'}
        raise AssertionError(op)


def action(skill, **args):
    return UnifiedAction.from_payload({'skill': skill, 'args': args})


def test_browser_schema_and_prompt_have_only_pixel_game_tools(tmp_path):
    schema = plan_schema(selected_windows=False, browser=True)
    alternatives = schema['properties']['actions']['items']['anyOf']
    assert {item['properties']['skill']['enum'][0] for item in alternatives} == set(browser.SKILLS)
    key = next(item for item in alternatives if item['properties']['skill']['enum'] == ['press_key'])
    assert 'f12' not in key['properties']['args']['properties']['key']['enum']
    session = Session([_reply([{'skill': 'wait', 'args': {'duration_ms': 100}}])])
    adapter = ChatModelAdapter(session, selected_windows=False, browser=True)
    adapter.predict('play', screenshot=tmp_path / 'image.png', history=[])
    prompt = session.requests[0]['prompt']
    assert 'isolated Ancient Beast browser viewport' in prompt
    assert 'No DOM, hidden game state' in prompt and 'primary desktop' not in prompt


@pytest.mark.parametrize('skill,args,expected', [
    ('click', {'x': 1, 'y': .5}, {'kind': 'click', 'x': 1279, 'y': 400, 'button': 'left', 'count': 1}),
    ('double_click', {'x': 0, 'y': 0, 'button': 'right'}, {'kind': 'click', 'x': 0, 'y': 0, 'button': 'right', 'count': 2}),
    ('move_cursor', {'x': .5, 'y': 1}, {'kind': 'move', 'x': 640, 'y': 799}),
    ('press_key', {'key': 's'}, {'kind': 'key', 'key': 's'}),
    ('press_key', {'key': 'up'}, {'kind': 'key', 'key': 'ArrowUp'}),
    ('scroll', {'direction': 'down', 'amount': 3, 'by': 'line'}, {'kind': 'scroll', 'dx': 0, 'dy': 120}),
])
def test_prepare_preserves_pixel_action_semantics(tmp_path, skill, args, expected):
    driver = Driver(tmp_path)
    backend = BrowserExecutionBackend(driver)
    assert backend.prepare(action(skill, **args), driver.state) == {'generation': 1, 'action': expected}
    assert driver.calls == []


@pytest.mark.parametrize('skill,args', [
    ('press_key', {'key': 'f12'}), ('hotkey', {'keys': ['ctrl', 'l']}),
    ('type_text', {'text': 'window.game'}), ('click', {'x': .1, 'y': .1, 'button': 'middle'}),
    ('scroll', {'direction': 'down', 'amount': 50, 'by': 'page'}),
    ('wait', {'duration_ms': 5001}),
])
def test_unsupported_actions_are_rejected_without_input(tmp_path, skill, args):
    driver = Driver(tmp_path)
    with pytest.raises(ActionUnavailable):
        BrowserExecutionBackend(driver).prepare(action(skill, **args), driver.state)
    assert driver.calls == []


def test_target_navigation_and_no_foreground_guards(tmp_path):
    driver = Driver(tmp_path)
    backend = BrowserExecutionBackend(driver)
    with pytest.raises(ValueError):
        backend.authorize(action('click', x=0, y=0), {'kind': 'desktop'})
    with pytest.raises(ValueError):
        backend.authorize(action('switch_window', pid=1, window_id=2), BROWSER_TARGET)
    with pytest.raises(ActionUnavailable, match='绝不回退'):
        backend.retry_foreground(None, None, BROWSER_TARGET, Stop())
    old = dict(driver.state)
    driver.state['generation'] = 2
    with pytest.raises(ObservationStale):
        backend.check_current(BROWSER_TARGET, old)
    assert not any(op == 'action' for op, _ in driver.calls)


def test_observation_binds_pixels_and_exposes_no_dom(tmp_path):
    driver = Driver(tmp_path)
    observer = BrowserObservation(driver)
    target, state, path = observer.observe(BROWSER_TARGET, 0)
    assert target == BROWSER_TARGET and path.is_file()
    context = observer.context(target, state, BrowserExecutionBackend(driver))
    assert set(context) == {'target', 'url', 'scope', 'capabilities'}
    assert context['capabilities']['no_dom_or_game_state']
    driver.call = lambda *args, **kw: {**state, 'sha256': '0' * 64}
    with pytest.raises(RuntimeError, match='哈希'):
        observer.observe(target, 1)


def test_uncertain_dispatch_stops_without_retry_or_foreground(tmp_path):
    driver = Driver(tmp_path)
    original = driver.call
    def fail(op, **args):
        if op == 'action':
            driver.calls.append((op, args))
            raise TimeoutError('uncertain')
        return original(op, **args)
    driver.call = fail
    core = ExecutionCore(BrowserExecutionBackend(driver), Stop(), lambda *a, **kw: None)
    with pytest.raises(TimeoutError):
        core.execute({'actions': [{'skill': 'click', 'args': {'x': .5, 'y': .5}}]},
                     target=BROWSER_TARGET, state=driver.state, observation_id='one')
    assert core.stopped
    assert len([op for op, _ in driver.calls if op == 'action']) == 1


def test_worker_transport_checks_reply_identity_and_stop(tmp_path):
    driver = BrowserDriver(tmp_path, lambda *a, **kw: None, Stop())
    driver.messages.put({'id': 2, 'ok': True})
    with pytest.raises(RuntimeError, match='身份'):
        driver._receive(1, 1)
    assert driver.broken
    def cancel():
        raise EmergencyStopRequested('cancel')
    driver = BrowserDriver(tmp_path, lambda *a, **kw: None,
                           SimpleNamespace(raise_if_requested=cancel))
    with pytest.raises(EmergencyStopRequested):
        driver._receive(0, 1)
    assert driver.broken


@pytest.mark.parametrize('provider', ['codex', 'api'])
def test_real_shared_loop_with_fake_browser_and_model(tmp_path, provider):
    driver = Driver(tmp_path / 'screenshots')
    session = Session([_reply([{'skill': 'click', 'args': {'x': .5, 'y': .5}}]),
                       _reply(complete=True), json.dumps({'verdict': 'complete',
                       'evidence': 'Synthetic visible result', 'missing': ''})])
    result = run_chat_agent(instruction='synthetic test', model='test-model', reasoning_effort='low',
                            output_root=tmp_path / 'runs', emergency_stop=Stop(), status_callback=lambda _: None,
                            executor_backend='browser', browser_driver=driver, session=session,
                            api_config=SimpleNamespace() if provider == 'api' else None)
    assert driver.closed and result['actions'] == 1 and result['browser_scope'] == BROWSER_TARGET
    assert result['mode'] == 'browser_baseline'
    assert result['model_io'][0]['execution']['delivery_mode_requested'] == 'isolated_browser'
    assert result['model_io'][0]['execution']['effect'] == 'unverifiable'
    assert len([op for op, _ in driver.calls if op == 'observe']) == 3
    assert result['verified'] is False
    assert result['completion_reviews'][-1]['independent'] is False


@pytest.mark.parametrize('options', [
    {'execution_scope': 'desktop'}, {'executor_backend': 'win32'}, {'provider': 'trained_d'},
    {'execute': False}, {'background': True}, {'cua_target': {'kind': 'desktop'}},
    {'orchestration': 'langgraph'}, {'resume_from': 'anything'},
])
def test_controller_rejects_incompatible_browser_modes_before_dispatch(tmp_path, monkeypatch, options):
    controller = WebConsoleController(tmp_path)
    monkeypatch.setattr(controller, '_run_job', lambda *a, **kw: pytest.fail('must not dispatch'))
    kwargs = {'task_path': '', 'instruction': 'play', 'execute': True, 'use_experience': False,
              'execution_scope': 'browser', 'executor_backend': 'browser'}
    kwargs.update(options)
    expected = 'Win32 执行后端已停用' if options.get('executor_backend') == 'win32' else '浏览器首版'
    with pytest.raises(ValueError, match=expected):
        controller.start_job(**kwargs)


def test_browser_run_preserves_b_hash_and_is_admissible_for_r(tmp_path, monkeypatch):
    from trace2task import chat_agent_runner, web_console
    trace, _ = recording(tmp_path, 2)
    parent = compile_fake(tmp_path, trace)['id']
    controller = WebConsoleController(tmp_path)
    monkeypatch.setattr(browser, 'browser_runtime', lambda: None)
    monkeypatch.setattr(web_console, 'ConsoleEmergencyStop', lambda _: Stop())
    monkeypatch.setattr(browser, 'BrowserDriver', Driver)
    real = chat_agent_runner.run_chat_agent
    session = Session([_reply([{'skill': 'click', 'args': {'x': .4, 'y': .4}}]),
                       _reply(complete=True), json.dumps({'verdict': 'complete',
                       'evidence': 'Synthetic result', 'missing': ''})])
    monkeypatch.setattr(chat_agent_runner, 'run_chat_agent', lambda **kw: real(**kw, session=session))
    job = controller.start_job(task_path=f'trace-library/{parent}/model-input.txt',
                               instruction='synthetic browser task', execute=True, use_experience=True,
                               executor_backend='browser', execution_scope='browser')
    finished = wait_job(controller, job['job_id'])
    assert finished['status'] == 'completed', finished.get('error')
    assert finished['run_facts']['operation_scope'] == 'browser'
    assert finished['run_facts']['input_mode'] == 'isolated_browser'
    assert finished['run_facts']['experience_id'] == parent
    assert finished['result']['executor_backend'] == 'browser'
    prepared = prepare_evolution(tmp_path, controller.run_store, job['job_id'])
    assert prepared['lineage']['parent_id'] == parent
