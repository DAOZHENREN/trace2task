import {useEffect, useState} from 'react';
import {Button, Checkbox, Input, Textarea} from '@fluentui/react-components';
import {api, requestFor} from './api';
import {useConfirm} from './ConfirmDialog';
import type {AppState, Catalog, Config, Run} from './types';
import {Choice, Empty, Field, PageHeading, Refresh} from './ui';
import {Sections, useAction} from './workflow';

export function Settings({state, catalog, config, setConfig, busy, section, setSection, visible, onRun, onError, sound, setSound, playSound, onPromptDirty}: {
  state: AppState; catalog: Catalog; config: Config; setConfig: (next: Config) => void; busy: boolean;
  section: string; setSection: (tab: string) => void; visible: boolean; onRun: (run: Run) => void; onError: (text: string) => void;
  sound: boolean; setSound: (value: boolean) => void; playSound: () => void; onPromptDirty: (key: string, dirty: boolean) => void}) {
  return <>
    <PageHeading eyebrow="PREFERENCES" title="保持控制，也保留细节。" copy="执行偏好、提示词、模型环境与恢复工具，使用同一套工作台。"/>
    <Sections label="设置分类" value={section} onChange={setSection} items={[{id: 'preferences', label: '执行偏好'}, {id: 'prompts', label: '模型提示词'},
      {id: 'components', label: '组件与模型文件'}, {id: 'recovery', label: '任务恢复'}]}/>
    <div hidden={section !== 'preferences'} className="settings-grid"><section className="surface setting-card"><h2>执行偏好</h2><p>设置只影响下一次任务；运行中的配置已经固定。</p>
      <Field label="Codex 思考强度"><Choice value={config.effort} disabled={busy} onChange={e => setConfig({...config, effort: e.target.value})}>{state.agent_options.reasoning_efforts.map(e => <option key={e}>{e}</option>)}</Choice></Field>
      <div className="button-row spaced"><Checkbox label="任务结束提示音" checked={sound} onChange={(_, d) => setSound(!!d.checked)}/><Button onClick={playSound}>试听</Button></div></section>
      <section className="surface setting-card"><h2>本地记录与隐私</h2><p>新任务的摘要、增量事件和逐轮原文写入本地 runs/workbench.sqlite3。重启后可以回看，不会自动恢复操作。</p><p>既有日志、经验、反馈和权重继续保留；不启用外部遥测，也没有自动清理。原始请求可能包含截图和敏感文字，请按需查看。</p></section></div>
    <div hidden={section !== 'prompts'}><PromptEditor catalog={catalog} busy={busy} onError={onError} visible={visible && section === 'prompts'} onDirty={onPromptDirty}/></div>
    <div hidden={section !== 'components'}><Components catalog={catalog} busy={busy} onError={onError} visible={visible && section === 'components'}/></div>
    <div hidden={section !== 'recovery'}><Recovery config={config} busy={busy} onError={onError} onRun={onRun} visible={visible && section === 'recovery'}/></div>
  </>;
}

