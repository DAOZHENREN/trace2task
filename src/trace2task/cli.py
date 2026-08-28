from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from trace2task import __version__
from trace2task.compiler import compile_trace, confirm_taskpack
from trace2task.runner import DEFAULT_TASK_PATH, record_human, replay_trace, run_agent, run_demo
from trace2task.windows_capture import GdiWindowCapture, capture_window_once
from trace2task.windows_control import (
    Win32Backend,
    WindowSelector,
    list_window_records,
)
from trace2task.windows_recording import Win32InputMonitor, record_window_trace
from trace2task.windows_runner import run_windows_agent


def _print_result(result: object) -> None:
    if hasattr(result, "__dataclass_fields__"):
        result = asdict(result)  # type: ignore[arg-type]
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _add_window_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--handle", type=int, help="Exact window handle from 'windows list'.")
    parser.add_argument("--title", help="Require the window title to contain this text.")
    parser.add_argument("--process", help="Require this executable name.")


def _window_selector_from_args(args: argparse.Namespace) -> WindowSelector:
    return WindowSelector(
        handle=args.handle,
        title_contains=args.title,
        process_name=args.process,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace2task",
        description="Record a small game task, replay it, or solve it with a visual replanning agent.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Record a human demonstration.")
    record.add_argument("--seed", type=int, default=7)
    record.add_argument("--output", type=Path, default=Path("runs"))

    replay = subparsers.add_parser("replay", help="Replay recorded actions on a reset task.")
    replay.add_argument("trace", type=Path)
    replay.add_argument("--seed", type=int, default=19)
    replay.add_argument("--output", type=Path, default=Path("runs"))
    replay.add_argument("--headless", action="store_true")

    compile_command = subparsers.add_parser(
        "compile",
        help="Compile a successful recorded trace into a draft task pack.",
    )
    compile_command.add_argument("trace", type=Path)
    compile_command.add_argument(
        "--output",
        type=Path,
        default=Path("taskpacks/generated"),
        help="Root directory for generated task packs.",
    )

    confirm = subparsers.add_parser(
        "confirm",
        help="Mark a reviewed compiler-generated task pack as executable.",
    )
    confirm.add_argument("task", type=Path)

    windows = subparsers.add_parser(
        "windows",
        help="Inspect Windows targets for the desktop control adapter.",
    )
    windows_subparsers = windows.add_subparsers(dest="windows_command", required=True)
    windows_list = windows_subparsers.add_parser("list", help="List visible top-level windows.")
    windows_list.add_argument("--title", help="Keep windows whose title contains this text.")
    windows_list.add_argument("--process", help="Keep windows with this executable name.")
    windows_capture = windows_subparsers.add_parser(
        "capture",
        help="Capture one visible target client area, optionally focusing it first.",
    )
    _add_window_selector(windows_capture)
    windows_capture.add_argument(
        "--focus",
        action="store_true",
        help="Bring the target to the foreground before capture for GPU/screen-pixel fallback.",
    )
    windows_capture.add_argument(
        "--output",
        type=Path,
        default=Path("runs/window-capture.png"),
    )
    windows_record = windows_subparsers.add_parser(
        "record",
        help="Record raw target-window keyboard/mouse transitions and screenshots.",
    )
    _add_window_selector(windows_record)
    windows_record.add_argument("--task-id", default="windows-task")
    windows_record.add_argument("--output", type=Path, default=Path("runs"))
    windows_record.add_argument("--poll-hz", type=int, default=120)
    windows_record.add_argument("--max-seconds", type=float, default=300)
    windows_agent = windows_subparsers.add_parser(
        "agent",
        help="Plan or explicitly execute a confirmed Windows task pack.",
    )
    windows_agent.add_argument("--task", type=Path, required=True)
    windows_agent.add_argument("--model", default="gpt-5.6-terra")
    windows_agent.add_argument("--codex-bin", default="codex")
    windows_agent.add_argument("--plan-horizon", type=int, default=1)
    windows_agent.add_argument("--max-actions", type=int)
    windows_agent.add_argument("--output", type=Path, default=Path("runs"))
    windows_agent.add_argument(
        "--execute",
        action="store_true",
        help="Inject the validated actions. Without this flag the command is read-only dry-run.",
    )
    windows_agent.add_argument(
        "--background",
        action="store_true",
        help=(
            "Deliver execution input directly to a visible, unminimized target without focusing "
            "it. The target app must accept Win32 window messages."
        ),
    )
    windows_agent.add_argument(
        "--focus",
        action="store_true",
        help=(
            "Bring the target to the foreground before a dry-run capture. Foreground execution "
            "already focuses it."
        ),
    )

    agent = subparsers.add_parser("agent", help="Run a deterministic or multimodal agent.")
    agent.add_argument("--seed", type=int, default=19)
    agent.add_argument(
        "--provider",
        choices=("visual", "codex"),
        default="visual",
        help="'codex' reuses the Codex CLI's saved ChatGPT subscription login.",
    )
    agent.add_argument("--model", default="gpt-5.6-terra")
    agent.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI name or full path; Windows desktop bundles are auto-discovered.",
    )
    agent.add_argument("--task", type=Path, default=DEFAULT_TASK_PATH)
    agent.add_argument("--relocate-after", type=int, default=4)
    agent.add_argument(
        "--plan-horizon",
        type=int,
        default=12,
        help="Maximum local action batch returned by each model turn.",
    )
    agent.add_argument(
        "--motor-fps",
        type=int,
        default=20,
        help="Visible local action execution speed.",
    )
    agent.add_argument("--output", type=Path, default=Path("runs"))
    agent.add_argument("--headless", action="store_true")

    demo = subparsers.add_parser("demo", help="Run the complete comparison automatically.")
    demo.add_argument("--record-seed", type=int, default=7)
    demo.add_argument("--changed-seed", type=int, default=19)
    demo.add_argument("--relocate-after", type=int, default=4)
    demo.add_argument("--output", type=Path, default=Path("runs/demo"))
    demo.add_argument("--show", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "record":
        result = record_human(args.seed, args.output)
    elif args.command == "replay":
        result = replay_trace(
            args.trace,
            seed=args.seed,
            show=not args.headless,
            output_root=args.output,
        )
    elif args.command == "compile":
        result = compile_trace(args.trace, args.output)
    elif args.command == "confirm":
        result = confirm_taskpack(args.task)
    elif args.command == "windows":
        if args.windows_command == "list":
            result = list_window_records(
                title_contains=args.title,
                process_name=args.process,
            )
        elif args.windows_command == "capture":
            backend = Win32Backend()
            if args.focus:
                print("Waiting up to 10 seconds for the target to become foreground...")
            result = capture_window_once(
                _window_selector_from_args(args),
                args.output,
                backend=backend,
                capture=GdiWindowCapture(),
                focus=args.focus,
            )
        elif args.windows_command == "record":
            backend = Win32Backend()
            result = record_window_trace(
                _window_selector_from_args(args),
                task_id=args.task_id,
                output_root=args.output,
                poll_hz=args.poll_hz,
                max_seconds=args.max_seconds,
                backend=backend,
                capture=GdiWindowCapture(),
                monitor=Win32InputMonitor(),
            )
        else:
            if args.focus or (args.execute and not args.background):
                print("Waiting up to 10 seconds for the target to become foreground...")
            result = run_windows_agent(
                args.task,
                execute=args.execute,
                model=args.model,
                codex_bin=args.codex_bin,
                plan_horizon=args.plan_horizon,
                max_actions=args.max_actions,
                output_root=args.output,
                background=args.background,
                focus=args.focus,
            )
    elif args.command == "agent":
        result = run_agent(
            args.seed,
            provider=args.provider,
            model=args.model,
            codex_bin=args.codex_bin,
            task_path=args.task,
            relocate_after=args.relocate_after,
            plan_horizon=args.plan_horizon,
            fps=args.motor_fps,
            show=not args.headless,
            output_root=args.output,
        )
    else:
        result = run_demo(
            record_seed=args.record_seed,
            changed_seed=args.changed_seed,
            relocate_after=args.relocate_after,
            output_root=args.output,
            show=args.show,
        )
    _print_result(result)
    return 0
