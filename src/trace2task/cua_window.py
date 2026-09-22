"""Read-only native identity checks for windows omitted from Cua's top-level list."""
from ctypes import wintypes

from trace2task.windows_control import Win32Backend


def inspect_window(target):
    """Prove an exact HWND/PID and its current root; never search by name or PID."""
    native = Win32Backend()
    hwnd = target['window_id']
    label = f"PID={target['pid']}, HWND={hwnd}"
    info = native.get_window(hwnd)
    if info is None:
        raise RuntimeError(f'选中的窗口已关闭或无法读取（{label}）；请刷新后重新选择')
    if info.process_id != target['pid']:
        raise RuntimeError(f'窗口所属进程已变化（{label}）；拒绝绑定已复用的窗口编号')
    if not info.process_name:
        raise RuntimeError(f'无法核实窗口所属应用（{label}）；未扩大操作范围')
    ancestor = native.user32.GetAncestor
    ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
    ancestor.restype = wintypes.HWND
    root_hwnd = int(ancestor(hwnd, 2) or 0)  # GA_ROOT, not owner or arbitrary sibling.
    root = native.get_window(root_hwnd) if root_hwnd else None
    if root is None:
        raise RuntimeError(f'无法核实窗口宿主（{label}）；请刷新后重新选择')
    # Refuse a racing destruction/reparenting rather than attaching to a stale root.
    rechecked = native.get_window(hwnd)
    root_rechecked = native.get_window(root_hwnd)
    if (rechecked is None or rechecked.process_id != target['pid']
            or root_rechecked is None or root_rechecked.process_id != root.process_id
            or int(ancestor(hwnd, 2) or 0) != root_hwnd):
        raise RuntimeError(f'核验期间窗口身份或宿主变化（{label}）；未绑定其他窗口')
    return {
        **target, 'title': info.title, 'app_name': info.process_name,
        'minimized': rechecked.is_minimized or root_rechecked.is_minimized,
        'root_target': {'pid': root.process_id, 'window_id': root_hwnd},
    }