type Prompt = {guidance?: string; customized?: boolean; effective?: {system_prompt: string; turn_template: string}};
function PromptEditor({catalog, busy, visible, onError, onDirty}: {catalog: Catalog; busy: boolean; visible: boolean; onError: (text: string) => void; onDirty: (key: string, dirty: boolean) => void}) {
  const confirm = useConfirm();
  const [target, setTarget] = useState('codex'); const [backend, setBackend] = useState('win32');
  const [guidance, setGuidance] = useState(''); const [system, setSystem] = useState(''); const [template, setTemplate] = useState('');
  const [loaded, setLoaded] = useState(''); const [dirty, setDirty] = useState(false); const [message, setMessage] = useState('');
  const {pending, perform} = useAction(onError); const local = !['codex', 'api'].includes(target);
  const key = local ? `${target}:${backend}` : target;
  function changeDirty(value: boolean) {setDirty(value); onDirty(key, value);}
  function apply(profile: Prompt) {setGuidance(profile.guidance || ''); setSystem(profile.effective?.system_prompt || ''); setTemplate(profile.effective?.turn_template || ''); changeDirty(false);}
  useEffect(() => {
    if (!visible || loaded === key) return;
    const abort = new AbortController(); setMessage('正在读取提示词…');
    api<Prompt>(local ? `/api/local-prompts?model=${encodeURIComponent(target)}&backend=${backend}` : `/api/chat-prompts?provider=${target}`, undefined, abort.signal)
      .then(profile => {apply(profile); setLoaded(key); setMessage(profile.customized ? '当前使用自定义提示词。' : '当前使用默认提示词。');})
      .catch(e => {if (!abort.signal.aborted) onError(String(e));});
    return () => abort.abort();
  }, [visible, key]);
  async function select(value: string, execution = false) {
    if (dirty && !await confirm({title: '放弃未保存的修改？', body: '当前提示词有未保存的修改。切换后这些修改不会保留。', confirmLabel: '放弃并切换', intent: 'danger'})) return;
    changeDirty(false); setLoaded(''); if (execution) setBackend(value); else setTarget(value);
  }
  async function save(reset = false) {
    if (reset && !await confirm({title: '恢复默认提示词？', body: '恢复该模型 / 后端的默认提示词。其他配置和正在运行的任务不变。', confirmLabel: '恢复默认'})) return;
    await perform('save', async () => {const profile = await api<Prompt>(local ? '/api/local-prompts' : '/api/chat-prompts', local
      ? {model: target, backend, profile: reset ? null : {system_prompt: system, turn_template: template}}
      : {provider: target, guidance: reset ? '' : guidance}); apply(profile); setMessage('已保存，下一次任务生效。');});
  }
  return <section className="surface workflow-card"><h2>模型提示词</h2><p className="quiet">本地保存，不要写入密钥。不会修改固定安全约束、动作校验或运行中的任务。</p>
    <div className="two-fields spaced"><Field label="提示词对象"><Choice value={target} disabled={busy || !!pending} onChange={e => select(e.target.value)}><option value="codex">Codex 执行指导</option><option value="api">视觉 API 执行指导</option>
      {catalog.models.map(p => <option value={p.id} key={p.id}>{p.label}</option>)}</Choice></Field>{local && <Field label="提示词执行后端"><Choice value={backend} disabled={busy || !!pending} onChange={e => select(e.target.value, true)}><option value="win32">Win32</option><option value="cua">Cua</option></Choice></Field>}</div>
    {target === 'codex' && <p className="notice">这里编辑本程序发送的执行指导，不是 Codex 内置系统提示词。</p>}
    {local ? <><Field label="系统提示词"><Textarea rows={10} resize="vertical" spellCheck={false} value={system} disabled={busy || !!pending || loaded !== key} onChange={(_, d) => {setSystem(d.value); changeDirty(true);}}/></Field>
      <Field label="每轮任务模板" hint="必须保留 {{task}}、{{history}}、{{experience_block}}、{{execution_feedback_block}}。执行范围仍由程序校验。"><Textarea rows={7} resize="vertical" spellCheck={false} value={template} disabled={busy || !!pending || loaded !== key} onChange={(_, d) => {setTemplate(d.value); changeDirty(true);}}/></Field></>
      : <Field label="自定义执行指导"><Textarea rows={8} resize="vertical" maxLength={4000} value={guidance} disabled={busy || !!pending || loaded !== key} onChange={(_, d) => {setGuidance(d.value); changeDirty(true);}}/></Field>}
    <div className="action-footer"><span className="quiet" role="status">{dirty ? '有未保存修改；切换工作台页面会保留草稿。' : message}</span><div className="button-row">
      <Button disabled={busy || !!pending || loaded !== key} onClick={() => void save(true)}>恢复默认</Button><Button appearance="primary" disabled={busy || !!pending || loaded !== key || !dirty} onClick={() => void save()}>保存提示词</Button></div></div></section>;
}

