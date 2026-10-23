import {memo, useEffect, useState} from 'react';
import {Button, Spinner} from '@fluentui/react-components';
import {api} from './api';
import {active, labels} from './types';
import type {AppState, Round, Run, RunEvent} from './types';
import {Empty, Icon} from './ui';
import {ModelInput} from './ModelInput';
import type {RawRound} from './modelInputData';
import {ProcedureImprovement} from './ProcedureImprovement';
import {RunExperience} from './RunExperience';
import {RunInterventions} from './RunInterventions';

function useRun(id: string | null) {
  const [run, setRun] = useState<Run | null>(null);
  const [rounds, setRounds] = useState<Record<number, Round>>({});
  const [logs, setLogs] = useState<{seq: number; message: string}[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    setRun(null); setRounds({}); setLogs([]); setError('');
    if (!id) return;
    const abort = new AbortController(); let cursor = 0; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      let delay = 1000;
      try {
        const data = await api<{session: Run; events: RunEvent[]; cursor: number; has_more: boolean}>(
          `/api/workbench/runs/${id}?after=${cursor}`, undefined, abort.signal);
        cursor = data.cursor; setRun(data.session); setError('');
        const changed = data.events.filter(e => e.name === 'model.round');
        if (changed.length) setRounds(previous => {const next = {...previous}; changed.forEach(e => {next[e.attributes.step_index] = e.attributes;}); return next;});
        const added = data.events.filter(e => e.name === 'run.log').map(e => ({seq: e.seq, message: e.attributes.message || ''}));
        if (added.length) setLogs(previous => [...previous, ...added]);
        if (data.has_more) delay = 30;
        else if (!active(data.session)) return;
      } catch (e) {if (abort.signal.aborted) return; setError(String(e)); delay = 3000;}
      if (!abort.signal.aborted) timer = setTimeout(poll, delay);
    }
    void poll(); return () => {abort.abort(); clearTimeout(timer);};
  }, [id]);
  return {run, rounds: Object.values(rounds).sort((a, b) => a.step_index - b.step_index), logs, error};
}

function RoundObservation({path, step}: {path: string; step: number}) {
  const [failed, setFailed] = useState(false);
  const url = `/api/local-image?path=${encodeURIComponent(path)}`;
  return <figure className="round-observation">
    <figcaption><strong>第 {step + 1} 轮 · 观察画面</strong><a href={url} target="_blank" rel="noreferrer">打开原图</a></figcaption>
    {failed ? <p className="quiet" role="status">画面加载失败，原运行记录仍保留；可尝试打开原图。</p>
      : <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={`第 ${step + 1} 轮归档观察截图`}
        onError={() => setFailed(true)}/></a>}
  </figure>;
}

