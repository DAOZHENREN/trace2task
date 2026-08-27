from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from trace2task.runner import record_human, replay_trace, run_demo, run_visual_agent


def _print_result(result: object) -> None:
    if hasattr(result, "__dataclass_fields__"):
        result = asdict(result)  # type: ignore[arg-type]
    print(json.dumps(result, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace2task",
        description="Record a small game task, replay it, or solve it with a visual replanning agent.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Record a human demonstration.")
    record.add_argument("--seed", type=int, default=7)
    record.add_argument("--output", type=Path, default=Path("runs"))

    replay = subparsers.add_parser("replay", help="Replay recorded actions on a reset task.")
    replay.add_argument("trace", type=Path)
    replay.add_argument("--seed", type=int, default=19)
    replay.add_argument("--output", type=Path, default=Path("runs"))
    replay.add_argument("--headless", action="store_true")

    agent = subparsers.add_parser("agent", help="Run the visual replanning agent.")
    agent.add_argument("--seed", type=int, default=19)
    agent.add_argument("--relocate-after", type=int, default=4)
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
    elif args.command == "agent":
        result = run_visual_agent(
            args.seed,
            relocate_after=args.relocate_after,
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
