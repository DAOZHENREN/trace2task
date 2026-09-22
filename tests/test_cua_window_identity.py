"""Regression coverage for exact Cua HWND/PID identity checks."""
from types import SimpleNamespace

import pytest

from trace2task.cua_backend import CuaBackend
from trace2task.cua_scope import CuaScope

CHILD = {
    'pid': 41,
    'window_id': 101,
    'title': 'Child document',
    'app_name': 'host.exe',
    'bounds': {'x': 12, 'y': 34, 'width': 640, 'height': 480},
    'minimized': False,
}
ROOT = {
    **CHILD,
    'window_id': 501,
    'title': 'Top-level document host',
}
SAME_PID_NEIGHBOR = {**ROOT, 'window_id': 502, 'title': 'Other top-level child'}
NATIVE_CHILD = {**CHILD, 'root_target': {'pid': 41, 'window_id': 501}}
CHILD_TARGET = {'pid': 41, 'window_id': 101}
ROOT_TARGET = {'pid': 41, 'window_id': 501}


def backend(tmp_path):
    return CuaBackend(tmp_path, lambda *args, **kwargs: None)


def test_window_maps_child_to_exact_native_root_in_fresh_cua_list(tmp_path, monkeypatch):
    """A child HWND may map only to its PID-verified root that Cua lists now."""
    value = backend(tmp_path)
    calls = []
    value.call = lambda tool, payload: calls.append((tool, payload)) or {'windows': [ROOT]}
    monkeypatch.setattr('trace2task.cua_backend.inspect_window', lambda target: dict(NATIVE_CHILD))

    assert value.window(CHILD_TARGET) == ROOT
    assert calls == [('list_windows', {})]


@pytest.mark.parametrize('native', [
    RuntimeError('closed'),
    {**NATIVE_CHILD, 'pid': 99},
    {**NATIVE_CHILD, 'minimized': True},
])
def test_closed_pid_mismatch_or_minimized_native_root_is_refused(tmp_path, monkeypatch, native):
    value = backend(tmp_path)
    value.call = lambda tool, payload: {'windows': [ROOT]}
    def inspect(target):
        if isinstance(native, Exception):
            raise native
        return native
    monkeypatch.setattr('trace2task.cua_backend.inspect_window', inspect)

    with pytest.raises(RuntimeError):
        value.window(CHILD_TARGET)


def test_same_pid_unrelated_root_never_becomes_bound_target(tmp_path, monkeypatch):
    value = backend(tmp_path)
    value.call = lambda tool, payload: {'windows': [SAME_PID_NEIGHBOR]}
    monkeypatch.setattr('trace2task.cua_backend.inspect_window', lambda target: dict(NATIVE_CHILD))

    with pytest.raises(RuntimeError):
        value.bind(CHILD_TARGET)


def test_bind_returns_verified_root_identity_after_child_resolution(tmp_path, monkeypatch):
    value = backend(tmp_path)
    value.call = lambda tool, payload: {'windows': [ROOT]}
    monkeypatch.setattr('trace2task.cua_backend.inspect_window', lambda target: dict(NATIVE_CHILD))

    assert value.bind(CHILD_TARGET) == ROOT_TARGET


def test_backend_accepts_only_the_native_verified_cross_pid_root(tmp_path, monkeypatch):
    value = backend(tmp_path)
    cross_pid_root = {**ROOT, 'pid': 77}
    native_child = {**NATIVE_CHILD, 'root_target': {'pid': 77, 'window_id': 501}}
    value.call = lambda tool, payload: {'windows': [cross_pid_root]}
    monkeypatch.setattr('trace2task.cua_backend.inspect_window', lambda target: native_child)

    assert value.window(CHILD_TARGET) == cross_pid_root


def test_window_rechecks_effective_root_without_title_or_pid_substitution(tmp_path, monkeypatch):
    value = backend(tmp_path)
    value.call = lambda tool, payload: {'windows': [SAME_PID_NEIGHBOR]}
    monkeypatch.setattr(
        'trace2task.cua_backend.inspect_window',
        lambda target: (_ for _ in ()).throw(RuntimeError('closed')),
    )

    with pytest.raises(RuntimeError):
        value.window(ROOT_TARGET)


class IdentityDriver:
    def __init__(self, failure=None):
        self.failure = failure
        self.binds = []
        self.window_calls = []

    def bind(self, target):
        self.binds.append(dict(target))
        if self.failure:
            raise self.failure
        if (target.get('pid'), target.get('window_id')) != (41, 101):
            raise RuntimeError('wrong child HWND/PID')
        return dict(ROOT_TARGET)

    def window(self, target):
        self.window_calls.append(dict(target))
        if self.failure:
            raise self.failure
        if (target.get('pid'), target.get('window_id')) != (41, 501):
            raise RuntimeError('wrong effective HWND/PID')
        return dict(ROOT)

    def call(self, name, payload):
        assert name == 'list_apps'
        return {'apps': []}

    def windows(self):
        pytest.fail('scope must verify the authorized identity via driver.window')


