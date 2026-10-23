"""F9 cross-thread checks without registering or synthesizing real keyboard input."""
import threading
from types import SimpleNamespace

import pytest

from trace2task.windows_runner import (
    EMERGENCY_HOTKEY_ID,
    EmergencyStopRequested,
    Win32EmergencyStop,
)


def fake_stop(down=False, message=False):
    state = {'down': down, 'message': message, 'peeks': []}
    def peek(pointer, *args):
        state['peeks'].append(threading.get_ident())
        if state['message']:
            pointer._obj.wParam = EMERGENCY_HOTKEY_ID
            state['message'] = False
            return True
        return False
    stop = Win32EmergencyStop.__new__(Win32EmergencyStop)
    stop._started = True
    stop._owner_thread = threading.get_ident()
    stop._requested = threading.Event()
    stop.user32 = SimpleNamespace(GetAsyncKeyState=lambda key: 0x8000 if state['down'] else 0,
                                 PeekMessageW=peek)
    return stop, state


def test_worker_f9_request_is_latched_for_execution_thread():
    stop, state = fake_stop(down=True)
    errors = []
    def worker():
        try:
            stop.raise_if_requested()
        except EmergencyStopRequested as error:
            errors.append(error)
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=1)
    assert len(errors) == 1 and not state['peeks']
    state['down'] = False
    with pytest.raises(EmergencyStopRequested):
        stop.raise_if_requested()


def test_worker_does_not_pump_another_threads_hotkey_queue():
    stop, state = fake_stop(message=True)
    thread = threading.Thread(target=stop.raise_if_requested)
    thread.start()
    thread.join(timeout=1)
    assert not state['peeks'] and state['message']
    with pytest.raises(EmergencyStopRequested):
        stop.raise_if_requested()
    assert stop._requested.is_set()
