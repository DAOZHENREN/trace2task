import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from trace2task import child_desktop
from trace2task.cua_backend import CuaBackend
from trace2task.execution_protocol import ObservationStale
from trace2task.windows_runner import EmergencyStopRequested


@pytest.mark.parametrize('initial', ['gone', 'present', 'unknown'])
def test_slow_host_close_tracks_the_actual_windows_logon(tmp_path, monkeypatch, initial):
    workspace = child_desktop.ChildDesktopWorkspace()
    host = child_desktop._ChildDesktopHost(tmp_path / 'driver.exe')
    workspace._host = host
    host.session_id = 23
    host.host_dir = Mock()
    process = host.process = Mock()
    process.wait.side_effect = subprocess.TimeoutExpired('child host', 15)
    process.poll.return_value = None
    process.kill.side_effect = lambda: setattr(process.poll, 'return_value', 1)
    state = initial

    def exists(session_id):
        assert session_id == 23
        if state == 'unknown':
            raise PermissionError('WTS query denied')
        return state == 'present'

    monkeypatch.setattr(child_desktop, 'windows_session_exists', exists)
    result = workspace.close_idle(0)
    if initial != 'gone':
        assert result == {'status': 'logoff_pending' if initial == 'present' else 'logoff_unknown',
                          'session_id': 23, 'generation': 0}
        process.kill.assert_not_called()
        host.host_dir.cleanup.assert_not_called()
        with pytest.raises(RuntimeError, match='注销尚未确认'):
            workspace.present('control', 0)
        state = 'gone'
    for _ in range(2):
        result = workspace.status()
    assert result == {'status': 'closed', 'session_id': None, 'generation': 0}
    process.stdin.close.assert_called_once()
    process.kill.assert_called_once()
    host.host_dir.cleanup.assert_called_once()


def test_exited_host_with_disconnected_session_is_not_closed(tmp_path, monkeypatch):
    host = child_desktop._ChildDesktopHost(tmp_path / 'driver.exe')
    host.bound_session_id = 24  # Windows authenticated, driver never reached READY.
    host.process = Mock()
    host.process.poll.return_value = 0
    monkeypatch.setattr(child_desktop, 'windows_session_exists', lambda sid: sid == 24)
    assert host.close() == 'logoff_pending'
    host.process.kill.assert_not_called()


def test_cancel_during_login_reads_bound_id_before_reporting_closed(tmp_path, monkeypatch):
    host = child_desktop._ChildDesktopHost(tmp_path / 'driver.exe')
    host.host_dir = SimpleNamespace(name=str(tmp_path), cleanup=Mock())
    (tmp_path / 'child-desktop.log').write_text('CONNECTED\nLOGGED_IN\nBOUND 24\n', encoding='utf-8')
    host.process = Mock()
    host.process.poll.return_value = 0
    checked = []
    def exists(session_id):
        checked.append(session_id)
        return False
    monkeypatch.setattr(child_desktop, 'windows_session_exists', exists)
    assert host.close() is None
    assert checked == [24]
    host.host_dir.cleanup.assert_called_once()


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    workspace = child_desktop.ChildDesktopWorkspace()
    hosts, captures, events = [], [], []
    wts = SimpleNamespace(current=None)
    stop = SimpleNamespace(raise_if_requested=lambda: None, sleep=lambda _: None)

    def start(host, client):
        hosts.append(host)
        host.session_id = wts.current = 20 + len(hosts)
        host.driver_pid = 9000 + len(hosts)
        host.process = SimpleNamespace(poll=lambda: None)

    def close(host):
        assert host.process is not None, 'host closed twice'
        host.process = None
        if wts.current == host.session_id:
            wts.current = None

    def call(client, tool, payload):
        captures.append((client.root.name, tool))
        if tool == 'get_desktop_state':
            frame = Image.new('RGB', (20, 10))
            frame.putpixel((0, 0), (255, 255, 255))
            frame.save(payload['screenshot_out_file'])
            return {'display': 'primary', 'screen_width': 20, 'screen_height': 10}
        if tool == 'get_screen_size':
            return {'width': 20, 'height': 10}
        return {'effect': 'unverifiable'}

    def task(name):
        root = tmp_path / name
        root.mkdir()
        return child_desktop.ChildDesktopDriver(root, lambda kind, **data: events.append((name, kind, data)),
                                               stop, workspace=workspace)

    monkeypatch.setattr(child_desktop, 'child_session_id', lambda: wts.current)
    monkeypatch.setattr(child_desktop._ChildDesktopHost, 'start', start)
    monkeypatch.setattr(child_desktop._ChildDesktopHost, 'close', close)
    monkeypatch.setattr(CuaBackend, 'call', call)
    return SimpleNamespace(workspace=workspace, hosts=hosts, captures=captures, events=events, task=task, wts=wts)


