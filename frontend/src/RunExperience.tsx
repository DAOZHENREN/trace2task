import {useEffect, useState} from 'react';
import {Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Spinner} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import {active} from './types';
import type {AppState, CompilationConfirmation, ExperienceLineage, ModelChoice, Run} from './types';
import {Icon} from './ui';
import {CompilerChoice, useAction} from './workflow';

type Summary = {run_id: string; instruction: string; status: string; outcome: string; stop_reason: string;
  scope: string; package: string; model: string; round_count: number; delivered_count: number; image_count: number;
  assisted_by_user?: boolean; execution_condition?: string; intervention_count?: number; takeover_count?: number;
  derivation?: string; parent_experience?: {id: string; name: string; representation: string; sha256: string; selection_mode: string} | null};
type Picture = {id: string; file: string; step_index?: number; label?: string; preview_path?: string};
type Preview = Summary & {source_sha256: string; input: string; images: Picture[];
  lineage?: ExperienceLineage | null; parent_content?: string;
  image_selection?: {rule: string};
  drafts: {draft_id: string; trace_name: string; created_at: string; saved_trace_id: string | null}[]};
type Draft = {draft_id: string; draft_sha256: string; content: string; trace_name: string; description: string;
  lineage?: ExperienceLineage | null; parent_content?: string; revision_diff?: string; revision_changed?: boolean | null;
  saved_trace_id: string | null; summary: Summary; compiler_images: Picture[]};
const scopeLabel = (scope: string) => ({android: 'Android · 指定应用', desktop: '桌面', selected_windows: '指定窗口', browser: '独立浏览器'}[scope] || scope);

/** Plain-text Markdown subset; no HTML, scripts or model-controlled links. */
function MethodText({content}: {content: string}) {
  return <div className="method-prose">{content.split('\n').map((line, i) => {
    if (line.startsWith('# ')) return <h2 key={i}>{line.slice(2)}</h2>;
    if (line.startsWith('## ')) return <h3 key={i}>{line.slice(3)}</h3>;
    const step = /^(\d+)[.)]\s+(.+)$/.exec(line);
    if (step) return <div className="method-step" key={i}><span>{step[1].padStart(2, '0')}</span><p>{step[2]}</p></div>;
    return line.trim() ? <p key={i}>{line.replace(/^[-*]\s+/, '• ')}</p> : null;
  })}</div>;
}

