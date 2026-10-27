import pytest
from test_codex_app_server import ScriptedTransport

from trace2task.codex_app_server import CodexAppServerSession
from trace2task.codex_catalog import CodexModelCatalog, normalize_models


def row(model, efforts=('low', 'ultra'), **extra):
    return {'model': model, 'displayName': model.upper(), 'defaultReasoningEffort': efforts[-1],
            'supportedReasoningEfforts': [{'reasoningEffort': e} for e in efforts], **extra}


def test_model_list_pages_without_starting_a_thread_or_turn(tmp_path):
    transport = ScriptedTransport([{'id': 1, 'result': {}},
        {'id': 2, 'result': {'data': [row('new-model')], 'nextCursor': 'next'}},
        {'id': 3, 'result': {'data': [row('small-model', ('medium', 'high'))], 'nextCursor': None}}])
    session = CodexAppServerSession('fake', model=None, cwd=tmp_path, transport_factory=lambda _: transport)
    assert len(session.list_models()) == 2
    assert [message['method'] for message in transport.sent] == ['initialize', 'initialized', 'model/list', 'model/list']
    assert transport.sent[-1]['params']['cursor'] == 'next'
    assert transport.sent[-1]['params']['includeHidden'] is False
    assert session.thread_id is None
    session.close()
    assert transport.closed


def test_catalog_uses_server_order_defaults_efforts_and_hidden_flags(tmp_path):
    rows = [row('future-model', isDefault=True), row('hidden', hidden=True),
            row('small-model', ('medium', 'high')), row('text-only', inputModalities=['text'])]
    catalog = CodexModelCatalog(tmp_path, loader=lambda _: rows)
    options = catalog.options()
    assert options['models'] == ['future-model', 'small-model', 'text-only']
    assert options['defaults'] == {'model': 'future-model', 'reasoning_effort': 'ultra'}
    assert options['compiler_defaults'] == options['defaults']
    assert options['model_details'][0]['input_modalities'] == ['text', 'image']
    catalog.validate('future-model', 'ultra')
    with pytest.raises(ValueError, match='不支持思考强度'):
        catalog.validate('small-model', 'ultra')
    with pytest.raises(ValueError, match='不包含'):
        catalog.validate('hidden', 'low')
    with pytest.raises(ValueError, match='截图'):
        catalog.validate('text-only', 'low')


def test_cache_refresh_failure_keeps_last_list_but_reports_staleness(tmp_path):
    calls = []
    def load(_):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError('SECRET TRANSPORT STDERR')
        return [row('discovered')]
    catalog = CodexModelCatalog(tmp_path, loader=load)
    assert catalog.options()['catalog_status'] == 'ready'
    catalog.options()
    assert len(calls) == 1
    stale = catalog.options(refresh=True)
    assert stale['models'] == ['discovered'] and stale['catalog_status'] == 'stale'
    assert 'SECRET' not in stale['catalog_error']
    with pytest.raises(ValueError, match='无法读取'):
        catalog.validate('discovered', 'low')
    unavailable = CodexModelCatalog(tmp_path, loader=load).options()
    assert unavailable['models'] == [] and unavailable['defaults']['model'] == ''
    assert unavailable['catalog_status'] == 'unavailable'


def test_empty_or_all_hidden_catalog_is_not_replaced_with_fixed_models():
    with pytest.raises(ValueError, match='未返回'):
        normalize_models([row('hidden', hidden=True)])


@pytest.mark.parametrize('target', ['run_experience.compile_run_draft', 'trace_procedure.compile_b',
                                    'procedure_evolution.evolve_procedure'])
def test_cancelled_compilation_does_not_open_catalog_connection(tmp_path, monkeypatch, target):
    import threading
    from importlib import import_module

    from trace2task.trace_procedure import ProcedureCompileCancelled

    module, function = target.split('.')
    implementation = import_module('trace2task.' + module)
    monkeypatch.setattr(implementation, 'CodexModelCatalog', lambda *args: pytest.fail('must not connect'))
    stop = threading.Event()
    stop.set()
    with pytest.raises(ProcedureCompileCancelled):
        getattr(implementation, function)(tmp_path, {}, model='unused', reasoning_effort='low', stop_event=stop)
