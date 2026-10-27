import {useEffect, useRef, useState} from 'react';
import {Button, Textarea} from '@fluentui/react-components';
import {api} from './api';
import {ImageAnnotator} from './ImageAnnotator';
import type {HandoffPicture, InterventionCommand, InterventionState, Run, VisualInput} from './types';

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
  const [showImage, setShowImage] = useState(false);
  const [visual, setVisual] = useState<VisualInput | null>(null);
  const retries = useRef(new Map<string, {kind: InterventionCommand['kind']; text: string; request_id: string; visual?: VisualInput}>());
  const remote = run.interventions;
  const state = reply && (!remote || reply.version > remote.version) ? reply : remote;
  // Non-interactive jobs persist an empty intervention object.
  const segment = state?.supported ? state.takeovers[state.takeovers.length - 1] : undefined;
  useEffect(() => {
    if (state?.phase === 'running' && segment?.status === 'returned') {
      setNote(''); setVisual(null); setShowImage(false);
    }
  }, [state?.phase, segment?.id, segment?.status]);
  if (!state?.supported) return null;
  const running = run.status === 'running' && !run.stop_requested;
  const ended = !['queued', 'running', 'stopping'].includes(run.status);
  const phase = ended ? 'closed' : state.phase;
  if (ended && !state.commands.length) return null;
  const editable = running && !['starting', 'closed'].includes(phase) && !pending;
  const editingImage = showImage && (phase === 'paused' || phase === 'resuming');
  function openImage() {
    const combined = [note, instruction].filter(text => text.trim()).join('\n');
    if (combined.length > 2000) {setError('两处说明合计超过 2000 字，请先缩短文字再添加截图。'); return false;}
    setNote(combined); setInstruction(''); setShowImage(true); return true;
  }
  async function send(kind: InterventionCommand['kind'], text = '', attachment?: VisualInput) {
    if (pending) return;
    const key = JSON.stringify([kind, text, attachment]);
    const previous = retries.current.get(key);
    const rejected = previous && state?.commands.some(c => c.id === previous.request_id && c.status === 'scope_rejected');
    // A lost HTTP response is not permission to repeat an intervention.
    const request = (rejected ? undefined : previous) || {kind, text, request_id: crypto.randomUUID(), ...(attachment ? {visual: attachment} : {})};
    retries.current.set(key, request);
    setPending(true); setError('');
    try {
      const value = await api<InterventionState>(`/api/jobs/${run.job_id}/interventions`, request);
      setReply(value); retries.current.delete(key);
      if (kind === 'instruction') setInstruction(current => current === text ? '' : current);
      // Keep image and note until the execution thread confirms the return;
      // a rejected app/focus check must leave the user's draft available.
    } catch (e) {setError(`${String(e)}。请核对下方记录；再次提交相同内容会复用请求标识，不会重复追加。`);}
    finally {setPending(false);}
  }
  return <section className="run-interventions" aria-label="运行中干预">
    <div className="row-between"><h3>运行中干预</h3><span className="tag">{phaseLabels[phase]}</span></div>
    <p role="status">{ended ? '仅查看历史；不会自动恢复手机操作。' : state.message}</p>
    {state.assisted_by_user && <p className="quiet">本次含人工干预，不能计为纯模型自主完成。</p>}
    {!ended && <>
      {!editingImage && <>
      <label className="intervention-label" htmlFor={`instruction-${run.job_id}`}>补充指令</label>
      <Textarea id={`instruction-${run.job_id}`} value={instruction} disabled={!editable} maxLength={2000} resize="vertical"
        placeholder="例如：不要继续消耗开拓力，优先完成支援任务。" onChange={(_, d) => setInstruction(d.value)}/>
      <div className="intervention-actions"><span className="quiet">{instruction.length} / 2000 · 下一次规划生效，剩余旧动作作废</span>
        <Button disabled={!editable || !instruction.trim()} onClick={() => void send('instruction', instruction)}>发送指令</Button>
        {phase === 'running' && state.visual_supported && <Button disabled={!editable} onClick={() => {if (openImage()) void send('pause');}}>截图并标注</Button>}
        {phase === 'running' && <Button disabled={!editable} onClick={() => void send('pause')}>我要接管</Button>}
      </div>
      </>}
      {(phase === 'paused' || phase === 'resuming') && <div className="handoff-return">
        {showImage && segment ? <ImageAnnotator key={segment.id} picture={segment.before} takeoverId={segment.id}
          disabled={!editable || phase !== 'paused'} onChange={setVisual} onRemove={() => {setShowImage(false); setVisual(null);}}/> :
          state.visual_supported && <Button disabled={!editable || phase !== 'paused'} onClick={openImage}>添加截图与标注</Button>}
        <label className="intervention-label" htmlFor={`handoff-note-${run.job_id}`}>接管说明（可选）</label>
        <Textarea id={`handoff-note-${run.job_id}`} value={note} disabled={!editable || phase !== 'paused'} maxLength={2000}
          resize="vertical" placeholder="例如：点击圈出的按钮；不要点击打叉的位置；沿画线方向移动。也可以说明你刚才做了什么。"
          onChange={(_, d) => setNote(d.value)}/>
        <div className="intervention-actions"><span className="quiet">先回到本次授权应用，再交还。</span>
          <Button appearance="primary" disabled={!editable || phase !== 'paused' || (showImage && !visual)}
            onClick={() => void send('resume', note, visual || undefined)}>{showImage ? '发送图文并继续' : '交还模型'}</Button></div>
      </div>}
      <p className="quiet">记录接管前后画面、说明及你添加的截图标注，不录制点击轨迹。内容将发送给{run.run_facts?.destination || '当前模型'}并随运行留档。
        暂停不冻结手机或游戏；原时间/动作预算与 F9 继续生效。</p>
    </>}
    {error && <p role="alert" className="inline-warning">{error}</p>}
    {!!state.commands.length && <details className="intervention-history" open>
      <summary>人工干预记录 · {state.commands.length} 条</summary>
      <ol>{state.commands.map(command => <li key={command.id}>
        <div className="row-between"><strong>{command.index}. {{instruction: '追加指令', pause: '请求接管', resume: '请求交还'}[command.kind]}</strong>
          <time>{new Date(command.requested_at).toLocaleTimeString()}</time></div>
        {command.text && <p className="intervention-text">{command.text}</p>}
        {command.visual && <details className="handoff-evidence"><summary>查看截图指导 · {command.visual.strokes.length} 个标注</summary>
          <div className="handoff-pictures"><HandoffImage picture={command.visual.source} label={`人工指令 ${command.index} · 参考原图`}/>
            <HandoffImage picture={command.visual.image} label={`人工指令 ${command.index} · 用户标注图`}/></div>
        </details>}
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
