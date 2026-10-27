"""Bounded pixel-only joystick control, not a general 3-D path planner.

Navigation tracks a distinctive minimap marker toward the fixed player anchor.
The model supplies visible geometry and camera heading, never an internal game
coordinate or executable code. Loss, ambiguity and lack of progress release the
contact. Entering the stop radius is only an arrival candidate, not task success.
"""
from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image

from trace2task.android_touch import NavigationSuperseded
from trace2task.execution_protocol import ActionUnavailable

NAVIGATION_SKILLS = ('joystick_move', 'navigate_to')
MAX_NAVIGATION_DURATION_MS = 1500


def _object(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f'{name} requires exactly {", ".join(keys)}')


def _number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite and between {low} and {high}')
    return float(value)


def validate_navigation_action(skill, value):
    if skill not in NAVIGATION_SKILLS:
        raise ValueError('Unknown Android navigation skill')
    fields = (('start_x', 'start_y', 'end_x', 'end_y', 'duration_ms') if skill == 'joystick_move'
              else ('joystick', 'map_region', 'marker', 'anchor', 'stop_radius',
                    'camera_heading_deg', 'duration_ms'))
    _object(value, fields, skill)
    duration = value['duration_ms']
    if type(duration) is not int or duration < 1:
        raise ValueError('Movement duration_ms must be a positive integer')
    if skill == 'navigate_to' and duration > MAX_NAVIGATION_DURATION_MS:
        raise ValueError('navigate_to duration_ms must be an integer 1..1500')
    if skill == 'joystick_move':
        args = {key: _number(value[key], 0, 1, key) for key in fields if key != 'duration_ms'}
    else:
        args = {}
        for name, keys in [('joystick', ('x', 'y', 'radius')),
                           ('map_region', ('left', 'top', 'right', 'bottom')),
                           ('marker', ('x', 'y', 'radius')), ('anchor', ('x', 'y'))]:
            _object(value[name], keys, name)
            args[name] = {key: _number(value[name][key], 0, 1, f'{name}.{key}') for key in keys}
        for name, low, high in [('joystick', .02, .2), ('marker', .004, .04)]:
            _number(args[name]['radius'], low, high, name + '.radius')
        region = args['map_region']
        if not (.04 <= region['right'] - region['left'] <= .6
                and .04 <= region['bottom'] - region['top'] <= .6):
            raise ValueError('map_region must be a bounded minimap, not the whole scene')
        for name in ('marker', 'anchor'):
            if not (region['left'] < args[name]['x'] < region['right']
                    and region['top'] < args[name]['y'] < region['bottom']):
                raise ValueError(f'{name} must be inside map_region')
        args['stop_radius'] = _number(value['stop_radius'], .005, .1, 'stop_radius')
        args['camera_heading_deg'] = _number(value['camera_heading_deg'], 0, 360, 'camera_heading_deg')
    return {**args, 'duration_ms': duration}


def navigation_schemas():
    def number(low=0, high=1):
        return {'type': 'number', 'minimum': low, 'maximum': high}

    def obj(properties):
        return {'type': 'object', 'additionalProperties': False, 'properties': properties,
                'required': list(properties)}

    duration = {'type': 'integer', 'minimum': 1}
    variants = {
        'joystick_move': {**{key: number() for key in ('start_x', 'start_y', 'end_x', 'end_y')},
                          'duration_ms': duration},
        'navigate_to': {
            'joystick': obj({'x': number(), 'y': number(), 'radius': number(.02, .2)}),
            'map_region': obj({key: number() for key in ('left', 'top', 'right', 'bottom')}),
            'marker': obj({'x': number(), 'y': number(), 'radius': number(.004, .04)}),
            'anchor': obj({'x': number(), 'y': number()}), 'stop_radius': number(.005, .1),
            'camera_heading_deg': number(0, 360),
            'duration_ms': {**duration, 'maximum': MAX_NAVIGATION_DURATION_MS},
        },
    }
    return [obj({'skill': {'type': 'string', 'enum': [skill]}, 'args': obj(properties)})
            for skill, properties in variants.items()]


