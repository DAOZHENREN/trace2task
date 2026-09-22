from trace2task import local_process


class _RunningProcess:
    def __init__(self, pid=43780):
        self.pid = pid

    @staticmethod
    def poll():
        return None


class _ExitedProcess:
    pid = 60268

    @staticmethod
    def poll():
        return 0


def test_windows_cleanup_targets_only_started_process_tree(monkeypatch):
    calls = []
    monkeypatch.setattr(local_process.sys, "platform", "win32")
    monkeypatch.setattr(local_process.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    assert local_process.stop_started_process(_RunningProcess()) is True
    assert calls == [(
        ["taskkill.exe", "/PID", "43780", "/T", "/F"],
        {
            "capture_output": True,
            "check": False,
            "creationflags": getattr(local_process.subprocess, "CREATE_NO_WINDOW", 0),
        },
    )]


def test_cleanup_skips_already_exited_process(monkeypatch):
    calls = []
    monkeypatch.setattr(local_process.subprocess, "run", lambda args, **kwargs: calls.append(args))

    assert local_process.stop_started_process(_ExitedProcess()) is False
    assert calls == []


def test_cleanup_skips_no_process(monkeypatch):
    monkeypatch.setattr(local_process.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError))
    assert local_process.stop_started_process(None) is False
