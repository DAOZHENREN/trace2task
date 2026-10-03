import {useEffect, useRef, useState} from 'react';
import {Button, Input, Textarea} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import {Choice, Empty, Field, PageHeading, Refresh} from './ui';
import {JsonDetails, useAction} from './workflow';

type Health = {configured: boolean; ready: boolean; message?: string; models: string[]; reasoning_efforts: string[];
  checks: {label: string; ready: boolean}[]; max_model_calls?: number; max_wall_seconds?: number; max_project_budget?: number};
type RsiRun = {id: string; state: string; created: string; stop_requested: boolean; candidate_sha256?: string;
  spec: {instruction: string; model: string; max_model_calls: number; wall_seconds: number; project_budget: number};
  result?: {status?: string; reason?: string; error?: string}; review?: {decision: string; note: string}};
type Event = {seq: number; time: string; kind: string; payload: Record<string, unknown>};
type Candidate = {verification?: {verdict?: string; scope?: string; artifact?: string; evidence?: unknown[]};
  memory?: Record<string, {text?: string; sha256?: string}>; [key: string]: unknown};
type Recovery = {eligible: boolean; reason: string; remaining_model_calls?: number; remaining_wall_ms?: number};
const running = (run: RsiRun) => ['queued', 'preflight', 'running', 'finalizing'].includes(run.state);
const blocks = (run: RsiRun) => running(run) || run.state === 'needs_recovery';