def pixel_point(x, y, state):
    return (min(state['width'] - 1, round(x * state['width'])),
            min(state['height'] - 1, round(y * state['height'])))


class MarkerTracker:
    """Small local normalized-correlation tracker, with ambiguity rejection.

    Searches near the last marker; never silently re-acquires a similar icon on
    the other side of a minimap. Uses only bundled numpy/Pillow dependencies.
    """

    def __init__(self, image, args):
        self.size = image.size
        width, height = self.size
        unit = min(self.size)
        region = args['map_region']
        self.region = (round(region['left'] * width), round(region['top'] * height),
                       round(region['right'] * width), round(region['bottom'] * height))
        if max(self.region[2]-self.region[0], self.region[3]-self.region[1]) > 512:
            raise ActionUnavailable('小地图区域每边不能超过 512 像素，请缩小可见区域')
        self.position = np.array([args['marker']['x'] * width, args['marker']['y'] * height])
        self.radius = max(3, round(args['marker']['radius'] * unit))
        self.search_radius = max(8, min(48, round(unit * .04)))
        x, y = np.rint(self.position).astype(int)
        r = self.radius
        if not (self.region[0] <= x - r and x + r + 1 <= self.region[2]
                and self.region[1] <= y - r and y + r + 1 <= self.region[3]):
            raise ActionUnavailable('目标标记截图越过小地图边界，请重新定位')
        self.template = np.asarray(image.convert('L'), dtype=np.float32)[y-r:y+r+1, x-r:x+r+1].copy()
        self.template -= self.template.mean()
        self.energy = float(np.square(self.template).sum())
        if float(self.template.std()) < 8:
            raise ActionUnavailable('目标标记缺少清晰视觉特征，不能启动自动移动')

    def locate(self, image, *, cropped=False):
        origin = self.region[:2] if cropped else (0, 0)
        size = ((self.region[2]-self.region[0], self.region[3]-self.region[1])
                if cropped else self.size)
        if image.size != size:
            return None, 'geometry_changed'
        r, travel = self.radius, self.search_radius
        x, y = np.rint(self.position).astype(int)
        left, top = max(self.region[0], x-r-travel), max(self.region[1], y-r-travel)
        right, bottom = min(self.region[2], x+r+travel+1), min(self.region[3], y+r+travel+1)
        pixels = np.asarray(image.convert('L').crop((left-origin[0], top-origin[1],
                            right-origin[0], bottom-origin[1])), dtype=np.float32)
        size = 2 * r + 1
        if min(pixels.shape) < size:
            return None, 'target_lost'
        windows = np.lib.stride_tricks.sliding_window_view(pixels, (size, size))
        cross = np.einsum('ijkl,kl->ij', windows, self.template, optimize=False)

        def box_sum(array):
            integral = np.pad(array.cumsum(0, dtype=np.float64).cumsum(1), ((1, 0), (1, 0)))
            return (integral[size:, size:] - integral[:-size, size:]
                    - integral[size:, :-size] + integral[:-size, :-size])

        sums, squares = box_sum(pixels), box_sum(np.square(pixels))
        variance = np.maximum(0, squares - np.square(sums) / (size * size))
        scores = cross / np.sqrt(np.maximum(1, variance * self.energy))
        row, col = np.unravel_index(int(scores.argmax()), scores.shape)
        best = float(scores[row, col])
        if best < .85:
            return None, 'target_lost'
        competitors = scores.copy()
        exclusion = max(3, r)
        competitors[max(0, row-exclusion):row+exclusion+1,
                    max(0, col-exclusion):col+exclusion+1] = -1
        if best - float(competitors.max()) < .06:
            return None, 'target_ambiguous'
        self.position = np.array([left + col + r, top + row + r], dtype=float)
        return {'position': self.position.tolist(), 'confidence': round(best, 4)}, None


