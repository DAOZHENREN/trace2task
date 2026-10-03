import {useEffect, useRef, useState} from 'react';
import {Button, Input, Textarea} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import {Dictation, dictationActive} from './audio';
import {traceAssetUrl, usableTrace, versionTime} from './experience';
import type {AppState, Candidate, CompilationConfirmation, EvidencePicture, ModelChoice, Recording, Run, TaskPack, Trace, TraceEvidenceBody, TraceModelInput, TrashItem} from './types';
import {Empty, Field, Icon, PageHeading, Refresh} from './ui';
import {CompilerChoice, EvidenceImage, JsonDetails, Sections, useAction} from './workflow';

type Props = {state: AppState; busy: boolean; refresh: () => Promise<void>; onRun: (run: Run) => void;
  onError: (text: string) => void; onUse: (trace: Trace) => void; onRecord: () => void};
export function Library({state, busy, refresh, onRun, onError, onUse, onRecord}: Props) {
  const confirm = useConfirm();
  const [tab, setTab] = useState('sequences'); const [filter, setFilter] = useState('');
  const [selection, setSelection] = useState<{kind: string; id: string} | null>(null);
  const [preview, setPreview] = useState<{trace: Trace; input: TraceModelInput; body: TraceEvidenceBody} | null>(null);
  const [compiler, setCompiler] = useState<ModelChoice>(state.agent_options.compiler_defaults || state.agent_options.defaults);
  const [message, setMessage] = useState('');
  const trash = state.trash || [];
  const {pending, perform} = useAction(onError); const disabled = busy || !!pending;
  const matches = (text: string) => text.toLowerCase().includes(filter.toLowerCase());
  const candidate = state.candidates.find(c => selection?.kind === 'candidate' && c.local_path === selection.id);
  const task = state.taskpacks.find(t => selection?.kind === 'task' && t.path === selection.id);
  async function inspect(trace: Trace) {
    await perform('read', async () => {const [input, body] = await Promise.all([
      api<TraceModelInput>('/api/traces/model-input', {id: trace.id}), api<TraceEvidenceBody>('/api/traces/body', {id: trace.id})]);
      setPreview({trace, input, body}); setSelection({kind: 'sequence', id: trace.id});});
  }
  async function remove(endpoint: string, payload: unknown, description: string) {
    if (!await confirm({title: '移到回收站？', body: description, confirmLabel: '移到回收站', note: '此操作可恢复，不会永久删除。'})) return;
    setMessage('');
    await perform('delete', async () => {const result = await api<{pending_cleanup?: boolean}>(endpoint, payload);
      setSelection(null); setPreview(null); await refresh();
      setMessage(result.pending_cleanup ? '已保存完整回收站副本并从正常列表移除。原文件仍被占用，等待安全清理；现在可在回收站查看。'
        : '已移到回收站并从正常列表移除。可在「回收站」恢复；已有运行记录仍保留。');});
  }
  async function trashAction(item: TrashItem, removePermanently = false) {
    const confirmation = removePermanently
      ? `永久删除“${item.name}”？\n仅删除 ${item.path}。此操作无法撤销，运行记录及其他已编译经验不受影响。`
      : `恢复“${item.name}”到 ${item.original_path}？\n${item.legacy ? '这是旧归档，原位置根据目录命名推断，请核对。\n' : ''}不会覆盖已有文件，也不会自动执行任务。`;
    if (!await confirm({title: removePermanently ? '永久删除这份归档？' : '恢复这份归档？', body: confirmation,
      confirmLabel: removePermanently ? '永久删除' : '确认恢复', intent: removePermanently ? 'danger' : 'normal',
      note: removePermanently ? '此操作无法撤销，请核对归档名称和位置。' : '不会自动选为任务经验，也不会启动任务。'})) return;
    setMessage('');
    await perform(removePermanently ? 'purge' : 'restore', async () => {
      await api(removePermanently ? '/api/trash/delete' : '/api/trash/restore', {path: item.path, ...(removePermanently ? {confirmed: true} : {})});
      await refresh(); setMessage(removePermanently ? `已永久删除“${item.name}”，无法从本回收站恢复。` : `已恢复“${item.name}”。可回到对应分类查看，未自动启用为当前任务经验。`);
    });
  }
  async function openLocal(path: string) {await perform('open', async () => {await api('/api/open-local', {path});});}
  async function change(endpoint: string, payload: unknown, confirmation: string, job = false) {
    if (job && dictationActive()) return onError('请先结束语音输入并等待转写完成。');
    if (!await confirm({title: job ? '生成修订草稿？' : '确认这次修改？', body: confirmation, confirmLabel: job ? '生成草稿' : '确认修改'})) return;
    await perform('change', async () => {const result = await api<Run>(endpoint, payload); if (job) onRun(result); await refresh();});
  }
  async function compile(record: Recording, representation: 'A' | 'D' | 'semantic' = 'D') {
    const semantic = representation === 'semantic';
    if (semantic && dictationActive()) return onError('请先结束语音输入并等待转写完成。');
    if (semantic && !await confirm({title: '通过 Codex 编译这段示范？', body: '示范与相关文本将交给 Codex。原始 Trace 保留，编译结果需单独核对。', confirmLabel: '开始语义编译'})) return;
    setMessage('');
    await perform('compile', async () => {
      const endpoint = semantic ? '/api/recordings/compile' : `/api/traces/generate-${representation.toLowerCase()}`;
      const payload = {trace_path: record.trace_path, ...(semantic ? compiler : {})};
      type Reply = CompilationConfirmation | Run | {id: string};
      let result = await api<Reply>(endpoint, payload);
      if ('confirmation_required' in result) {
        const existing = result;
        if (!await confirm({title: `重新生成${semantic ? '语义编译结果' : representation === 'A' ? '原始证据 A' : '精简序列 D'}？`,
          body: <><p>{existing.message}</p><div className="version-list-heading"><span>这份录制的已有版本</span><span>{existing.existing_versions.length} 个版本</span></div>
            <ul className="confirmation-versions">{existing.existing_versions.map(v => <li key={v.id || v.path}><strong>{v.trace_name}</strong>
              <div className="quiet">{versionTime(v)}</div><span className="mono">{v.id?.slice(0, 8) || v.path}</span></li>)}</ul></>,
          confirmLabel: semantic ? '继续编译' : '生成新版本', note: '取消不会生成新版本或启动编译。新生成的经验不会自动启用。'})) {
          setMessage('已取消重复编译，未生成新版本。'); return;
        }
        result = await api<Reply>(endpoint, {...payload, confirm_duplicate: true});
      }
      if ('confirmation_required' in result) throw new Error('后台仍要求确认，未开始重复编译，请刷新后重试。');
      if ('job_id' in result) onRun(result);
      else {setTab('sequences'); setMessage('新版本已生成；已有版本保留，未自动切换当前任务经验。');}
      await refresh();
    });
  }
  return <>
    <PageHeading eyebrow="EXPERIENCE LIBRARY" title="经验积累下来，下一次更从容。" copy="原始示范、编译结果与人工反馈各自保留来源。确认不改变内容；反馈修订生成独立版本。"
      action={<div className="button-row"><Refresh busy={!!pending} onClick={() => void refresh().catch(e => onError(String(e)))}/><Button onClick={onRecord}>录制新示范</Button></div>}/>
    <Sections label="经验分类" value={tab} onChange={value => {setTab(value); setSelection(null); setFilter('');}} items={[
      {id: 'sequences', label: `序列经验 · ${state.trace_representations.length}`}, {id: 'recordings', label: `原始录制 · ${state.recordings.length}`},
      {id: 'tasks', label: `编译与历史版本 · ${state.taskpacks.length}`}, {id: 'candidates', label: `反馈审查 · ${state.candidates.length}`},
      {id: 'trash', label: `回收站 · ${trash.length}`} ]}/>
    {message && <p className="notice" role="status">{message}</p>}
    <div className="library-toolbar"><Input aria-label="搜索经验" value={filter} placeholder="搜索名称、指令或说明" contentBefore={<Icon name="library" size={18}/>} onChange={(_, d) => setFilter(d.value)}/>
      <span className="quiet">{pending ? '正在处理…' : '所有内容直接读取本地数据，不自动启用新版本'}</span></div>
    {['recordings', 'candidates'].includes(tab) && <details className="surface compiler-options"><summary>编译与反馈模型</summary><CompilerChoice state={state} value={compiler} onChange={setCompiler} disabled={disabled}/><p className="quiet">语义编译 / 反馈修订通过 Codex 处理示范与相关文本；原始证据 A 与精简序列 D 都是本地确定性提取。</p></details>}
    {tab === 'trash' && <p className="notice">回收站不自动清空。恢复不会覆盖同名资产；永久删除须再次确认。运行记录是审计历史，移除原始录制后仍会保留。</p>}
    {selection && <section className="surface detail-workspace"><div className="row-between"><div><span className="section-label">经验详情</span><h2>{candidate?.task_id || task?.task_id || preview?.trace.trace_name}</h2></div>
      <Button onClick={() => setSelection(null)}>返回列表</Button></div>
      {selection.kind === 'sequence' && preview && <><p className="quiet">{versionTime(preview.trace)}</p>
        {preview.trace.representation === 'A' && <div className="notice"><strong>模型输入 = 下方完整文本 + {preview.input.images?.length || 0} 张历史图片</strong><p>{preview.input.image_selection?.rule}</p>
          {preview.input.format === 'actions-with-images-v2' && <p>文本以 D 式动作序列为主，只引用选中的历史截图。原始事件、文件路径和校验信息仅供审计，不塞入模型文本。</p>}
          {preview.input.format === 'raw-evidence-v1' && <p>这是旧版 A，模型文本包含完整原始日志与截图索引。可回到原始录制重新生成精简格式的 A；此版本不会被自动改写。</p>}
          <p>仅这些图片作为 A 附件发送；其余截图只在审计归档中保留。当前任务与实时截图由执行器另行提供，超出模型容量会报错，不静默删减这份经验。</p></div>}
        <h3 className="spaced">完整模型文本</h3><p className="quiet">直接读取编译版本中的 model-input.txt，未重新摘要或截断。
          {preview.trace.representation === 'A' && <> <a href={traceAssetUrl(preview.trace.id, 'model-input.txt')} target="_blank" rel="noreferrer">单独打开完整文本</a></>}</p>
        <pre className="experience-preview">{preview.input.content}</pre>
        {preview.trace.representation === 'A' && <><h3 className="spaced">实际发送的历史图片 · {preview.input.images?.length || 0} 张</h3>
          <div className="evidence-gallery">{preview.input.images?.map((picture, i) => <BundlePicture key={picture.id} traceId={preview.trace.id} picture={picture} label={`附件 ${i + 1} · 历史帧 ${picture.id}`}/>)}</div>
          <details className="data-disclosure"><summary>完整证据归档与未发送图片（不额外发送给模型）</summary>
            <p className="quiet">这些文件是原始字节副本，仅供核对来源。是否进入模型输入以「完整模型文本」和「实际发送的历史图片」为准；这里的归档不会额外发送。</p>
            <div className="evidence-file-list">{preview.body.source_files?.map(file => <a key={file} href={traceAssetUrl(preview.trace.id, file)} target="_blank" rel="noreferrer">{file}</a>)}</div>
            <ul className="evidence-index">{preview.body.frames?.map(picture => <li key={picture.id}><span>历史帧 {picture.id} · {picture.selected ? '已发送' : picture.file ? '仅归档，未发送' : '缺图，未发送'}</span>
              {picture.file && <a href={traceAssetUrl(preview.trace.id, picture.file)} target="_blank" rel="noreferrer">查看原图</a>}</li>)}</ul>
          </details></>}
        <JsonDetails label={preview.trace.representation === 'A' ? '审计元数据与完整索引（不额外发送给模型）' : '结构化来源与证据'} value={preview.body}/>
        <Button appearance="primary" disabled={!usableTrace(preview.trace)} onClick={() => onUse(preview.trace)}>用于新任务</Button></>}
      {task && <TaskDetail task={task}/>}
      {candidate && <CandidateEditor key={candidate.local_path} candidate={candidate} compiler={compiler} disabled={disabled} onError={onError}
        onChange={async (endpoint, extra, job, confirmation) => change(endpoint, {path: candidate.local_path, ...extra}, confirmation, job)}/>}
    </section>}
    <div className="experience-grid">
      {tab === 'sequences' && state.trace_representations.filter(t => matches(`${t.trace_name} ${t.description}`)).map(trace => <article className="surface experience-card" key={trace.id}>
        <div className="row-between"><Icon name="library" size={25}/><span className="tag">{trace.representation === 'D' ? '可执行参考 · 序列 D' : trace.representation === 'A' ? '可执行参考 · 原始证据 A' : `${trace.representation} · 只读来源`}</span></div>
        <h2>{trace.trace_name}</h2><p className="quiet version-time">{versionTime(trace)}</p><p>{trace.description}</p><div className="experience-meta mono">{trace.id}</div>
        <div className="button-row"><Button disabled={!!pending} onClick={() => void inspect(trace)}>查看模型原文</Button><Button appearance="primary" disabled={!usableTrace(trace)} onClick={() => onUse(trace)}>用于新任务</Button>
          <Button appearance="subtle" disabled={disabled} onClick={() => void remove('/api/traces/delete', {id: trace.id}, `将序列经验“${trace.trace_name}”移到回收站？可在回收站恢复；原始录制与运行记录保留。如果正在被选为任务参考，会取消该选择。`)}>移到回收站</Button></div></article>)}
      {tab === 'recordings' && state.recordings.filter(r => matches(r.task_id || r.trace_path)).map(record => <article className="surface experience-card" key={record.trace_path}>
        <div className="row-between"><Icon name="record" size={25}/><span className="tag">{record.success ? '已完成' : '未完成'} · {record.recording_backend === 'opencua' ? 'OpenCUA' : '人类示范'}</span></div>
        <h2>{record.task_id || '未命名录制'}</h2><p>{record.input_events} 个输入事件 · {record.created_at ? new Date(record.created_at).toLocaleString('zh-CN') : '时间未记录'}</p>
        {record.derivation && <p>动作整理：{record.derivation.status} · {record.derivation.action_count ?? '—'} 组</p>}
        {record.narrated && <p>含人类讲解 · {record.narration_chars || 0} 字</p>}
        {record.recording_backend === 'opencua' && <p className="quiet">A：D 式动作序列与最多 8 张关联历史截图，原始日志另存审计归档。D：无图精简动作序列。均在本地生成，可完整审查模型输入；B/C 与 OpenCUA 语义编译尚未实现。</p>}
        <div className="button-row spaced"><Button appearance="primary" disabled={disabled || record.recording_backend !== 'opencua' || record.derivation?.status !== 'completed'}
          onClick={() => void compile(record)}>生成精简序列 D</Button>
          <Button disabled={disabled || record.recording_backend !== 'opencua' || record.derivation?.status !== 'completed'} onClick={() => void compile(record, 'A')}>生成原始证据 A</Button>
          <Button disabled={disabled || !record.success || record.compilation_supported === false} onClick={() => void compile(record, 'semantic')}>语义编译</Button></div>
        <div className="button-row"><Button appearance="subtle" disabled={!!pending} onClick={() => void openLocal(record.local_path)}>打开原始归档</Button>
          <Button appearance="subtle" disabled={disabled} onClick={() => void remove('/api/recordings/delete', {trace_path: record.trace_path}, `将原始录制“${record.task_id}”移到回收站并从本列表移除？可在回收站恢复；已生成经验与运行记录保留。`)}>移到回收站</Button></div></article>)}
      {tab === 'tasks' && state.taskpacks.filter(t => matches(`${t.task_id} ${t.instruction}`)).map(item => <article className="surface experience-card" key={item.path}>
        <div className="row-between"><Icon name="library" size={25}/><span className="tag">Trace Compile · {item.confirmed ? '已审查' : '待审查'}</span></div>
        <h2>{item.task_id}</h2><p>{item.semantic_experience?.summary || item.instruction}</p><p>状态图 v{item.semantic_experience?.revision || 0} · 人工反馈 v{item.human_guidance?.revision || 0}</p>
        <p className="quiet">保留的状态图经验，不混入当前序列 D 执行入口。</p>
        <div className="button-row spaced"><Button onClick={() => setSelection({kind: 'task', id: item.path})}>阶段、规则与历史</Button>
          <Button disabled={disabled || item.confirmed} onClick={() => void change('/api/taskpacks/confirm', {task_path: item.path}, `确认已审查“${item.task_id}”的阶段、允许动作和成功证据？仅记录审查状态，不改写经验内容。`)}>确认已审查</Button>
          {!!item.missing_message_capabilities?.length && <Button disabled={disabled} onClick={() => void change('/api/taskpacks/upgrade', {task_path: item.path}, '补充文本输入能力并将该任务标记为待确认草稿？')}>升级文本能力</Button>}</div>
        <div className="button-row"><Button appearance="subtle" disabled={!!pending} onClick={() => void openLocal(item.local_path)}>打开归档</Button>
          {item.human_guidance && <Button appearance="subtle" disabled={disabled} onClick={() => void remove('/api/taskpacks/guidance/delete', {task_path: item.path}, '只将当前人工反馈及其历史移到 taskpacks/.trash/guidance？可以恢复；Trace、Compiler 与状态图保留。')}>移除人工反馈</Button>}
          <Button appearance="subtle" disabled={disabled} onClick={() => void remove('/api/taskpacks/delete', {task_path: item.path}, `将“${item.task_id}”经验包及附属反馈移到 taskpacks/.trash？可恢复，原始 Trace 和运行日志保留。`)}>移到回收站</Button></div></article>)}
      {tab === 'candidates' && state.candidates.filter(c => matches(`${c.task_id} ${c.instruction || ''}`)).map(item => <article className="surface experience-card" key={item.local_path}>
        <div className="row-between"><Icon name="history" size={25}/><span className="tag">Feedback Revision · {item.revision?.status || item.status}</span></div>
        <h2>{item.task_id}</h2><p>{item.instruction || '查看实际运行证据，再提出修订意见。'}</p>
        <div className="button-row spaced"><Button appearance="primary" onClick={() => setSelection({kind: 'candidate', id: item.local_path})}>审查与反馈</Button>
          <Button appearance="subtle" disabled={!!pending} onClick={() => void openLocal(item.local_path)}>打开归档</Button>
          <Button appearance="subtle" disabled={disabled} onClick={() => void remove('/api/candidates/delete', {path: item.local_path}, '将这条候选反馈移到回收站？可在回收站恢复；原任务与运行记录保留。')}>移到回收站</Button></div></article>)}
      {tab === 'trash' && trash.filter(item => matches(`${item.name} ${item.original_path || ''}`)).map(item => <article className="surface experience-card" key={item.path}>
        <div className="row-between"><Icon name="history" size={25}/><span className="tag">{({recording: '原始录制', trace_experience: '序列经验', taskpack: '任务经验', candidate: '候选反馈', human_guidance: '人工反馈'}[item.kind] || '归档')} · 回收站</span></div>
        <h2>{item.name}</h2><p>{item.deleted_at ? `移入时间：${new Date(item.deleted_at).toLocaleString('zh-CN')}` : '旧版回收站归档'}</p>
        {item.original_path && <p className="quiet mono">恢复位置：{item.original_path}</p>}
        {typeof item.file_count === 'number' && <p>{item.file_count} 个文件 · {((item.bytes || 0) / 1024 / 1024).toFixed(1)} MB</p>}
        {item.pending_cleanup && <p className="inline-warning">原文件被占用，安全副本已保存；正常列表已隐藏，待释放占用后恢复或重启工作台清理。</p>}
        {item.restore_blocked_reason && <p className="quiet">{item.restore_blocked_reason}</p>}
        {item.delete_blocked_reason && <p className="quiet">{item.delete_blocked_reason}</p>}
        <div className="button-row spaced"><Button appearance="primary" disabled={disabled || !item.can_restore} onClick={() => void trashAction(item)}>恢复</Button>
          <Button disabled={!!pending || item.can_open === false} onClick={() => void openLocal(item.path)}>打开归档</Button>
          <Button appearance="subtle" disabled={disabled || !item.can_delete} onClick={() => void trashAction(item, true)}>永久删除</Button></div></article>)}
    </div>
    {({sequences: state.trace_representations.length, recordings: state.recordings.length, tasks: state.taskpacks.length, candidates: state.candidates.length, trash: trash.length}[tab] || 0) === 0 &&
      <section className="surface"><Empty icon="library" title={tab === 'trash' ? '回收站是空的' : '这里还没有记录'} text={tab === 'trash' ? '移入回收站的资产会在这里显示，可以恢复或明确确认后永久删除。' : '新录制、编译结果或反馈会出现在对应分类。已有数据不会被自动转换或覆盖。'} action={tab === 'trash' ? undefined : <Button onClick={onRecord}>录制示范</Button>}/></section>}
  </>;
}

