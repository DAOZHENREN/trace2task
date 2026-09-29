"""Cua implementation of the execution boundary, limited to selected windows."""
from trace2task.cua_backend import action_request, text_element, valid_window_bounds
from trace2task.execution_protocol import ActionUnavailable, ForegroundUnavailable, ObservationStale
from trace2task.windows_control import Win32Backend


class CuaExecutionBackend:
    default_delivery_mode = 'background'

    def __init__(self, driver, scope, foreground=None, allow_foreground_retry=True):
        self.driver, self.scope = driver, scope
        self.foreground = foreground
        self.allow_foreground_retry = allow_foreground_retry
        self.foreground_targets = set()

    def authorize(self, action, target):
        self.scope.require_window(target)
        if action.skill == 'switch_window':
            self.scope.require_window(action.args)
        elif action.skill == 'launch_app':
            self.scope.require_app(action.args['app_id'])

    def prepare(self, action, state, execution_context=None):
        if action.skill == 'move_cursor':
            raise ActionUnavailable('Cua window cursor overlay cannot deliver native background hover; no input sent')
        if action.skill in ('wait', 'switch_window', 'launch_app'):
            if action.skill == 'wait' and action.args['duration_ms'] > 5000:
                raise ActionUnavailable('Cua wait is limited to 5000ms')
            return None
        try:
            return action_request(action, state)
        except ActionUnavailable as error:
            if action.skill == 'type_text':
                raise ActionUnavailable(
                    f'{error}。未发送输入；当前窗口中的文本目标需要明确的截图坐标 x/y。'
                    '请重新观察并选择输入框，而不是发送按钮。') from error
            raise

    def request_view(self, action, prepared, target, *, delivery_mode=None):
        """The Cua request, including pixel coordinates for input calls."""
        if prepared is None:
            return {'backend': 'cua', 'operation': action.skill,
                    'args': action.args,
                    'delivery_mode': 'local' if action.skill == 'wait' else 'control'}
        tool, payload = prepared
        if delivery_mode == 'foreground' or (
                target['pid'], target['window_id']) in self.foreground_targets:
            payload = {**payload, 'delivery_mode': 'foreground'}
        return {'backend': 'cua', 'operation': tool, 'args': dict(payload),
                'delivery_mode': payload['delivery_mode']}

    def check_current(self, target, state):
        self.scope.require_window(target)
        now = self.driver.window(target)
        if any(state.get(k) != v or now.get(k) != v for k, v in target.items()):
            raise RuntimeError('目标窗口身份变化，请重新选择；未执行旧计划')
        if (not valid_window_bounds(now.get('bounds'))
                or not valid_window_bounds(state.get('window_bounds'))):
            raise RuntimeError('目标窗口位置/尺寸无效；未执行旧计划')
        if now['bounds'] != state['window_bounds']:
            raise ObservationStale('目标窗口位置/尺寸变化；未执行旧坐标动作，请重新截图规划')

    def dispatch(self, action, prepared, target, stop):
        if action.skill == 'launch_app':
            target = self.scope.launch(action.args['app_id'])
            return {'effect': 'confirmed', 'route': 'launch_and_bind', 'target': target}, target
        if action.skill == 'switch_window':
            target = self.scope.switch(action.args)
            return {'effect': 'confirmed', 'route': 'observation_target_switch', 'target': target}, target
        if action.skill == 'wait':
            stop.sleep(action.args['duration_ms']/1000)
            return {'effect': 'confirmed', 'route': 'local_wait'}, target
        if (target['pid'], target['window_id']) in self.foreground_targets:
            # A definitive refusal already proved this exact window needs
            # foreground delivery. Do not probe and fail in background again.
            return self.retry_foreground(action, prepared, target, stop)
        tool, payload = prepared
        return self._annotate_receipt(self.driver.call(tool, payload)), target

    @staticmethod
    def _annotate_receipt(receipt):
        """Expose a driver delivery warning without guessing the app's effect."""
        if (isinstance(receipt, dict) and isinstance(receipt.get('escalation'), dict)
                and receipt['escalation'].get('reason') == 'delivery_failed'):
            return {**receipt, 'adapter_advisory': 'delivery_path_warning'}
        return receipt

    def retry_foreground(self, action, prepared, target, stop):
        """One explicit foreground attempt, only after verifying the target has focus."""
        if not self.allow_foreground_retry:
            raise ForegroundUnavailable(
                '目标明确拒绝后台输入；为避免占用键鼠，未自动切前台，也未发送该动作')
        self.scope.require_window(target)
        if prepared is None or action.skill in ('wait', 'switch_window', 'launch_app'):
            raise RuntimeError('Only a refused Cua input action can be retried in foreground')
        stop.raise_if_requested()
        tool, payload = prepared
        if payload.get('delivery_mode') != 'background' or (
                payload.get('pid'), payload.get('window_id')) != (target['pid'], target['window_id']):
            raise RuntimeError('Refused action target or mode changed; no foreground retry')
        native = self.foreground or Win32Backend()
        target_window = native.get_window(target['window_id'])
        if (target_window is None or target_window.process_id != target['pid']
                or target_window.is_minimized):
            raise ForegroundUnavailable('前台重试前目标窗口身份变化或已最小化；未发送输入')
        previous_handle = native.foreground_handle()
        previous_window = native.get_window(previous_handle) if previous_handle else None
        try:
            if previous_handle != target['window_id']:
                native.focus_window(target['window_id'])
            focused_window = native.get_window(target['window_id'])
            if (native.foreground_handle() != target['window_id']
                    or focused_window is None or focused_window.process_id != target['pid']
                    or focused_window.is_minimized):
                raise ForegroundUnavailable('Windows 未允许目标窗口切到前台；未发送全局输入')
            # Cua's foreground receipt means a route was attempted, not that
            # the application changed. Re-observe after the call.
            stop.raise_if_requested()
            receipt = self._annotate_receipt(
                self.driver.call(tool, {**payload, 'delivery_mode': 'foreground'}))
            self.foreground_targets.add((target['pid'], target['window_id']))
            delivery = receipt.get('delivery')
            return {**receipt, 'delivery': {
                **(delivery if isinstance(delivery, dict) else {}), 'mode': 'foreground'}}, target
        finally:
            # Cua may restore the foreground it observed, which is our target.
            # Restore the user's previous window only if nobody else switched
            # focus during the call, and only if its HWND still has the same PID.
            if (previous_window is not None and previous_handle != target['window_id']
                    and native.foreground_handle() == target['window_id']):
                now = native.get_window(previous_handle)
                if (now is not None and now.process_id == previous_window.process_id
                        and not now.is_minimized):
                    native.focus_window(previous_handle)

    def capabilities(self, state):
        skills = ['click', 'double_click', 'press_key', 'hotkey', 'wait', 'type_text',
                  'scroll', 'drag', 'switch_window', 'launch_app']
        unavailable = {'move_cursor': 'Window-scoped Cua moves only the agent overlay, not the real pointer'}
        requires_coordinate = False
        if state.get('elements_complete') is False:
            text_route = 'focused_control'
        else:
            try:
                text_element(state)
                text_route = 'element_token'
            except ActionUnavailable as error:
                requires_coordinate = True
                text_route = 'coordinate_required'
                unavailable['type_text_without_coordinate'] = str(error)
        return {'available_skills': skills, 'unavailable_skills': unavailable,
                'text_input_requires_coordinate': requires_coordinate,
                'text_input_route': text_route,
                'coordinate_space': 'current_window_screenshot', 'delivery_mode': 'background',
                'scroll_target': 'current_focused_region_only',
                'drag_requires': 'explicit_start_and_end_in_current_screenshot',
                'note': 'Available means a supported route, not a guarantee of application response. '
                        'With a degraded control tree, bare type targets the currently focused control '
                        'of this selected window through Cua; its effect still needs a new screenshot. '
                        'Use an explicit coordinate from the CURRENT screenshot when focus is uncertain. '
                        'Never use a window title or Send button as a text target. '
                        'If background delivery is explicitly refused, the host may briefly '
                        'focus the SAME authorized window and retry that action once. '
                        'Later actions for that window use foreground delivery; focus is restored when possible.'}