def prepare_navigation(action, state, observation):
    args = validate_navigation_action(action.skill, action.args)
    if action.skill == 'joystick_move':
        return {'state': dict(state), 'args': args}
    if not observation or observation[0].get('sha256') != state.get('sha256'):
        raise ActionUnavailable('导航需要与本轮动作绑定的原始截图')
    path = Path(observation[1])
    if hashlib.sha256(path.read_bytes()).hexdigest() != state.get('sha256'):
        raise ActionUnavailable('导航截图哈希不匹配，未发送输入')
    width, height, unit = state['width'], state['height'], min(state['width'], state['height'])
    joystick = args['joystick']
    x, y, radius = joystick['x'] * width, joystick['y'] * height, joystick['radius'] * unit
    if not (0 <= x-radius and x+radius < width and 0 <= y-radius and y+radius < height):
        raise ActionUnavailable('摇杆运动范围越过屏幕边界')
    region = args['map_region']
    if args['stop_radius'] * unit > min((region['right'] - region['left']) * width,
                                      (region['bottom'] - region['top']) * height) / 4:
        raise ActionUnavailable('停止半径过大，请使用目标附近的小范围')
    with Image.open(path) as image:
        if image.size != (width, height):
            raise ActionUnavailable('导航截图尺寸不匹配')
        MarkerTracker(image, args)  # Fail before helper setup or DOWN on featureless targets.
    return {'state': dict(state), 'args': args, 'seed_screenshot': str(path)}


def steering(position, args, state, previous_angle=None):
    unit = min(state['width'], state['height'])
    anchor = np.array([args['anchor']['x'] * state['width'], args['anchor']['y'] * state['height']])
    delta = np.asarray(position) - anchor
    distance = float(np.linalg.norm(delta))
    stop_radius = args['stop_radius'] * unit
    if distance <= stop_radius:
        return None, distance, previous_angle
    angle = math.atan2(float(delta[0]), -float(delta[1])) - math.radians(args['camera_heading_deg'])
    if previous_angle is not None:
        change = (angle - previous_angle + math.pi) % (2 * math.pi) - math.pi
        angle = previous_angle + max(-math.pi / 6, min(math.pi / 6, change))
    # Slow near the stop radius, without forcing the stick into an uncalibrated dead zone.
    scale = min(1, max(.4, (distance-stop_radius) / max(1, stop_radius*2)))
    joystick = args['joystick']
    radius = joystick['radius'] * unit * scale
    endpoint = (round(joystick['x'] * state['width'] + math.sin(angle) * radius),
                round(joystick['y'] * state['height'] - math.cos(angle) * radius))
    return endpoint, distance, angle


