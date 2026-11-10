"""Inference-only adapter for pinned MAI native105 and Transformers 4.57.3.

Retains the real MASK prefix. Uses upstream DynamicCache/forward, explicit full
MRoPE positions and suffix-only image features. No sampling or generated tokens.
The frozen release and its reference reader are unchanged.
"""
import time


class CachedRuntime:
    def __init__(self, runtime):
        self.runtime = runtime
        self.c = runtime.c
        self.reset_cache()

    def reset_cache(self):
        self.cache = None
        self.previous = None
        self.last_metrics = {}
        self.turn_offset = 0
        self.turn_count = 0

    def inputs(self, turns):
        # Keep the published, hash-checking input/window builder authoritative.
        if len(turns) <= self.turn_count:
            self.reset_cache()
        # Reuse upstream history_offset; do not repeatedly tokenize already-dropped
        # turns. The exact same 16384-token window is still chosen by fit_window.
        result = self.runtime.inputs(turns[self.turn_offset:], history_offset=self.turn_offset)
        self.turn_offset = len(turns) - result['image_grid_thw'].shape[0]
        self.turn_count = len(turns)
        return result

    def logits(self, inputs):
        import torch
        from runtime_parts import prepare_parallel_inputs
        r = self.runtime
        torch.cuda.synchronize(r.device)
        start = time.perf_counter()
        prepared = prepare_parallel_inputs(inputs, r.model.get_base_model(), r.device, r.c)
        ids = inputs['input_ids']
        n = ids.shape[1]
        reuse = 0
        images_before = patches_before = 0
        reason = 'cold_start'
        if self.previous is not None:
            old = self.previous
            k = old['input_ids'].shape[1]
            g = old['image_grid_thw'].shape[0]
            p = old['pixel_values'].shape[0]
            # Token identity alone is insufficient: images may have identical grids.
            if (k < n and torch.equal(ids[:, :k], old['input_ids'])
                    and torch.equal(prepared['position_ids'][..., :k].cpu(), old['position_ids'])
                    and torch.equal(inputs['image_grid_thw'][:g], old['image_grid_thw'])
                    and torch.equal(inputs['pixel_values'][:p], old['pixel_values'])
                    and int(inputs['slot_positions'].min()) - 1 >= k):
                if self.cache.get_seq_length() != k:
                    raise RuntimeError('KV cache length differs from verified prefix')
                reuse, images_before, patches_before = k, g, p
                reason = 'append'
            else:
                reason = 'prefix_changed_or_window_trimmed'
                self.cache = None
        keep = inputs['slot_positions'][0].to(r.device) - 1 - reuse
        native = dict(input_ids=prepared['input_ids'][:, reuse:],
                      attention_mask=prepared['attention_mask'],
                      position_ids=prepared['position_ids'][..., reuse:],
                      cache_position=torch.arange(reuse, n, device=r.device),
                      past_key_values=self.cache, use_cache=True,
                      logits_to_keep=keep)
        if inputs['image_grid_thw'].shape[0] > images_before:
            native.update(pixel_values=prepared['pixel_values'][patches_before:],
                          image_grid_thw=prepared['image_grid_thw'][images_before:])
        torch.cuda.synchronize(r.device)
        prep_end = time.perf_counter()
        torch.cuda.reset_peak_memory_stats(r.device)
        try:
            with torch.inference_mode(), torch.autocast('cuda', dtype=torch.bfloat16):
                out = r.model(**native)
            torch.cuda.synchronize(r.device)
            forward_end = time.perf_counter()
            if out.logits.shape[:2] != (1, 105) or out.past_key_values.get_seq_length() != n:
                raise RuntimeError('Invalid cached native105 output/cache shape')
            result = out.logits[0].detach().float().cpu()
            self.cache = out.past_key_values
            self.previous = {name: inputs[name].clone() for name in
                             ('input_ids', 'image_grid_thw', 'pixel_values')}
            self.previous['position_ids'] = prepared['position_ids'].cpu().clone()
            self.last_metrics = dict(cache_reason=reason, dropped_history_turns=self.turn_offset, total_tokens=n, cached_tokens=reuse,
                new_tokens=n-reuse, new_images=inputs['image_grid_thw'].shape[0]-images_before,
                prepare_seconds=prep_end-start, forward_seconds=forward_end-prep_end,
                logits_seconds=time.perf_counter()-start,
                peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(r.device),
                gpu_allocated_bytes=torch.cuda.memory_allocated(r.device))
            return result
        except Exception:
            self.reset_cache()
            raise
