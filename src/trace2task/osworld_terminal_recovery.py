"""Mainline-only escape hatch for the pinned terminal guard: Alt+F4 only.

The serial worker temporarily allows this exact GUI close shortcut. All other
native actions retain the pinned guard; original capsule files are untouched.
"""
from contextlib import contextmanager


@contextmanager
def terminal_close_action(episode, action):
    if not (set(action) == {'action', 'keys'} and action['action'] == 'key'
            and isinstance(action['keys'], list)
            and all(isinstance(key, str) for key in action['keys'])
            and [key.upper() for key in action['keys']] == ['ALT', 'F4']):
        yield
        return
    namespace = episode._dispatch_gui_action.__func__.__globals__
    original = namespace['_ACTION_PYTHON_PREFIX']
    marker = '    raise SystemExit(74)'
    if original.count(marker) != 1:
        raise ValueError('Pinned terminal guard differs; close shortcut not enabled')
    namespace['_ACTION_PYTHON_PREFIX'] = original.replace(marker, '    pass  # Allow exact GUI Alt+F4 recovery only')
    try:
        yield
    finally:
        namespace['_ACTION_PYTHON_PREFIX'] = original