def execute_navigation(driver, action, prepared, stop, *, is_current=lambda: True):
    args, state = prepared['args'], prepared['state']
    frames, outcome, touch = [], 'duration_limit', None
    driver._navigation_index = getattr(driver, '_navigation_index', 0) + 1
    root = driver.root / 'navigation' / f'{driver._navigation_index:04d}'
    started = time.monotonic()
    input_sent = False
    corrections = 0

    def check():
        stop.raise_if_requested()
        if not is_current():
            raise NavigationSuperseded('人工干预使当前导航失效')
        if touch is not None:
            touch.check()

    def capture():
        check()
        item = touch.capture_region(tracker.region, root / f'frame-{len(frames):03d}.png')
        if item is None:
            return None, 'duration_limit'
        check()
        with Image.open(item['screenshot']) as image:
            found, error = tracker.locate(image, cropped=True)
        item.update({
                'elapsed_ms': round((time.monotonic() - started) * 1000, 2),
                'marker': found, 'tracking_error': error})
        frames.append(item)
        driver.record('android_navigation_frame', **item)
        return found, error

    def finish(reason):
        if touch is not None:
            touch.close()
        return _receipt(reason, input_sent, frames, corrections, touch)

    try:
        if action.skill == 'navigate_to':
            with Image.open(prepared['seed_screenshot']) as seed:
                tracker = MarkerTracker(seed, args)
            center = pixel_point(args['joystick']['x'], args['joystick']['y'], state)
        else:
            center = pixel_point(args['start_x'], args['start_y'], state)
            endpoint = pixel_point(args['end_x'], args['end_y'], state)

        touch = driver.create_touch_session(state, stop, is_current=is_current)
        touch.open()
        check()
        # Upload/startup can be slow. Never start from marker geometry sampled
        # before that setup; re-ground immediately before the first DOWN.
        if action.skill == 'navigate_to':
            found, error = capture()
            # One read-only warm-up is allowed for the graphics API. Never
            # compensate for slow feedback by lengthening or repeating input.
            if not error and frames[-1]['capture_ms'] > min(500, args['duration_ms'] / 2):
                found, error = capture()
            if error:
                outcome = error
                return finish(outcome)
            endpoint, initial_distance, angle = steering(found['position'], args, state)
            frames[-1]['distance_px'] = round(initial_distance, 2)
            if endpoint is None:
                outcome = 'arrival_candidate'
                return finish(outcome)
            if frames[-1]['capture_ms'] > min(500, args['duration_ms'] / 2):
                return finish('feedback_too_slow')
        input_sent = True  # A START may deliver DOWN even if its acknowledgement is late.
        if not touch.begin(center, endpoint, args['duration_ms']):
            return finish('duration_limit')
        moving_since = time.monotonic()
        while not touch.released.is_set():
            check()
            if (time.monotonic() - moving_since) * 1000 >= args['duration_ms']:
                break
            if action.skill == 'joystick_move':
                stop.sleep(.02)
                continue
            stop.sleep(.1)
            found, error = capture()
            if error:
                outcome = error
                break
            endpoint, distance, angle = steering(found['position'], args, state, angle)
            frames[-1]['distance_px'] = round(distance, 2)
            if endpoint is None:
                outcome = 'arrival_candidate'
                break
            if distance > initial_distance + max(3, args['stop_radius'] * min(state['width'], state['height']) / 2):
                outcome = 'moving_away'
                break
            if time.monotonic() - moving_since >= .6 and initial_distance - distance < 1.5:
                outcome = 'no_progress'
                break
            # Never submit a late correction after the local or device-side lease.
            if (time.monotonic() - moving_since >= args['duration_ms'] / 1000
                    or touch.released.is_set()):
                break
            check()
            # Device-side activity/display guard runs immediately before MOVE.
            if not touch.move(endpoint):
                break
            corrections += 1
        touch.check()  # Surface a lost heartbeat/transport even if it already released the contact.
    except NavigationSuperseded:
        outcome = 'intervened'
    finally:
        if touch is not None:
            touch.close()
            driver._touch_session = None
    return _receipt(outcome, input_sent, frames, corrections, touch)


def _receipt(outcome, input_sent, frames, corrections, touch):
    if input_sent and touch is not None and not touch.released.is_set():
        raise RuntimeError('未确认触点释放；停止整个运行，不再自动发送输入')
    distances = [frame['distance_px'] for frame in frames if 'distance_px' in frame]
    return {'effect': 'unverifiable', 'route': 'android_bounded_touch',
            'delivery': {'mode': 'remote_android'}, 'reobserve': True,
            'navigation': {'outcome': outcome, 'input_sent': input_sent, 'frames': frames,
                           'corrections': corrections, 'touch_events': touch.events if touch else [],
                           'progress_px': round(distances[0]-distances[-1], 2) if distances else None,
                           'release_confirmed': touch.released.is_set() if touch else True},
            'note': 'Bounded movement ended. Arrival is only a pixel-space candidate; '
                    're-observe and verify the interaction/task result. Never blindly replay.'}
