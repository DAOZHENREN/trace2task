import {useEffect, useState} from 'react';
import {Input} from '@fluentui/react-components';
import {api} from './api';
import {Choice, Field} from './ui';
import type {Config} from './types';

type Task = {id: string; app: string; instruction: string};
export function AndroidWorldTask({config, setConfig, onInstruction, busy}: {
  config: Config; setConfig: (c: Config) => void; onInstruction: (s: string) => void; busy: boolean}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {const abort = new AbortController();
    api<{configured: boolean; tasks: Task[]; error?: string}>('/api/androidworld/tasks', undefined, abort.signal)
      .then(r => {setTasks(r.tasks); if (!r.configured) setError(r.error || '环境未配置');})
      .catch(e => {if (!abort.signal.aborted) setError(String(e));});
    return () => abort.abort();
  }, []);
  const selected = tasks.find(t => t.id === config.androidworld?.task_id);
  useEffect(() => {if (selected) onInstruction(selected.instruction);}, [selected]);
  const filtered = tasks.filter(t => `${t.app} ${t.id} ${t.instruction}`.toLowerCase().includes(query.toLowerCase()));
  const visible = selected && !filtered.includes(selected) ? [selected, ...filtered] : filtered;
  return <section className="surface"><h3>AndroidWorld 开发测试</h3>
    <p>连接官方 AndroidWorld 专用模拟器，使用 MAI-Swift 执行与云端指导。选择任务后重置该任务，结束时由环境评分器判定。一次只运行一个任务，不操作真实手机。</p>
    {error && <p role="alert">{error}</p>}
    <Field label="查找任务"><Input value={query} disabled={busy} onChange={(_, d) => setQuery(d.value)} placeholder="应用、任务 ID 或指令"/></Field>
    <Field label={`任务（${filtered.length} / ${tasks.length}）`}><Choice value={config.androidworld?.task_id || ''} disabled={busy} onChange={e => {
      const t = tasks.find(t => t.id === e.target.value); if (!t) return;
      setConfig({...config, experience: '', androidworld: {task_id: t.id, max_actions: config.androidworld?.max_actions || 120}});
      onInstruction(t.instruction);
    }}><option value="">请选择任务</option>{visible.map(t => <option key={t.id} value={t.id}>{t.app} · {t.instruction.slice(0, 95)} · {t.id}</option>)}</Choice></Field>
    <Field label="总动作预算"><Input type="number" min={1} max={500} value={String(config.androidworld?.max_actions || 120)} disabled={busy} onChange={(_, d) =>
      setConfig({...config, androidworld: {task_id: config.androidworld?.task_id || '', max_actions: Number(d.value)}})}/></Field>
    {selected && <p className="quiet">任务 ID：{selected.id}。任务原文固定；仅在专用模拟器执行。完整轨迹、模型输入输出和评分在运行记录中展示。</p>}
  </section>;
}
