from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from trace2task import __version__
from trace2task.codex_app_server import CODEX_REASONING_EFFORTS
from trace2task.compiler import compile_trace, confirm_taskpack
from trace2task.evaluation import run_evaluation_suite
from trace2task.model_api import (
    API_REASONING_EFFORTS,
    API_RESPONSE_FORMATS,
    DEFAULT_API_BASE_URL,
    ModelAPIConfig,
)
from trace2task.runner import DEFAULT_TASK_PATH, record_human, replay_trace, run_agent, run_demo
from trace2task.waa_bridge import serve_waa_bridge
from trace2task.waa_experiment import (
    DEFAULT_WAA_CONDITIONS,
    DEFAULT_WAA_RESET_SPEC,
    run_waa_experiment,
)
from trace2task.waa_results import write_waa_report
from trace2task.waa_study import DEFAULT_STUDY_OUTPUT, prepare_waa_study
from trace2task.waa_study_results import write_waa_study_report
from trace2task.web_console import serve_web_console
from trace2task.windows_agent import WINDOWS_EXPERIENCE_MODES
from trace2task.windows_capture import GdiWindowCapture, capture_window_once
from trace2task.windows_control import (
    Win32Backend,
    WindowSelector,
    list_window_records,
    probe_window_key,
    probe_window_mouse_button,
)
from trace2task.windows_experience import (
    DEFAULT_COMPILER_MODEL,
    DEFAULT_COMPILER_REASONING_EFFORT,
    compile_windows_semantic_experience,
)
from trace2task.windows_recording import (
    CONTROL_KEY_CODES,
    Win32InputMonitor,
    record_window_trace,
)
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
        description=(
            "Turn reviewed human Windows demonstrations into adaptive multimodal Agent tasks."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    web_console = subparsers.add_parser(
        "web",
        help="Open the local browser control console.",
    )
    web_console.add_argument("--port", type=int, default=8765)
    web_console.add_argument(
        "--no-open",
        action="store_true",
        help="Start the server without opening the default browser.",
    )

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

    evaluation = subparsers.add_parser(
        "eval",
        help="Run a resettable task suite repeatedly and aggregate verifier outcomes.",
    )
    evaluation_subparsers = evaluation.add_subparsers(
        dest="evaluation_command",
        required=True,
    )
    evaluation_run = evaluation_subparsers.add_parser(
        "run",
        help="Run every case in an evaluation suite.",
    )
    evaluation_run.add_argument("--suite", type=Path, required=True)
    evaluation_run.add_argument("--model", default="gpt-5.6-terra")
    evaluation_run.add_argument(
        "--reasoning-effort",
        choices=CODEX_REASONING_EFFORTS,
        default="low",
    )
    evaluation_run.add_argument(
        "--output",
        type=Path,
        default=Path("evaluations"),
    )
    evaluation_run.add_argument(
        "--execute",
        action="store_true",
        help="Execute task actions. Without this flag the suite only evaluates dry-run plans.",
    )

    waa = subparsers.add_parser(
        "waa",
        help="Connect Trace2Task to a Windows Agent Arena client.",
    )
    waa_subparsers = waa.add_subparsers(dest="waa_command", required=True)
    waa_bridge = waa_subparsers.add_parser(
        "bridge",
        help="Serve Codex decisions to the isolated WAA runner over authenticated HTTP.",
    )
    waa_bridge.add_argument("--task", type=Path, required=True)
    waa_bridge.add_argument(
        "--experience-mode",
        choices=WINDOWS_EXPERIENCE_MODES,
        required=True,
    )
    waa_bridge.add_argument("--model", default="gpt-5.6-terra")
    waa_bridge.add_argument(
        "--reasoning-effort",
        choices=CODEX_REASONING_EFFORTS,
        default="low",
    )
    waa_bridge.add_argument("--codex-bin", default="codex")
    waa_bridge.add_argument("--plan-horizon", type=int, default=12)
    waa_bridge.add_argument("--host", default="127.0.0.1")
    waa_bridge.add_argument("--port", type=int, default=8776)
    waa_bridge.add_argument(
        "--token",
        required=True,
        help="Shared local token required from the WAA client.",
    )
    waa_report = waa_subparsers.add_parser(
        "report",
        help="Aggregate WAA evaluator outcomes and efficiency metrics by experience mode.",
    )
    waa_report.add_argument("--results-root", type=Path, required=True)
    waa_report.add_argument(
        "--output",
        type=Path,
        default=Path("evaluations") / "windows-agent-arena",
    )
    waa_experiment = waa_subparsers.add_parser(
        "experiment",
        help="Run reset-verified WAA experience ablations and write one report.",
    )
    waa_experiment.add_argument("--waa-root", type=Path, required=True)
    waa_experiment.add_argument("--task", type=Path, required=True)
    waa_experiment.add_argument(
        "--narrated-task",
        type=Path,
        help=(
            "Separate task pack compiled from the same Trace with human narration; required "
            "when comparing compiled and narrated_compiled."
        ),
    )
    waa_experiment.add_argument(
        "--feedback-task",
        type=Path,
        help=(
            "Reviewed task pack containing human guidance for the feedback condition. "
            "Defaults to --narrated-task when supplied, otherwise --task."
        ),
    )
    waa_experiment.add_argument(
        "--reset-spec",
        type=Path,
        default=DEFAULT_WAA_RESET_SPEC,
    )
    waa_experiment.add_argument(
        "--conditions",
        nargs="+",
        choices=WINDOWS_EXPERIENCE_MODES,
        default=DEFAULT_WAA_CONDITIONS,
    )
    waa_experiment.add_argument("--repetitions", type=int, default=3)
    waa_experiment.add_argument("--model", default="gpt-5.6-terra")
    waa_experiment.add_argument(
        "--reasoning-effort",
        choices=CODEX_REASONING_EFFORTS,
        default="low",
    )
    waa_experiment.add_argument("--codex-bin", default="codex")
    waa_experiment.add_argument("--plan-horizon", type=int, default=12)
    waa_experiment.add_argument("--distro", default="Trace2Task-WAA")
    waa_experiment.add_argument("--container", default="winarena")
    waa_experiment.add_argument(
        "--json-name",
        default="evaluation_examples_windows/test_trace2task.json",
    )
    waa_experiment.add_argument("--token", default="trace2task-local-eval")
    waa_experiment.add_argument("--bridge-port", type=int, default=8776)
    waa_experiment.add_argument("--relay-port", type=int, default=8876)
    waa_experiment.add_argument(
        "--allow-automatic-compiler-draft",
        action="store_true",
        help=(
            "Run one frozen, unreviewed Compiler snapshot as an isolated compiled "
            "research condition. Never enables ordinary draft task packs."
        ),
    )
    waa_experiment.add_argument(
        "--output",
        type=Path,
        default=Path("evaluations") / "windows-agent-arena",
    )
    waa_study = waa_subparsers.add_parser(
        "study-plan",
        help="Freeze a paper-grade WAA protocol and deterministic episode schedule.",
    )
    waa_study.add_argument("--spec", type=Path, required=True)
    waa_study.add_argument("--waa-root", type=Path, required=True)
    waa_study.add_argument("--output", type=Path, default=DEFAULT_STUDY_OUTPUT)
    waa_study.add_argument(
        "--strict",
        action="store_true",
        help="Fail after writing the readiness report when any study cell is incomplete.",
    )
    waa_study_report = waa_subparsers.add_parser(
        "study-report",
        help="Aggregate one completed paper-grade WAA study run.",
    )
    waa_study_report.add_argument("--study-root", type=Path, required=True)
    waa_study_report.add_argument("--run-root", type=Path, required=True)
    waa_study_report.add_argument(
        "--output",
        type=Path,
        help="Output directory; defaults to <run-root>/analysis.",
    )

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
    windows_record.add_argument(
        "--success-key",
        choices=sorted(CONTROL_KEY_CODES),
        default="f8",
        help="Key that saves the success frame and finishes recording (default: f8).",
    )
    windows_record.add_argument(
        "--cancel-key",
        choices=sorted(CONTROL_KEY_CODES),
        default="f9",
        help="Key that cancels recording (default: f9).",
    )
    windows_probe = windows_subparsers.add_parser(
        "input-probe",
        help="Test one explicit Windows input delivery method against a target.",
    )
    _add_window_selector(windows_probe)
    windows_probe.add_argument(
        "--method",
        choices=("send-message", "send-input-vk", "send-input-mouse"),
        default="send-message",
        help="Input delivery method to test.",
    )
    windows_probe.add_argument("--key", default="f", help="Keyboard key to test (default: f).")
    windows_probe.add_argument(
        "--button",
        choices=("left", "right", "middle"),
        default="middle",
        help="Mouse button for send-input-mouse (default: middle).",
    )
    windows_probe.add_argument(
        "--hold-ms",
        type=int,
        default=500,
        help="How long to hold the test key or button (default: 500ms).",
    )
    windows_probe.add_argument(
        "--settle-seconds",
        type=float,
        default=1.0,
        help="Delay after focusing the target before sending input (default: 1s).",
    )
    windows_probe.add_argument(
        "--message-timeout-ms",
        type=int,
        default=1_000,
        help="Per-message hang timeout (default: 1000ms).",
    )
    windows_compile_experience = windows_subparsers.add_parser(
        "compile-experience",
        help="Compile one Windows task pack into a semantic state graph.",
    )
    windows_compile_experience.add_argument("--task", type=Path, required=True)
    windows_compile_experience.add_argument("--model", default=DEFAULT_COMPILER_MODEL)
    windows_compile_experience.add_argument(
        "--reasoning-effort",
        choices=CODEX_REASONING_EFFORTS,
        default=DEFAULT_COMPILER_REASONING_EFFORT,
    )
    windows_compile_experience.add_argument(
        "--ignore-narration",
        action="store_true",
        help="Compile only from Trace actions and screenshots even if narration.json exists.",
    )
    windows_agent = windows_subparsers.add_parser(
        "agent",
        help="Plan or explicitly execute a confirmed Windows task pack.",
    )
    windows_agent.add_argument("--task", type=Path, required=True)
    windows_agent.add_argument("--model", default="gpt-5.6-terra")
    windows_agent.add_argument("--provider", choices=("codex", "api"), default="codex")
    windows_agent.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    windows_agent.add_argument(
        "--api-key-env", default="TRACE2TASK_API_KEY",
        help="Environment variable holding the API key; never pass the key on the command line.",
    )
    windows_agent.add_argument(
        "--api-response-format", choices=API_RESPONSE_FORMATS, default="json_schema",
    )
    windows_agent.add_argument("--api-timeout", type=float, default=120)
    windows_agent.add_argument(
        "--reasoning-effort",
        choices=tuple(dict.fromkeys((*CODEX_REASONING_EFFORTS, *API_REASONING_EFFORTS))),
        default=None,
        help="Model reasoning depth (Codex default: low; API default: omit the parameter).",
    )
    windows_agent.add_argument("--codex-bin", default="codex")
    windows_agent.add_argument(
        "--plan-horizon",
        type=int,
        default=12,
        help="Maximum actions in one stage program per model decision (default: 12).",
    )
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
    if args.command == "web":
        serve_web_console(port=args.port, open_browser=not args.no_open)
        return 0
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
    elif args.command == "eval":
        result = run_evaluation_suite(
            args.suite,
            execute=args.execute,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            output_root=args.output,
        )
    elif args.command == "waa":
        if args.waa_command == "bridge":
            serve_waa_bridge(
                args.task,
                experience_mode=args.experience_mode,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                token=args.token,
                host=args.host,
                port=args.port,
                codex_bin=args.codex_bin,
                plan_horizon=args.plan_horizon,
            )
            return 0
        if args.waa_command == "experiment":
            result = run_waa_experiment(
                args.waa_root,
                args.task,
                reset_spec=args.reset_spec,
                conditions=args.conditions,
                narrated_task_path=args.narrated_task,
                feedback_task_path=args.feedback_task,
                repetitions=args.repetitions,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                codex_bin=args.codex_bin,
                plan_horizon=args.plan_horizon,
                distro=args.distro,
                container=args.container,
                json_name=args.json_name,
                token=args.token,
                bridge_port=args.bridge_port,
                relay_port=args.relay_port,
                output_root=args.output,
                allow_automatic_compiler_draft=args.allow_automatic_compiler_draft,
            )
        elif args.waa_command == "study-plan":
            result = prepare_waa_study(
                args.spec,
                args.waa_root,
                output_root=args.output,
                strict=args.strict,
            )
        elif args.waa_command == "study-report":
            result = write_waa_study_report(
                args.study_root,
                args.run_root,
                output_root=args.output,
            )
        else:
            result = write_waa_report(args.results_root, args.output)
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
                monitor=Win32InputMonitor(
                    success_key=args.success_key,
                    cancel_key=args.cancel_key,
                ),
            )
        elif args.windows_command == "input-probe":
            print("Waiting up to 10 seconds for the target to become foreground...")
            selector = _window_selector_from_args(args)
            backend = Win32Backend()
            if args.method == "send-input-mouse":
                result = probe_window_mouse_button(
                    selector,
                    args.button,
                    hold_ms=args.hold_ms,
                    settle_seconds=args.settle_seconds,
                    backend=backend,
                )
            else:
                result = probe_window_key(
                    selector,
                    args.key,
                    method=args.method,
                    hold_ms=args.hold_ms,
                    settle_seconds=args.settle_seconds,
                    timeout_ms=args.message_timeout_ms,
                    backend=backend,
                )
        elif args.windows_command == "compile-experience":
            result = compile_windows_semantic_experience(
                args.task,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                use_narration=not args.ignore_narration,
            )
        else:
            api_config = (
                ModelAPIConfig(
                    base_url=args.api_base_url,
                    api_key_env=args.api_key_env,
                    response_format=args.api_response_format,
                    timeout_seconds=args.api_timeout,
                ).with_credentials()
                if args.provider == "api" else None
            )
            if args.focus or (args.execute and not args.background):
                print("Waiting up to 10 seconds for the target to become foreground...")
            result = run_windows_agent(
                args.task,
                execute=args.execute,
                model=args.model,
                reasoning_effort=args.reasoning_effort or (
                    "default" if api_config is not None else "low"
                ),
                api_config=api_config,
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
