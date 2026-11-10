import json
from trace2task.round_input_evidence import with_local_input
from trace2task.run_session import RunStore


def test_historical_round_reads_native_input_without_rewriting_evidence(tmp_path):
    folder = tmp_path / 'runs/local-gui/round'
    folder.mkdir(parents=True)
    messages = [{'role': 'user', 'content': 'Goal once'},
                {'role': 'assistant', 'content': 'click'},
                {'role': 'user', 'content': 'Receipt; continue current goal'}]
    (folder / 'input.json').write_text(json.dumps({'messages': messages,
        'new_messages': [{'role': 'user', 'content': 'Repeated transport goal'}]}))
    old = {'step_index': 2, 'status': 'predicted', 'model_role': 'executor',
           'output_directory': str(folder), 'input': {'messages': [{'role': 'user', 'content': 'Wrong goal'}]}}
    store = RunStore(tmp_path)
    store.round('test', old)
    view = store.read_round('test', 2)
    assert view['input']['messages'] == messages
    assert view['provider'] == 'local'
    saved = json.loads(store.db.execute('SELECT value FROM rounds').fetchone()[0])
    assert saved == old
    assert with_local_input({**old, 'model_role': 'director'}, tmp_path/'runs') == {**old, 'model_role': 'director'}
    assert with_local_input(old, tmp_path/'other') == old
    (folder / 'input.json').unlink()
    assert store.read_round('test', 2) == old
