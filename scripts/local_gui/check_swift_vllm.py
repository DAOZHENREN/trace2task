"""Bounded saved-observation parity and latency check; executes no GUI actions."""

import argparse
import json
from pathlib import Path


def main():
    import torch
    from trace2task.local_gui_swift_vllm import VLLMReader

    p = argparse.ArgumentParser()
    p.add_argument("--export", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    reader = VLLMReader(a.export / "model")
    results = []
    for i, check in enumerate(json.loads((a.export / "merge-check.json").read_text())):
        request = json.loads(Path(check["request"]).read_text())
        repeats = []
        native = torch.load(a.export / f"comparison-{i}.pt", map_location="cpu", weights_only=True)[
            "after"
        ]
        for repeat in range(3):
            result = reader.predict(request)
            x, y = native, reader.last_logits
            px, py = x.log_softmax(-1), y.log_softmax(-1)
            result.update(
                exact_action=result["action"] == check["before"]["action"],
                mean_kl=(px.exp() * (px - py)).sum(-1).mean().item(),
                mean_abs=(x - y).abs().mean().item(),
                max_abs=(x - y).abs().max().item(),
            )
            repeats.append(result)
            torch.save(y, a.output / f"logits-{i}-{repeat}.pt")
            print(json.dumps({"case": i, "repeat": repeat, **result}), flush=True)
        results.append({"request": check["request"], "repeats": repeats})
        (a.output / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
