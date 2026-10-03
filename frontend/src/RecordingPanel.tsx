import {useEffect, useRef, useState} from 'react';
import {Button, Checkbox, Input, Textarea} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import {audioPayload, captureAudio, dictationActive, finishAudio, releaseAudio} from './audio';
import type {Capture} from './audio';
import type {AppState, ModelChoice, Run} from './types';
import {active, labels} from './types';
import {Choice, Field, Icon, PageHeading} from './ui';
import {CompilerChoice, useAction} from './workflow';
import {RunInspector} from './RunInspector';

type WaaTask = {id: string; example_path: string; instruction: string; domain: string; reset_paths: string[]};
export function RecordingPanel({state, run, busy, onRun, onLibrary, onError}: {state: AppState; run?: Run;
  busy: boolean; onRun: (run: Run) => void; onLibrary: () => void; onError: (text: string) => void}) {
  const confirm = useConfirm();
  const [source, setSource] = useState('opencua'); const [name, setName] = useState('');
  const [root, setRoot] = useState(state.agent_options.waa_defaults?.root || '');
  const [tasks, setTasks] = useState<WaaTask[]>([]); const [example, setExample] = useState('');
  const [narrated, setNarrated] = useState(true); const [defer, setDefer] = useState(true);
  const [compiler, setCompiler] = useState<ModelChoice>(state.agent_options.compiler_defaults || state.agent_options.defaults);
  const [transcript, setTranscript] = useState(''); const [audioStatus, setAudioStatus] = useState('');
  const [preparing, setPreparing] = useState(false); const [detail, setDetail] = useState<Run | null>(null);
  const capture = useRef<Capture | null>(null); const archived = useRef(false);
  const segments = useRef<unknown[]>([]); const engine = useRef('manual');
  const currentJob = useRef(run?.job_id); currentJob.current = run?.job_id;
  const prepared = useRef(''); const version = useRef(0);
  const {pending, perform} = useAction(onError);
  const selected = tasks.find(t => t.example_path === example);
  useEffect(() => () => {version.current++; releaseAudio(capture.current);}, []);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {if (capture.current) {e.preventDefault(); e.returnValue = '';}};
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, []);
  useEffect(() => {
    if (!run) return;
    const id = run.job_id;
    if (run.status !== 'awaiting_narration') {
      if (!active(run)) {releaseAudio(capture.current); capture.current = null;}
      return;
    }
    if (prepared.current === id) return;
    prepared.current = id; setPreparing(true); setAudioStatus('正在准备讲解审核…');
    const epoch = version.current;
    const valid = () => epoch === version.current && currentJob.current === id;
    void (async () => {
      try {
        const result = await api<Run>(`/api/jobs/${id}`); if (!valid()) return;
        setDetail(result);
        const saved = result.result?.narration;
        if (saved) {setTranscript(saved.transcript || ''); segments.current = saved.segments || [];
          engine.current = saved.engine || 'manual'; archived.current = true;}
        if (capture.current) {
          const blob = await finishAudio(capture.current); if (!valid()) return;
          setAudioStatus('使用本地 Whisper 转写；首次使用可能下载模型…');
          const result = await api<{transcription: {transcript: string; segments?: unknown[]; model?: string}}>(
            '/api/recordings/transcribe', {job_id: id, ...await audioPayload(blob)});
          if (!valid()) return;
          setTranscript(result.transcription.transcript); segments.current = result.transcription.segments || [];
          engine.current = `faster_whisper:${result.transcription.model || 'turbo'}`; archived.current = true;
        }
        if (valid()) setAudioStatus('请核对讲解文字；可以修改后提交。');
      } catch (e) {if (valid()) {setAudioStatus('自动转写未完成，录音草稿仍保留；可以手动填写讲解。'); onError(String(e));}}
      finally {if (valid()) setPreparing(false);}
    })();
  }, [run?.job_id, run?.status]);
  async function refreshTasks() {
    await perform('tasks', async () => {
      const data = await api<{tasks: WaaTask[]}>(`/api/waa/tasks?root=${encodeURIComponent(root)}`);
      setTasks(data.tasks); setExample(data.tasks.some(t => t.example_path === example) ? example : data.tasks[0]?.example_path || '');
    });
  }
  async function start() {
    if (dictationActive()) return onError('请先结束语音输入并等待转写完成。');
    if (!name.trim()) return onError('请输入经验名称。');
    if (source === 'waa' && !selected) return onError('请先读取并选择 WAA 标准任务。');
    const notice = source === 'opencua' ? '将录制整个主显示器的画面和键鼠操作。请关闭敏感内容。F8 完成，F9 取消。'
      : `将在 WAA 环境准备演示任务并重置其指定测试数据：\n${selected!.reset_paths.join('\n')}\n${narrated ? '准备完成后需点击开始讲解，授权麦克风。' : ''}${!defer ? '\n结束后会通过 Codex 编译示范。' : ''}`;
    if (!await confirm({title: '开始录制这份示范？', body: `${notice}\n\n经验名称：${name.trim()}`, confirmLabel: '确认开始录制', note: '请先关闭不希望录入的敏感内容。'})) return;
    await perform('start', async () => {
      setTranscript(''); setDetail(null); prepared.current = ''; archived.current = false;
      segments.current = []; engine.current = 'manual';
      const result = await api<Run>(source === 'opencua' ? '/api/recordings' : '/api/waa/recordings',
        {...compiler, task_id: name.trim(), ...(source === 'opencua'
          ? {recording_backend: 'opencua', execution_scope: 'desktop', narrated: false, defer_compilation: true}
          : {waa_root: root, example_path: example, narrated, defer_compilation: defer})});
      onRun(result);
    });
  }
  async function go() {
    if (!run) return;
    await perform('go', async () => {
      try {
        if (run.narrated) {capture.current = await captureAudio(); setAudioStatus('正在录制讲解，切换工作台页面不会中断麦克风。');}
        const result = await api<Run>('/api/waa/recordings/go', {job_id: run.job_id, audio_started_at_epoch_ms: capture.current?.started ?? null});
        onRun(result);
      } catch (e) {releaseAudio(capture.current); capture.current = null; throw e;}
    });
  }
  async function submit() {
    if (!run) return;
    await perform('submit', async () => {
      const audio = !archived.current && capture.current?.blob ? await audioPayload(capture.current.blob) : {};
      const next = await api<Run>('/api/recordings/narration', {job_id: run.job_id, transcript,
        segments: segments.current, transcription_engine: engine.current, ...audio});
      releaseAudio(capture.current); capture.current = null; setAudioStatus('讲解已保存。'); onRun(next);
    });
  }
  return <>
    <PageHeading eyebrow="DEMONSTRATE" title="演示一次，留下可复用的经验。" copy="示范、整理、执行彼此独立。原始证据保留，录制不会自动授予执行权限。"
      action={<Button onClick={onLibrary}>查看录制与经验 <Icon name="arrow" size={16}/></Button>}/>
    <div className="workflow-steps"><span><b>01</b> 录制人类示范</span><Icon name="arrow" size={17}/><span><b>02</b> 整理与核对</span><Icon name="arrow" size={17}/><span><b>03</b> 用于新任务</span></div>
    <section className="surface workflow-card"><div className="two-fields">
      <Field label="经验名称"><Input value={name} maxLength={80} disabled={busy || !!pending} onChange={(_, d) => setName(d.value)} placeholder="给这段示范一个容易辨认的名字"/></Field>
      <Field label="录制环境"><Choice value={source} disabled={busy || !!pending} onChange={e => setSource(e.target.value)}><option value="opencua">本机桌面 · OpenCUA</option><option value="waa">WAA 标准任务</option></Choice></Field>
    </div>
    {source === 'opencua' ? <div className="record-guide"><Icon name="screen" size={32}/><div><h3>跨应用录制整个主显示器</h3><p>保存画面和键鼠证据；当前本机路径不录制语音。结束后在经验库生成精简序列 D，不调用云端模型。</p>
      <div className="shortcut-row"><span><kbd>F8</kbd> 完成录制</span><span><kbd>F9</kbd> 取消录制</span></div></div></div> : <>
      <Field label="WAA 根目录"><Input value={root} disabled={busy || !!pending} onChange={(_, d) => {setRoot(d.value); setTasks([]); setExample('');}}/></Field>
      <div className="button-row spaced"><Button disabled={busy || !!pending || !root} onClick={() => void refreshTasks()}>读取标准任务</Button><span className="quiet">仅列出可录制演示，不包含 held-out 测试变体。</span></div>
      <Field label="WAA 任务"><Choice value={example} disabled={busy || !!pending} onChange={e => setExample(e.target.value)}><option value="">请选择任务</option>{tasks.map(t => <option key={t.id} value={t.example_path}>{t.domain} · {t.instruction}</option>)}</Choice></Field>
      {selected && <p className="inline-warning">准备时会重置该测试任务的数据：{selected.reset_paths.join('；')}</p>}
      <div className="button-row spaced"><Checkbox checked={narrated} disabled={busy} label="同时录制人类讲解" onChange={(_, d) => setNarrated(!!d.checked)}/>
        <Checkbox checked={defer} disabled={busy} label="先保存原始录制，稍后编译" onChange={(_, d) => setDefer(!!d.checked)}/></div>
      <CompilerChoice state={state} value={compiler} onChange={setCompiler} disabled={busy || !!pending}/>
    </>}
    <div className="action-footer"><p className="quiet">请关闭敏感内容，确保有权录制相关窗口。</p><Button appearance="primary" disabled={busy || !!pending || !name.trim()} onClick={() => void start()}>开始录制</Button></div></section>
    {run && <section className="surface workflow-card"><div className="row-between"><h2>{run.task_id}</h2><span className="status-tag">{labels[run.status] || run.status}</span></div>
      {run.status === 'awaiting_recording_start' && <div className="notice"><p>WAA 已准备完成。{run.narrated ? '点击后开始录音，再同步开始 Trace。' : '点击后开始 Trace。'}</p><Button appearance="primary" disabled={!!pending} onClick={() => void go()}>开始示范{run.narrated ? '与讲解' : ''}</Button></div>}
      {active(run) && run.status !== 'awaiting_narration' && <p className="quiet">使用录制热键完成示范；顶部停止按钮用于取消。{audioStatus}</p>}
      {run.status === 'awaiting_narration' && <div className="narration-editor"><p className="notice" role="status">{audioStatus}</p>
        <Field label="讲解文字"><Textarea value={transcript} resize="vertical" rows={7} disabled={preparing || !!pending} onChange={(_, d) => {setTranscript(d.value); segments.current = []; engine.current = 'manual';}}/></Field>
        <div className="action-footer"><span className="quiet">确认讲解不会覆盖原始 Trace；放弃请使用顶部停止。</span><Button appearance="primary" disabled={preparing || !!pending || !transcript.trim()} onClick={() => void submit()}>保存讲解并继续</Button></div></div>}
      {!active(run) && <p className="quiet">这是一条保留的运行记录。原始文件与可用编译方式请在经验库查看；移入回收站的录制不会再出现在正常录制列表。</p>}
      {detail?.error && <p role="alert">{detail.error}</p>}
    </section>}
    {run && <RunInspector id={run.job_id}/>}
  </>;
}
