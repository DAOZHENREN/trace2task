import {useCallback, useEffect, useRef, useState} from 'react';
import {Button, Spinner} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import type {AppState, Catalog, Config, Page, Run} from './types';
import {active, labels} from './types';
import {TaskComposer, host, isLocal} from './TaskComposer';
import {RunInspector} from './RunInspector';
import {Models} from './Models';
import {Empty, Icon, PageHeading, Refresh} from './ui';
import {Library} from './Library';
import {RecordingPanel} from './RecordingPanel';
import {Settings} from './Settings';
import {Experiments} from './Experiments';
import {useTaskSound} from './useTaskSound';

const navigation: {id: Page; label: string; group: string}[] = [
  {id: 'tasks', label: '任务工作台', group: '工作空间'}, {id: 'record', label: '录制示范', group: '工作空间'},
  {id: 'library', label: '经验库', group: '工作空间'}, {id: 'history', label: '运行记录', group: '工作空间'},
  {id: 'models', label: '模型与连接', group: '管理'}, {id: 'settings', label: '设置与工具', group: '管理'},
  {id: 'experiments', label: '实验室', group: '探索'},
];
const initialConfig: Config = {provider: 'codex', model: '', localModel: 'gui-owl-2b', inference: 'llama-server',
  executor: 'win32', scope: 'desktop', experience: '', effort: 'low', apiUrl: 'https://api.openai.com/v1',
  apiModel: '', apiKey: '', apiFormat: 'json_schema', apiBudget: 32768, apiTimeout: 120, targets: [], initialTarget: 0};
const isRecording = (run: Run) => ['recording', 'waa_recording'].includes(run.kind);

