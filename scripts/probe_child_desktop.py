"""Exercise the real child-session transport with a disposable WinForms editor."""
import argparse
import ctypes
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from trace2task.child_desktop import ChildDesktopDriver, ChildDesktopWorkspace
from trace2task.cua_desktop import (
    CHILD_DESKTOP_TARGET,
    CuaDesktopExecutionBackend,
    CuaDesktopObservation,
)
from trace2task.execution_protocol import PostActionReobserve, UnifiedAction

EDITOR = '''
using System;
using System.IO;
using System.Windows.Forms;
class Probe {
    [STAThread] static void Main() {
        var form = new Form { Text = "Trace2Task PiP Probe", KeyPreview = true };
        var text = new TextBox { Dock = DockStyle.Fill, Multiline = true };
        form.Controls.Add(text);
        text.MouseMove += (s, e) => {
            var point = Cursor.Position;
            File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "child-cursor.txt"),
                              point.X + "," + point.Y);
        };
        form.KeyDown += (s, e) => {
            if (e.KeyCode != Keys.S || !e.Control) return;
            e.SuppressKeyPress = true;
            using (var dialog = new SaveFileDialog()) {
                if (dialog.ShowDialog(form) == DialogResult.OK) File.WriteAllText(dialog.FileName, text.Text);
            }
        };
        form.Shown += (s, e) => text.Focus();
        Application.Run(form);
    }
}
'''


class Stop:
    def raise_if_requested(self):
        pass

    def sleep(self, seconds):
        time.sleep(seconds)


def main_state():
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError()
    return user32.GetForegroundWindow(), point.x, point.y


