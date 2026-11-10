"""Small custom Pooler over the unchanged vLLM0.11 Qwen3-VL forward/head."""

import torch
from vllm.model_executor.layers.pooler import Pooler, PoolingParamsUpdate
from vllm.model_executor.models.qwen3_vl import (
    Qwen3VLDummyInputsBuilder,
    Qwen3VLForConditionalGeneration,
    Qwen3VLMultiModalProcessor,
    Qwen3VLProcessingInfo,
)
from vllm.multimodal import MULTIMODAL_REGISTRY


class SlotPooler(Pooler):
    def __init__(self, model, table, slots):
        super().__init__()
        object.__setattr__(self, "read_logits", model.compute_logits)
        self.table, self.slots = table, slots

    def get_supported_tasks(self):
        return {"encode"}

    def get_pooling_updates(self, task):
        return PoolingParamsUpdate(requires_token_ids=True)

    def forward(self, hidden_states, pooling_metadata):
        cursor = pooling_metadata.pooling_cursor
        outputs = []
        for i, params in enumerate(pooling_metadata.pooling_params):
            length = int(pooling_metadata.prompt_lens[i])
            scheduled = int(cursor.num_scheduled_tokens_cpu[i])
            end = int(cursor.last_token_indices_gpu[i]) + 1
            extra = params.extra_kwargs
            if extra is None:
                # Upstream profile/warmup has no request parameters and uses
                # synthetic zeros. Exercise the real131-position output head.
                selected = hidden_states[end - 1 : end].expand(131, -1)
            else:
                if extra.get("swift131_table_ids") != self.table:
                    raise ValueError("wrong_swift131_table_contract")
                if scheduled < len(self.table):
                    raise ValueError("current_table_missing_from_prefill; chunking is unsupported")
                ids = pooling_metadata.prompt_token_ids[i, :length]
                if ids[-len(self.table) :].tolist() != self.table:
                    raise ValueError("invalid_swift131_input_suffix")
                indices = (
                    torch.tensor(self.slots, device=hidden_states.device)
                    - 1
                    + end
                    - len(self.table)
                )
                selected = hidden_states[indices]
            outputs.append(self.read_logits(selected).float())
        return outputs


@MULTIMODAL_REGISTRY.register_processor(
    Qwen3VLMultiModalProcessor, info=Qwen3VLProcessingInfo, dummy_inputs=Qwen3VLDummyInputsBuilder
)
class Swift131Qwen3VL(Qwen3VLForConditionalGeneration):
    is_pooling_model = True
    default_pooling_type = "LAST"

    def __init__(self, *, vllm_config, prefix="model"):
        if vllm_config.scheduler_config.enable_chunked_prefill:
            raise ValueError("swift131_initial_adapter_requires_unchunked_prefill")
        if vllm_config.parallel_config.tensor_parallel_size != 1:
            raise ValueError("swift131_initial_adapter_requires_tp1")
        super().__init__(vllm_config=vllm_config, prefix=prefix)
        config = vllm_config.model_config.hf_config
        if len(config.swift131_slots) != 131:
            raise ValueError("expected131slots")
        self.pooler = SlotPooler(self, config.swift131_table_ids, config.swift131_slots)
