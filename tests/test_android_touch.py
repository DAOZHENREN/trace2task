"""Run the actual Java protocol against fake Android classes, never against ADB.

This exercises the device-side watchdog, EOF and exception cleanup independently
of the Python controller. It is not a real-device compatibility test.
"""
import base64
import shutil
import subprocess
import threading
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from test_navigation_hotkey import fake_stop

from trace2task.android_touch import BoundedTouchSession, NavigationSuperseded, helper_artifact
from trace2task.execution_budget import BudgetStop, TimeBudgetExceeded
from trace2task.windows_runner import EmergencyStopRequested


@pytest.fixture(scope='module')
def java_helper(tmp_path_factory):
    java, javac = shutil.which('java'), shutil.which('javac')
    if not java or not javac:
        pytest.skip('JDK required for isolated Android touch protocol tests')
    root = Path(__file__).resolve().parents[1]
    classes = tmp_path_factory.mktemp('android-touch-java')
    sources = [root / 'src/trace2task/android_touch/BoundedTouch.java',
               *sorted((root / 'tests/android_touch/java').rglob('*.java'))]
    subprocess.run([javac, '--release', '8', '-d', str(classes), *(str(p) for p in sources)],
                   check=True, capture_output=True, timeout=30)

    def launch(*, package='com.example.game', activity='.Main', width=320, flags=()):
        return subprocess.Popen([java, *flags, '-cp', str(classes),
                                 'org.trace2task.input.TestMain', str(width), '240', '1', package, activity, 'a'*36],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', bufsize=1,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return launch


def output_for(java_helper, command, *, keep_open=False, **options):
    process = java_helper(**options)
    try:
        if keep_open:
            process.stdin.write(command)
            process.stdin.flush()
            process.wait(timeout=3)
            output = process.stdout.read()
        else:
            output, _ = process.communicate(command, timeout=3)
        return output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except OSError:
                pass


def injections(output):
    return [line.split() for line in output.splitlines() if line.startswith('INJECT ')]


def test_packaged_artifact_matches_current_reviewable_source():
    jar, meta = helper_artifact()
    assert jar.stat().st_size < 100_000 and meta['min_api'] == 29
    assert meta['protocol'] == 'T2T2'
    assert meta['max_duration_ms'] is None and meta['heartbeat_timeout_ms'] == 1500


def test_device_watchdog_releases_without_host_up(java_helper):
    output = output_for(java_helper, 'START 120 64 192 64 150\n', keep_open=True)
    events = injections(output)
    assert [e[1] for e in events] == ['0', '2', '1']
    assert events[0][2:4] == ['64.0', '192.0']
    assert events[1][2:4] == events[2][2:4] == ['64.0', '150.0']
    assert len({e[5] for e in events}) == 1  # Same downTime through MOVE and UP.
    assert 100 <= int(events[-1][6]) - int(events[0][6]) < 800
    assert 'T2T2 RELEASED deadline' in output


@pytest.mark.parametrize('duration', [60000, 2**63 - 1])
def test_long_gesture_releases_on_lost_heartbeat_without_host_up(java_helper, duration):
    output = output_for(java_helper, f'START {duration} 64 192 64 150\n', keep_open=True)
    events = injections(output)
    assert [e[1] for e in events] == ['0', '2', '1']
    assert len({e[5] for e in events}) == 1
    assert 1400 <= int(events[-1][6]) - int(events[0][6]) < 2500
    assert 'T2T2 RELEASED heartbeat_timeout' in output


def test_device_eof_and_explicit_up_release_immediately(java_helper):
    for suffix in ('', 'UP\n'):
        output = output_for(java_helper, 'START 1500 64 192 64 150\n' + suffix)
        events = injections(output)
        assert [e[1] for e in events] == ['0', '2', '1']
        assert int(events[-1][6]) - int(events[0][6]) < 500
        assert 'T2T2 RELEASED requested' in output


def test_device_direction_updates_preserve_contact_and_down_time(java_helper):
    output = output_for(java_helper, 'START 1000 64 192 64 150\nMOVE 80 160\nUP\n')
    events = injections(output)
    assert [e[1] for e in events] == ['0', '2', '2', '1']
    assert events[-2][2:4] == events[-1][2:4] == ['80.0', '160.0']
    assert len({e[5] for e in events}) == 1
    assert events[-1][4] == '0.0'


@pytest.mark.parametrize('command', [
    'START -1 64 192 64 150\n', 'START 0 64 192 64 150\n',
    'START 1000 -1 192 64 150\n', 'START 1000 64 192 320 150\n',
    'START 1000 NaN 192 64 150\n', 'shell something\n',
])
def test_device_refuses_bad_commands_before_input(java_helper, command):
    output = output_for(java_helper, command)
    assert not injections(output) and 'T2T2 ERROR' in output


def test_device_refuses_wrong_scope_before_input(java_helper):
    for options in ({'package': 'com.other.app'}, {'width': 321}, {'activity': '.Other'}):
        output = output_for(java_helper, 'START 1000 64 192 64 150\n', **options)
        assert not injections(output) and 'T2T2 ERROR' in output


def test_device_rejects_focused_overlay_even_when_top_activity_matches(java_helper):
    output = output_for(java_helper, 'START 1000 64 192 64 150\n',
                        flags=('-Dtest.windowFocus=com.android.systemui/NotificationShade',))
    assert not injections(output) and 'T2T2 ERROR' in output


def test_device_rejects_reused_transport_after_boot_change(java_helper):
    output = output_for(java_helper, 'START 1000 64 192 64 150\n', flags=('-Dtest.boot=' + 'b'*36,))
    assert not injections(output) and 'T2T2 ERROR' in output


def test_focus_change_before_correction_releases_instead_of_moving(java_helper):
    output = output_for(java_helper, 'START 1000 64 192 64 150\nMOVE 80 160\n',
                        flags=('-Dtest.focusChangesAt=3',))
    assert [e[1] for e in injections(output)] == ['0', '2', '1']
    assert 'T2T2 ERROR' in output


def test_focus_change_before_heartbeat_releases_without_direction_change(java_helper):
    output = output_for(java_helper, 'START 60000 64 192 64 150\nKEEPALIVE\n',
                        flags=('-Dtest.focusChangesAt=3',))
    assert [e[1] for e in injections(output)] == ['0', '2', '1']
    assert 'T2T2 ERROR' in output and 'T2T2 ALIVE' not in output


def test_device_exception_after_down_still_sends_up(java_helper):
    output = output_for(java_helper, 'START 1000 64 192 64 150\n',
                        flags=('-Dtest.failMove=true',))
    assert [e[1] for e in injections(output)] == ['0', '2', '1']
    assert 'T2T2 ERROR' in output


def test_duplicate_start_cannot_extend_lease(java_helper):
    output = output_for(java_helper, 'START 1000 64 192 64 150\nSTART 1000 64 192 80 150\n')
    assert [e[1] for e in injections(output)] == ['0', '2', '1']
    assert 'T2T2 ERROR' in output


class EventStop:
    def __init__(self):
        self.event = threading.Event()

    def raise_if_requested(self):
        if self.event.is_set():
            raise EmergencyStopRequested()

    def sleep(self, seconds):
        self.event.wait(seconds)
        self.raise_if_requested()


@pytest.fixture
def client(java_helper, monkeypatch, tmp_path):
    _, metadata = helper_artifact()
    real_popen = subprocess.Popen
    launches, events, commands = [], [], []
    state = {'width': 320, 'height': 240, 'rotation': 1,
             'focus': 'com.example.game/.Main', 'boot_id': 'a'*36}
    def run(*args):
        commands.append(args)
        if args[0] == 'push':
            return b''
        assert args[:2] == ('shell', 'sha256sum')
        return (metadata['jar_sha256'] + ' remote.jar').encode()
    phone = SimpleNamespace(adb='bound-adb', port=5038, transport=7, package='com.example.game', root=tmp_path,
                            target={'serial': 'phone', 'transport_id': 7}, _run=run,
                            touch_output=[],
                            state=lambda: dict(state), record=lambda kind, **data: events.append((kind, data)))
    def popen(args, **kwargs):
        launches.append(args)
        # Launch the real helper against fake framework classes, not the requested ADB executable.
        with monkeypatch.context() as context:
            context.setattr(subprocess, 'Popen', real_popen)
            process = java_helper(flags=getattr(phone, 'test_flags', ()))
        output = process.stdout
        def readline(*args):
            line = output.readline(*args)
            if line.startswith('T2T2 STARTED'):
                time.sleep(getattr(phone, 'start_ack_delay', 0))
            phone.touch_output.append(line)
            return line
        process.stdout = SimpleNamespace(readline=readline, close=output.close)
        return process
    monkeypatch.setattr(subprocess, 'Popen', popen)
    return phone, state, EventStop(), launches, events, commands


def test_client_uses_pinned_transport_and_records_release(client):
    phone, state, stop, launches, events, commands = client
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        assert session.begin((64, 192), (64, 150), 1000)
        assert session.move((80, 160))
    finally:
        session.close()
    assert launches[0][:5] == ['bound-adb', '-P', '5038', '-t', '7']
    assert len(launches) == 1 and session.released.is_set()
    assert events[-1][1]['release_confirmed']
    assert len(commands) == 2
    before = len(events)
    session.close()
    assert len(events) == before  # Idempotent cleanup.


def test_client_keeps_one_contact_past_old_limits_and_cannot_extend_duration(client):
    phone, state, stop, launches, events, _commands = client
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        assert session.begin((64, 192), (64, 150), 6000)
        assert session.released.wait(8)
        session.check()
    finally:
        session.close()
    injected = injections(''.join(phone.touch_output))
    assert [e[1] for e in injected] == ['0', '2', '1']
    assert len({e[5] for e in injected}) == 1
    assert injected[1][2:4] == injected[2][2:4] == ['64.0', '150.0']
    assert 5900 <= int(injected[-1][6]) - int(injected[0][6]) < 7000
    assert 'T2T2 RELEASED deadline\n' in phone.touch_output
    assert events[-1][1]['heartbeat_count'] >= 10
    assert len(launches) == 1
    assert len([e for e in session.events if e['event'] == 'start_requested']) == 1


def test_delayed_start_ack_does_not_shorten_or_extend_device_hold(client):
    phone, state, stop, launches, _events, _commands = client
    phone.start_ack_delay = 1.2
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        began = time.monotonic()
        assert session.begin((64, 192), (64, 150), 2000)
        assert time.monotonic() - began >= 1.2
        assert session.released.wait(2)
        session.check()
    finally:
        session.close()
    injected = injections(''.join(phone.touch_output))
    assert [e[1] for e in injected] == ['0', '2', '1']
    assert 1900 <= int(injected[-1][6]) - int(injected[0][6]) < 2800
    assert len(launches) == 1
    assert len([e for e in session.events if e['event'] == 'start_requested']) == 1


def test_stop_during_start_ack_wait_remains_immediate(monkeypatch):
    stop = EventStop()
    driver = SimpleNamespace(target={}, record=lambda *a, **kw: None)
    session = BoundedTouchSession(driver, {}, stop)
    commands = []
    monkeypatch.setattr(session, '_send', lambda command: commands.append(command) or True)
    timer = threading.Timer(.1, stop.event.set)
    timer.start()
    began = time.monotonic()
    try:
        with pytest.raises(EmergencyStopRequested):
            session.begin((64, 192), (64, 150), 6000)
        assert time.monotonic() - began < .5
        assert commands == ['START 6000 64 192 64 150']
    finally:
        timer.join(1)


@pytest.mark.parametrize('kind', ['heartbeat', 'focus', 'transport'])
def test_long_hold_failures_surface_even_without_waiting_for_more_commands(client, kind):
    phone, state, stop, launches, _events, _commands = client
    if kind == 'focus':
        phone.test_flags = ('-Dtest.focusChangesAt=3',)
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        assert session.begin((64, 192), (64, 150), 60000)
        if kind == 'heartbeat':
            session._keepalive.clear()  # Simulate a stalled host heartbeat producer.
        elif kind == 'transport':
            session._process.kill()
            session._process.wait(timeout=2)
        session._reader.join(timeout=3)
        with pytest.raises(RuntimeError):
            session.check()
    finally:
        session.close()
    assert len(launches) == 1
    if kind != 'transport':
        assert [e[1] for e in injections(''.join(phone.touch_output))] == ['0', '2', '1']
    if kind == 'heartbeat':
        assert session.released.is_set()


@pytest.mark.parametrize('kind', ['stop', 'budget', 'intervention'])
def test_host_watchdog_releases_while_main_thread_is_busy(client, kind):
    phone, state, base, launches, _events, _commands = client
    stop = BudgetStop(base, 10) if kind == 'budget' else base
    current = [True]
    session = BoundedTouchSession(phone, state, stop, is_current=lambda: current[0])
    try:
        session.open()
        assert session.begin((64, 192), (64, 150), 60000)
        began = time.monotonic()
        if kind == 'budget':
            stop.deadline = time.monotonic() - .01
        elif kind == 'stop':
            base.event.set()
        else:
            current[0] = False
        # No session.check()/move() on the main thread: independent cancellation must work.
        assert session.released.wait(1)
        assert time.monotonic() - began < 1
        expected = {'stop': EmergencyStopRequested, 'budget': TimeBudgetExceeded,
                    'intervention': NavigationSuperseded}[kind]
        with pytest.raises(expected):
            session.move((80, 160))
    finally:
        session.close()
    assert len(launches) == 1
    assert len([e for e in session.events if e['event'] == 'start_requested']) == 1


def test_client_failed_transport_has_no_reconnect_or_replay(client):
    phone, state, stop, launches, _events, _commands = client
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        session._process.kill()
        session._process.wait(timeout=2)
        with pytest.raises(RuntimeError):
            session.begin((64, 192), (64, 150), 1000)
    finally:
        session.close()
    assert len(launches) == 1


def test_client_rejects_helper_digest_before_starting_process(client):
    phone, state, stop, launches, _events, _commands = client
    phone._run = lambda *args: b'mismatch remote.jar'
    session = BoundedTouchSession(phone, state, stop)
    try:
        with pytest.raises(RuntimeError, match='digest'):
            session.open()
    finally:
        session.close()
    assert not launches


def test_client_rejects_non_component_focus_before_any_adb_call(client):
    phone, state, stop, launches, _events, commands = client
    for focus in ('com.example.game/.Main;command', 'com.example.game/.Main extra',
                  'com.other.app/.Main', 'com.example.game/$(command)'):
        with pytest.raises(RuntimeError, match='Invalid bound'):
            BoundedTouchSession(phone, {**state, 'focus': focus}, stop).open()
    assert not launches and not commands


def test_client_quotes_nested_activity_name_for_remote_shell(client):
    phone, state, stop, launches, _events, _commands = client
    session = BoundedTouchSession(phone, {**state, 'focus': 'com.example.game/.Main$Nested'}, stop)
    try:
        session.open()
        assert launches[0][-2] == "'.Main$Nested'"
    finally:
        session.close()


def test_region_protocol_uses_current_pixels_and_preserves_coordinates(client):
    phone, state, stop, _launches, _events, _commands = client
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        for index in range(2):
            item = session.capture_region((10, 20, 42, 44), phone.root / f'crop-{index}.png')
            assert item['image_kind'] == 'minimap_luminance_crop'
            assert item['crop_region'] == [10, 20, 42, 44]
            with Image.open(item['screenshot']) as image:
                expected = Image.new('RGB', (1, 1), (10, 20, 100)).convert('L').getpixel((0, 0))
                assert image.mode == 'L' and image.size == (32, 24)
                assert image.getpixel((0, 0)) == expected
    finally:
        session.close()
    assert not any(e['event'] in {'start_requested', 'move_requested'} for e in session.events)


@pytest.mark.parametrize('kind', ['stop', 'f9', 'budget', 'intervention'])
def test_frame_in_flight_does_not_block_device_release(client, kind):
    phone, state, base, _launches, _events, _commands = client
    phone.test_flags = ('-Dtest.captureDelayMs=1200',)
    stop = BudgetStop(base, 10) if kind == 'budget' else base
    if kind == 'f9':
        stop, hotkey = fake_stop()
    current = [True]
    session = BoundedTouchSession(phone, state, stop, is_current=lambda: current[0])
    try:
        session.open()
        session.begin((64, 192), (64, 150), 60000)
        assert session._send('FRAME 10 20 32 24')
        time.sleep(.05)  # Capture remains in flight, but its worker must not own stdin.
        began = time.monotonic()
        if kind == 'f9':
            hotkey['down'] = True
        elif kind == 'budget':
            stop.deadline = time.monotonic() - .01
        elif kind == 'intervention':
            current[0] = False
        else:
            base.event.set()
        assert session.released.wait(.7)
        assert time.monotonic() - began < .7
    finally:
        session.close()
    assert session.released.is_set()


def test_device_lease_releases_during_slow_capture(client):
    phone, state, stop, _launches, _events, _commands = client
    phone.test_flags = ('-Dtest.captureDelayMs=1200',)
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        session.begin((64, 192), (64, 150), 200)
        assert session.capture_region((10, 20, 42, 44), phone.root / 'too-late.png') is None
        assert session.released.is_set()
        assert not session.move((80, 160))
        assert not (phone.root / 'too-late.png').exists()
    finally:
        session.close()


def test_device_capture_failure_after_down_releases_and_fails_closed(client):
    phone, state, stop, _launches, _events, _commands = client
    phone.test_flags = ('-Dtest.failCapture=true',)
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        session.begin((64, 192), (64, 150), 60000)
        with pytest.raises(RuntimeError, match='capture failed'):
            session.capture_region((10, 20, 42, 44), phone.root / 'failed.png')
        assert session.released.is_set()
    finally:
        session.close()


@pytest.mark.parametrize('data', [b'not-zlib', zlib.compress(b'short'),
                                zlib.compress(b'X' * 10000), zlib.compress(b'X' * 768) + b'trailing'])
def test_malformed_or_oversized_region_payload_is_never_a_frame(client, monkeypatch, data):
    phone, state, stop, launches, _events, _commands = client
    session = BoundedTouchSession(phone, state, stop)
    monkeypatch.setattr(session, '_send', lambda _: True)
    monkeypatch.setattr(session, '_await', lambda *args, **kw: base64.b64encode(data).decode())
    with pytest.raises((RuntimeError, zlib.error)):
        session.capture_region((10, 20, 42, 44), phone.root / 'invalid.png')
    assert not (phone.root / 'invalid.png').exists() and not launches


@pytest.mark.parametrize('duration', [60000, 10**40])
def test_device_lease_clamped_to_remaining_time_budget(client, duration):
    phone, state, base, _launches, _events, _commands = client
    stop = BudgetStop(base, 10)
    session = BoundedTouchSession(phone, state, stop)
    try:
        session.open()
        stop.deadline = time.monotonic() + .3
        session.begin((64, 192), (64, 150), duration)
        start = next(e for e in session.events if e['event'] == 'start_requested')
        assert 1 <= start['duration_ms'] <= 300
        assert session.released.wait(.8)
    finally:
        session.close()


def test_bad_region_rejected_without_transport_or_file(client):
    phone, state, stop, launches, _events, _commands = client
    session = BoundedTouchSession(phone, state, stop)
    for region in ((-1, 0, 32, 32), (0, 0, 321, 32), (0, 0, 0, 10), (0., 0, 32, 32)):
        with pytest.raises(ValueError):
            session.capture_region(region, phone.root / 'invalid.png')
    with pytest.raises(ValueError, match='run directory'):
        session.capture_region((0, 0, 32, 32), phone.root.parent / 'outside.png')
    assert not launches
