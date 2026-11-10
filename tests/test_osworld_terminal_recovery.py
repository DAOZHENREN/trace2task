import pytest
from trace2task.osworld_terminal_recovery import terminal_close_action

_ACTION_PYTHON_PREFIX = 'if terminal:\n    raise SystemExit(74)\n'
class Episode:
    def _dispatch_gui_action(self, code):
        return _ACTION_PYTHON_PREFIX


def test_only_exact_close_shortcut_and_restore_on_failure():
    episode = Episode()
    original = _ACTION_PYTHON_PREFIX
    for action in ({'action':'type','text':'cp'}, {'action':'key','keys':['ALT','F4','ENTER']}):
        with terminal_close_action(episode, action):
            assert _ACTION_PYTHON_PREFIX == original
    with pytest.raises(RuntimeError):
        with terminal_close_action(episode, {'action':'key','keys':['ALT','F4']}):
            assert 'raise SystemExit' not in _ACTION_PYTHON_PREFIX
            raise RuntimeError('transport failure')
    assert _ACTION_PYTHON_PREFIX == original