export function RunExperience({run, state, busy, onRun, onLibrary}: {
  run: Run; state: AppState; busy: boolean; onRun: (run: Run) => void;
  onLibrary?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [error, setError] = useState('');
  const [model, setModel] = useState<ModelChoice>(state.agent_options.compiler_defaults || state.agent_options.defaults);
  const {pending, perform} = useAction(setError);
  const confirm = useConfirm();
  const generated = run.result?.experience_draft_id;
  useEffect(() => {setOpen(false); setPreview(null); setDraft(null); setReviewed(false); setError('');}, [run.job_id]);
  const eligible = run.kind === 'agent' && !active(run);
  if (!eligible && !generated && run.kind !== 'experience_compilation') return null;
  const source = draft?.summary || preview;
  const parent = source?.parent_experience;
  const parentContent = draft?.parent_content || preview?.parent_content;
  const lineage = draft?.lineage || preview?.lineage;
  const previouslyUsed = run.run_facts?.experience_id || run.memory_selection?.selected?.id || run.result?.memory_selection?.selected?.id;
  const pictures = draft?.compiler_images || preview?.images || [];
  async function readDraft(id: string) {
    setError('');
    const result = await api<Draft>('/api/run-experience/draft', {draft_id: id});
    setDraft(result); setReviewed(false);
  }
  async function inspect() {
    await perform('preview', async () => {
      setError(''); setPreview(null); setDraft(null); setReviewed(false); setOpen(true);
      if (generated) await readDraft(generated);
      else setPreview(await api<Preview>('/api/run-experience/preview', {run_id: run.job_id}));
    });
  }
  async function generate() {
    if (!preview) return;
    await perform('generate', async () => {
      setError('');
      const payload = {run_id: run.job_id, source_sha256: preview.source_sha256, ...model};
      let result = await api<Run | CompilationConfirmation>('/api/run-experience/generate', payload);
      if ('confirmation_required' in result) {
        if (!await confirm({title: '再次提炼这次运行？', body: result.message, confirmLabel: '再次提炼',
          note: '会再次调用模型；已有草稿与经验保留。'})) return;
        result = await api<Run | CompilationConfirmation>('/api/run-experience/generate', {...payload, confirm_duplicate: true});
      }
      if ('confirmation_required' in result) throw new Error('请重新预览后再次确认。');
      setOpen(false); onRun(result);
    });
  }
  async function save() {
    if (!draft || !reviewed) return;
    await perform('save', async () => {
      setError('');
      const result = await api<{id: string}>('/api/run-experience/save', {
        draft_id: draft.draft_id, draft_sha256: draft.draft_sha256, confirmed: true});
      setDraft({...draft, saved_trace_id: result.id});
    });
  }
  const compiling = run.kind === 'experience_compilation' && active(run);
  const canDismiss = !pending || pending === 'preview' || pending === 'read';
  return <>
    <section className="experience-invitation" aria-label="从运行提炼经验">
      <div className="experience-mark"><Icon name="library" size={24}/><span>TRACE<br/>COMPILE</span></div>
      <div className="experience-invitation-copy"><div className="eyebrow">让这一次，成为下一次的参考</div>
        <h3>{compiling ? '正在提炼这次运行…' : generated ? '经验手稿已就绪' : '把走过的步骤，留下来。'}</h3>
        <p>{compiling ? '仅分析历史轨迹，不操作设备；完成后先生成草稿。' : generated ? '先核对方法、完成判断与证据，再决定是否收入经验库。'
          : previouslyUsed ? '把本次实际使用的原经验与执行反馈一起交给编译 Agent，形成有父版本关系的新方法；原版保留。'
          : '从这次任务中提炼关键步骤、判断依据和注意事项。原始轨迹完整保留。'}</p></div>
      {compiling ? <Spinner size="small"/> : <Button appearance="primary" disabled={!!pending || (!eligible && !generated)}
        icon={<Icon name="arrow" size={16}/>} iconPosition="after" onClick={() => void inspect()}>
        {pending === 'preview' ? '正在读取…' : generated ? '审阅经验草稿' : '提炼为经验'}</Button>}
    </section>
    {open && <Dialog open onOpenChange={(_, data) => {if (canDismiss) setOpen(data.open);}}>
      <DialogSurface className="experience-studio"><DialogBody>
        <DialogTitle className="studio-title" action={<Button appearance="subtle" disabled={!canDismiss} onClick={() => setOpen(false)}>关闭</Button>}>
          <span className="eyebrow">TRACE COMPILE / EXPERIENCE STUDIO</span>经验手稿
        </DialogTitle>
        <DialogContent className="studio-content">
          <ol className="studio-progress" aria-label="经验生成流程">
            {['回看轨迹', '提炼方法', '确认入库'].map((label, i) => <li key={label}
              className={i === (draft?.saved_trace_id ? 2 : draft ? 1 : 0) ? 'current' : ''}><span>0{i + 1}</span>{label}</li>)}
          </ol>
          {pending === 'preview' && <div className="studio-feedback" role="status">
            <Spinner size="small" label={generated ? '正在读取经验草稿…' : '正在读取并校验本次轨迹…'}/>
            <p className="quiet">仅在本机读取；不会调用模型或修改原始记录。</p>
          </div>}
          {error && <div className="studio-feedback inline-warning" role="alert">
            <p>{error}</p>
            {!source && <><p>尚未调用模型，也没有修改原始轨迹。可以关闭后检查记录，或重新读取。</p>
              <Button disabled={!!pending} onClick={() => void inspect()}>重新读取</Button></>}
          </div>}
          {source && <div className="studio-grid">
            <aside className="studio-evidence"><div className="section-label">本次来源</div><h3>{source.instruction}</h3>
              <span className="studio-scope">{scopeLabel(source.scope)}</span>
              <dl className="studio-stats"><div><dt>记录轮次</dt><dd>{source.round_count}</dd></div>
                <div><dt>已交付轮次</dt><dd>{source.delivered_count}</dd></div><div><dt>选中画面</dt><dd>{source.image_count}</dd></div></dl>
              <p className="studio-outcome"><Icon name="shield" size={16}/>{source.outcome}</p>
              {source.assisted_by_user && <p className="inline-warning">人工辅助来源 · {source.intervention_count} 条干预、{source.takeover_count} 段接管。
                将区分模型与人工贡献；接管前后画面不是精确操作记录，也不能算作自主成功。</p>}
              {parent && <div className="studio-existing"><h4>本次实际使用的父经验</h4><p>{parent.name} · {parent.representation}</p>
                <p className="quiet">{parent.selection_mode === 'auto' ? 'Agent 自动加载' : '手动选择'} · 反馈版本 {lineage?.generation}</p>
                <p className="quiet mono">{parent.id}<br/>SHA256 {parent.sha256.slice(0, 16)}…</p>
                <details className="studio-raw"><summary>查看冻结的父方法 / 参考原文</summary><pre>{parentContent}</pre></details></div>}
              <div className="studio-thumbnails">{pictures.map(p => {
                const path = draft ? `runs/experience-drafts/${draft.draft_id}/${p.file}` : p.preview_path!;
                const url = `/api/local-image?path=${encodeURIComponent(path)}`;
                const label = p.label || `第 ${(p.step_index ?? 0) + 1} 轮`;
                return <a key={p.id} href={url} target="_blank" rel="noreferrer"><img src={url} loading="lazy" alt={p.label || `源运行${label}截图`}/><span>{label}</span></a>;
              })}</div>
              {!pictures.length && <p className="quiet">没有可用截图，仅基于动作与回执提炼，方法将标注证据缺口。</p>}
              <p className="quiet">模型观察按时间取样，最多 8 张，包含首尾；另附已记录的人工接管前后画面。都是历史观察，不是当前设备状态。</p>
              {source.package && <p className="quiet studio-package">应用 · {source.package}</p>}
              <p className="quiet mono">RUN {source.run_id.slice(0, 12)}</p>
            </aside>
            <div className="studio-paper">{draft ? <>
              <div className="paper-label"><span>{draft.saved_trace_id ? '已收入经验库' : parent ? '待核对的反馈修订草稿' : '待核对的经验草稿'}</span><span>{parent ? 'B · 反馈修订条件' : '方法 B · 纯文本'}</span></div>
              <MethodText content={draft.content}/>
              {draft.revision_changed === false && <p className="inline-warning">方法内容未变。本次未产生有证据支持的修改，不代表方法已经改善。</p>}
              {draft.revision_diff != null && <details className="studio-raw"><summary>与父方法的文本差异</summary><pre>{draft.revision_diff || '无文本差异'}</pre></details>}
              <details className="studio-raw"><summary>查看完整 Markdown 原文</summary><pre>{draft.content}</pre></details>
              <div className="studio-review">{draft.saved_trace_id ? <p role="status"><Icon name="check" size={18}/>{parent ? '已保存带父版本关系的反馈修订版，原方法保留；后续自动检索优先提供同一谱系最新已确认版本。没有启动新任务。' : '已保存独立经验版本。没有启动新任务，也没有自动选为任务经验。'}</p>
                : <Checkbox checked={reviewed} disabled={!!pending} onChange={(_, d) => setReviewed(d.checked === true)}
                    label={parent ? '我已核对原经验、本次反馈与修订内容，同意保存新版本供后续检索；这不代表效果提升。' : '我已核对方法与来源，理解保存不代表独立验证成功。'}/>}</div>
            </> : <>
              <div className="paper-label"><span>{parent ? '原经验 + 本次反馈 → 新版本' : '从一次执行，到一份方法'}</span><Icon name="record" size={18}/></div>
              <h2>{parent ? <>用这次反馈，<br/>修订原方法。</> : <>留下判断，<br/>不只是点击。</>}</h2>
              <p className="studio-lead">{parent ? '保留仍有依据的原步骤，只根据本次证据修改、补充或标注不确定性。未执行到的旧步骤不等于无效；新旧版本不会覆盖。' : '把零散的动作整理成可读、可核对的步骤。相同方法可作为新任务的参考，具体操作仍以当前观察为准。'}</p>
              <div className="studio-outline">{[
                ['01', '适用条件', '在什么场景下参考这份经验'], ['02', '关键步骤', '保留顺序与判断，合并机械等待'],
                ['03', '完成判断', '说明需要看到什么，不把点击当成功'], ['04', '注意与局限', '保留失败教训与尚未证实的部分'],
              ].map(([number, title, text]) => <div key={number}><span>{number}</span><div><h4>{title}</h4><p>{text}</p></div></div>)}</div>
              {!!preview?.drafts.length && <div className="studio-existing"><h4>这次运行已有手稿</h4>{preview.drafts.map(d => <Button key={d.draft_id}
                appearance="subtle" disabled={!!pending} onClick={() => void perform('read', () => readDraft(d.draft_id))}>{d.trace_name} · {d.saved_trace_id ? '已保存' : '草稿'}</Button>)}</div>}
              <details className="studio-settings"><summary>提炼设置 · {model.model}</summary>
                <CompilerChoice state={state} value={model} onChange={setModel} disabled={!!pending || busy}/></details>
              <div className="studio-disclosure"><Icon name="shield" size={17}/><p>点击生成后，将{parent ? '实际使用的原经验全文、' : ''}本次任务、动作与回执、模型回答{source.assisted_by_user ? '、人工指令与接管说明' : ''}及 {source.image_count} 张历史截图发送到 Codex 订阅服务。请先确认经验和截图中没有不希望发送的敏感内容。只调用一次模型，不操作设备，不自动入库。</p></div>
              <details className="studio-raw"><summary>查看实际发送的任务证据</summary><pre>{preview?.input}</pre></details>
              <p className="quiet">审计 SHA、文件路径与来源校验留在本地，不作为编译证据发送。相同动作按引用去重，保留全部轮次、判断、执行差异和人工说明；原始记录不改写。</p>
              <p className="quiet">不设固定文本大小或轮数上限。输入仍受所选模型的实际上下文容量约束；服务端拒绝时会保留记录并显示错误。</p>
            </>}</div>
          </div>}
        </DialogContent>
        <DialogActions className="studio-actions"><span className="quiet">{draft ? '保存的是方法，原始运行记录不会改动。' : '先生成草稿，再由你核对保存。'}</span>
          <Button disabled={!canDismiss} onClick={() => setOpen(false)}>{draft ? '稍后再看' : '暂不提炼'}</Button>
          {draft?.saved_trace_id ? <Button appearance="primary" onClick={() => {setOpen(false); onLibrary?.(draft.saved_trace_id!);}}>到经验库查看</Button>
            : draft ? <Button appearance="primary" disabled={busy || !!pending || !reviewed} onClick={() => void save()}>{pending === 'save' ? '正在保存…' : '保存到经验库'}</Button>
              : <Button appearance="primary" disabled={busy || !!pending || !preview} onClick={() => void generate()}>{pending === 'generate' ? '正在提交…' : '生成经验草稿'}</Button>}
        </DialogActions>
      </DialogBody></DialogSurface>
    </Dialog>}
  </>;
}