def test_scope_start_binds_each_authorized_window_and_context_reverifies_identity():
    driver = IdentityDriver()
    scope = CuaScope(driver, {'pid': 41, 'window_id': 101})

    current = scope.start()
    context = scope.context(current)

    assert driver.binds == [{'pid': 41, 'window_id': 101}]
    assert driver.window_calls == [{'pid': 41, 'window_id': 501}]
    assert current == ROOT_TARGET
    assert context['current_window'] == ROOT_TARGET
    assert context['windows'] == [{key: ROOT[key] for key in ('pid', 'window_id', 'title', 'app_name')}]


def test_scope_propagates_identity_refusal_without_same_pid_fallback():
    driver = IdentityDriver(RuntimeError('closed or PID mismatch'))

    with pytest.raises(RuntimeError, match='closed or PID mismatch'):
        CuaScope(driver, {'pid': 41, 'window_id': 101}).start()
    assert driver.binds == [{'pid': 41, 'window_id': 101}]


def test_scope_refuses_post_start_root_remapping_without_mutating_authorization():
    root_a = {'pid': 41, 'window_id': 501}
    root_b = {'pid': 41, 'window_id': 502}

    class RemappingDriver:
        def __init__(self):
            self.binds = []

        def bind(self, target):
            self.binds.append(dict(target))
            return root_a if len(self.binds) == 1 else root_b

    driver = RemappingDriver()
    scope = CuaScope(driver, CHILD_TARGET)
    current = scope.start()

    with pytest.raises(RuntimeError):
        scope.switch(current)
    assert scope.windows == {(41, 501)}
    assert scope.bindings == [{'selected': CHILD_TARGET, 'effective': root_a}]


def test_requested_scope_is_recorded_before_initial_bind_failure(tmp_path, monkeypatch):
    from trace2task import cua_runner

    driver = IdentityDriver(RuntimeError('closed or PID mismatch'))
    driver.start = lambda: None
    driver.close = lambda: None
    monkeypatch.setattr(cua_runner, 'CuaBackend', lambda *args: driver)
    stop = SimpleNamespace(start=lambda: None, close=lambda: None,
                           raise_if_requested=lambda: None, sleep=lambda seconds: None)

    result = cua_runner.run_cua_local(
        instruction='test', output_root=tmp_path, emergency_stop=stop,
        status_callback=lambda message: None, model='qwen3-vl-2b', cua_target={'pid': 41, 'window_id': 101},
    )

    trace = (next(tmp_path.glob('*-cua')) / 'trace.jsonl').read_text(encoding='utf-8')
    assert result['stop_reason'] == 'error'
    assert '"requested_scope"' in trace
    assert trace.index('"requested_scope"') < trace.index('"error"')


