from __future__ import annotations

from dataclasses import dataclass
from typing import Any

WINDOWS_MOTOR_SKILLS = (
    "focus_window",
    "click",
    "double_click",
    "drag",
    "press_key",
    "hold_key",
    "hotkey",
    "wait",
)

SPECIAL_KEYS = {
    "alt",
    "backspace",
    "ctrl",
    "delete",
    "down",
    "end",
    "enter",
    "escape",
    "home",
    "insert",
    "left",
    "page_down",
    "page_up",
    "right",
    "shift",
    "space",
    "tab",
    "up",
    *(f"f{number}" for number in range(1, 13)),
}
MOUSE_BUTTONS = {"left", "middle", "right"}


class ActionValidationError(ValueError):
    """Raised when a model or trace supplies an unsafe action payload."""


def normalize_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionValidationError("A keyboard action requires a non-empty string key")
    key = value.strip().casefold()
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        return key
    if key not in SPECIAL_KEYS:
        raise ActionValidationError(f"Unsupported keyboard key: {value!r}")
    return key


def _require_exact_args(
    skill: str,
    args: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - args.keys()
    unexpected = args.keys() - required - optional
    if missing:
        raise ActionValidationError(f"Skill '{skill}' is missing arguments: {sorted(missing)}")
    if unexpected:
        raise ActionValidationError(
            f"Skill '{skill}' received unexpected arguments: {sorted(unexpected)}"
        )


def _normalized_coordinate(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ActionValidationError(f"Mouse coordinate '{name}' must be a number")
    coordinate = float(value)
    if not 0 <= coordinate <= 1:
        raise ActionValidationError(f"Mouse coordinate '{name}' must be between 0 and 1")
    return coordinate


def _duration(value: object, *, maximum: int, skill: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ActionValidationError(
            f"Skill '{skill}' duration_ms must be an integer between 1 and {maximum}"
        )
    return value


@dataclass(frozen=True)
class ActionCall:
    """A validated, parameterized motor-skill invocation."""

    skill: str
    args: dict[str, Any]

    def __post_init__(self) -> None:
        if self.skill not in WINDOWS_MOTOR_SKILLS:
            raise ActionValidationError(f"Unsupported motor skill: {self.skill!r}")
        if not isinstance(self.args, dict):
            raise ActionValidationError("Action args must be an object")
        normalized = self._validate_and_normalize(dict(self.args))
        object.__setattr__(self, "args", normalized)

    @classmethod
    def from_payload(cls, payload: object) -> ActionCall:
        if not isinstance(payload, dict):
            raise ActionValidationError("A parameterized action must be an object")
        if set(payload) != {"skill", "args"}:
            raise ActionValidationError("An action object must contain exactly 'skill' and 'args'")
        return cls(skill=payload["skill"], args=payload["args"])

    def to_payload(self) -> dict[str, Any]:
        return {"skill": self.skill, "args": dict(self.args)}

    def _validate_and_normalize(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.skill == "focus_window":
            _require_exact_args(self.skill, args, required=set())
            return {}
        if self.skill in {"click", "double_click"}:
            _require_exact_args(
                self.skill,
                args,
                required={"x", "y"},
                optional={"button"},
            )
            button = args.get("button", "left")
            if not isinstance(button, str) or button.casefold() not in MOUSE_BUTTONS:
                raise ActionValidationError(f"Unsupported mouse button: {button!r}")
            return {
                "x": _normalized_coordinate(args["x"], "x"),
                "y": _normalized_coordinate(args["y"], "y"),
                "button": button.casefold(),
            }
        if self.skill == "drag":
            _require_exact_args(
                self.skill,
                args,
                required={"start_x", "start_y", "end_x", "end_y", "duration_ms"},
                optional={"button"},
            )
            button = args.get("button", "left")
            if not isinstance(button, str) or button.casefold() not in MOUSE_BUTTONS:
                raise ActionValidationError(f"Unsupported mouse button: {button!r}")
            return {
                "start_x": _normalized_coordinate(args["start_x"], "start_x"),
                "start_y": _normalized_coordinate(args["start_y"], "start_y"),
                "end_x": _normalized_coordinate(args["end_x"], "end_x"),
                "end_y": _normalized_coordinate(args["end_y"], "end_y"),
                "duration_ms": _duration(args["duration_ms"], maximum=5_000, skill=self.skill),
                "button": button.casefold(),
            }
        if self.skill == "press_key":
            _require_exact_args(self.skill, args, required={"key"})
            return {"key": normalize_key(args["key"])}
        if self.skill == "hold_key":
            _require_exact_args(self.skill, args, required={"key", "duration_ms"})
            return {
                "key": normalize_key(args["key"]),
                "duration_ms": _duration(args["duration_ms"], maximum=5_000, skill=self.skill),
            }
        if self.skill == "hotkey":
            _require_exact_args(self.skill, args, required={"keys"})
            keys = args["keys"]
            if not isinstance(keys, list) or not 2 <= len(keys) <= 4:
                raise ActionValidationError("Skill 'hotkey' requires a list of 2 to 4 keys")
            normalized_keys = [normalize_key(key) for key in keys]
            if len(set(normalized_keys)) != len(normalized_keys):
                raise ActionValidationError("Skill 'hotkey' keys must be unique")
            return {"keys": normalized_keys}
        if self.skill == "wait":
            _require_exact_args(self.skill, args, required={"duration_ms"})
            return {
                "duration_ms": _duration(args["duration_ms"], maximum=10_000, skill=self.skill)
            }
        raise AssertionError(f"Unhandled motor skill: {self.skill}")


def parameterized_action_schema(
    allowed_skills: tuple[str, ...] = WINDOWS_MOTOR_SKILLS,
) -> dict[str, Any]:
    """Return the strict JSON Schema used for future Windows-agent model output."""

    unknown = set(allowed_skills) - set(WINDOWS_MOTOR_SKILLS)
    if unknown:
        raise ValueError(f"Unknown motor skills requested for schema: {sorted(unknown)}")

    def action_schema(skill: str, args_schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "enum": [skill]},
                "args": {**args_schema, "additionalProperties": False},
            },
            "required": ["skill", "args"],
            "additionalProperties": False,
        }

    coordinate = {"type": "number", "minimum": 0, "maximum": 1}
    supported_keys = sorted(SPECIAL_KEYS | set("abcdefghijklmnopqrstuvwxyz0123456789"))
    key = {"type": "string", "enum": supported_keys}
    definitions = {
        "focus_window": action_schema(
            "focus_window",
            {"type": "object", "properties": {}, "required": []},
        ),
        "click": action_schema(
            "click",
            {
                "type": "object",
                "properties": {
                    "x": coordinate,
                    "y": coordinate,
                    "button": {"type": "string", "enum": sorted(MOUSE_BUTTONS)},
                },
                "required": ["x", "y", "button"],
            },
        ),
        "double_click": action_schema(
            "double_click",
            {
                "type": "object",
                "properties": {
                    "x": coordinate,
                    "y": coordinate,
                    "button": {"type": "string", "enum": sorted(MOUSE_BUTTONS)},
                },
                "required": ["x", "y", "button"],
            },
        ),
        "drag": action_schema(
            "drag",
            {
                "type": "object",
                "properties": {
                    "start_x": coordinate,
                    "start_y": coordinate,
                    "end_x": coordinate,
                    "end_y": coordinate,
                    "duration_ms": {"type": "integer", "minimum": 1, "maximum": 5_000},
                    "button": {"type": "string", "enum": sorted(MOUSE_BUTTONS)},
                },
                "required": [
                    "start_x",
                    "start_y",
                    "end_x",
                    "end_y",
                    "duration_ms",
                    "button",
                ],
            },
        ),
        "press_key": action_schema(
            "press_key",
            {"type": "object", "properties": {"key": key}, "required": ["key"]},
        ),
        "hold_key": action_schema(
            "hold_key",
            {
                "type": "object",
                "properties": {
                    "key": key,
                    "duration_ms": {"type": "integer", "minimum": 1, "maximum": 5_000},
                },
                "required": ["key", "duration_ms"],
            },
        ),
        "hotkey": action_schema(
            "hotkey",
            {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": key,
                        "minItems": 2,
                        "maxItems": 4,
                    }
                },
                "required": ["keys"],
            },
        ),
        "wait": action_schema(
            "wait",
            {
                "type": "object",
                "properties": {
                    "duration_ms": {"type": "integer", "minimum": 1, "maximum": 10_000}
                },
                "required": ["duration_ms"],
            },
        ),
    }
    return {"anyOf": [definitions[skill] for skill in allowed_skills]}