function BundlePicture({traceId, picture, label}: {traceId: string; picture: EvidencePicture; label: string}) {
  const [failed, setFailed] = useState(false);
  if (!picture.file) return null;
  const url = traceAssetUrl(traceId, picture.file);
  return <figure className="bundle-picture"><figcaption><strong>{label}</strong><span>{picture.width} × {picture.height}</span></figcaption>
    {failed ? <p role="alert">截图读取或校验失败，请重新生成并审查。</p> : <a href={url} target="_blank" rel="noreferrer" aria-label={`${label}，打开完整原图`}><img loading="lazy" src={url} alt={label} onError={() => setFailed(true)}/></a>}
    <p className="quiet mono">SHA-256 · {picture.sha256}</p></figure>;
}

function TaskDetail({task}: {task: TaskPack}) {
  const semantic = task.semantic_experience;
  return <><p>{task.instruction}</p><div className="library-stats"><div><strong>{semantic?.stage_count || 0}</strong><span>示范阶段</span></div><div><strong>{semantic?.state_count || 0}</strong><span>状态</span></div></div>
    {semantic?.stages.map(stage => <details className="evidence-stage" key={stage.id}><summary>{stage.name} · {stage.intent}</summary><p>{stage.state_before} → {stage.state_after}</p>
      <div className="evidence-pair"><EvidenceImage path={stage.evidence_before} label="操作前证据"/><EvidenceImage path={stage.evidence_after} label="操作后证据"/></div></details>)}
    <JsonDetails label="状态与转移" value={semantic?.states || []}/><JsonDetails label="任务图版本历史" value={semantic?.history || []}/>
    <h3>人工反馈 v{task.human_guidance?.revision || 0}</h3><p>{task.human_guidance?.summary || '尚无人工反馈。'}</p>
    <JsonDetails label="当前规则" value={task.human_guidance?.rules || []}/><JsonDetails label="人工反馈版本历史" value={task.human_guidance?.history || []}/>
    <JsonDetails label="完整经验与审计信息" value={task}/></>;
}

