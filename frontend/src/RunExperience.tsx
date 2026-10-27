import {useEffect, useState} from 'react';
import {Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Radio, RadioGroup, Spinner, Textarea} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import {active} from './types';
import type {AppState, CompilationConfirmation, ExperienceLineage, ExperienceVersioning, ModelChoice, Run, Trace} from './types';
import {Choice, Field, Icon} from './ui';
import {versionLabel} from './experience';
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
  save_options?: {default_mode: ExperienceVersioning['mode']; default_parent_id: string; revision_targets: (Trace & {version_revision: number})[]};
  saved_versioning?: ExperienceVersioning | null;
  authoring?: {kind: string; guidance: string; revision: number} | null;
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
  const [saveMode, setSaveMode] = useState<ExperienceVersioning['mode']>('independent');
  const [revisionParent, setRevisionParent] = useState('');
  const [error, setError] = useState('');
  const [guidance, setGuidance] = useState('');
  const [feedback, setFeedback] = useState('');
  const [editing, setEditing] = useState(false);
  const [editedContent, setEditedContent] = useState('');
  const [selectedDraft, setSelectedDraft] = useState<string | null>(null);
  const [model, setModel] = useState<ModelChoice>(state.agent_options.compiler_defaults || state.agent_options.defaults);
  const {pending, perform} = useAction(setError);
  const confirm = useConfirm();
  const generated = run.result?.experience_draft_id;
  useEffect(() => {setOpen(false); setPreview(null); setDraft(null); setReviewed(false); setError('');
    setGuidance(''); setFeedback(''); setEditing(false); setSelectedDraft(null);}, [run.job_id]);
  const eligible = run.kind === 'agent' && !active(run);
  if (!eligible && !generated && run.kind !== 'experience_compilation') return null;
  const source = draft?.summary || preview;
  const parent = source?.parent_experience;
  const parentContent = draft?.parent_content || preview?.parent_content;
  const lineage = draft?.lineage || preview?.lineage;
  const previouslyUsed = run.run_facts?.experience_id || run.memory_selection?.selected?.id || run.result?.memory_selection?.selected?.id;
  const pictures = draft?.compiler_images || preview?.images || [];
  const revisionTargets = draft?.save_options?.revision_targets || [];
  const revisionTarget = revisionTargets.find(t => t.id === revisionParent);
  function receiveDraft(result: Draft) {
    setDraft(result); setSelectedDraft(result.draft_id); setReviewed(false); setEditing(false); setFeedback('');
    setSaveMode(result.saved_versioning?.mode || result.save_options?.default_mode || 'independent');
    setRevisionParent(result.saved_versioning?.parent_id || result.save_options?.default_parent_id || '');
  }
  async function readDraft(id: string) {
    setError('');
    const result = await api<Draft>('/api/run-experience/draft', {draft_id: id});
    receiveDraft(result);
  }
  async function inspect() {
    await perform('preview', async () => {
      setError(''); setPreview(null); setDraft(null); setReviewed(false); setOpen(true);
      if (selectedDraft || generated) await readDraft(selectedDraft || generated!);
      else setPreview(await api<Preview>('/api/run-experience/preview', {run_id: run.job_id}));
    });
  }
  async function generate() {
    if (!preview) return;
    await perform('generate', async () => {
      setError('');
      const payload = {run_id: run.job_id, source_sha256: preview.source_sha256, guidance, ...model};
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
  const unsavedEdit = editing && editedContent !== draft?.content;
  async function dismiss() {
    if (!canDismiss) return;
    if (unsavedEdit && !await confirm({title: '放弃未保存的编辑？', body: '当前正文的修改还没有保存为新草稿。', confirmLabel: '放弃编辑'})) return;
    setEditing(false); setOpen(false);
  }
  async function applyEdit() {
    if (!draft) return;
    await perform('edit', async () => {
      const result = await api<Draft>('/api/run-experience/edit', {
        draft_id: draft.draft_id, draft_sha256: draft.draft_sha256, content: editedContent});
      receiveDraft(result);
    });
  }
  async function revise() {
    if (!draft || !feedback.trim() || unsavedEdit) return;
    await perform('revise', async () => {
      const result = await api<Run>('/api/run-experience/generate', {
        draft_id: draft.draft_id, draft_sha256: draft.draft_sha256, guidance: feedback, ...model});
      setOpen(false); onRun(result);
    });
  }
  async function save() {
    if (!draft || !reviewed || (saveMode === 'revision' && !revisionTarget)) return;
    await perform('save', async () => {
      setError('');
      const result = await api<{id: string; versioning?: ExperienceVersioning}>('/api/run-experience/save', {
        draft_id: draft.draft_id, draft_sha256: draft.draft_sha256, confirmed: true, save_mode: saveMode,
        ...(saveMode === 'revision' && revisionTarget ? {revision_parent_id: revisionTarget.id,
          expected_revision: revisionTarget.version_revision} : {})});
      setDraft({...draft, saved_trace_id: result.id, saved_versioning: result.versioning || {
        mode: saveMode, parent_id: saveMode === 'revision' ? revisionParent : null}});
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
          : previouslyUsed ? '根据原经验与本次执行反馈提炼方法，保存时可接续原经验版本，也可建立新的独立经验。'
          : '从这次任务中提炼关键步骤、判断依据和注意事项。原始轨迹完整保留。'}</p></div>
      {compiling ? <Spinner size="small"/> : <Button appearance="primary" disabled={!!pending || (!eligible && !generated)}
        icon={<Icon name="arrow" size={16}/>} iconPosition="after" onClick={() => void inspect()}>
        {pending === 'preview' ? '正在读取…' : generated ? '审阅经验草稿' : '提炼为经验'}</Button>}
    </section>
    {open && <Dialog open onOpenChange={(_, data) => {if (!data.open) void dismiss();}}>
      <DialogSurface className="experience-studio"><DialogBody>
        <DialogTitle className="studio-title" action={<Button appearance="subtle" disabled={!canDismiss} onClick={() => void dismiss()}>关闭</Button>}>
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
              <div className="paper-label"><span>{draft.saved_trace_id ? '已收入经验库' : parent ? '待核对的反馈修订草稿' : '待核对的经验草稿'}</span><span>{draft.authoring ? draft.authoring.kind === 'edit' ? '人工编辑版' : '人工指导版' : parent ? 'B · 反馈修订条件' : '方法 B · 纯文本'}</span></div>
              {editing ? <div className="studio-editor">
                <label className="field"><span>经验正文（Markdown）</span><Textarea aria-label="经验正文（Markdown）" value={editedContent} resize="vertical"
                  disabled={!!pending} onChange={(_, d) => {setEditedContent(d.value); setReviewed(false);}}/></label>
                <p className="quiet">保留章节结构和真实的来源轮次引用。修改保存在新草稿中，原稿保留。</p>
                <div className="studio-edit-actions"><Button disabled={!!pending || !unsavedEdit || !editedContent.trim()} onClick={() => void applyEdit()}>
                  {pending === 'edit' ? '正在保存修改…' : '保存修改为新草稿'}</Button>
                  <Button disabled={!!pending} onClick={() => {setEditing(false); setReviewed(false);}}>取消编辑</Button></div>
              </div> : <><MethodText content={draft.content}/><Button disabled={!!pending || busy}
                onClick={() => {setEditedContent(draft.content); setEditing(true); setReviewed(false);}}>直接编辑正文</Button></>}
              <div className="studio-revision">
                <label className="field"><span>希望怎样修改</span><Textarea value={feedback} resize="vertical" disabled={!!pending || busy}
                  placeholder="例如：只保留酒店露台导航；补充容易走错的分岔，不要把试错动作写成必要步骤。"
                  onChange={(_, d) => {setFeedback(d.value); setReviewed(false);}}/></label>
                <p className="quiet">提交后会把当前草稿、修改意见和 {pictures.length} 张已冻结历史截图及轨迹证据交给所选 Codex 模型，生成新草稿；旧稿保留。</p>
                <div className="studio-edit-actions"><Button disabled={busy || !!pending || !feedback.trim() || unsavedEdit}
                  onClick={() => void revise()}>{pending === 'revise' ? '正在提交…' : '按意见修订'}</Button>
                  {!!feedback && <Button disabled={!!pending} onClick={() => setFeedback('')}>清空意见</Button>}</div>
                {unsavedEdit && <p className="quiet">请先保存正文修改，再提交修订意见或确认入库。</p>}
                <details className="studio-settings"><summary>修订模型 · {model.model}</summary>
                  <CompilerChoice state={state} value={model} onChange={setModel} disabled={!!pending || busy}/></details>
              </div>
              <Button appearance="subtle" disabled={!!pending} onClick={() => void perform('versions', async () => {
                setPreview(await api<Preview>('/api/run-experience/preview', {run_id: draft.summary.run_id}));
              })}>查看这次运行的其他草稿</Button>
              {!!preview?.drafts.length && <div className="studio-existing">{preview.drafts.map(d => <Button key={d.draft_id}
                appearance="subtle" disabled={!!pending || unsavedEdit} onClick={() => void perform('read', () => readDraft(d.draft_id))}>
                {d.trace_name} · {d.saved_trace_id ? '已保存' : '草稿'} · {new Date(d.created_at).toLocaleString()}</Button>)}</div>}
              {draft.revision_changed === false && <p className="inline-warning">方法内容未变。本次未产生有证据支持的修改，不代表方法已经改善。</p>}
              {draft.revision_diff != null && <details className="studio-raw"><summary>与父方法的文本差异</summary><pre>{draft.revision_diff || '无文本差异'}</pre></details>}
              <details className="studio-raw"><summary>查看完整 Markdown 原文</summary><pre>{draft.content}</pre></details>
              {!draft.saved_trace_id && <div className="studio-save-options">
                <h3>保存方式</h3>
                <RadioGroup aria-label="保存方式" value={saveMode} disabled={busy || !!pending || unsavedEdit}
                  onChange={(_, data) => {setSaveMode(data.value as ExperienceVersioning['mode']); setReviewed(false);}}>
                  <Radio value="revision" label="成为已有经验的新版本" disabled={!revisionTargets.length}/>
                  <Radio value="independent" label="成为新的独立经验"/>
                </RadioGroup>
                {saveMode === 'revision' ? <>
                  <Field label="接续哪份经验"><Choice value={revisionParent} disabled={busy || !!pending || unsavedEdit}
                    onChange={event => {setRevisionParent(event.target.value); setReviewed(false);}}>
                    <option value="">请选择要更新的经验</option>
                    {revisionTargets.map(target => <option key={target.id} value={target.id}>{versionLabel(target)}</option>)}
                  </Choice></Field>
                  <p className="quiet">保存后成为所选经验的当前版本，原当前版本进入历史；后续自动选择只使用新版本。</p>
                </> : <p className="quiet">建立自己的版本历史，已有经验的当前版本不变。来源证据仍保留，可作为独立候选参与后续自动检索。</p>}
                {!revisionTargets.length && <p className="quiet">暂无范围匹配的已有方法，可先保存为独立经验。</p>}
              </div>}
              <div className="studio-review">{draft.saved_trace_id ? <p role="status"><Icon name="check" size={18}/>{draft.saved_versioning?.mode === 'independent'
                ? '已保存为新的独立经验，已有经验的当前版本保持不变；没有启动新任务。'
                : draft.saved_versioning?.mode === 'revision' ? '已保存为新版本，原当前版本保留在版本历史中；没有启动新任务。'
                : '已保存到经验库，可查看当前版本和版本历史；没有启动新任务。'}</p>
                : <Checkbox checked={reviewed} disabled={!!pending || unsavedEdit || !!feedback.trim()} onChange={(_, d) => setReviewed(d.checked === true)}
                    label={`我已核对方法、来源和保存方式，同意${saveMode === 'revision' ? '保存为所选经验的新版本' : '保存为新的独立经验'}；保存不代表独立验证成功。`}/>}</div>
            </> : <>
              <div className="paper-label"><span>{parent ? '原经验 + 本次反馈 → 方法草稿' : '从一次执行，到一份方法'}</span><Icon name="record" size={18}/></div>
              <h2>{parent ? <>用这次反馈，<br/>修订原方法。</> : <>留下判断，<br/>不只是点击。</>}</h2>
              <p className="studio-lead">{parent ? '保留仍有依据的原步骤，只根据本次证据修改、补充或标注不确定性。未执行到的旧步骤不等于无效；新旧版本不会覆盖。' : '把零散的动作整理成可读、可核对的步骤。相同方法可作为新任务的参考，具体操作仍以当前观察为准。'}</p>
              <div className="studio-outline">{[
                ['01', '进入条件', '识别当前状态，找到适用的任务阶段'], ['02', '阶段操作', '保留关键顺序，合并重复移动和等待'],
                ['03', '反馈判断', '说明观察什么、何时继续或转入分支'], ['04', '独立方法', '写全流程与条件，直接用于新的任务'],
              ].map(([number, title, text]) => <div key={number}><span>{number}</span><div><h4>{title}</h4><p>{text}</p></div></div>)}</div>
              {!!preview?.drafts.length && <div className="studio-existing"><h4>这次运行已有手稿</h4>{preview.drafts.map(d => <Button key={d.draft_id}
                appearance="subtle" disabled={!!pending} onClick={() => void perform('read', () => readDraft(d.draft_id))}>{d.trace_name} · {d.saved_trace_id ? '已保存' : '草稿'}</Button>)}</div>}
              <label className="field studio-guidance"><span>提炼要求（可选）</span><Textarea value={guidance} resize="vertical"
                disabled={!!pending || busy} onChange={(_, d) => setGuidance(d.value)}
                placeholder="指定要保留的部分、重点、详略或应避免的结论。生成后还可以编辑或提出修改意见。"/></label>
              <details className="studio-settings"><summary>提炼设置 · {model.model}</summary>
                <CompilerChoice state={state} value={model} onChange={setModel} disabled={!!pending || busy}/></details>
              <div className="studio-disclosure"><Icon name="shield" size={17}/><p>点击生成后，将提炼要求、{parent ? '实际使用的原经验方法、' : ''}本次任务、动作与回执、模型回答{source.assisted_by_user ? '、人工指令与接管说明' : ''}及 {source.image_count} 张历史截图发送到 Codex 订阅服务。请先确认经验和截图中没有不希望发送的敏感内容。只调用一次模型，不操作设备，不自动入库。</p></div>
              <details className="studio-raw"><summary>查看实际发送的任务证据</summary><pre>{preview?.input}</pre></details>
              <p className="quiet">审计 SHA、文件路径与来源校验留在本地，不作为编译证据发送。逐轮简表保留判断、交付状态和人工说明；普通坐标、机械时长与重复回执留在本地，相同图片只发送一次。方法正文按阶段组织，不含轮次引用或修订报告。</p>
              <p className="quiet">不设固定文本大小或轮数上限。输入仍受所选模型的实际上下文容量约束；服务端拒绝时会保留记录并显示错误。</p>
            </>}</div>
          </div>}
        </DialogContent>
        <DialogActions className="studio-actions"><span className="quiet">{draft ? '保存的是方法，原始运行记录不会改动。' : '先生成草稿，再由你核对保存。'}</span>
          <Button disabled={!canDismiss} onClick={() => void dismiss()}>{draft ? '稍后再看' : '暂不提炼'}</Button>
          {draft?.saved_trace_id ? <Button appearance="primary" onClick={() => {setOpen(false); onLibrary?.(draft.saved_trace_id!);}}>到经验库查看</Button>
            : draft ? <Button appearance="primary" disabled={busy || !!pending || !reviewed || unsavedEdit || !!feedback.trim() || (saveMode === 'revision' && !revisionTarget)} onClick={() => void save()}>{pending === 'save' ? '正在保存…' : '保存到经验库'}</Button>
              : <Button appearance="primary" disabled={busy || !!pending || !preview} onClick={() => void generate()}>{pending === 'generate' ? '正在提交…' : '生成经验草稿'}</Button>}
        </DialogActions>
      </DialogBody></DialogSurface>
    </Dialog>}
  </>;
}
