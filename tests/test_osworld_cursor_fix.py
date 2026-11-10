import ctypes
import pytest
from trace2task.osworld_cursor_fix import patched_sources


def test_unknown_image_is_not_patched():
    with pytest.raises(ValueError, match="Unrecognized"):
        patched_sources("unknown", "unknown")


def test_pinned_cursor_closes_on_exception_and_repeated_close_is_safe():
    # Test the exact lifecycle code inserted by the compatibility patch.
    import inspect
    import trace2task.osworld_cursor_fix as module

    source = inspect.getsource(module.patched_sources)
    import ast

    tree = ast.parse(source)
    lifecycle = next(
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "lifecycle" for t in node.targets)
    )

    class Display(ctypes.Structure):
        pass

    env = {"ctypes": ctypes, "Display": Display}
    exec("class Cursor:\n" + lifecycle, env)
    calls = []

    class Func:
        def __init__(self, name):
            self.name = name

        def __call__(self, p):
            calls.append((self.name, p))

    class Lib:
        XFree = Func("free")
        XCloseDisplay = Func("close")

    c = env["Cursor"]()
    c.display = 123
    c._cursor_data = 456
    c.xlib = Lib()
    with pytest.raises(RuntimeError, match="capture failed"):
        with c:
            raise RuntimeError("capture failed")
    c.close()
    assert calls == [("free", 456), ("close", 123)]