export function App() {
  const confirm = useConfirm();
  const [page, setPage] = useState<Page>('tasks');
  const [state, setState] = useState<AppState | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [config, setConfig] = useState(initialConfig);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(true);
  const [stopping, setStopping] = useState(false);
  const [visited, setVisited] = useState(new Set<Page>(['tasks']));
  const [recordId, setRecordId] = useState('');
  const [settingsSection, setSettingsSection] = useState('preferences');
  const [promptDrafts, setPromptDrafts] = useState<Record<string, boolean>>({});
  const onPromptDirty = useCallback((key: string, dirty: boolean) => setPromptDrafts(old => ({...old, [key]: dirty})), []);
  const sound = useTaskSound(runs);
  const runStatuses = useRef(new Map<string, string>());
  const refreshCatalog = useCallback(async () => setCatalog(await api<Catalog>('/api/runtime-catalog')), []);
  const refresh = useCallback(async () => {
    const [next, history] = await Promise.all([api<AppState>('/api/state?compact=1'), api<{runs: Run[]}>('/api/workbench/runs')]);
    setState(next); setRuns(history.runs);
    setConfig(current => current.experience.startsWith('trace-library/') &&
      !next.trace_representations.some(t => current.experience === `trace-library/${t.id}/model-input.txt`)
      ? {...current, experience: ''} : current);
  }, []);
  const initialize = useCallback(async () => {
    setStarting(true); setError('');
    try {
      const [next, cat, history] = await Promise.all([api<AppState>('/api/state?compact=1'), api<Catalog>('/api/runtime-catalog'),
        api<{runs: Run[]}>('/api/workbench/runs')]);
      setState(next); setCatalog(cat); setRuns(history.runs);
      const saved = next.agent_options.api_defaults.saved_settings;
      setConfig(c => ({...c, model: next.agent_options.defaults.model, effort: next.agent_options.defaults.reasoning_effort,
        apiUrl: saved?.base_url || c.apiUrl, apiModel: saved?.model || c.apiModel,
        apiFormat: saved?.response_format || c.apiFormat, apiTimeout: saved?.timeout_seconds || c.apiTimeout,
        apiBudget: saved?.context_window_tokens || c.apiBudget, inference: cat.configured_backend || c.inference}));
      if (active(next.active_job)) {
        if (isRecording(next.active_job!)) {setRecordId(next.active_job!.job_id); setPage('record');}
        else setSelectedRun(next.active_job!.job_id);
      }
    } catch (e) {setError(String(e));} finally {setStarting(false);}
  }, []);
  useEffect(() => {void initialize();}, [initialize]);
  useEffect(() => {
    if (!state) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<{runs: Run[]}>('/api/workbench/runs', undefined, abort.signal); setRuns(result.runs);
        const finished = result.runs.some(run => {const old = runStatuses.current.get(run.job_id); return old && active({...run, status: old}) && !active(run);});
        runStatuses.current = new Map(result.runs.map(run => [run.job_id, run.status]));
        if (finished) setState(await api<AppState>('/api/state?compact=1', undefined, abort.signal));
      }
      catch (e) {if (!abort.signal.aborted) setError(`运行状态暂不可用：${String(e)}`);}
      if (!abort.signal.aborted) timer = setTimeout(poll, 1500);
    }
    timer = setTimeout(poll, 1500); return () => {abort.abort(); clearTimeout(timer);};
  }, [!!state]);
  const liveRun = runs.find(active);
  const busy = !!liveRun;
  const recording = liveRun && isRecording(liveRun) ? liveRun : runs.find(run => run.job_id === recordId) || runs.find(isRecording);
  const go = (next: Page) => {setPage(next); setError(''); setVisited(old => new Set([...old, next]));
    if (['library', 'history'].includes(next)) void refresh().catch(e => setError(String(e)));};
  function viewRun(run: Run) {
    if (isRecording(run)) {setRecordId(run.job_id); go('record');} else {setSelectedRun(run.job_id); go('tasks');}
  }
  const onRun = (run: Run) => {runStatuses.current.set(run.job_id, run.status); setRuns(old => [run, ...old.filter(r => r.job_id !== run.job_id)]); viewRun(run);};
  async function stop() {
    if (!liveRun) return;
    if (liveRun.status === 'awaiting_narration' && !await confirm({title: '放弃讲解并停止编译？', body: '原始 Trace 保留，临时录音会删除。', confirmLabel: '放弃并停止', intent: 'danger'})) return;
    setStopping(true);
    try {const run = await api<Run>(`/api/jobs/${liveRun.job_id}/stop`, {}); setRuns(old => old.map(r => r.job_id === run.job_id ? run : r));}
    catch (e) {setError(String(e));} finally {setStopping(false);}
  }
  const flow = liveRun?.run_facts;
  const promptKey = config.provider === 'local'
    ? `${config.localModel}:${config.executor}` : config.provider === 'codex' ? 'codex' : 'api';
  const activityDestination = liveRun && !flow ? (isRecording(liveRun)
    ? liveRun.defer_compilation ? '本地录制' : '本地录制 → Codex 编译'
    : ['compilation', 'revision', 'task_model_revision'].includes(liveRun.kind) ? 'Codex 编译 / 修订' : '') : '';
  const local = flow ? flow.data_flow === 'local' : activityDestination ? activityDestination === '本地录制'
    : config.provider === 'local' || config.provider === 'api' && isLocal(config.apiUrl);
  const destination = flow?.destination || activityDestination || (config.provider === 'codex' ? 'Codex 订阅' : config.provider === 'local' ? '本机模型' : host(config.apiUrl));
  return <div className="app-shell">
    <a className="skip-link" href="#main">跳到工作区</a>
    <aside className="sidebar"><a className="brand" href="#" onClick={e => {e.preventDefault(); go('tasks');}} aria-label="Trace2Task 工作台">
      <span className="brand-symbol"><span/><span/><span/></span><span>Trace2Task<small>EXPERIENCE IN MOTION</small></span></a>
      <nav aria-label="主导航">{['工作空间', '管理', '探索'].map(group => <div className="nav-group" key={group}><div className="nav-label">{group}</div>
        {navigation.filter(n => n.group === group).map(n => <button key={n.id} aria-current={page === n.id ? 'page' : undefined}
          className={page === n.id ? 'active' : ''} onClick={() => go(n.id)}><Icon name={n.id}/><span>{n.label}</span>{n.id === 'tasks' && busy && <i/>}</button>)}</div>)}</nav>
      <div className="sidebar-foot"><span className="small-mark">T2</span><div>桌面工作台<small>Python + WebView2 · v{state?.version || '—'}</small></div></div>
    </aside>
    <div className="main-shell"><header className="app-header"><div className="breadcrumb">{navigation.find(n => n.id === page)?.group} <span>/</span> <strong>{navigation.find(n => n.id === page)?.label}</strong></div>
      <div className="header-actions"><span className={`flow-pill ${local ? 'local' : 'remote'}`}><span/>{busy ? '本次运行' : '待执行'} · {destination}</span>
        {busy ? <Button className="global-stop" appearance="primary" icon={<Icon name="stop" size={16}/>} disabled={stopping || liveRun.stop_requested} onClick={() => void stop()}>
          {stopping || liveRun.stop_requested ? '等待安全停止…' : '停止任务'} <kbd>F9</kbd></Button> : <span className="ready-label"><span/>待命</span>}</div></header>
      <main id="main" tabIndex={-1}>
        {error && <div className="error-banner" role="alert"><span>{error}</span><Button appearance="transparent" size="small" onClick={() => setError('')}>关闭</Button></div>}
        {starting ? <div className="loading"><Spinner label="正在连接本地工作台…"/></div> : !state || !catalog ? <Empty title="暂时无法连接" text="本地服务没有返回完整状态。请重试；不会启动或恢复任何任务。"
          action={<Button onClick={() => void initialize()}>重新连接</Button>}/> : <>
          <div hidden={page !== 'tasks'}><PageHeading eyebrow="YOUR WORKSPACE" title="把目标交给 Agent。" copy="用一次示范积累经验，用当前画面决定下一步。"
            action={selectedRun && !busy ? <Button icon={<Icon name="plus" size={17}/>} onClick={() => setSelectedRun(null)}>新任务</Button> : undefined}/>
            <TaskComposer config={config} setConfig={setConfig} state={state} catalog={catalog} busy={busy} promptDirty={!!promptDrafts[promptKey]} onRun={onRun} onModels={() => go('models')} onError={setError}/>
            <RunInspector id={selectedRun}/></div>
          <div hidden={page !== 'models'}>{visited.has('models') && <Models catalog={catalog} config={config} setConfig={setConfig} busy={busy} refresh={refreshCatalog}
            onError={setError} onComponents={() => {setSettingsSection('components'); go('settings');}}/>}</div>
          <div hidden={page !== 'library'}>{visited.has('library') && <Library state={state} busy={busy} refresh={refresh} onRun={onRun} onError={setError} onUse={trace => {
            setConfig(c => ({...c, experience: `trace-library/${trace.id}/model-input.txt`})); go('tasks');}}
            onRecord={() => go('record')}/>}</div>
          {page === 'history' && <><PageHeading eyebrow="RUN HISTORY" title="每一步，都可以回看。" copy="真实写入本地的运行摘要、事件和逐轮原文；重启后仍可读取，中断任务不会自动重放。"
            action={<Refresh onClick={() => void refresh().catch(e => setError(String(e)))}/>}/>
            <section className="surface history-list">{runs.length ? runs.map(r => <button className="history-row" key={r.job_id} onClick={() => viewRun(r)}>
              <div className="history-symbol"><Icon name={isRecording(r) ? 'record' : 'tasks'}/></div><div><h3>{r.instruction || r.task_id}</h3>
                <p>{r.model} · {r.run_facts?.inference_backend || r.kind} · {new Date(r.created_at).toLocaleString('zh-CN')}</p></div>
              <span className="status-tag">{labels[r.status] || r.status}</span><Icon name="arrow" size={18}/></button>)
              : <Empty icon="history" title="从下一次运行开始积累" text="新工作台会自动登记后续任务。迁移前的原始运行日志仍保留在本地，没有自动改写。"/>}</section></>}
          <div hidden={page !== 'record'}><RecordingPanel state={state} run={recording} busy={busy} onRun={onRun} onLibrary={() => go('library')} onError={setError}/></div>
          <div hidden={page !== 'settings'}>{visited.has('settings') && <Settings state={state} catalog={catalog} config={config} setConfig={setConfig} busy={busy} section={settingsSection} setSection={setSettingsSection}
            visible={page === 'settings'} onRun={onRun} onError={setError} sound={sound.enabled} setSound={sound.set} playSound={sound.play} onPromptDirty={onPromptDirty}/>}</div>
          <div hidden={page !== 'experiments'}>{visited.has('experiments') && <Experiments visible={page === 'experiments'} onError={setError}/>}</div>
        </>}
      </main><footer className="app-footer"><span>经验是参考，当前观察是依据。</span><span>原始记录保留 · 无外部遥测</span></footer>
    </div>
  </div>;
}