const RoundCard = memo(function RoundCard({round, provider}: {round: Round; provider?: string}) {
  const [open, setOpen] = useState(false);
  const [imageOpen, setImageOpen] = useState(false);
  const [raw, setRaw] = useState<RawRound | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    setError('');
    api<RawRound>(round.artifact_ref, undefined, abort.signal).then(setRaw).catch(e => {if (!abort.signal.aborted) setError(String(e));});
    return () => abort.abort();
  }, [open, round]);
  const status = round.status === 'pending' ? round.purpose === 'memory' ? 'Agent 选择经验中' : round.purpose === 'compile' ? 'Agent 编译中' : '观察与规划中'
    : round.status === 'memory_requested' ? '经验工具请求已返回 · 未发送操作输入'
    : round.status === 'compiled' ? 'Procedure 已返回 · 未执行任务' : round.status === 'predicted' ? '已生成动作' : round.status;
  return <article className="round-card">
    <div className="round-number">{String(round.step_index + 1).padStart(2, '0')}</div>
    <div className="round-content"><div className="row-between"><h3>{round.purpose === 'memory' ? 'Agent 经验检索 / 按需加载' : round.purpose === 'improve' ? '固定改进器：执行反馈 → 候选方法' : round.purpose === 'compile' ? 'Agent 编译：历史证据 → Procedure' : ['review', 'verification', 'verify_completion'].includes(round.purpose || '') ? '完成复核' : '观察 → 规划 → 执行'}</h3>
      <span className="quiet mono">{round.duration_ms != null ? `${(round.duration_ms / 1000).toFixed(1)} s` : '等待回包'}</span></div>
      <div className="round-status"><span className={`status-dot ${round.status === 'pending' ? 'pulsing' : ''}`}/>{status}
        {round.executed !== undefined && <span className="receipt">{round.executed ? '操作输入已交付 · 效果待核对' : '未发送操作输入'}</span>}
        {round.execution_status && <span className="quiet">{round.execution_status}</span>}</div>
      {round.error && <p className="inline-warning">{round.error}</p>}
      {round.execution_status === 'intervened' && <p className="inline-warning">人工干预使本轮剩余计划或核验结论作废；已交付动作以回执为准。</p>}
      {round.verification && <p className="verification"><strong>模型视觉复核：{round.verification.verdict}</strong> · {round.verification.evidence} {round.verification.missing}</p>}
      <div className="round-actions">{round.screenshot && <Button size="small" appearance="subtle" icon={<Icon name="screen" size={16}/>}
        aria-expanded={imageOpen} onClick={() => setImageOpen(value => !value)}>{imageOpen ? '收起该轮画面' : '查看该轮画面'}</Button>}
        {round.tokens?.input_tokens != null && <span className="quiet mono">IN {round.tokens.input_tokens.toLocaleString()} · OUT {round.tokens.output_tokens ?? '—'}</span>}</div>
      {imageOpen && round.screenshot && <RoundObservation key={round.screenshot} path={round.screenshot} step={round.step_index}/>}
      <details open={open} onToggle={e => setOpen(e.currentTarget.open)}><summary>模型输入、输出与执行回执</summary>
        {error ? <p role="alert">{error}</p> : !raw ? <Spinner size="tiny" label="读取该轮记录"/> : <div className="raw-round">
          <ModelInput raw={raw} provider={provider}/>
          <h4>模型输出</h4><pre>{raw.raw_output || JSON.stringify(raw.prediction || raw.verification || raw.error || '等待模型返回', null, 2)}</pre>
          <h4>执行回执</h4><pre>{JSON.stringify(raw.execution || '尚无执行回执；模型输出不代表已执行', null, 2)}</pre>
          <details><summary>原始请求 / 完整记录（可能含敏感内容）</summary><pre>{JSON.stringify(raw, null, 2)}</pre></details>
        </div>}
      </details>
    </div>
  </article>;
});

