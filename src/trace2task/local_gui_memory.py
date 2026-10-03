"""VRAM admission estimate, not an application token/context cap.

Measured free CUDA memory and reclaimable allocator blocks determine admission.
The estimate includes generation KV, input tensors, vision/prefill workspace and
observed workspace peaks. Actual CUDA OOM is still handled by the caller.
"""


def memory_admission(model, inputs, torch, *, output_tokens=512, observed_bytes_per_token=0):
    config = model.config.text_config
    tokens = int(inputs['input_ids'].shape[-1])
    element_bytes = model.dtype.itemsize
    kv_bytes = (2 * config.num_hidden_layers * config.num_key_value_heads
                * config.head_dim * element_bytes * (tokens + output_tokens))
    input_bytes = sum(value.numel() * value.element_size() for value in inputs.values()
                      if hasattr(value, 'numel'))
    grid = inputs.get('image_grid_thw')
    patches = int(grid.prod(dim=-1).sum().item()) if grid is not None else 0
    vision_bytes = patches * model.config.vision_config.hidden_size * element_bytes * 8
    prefill_bytes = tokens * config.hidden_size * element_bytes * 16
    # Workspace floor is bytes, not a hard token count; estimate is deliberately conservative.
    workspace_bytes = max(256 * 1024**2, prefill_bytes + vision_bytes)
    estimated = max(kv_bytes + input_bytes + workspace_bytes,
                    int(observed_bytes_per_token * tokens) + input_bytes)
    free, total = torch.cuda.mem_get_info()
    reclaimable = max(0, torch.cuda.memory_reserved() - torch.cuda.memory_allocated())
    reserve = int(total * .12)  # Leave headroom for desktop/driver activity and estimation error.
    available = max(0, free + reclaimable - reserve)
    return {'basis': 'measured_cuda_vram', 'free_bytes': free, 'total_bytes': total,
            'reclaimable_bytes': reclaimable, 'reserve_bytes': reserve,
            'available_for_inference_bytes': available,
            'estimated_inference_bytes': estimated, 'estimated_kv_bytes': kv_bytes,
            'fits': estimated <= available}
