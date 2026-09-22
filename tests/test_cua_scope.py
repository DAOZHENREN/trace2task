import json
from types import SimpleNamespace

import pytest

from trace2task.cua_scope import CuaScope, normalize_scope

CALCULATOR = {'pid': 10, 'window_id': 20}
NOTEPAD = {'pid': 11, 'window_id': 21}
REALTEK = {'pid': 10, 'window_id': 22}  # Shared ApplicationFrameHost.exe PID!


class Driver:
    def __init__(self):
        self.rows = [dict(**CALCULATOR, title='计算器', app_name='ApplicationFrameHost.exe'),
                     dict(**NOTEPAD, title='记事本', app_name='Notepad.exe'),
                     dict(**REALTEK, title='Realtek', app_name='ApplicationFrameHost.exe')]
        self.calls = []
        self.binds = []
    def windows(self): return self.rows
    def window(self, target):
        matches = [row for row in self.rows
                   if (row.get('pid'), row.get('window_id')) ==
                   (target.get('pid'), target.get('window_id'))]
        if len(matches) != 1 or matches[0].get('minimized'):
            raise RuntimeError('selected window is unavailable')
        return dict(matches[0])
    resolve_window = window
    def call(self, name, payload):
        self.calls.append((name, payload))
        assert name == 'list_apps'
        return {'apps': [{'name':'记事本', 'launch_path':'notepad.exe'},
                         {'name':'无关应用', 'launch_path':'unrelated.exe'}]}
    def bind(self, target):
        self.binds.append(target)
        if 'launch_path' in target:
            assert target['launch_path'] == 'notepad.exe'
            return NOTEPAD
        assert target in [CALCULATOR, NOTEPAD, REALTEK]
        window = self.window(target)
        return {key: window[key] for key in ('pid', 'window_id')}


def test_single_legacy_target_has_no_full_catalog_or_shared_pid_neighbor():
    driver = Driver()
    scope = CuaScope(driver, CALCULATOR)
    current = scope.start()
    context = scope.context(current)
    assert len(context['windows']) == 1
    assert context['windows'][0]['title'] == '计算器'
    assert context['apps'] == []
    assert driver.calls == []  # No full installed-app enumeration needed.
    assert 'Realtek' not in json.dumps(context)
    with pytest.raises(ValueError):
        scope.switch(REALTEK)
    with pytest.raises(ValueError):
        scope.switch(NOTEPAD)
    with pytest.raises(ValueError):
        scope.launch(0)
    assert driver.binds == [CALCULATOR]


def test_multiple_windows_and_nonzero_initial():
    scope = CuaScope(Driver(), {'targets':[CALCULATOR, NOTEPAD], 'initial_index':1})
    assert scope.start() == NOTEPAD
    assert {w['window_id'] for w in scope.context(NOTEPAD)['windows']} == {20,21}
    assert scope.switch(CALCULATOR) == CALCULATOR


def test_selected_launchable_app_only_added_after_explicit_launch():
    driver = Driver()
    scope = CuaScope(driver, {'targets':[CALCULATOR, {'launch_path':'notepad.exe'}], 'initial_index':0})
    scope.start()
    context = scope.context(CALCULATOR)
    assert context['apps'] == [{'app_id':0, 'name':'记事本'}]
    assert len(context['windows']) == 1
    assert scope.launch(0) == NOTEPAD
    assert len(scope.context(NOTEPAD)['windows']) == 2
    assert '无关应用' not in json.dumps(scope.context(NOTEPAD), ensure_ascii=False)
    with pytest.raises(ValueError): scope.launch(1)


@pytest.mark.parametrize('value', [None, {}, {'targets':[],'initial_index':0},
    {'targets':[CALCULATOR],'initial_index':1}, {'targets':[CALCULATOR],'initial_index':True},
    {'targets':[CALCULATOR,CALCULATOR],'initial_index':0}, {'pid':True,'window_id':20},
    {'targets':[CALCULATOR]*13,'initial_index':0}, {'launch_path':''}])
