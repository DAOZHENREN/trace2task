import {useEffect, useRef, useState} from 'react';
import {Button, Input, Textarea} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import {Dictation, dictationActive} from './audio';
import {currentTrace, traceAssetUrl, usableTrace, versionTime} from './experience';
import type {AppState, Candidate, CompilationConfirmation, EvidencePicture, ModelChoice, Recording, Run, TaskPack, Trace, TraceEvidenceBody, TraceModelInput, TrashItem, TrialReview} from './types';
import {Empty, Field, Icon, PageHeading, Refresh} from './ui';
import {CompilerChoice, EvidenceImage, JsonDetails, Sections, useAction} from './workflow';
import {useBatchTrash, type BatchItem} from './BatchTrash';

type Props = {state: AppState; busy: boolean; refresh: () => Promise<void>; onRun: (run: Run) => void;
  onError: (text: string) => void; onUse: (trace: Trace) => void; onRecord: () => void;
  mode?: 'trash' | 'recordings'; requestedTraceId?: string; onRequestedTraceHandled?: () => void};
export function Library({state, busy, refresh, onRun, onError, onUse, onRecord, requestedTraceId, onRequestedTraceHandled, mode}: Props) {
  const confirm = useConfirm();
  const [tab, setTab] = useState(mode || 'sequences'); const [filter, setFilter] = useState('');
  const [historyFamily, setHistoryFamily] = useState('');
  const [selection, setSelection] = useState<{kind: string; id: string} | null>(null);
  const [preview, setPreview] = useState<{trace: Trace; input: TraceModelInput; body: TraceEvidenceBody} | null>(null);
  const [compiler, setCompiler] = useState<ModelChoice>(state.agent_options.compiler_defaults || state.agent_options.defaults);
  const [message, setMessage] = useState('');
  const trash = [...(state.trash || [])].sort((a, b) => (b.deleted_at || '').localeCompare(a.deleted_at || ''));
  const {pending, perform} = useAction(onError);
  const matches = (text: string) => text.toLowerCase().includes(filter.toLowerCase());
  const currentTraces = state.trace_representations.filter(currentTrace);
  const historicalTraces = state.trace_representations.filter(t => !currentTrace(t));
  const visibleTraces = (tab === 'history' ? historyFamily
    ? state.trace_representations.filter(t => t.family_id === historyFamily) : historicalTraces : currentTraces)
    .filter(t => matches(`${t.trace_name} ${t.description}`));
  const batchItems: BatchItem[] = tab === 'sequences' ? visibleTraces.map(t =>
    ({id: t.id, name: t.trace_name, endpoint: '/api/traces/delete', payload: {id: t.id}}))
    : tab === 'recordings' ? state.recordings.filter(r => matches(r.task_id || r.trace_path)).map(r =>
      ({id: r.trace_path, name: r.task_id || r.trace_path, endpoint: '/api/recordings/delete', payload: {trace_path: r.trace_path}}))
    : tab === 'tasks' ? state.taskpacks.filter(t => matches(`${t.task_id} ${t.instruction}`)).map(t =>
      ({id: t.path, name: t.task_id, endpoint: '/api/taskpacks/delete', payload: {task_path: t.path}}))
    : tab === 'candidates' ? state.candidates.filter(c => matches(`${c.task_id} ${c.instruction || ''}`)).map(c =>
      ({id: c.local_path, name: c.task_id, endpoint: '/api/candidates/delete', payload: {path: c.local_path}})) : [];
  const batch = useBatchTrash(batchItems, busy || !!pending, async () => {setSelection(null); setPreview(null); await refresh();}, onError);
  const disabled = busy || !!pending || batch.pending;
  const candidate = state.candidates.find(c => selection?.kind === 'candidate' && c.local_path === selection.id);
  const task = state.taskpacks.find(t => selection?.kind === 'task' && t.path === selection.id);
  async function inspect(trace: Trace) {
    await perform('read', async () => {const [input, body] = await Promise.all([
      api<TraceModelInput>('/api/traces/model-input', {id: trace.id}), api<TraceEvidenceBody>('/api/traces/body', {id: trace.id})]);
      setPreview({trace, input, body}); setSelection({kind: 'sequence', id: trace.id});});
  }
  useEffect(() => {
    const trace = state.trace_representations.find(t => t.id === requestedTraceId);
    if (!trace) return;
    setTab(currentTrace(trace) ? 'sequences' : 'history'); setHistoryFamily(trace.family_id || '');
    setFilter(''); void inspect(trace); onRequestedTraceHandled?.();
  }, [requestedTraceId, state.trace_representations]);
  useEffect(() => {
    setPreview(previous => {
      if (!previous) return null;
      const trace = state.trace_representations.find(t => t.id === previous.trace.id);
      return trace ? {...previous, trace} : null;
    });
  }, [state.trace_representations]);
  function showHistory(trace: Trace) {
    setHistoryFamily(trace.family_id || ''); setTab('history'); setFilter(''); setSelection(null); setPreview(null);
  }
  async function rollback() {
    if (!preview || currentTrace(preview.trace) || disabled) return;
    const {trace, input} = preview;
    const current = state.trace_representations.find(t => t.id === trace.current_version_id);
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input.content));
    const model_input_sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    if (!await confirm({title: '回滚为当前版本？',
      body: `经验：${trace.trace_name}\n回滚目标：${trace.id}\n${versionTime(trace)}\n当前版本：${current?.id || trace.current_version_id || '无'}${current ? `\n${versionTime(current)}` : ''}`,
      confirmLabel: '确认回滚', note: '后续自动选择将只使用回滚后的当前版本。所有版本及运行记录保留，不会启动任务。'})) return;
    await perform('rollback', async () => {
      await api('/api/traces/rollback', {id: trace.id, expected_revision: trace.version_revision, model_input_sha256});
      await refresh(); setMessage('已回滚为当前版本。后续自动选择使用此版本，其他版本保留在版本历史中。');
    });
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
      ? `永久删除“${item.name}”？\n${item.kind === 'run' ? '仅删除这条运行记录的摘要、事件与逐轮原文；关联文件、录制、经验及其他运行记录保留。' : `仅删除 ${item.path}；运行记录及其他已编译经验不受影响。`}此操作无法撤销。`
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
  async function reviewImprovement(decision: 'approved_for_trial' | 'rejected') {
    if (!preview?.input.review || preview.input.review.decision !== 'pending_review') return;
    const {trace, input} = preview;
    if (!await confirm({title: decision === 'approved_for_trial' ? '允许这个候选用于试验？' : '拒绝这个候选？',
      body: `${trace.trace_name}\n父版本：${input.evolution?.parent_id}\n源运行：${input.evolution?.source_run_id}\n请核对父方法、候选全文及归档证据。决定绑定当前候选哈希，保存后不能覆盖。`,
      confirmLabel: decision === 'approved_for_trial' ? '确认允许试用' : '确认拒绝',
      note: '审查不是效果评分；不会自动选用、启动任务或替换原经验。回退时重新选择原版本即可。'})) return;
    await perform('review-evolution', async () => {
      const review = await api<TrialReview>('/api/traces/evolution/review', {id: trace.id,
        candidate_sha256: input.review!.candidate_sha256, decision});
      setPreview({trace: {...trace, review_status: review.decision}, input: {...input, review}, body: preview.body});
      await refresh(); setMessage(decision === 'approved_for_trial' ? '已允许试用，尚未选用或执行；改进效果仍待独立评测。' : '已拒绝候选；原经验和证据保留。');
    });
  }
  async function change(endpoint: string, payload: unknown, confirmation: string, job = false) {
    if (job && dictationActive()) return onError('请先结束语音输入并等待转写完成。');
    if (!await confirm({title: job ? '生成修订草稿？' : '确认这次修改？', body: confirmation, confirmLabel: job ? '生成草稿' : '确认修改'})) return;
    await perform('change', async () => {const result = await api<Run>(endpoint, payload); if (job) onRun(result); await refresh();});
  }
  async function combine(trace: Trace) {
    if (!await confirm({title: '从这个 B 版本生成 C？',
      body: `${trace.trace_name}\n${versionTime(trace)}\nB 版本：${trace.id}\n复用此版本的完整 Procedure 和编译时冻结的 A 动作证据及历史截图，不再调用模型。`,
      confirmLabel: '生成组合证据 C', note: '生成独立版本，不覆盖 A/B/D，不自动选用或执行。'})) return;
    await perform('combine', async () => {
      type Reply = CompilationConfirmation | {id: string};
      const payload = {b_id: trace.id};
      let result = await api<Reply>('/api/traces/generate-c', payload);
      if ('confirmation_required' in result) {
        if (!await confirm({title: '重新生成组合证据 C？', body: <><p>{result.message}</p>
          <ul>{result.existing_versions.map(v => <li key={v.id}>{v.trace_name} · {versionTime(v)} · {v.id}</li>)}</ul></>,
          confirmLabel: '生成新版本', note: '仍复用同一个 B，不重新编译；已有 C 保留。'})) return;
        result = await api<Reply>('/api/traces/generate-c', {...payload, confirm_duplicate: true});
      }
      if ('confirmation_required' in result) throw new Error('仍需重复确认，请刷新后重试。');
      await refresh(); setSelection(null); setPreview(null);
      setMessage(`已生成组合证据 C（${result.id}）：B 原文与对应 A 证据已独立保存，未自动选用。`);
    });
  }
  async function compile(record: Recording, representation: 'A' | 'B' | 'D' | 'semantic' = 'D') {
    const semantic = representation === 'semantic';
    const agent = representation === 'B';
    if ((semantic || agent) && dictationActive()) return onError('请先结束语音输入并等待转写完成。');
    if (agent && !await confirm({title: '通过 Agent 编译方法 B？',
      body: `将这段示范的名称、动作序列与最多 8 张选中历史截图交给 Codex（${compiler.model} / ${compiler.reasoning_effort}），按已确认的提示词生成 Markdown Procedure。`,
      confirmLabel: '开始 Agent 编译', note: '不会执行桌面动作，不自动启用结果。原始录制与已有经验保留；请核对生成的方法。'})) return;
    if (semantic && !await confirm({title: '通过 Codex 编译这段示范？', body: '示范与相关文本将交给 Codex。原始 Trace 保留，编译结果需单独核对。', confirmLabel: '开始语义编译'})) return;
    setMessage('');
    await perform('compile', async () => {
      const endpoint = semantic ? '/api/recordings/compile' : `/api/traces/generate-${representation.toLowerCase()}`;
      const payload = {trace_path: record.trace_path, ...(semantic || agent ? compiler : {})};
      type Reply = CompilationConfirmation | Run | {id: string};
      let result = await api<Reply>(endpoint, payload);
      if ('confirmation_required' in result) {
        const existing = result;
        if (!await confirm({title: `重新生成${semantic ? '语义编译结果' : agent ? 'Agent 方法 B' : representation === 'A' ? '原始证据 A' : '精简序列 D'}？`,
          body: <><p>{existing.message}</p><div className="version-list-heading"><span>这份录制的已有版本</span><span>{existing.existing_versions.length} 个版本</span></div>
            <ul className="confirmation-versions">{existing.existing_versions.map(v => <li key={v.id || v.path}><strong>{v.trace_name}</strong>
              <div className="quiet">{versionTime(v)}</div><span className="mono">{v.id?.slice(0, 8) || v.path}</span></li>)}</ul></>,
          confirmLabel: semantic ? '继续编译' : '生成新版本', note: semantic ? '取消不会生成新版本或启动编译。' : '生成后作为当前版本，旧版本保留在版本历史中；不会启动任务。'})) {
          setMessage('已取消重复编译，未生成新版本。'); return;
        }
        result = await api<Reply>(endpoint, {...payload, confirm_duplicate: true});
      }
      if ('confirmation_required' in result) throw new Error('后台仍要求确认，未开始重复编译，请刷新后重试。');
      if ('job_id' in result) onRun(result);
      else {setTab('sequences'); setMessage('新版本已生成并设为当前版本；旧版本保留在版本历史中，未启动任务。');}
      await refresh();
    });
  }
  return <>
    <PageHeading eyebrow={mode === 'trash' ? 'RECYCLE BIN' : 'EXPERIENCE LIBRARY'} title={mode === 'trash' ? '全局回收站' : mode === 'recordings' ? '已保存的录制示范' : '经验积累下来，下一次更从容。'} copy={mode === 'trash' ? '统一管理录制示范、经验版本、反馈和运行记录；不会自动清空。' : '默认展示当前版本，自动选择也只使用当前版本。旧版本保留在版本历史中，可查看原文并回滚，不自动删除。'}
      action={<div className="button-row"><Refresh busy={!!pending} onClick={() => void refresh().catch(e => onError(String(e)))}/>{mode !== 'trash' && <Button onClick={onRecord}>录制新示范</Button>}</div>}/>
    {!mode && <Sections label="经验分类" value={tab} onChange={value => {setTab(value); setHistoryFamily(''); setSelection(null); setFilter('');}} items={[
      {id: 'sequences', label: `经验版本 · ${currentTraces.length}`}, {id: 'history', label: `版本历史 · ${historicalTraces.length}`}, {id: 'recordings', label: `原始录制 · ${state.recordings.length}`},
      {id: 'tasks', label: `旧版状态图 · ${state.taskpacks.length}`}, {id: 'candidates', label: `反馈审查 · ${state.candidates.length}`} ]}/>}
    {message && <p className="notice" role="status">{message}</p>}
    <div className="library-toolbar"><Input aria-label="搜索经验" value={filter} placeholder="搜索名称、指令或说明" contentBefore={<Icon name="library" size={18}/>} onChange={(_, d) => setFilter(d.value)}/>
      <span className="quiet">{pending ? '正在处理…' : '新版本正式保存后成为当前版本；草稿不影响当前版本'}</span></div>
    {tab === 'history' && <div className="notice"><span>{historyFamily ? '这份经验的完整版本历史（含当前版本）' : '历史版本仅供查看；回滚后可用于后续任务。'}</span>
      {historyFamily && <Button appearance="subtle" onClick={() => {setHistoryFamily(''); setSelection(null); setPreview(null);}}>查看全部历史版本</Button>}</div>}
    {['recordings', 'candidates'].includes(tab) && <details className="surface compiler-options"><summary>编译与反馈模型</summary><CompilerChoice state={state} value={compiler} onChange={setCompiler} disabled={disabled}/><p className="quiet">Agent 编译 B 将动作与已选截图交给 Codex，返回 Markdown 方法。旧版状态图编译 / 反馈修订仍独立保留；A 与 D 都是本地确定性提取。</p></details>}
    {!['trash', 'history'].includes(tab) && batch.toolbar}
    {tab === 'trash' && <p className="notice">恢复不会覆盖同名资产或自动执行任务；永久删除须再次确认。运行记录与录制、经验分别管理，不会连带删除。运行记录归档包含事件与逐轮原文，不包含关联目录中的文件。</p>}
    {selection && <section className="surface detail-workspace"><div className="row-between"><div><span className="section-label">经验详情</span><h2>{candidate?.task_id || task?.task_id || preview?.trace.trace_name}</h2></div>
      <Button onClick={() => setSelection(null)}>返回列表</Button></div>
      {selection.kind === 'sequence' && preview && <><p className="quiet">{versionTime(preview.trace)}</p>
        <div className="button-row"><span className="tag">{currentTrace(preview.trace) ? '当前版本' : '历史版本'}</span>
          {preview.trace.versioning?.mode === 'independent' && <span className="tag">独立经验 · 版本历史单独管理</span>}
          {!!preview.trace.family_id && <Button onClick={() => showHistory(preview.trace)}>查看版本历史</Button>}
          {!currentTrace(preview.trace) && ['A', 'B', 'C', 'D'].includes(preview.trace.representation) &&
            <Button appearance="primary" disabled={disabled} onClick={() => void rollback().catch(e => onError(String(e)))}>回滚为当前版本</Button>}</div>
        {preview.trace.representation === 'R' && <div className="notice"><strong>R · 运行后改进候选 · 效果尚未评测</strong>
          <p>固定改进器第 {preview.input.evolution?.generation} 代；父版本 {preview.input.evolution?.parent_id}，源运行 {preview.input.evolution?.source_run_id}。</p>
          <p>这是独立实验条件，不是原始 Trace Compile B/C，也不代表改进器自身已改进。执行时仅发送下方候选方法。</p>
          <p>审查状态：{({pending_review: '待审查，禁止执行', approved_for_trial: '已允许试用，未证明改进', rejected: '已拒绝，禁止执行'} as const)[preview.input.review?.decision || 'pending_review']}</p>
          <details><summary>父方法全文（生成前）</summary><pre className="experience-preview">{preview.input.parent_content}</pre></details>
        </div>}
        {preview.trace.representation === 'B' && <div className="notice"><strong>Agent 方法 B · 执行时仅发送下方完整 Markdown Procedure</strong>
          <p>不附带编译提示词、原始动作或历史截图；不自动填入示范参数，不自动执行。请核对方法与完成判断后再选用。</p></div>}
        {preview.input.lineage && <div className="notice"><strong>反馈修订版 · 第 {preview.input.lineage.generation} 代</strong>
          <p>父版本 {preview.input.lineage.parent_id} · 源运行 {preview.input.lineage.source_run_id}。原方法保留；修订来自实际使用后的反馈，不等于效果提升。</p>
          <p className="quiet">实验条件：{preview.input.lineage.condition}{preview.input.revision_changed === false ? ' · 方法内容未变' : ''}</p>
          <details><summary>本次修订所依据的父方法 / 参考原文</summary><pre className="experience-preview">{preview.input.parent_content}</pre></details>
          {preview.input.revision_diff != null && <details><summary>与父方法的文本差异</summary><pre className="experience-preview">{preview.input.revision_diff || '无文本差异'}</pre></details>}
        </div>}
        {preview.trace.source_kind === 'agent_run' && <div className="notice"><strong>来自 Agent 运行 · Trace Compile</strong>
          <p>源运行 {preview.trace.source_run_id} · {preview.trace.source_scope}{preview.trace.source_package && ` · ${preview.trace.source_package}`}。保存代表人工核对，不代表独立验证或泛化成功。运行截图用于提炼与审阅，后续任务仅接收方法文本。</p></div>}
        {preview.trace.representation === 'C' && <div className="notice"><strong>组合证据 C · 同一 B 原文 + 对应 A 证据</strong>
          <p>来源 B：{String(preview.body.source_b_id || '')}。未重新编译 Procedure；执行发送下方完整组合文本与已选历史图片，不发送编译提示词或审计元数据。</p></div>}
        {['A', 'C'].includes(preview.trace.representation) && <div className="notice"><strong>模型输入 = 下方完整文本 + {preview.input.images?.length || 0} 张历史图片</strong><p>{preview.input.image_selection?.rule}</p>
          {preview.input.format === 'actions-with-images-v2' && <p>文本以 D 式动作序列为主，只引用选中的历史截图。原始事件、文件路径和校验信息仅供审计，不塞入模型文本。</p>}
          {preview.input.format === 'raw-evidence-v1' && <p>这是旧版 A，模型文本包含完整原始日志与截图索引。可回到原始录制重新生成精简格式的 A；此版本不会被自动改写。</p>}
          <p>仅这些历史图片作为经验附件发送；其余截图只在审计归档中保留。当前任务与实时截图由执行器另行提供，超出模型容量会报错，不静默删减这份经验。</p></div>}
        <h3 className="spaced">完整模型文本</h3><p className="quiet">{preview.input.formatting
          ? `执行时仅去除 JSON 排版空白：${preview.input.formatting.source_bytes.toLocaleString()} → ${preview.input.formatting.runtime_bytes.toLocaleString()} 字节。未摘要或截断，字符串、动作和方法正文不变。`
          : '直接读取编译版本中的 model-input.txt，未重新摘要或截断。'}
          {['A', 'C'].includes(preview.trace.representation) && <> <a href={traceAssetUrl(preview.trace.id, 'model-input.txt')} target="_blank" rel="noreferrer">单独打开归档原文</a></>}</p>
        <pre className="experience-preview">{preview.input.runtime_content ?? preview.input.content}</pre>
        {preview.input.formatting && <details className="data-disclosure"><summary>归档原文与输入哈希（历史文件未改写）</summary>
          <p className="quiet mono">归档 SHA256：{preview.input.formatting.source_sha256}<br/>实际输入 SHA256：{preview.input.formatting.runtime_sha256}</p>
          <pre className="experience-preview">{preview.input.content}</pre></details>}
        {['B', 'R'].includes(preview.trace.representation) && preview.input.compiler && <details className="data-disclosure">
          <summary>{preview.trace.representation === 'R' ? '改进器输入与执行证据（不发送给执行模型）' : 'Agent 编译输入与来源（不发送给执行模型）'}</summary>
          <p>{preview.input.compiler.model} · {preview.input.compiler.reasoning_effort} · {preview.input.compiler.prompt_version}</p>
          <p className="quiet">{preview.input.compiler.image_selection.rule}</p>
          <h3>本次使用的完整编译提示词</h3><pre className="experience-preview">{preview.input.compiler.instructions}</pre>
          <h3>{preview.trace.representation === 'R' ? '交给固定改进器的完整反馈投影' : '交给编译 Agent 的历史证据文本'}</h3><pre className="experience-preview">{preview.input.compiler.input}</pre>
          <h3>编译使用的历史图片 · {preview.input.compiler.images.length} 张</h3>
          <div className="evidence-gallery">{preview.input.compiler.images.map((picture, i) => <BundlePicture key={picture.id}
            traceId={preview.trace.id} picture={picture} label={`编译附件 ${i + 1} · 历史帧 ${picture.id}`}/>)}</div>
        </details>}
        {['A', 'C'].includes(preview.trace.representation) && <><h3 className="spaced">实际发送的历史图片 · {preview.input.images?.length || 0} 张</h3>
          <div className="evidence-gallery">{preview.input.images?.map((picture, i) => <BundlePicture key={picture.id} traceId={preview.trace.id} picture={picture} label={`附件 ${i + 1} · 历史帧 ${picture.id}`}/>)}</div>
          <details className="data-disclosure"><summary>完整证据归档与未发送图片（不额外发送给模型）</summary>
            <p className="quiet">这些文件是原始字节副本，仅供核对来源。是否进入模型输入以「完整模型文本」和「实际发送的历史图片」为准；这里的归档不会额外发送。</p>
            <div className="evidence-file-list">{preview.body.source_files?.map(file => <a key={file} href={traceAssetUrl(preview.trace.id, file)} target="_blank" rel="noreferrer">{file}</a>)}</div>
            <ul className="evidence-index">{preview.body.frames?.map(picture => <li key={picture.id}><span>历史帧 {picture.id} · {picture.selected ? '已发送' : picture.file ? '仅归档，未发送' : '缺图，未发送'}</span>
              {picture.file && <a href={traceAssetUrl(preview.trace.id, picture.file)} target="_blank" rel="noreferrer">查看原图</a>}</li>)}</ul>
          </details></>}
        <JsonDetails label={['A', 'B', 'C', 'R'].includes(preview.trace.representation) ? '审计元数据与完整索引（不额外发送给模型）' : '结构化来源与证据'} value={preview.body}/>
        {preview.trace.representation === 'R' && preview.input.review?.decision === 'pending_review' && <div className="button-row">
          <Button disabled={disabled} onClick={() => void reviewImprovement('approved_for_trial')}>审查后允许试用</Button>
          <Button disabled={disabled} onClick={() => void reviewImprovement('rejected')}>拒绝候选</Button></div>}
        <Button appearance="primary" disabled={!usableTrace(preview.trace)} onClick={() => onUse(preview.trace)}>用于新任务</Button></>}
      {task && <TaskDetail task={task}/>}
      {candidate && <CandidateEditor key={candidate.local_path} candidate={candidate} compiler={compiler} disabled={disabled} onError={onError}
        onChange={async (endpoint, extra, job, confirmation) => change(endpoint, {path: candidate.local_path, ...extra}, confirmation, job)}/>}
    </section>}
    <div className="experience-grid">
      {['sequences', 'history'].includes(tab) && visibleTraces.map(trace => <article className="surface experience-card" key={trace.id}>
        {tab === 'sequences' && batch.checkbox(trace.id, trace.trace_name)}
        <div className="row-between"><Icon name="library" size={25}/><span className="tag">{trace.representation === 'D' ? '可执行参考 · 序列 D' : trace.representation === 'A' ? '可执行参考 · 原始证据 A' : trace.representation === 'B' ? '可执行参考 · Agent 方法 B' : trace.representation === 'C' ? '可执行参考 · 组合证据 C' : trace.representation === 'R' ? `实验候选 R · ${trace.review_status === 'approved_for_trial' ? '已允许试用' : trace.review_status === 'rejected' ? '已拒绝' : '待审查'}` : `${trace.representation} · 只读来源`}</span></div>
        <h2>{trace.trace_name}</h2><p className="quiet version-time">{versionTime(trace)}</p><p>{trace.description}</p><div className="experience-meta mono">{trace.id}</div>
        <p className="quiet">{currentTrace(trace) ? '当前版本' : '历史版本'}{trace.version_count ? ` · 共 ${trace.version_count} 个版本` : ''}</p>
        {trace.versioning?.mode === 'independent' && <p className="quiet">独立经验 · 保留来源关系</p>}
        {trace.lineage && <p className="quiet">反馈修订 {trace.lineage.generation} · 父版本 {trace.lineage.parent_id.slice(0, 8)}{trace.revision_changed === false ? ' · 方法未变' : ''}</p>}
        {trace.assisted_by_user && <p className="quiet">人工辅助来源 · 非纯自主执行结果</p>}
        <div className="button-row"><Button disabled={!!pending} onClick={() => void inspect(trace)}>查看模型原文</Button><Button appearance="primary" disabled={!usableTrace(trace)} onClick={() => onUse(trace)}>用于新任务</Button>
          {tab === 'sequences' && (trace.version_count || 0) > 1 && <Button onClick={() => showHistory(trace)}>查看版本历史</Button>}
          {trace.representation === 'B' && trace.source_kind !== 'agent_run' && <Button disabled={disabled} onClick={() => void combine(trace)}>生成组合证据 C</Button>}
          </div></article>)}
      {tab === 'recordings' && state.recordings.filter(r => matches(r.task_id || r.trace_path)).map(record => <article className="surface experience-card" key={record.trace_path}>
        {batch.checkbox(record.trace_path, record.task_id || record.trace_path)}
        <div className="row-between"><Icon name="record" size={25}/><span className="tag">{record.success ? '已完成' : '未完成'} · {record.recording_backend === 'opencua' ? 'OpenCUA' : '人类示范'}</span></div>
        <h2>{record.task_id || '未命名录制'}</h2><p>{record.input_events} 个输入事件 · {record.created_at ? new Date(record.created_at).toLocaleString('zh-CN') : '时间未记录'}</p>
        {record.derivation && <p>动作整理：{record.derivation.status} · {record.derivation.action_count ?? '—'} 组</p>}
        {record.narrated && <p>含人类讲解 · {record.narration_chars || 0} 字</p>}
        {record.recording_backend === 'opencua' && <p className="quiet">A：动作序列与最多 8 张历史截图。D：无图精简动作序列。B：Agent 编译 Markdown 方法。C：在「经验版本」选定 B，点击「生成组合证据 C」，复用其原文与对应 A 证据，不再调用模型。</p>}
        <div className="button-row spaced"><Button appearance="primary" disabled={disabled || record.recording_backend !== 'opencua' || record.derivation?.status !== 'completed'}
          onClick={() => void compile(record)}>生成精简序列 D</Button>
          <Button disabled={disabled || record.recording_backend !== 'opencua' || record.derivation?.status !== 'completed'} onClick={() => void compile(record, 'A')}>生成原始证据 A</Button>
          {record.recording_backend === 'opencua' ? <Button disabled={disabled || !record.success || record.derivation?.status !== 'completed'}
            onClick={() => void compile(record, 'B')}>Agent 编译方法 B</Button>
            : <Button disabled={disabled || !record.success || record.compilation_supported === false} onClick={() => void compile(record, 'semantic')}>语义编译</Button>}</div>
        <div className="button-row"><Button appearance="subtle" disabled={!!pending} onClick={() => void openLocal(record.local_path)}>打开原始归档</Button>
          </div></article>)}
      {tab === 'tasks' && state.taskpacks.filter(t => matches(`${t.task_id} ${t.instruction}`)).map(item => <article className="surface experience-card" key={item.path}>
        {batch.checkbox(item.path, item.task_id)}
        <div className="row-between"><Icon name="library" size={25}/><span className="tag">Trace Compile · {item.confirmed ? '已审查' : '待审查'}</span></div>
        <h2>{item.task_id}</h2><p>{item.semantic_experience?.summary || item.instruction}</p><p>状态图 v{item.semantic_experience?.revision || 0} · 人工反馈 v{item.human_guidance?.revision || 0}</p>
        <p className="quiet">保留的状态图经验，不混入当前序列 D 执行入口。</p>
        <div className="button-row spaced"><Button onClick={() => setSelection({kind: 'task', id: item.path})}>阶段、规则与历史</Button>
          <Button disabled={disabled || item.confirmed} onClick={() => void change('/api/taskpacks/confirm', {task_path: item.path}, `确认已审查“${item.task_id}”的阶段、允许动作和成功证据？仅记录审查状态，不改写经验内容。`)}>确认已审查</Button>
          {!!item.missing_message_capabilities?.length && <Button disabled={disabled} onClick={() => void change('/api/taskpacks/upgrade', {task_path: item.path}, '补充文本输入能力并将该任务标记为待确认草稿？')}>升级文本能力</Button>}</div>
        <div className="button-row"><Button appearance="subtle" disabled={!!pending} onClick={() => void openLocal(item.local_path)}>打开归档</Button>
          {item.human_guidance && <Button appearance="subtle" disabled={disabled} onClick={() => void remove('/api/taskpacks/guidance/delete', {task_path: item.path}, '只将当前人工反馈及其历史移到 taskpacks/.trash/guidance？可以恢复；Trace、Compiler 与状态图保留。')}>移除人工反馈</Button>}
          </div></article>)}
      {tab === 'candidates' && state.candidates.filter(c => matches(`${c.task_id} ${c.instruction || ''}`)).map(item => <article className="surface experience-card" key={item.local_path}>
        {batch.checkbox(item.local_path, item.task_id)}
        <div className="row-between"><Icon name="history" size={25}/><span className="tag">Feedback Revision · {item.revision?.status || item.status}</span></div>
        <h2>{item.task_id}</h2><p>{item.instruction || '查看实际运行证据，再提出修订意见。'}</p>
        <div className="button-row spaced"><Button appearance="primary" onClick={() => setSelection({kind: 'candidate', id: item.local_path})}>审查与反馈</Button>
          <Button appearance="subtle" disabled={!!pending} onClick={() => void openLocal(item.local_path)}>打开归档</Button>
          </div></article>)}
      {tab === 'trash' && trash.filter(item => matches(`${item.name} ${item.original_path || ''}`)).map(item => <article className="surface experience-card" key={item.path}>
        <div className="row-between"><Icon name="history" size={25}/><span className="tag">{({run: '运行记录', recording: '原始录制', trace_experience: '经验版本', taskpack: '任务经验', candidate: '候选反馈', human_guidance: '人工反馈'}[item.kind] || '归档')} · 回收站</span></div>
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
    {({sequences: currentTraces.length, history: visibleTraces.length, recordings: state.recordings.length, tasks: state.taskpacks.length, candidates: state.candidates.length, trash: trash.length}[tab] || 0) === 0 &&
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
