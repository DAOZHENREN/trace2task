import type {Config} from './types';

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', signal,
    headers: {'Content-Type': 'application/json', 'X-Trace2Task-CSRF':
      document.querySelector<HTMLMetaElement>('meta[name="trace2task-csrf"]')?.content ?? ''},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data as T;
}

export function requestFor(config: Config, instruction: string) {
  const localGUI = config.provider === 'local';
  return {instruction, mode: 'execute', execution_scope: 'desktop', orchestration: 'legacy', continuous: true,
    provider: localGUI ? 'trained_d' : config.provider === 'codex' ? 'codex' : 'api',
    model: config.provider === 'local' ? (config.localModel === 'trained_d' ? 'D-5970' : config.localModel)
      : config.provider === 'api' ? config.apiModel : config.model,
    reasoning_effort: config.provider === 'codex' ? config.effort : 'default',
    ...(localGUI && config.localModel !== 'trained_d' ? {inference_backend: config.inference} : {}),
    executor_backend: config.executor,
    cua_target: config.executor !== 'cua' ? null : config.scope === 'desktop'
      ? {kind: 'desktop', display_id: 'primary'}
      : {targets: config.targets.map(t => t.value), initial_index: config.initialTarget},
    use_experience: !!config.experience, task_path: config.experience,
    ...(config.provider === 'codex' || localGUI ? {} : {api:
        {base_url: config.apiUrl, api_key: config.apiKey, response_format: config.apiFormat,
         timeout_seconds: config.apiTimeout, context_window_tokens: config.apiBudget}})};
}