def test_invalid_scope_never_becomes_global(value):
    with pytest.raises(ValueError): normalize_scope(value)


def test_missing_selected_window_fails_without_using_another_same_process():
    driver = Driver()
    driver.rows = [driver.rows[-1]]
    with pytest.raises(RuntimeError): CuaScope(driver, CALCULATOR).start()
    assert driver.binds == [CALCULATOR]


def test_runner_passes_only_selected_scope_and_blocks_invented_switch(tmp_path, monkeypatch):
    from trace2task import cua_runner, local_gui_client
    driver = Driver()
    driver.start = lambda: None
    driver.close = lambda: None
    def observe(target, step):
        image = tmp_path/f'{step}.png'
        image.write_bytes(b'fake PNG not passed to a real model')
        return {'window_bounds':{}}, image
    driver.observe = observe
    monkeypatch.setattr(cua_runner,'CuaBackend',lambda *a:driver)
    received = []
    def predict(task, **kwargs):
        received.append(kwargs['cua_context'])
        return {'status':'predicted','prediction':{'actions':[{'skill':'switch_window','args':REALTEK}]}}
    monkeypatch.setattr(local_gui_client,'predict_gui',predict)
    stop = SimpleNamespace(start=lambda:None,close=lambda:None,raise_if_requested=lambda:None,sleep=lambda _:None)
    result = cua_runner.run_cua_local(instruction='test', output_root=tmp_path,
        emergency_stop=stop,status_callback=lambda _:None,model='qwen3-vl-2b',cua_target=CALCULATOR)
    assert result['actions'] == 0 and result['stop_reason'] == 'error'
    assert len(received[0]['windows']) == 1 and received[0]['apps'] == []
    assert driver.binds == [CALCULATOR, CALCULATOR]
    assert 'Realtek' not in json.dumps(received)


def test_runner_stops_after_an_unverifiable_delivery(tmp_path, monkeypatch):
    """An uncertain driver receipt must not trigger another model round or retry."""
    from trace2task import cua_runner, local_gui_client

    class UncertainDriver(Driver):
        def __init__(self):
            super().__init__()
            self.action_calls = []

        def start(self):
            pass

        def close(self):
            pass

        def observe(self, target, step):
            image = tmp_path / f'{step}.png'
            image.write_bytes(b'not provided to a real model')
            return {
                'pid': target['pid'], 'window_id': target['window_id'], 'session': 'test',
                'screenshot_width': 100, 'screenshot_height': 100,
                'window_bounds': {'x': 0, 'y': 0, 'width': 100, 'height': 100},
                'elements_complete': True, 'elements': [],
            }, image

        def window(self, target):
            value = super().window(target)
            value['bounds'] = {'x': 0, 'y': 0, 'width': 100, 'height': 100}
            return value

        def call(self, name, payload):
            self.action_calls.append((name, payload))
            if name == 'click':
                return {'effect': 'unverifiable'}
            return super().call(name, payload)

    driver = UncertainDriver()
    monkeypatch.setattr(cua_runner, 'CuaBackend', lambda *args: driver)
    predictions = []

    def predict(task, **kwargs):
        predictions.append(kwargs)
        return {'status': 'predicted', 'prediction': {'actions': [
            {'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}]}}

    monkeypatch.setattr(local_gui_client, 'predict_gui', predict)
    stop = SimpleNamespace(start=lambda: None, close=lambda: None,
                           raise_if_requested=lambda: None, sleep=lambda _: None)

    result = cua_runner.run_cua_local(
        instruction='test', output_root=tmp_path, emergency_stop=stop,
        status_callback=lambda _: None, model='qwen3-vl-2b', cua_target=CALCULATOR)

    assert result['actions'] == 1
    assert result['stop_reason'] == 'effect_unverifiable'
    assert result['history'][-1]['effect'] == 'unverifiable'
    assert len(predictions) == 1