def test_consecutive_tasks_share_one_desktop_but_have_exclusive_input_and_fresh_evidence(desktop):
    space = desktop.workspace
    first, second = desktop.task('first'), desktop.task('second')
    assert space.status()['status'] == 'closed'
    assert first.start() is False
    generation = space.status()['generation']
    with pytest.raises(RuntimeError):
        second.start()
    second.close()  # A rejected runner still calls close in its finally block.
    with pytest.raises(RuntimeError):
        space.close_idle(generation)
    first.call('click', {})

    def stopped():
        raise EmergencyStopRequested('F9')

    first.stop = SimpleNamespace(raise_if_requested=stopped)
    with pytest.raises(EmergencyStopRequested):
        first.call('click', {})
    first.close()
    assert space.status() == {'status': 'idle', 'session_id': 21, 'generation': generation}
    assert second.start() is True
    with pytest.raises(RuntimeError, match='本任务已结束'):
        first.call('click', {})
    second.call('type_text', {})
    second.close()
    assert len(desktop.hosts) == 1
    assert desktop.captures == [('first', 'get_desktop_state'), ('first', 'click'),
                                ('second', 'get_desktop_state'), ('second', 'type_text')]
    assert all((client.root / 'child-ready.png').is_file() for client in (first, second))
    assert [(run, data['session_id'], data['driver_pid'], data['reused'])
            for run, kind, data in desktop.events if kind == 'child_desktop_ready'] == [
                ('first', 21, 9001, False), ('second', 21, 9001, True)]
    assert space.close_idle(generation)['status'] == 'closed'
    third = desktop.task('third')
    assert third.start() is False
    third.close()
    with pytest.raises(ValueError, match='已变化'):
        space.close_idle(generation)
    space.shutdown()
    assert all(host.process is None for host in desktop.hosts)


def test_workbench_codex_then_local_job_keep_the_same_host(tmp_path, monkeypatch, desktop):
    from test_chat_agent_bridge import Session, Stop, _reply
    from test_trace_procedure import wait_job

    from trace2task import chat_agent_runner, cua_runner, web_console
    from trace2task.cua_desktop import CHILD_DESKTOP_TARGET

    controller = web_console.WebConsoleController(tmp_path)
    server = web_console.create_web_server(tmp_path, port=0, controller=controller)
    monkeypatch.setattr(web_console, 'ConsoleEmergencyStop', lambda event: Stop())
    chat = chat_agent_runner.run_chat_agent
    motor = Session([_reply([{'skill': 'type_text', 'args': {'text': 'first'}}]), _reply(complete=True),
                     json.dumps({'verdict': 'complete', 'evidence': 'fixture', 'missing': ''})])
    monkeypatch.setattr(chat_agent_runner, 'run_chat_agent', lambda **kw: chat(**kw, session=motor))
    local = iter([{'status': 'predicted', 'prediction': {'actions': [
        {'skill': 'type_text', 'args': {'text': 'second'}}]}},
        {'status': 'model_control', 'control': 'answer', 'text': 'done'}])
    monkeypatch.setattr(cua_runner, 'predict_local', lambda *a, **kw: next(local))
    try:
        for options in ({'provider': 'codex'}, {'provider': 'trained_d', 'model': 'D-5970'}):
            job = controller.start_job(task_path='', instruction='type', execute=True, continuous=True,
                                       execution_scope='desktop', executor_backend='cua',
                                       cua_target=CHILD_DESKTOP_TARGET, **options)
            finished = wait_job(controller, job['job_id'])
            assert finished['status'] == 'completed', finished.get('error')
            assert controller.workbench_runs()['child_desktop']['status'] == 'idle'
            events = controller.run_store.get(job['job_id'])['events']
            pictures = [e['attributes']['screenshot'] for e in events
                        if e['name'] == 'run.log' and e['attributes'].get('screenshot')]
            assert len(pictures) == 1 and pictures[0].endswith('child-ready.png')
            assert any(e['name'] == 'model.round' and e['attributes'].get('screenshot') for e in events)
        assert len(desktop.hosts) == 1
        jobs_with_input = {run for run, tool in desktop.captures if tool == 'type_text'}
        assert len(jobs_with_input) == 2
    finally:
        server.server_close()
        controller.run_store.db.close()
    assert desktop.hosts[0].process is None


