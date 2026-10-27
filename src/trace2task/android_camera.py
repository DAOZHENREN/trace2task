"""A five-parameter camera swipe, followed by a fresh visual observation."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from trace2task.execution_protocol import ActionUnavailable

MAX_CAMERA_DURATION_MS = 1500
_POINTS = ('start_x', 'start_y', 'end_x', 'end_y')


def _coordinate(value, name):
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise ValueError(f'{name} must be finite and between 0 and 1')
    return float(value)


def validate_camera_action(value):
    if not isinstance(value, dict) or set(value) != {*_POINTS, 'duration_ms'}:
        raise ValueError('camera_drag requires start_x/start_y, end_x/end_y and duration_ms')
    duration = value['duration_ms']
    if type(duration) is not int or not 1 <= duration <= MAX_CAMERA_DURATION_MS:
        raise ValueError('camera_drag duration_ms must be an integer 1..1500')
    args = {key: _coordinate(value[key], key) for key in _POINTS}
    if (args['start_x'], args['start_y']) == (args['end_x'], args['end_y']):
        raise ValueError('camera_drag must move, not hold in place')
    return {**args, 'duration_ms': duration}


def camera_schema():
    def obj(properties):
        return {'type': 'object', 'additionalProperties': False,
                'properties': properties, 'required': list(properties)}

    coordinate = {'type': 'number', 'minimum': 0, 'maximum': 1}
    return obj({'skill': {'type': 'string', 'enum': ['camera_drag']}, 'args': obj({
        **{key: dict(coordinate) for key in _POINTS},
        'duration_ms': {'type': 'integer', 'minimum': 1, 'maximum': MAX_CAMERA_DURATION_MS},
    })})


def prepare_camera(action, state, observation):
    args = validate_camera_action(action.args)
    if not observation or not state.get('sha256') or observation[0].get('sha256') != state['sha256']:
        raise ActionUnavailable('转镜头需要与本轮动作绑定的原始截图')
    if hashlib.sha256(Path(observation[1]).read_bytes()).hexdigest() != state['sha256']:
        raise ActionUnavailable('转镜头截图哈希不匹配，未发送输入')
    width, height = state['width'], state['height']
    start = [min(width - 1, int(args['start_x'] * width)),
             min(height - 1, int(args['start_y'] * height))]
    end = [min(width - 1, int(args['end_x'] * width)),
           min(height - 1, int(args['end_y'] * height))]
    if start == end:
        raise ActionUnavailable('转镜头轨迹不足一个像素，未发送输入')
    return {'state': dict(state),
            'command': ['swipe', *map(str, start), *map(str, end), str(args['duration_ms'])],
            'camera': {'start_px': start, 'end_px': end,
                       'duration_ms': args['duration_ms'], 'angle_degrees': None}}