def exercise(root, driver, stop, work, connect_only=False, next_task=None):
    target = dict(CHILD_DESKTOP_TARGET)
    observer = CuaDesktopObservation(driver, target)
    backend = CuaDesktopExecutionBackend(driver, target)
    index = 0
    def observe():
        nonlocal index
        index += 1
        _, state, _ = observer.observe(target, index)
        return state

    def act(skill, **params):
        state = observe()
        action = UnifiedAction.from_payload({'skill': skill, 'args': params})
        backend.authorize(action, target)
        backend.check_current(target, state)
        try:
            receipt, _ = backend.dispatch(action, backend.prepare(action, state), target, stop)
        except PostActionReobserve:
            observe()
            return
        print(skill, receipt.get('effect'), flush=True)

    state = observe()
    print('Child session', driver.session_id, state['screen_width'], state['screen_height'], flush=True)
    if connect_only:
        return
    source = Path(work, 'Editor.cs')
    editor = Path(work, 'Trace2TaskEditor.exe')
    title = 'Trace2Task PiP Probe ' + uuid.uuid4().hex[:8]
    source.write_text(EDITOR.replace('Trace2Task PiP Probe', title), encoding='utf-8')
    csc = Path(os.environ['SystemRoot'], 'Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    subprocess.run([csc, '/nologo', '/target:winexe', '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll',
                    f'/out:{editor}', source], check=True)
    # Launch only the disposable editor through the verified child daemon.
    driver.call('launch_app', {'launch_path': str(editor)})

    def editor_window():
        return next((w for w in driver.windows() if w.get('title') == title), None)

    deadline = time.monotonic() + 10
    while not editor_window() and time.monotonic() < deadline:
        stop.sleep(.2)
    window = editor_window()
    if not window:
        raise RuntimeError('Editor did not open in the child session')
    win = driver.call('get_window_state', {'pid': window['pid'], 'window_id': window['window_id'],
                      'session': driver.root.name})
    bounds = win['window_bounds']
    act('click', x=(bounds['x'] + bounds['width'] / 2) / state['screen_width'],
        y=(bounds['y'] + bounds['height'] / 2) / state['screen_height'], button='left')
    expected = '后台桌面中文验证 ' + uuid.uuid4().hex[:8]
    act('type_text', text=expected)
    if next_task:
        first_session = driver.session_id
        driver = next_task()
        observer = CuaDesktopObservation(driver, target)
        backend = CuaDesktopExecutionBackend(driver, target)
        index = 0
        state = observe()
        existing = editor_window()
        if (driver.session_id != first_session or not existing
                or (existing['pid'], existing['window_id']) != (window['pid'], window['window_id'])):
            raise RuntimeError('The second task did not resume the same session and editor window')
        suffix = ' 第二个任务继续'
        act('type_text', text=suffix)
        expected += suffix
        print('Second task resumed the same open editor', flush=True)
    # Observe the actual GUI mouse events inside the editor and independently sample
    # the user's desktop. The user can continue moving their own mouse during this test.
    matches = []
    cursor = Path(work, 'child-cursor.txt')
    for ratio in (.3, .7):
        x = int(bounds['x'] + bounds['width'] * ratio)
        y = int(bounds['y'] + bounds['height'] * ratio)
        act('move_cursor', x=x / state['screen_width'], y=y / state['screen_height'])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                coords = [int(v) for v in cursor.read_text().split(',')]
            except (OSError, ValueError):
                coords = []
            if len(coords) == 2 and abs(coords[0] - x) <= 2 and abs(coords[1] - y) <= 2:
                break
            stop.sleep(.05)
        else:
            raise RuntimeError('The child editor did not observe the requested cursor move')
        _, parent_x, parent_y = main_state()
        matches.append(abs(parent_x - x) <= 2 and abs(parent_y - y) <= 2)
    print('Child cursor moves verified:', len(matches), 'main cursor at matching point:', sum(matches), flush=True)
    if all(matches):
        raise RuntimeError('The main cursor matched both commanded child points; isolation check inconclusive')
    saved = root / 'probe.txt'
    act('hotkey', keys=['ctrl', 's'])
    stop.sleep(.7)
    act('type_text', text=str(saved))
    act('press_key', key='enter')
    deadline = time.monotonic() + 6
    while not saved.exists() and time.monotonic() < deadline:
        stop.sleep(.1)
    if saved.read_text(encoding='utf-8-sig') != expected:
        raise RuntimeError('Saved contents differ from the text sent to the editor')
    observe()
    act('hotkey', keys=['alt', 'f4'])
    deadline = time.monotonic() + 4
    while editor_window() and time.monotonic() < deadline:
        stop.sleep(.1)
    print('Verified saved Chinese text', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect-only', action='store_true')
    parser.add_argument('--silent', action='store_true', help='Use Windows single sign-on without opening the login form')
    args = parser.parse_args()
    root = Path('artifacts', 'child-desktop-' + uuid.uuid4().hex[:12]).resolve()
    root.mkdir(parents=True)
    print(root, flush=True)
    stop = Stop()
    workspace = ChildDesktopWorkspace()
    def task(name):
        directory = root / name
        directory.mkdir()
        def record(kind, **data):
            if kind == 'child_desktop_ready':
                (directory / 'session.json').write_text(json.dumps(data), encoding='utf-8')
                print(name, data, flush=True)
        return ChildDesktopDriver(directory, record, stop, workspace=workspace, show_login=not args.silent)
    driver = task('task-1')

    def next_task():
        nonlocal driver
        driver.close()
        if workspace.status()['status'] != 'idle':
            raise RuntimeError('Releasing task 1 did not preserve an idle desktop')
        driver = task('task-2')
        if not driver.start():
            raise RuntimeError('Task 2 created another desktop')
        return driver
    initial = None
    changes = set()
    done = threading.Event()
    def monitor():
        while not done.wait(.05):
            state = main_state()
            if state != initial:
                changes.add(state)
    thread = threading.Thread(target=monitor)
    work = tempfile.TemporaryDirectory(prefix='Trace2TaskEditor-')
    try:
        driver.start()
        # Windows credential entry is interactive. Sample only the actual GUI execution.
        initial = main_state()
        thread.start()
        exercise(root, driver, stop, Path(work.name), args.connect_only, next_task)
    finally:
        try:
            try:
                driver.close()
            finally:
                workspace.shutdown()
        finally:
            done.set()
            if thread.ident is not None:
                thread.join()
                print(json.dumps({'foreground_changed': any(s[0] != initial[0] for s in changes),
                                  'cursor_changed': any(s[1:] != initial[1:] for s in changes)}), flush=True)
            work.cleanup()


if __name__ == '__main__':
    main()
