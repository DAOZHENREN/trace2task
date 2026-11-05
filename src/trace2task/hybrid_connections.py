"""Independent hybrid executor selection; credentials stay out of job snapshots."""
from trace2task.hybrid_v7_profile import resolve_controller_config
from trace2task.model_api import API_REASONING_EFFORTS, ModelAPIConfig, validate_api_model
from trace2task.model_registry import profile_for


def prepare_executor(value, catalog, settings):
    if not isinstance(value, dict):
        raise ValueError('请选择主要执行模型')  # noqa: TRY004 - public request validation
    value = dict(value)
    controller_config = resolve_controller_config(value.pop('controller_config', None))
    provider = value.get('provider', 'local')
    if provider == 'local':
        if set(value) not in ({'local_model', 'inference_backend'}, {'provider', 'local_model', 'inference_backend'}):
            raise ValueError('本地执行模型配置无效')
        if not all(isinstance(value[k], str) and value[k] for k in ('local_model', 'inference_backend')):
            raise ValueError('本地模型和推理引擎不能为空')
        profile_for(value['local_model'], value['inference_backend'])
        return {**value, 'controller_config': controller_config}, None
    if provider not in {'codex', 'api'} or set(value) - {'provider', 'model', 'reasoning_effort', 'api'}:
        raise ValueError('执行模型来源必须是 codex / api / local')
    model = validate_api_model(value.get('model'))
    effort = value.get('reasoning_effort', 'low' if provider == 'codex' else 'default')
    public = {'provider': provider, 'model': model, 'reasoning_effort': effort,
              'controller_config': controller_config}
    if provider == 'codex':
        if 'api' in value:
            raise ValueError('Codex 执行模型不接受 API 配置')
        catalog.validate(model, effort)
        return public, None
    if effort not in API_REASONING_EFFORTS or not isinstance(value.get('api'), dict):
        raise ValueError('请配置执行模型的 API 连接和思考强度')
    config = settings.with_saved_key(ModelAPIConfig(**value['api'])).with_credentials()
    public['api_base_url'] = config.base_url
    return public, config
