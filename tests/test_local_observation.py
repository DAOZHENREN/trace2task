from types import SimpleNamespace

import pygame

from trace2task.local_observation import CuaWindowObservation, Win32DesktopObservation


def test_win32_observation_binds_foreground_and_screenshot(tmp_path):
    window = SimpleNamespace(handle=42, client_width=120, client_height=80)
    frame = pygame.Surface((120, 80))
    observer = Win32DesktopObservation(
        SimpleNamespace(observe=lambda: window),
        SimpleNamespace(capture=lambda observed: frame if observed is window else None),
        tmp_path,
    )
    target, state, path = observer.observe(None, 0)
    assert target == state == {"foreground_handle": 42, "screen_size": (120, 80)}
    assert path.is_file() and pygame.image.load(path).get_size() == (120, 80)
    backend = SimpleNamespace(capabilities=lambda _state: {
        'available_skills': ['click', 'move_cursor', 'hold_key']})
    raw_context = observer.context(target, state, backend)
    assert raw_context is None


def test_cua_observation_rechecks_exact_target_and_exposes_only_authorized_targets(tmp_path):
    selected = {"pid": 9, "window_id": 12}
    state = {"session": "test"}
    path = tmp_path / "frame.png"
    scope = SimpleNamespace(
        switch=lambda target: selected if target == selected else None,
        context=lambda target: {
            "current_window": target,
            "windows": [{"pid": 9, "window_id": 12}],
            "apps": [{"app_id": 0, "name": "Notepad"}],
        },
    )
    driver = SimpleNamespace(observe=lambda target, index: (state, path))
    backend = SimpleNamespace(capabilities=lambda _state: {
        "available_skills": ["click", "switch_window", "launch_app"]})
    observer = CuaWindowObservation(scope, driver)
    assert observer.observe(selected, 0) == (selected, state, path)
    raw_context = observer.context(selected, state, backend)
    assert raw_context == {
        "current_window": selected,
        "windows": [{"pid": 9, "window_id": 12}],
        "apps": [{"app_id": 0, "name": "Notepad"}],
    }
    assert 'capabilities' not in raw_context and 'execution_scope' not in raw_context
