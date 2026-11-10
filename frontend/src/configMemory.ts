import type {Config} from './types';

export const CONFIG_MEMORY_KEY = 'trace2task.workbench.config.v1';
const strings = ['provider', 'model', 'localModel', 'inference', 'executor', 'scope', 'experience',
  'effort', 'apiUrl', 'apiModel', 'apiFormat', 'executionMode', 'hybridDirectorProvider',
  'desktopKnowledgeId', 'mobileforge', 'androidModelProfile'] as const;
const numbers = ['apiBudget', 'apiTimeout'] as const;
const groups = {
  hybridLimits: {max_decisions: 'number', max_guide_calls: 'number', review_interval: 'number', repeated_no_change_limit: 'number'},
  hybridExecutor: {provider: 'string', model: 'string', effort: 'string', apiUrl: 'string', apiFormat: 'string'},
  osworld: {task_id: 'string', max_actions: 'number'},
  androidworld: {task_id: 'string', max_actions: 'number'},
  android: {adb: 'string', serial: 'string', package: 'string', server_port: 'number', launch_app: 'boolean'},
  androidBudget: {max_actions: 'number', max_seconds: 'number'},
} as const;

// Explicit fields: never persist API keys, window handles or execution authority.
function preferences(value: unknown): Partial<Config> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const source = value as Record<string, unknown>, result: Record<string, unknown> = {};
  for (const key of strings) if (typeof source[key] === 'string') result[key] = source[key];
  for (const key of numbers) if (typeof source[key] === 'number' && Number.isFinite(source[key])) result[key] = source[key];
  if (typeof source.androidKnowledgeOff === 'boolean') result.androidKnowledgeOff = source.androidKnowledgeOff;
  for (const [key, fields] of Object.entries(groups)) {
    const obj = source[key];
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) continue;
    const clean: Record<string, unknown> = {};
    for (const [field, type] of Object.entries(fields)) {
      const v = (obj as Record<string, unknown>)[field];
      if (typeof v === type && (type !== 'number' || Number.isFinite(v))) clean[field] = v;
    }
    if (key === 'hybridExecutor') clean.apiKey = '';
    // Ignore incomplete required structures (e.g. interrupted or outdated writes).
    const optional = key === 'android' ? ['launch_app'] : key === 'hybridExecutor' ? ['apiFormat'] : [];
    if (Object.keys(fields).every(field => optional.includes(field) || field in clean)) result[key] = clean;
  }
  return result as Partial<Config>;
}

export function loadConfigMemory(): Partial<Config> | null {
  try {
    const raw = localStorage.getItem(CONFIG_MEMORY_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    if (saved.version !== 1) return null;
    const value = preferences(saved.config);
    return Object.keys(value).length ? value : null;
  } catch { return null; }
}

export function saveConfigMemory(config: Config) {
  try { localStorage.setItem(CONFIG_MEMORY_KEY, JSON.stringify({version: 1, config: preferences(config)})); }
  catch { /* Storage disabled/full must not prevent task execution. */ }
}