export function Experiments({visible, onError}: {visible: boolean; onError: (text: string) => void}) {
  const confirm = useConfirm();
  const [health, setHealth] = useState<Health | null>(null); const [runs, setRuns] = useState<RsiRun[]>([]);
  const [selected, setSelected] = useState(''); const [instruction, setInstruction] = useState('');
  const [model, setModel] = useState(''); const [effort, setEffort] = useState('');
  const [calls, setCalls] = useState(30); const [seconds, setSeconds] = useState(900); const [projects, setProjects] = useState(1);
  const [statusError, setStatusError] = useState(''); const {pending, perform} = useAction(onError);
  async function refresh(signal?: AbortSignal, probe = false) {
    let current = health;
    if (probe || !current) {
      current = await api<Health>('/api/rsi/health', undefined, signal); setHealth(current);
      const next = current;
      setModel(value => next.models.includes(value) ? value : next.models[0] || '');
      setEffort(value => next.reasoning_efforts.includes(value) ? value : next.reasoning_efforts[0] || '');
      setCalls(value => Math.min(value, next.max_model_calls ?? value)); setSeconds(value => Math.min(value, next.max_wall_seconds ?? value)); setProjects(value => Math.min(value, next.max_project_budget ?? value));
    }
    if (current.configured) setRuns((await api<{runs: RsiRun[]}>('/api/rsi/runs?limit=50', undefined, signal)).runs);
    else setRuns([]);
    setStatusError('');
  }
  useEffect(() => {
    if (!visible) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll(first = false) {try {await refresh(abort.signal, first && !health);} catch (e) {if (!abort.signal.aborted) setStatusError(String(e));}
      if (!abort.signal.aborted) timer = setTimeout(() => void poll(), 5000);}
    void poll(true); return () => {abort.abort(); clearTimeout(timer);};
  }, [visible, health?.configured]);
  const disabled = !!pending || !health?.ready || runs.some(blocks) || !!statusError;
  async function start() {
    if (!await confirm({title: '启动远程有界练习？', body: `${instruction}\n最多 ${calls} 次模型调用、${seconds} 秒、${projects} 个项目。`, confirmLabel: '启动练习', note: '在远程隔离 VM 中运行，不会操作本机或自动修改当前经验。'})) return;
    await perform('start', async () => {
      const result = await api<{run: RsiRun}>('/api/rsi/start', {instruction, model, reasoning_effort: effort, max_model_calls: calls, wall_seconds: seconds, project_budget: projects});
      setSelected(result.run.id); await refresh(undefined, true);
    });
  }
  async function action(name: string, run: RsiRun, body = {}) {
    const notice = name === 'stop' ? '请求停止此远程练习？只有远端资源清理完成并记录为 cancelled，才表示真正停止。'
      : name === 'recover' ? '从服务器验证的完成项目边界恢复？未完成操作不会重放，模型调用和时间继续累计。'
      : '写入不可变的候选审查记录？接受仅归档，不会自动修改当前 Trace 或生效经验。';
    if (!await confirm({title: name === 'stop' ? '停止远程练习？' : name === 'recover' ? '恢复远程练习？' : '归档候选审查？', body: notice, confirmLabel: name === 'stop' ? '请求停止' : name === 'recover' ? '确认恢复' : '确认归档'})) return;
    await perform(name, async () => {await api(`/api/rsi/${name}`, {run_id: run.id, ...body}); await refresh(undefined, name === 'recover');});
  }
  const run = runs.find(item => item.id === selected);
  return <>
    <PageHeading eyebrow="EXPERIMENTS" title="给新能力一个独立的试验区。" copy="远程 RSI 练习独立于日常执行；候选必须经过验证和人工审查。"
      action={<Refresh busy={!!pending} onClick={() => void perform('refresh', () => refresh(undefined, true))}/>}/>
    <section className="surface workflow-card"><div className="row-between"><h2>有界自主练习</h2><span className="status-tag">{health?.ready && !statusError ? '远端就绪' : '尚未就绪'}</span></div>
      <p className="notice" role="status">{statusError || health?.message || '正在读取远端准备状态…'}</p>
      {health?.checks.filter(c => !c.ready).map(c => <p className="inline-warning" key={c.label}>{c.label}</p>)}
      <Field label="练习方向"><Textarea rows={3} resize="vertical" value={instruction} maxLength={2000} disabled={disabled} onChange={(_, d) => setInstruction(d.value)}/></Field>
      <div className="two-fields spaced"><Field label="RSI 模型"><Choice value={model} disabled={disabled} onChange={e => setModel(e.target.value)}>{health?.models.map(m => <option key={m}>{m}</option>)}</Choice></Field>
        <Field label="RSI 思考强度"><Choice value={effort} disabled={disabled} onChange={e => setEffort(e.target.value)}>{health?.reasoning_efforts.map(e => <option key={e}>{e}</option>)}</Choice></Field></div>
      <div className="three-fields"><Field label="模型调用上限"><Input type="number" min={1} max={health?.max_model_calls} value={String(calls)} disabled={disabled} onChange={(_, d) => setCalls(Number(d.value))}/></Field>
        <Field label="最长时间 / 秒"><Input type="number" min={1} max={health?.max_wall_seconds} value={String(seconds)} disabled={disabled} onChange={(_, d) => setSeconds(Number(d.value))}/></Field>
        <Field label="项目预算"><Input type="number" min={1} max={health?.max_project_budget} value={String(projects)} disabled={disabled} onChange={(_, d) => setProjects(Number(d.value))}/></Field></div>
      <Button appearance="primary" disabled={disabled || !instruction.trim() || !model || !effort || [calls, seconds, projects].some(n => !Number.isInteger(n) || n < 1)} onClick={() => void start()}>开始隔离练习</Button></section>
    <div className="research-workspace"><section className="surface research-runs"><div className="panel-heading"><h2>持久练习记录</h2></div>
      {runs.map(item => <button className={`history-row ${selected === item.id ? 'selected' : ''}`} key={item.id} onClick={() => setSelected(item.id)}><div><h3>{item.spec.instruction}</h3><p>{item.state} · {item.spec.model}</p></div></button>)}
      {!runs.length && <Empty icon="experiments" title="尚无练习记录" text="连接和启动都需要明确配置；不会在后台自动开始练习。"/>}</section>
      {run && <RsiDetail key={`${run.id}:${run.candidate_sha256 || ''}`} run={run} visible={visible} pending={!!pending} onError={onError} action={action}/>}</div>
  </>;
}