function CandidateEditor({candidate: c, compiler, disabled, onChange, onError}: {candidate: Candidate; compiler: ModelChoice; disabled: boolean;
  onChange: (endpoint: string, data: Record<string, unknown>, job: boolean, confirmation: string) => Promise<void>; onError: (text: string) => void}) {
  const [feedback, setFeedback] = useState(''); const [summary, setSummary] = useState(c.revision?.summary || '');
  const savedSummary = useRef(c.revision?.summary || '');
  useEffect(() => {const next = c.revision?.summary || ''; const previous = savedSummary.current;
    setSummary(value => value === previous ? next : value); savedSummary.current = next;}, [c.revision?.summary]);
  const [kind, setKind] = useState('guidance');
  const revision = c.revision; const graph = c.task_model_revision;
  return <>
    <p>{c.instruction}</p><div className="evidence-pair"><EvidenceImage path={c.review_timeline?.initial_frame} label="初始画面"/><EvidenceImage path={c.review_timeline?.final_frame} label="结束画面"/></div>
    {c.review_timeline?.rounds?.map((round, i) => <details className="evidence-stage" key={i}><summary>运行证据 · 第 {i + 1} 轮</summary><div className="evidence-pair"><EvidenceImage path={round.before_frame} label="操作前"/><EvidenceImage path={round.after_frame} label="操作后"/></div><JsonDetails label="该轮回执" value={round}/></details>)}
    <Sections label="反馈类型" value={kind} onChange={setKind} items={[{id: 'guidance', label: '行为与诀窍反馈'}, {id: 'graph', label: '阶段与状态图修订'}]}/>
    <Field label="改进意见" hint="指出具体偏差、期望行为和判断依据；会交给所选 Codex 模型生成待审查草稿。"><Textarea rows={4} resize="vertical" maxLength={2000} value={feedback} disabled={disabled} onChange={(_, d) => setFeedback(d.value)}/></Field>
    <div className="button-row spaced"><Dictation disabled={disabled} onAppend={text => setFeedback(value => `${value} ${text}`.trim().slice(0, 2000))} onError={onError}/><Button appearance="primary" disabled={disabled || !feedback.trim()}
      onClick={() => void onChange(kind === 'graph' ? '/api/candidates/task-model/revise' : '/api/candidates/revise', {...compiler, feedback}, true,
        '将反馈与相关运行证据交给所选 Codex 模型生成独立修订草稿？不会自动启用，也不覆盖原始 Trace。')}>生成修订草稿</Button></div>
    {revision && <section className="revision-block"><h3>人工反馈草稿 · {revision.status}</h3><JsonDetails label="完整修订内容（请先审查）" value={revision} open/>
      <Field label="修订摘要"><Textarea value={summary} disabled={disabled || revision.status !== 'draft'} onChange={(_, d) => setSummary(d.value)} rows={3}/></Field>
      <div className="button-row spaced"><Button disabled={disabled || revision.status !== 'draft' || !summary.trim()} onClick={() => void onChange('/api/candidates/revisions/summary', {summary}, false, '保存这条草稿的人工摘要？')}>保存摘要</Button>
        <Button appearance="primary" disabled={disabled || revision.status !== 'draft' || !revision.summary || summary !== revision.summary} onClick={() => void onChange('/api/candidates/revisions/confirm', {}, false,
          '确认已审查此反馈并启用为新人工诀窍版本？原始 Trace、Compiler 结果和旧反馈版本均保留。')}>确认反馈版本</Button></div>
      {summary !== revision.summary && <p className="quiet">摘要有未保存修改，请先保存再确认。</p>}</section>}
    {graph && <section className="revision-block"><h3>任务图草稿 · v{graph.proposed_revision} · {graph.status}</h3><JsonDetails label="状态图变化与 Guidance 映射" value={graph} open/>
      <Button disabled={disabled || graph.status !== 'draft' || (graph.blocking_issue_count || 0) > 0} onClick={() => void onChange('/api/candidates/task-model/confirm', {}, false,
        `确认启用此任务图版本？${graph.guidance_review?.pending?.length || 0} 条关联语义变化的规则将暂停生效并等待复核。原始 Trace 和旧版本保留。`)}>确认任务图版本</Button></section>}
    <JsonDetails label="完整候选、指标与来源" value={c}/>
  </>;
}
