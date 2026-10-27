import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def synthetic_console_hotkey(monkeypatch):
    """Fake runners need no native input; real F9 has separate protocol tests."""
    from types import SimpleNamespace

    from trace2task import web_console

    monkeypatch.setattr(web_console, 'Win32EmergencyStop', lambda: SimpleNamespace(
        start=lambda: None, close=lambda: None, raise_if_requested=lambda: None))


@pytest.fixture(autouse=True)
def isolated_personal_profiles(tmp_path):
    """Offline tests must not load the developer's deployments or API credentials."""
    from trace2task.api_settings import APISettingsStore

    original_init = APISettingsStore.__init__

    def isolated_init(self, path=None):
        original_init(self, path if path is not None else tmp_path / 'model-api.json')

    # An individual test may undo its own patches mid-test; keep isolation alive.
    with pytest.MonkeyPatch.context() as isolation:
        isolation.setenv('TRACE2TASK_RSI_PROFILE', str(tmp_path / 'missing-rsi-profile.json'))
        isolation.delenv('TRACE2TASK_API_KEY', raising=False)
        isolation.delenv('OPENAI_API_KEY', raising=False)
        isolation.setattr(APISettingsStore, '__init__', isolated_init)
        yield


@pytest.fixture(autouse=True)
def offline_codex_catalog():
    """No model metadata requests to the signed-in account from unit tests."""
    from trace2task import codex_catalog
    models = ('gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-sol', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna')
    rows = [{'model': model, 'displayName': model, 'isDefault': i == 0,
             'defaultReasoningEffort': 'low', 'inputModalities': ['text', 'image'],
             'supportedReasoningEfforts': [{'reasoningEffort': e} for e in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')]}
            for i, model in enumerate(models)]
    with pytest.MonkeyPatch.context() as isolation:
        isolation.setattr(codex_catalog, 'read_codex_models', lambda root: rows)
        yield