def test_changed_effective_root_bounds_before_dispatch_sends_no_input(tmp_path, monkeypatch):
    from trace2task import cua_runner, local_gui_client

    class BoundsChangedDriver(IdentityDriver):
        def __init__(self):
            super().__init__()
            self.input_calls = []

        def start(self):
            return None

        def close(self):
            return None

        def observe(self, target, step):
            image = tmp_path / f'{step}.png'
            image.write_bytes(b'not a real screenshot')
            return {'window_bounds': ROOT['bounds'], 'screenshot_width': 640,
                    'screenshot_height': 480, 'elements_complete': True, 'elements': []}, image

        def window(self, target):
            value = super().window(target)
            if len(self.window_calls) > 1:  # The pre-dispatch check, after model inference.
                return {**value, 'bounds': {**value['bounds'], 'width': 641}}
            return value

        def call(self, name, payload):
            self.input_calls.append(name)
            return {'effect': 'confirmed'}

    driver = BoundsChangedDriver()
    monkeypatch.setattr(cua_runner, 'CuaBackend', lambda *args: driver)
    monkeypatch.setattr(
        local_gui_client,
        'predict_gui',
        lambda task, **kwargs: {'status': 'predicted', 'prediction': {
            'actions': [{'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}],
        }},
    )
    stop = SimpleNamespace(start=lambda: None, close=lambda: None,
                           raise_if_requested=lambda: None, sleep=lambda seconds: None)

    result = cua_runner.run_cua_local(
        instruction='test', output_root=tmp_path, emergency_stop=stop,
        status_callback=lambda message: None, model='qwen3-vl-2b', cua_target=CHILD_TARGET,
    )

    assert result['stop_reason'] == 'error'
    assert result['actions'] == 0
    assert driver.input_calls == []


def test_missing_window_rectangle_before_dispatch_sends_no_input(tmp_path, monkeypatch):
    from trace2task import cua_runner, local_gui_client

    class MissingBoundsDriver(IdentityDriver):
        def __init__(self):
            super().__init__()
            self.input_calls = []

        start = lambda self: None
        close = lambda self: None

        def observe(self, target, step):
            image = tmp_path / f'{step}.png'
            image.write_bytes(b'not a real screenshot')
            return {'screenshot_width': 640, 'screenshot_height': 480,
                    'elements_complete': True, 'elements': []}, image

        def window(self, target):
            return {key: value for key, value in super().window(target).items() if key != 'bounds'}

        def call(self, name, payload):
            self.input_calls.append(name)
            return {'effect': 'confirmed'}

    driver = MissingBoundsDriver()
    monkeypatch.setattr(cua_runner, 'CuaBackend', lambda *args: driver)
    monkeypatch.setattr(
        local_gui_client,
        'predict_gui',
        lambda task, **kwargs: {'status': 'predicted', 'prediction': {
            'actions': [{'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}],
        }},
    )
    stop = SimpleNamespace(start=lambda: None, close=lambda: None,
                           raise_if_requested=lambda: None, sleep=lambda seconds: None)

    result = cua_runner.run_cua_local(
        instruction='test', output_root=tmp_path, emergency_stop=stop,
        status_callback=lambda message: None, model='qwen3-vl-2b', cua_target=CHILD_TARGET,
    )

    assert result['stop_reason'] == 'error'
    assert result['actions'] == 0
    assert driver.input_calls == []


class FakeAncestor:
    argtypes = None
    restype = None

    def __init__(self, roots):
        self.roots = iter(roots)

    def __call__(self, hwnd, flag):
        assert flag == 2  # GA_ROOT
        return next(self.roots)


class FakeNativeBackend:
    def __init__(self, windows, roots):
        self.windows = windows
        self.user32 = SimpleNamespace(GetAncestor=FakeAncestor(roots))

    def get_window(self, hwnd):
        values = self.windows[hwnd]
        if isinstance(values, list):
            return values.pop(0)
        return values


def native_window(pid=41, title='Child document', minimized=False, process_name='host.exe'):
    return SimpleNamespace(process_id=pid, title=title, is_minimized=minimized,
                           process_name=process_name)


def install_native_backend(monkeypatch, windows, roots):
    from trace2task import cua_window

    monkeypatch.setattr(cua_window, 'Win32Backend', lambda: FakeNativeBackend(windows, roots))


def test_native_inspection_refuses_missing_or_pid_reused_original_window(monkeypatch):
    from trace2task.cua_window import inspect_window

    install_native_backend(monkeypatch, {101: None}, [501])
    with pytest.raises(RuntimeError, match='关闭'):
        inspect_window(CHILD_TARGET)

    install_native_backend(monkeypatch, {101: native_window(pid=99)}, [501])
    with pytest.raises(RuntimeError, match='进程已变化'):
        inspect_window(CHILD_TARGET)


def test_native_inspection_marks_root_minimized_and_accepts_verified_cross_pid_root(monkeypatch):
    from trace2task.cua_window import inspect_window

    child = native_window()
    root = native_window(pid=77, title='Top-level host', minimized=True)
    install_native_backend(monkeypatch, {101: [child, child], 501: root}, [501, 501])

    inspected = inspect_window(CHILD_TARGET)

    assert inspected['pid'] == 41
    assert inspected['window_id'] == 101
    assert inspected['minimized'] is True
    assert inspected['root_target'] == {'pid': 77, 'window_id': 501}


def test_native_inspection_refuses_reparenting_during_identity_check(monkeypatch):
    from trace2task.cua_window import inspect_window

    child = native_window()
    install_native_backend(
        monkeypatch,
        {101: [child, child], 501: native_window(), 502: native_window()},
        [501, 502],
    )

    with pytest.raises(RuntimeError, match='身份或宿主变化'):
        inspect_window(CHILD_TARGET)


def test_native_inspection_refuses_root_pid_reuse_during_identity_check(monkeypatch):
    from trace2task.cua_window import inspect_window

    child = native_window()
    install_native_backend(
        monkeypatch,
        {101: [child, child], 501: [native_window(pid=77), native_window(pid=88)]},
        [501, 501],
    )

    with pytest.raises(RuntimeError, match='宿主.*变化'):
        inspect_window(CHILD_TARGET)