export function RunInspector({id, onLibrary, state, busy = false, onRun, onError}: {id: string | null;
  onLibrary?: (traceId: string) => void; state?: AppState; busy?: boolean;
  onRun?: (run: Run) => void; onError?: (text: string) => void}) {
  const {run, rounds, logs, error} = useRun(id);
  if (!id) return <section className="idle-stage"><Icon name="history" size={20}/><div className="stage-copy">
    <h2>运行结果将在这里呈现</h2><p>截图、模型判断与执行回执按步骤记录；模型自报完成不等于核验成功。</p></div></section>;
  return <section className="run-workspace">
    <div className="surface timeline"><div className="panel-heading"><div><div className="section-label">运行时间线</div><h2>{run?.instruction || '正在读取运行…'}</h2></div>
      <span className={`status-tag ${active(run) ? 'live' : ''}`}>{labels[run?.status || ''] || run?.status || '载入'}</span></div>
      {run && <RunInterventions key={`interventions:${run.job_id}`} run={run}/>}
      {run && state && onRun && <RunExperience key={`experience:${run.job_id}`} run={run} state={state} busy={busy}
        onRun={onRun} onLibrary={onLibrary}/>}
      {error && <p className="error-inline" role="alert">{error} · 正在重试，不会重复启动任务。</p>}
      {run?.error && <p className="error-inline" role="alert">{run.error}</p>}
      {run?.result?.stop_reason && <p className="notice">停止原因：{run.result.stop_reason === 'action_limit' ? '动作预算耗尽，任务未完成' : run.result.stop_reason === 'time_limit' ? '时间预算耗尽，任务未完成' : run.result.stop_reason}</p>}
      {run?.result?.compiled_trace_id && <div className="notice"><p>{run.result.representation === 'R' ? '改进候选 R 已生成，尚未获准试用或证明效果提升。' : 'Agent 方法 B 已生成独立版本，未自动选为任务经验。'}</p>
        {onLibrary && <Button onClick={() => onLibrary(run.result!.compiled_trace_id!)}>{run.result.representation === 'R' ? '到经验库审查改进候选' : '到经验库查看 Procedure B'}</Button>}</div>}
      {run && state && onRun && onError && <ProcedureImprovement key={`improvement:${run.job_id}`} run={run} state={state} busy={busy} onRun={onRun} onError={onError}/>}
      {(run?.memory_selection || run?.result?.memory_selection) && <MemoryRecord selection={(run.memory_selection || run.result?.memory_selection)!} onLibrary={onLibrary}/>}
      {run?.run_facts && <details className="run-facts"><summary>本次运行事实 · {run.run_facts.model} · {run.run_facts.execution_backend}</summary><dl className="facts-list">
        <dt>模型</dt><dd>{run.run_facts.model}</dd><dt>所选引擎</dt><dd>{run.run_facts.inference_backend}</dd>
        <dt>回包证实</dt><dd>{run.observed_inference?.backend || '尚未取得引擎回执'}</dd>
        <dt>执行后端</dt><dd>{run.run_facts.execution_backend}</dd><dt>授权范围</dt><dd>{run.run_facts.operation_scope === 'none' ? '不执行操作' : run.run_facts.operation_scope === 'browser' ? 'Ancient Beast · 独立浏览器' : run.run_facts.operation_scope === 'desktop' ? '整个主显示器' : '仅指定窗口 / 应用'}</dd>
        <dt>数据去向</dt><dd>{run.run_facts.destination}</dd><dt>经验</dt><dd>{run.run_facts.memory_mode === 'auto' ? 'Agent 按需选择 · 实际加载见经验记录' : run.run_facts.experience_path ? '启动时固定的经验版本' : '未使用'}</dd></dl>
        <p className="quiet">{run.run_facts.context_description}</p><div className="run-id mono">RUN {run.job_id.slice(0, 12)}</div></details>}
      {rounds.length ? rounds.map(r => <RoundCard key={`${id}:${r.step_index}`} round={r} provider={run?.provider}/>)
        : <Empty title={active(run) ? '正在准备运行记录' : '此运行没有逐轮模型记录'} text={active(run) ? '过程会在这里更新，你可以随时停止后续动作。' : '可展开运行日志；录制、编译和修订结果也可在经验库查看。'}/>}
      <details className="log-disclosure"><summary>运行日志 · {logs.length} 条</summary><div className="log-scroll" tabIndex={0}>
        {logs.map(l => <pre key={l.seq}>{l.message}</pre>)}</div><p className="quiet">增量加载；不会强制滚动或折叠你正在阅读的内容。</p></details>
    </div>
  </section>;
}

function MemoryRecord({selection, onLibrary}: {selection: NonNullable<Run['memory_selection']>; onLibrary?: (id: string) => void}) {
  if (selection.mode !== 'auto') return null;
  return <div className="notice"><strong>自动经验 · {selection.status === 'loaded' ? '已加载方法 B' : selection.status === 'skipped' ? '未加载' : selection.status === 'error' ? '选择中止' : '选择中'}</strong>
    <p>{selection.selected?.name}{selection.selected ? ' · ' : ''}{selection.reason || selection.error || `可用方法 ${selection.candidate_count} 份`}</p>
    {selection.selected && onLibrary && <Button size="small" onClick={() => onLibrary(selection.selected!.id)}>查看选中的经验版本</Button>}
    <details><summary>经验选择记录</summary><pre>{JSON.stringify(selection, null, 2)}</pre></details>
  </div>;
}
