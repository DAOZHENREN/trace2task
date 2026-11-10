import {useEffect, useState} from 'react';
import {Input} from '@fluentui/react-components';
import {api} from './api';
import {Choice, Field} from './ui';
import type {Config} from './types';

type Task = {id: string; app: string; instruction: string};
export function OSWorldTask({config, setConfig, onInstruction, busy}: {
  config: Config; setConfig: (c: Config) => void; onInstruction: (s: string) => void; busy: boolean}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {const abort = new AbortController();
    api<{configured: boolean; tasks: Task[]; error?: string}>('/api/osworld/tasks', undefined, abort.signal)
      .then(r => {setTasks(r.tasks); if (!r.configured) setError(r.error || '环境未配置');})
      .catch(e => {if (!abort.signal.aborted) setError(String(e));});
    return () => abort.abort();
  }, []);
  const selected = tasks.find(t => t.id === config.osworld?.task_id);
  useEffect(() => {if (selected) onInstruction(selected.instruction);}, [selected]);
  const filtered = tasks.filter(t => `${t.app} ${t.id} ${t.instruction}`.toLowerCase().includes(query.toLowerCase()));
  const visible = selected && !filtered.includes(selected) ? [selected, ...filtered] : filtered;
  return <section className="surface"><h3>OSWorld 开发测试</h3>
    <p>复用已有 OSWorld 环境；任务来源：HybridCUA RLVR。每次启动创建独立访客并重置任务，运行结束调用原评分器。结果不等同于标准 OSWorld 榜单成绩。</p>
    {error && <p role="alert">{error}</p>}
    <Field label="查找任务"><Input value={query} disabled={busy} onChange={(_, d) => setQuery(d.value)} placeholder="应用、任务 ID 或指令"/></Field>
    <Field label={`任务（${filtered.length} / ${tasks.length}）`}><Choice value={config.osworld?.task_id || ''} disabled={busy} onChange={e => {
      const t = tasks.find(t => t.id === e.target.value); if (!t) return;
      setConfig({...config, experience: '', osworld: {task_id: t.id, max_actions: config.osworld?.max_actions || 120}});
      onInstruction(t.instruction);
    }}><option value="">请选择任务</option>{visible.map(t => <option key={t.id} value={t.id}>{t.app} · {t.instruction.slice(0, 95)} · {t.id}</option>)}</Choice></Field>
    <Field label="总动作预算"><Input type="number" min={1} max={500} value={String(config.osworld?.max_actions || 120)} disabled={busy} onChange={(_, d) =>
      setConfig({...config, osworld: {task_id: config.osworld?.task_id || '', max_actions: Number(d.value)}})}/></Field>
    {selected && <p className="quiet">任务 ID：{selected.id}。任务原文固定；仅对本次新访客执行。完整轨迹、模型输入输出和评分在运行记录中展示。</p>}
  </section>;
}
