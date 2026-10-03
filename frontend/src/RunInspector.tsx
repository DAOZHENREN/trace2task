import {memo, useEffect, useState} from 'react';
import {Button, Spinner} from '@fluentui/react-components';
import {api} from './api';
import {active, labels} from './types';
import type {Round, Run, RunEvent} from './types';
import {Empty, Icon} from './ui';

type RawRound = {raw_output?: string; prediction?: unknown; execution?: unknown; verification?: unknown;
  input?: unknown; prompt?: unknown; input_messages?: unknown; error?: string};

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

const RoundCard = memo(function RoundCard({round, onImage}: {round: Round; onImage: (source: string) => void}) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState<RawRound | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    api<RawRound>(round.artifact_ref, undefined, abort.signal).then(setRaw).catch(e => {if (!abort.signal.aborted) setError(String(e));});
    return () => abort.abort();
  }, [open, round]);
  const status = round.status === 'pending' ? '观察与规划中' : round.status === 'predicted' ? '已生成动作' : round.status;
  return <article className="round-card">
    <div className="round-number">{String(round.step_index + 1).padStart(2, '0')}</div>
    <div className="round-content"><div className="row-between"><h3>{round.purpose === 'review' || round.purpose === 'verification' ? '完成复核' : '观察 → 规划 → 执行'}</h3>
      <span className="quiet mono">{round.duration_ms != null ? `${(round.duration_ms / 1000).toFixed(1)} s` : '等待回包'}</span></div>
      <div className="round-status"><span className={`status-dot ${round.status === 'pending' ? 'pulsing' : ''}`}/>{status}
        {round.executed !== undefined && <span className="receipt">{round.executed ? '输入已交付 · 效果待核对' : '未发送输入'}</span>}
        {round.execution_status && <span className="quiet">{round.execution_status}</span>}</div>
      {round.error && <p className="inline-warning">{round.error}</p>}
      {round.verification && <p className="verification"><strong>独立复核：{round.verification.verdict}</strong> · {round.verification.evidence} {round.verification.missing}</p>}
      <div className="round-actions">{round.screenshot && <Button size="small" appearance="subtle" icon={<Icon name="screen" size={16}/>}
        onClick={() => onImage(round.screenshot!)}>查看该轮画面</Button>}
        {round.tokens?.input_tokens != null && <span className="quiet mono">IN {round.tokens.input_tokens.toLocaleString()} · OUT {round.tokens.output_tokens ?? '—'}</span>}</div>
      <details open={open} onToggle={e => setOpen(e.currentTarget.open)}><summary>模型回答与执行回执</summary>
        {error ? <p role="alert">{error}</p> : !raw ? <Spinner size="tiny" label="读取该轮记录"/> : <div className="raw-round">
          <h4>模型输出</h4><pre>{raw.raw_output || JSON.stringify(raw.prediction || raw.verification || raw.error || '等待模型返回', null, 2)}</pre>
          <h4>执行回执</h4><pre>{JSON.stringify(raw.execution || '尚无执行回执；模型输出不代表已执行', null, 2)}</pre>
          <details><summary>原始请求 / 完整记录（可能含敏感内容）</summary><pre>{JSON.stringify(raw, null, 2)}</pre></details>
        </div>}
      </details>
    </div>
  </article>;
});

export function RunInspector({id}: {id: string | null}) {
  const {run, rounds, logs, error} = useRun(id);
  const [selectedImage, setSelectedImage] = useState('');
  useEffect(() => setSelectedImage(''), [id]);
  const latestImage = selectedImage || [...rounds].reverse().find(r => r.screenshot)?.screenshot;
  if (!id) return <section className="surface idle-stage"><div className="stage-copy"><div className="eyebrow">从示范，到行动</div>
    <h2>让每一次操作，都有迹可循。</h2><p>任务开始后，这里会逐轮呈现观察、模型判断和执行回执。模型自报完成，与实际结果分开记录。</p></div>
    <div className="trace-motif" aria-hidden="true"><div><Icon name="record"/><span>示范</span></div><i/><div><Icon name="library"/><span>经验</span></div><i/><div className="motif-end"><Icon name="tasks"/><span>行动</span></div></div></section>;
  return <section className="run-workspace">
    <div className="surface timeline"><div className="panel-heading"><div><div className="section-label">运行时间线</div><h2>{run?.instruction || '正在读取运行…'}</h2></div>
      <span className={`status-tag ${active(run) ? 'live' : ''}`}>{labels[run?.status || ''] || run?.status || '载入'}</span></div>
      {error && <p className="error-inline" role="alert">{error} · 正在重试，不会重复启动任务。</p>}
      {run?.error && <p className="error-inline" role="alert">{run.error}</p>}
      {run?.result?.stop_reason && <p className="notice">停止原因：{run.result.stop_reason}</p>}
      {rounds.length ? rounds.map(r => <RoundCard key={`${id}:${r.step_index}`} round={r} onImage={setSelectedImage}/>)
        : <Empty title={active(run) ? '正在准备运行记录' : '此运行没有逐轮模型记录'} text={active(run) ? '过程会在这里更新，你可以随时停止后续动作。' : '可展开运行日志；录制、编译和修订结果也可在经验库查看。'}/>}
      <details className="log-disclosure"><summary>运行日志 · {logs.length} 条</summary><div className="log-scroll" tabIndex={0}>
        {logs.map(l => <pre key={l.seq}>{l.message}</pre>)}</div><p className="quiet">增量加载；不会强制滚动或折叠你正在阅读的内容。</p></details>
    </div>
    <aside className="run-aside"><div className="surface observation"><div className="panel-heading"><h3>观察画面</h3><span className="quiet">{selectedImage ? '已选轮次' : '最近一轮'}</span></div>
      {latestImage ? <><a href={`/api/local-image?path=${encodeURIComponent(latestImage)}`} target="_blank" rel="noreferrer"><img alt="模型该轮实际观察截图"
        src={`/api/local-image?path=${encodeURIComponent(latestImage)}`}/></a>{selectedImage && <Button appearance="subtle" size="small" onClick={() => setSelectedImage('')}>回到最近一轮</Button>}</>
        : <div className="observation-placeholder"><Icon name="screen" size={38}/><span>尚无观察截图</span></div>}</div>
      {run?.run_facts && <div className="surface facts-card"><span className="section-label">本次运行事实</span><dl className="facts-list">
        <dt>模型</dt><dd>{run.run_facts.model}</dd><dt>所选引擎</dt><dd>{run.run_facts.inference_backend}</dd>
        <dt>回包证实</dt><dd>{run.observed_inference?.backend || '尚未取得引擎回执'}</dd>
        <dt>执行后端</dt><dd>{run.run_facts.execution_backend}</dd><dt>授权范围</dt><dd>{run.run_facts.operation_scope === 'desktop' ? '整个主显示器' : '仅指定窗口 / 应用'}</dd>
        <dt>数据去向</dt><dd>{run.run_facts.destination}</dd><dt>经验</dt><dd>{run.run_facts.experience_path ? '启动时固定的经验版本' : '未使用'}</dd></dl>
        <p className="quiet">{run.run_facts.context_description}</p><div className="run-id mono">RUN {run.job_id.slice(0, 12)}</div></div>}
    </aside>
  </section>;
}
