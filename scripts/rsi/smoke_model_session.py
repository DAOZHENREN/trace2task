"""Explicit one-turn image smoke on the server; no VM or desktop actions."""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from trace2task.rsi_codex import CodexRSITransport
from trace2task.rsi_model_session import ModelContainerConfig, session_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--codex-bin", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    run_dir = root / "smoke" / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 80, 240, 250), fill="blue")
    draw.ellipse((380, 100, 530, 250), fill="red")
    image_path = run_dir / "fixture.png"
    image.save(image_path)
    config = ModelContainerConfig(args.codex_home, args.codex_bin,
                                  root / "controller/src/trace2task/rsi_model_only.py",
                                  os.getuid(), os.getgid())
    adapter = CodexRSITransport(
        session_factory=session_factory(config), model=args.model, reasoning_effort="low",
        audit_dir=run_dir / "calls", max_calls=1, wall_seconds=180)
    started = time.monotonic()
    answer = adapter.chat(args.model, "Answer from the supplied image only. Do not use tools.",
                          "What color is the circle? Reply with only the lowercase color name.",
                          image=image_path.read_bytes())
    result = {"answer": answer, "passed": answer.strip().lower() == "red",
              "elapsed_seconds": round(time.monotonic() - started, 2),
              "artifact_dir": str(run_dir), "model": args.model,
              "real_vm_test": False}
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit("Image smoke response did not match the fixture")


if __name__ == "__main__":
    main()