function RsiDetail({run, visible, pending, onError, action}: {run: RsiRun; visible: boolean; pending: boolean; onError: (text: string) => void;
  action: (name: string, run: RsiRun, body?: Record<string, unknown>) => Promise<void>}) {
  const [events, setEvents] = useState<Event[]>([]); const cursor = useRef(0);
  const [recovery, setRecovery] = useState<Recovery | null>(null); const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [note, setNote] = useState(''); const [loading, setLoading] = useState(false);
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {
    if (!visible) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<{events: Event[]}>(`/api/rsi/events?run_id=${encodeURIComponent(run.id)}&after=${cursor.current}`, undefined, abort.signal);
        const added = next.events.filter(e => e.seq > cursor.current);
        if (added.length) {cursor.current = Math.max(...added.map(e => e.seq)); setEvents(previous => [...previous, ...added]);}
        if (run.state === 'needs_recovery') setRecovery((await api<{recovery: Recovery}>(`/api/rsi/recovery?run_id=${encodeURIComponent(run.id)}`, undefined, abort.signal)).recovery);
      } catch (e) {if (!abort.signal.aborted) onError(String(e));}
      if (!abort.signal.aborted && running(run)) timer = setTimeout(poll, 5000);
    }
    void poll(); return () => {abort.abort(); clearTimeout(timer);};
  }, [run.id, run.state, visible]);
  async function readCandidate() {
    setLoading(true);
    try {const data = await api<{candidate: Candidate}>(`/api/rsi/candidate?run_id=${encodeURIComponent(run.id)}&digest=${encodeURIComponent(run.candidate_sha256!)}`);
      if (alive.current) setCandidate(data.candidate);
    } catch (e) {if (alive.current) onError(String(e));} finally {if (alive.current) setLoading(false);}
  }
  const last = <T,>(key: string) => [...events].reverse().find(e => e.payload[key] != null)?.payload[key] as T | undefined;
  return <section className="surface workflow-card research-detail"><h2>{run.spec.instruction}</h2><p className="quiet mono">{run.id} · {run.state}</p>
    <dl className="facts-list"><dt>调用</dt><dd>{last<number>('model_calls_used') ?? '—'} / {run.spec.max_model_calls}</dd>
      <dt>累计时间</dt><dd>{last<number>('active_elapsed_ms') != null ? `${(last<number>('active_elapsed_ms')! / 1000).toFixed(1)} s` : '尚未记录'}</dd>
      <dt>资源清理</dt><dd>{last<boolean>('cleanup_confirmed') === true ? '已确认' : '未取得确认'}</dd></dl>
    <div className="button-row"><Button disabled={pending || !blocks(run) || run.stop_requested} onClick={() => void action('stop', run)}>{run.stop_requested ? '已请求停止，等待清理' : '请求停止练习'}</Button>
      {run.state === 'needs_recovery' && <Button disabled={pending || !recovery?.eligible} onClick={() => void action('recover', run)}>从已验证边界恢复</Button>}</div>
    {recovery && <p className="notice">恢复检查：{recovery.eligible ? '允许' : '拒绝'} · {recovery.reason} · 剩余调用 {recovery.remaining_model_calls ?? '—'}</p>}
    {run.result && <JsonDetails label="练习结果 / 失败原因" value={run.result} open/>}
    {JSON.stringify(run.result || {}).includes('codex_authentication_required') && <p className="inline-warning">远端 Codex 授权失效；没有自动重试。请在远端重新登录后刷新检查。</p>}
    <details className="data-disclosure"><summary>增量事件 · {events.length}</summary><div className="log-scroll">{events.map(event => <pre key={event.seq}>{event.time} · {event.kind}{'\n'}{JSON.stringify(event.payload)}</pre>)}</div></details>
    {run.candidate_sha256 && <div className="revision-block"><h3>候选经验 · 尚未自动生效</h3><p className="quiet mono">SHA-256 {run.candidate_sha256}</p>
      <Button disabled={loading} onClick={() => void readCandidate()}>读取候选与验证证据</Button>
      {candidate && <><p>验证结论：{candidate.verification?.verdict || '未提供'} · {candidate.verification?.scope}</p>
        {Object.entries(candidate.memory || {}).map(([name, value]) => <section className="memory-file" key={name}><h4>{name}</h4><pre>{value.text || '未提供文本'}</pre><p className="quiet mono">{value.sha256}</p></section>)}
        <JsonDetails label="完整候选与验证来源" value={candidate}/>
        {run.review ? <p className="notice">已归档：{run.review.decision} · {run.review.note}</p> : <><Field label="候选审查意见"><Textarea rows={3} maxLength={4000} value={note} onChange={(_, d) => setNote(d.value)}/></Field>
          <div className="button-row spaced">{['accepted', 'rejected'].map(decision => <Button key={decision} disabled={pending} onClick={() => void action('review', run, {digest: run.candidate_sha256, decision, note})}>{decision === 'accepted' ? '归档为接受' : '归档为拒绝'}</Button>)}</div></>}</>}
    </div>}
  </section>;
}
