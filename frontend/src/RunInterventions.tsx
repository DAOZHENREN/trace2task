import {useRef, useState} from 'react';
import {Button, Textarea} from '@fluentui/react-components';
import {api} from './api';
import type {HandoffPicture, InterventionCommand, InterventionState, Run} from './types';

const phaseLabels: Record<InterventionState['phase'], string> = {starting: '准备中', running: '模型运行中',
  pausing: '正在交接 · 暂勿操作手机', paused: '已暂停 · 可以手动操作', resuming: '正在交还 · 请停止手动操作', closed: '运行已结束'};

function commandStatus(command: InterventionCommand, ended: boolean) {
  if (command.status === 'responded') return `已收到第 ${(command.responded_round ?? 0) + 1} 轮回复，不代表已经遵从`;
  if (command.status === 'submitted') return `已加入第 ${(command.submitted_round ?? 0) + 1} 轮请求`;
  if (command.status === 'paused') return '已确认停止自动输入';
  if (command.status === 'scope_rejected') return '交还未完成：授权应用检查未通过';
  return ended ? '运行已结束，未加入模型请求' : '已收到，等待安全检查点 / 下一轮请求';
}

function HandoffImage({picture, label}: {picture: HandoffPicture | null; label: string}) {
  const [failed, setFailed] = useState(false);
  if (!picture || failed) return <div className="handoff-image-missing">{label}：没有可用截图，不推断缺失画面。</div>;
  const url = `/api/local-image?path=${encodeURIComponent(picture.path)}`;
  return <figure><figcaption>{label}</figcaption><a href={url} target="_blank" rel="noreferrer">
    <img src={url} alt={label} loading="lazy" onError={() => setFailed(true)}/></a></figure>;
}

/** Mounted with a run-ID key so drafts and retry IDs never cross task boundaries. */
export function RunInterventions({run}: {run: Run}) {
  const [instruction, setInstruction] = useState('');
  const [note, setNote] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [reply, setReply] = useState<InterventionState | null>(null);
  const retries = useRef(new Map<string, {kind: InterventionCommand['kind']; text: string; request_id: string}>());
  const remote = run.interventions;
  const state = reply && (!remote || reply.version > remote.version) ? reply : remote;
  if (!state?.supported) return null;
  const running = run.status === 'running' && !run.stop_requested;
  const ended = !['queued', 'running', 'stopping'].includes(run.status);
  const phase = ended ? 'closed' : state.phase;
  if (ended && !state.commands.length) return null;
  const editable = running && !['starting', 'closed'].includes(phase) && !pending;
  async function send(kind: InterventionCommand['kind'], text = '') {
    if (pending) return;
    const key = JSON.stringify([kind, text]);
    const previous = retries.current.get(key);
    // A lost HTTP response is not permission to repeat an intervention.
    const request = previous || {kind, text, request_id: crypto.randomUUID()};
    retries.current.set(key, request);
    setPending(true); setError('');
    try {
      const value = await api<InterventionState>(`/api/jobs/${run.job_id}/interventions`, request);
      setReply(value); retries.current.delete(key);
      if (kind === 'instruction') setInstruction(current => current === text ? '' : current);
      if (kind === 'resume') setNote(current => current === text ? '' : current);
    } catch (e) {setError(`${String(e)}。请核对下方记录；再次提交相同内容会复用请求标识，不会重复追加。`);}
    finally {setPending(false);}
  }
  return <section className="run-interventions" aria-label="运行中干预">
    <div className="row-between"><h3>运行中干预</h3><span className="tag">{phaseLabels[phase]}</span></div>
    <p role="status">{ended ? '仅查看历史；不会自动恢复手机操作。' : state.message}</p>
    {state.assisted_by_user && <p className="quiet">本次含人工干预，不能计为纯模型自主完成。</p>}
    {!ended && <>
      <label className="intervention-label" htmlFor={`instruction-${run.job_id}`}>补充指令</label>
      <Textarea id={`instruction-${run.job_id}`} value={instruction} disabled={!editable} maxLength={2000} resize="vertical"
        placeholder="例如：不要继续消耗开拓力，优先完成支援任务。" onChange={(_, d) => setInstruction(d.value)}/>
      <div className="intervention-actions"><span className="quiet">{instruction.length} / 2000 · 下一次规划生效，剩余旧动作作废</span>
        <Button disabled={!editable || !instruction.trim()} onClick={() => void send('instruction', instruction)}>发送指令</Button>
        {phase === 'running' && <Button disabled={!editable} onClick={() => void send('pause')}>我要接管</Button>}
      </div>
      {(phase === 'paused' || phase === 'resuming') && <div className="handoff-return">
        <label className="intervention-label" htmlFor={`handoff-note-${run.job_id}`}>接管说明（可选）</label>
        <Textarea id={`handoff-note-${run.job_id}`} value={note} disabled={!editable || phase !== 'paused'} maxLength={2000}
          resize="vertical" placeholder="说明你做了什么、希望模型接下来做什么；此说明不是自动验证结果。"
          onChange={(_, d) => setNote(d.value)}/>
        <div className="intervention-actions"><span className="quiet">先回到本次授权应用，再交还。</span>
          <Button appearance="primary" disabled={!editable || phase !== 'paused'} onClick={() => void send('resume', note)}>交还模型</Button></div>
      </div>}
      <p className="quiet">仅记录接管前后画面与说明，不录制点击轨迹。内容将发送给{run.run_facts?.destination || '当前模型'}并随运行留档。
        暂停不冻结手机或游戏；原时间/动作预算与 F9 继续生效。</p>
    </>}
    {error && <p role="alert" className="inline-warning">{error}</p>}
    {!!state.commands.length && <details className="intervention-history" open>
      <summary>人工干预记录 · {state.commands.length} 条</summary>
      <ol>{state.commands.map(command => <li key={command.id}>
        <div className="row-between"><strong>{command.index}. {{instruction: '追加指令', pause: '请求接管', resume: '请求交还'}[command.kind]}</strong>
          <time>{new Date(command.requested_at).toLocaleTimeString()}</time></div>
        {command.text && <p className="intervention-text">{command.text}</p>}
        <p className="quiet">{commandStatus(command, ended)}</p>
      </li>)}</ol>
      {state.takeovers.map((segment, i) => <details className="handoff-evidence" key={segment.id}>
        <summary>人工接管 {i + 1} · {segment.status === 'returned' ? '已交还' : segment.status === 'not_returned' || ended ? '未完成交还' : '交接中'} · 查看前后画面</summary>
        {segment.note && <p className="intervention-text">用户说明：{segment.note}</p>}
        {(segment.before_error || segment.return_error) && <p className="quiet">{segment.before_error || segment.return_error}</p>}
        <div className="handoff-pictures"><HandoffImage key={segment.before?.path || 'missing-before'} picture={segment.before} label={`人工接管 ${i + 1} · 接管前`}/>
          <HandoffImage key={segment.after?.path || 'missing-after'} picture={segment.after} label={`人工接管 ${i + 1} · 交还后`}/></div>
      </details>)}
    </details>}
  </section>;
}
