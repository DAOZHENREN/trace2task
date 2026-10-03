from types import SimpleNamespace

from trace2task.local_gui_memory import memory_admission


def assess(tokens, *, free_gib=8, observed=0):
    model = SimpleNamespace(dtype=SimpleNamespace(itemsize=2), config=SimpleNamespace(
        text_config=SimpleNamespace(num_hidden_layers=28, num_key_value_heads=8,
                                    head_dim=128, hidden_size=2048),
        vision_config=SimpleNamespace(hidden_size=1024)))
    cuda = SimpleNamespace(mem_get_info=lambda: (free_gib * 1024**3, 12 * 1024**3),
                           memory_reserved=lambda: 5 * 1024**3,
                           memory_allocated=lambda: 5 * 1024**3)
    return memory_admission(model, {'input_ids': SimpleNamespace(shape=(1, tokens))},
                            SimpleNamespace(cuda=cuda), observed_bytes_per_token=observed)


def test_admission_is_vram_based_not_the_previous_6144_cap():
    assert assess(16000)['fits']
    assert not assess(16000, free_gib=2)['fits']
    assert assess(6000, free_gib=5)['fits']
    assert not assess(6000, free_gib=1)['fits']


def test_measured_workspace_and_headroom_are_included():
    estimate = assess(8000)
    measured = assess(8000, observed=1024**2)
    assert measured['estimated_inference_bytes'] > estimate['estimated_inference_bytes']
    assert not measured['fits']
    assert estimate['reserve_bytes'] > 0
    assert estimate['available_for_inference_bytes'] < estimate['free_bytes']
