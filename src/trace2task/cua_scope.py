"""Explicit per-task Cua authorization; never grant access by shared PID/app name."""


def normalize_scope(value):
    # Older clients that select one target remain restricted to exactly that target.
    if isinstance(value, dict) and set(value) in ({'pid', 'window_id'}, {'launch_path'}):
        value = {'targets': [value], 'initial_index': 0}
    if not isinstance(value, dict) or set(value) != {'targets', 'initial_index'}:
        raise ValueError('请选择本次允许操作的窗口或应用')
    targets, initial = value['targets'], value['initial_index']
    if not isinstance(targets, list) or not 1 <= len(targets) <= 12:
        raise ValueError('请选择 1–12 个窗口或应用')
    if type(initial) is not int or not 0 <= initial < len(targets):
        raise ValueError('初始目标必须属于本次选中范围')
    checked = []
    for item in targets:
        if not isinstance(item, dict):
            raise ValueError('无效的 Cua 目标')  # noqa: TRY004 - user selection validation contract
        if set(item) == {'pid', 'window_id'}:
            if any(type(n) is not int or n <= 0 for n in item.values()):
                raise ValueError('无效的 Cua 窗口身份')
        elif set(item) == {'launch_path'}:
            if not isinstance(item['launch_path'], str) or not item['launch_path'].strip() or len(item['launch_path']) > 4096:
                raise ValueError('无效的应用身份')
        else:
            raise ValueError('Cua 目标只能是窗口身份或已枚举的应用')
        if item in checked:
            raise ValueError('不能重复选择同一目标')
        checked.append(dict(item))
    return {'targets': checked, 'initial_index': initial}


class CuaScope:
    def __init__(self, driver, selection):
        self.driver = driver
        self.selection = normalize_scope(selection)
        self.windows = set()
        self.apps = []
        self.bindings = []

    def start(self):
        selected = self.selection['targets']
        resolved = {}
        for item in selected:
            if 'window_id' in item:
                effective = self.driver.bind(item)
                resolved[(item['pid'], item['window_id'])] = effective
                self.windows.add((effective['pid'], effective['window_id']))
                self.bindings.append({'selected': dict(item), 'effective': dict(effective)})
        paths = [item['launch_path'] for item in selected if 'launch_path' in item]
        if paths:
            catalog = self.driver.call('list_apps', {}).get('apps', [])
            for path in paths:
                matches = [a for a in catalog if a.get('launch_path') == path]
                if not matches:
                    raise RuntimeError('选中的应用不在当前目录中；请刷新后重新选择')
                self.apps.append({'launch_path': path, 'name': matches[0].get('name', '')})
        initial = selected[self.selection['initial_index']]
        if 'launch_path' in initial:
            return self.launch(paths.index(initial['launch_path']))
        return resolved[(initial['pid'], initial['window_id'])]

    def require_window(self, target):
        if not isinstance(target, dict) or set(target) != {'pid', 'window_id'} or (
            target['pid'], target['window_id']) not in self.windows:
            raise ValueError('拒绝操作未选中的窗口；本次没有授权该目标')

    def require_app(self, app_id):
        if type(app_id) is not int or not 0 <= app_id < len(self.apps):
            raise ValueError('拒绝启动未选中的应用')

    def switch(self, target):
        self.require_window(target)
        effective = self.driver.bind(target)
        if effective != target:
            raise RuntimeError('运行期间窗口宿主发生变化；请重新选择，不扩大本次授权范围')
        return effective

    def launch(self, app_id):
        self.require_app(app_id)
        target = self.driver.bind({'launch_path': self.apps[app_id]['launch_path']})
        # Authorize only the actual resolved window, not all windows sharing its PID.
        self.windows.add((target['pid'], target['window_id']))
        return target

    def context(self, current):
        self.require_window(current)
        windows = []
        for pid, window_id in sorted(self.windows):
            w = self.driver.window({'pid': pid, 'window_id': window_id})
            if (w['pid'], w['window_id']) != (pid, window_id):
                raise RuntimeError('截图后授权窗口的宿主发生变化；请重新规划，不执行旧坐标')
            windows.append({k: w.get(k) for k in ('pid', 'window_id', 'title', 'app_name')})
        return {'current_window': dict(current), 'windows': windows,
                'apps': [{'app_id': i, 'name': a['name']} for i,a in enumerate(self.apps)]}