def test_startup_failure_survives_cleanup_failure(desktop, monkeypatch):
    def start(host, client):
        raise RuntimeError('Windows 登录超时')
    def close(host):
        raise RuntimeError('清理超时')
    monkeypatch.setattr(child_desktop._ChildDesktopHost, 'start', start)
    monkeypatch.setattr(child_desktop._ChildDesktopHost, 'close', close)
    with pytest.raises(RuntimeError, match='Windows 登录超时'):
        desktop.task('first').start()
    assert desktop.workspace.status()['status'] == 'closed'
    assert any(kind == 'cleanup_error' for _, kind, _ in desktop.events)


def test_live_view_and_handoff_reuse_the_host_and_invalidate_old_actions(desktop, monkeypatch):
    commands = []
    monkeypatch.setattr(child_desktop._ChildDesktopHost, 'viewer', lambda host, mode: commands.append(mode))
    space = desktop.workspace
    info = space.present('control', 0)  # A human may open it before the first model request.
    assert info['manual_control'] and info['session_id'] == 21 and not desktop.captures
    task = desktop.task('agent')
    assert task.start() is True
    assert commands == ['control', 'return']
    generation = info['generation']
    space.present('view', generation)
    task.call('click', {})
    space.present('control', generation)
    entered = threading.Event()
    def old_input():
        entered.set()
        task.call('click', {})
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(old_input)
        assert entered.wait(2) and not pending.done()
        space.present('return', generation)
        with pytest.raises(ObservationStale):
            pending.result(2)
    assert desktop.captures.count(('agent', 'click')) == 1
    task.call('get_desktop_state', {'screenshot_out_file': str(task.root / 'fresh.png')})
    task.call('click', {})
    task.close()
    assert space.status()['status'] == 'idle' and len(desktop.hosts) == 1
    with pytest.raises(ValueError):
        space.present('control', generation-1)


def test_uncertain_handoff_does_not_resume_input(desktop, monkeypatch):
    task = desktop.task('agent')
    task.start()
    def viewer(host, mode):
        if mode == 'return':
            raise RuntimeError('未确认')
    monkeypatch.setattr(child_desktop._ChildDesktopHost, 'viewer', viewer)
    space = desktop.workspace
    space.present('control', space.status()['generation'])
    with pytest.raises(RuntimeError, match='未确认'):
        space.present('return', space.status()['generation'])
    assert space.status()['manual_control'] is True
    task.close()
    space.shutdown()


def test_session_loss_stops_old_task_and_only_a_new_task_can_reopen(desktop):
    first = desktop.task('first')
    first.start()
    desktop.wts.current = 33
    with pytest.raises(RuntimeError, match='连接已中断'):
        first.call('click', {})
    assert len(desktop.hosts) == 1
    first.close()
    second = desktop.task('second')
    assert second.start() is False
    assert second.session_id == 22 and desktop.hosts[0].process is None
    desktop.workspace.shutdown()
    with pytest.raises(RuntimeError):
        second.call('click', {})
    second.close()
    with pytest.raises(RuntimeError):
        desktop.task('third').start()
    assert all(tool != 'click' for _, tool in desktop.captures)


def test_cancel_before_first_picture_preserves_session_and_rechecks_first_frame(desktop, monkeypatch):
    first, second = desktop.task('first'), desktop.task('second')
    checks = []
    def wait_for_picture(client, host, *, require_drawn):
        checks.append(require_drawn)
        if client is first:
            raise EmergencyStopRequested('F9')

    monkeypatch.setattr(child_desktop.ChildDesktopDriver, '_wait_for_picture', wait_for_picture)
    with pytest.raises(EmergencyStopRequested):
        first.start()
    first.close()
    assert desktop.workspace.status()['status'] == 'waiting_picture'
    assert second.start() is True
    assert checks == [True, True] and len(desktop.hosts) == 1
    second.close()
    desktop.workspace.shutdown()


def test_workbench_exit_cancels_pending_login_and_closes_the_host(desktop, monkeypatch):
    entered, closed = threading.Event(), threading.Event()

    def pending(host, client):
        entered.set()
        assert desktop.workspace._shutdown.wait(3)
        client.check_stop()

    monkeypatch.setattr(child_desktop._ChildDesktopHost, 'start', pending)
    monkeypatch.setattr(child_desktop._ChildDesktopHost, 'close', lambda host: closed.set())
    task = desktop.task('first')
    with ThreadPoolExecutor(2) as pool:
        opening = pool.submit(task.start)
        assert entered.wait(3)
        assert desktop.workspace.status()['status'] == 'connecting'
        pool.submit(desktop.workspace.shutdown).result(3)
        with pytest.raises(RuntimeError, match='正在退出'):
            opening.result()
    task.close()
    assert closed.is_set() and desktop.workspace.status()['status'] == 'closed'
