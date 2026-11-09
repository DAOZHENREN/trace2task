import type {Config, KnowledgeProfile} from './types';
import {DEFAULT_ANDROID_BUDGET, DEFAULT_HYBRID_LIMITS} from './types';
import {AUTO_MEMORY} from './experience';

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', signal,
    headers: {'Content-Type': 'application/json', 'X-Trace2Task-CSRF':
      document.querySelector<HTMLMetaElement>('meta[name="trace2task-csrf"]')?.content ?? ''},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data as T;
}

export function requestFor(config: Config, instruction: string, knowledge?: KnowledgeProfile) {
  const hybrid = config.executionMode === 'hybrid';
  if (hybrid) config = {...config, provider: config.hybridDirectorProvider || 'codex', mobileforge: '', androidModelProfile: ''};
  const executor = config.hybridExecutor;
  const executorSelection = !executor || executor.provider === 'local'
    ? {local_model: config.localModel, inference_backend: config.inference}
    : {provider: executor.provider, model: executor.model, reasoning_effort: executor.provider === 'codex' ? executor.effort : 'default',
      ...(executor.provider === 'api' ? {api: {base_url: executor.apiUrl, api_key: executor.apiKey,
        response_format: executor.apiFormat || 'json_schema', timeout_seconds: 120, context_window_tokens: 32768}} : {})};
  const localGUI = config.provider === 'local';
  return {instruction, mode: 'execute', execution_scope: config.scope === 'android' ? 'android' : config.scope === 'browser' ? 'browser' : 'desktop', orchestration: 'legacy', continuous: true,
    ...(config.executor === 'android' ? {android_config: config.android, android_budget: config.androidBudget || DEFAULT_ANDROID_BUDGET, mobileforge: config.mobileforge || '',
      android_model_profile: config.androidModelProfile || ''} : {}),
    execution_mode: hybrid ? 'hybrid' : 'original',
    ...(knowledge ? {knowledge_selection: {id: knowledge.id, sha256: knowledge.sha256}} : {}),
    ...(hybrid ? {hybrid: {...executorSelection, controller_config: config.hybridLimits || DEFAULT_HYBRID_LIMITS}} : {}),
    provider: localGUI ? 'trained_d' : config.provider === 'codex' ? 'codex' : 'api',
    model: config.provider === 'local' ? (config.localModel === 'trained_d' ? 'D-5970' : config.localModel)
      : config.provider === 'api' ? config.apiModel : config.model,
    reasoning_effort: config.provider === 'codex' ? config.effort : 'default',
    ...(localGUI && config.localModel !== 'trained_d' ? {inference_backend: config.inference} : {}),
    executor_backend: config.executor,
    cua_target: config.executor !== 'cua' ? null : config.scope === 'desktop' || config.scope === 'child_desktop'
      ? {kind: 'desktop', display_id: config.scope === 'child_desktop' ? 'child_session' : 'primary'}
      : {targets: config.targets.map(t => t.value), initial_index: config.initialTarget},
    memory_mode: config.experience === AUTO_MEMORY ? 'auto' : config.experience ? 'manual' : 'off',
    use_experience: !!config.experience && config.experience !== AUTO_MEMORY,
    task_path: config.experience === AUTO_MEMORY ? '' : config.experience,
    ...(config.provider === 'codex' || localGUI ? {} : {api:
        {base_url: config.apiUrl, api_key: config.apiKey, response_format: config.apiFormat,
         timeout_seconds: config.apiTimeout, context_window_tokens: config.apiBudget}})};
}
