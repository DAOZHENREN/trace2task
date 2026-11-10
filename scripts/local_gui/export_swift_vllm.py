"""Inference-only PEFT merge; original release and observed outputs stay immutable."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    import torch
    from trace2task.local_gui_swift import NativeRuntime, deployment_paths

    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--requests", type=Path, nargs="+", required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    runtime = NativeRuntime().load()
    captured = []
    hook = runtime.reader.register_forward_hook(
        lambda mod, args, out: captured.append(out.logits[0].detach().float().cpu())
    )
    requests = [json.loads(path.read_text()) for path in a.requests]
    before = [runtime.predict(r) for r in requests]
    before_logits = captured[:]
    captured.clear()
    base = runtime.model.base
    # The frozen 4B release adapts the language stack only; head and embeddings
    # are not adapted. Preserve their original tying, unlike the 2B head export.
    language = base.model.language_model
    language = language.merge_and_unload(safe_merge=True)
    for i, layer in enumerate(language.layers):
        if hasattr(layer, "_checkpoint_wrapped_module"):
            language.layers[i] = layer._checkpoint_wrapped_module
    base.model.language_model = language
    assert base.lm_head.weight is base.get_input_embeddings().weight
    after = [runtime.predict(r) for r in requests]
    hook.remove()
    rows = []
    for i, (x, y) in enumerate(zip(before_logits, captured)):
        px, py = x.log_softmax(-1), y.log_softmax(-1)
        rows.append(
            dict(
                request=str(a.requests[i]),
                before=before[i],
                after=after[i],
                exact_action=before[i]["action"] == after[i]["action"],
                mean_abs=(x - y).abs().mean().item(),
                max_abs=(x - y).abs().max().item(),
                mean_kl=(px.exp() * (px - py)).sum(-1).mean().item(),
            )
        )
        torch.save({"before": x, "after": y}, a.output / f"comparison-{i}.pt")
    (a.output / "merge-check.json").write_text(json.dumps(rows, indent=2))
    # Report rounding even when actions agree; never claim bitwise equivalence.
    if not all(r["exact_action"] and r["mean_kl"] < 0.005 for r in rows):
        raise ValueError("Merge changes reference actions/distribution; inspect merge-check.json")
    base.config.use_cache = True
    base.save_pretrained(a.output / "model", safe_serialization=True, max_shard_size="4GB")
    runtime.proc.save_pretrained(a.output / "model")
    (a.output / "provenance.json").write_text(
        json.dumps(
            dict(
                sources=list(map(str, deployment_paths())),
                merge="PEFT merge_and_unload(safe_merge=True)",
                dtype="bfloat16",
                adapted_scope="language stack only; original tied head/embeddings preserved",
                rounding="approximate inference derivative; see merge-check.json",
                files={
                    str(f.relative_to(a.output)): hashlib.sha256(f.read_bytes()).hexdigest()
                    for f in (a.output / "model").iterdir()
                    if f.is_file()
                },
            ),
            indent=2,
        )
    )
    print(json.dumps(rows, indent=2), flush=True)


if __name__ == "__main__":
    main()
