"""Pinned vLLM0.11 adapter: native Qwen3-VL +131-position prefill readout.

Uses the upstream Apache-2.0 implementation; no attention/cache replacement.
The one private-API adapter bounds prefix hits before the current slot table.
Register through vLLM's general-plugin entry point in an isolated runtime.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

PINS = {
    "v1/core/kv_cache_manager.py": "f02d1d095365b83c5fcb9eff14ac887f35234495b8718f24718a218244dd5590",
    "v1/worker/gpu_model_runner.py": "ea6b7bfe4082349e80042df4801c7ef9e18c80d1c3879eeb6ac6e9c476303574",
    "model_executor/models/qwen3_vl.py": "1ad8854100c4c39d664eb23793ebecd3a65deff9e3733b30a5848c08e76a472f",
    "model_executor/layers/pooler.py": "1a230379db90e8c6e82fa4e649d7ae12dcfc031705db27ba0354e321f631151a",
}


def register():
    import vllm
    from vllm import ModelRegistry
    from vllm.v1.core.kv_cache_manager import KVCacheManager

    if importlib.metadata.version("vllm") != "0.11.0":
        raise ValueError("swift131_requires_pinned_vllm_0.11.0")
    root = Path(vllm.__file__).parent
    for name, expected in PINS.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("vllm_reference_changed:" + name)
    if getattr(KVCacheManager.get_computed_blocks, "_swift131", False):
        return
    ModelRegistry.register_model(
        "Swift131Qwen3VL", "trace2task.desktop131_vllm_model:Swift131Qwen3VL"
    )
    original = KVCacheManager.get_computed_blocks

    @functools.wraps(original)
    def bounded_lookup(manager, request):
        params = request.pooling_params
        extra = params.extra_kwargs if params is not None else None
        if not extra or "swift131_table_ids" not in extra:
            return original(manager, request)
        table = extra["swift131_table_ids"]
        if not table or request.prompt_token_ids[-len(table) :] != table:
            raise ValueError("swift131_request_does_not_end_in_mask_table")
        boundary = len(request.prompt_token_ids) - len(table)

        class PrefixView:
            # Only lookup sees the truncated hash list. Allocation and all
            # subsequent cache writes retain the original, complete request.
            def __getattr__(self, name):
                return getattr(request, name)

            @property
            def block_hashes(self):
                return request.block_hashes[: boundary // manager.block_size]

        result = original(manager, PrefixView() if manager.enable_caching else request)
        if result[1] > boundary:
            raise RuntimeError("readout_positions_were_cached")
        if trace := os.environ.get("SWIFT131_CACHE_TRACE"):
            with open(trace, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "request": request.request_id,
                            "tokens": request.num_tokens,
                            "cached_tokens": result[1],
                            "readout_boundary": boundary,
                        }
                    )
                    + "\n"
                )
        return result

    bounded_lookup._swift131 = True
    KVCacheManager.get_computed_blocks = bounded_lookup


def make_engine(
    model, codec, *, prefix_caching=True, max_model_len=24576, max_images=3, dtype="bfloat16"
):
    from vllm import LLM
    from vllm.config import PoolerConfig

    register()
    return LLM(
        model=str(model),
        tokenizer=str(model),
        runner="pooling",
        dtype=dtype,
        hf_overrides={
            "architectures": ["Swift131Qwen3VL"],
            "swift131_table_ids": list(codec.input_ids),
            "swift131_slots": list(codec.slot_positions),
        },
        pooler_config=PoolerConfig(pooling_type="LAST"),
        enable_prefix_caching=prefix_caching,
        enable_chunked_prefill=False,
        max_model_len=max_model_len,
        max_num_batched_tokens=max_model_len,
        max_num_seqs=1,
        tensor_parallel_size=1,
        enforce_eager=True,
        gpu_memory_utilization=0.70,
        limit_mm_per_prompt={"image": max_images, "video": 0},
        mm_processor_kwargs={"min_pixels": 65536, "max_pixels": 1048576},
        skip_mm_profiling=True,
        seed=20261106,
    )
