"""Versioned model-independent actions. Coordinates refer to the bound observation.

Model adapters normalize their native coordinate units before this boundary.
Targets and observation IDs are attached by trusted orchestration, never by models.
"""
from copy import deepcopy
from dataclasses import dataclass

from trace2task.actions import ActionCall

PROTOCOL_VERSION = '1'
COORDINATE_SPACE = 'observation_normalized_0_1'


@dataclass(frozen=True)
class UnifiedAction:
    skill: str
    args: dict

    @classmethod
    def from_payload(cls, payload):
        if (not isinstance(payload, dict) or set(payload) != {'skill', 'args'}
                or not isinstance(payload['skill'], str) or not isinstance(payload['args'], dict)):
            raise ValueError('Action requires exactly skill and args')
        skill, args = payload['skill'], deepcopy(payload['args'])
        if skill == 'type_text' and ('x' in args or 'y' in args):
            if set(args) != {'text', 'x', 'y'}:
                raise ValueError('Coordinate text input requires text, x and y only')
            text = ActionCall('type_text', {'text': args['text']}).args
            position = ActionCall('click', {'x': args['x'], 'y': args['y']}).args
            args = {**text, 'x': position['x'], 'y': position['y']}
        elif skill == 'move_cursor':
            if set(args) != {'x', 'y'}:
                raise ValueError('Cursor move requires x and y only')
            position = ActionCall('click', args).args
            args = {'x': position['x'], 'y': position['y']}
        elif skill == 'scroll':
            position_keys = {'x', 'y'} if 'x' in args or 'y' in args else set()
            if (set(args) != {'direction', 'amount', 'by'} | position_keys
                    or args['direction'] not in ('up', 'down', 'left', 'right')
                    or args['by'] not in ('line', 'page')
                    or type(args['amount']) is not int or not 1 <= args['amount'] <= 50):
                raise ValueError('Scroll requires direction, amount 1-50 and by line/page')
            if position_keys:
                checked = ActionCall('click', {k: args[k] for k in position_keys})
                args.update(x=checked.args['x'], y=checked.args['y'])
        elif skill == 'switch_window':
            if (set(args) != {'pid', 'window_id'}
                    or any(type(v) is not int or v <= 0 for v in args.values())):
                raise ValueError('Invalid window identity')
        elif skill == 'launch_app':
            if set(args) != {'app_id'} or type(args['app_id']) is not int or args['app_id'] < 0:
                raise ValueError('Invalid application identity')
        else:
            checked = ActionCall.from_payload(payload)
            args = checked.args
        return cls(skill, args)

    def to_payload(self):
        return {'skill': self.skill, 'args': deepcopy(self.args)}


@dataclass(frozen=True)
class ActionPlan:
    actions: tuple[UnifiedAction, ...]
    done: bool = False

    @classmethod
    def from_prediction(cls, value):
        if not isinstance(value, dict) or set(value) not in (
                {'actions'}, {'protocol_version', 'coordinate_space', 'actions'}):
            raise ValueError('A plan requires actions and a valid protocol envelope')
        if 'protocol_version' in value and (
                value['protocol_version'] != PROTOCOL_VERSION
                or value['coordinate_space'] != COORDINATE_SPACE):
            raise ValueError('Unsupported action protocol version or coordinate space')
        if not isinstance(value['actions'], list) or not 1 <= len(value['actions']) <= 9:
            raise ValueError('A plan requires 1-8 actions or an explicit done marker')
        items = value['actions']
        done = (isinstance(items[-1], dict) and set(items[-1]) == {'done'}
                and items[-1]['done'] is True)
        if len(items) - int(done) > 8:
            raise ValueError('A plan may contain at most 8 input actions')
        return cls(tuple(UnifiedAction.from_payload(a) for a in (items[:-1] if done else items)), done)

    def to_payload(self):
        actions = [action.to_payload() for action in self.actions]
        if self.done:
            actions.append({'done': True})
        return {'protocol_version': PROTOCOL_VERSION,
                'coordinate_space': COORDINATE_SPACE, 'actions': actions}


class ActionUnavailable(ValueError):
    """Known pre-dispatch capability rejection: no input was sent; safe to replan."""


class PostActionReobserve(RuntimeError):
    """Input may have taken effect; abandon the batch and observe without retry."""


class ObservationStale(RuntimeError):
    """Trusted target changed before dispatch; no input was sent."""


class DriverRefusal(RuntimeError):
    """Explicit input-path refusal, distinct from an uncertain transport failure."""

    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__('Cua 后台输入不可用；后台尝试未记为已送达')


class ForegroundUnavailable(RuntimeError):
    """Foreground preflight failed before any global input was submitted."""
