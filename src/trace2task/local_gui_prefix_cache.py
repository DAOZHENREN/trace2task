"""Bounded exact text-prefix KV reuse for Transformers 4.57 Qwen3-VL.

Never cache image tokens: every request recomputes its visual embeddings and RoPE.
Caller must serialize access to the model. This is not conversation memory.
"""
import copy


class TextPrefixCache:
    def __init__(self, max_tokens=6144):
        self.max_tokens = max_tokens
        self.clear()

    def clear(self):
        self.key = None
        self.cache = None

    def prepare(self, model, inputs):
        import torch
        ids = inputs['input_ids']
        mask = inputs['attention_mask']
        if ids.shape[0] != 1 or not bool(mask.all()) or inputs.get('pixel_values_videos') is not None:
            self.clear()
            return None, {'status': 'unsupported', 'reused_tokens': 0}
        tokens = ids[0].tolist()
        boundary_ids = {model.config.vision_start_token_id, model.config.image_token_id,
                        model.config.video_token_id}
        boundary = next((i for i, value in enumerate(tokens) if value in boundary_ids), len(tokens))
        length = min(boundary, self.max_tokens, len(tokens) - 2)
        if length <= 0:
            self.clear()
            return None, {'status': 'empty_prefix', 'reused_tokens': 0}
        key = tuple(tokens[:length])
        hit = self.key == key and self.cache is not None
        # Full current-image positions are essential even when the prefix is cached.
        positions, delta = model.model.get_rope_index(
            ids, inputs.get('image_grid_thw'), inputs.get('video_grid_thw'), attention_mask=mask)
        if not hit:
            self.clear()
            output = model.model(input_ids=ids[:, :length], attention_mask=mask[:, :length],
                position_ids=positions[:, :, :length], cache_position=torch.arange(length, device=ids.device),
                use_cache=True, return_dict=True)
            self.cache = output.past_key_values
            self.key = key
            del output
        # Generation mutates caches. Keep only one immutable, bounded prefix entry.
        cache = copy.deepcopy(self.cache)
        suffix = {k: v for k, v in inputs.items() if k not in {'input_ids', 'attention_mask', 'position_ids'}}
        output = model.model(input_ids=ids[:, length:-1], attention_mask=mask[:, :-1],
            position_ids=positions[:, :, length:-1],
            cache_position=torch.arange(length, ids.shape[-1] - 1, device=ids.device),
            past_key_values=cache, use_cache=True, return_dict=True, **suffix)
        model.model.rope_deltas = delta
        return output.past_key_values, {'status': 'hit' if hit else 'miss',
            'prefix_tokens': length, 'reused_tokens': length if hit else 0,
            'recomputed_prompt_tokens': len(tokens) - (length if hit else 0),
            'max_prefix_tokens': self.max_tokens}