type ComponentsStatus = {status: string; message: string; default_directory: string; python: string; models: string; log?: string};
function Components({catalog, busy, visible, onError}: {catalog: Catalog; busy: boolean; visible: boolean; onError: (text: string) => void}) {
  const confirm = useConfirm();
  const [status, setStatus] = useState<ComponentsStatus | null>(null); const [directory, setDirectory] = useState('');
  const [bundle, setBundle] = useState(''); const [model, setModel] = useState(catalog.models[0]?.id || '');
  const {pending, perform} = useAction(onError);
  async function refresh(signal?: AbortSignal) {const next = await api<ComponentsStatus>('/api/components', undefined, signal); setStatus(next); setDirectory(value => value || next.default_directory);}
  useEffect(() => {
    if (!visible) return; const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {try {await refresh(abort.signal);} catch (e) {if (!abort.signal.aborted) onError(String(e));}
      if (!abort.signal.aborted) timer = setTimeout(poll, 2000);}
    void poll(); return () => {abort.abort(); clearTimeout(timer);};
  }, [visible]);
  async function action(name: string) {
    const prompt = name === 'runtime' ? '下载并安装独立 GPU 环境？可能占用十余 GB。不会修改系统 Python 或删除已有模型。'
      : name === 'model' ? `从 Hugging Face 下载 ${model}？需要数 GB 空间和网络流量，已有模型不会被自动删除。`
      : '校验并登记已有 D 模型包？不会修改包内文件。';
    if (name !== 'cancel' && !await confirm({title: name === 'import_d' ? '登记已有模型？' : '下载并安装组件？', body: prompt, confirmLabel: name === 'import_d' ? '校验并登记' : '开始下载'})) return;
    await perform(name, async () => {await api('/api/components', {action: name, directory: name === 'import_d' ? bundle : directory, model}); await refresh();});
  }
  const disabled = busy || !!pending || status?.status === 'running' || !status;
  return <section className="surface workflow-card"><div className="row-between"><h2>组件与模型文件</h2><Refresh busy={!!pending} onClick={() => void refresh().catch(e => onError(String(e)))}/></div>
    <p className="notice" role="status">{status ? `${status.status} · ${status.message}` : '等待组件状态'}</p>
    <div className="two-fields"><Field label="组件安装目录"><Input value={directory} disabled={disabled} onChange={(_, d) => setDirectory(d.value)}/></Field>
      <Field label="下载模型"><Choice value={model} disabled={disabled} onChange={e => setModel(e.target.value)}>{catalog.models.map(p => <option value={p.id} key={p.id}>{p.label}</option>)}</Choice></Field></div>
    <div className="button-row"><Button disabled={disabled || !directory} onClick={() => void action('runtime')}>安装 GPU 环境</Button><Button appearance="primary" disabled={disabled || !directory || !model} onClick={() => void action('model')}>下载所选模型</Button>
      <Button disabled={!!pending || status?.status !== 'running'} onClick={() => void action('cancel')}>取消安装 / 下载</Button></div>
    <div className="revision-block"><h3>登记已有 D 模型包</h3><Field label="D 模型包目录"><Input value={bundle} disabled={disabled} onChange={(_, d) => setBundle(d.value)}/></Field>
      <Button className="spaced" disabled={disabled || !bundle.trim()} onClick={() => void action('import_d')}>校验并登记</Button></div>
    <dl className="facts-list"><dt>Python</dt><dd>{status?.python || '—'}</dd><dt>模型目录</dt><dd>{status?.models || '—'}</dd></dl>
    <details className="data-disclosure"><summary>安装日志</summary><pre>{status?.log || '尚无安装日志'}</pre></details></section>;
}

type Checkpoint = {path: string; name: string; instruction: string; uncertain: boolean};
function Recovery({config, busy, visible, onRun, onError}: {config: Config; busy: boolean; visible: boolean; onRun: (run: Run) => void; onError: (text: string) => void}) {
  const confirm = useConfirm();
  const [items, setItems] = useState<Checkpoint[]>([]); const {pending, perform} = useAction(onError);
  async function refresh(signal?: AbortSignal) {setItems((await api<{runs: Checkpoint[]}>('/api/desktop-checkpoints', undefined, signal)).runs);}
  useEffect(() => {if (!visible) return; const abort = new AbortController(); void refresh(abort.signal).catch(e => {if (!abort.signal.aborted) onError(String(e));}); return () => abort.abort();}, [visible]);
  const compatible = config.provider !== 'local' && config.executor === 'win32' && config.scope === 'desktop' && !config.experience;
  async function resume(item: Checkpoint) {
    if (!compatible) return;
    if (!await confirm({title: '恢复这个任务？', body: `“${item.instruction}”\n将使用当前所选模型 ${config.provider === 'codex' ? config.model : config.apiModel}，重新控制主显示器。${item.uncertain ? '\n有未确认的动作边界，程序会先重新观察，不盲目重放。' : ''}`, confirmLabel: '确认恢复', note: '请核对目标应用并关闭敏感内容。按 F9 停止。'})) return;
    await perform(item.path, async () => onRun(await api<Run>('/api/jobs', {...requestFor(config, item.instruction), orchestration: 'langgraph', resume_from: item.path})));
  }
  return <section className="surface workflow-card"><div className="row-between"><h2>显式恢复未完成任务</h2><Refresh busy={!!pending} onClick={() => void refresh().catch(e => onError(String(e)))}/></div>
    <p className="quiet">只列出已有 LangGraph 检查点。运行记录本身不是可重放脚本，打开此页不会触发任何操作。</p>
    {!compatible && <p className="inline-warning">恢复入口需要任务工作台选择 Codex / API、Win32、全桌面、无序列经验；不会替你扩大授权范围。</p>}
    {items.map(item => <article className="checkpoint-row" key={item.path}><div><h3>{item.instruction}</h3><p className="quiet">{item.name} · {item.uncertain ? '存在待核对动作' : '已保存检查点'}</p></div><Button disabled={busy || !!pending || !compatible} onClick={() => void resume(item)}>确认后恢复</Button></article>)}
    {!items.length && <Empty icon="history" title="没有待恢复检查点" text="普通运行记录仍可在历史页面回看；不会自动生成或重放任务。"/>}</section>;
}
