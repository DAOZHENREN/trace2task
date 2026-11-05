import {useState} from 'react';
import {Button, Checkbox, Combobox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Input, Option, Textarea} from '@fluentui/react-components';
import {api, requestFor} from './api';
import {Dictation, dictationActive} from './audio';
import {AUTO_MEMORY, usableTrace, versionLabel} from './experience';
import type {AppState, Catalog, Config, Run, Target} from './types';
import {DEFAULT_ANDROID_BUDGET, DEFAULT_HYBRID_LIMITS} from './types';
import {chooseCodexModel, codexChoiceError, codexEfforts, CodexModelOptions} from './codexModels';
import {Choice, Field, Icon} from './ui';
import {CloudPhoneConnection, cloudPhoneDestination, cloudPhoneSelected} from './CloudPhoneConnection';
import {AndroidDevices} from './AndroidDevices';
import {NitrogenTask} from './NitrogenTask';

type Props = {config: Config; setConfig: (next: Config) => void; state: AppState; catalog: Catalog;
  busy: boolean; promptDirty: boolean; onRun: (run: Run) => void; onModels: () => void; onRefreshCodex: () => Promise<void>; onError: (message: string) => void};

const HYBRID_LIMIT_FIELDS = [
  ['max_decisions', '最多执行决策', '包含指导模型直接执行的动作'],
  ['max_guide_calls', '最多指导次数', '包含首次指导'],
  ['review_interval', '复查间隔（决策次数）', '每个阶段达到此次数后复查'],
  ['repeated_no_change_limit', '重复动作交回阈值', '同一动作连续达到此次数且画面未变时交回'],
] as const;

export function TaskComposer({config, setConfig: saveConfig, state, catalog, busy, promptDirty, onRun, onModels, onRefreshCodex, onError}: Props) {
  const hybrid = config.executionMode === 'hybrid';
  const c: Config = hybrid ? {...config, provider: config.hybridDirectorProvider || 'codex', mobileforge: '', androidModelProfile: ''} : config;
  const setConfig = (next: Config) => saveConfig(hybrid ? {...next, provider: config.provider,
    mobileforge: config.mobileforge, androidModelProfile: config.androidModelProfile} : next);
  const executorChoice = c.hybridExecutor || {provider: 'local', model: c.model, effort: c.effort, apiUrl: '', apiKey: ''};
  const hybridLimits = c.hybridLimits || DEFAULT_HYBRID_LIMITS;
  const updateExecutor = (patch: Partial<NonNullable<Config['hybridExecutor']>>) => {
    const next = {...executorChoice, ...patch};
    if (patch.model && next.provider === 'codex') next.effort = chooseCodexModel(state.agent_options, patch.model, next.effort).reasoning_effort;
    setConfig({...c, hybridExecutor: next});
  };
  const [instruction, setInstruction] = useState('');
  const [nitrogenSelected, setNitrogenSelected] = useState(false);
  const nitrogen = !hybrid && c.provider === 'local' && nitrogenSelected;
  const [confirm, setConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [choices, setChoices] = useState<{label: string; value: Target}[]>([]);
  const [search, setSearch] = useState('');
  const [loadingTargets, setLoadingTargets] = useState(false);
  const [deviceStatus, setDeviceStatus] = useState('');
  const [probing, setProbing] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const android = c.android || {adb: 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe', serial: '', package: '', server_port: 5037};
  const knowledgeSupported = !nitrogen && !c.mobileforge && c.scope !== 'browser' &&
    (c.provider !== 'local' || c.localModel !== 'trained_d');
  const profiles = state.application_knowledge || [];
  const matchedKnowledge = profiles.find(item => item.scope === 'android' && item.application === android.package.trim());
  const desktopKnowledge = profiles.filter(item => item.scope === 'desktop');
  const knowledge = !knowledgeSupported ? undefined : c.scope === 'android'
    ? c.androidKnowledgeOff ? undefined : matchedKnowledge
    : desktopKnowledge.find(item => item.id === c.desktopKnowledgeId);
  const budget = c.androidBudget || DEFAULT_ANDROID_BUDGET;
  const updateAndroid = (key: keyof typeof android, value: string | number | boolean) => {
    setDeviceStatus(''); setConfig({...c, android: {...android, [key]: value}});
  };
  async function probeDevice() {
    setProbing(true); setDeviceStatus('');
    try {
      const result = await api<{state: {width: number; height: number; focus: string}}>('/api/android/probe', {android_config: android});
      setDeviceStatus(`已连接 · ${result.state.width}×${result.state.height} · ${result.state.focus} · 未发送输入`);
    } catch (e) { onError(String(e)); } finally { setProbing(false); }
  }
  async function prepareDevice(action: 'connect' | 'check') {
    setProbing(true); setDeviceStatus('');
    try {
      const result = await api<{message: string}>('/api/android/prepare', {android_config: android, action});
      setDeviceStatus(result.message);
    } catch (e) {onError(String(e));} finally {setProbing(false);}
  }
  const update = (key: keyof Config, value: unknown) => {
    const next = {...c, [key]: value};
    if (key === 'provider' && hybrid) next.hybridDirectorProvider = value as 'codex' | 'api';
    if (key === 'model') next.effort = chooseCodexModel(state.agent_options, String(value), c.effort).reasoning_effort;
    if (key === 'scope') next.executor = value === 'android' ? 'android' : value === 'browser' ? 'browser' : 'cua';
    if (key === 'localModel') {
      const p = catalog.models.find(p => p.id === value);
      if (p && !p.engines.includes(c.inference)) next.inference = p.engines[0];
    }
    setConfig(next);
  };
  const profile = catalog.models.find(p => p.id === c.localModel);
  const engine = catalog.inference_backends.find(e => e.id === (c.provider === 'codex' ? 'codex' : c.provider === 'api'
    ? 'chat-completions' : c.localModel === 'trained_d' ? 'frozen-d' : c.inference));
  const cloudPhone = cloudPhoneSelected(c);
  const remote = cloudPhone || c.provider === 'codex' || (c.provider === 'api' && !isLocal(c.apiUrl)) ||
    hybrid && (executorChoice.provider === 'codex' || executorChoice.provider === 'api' && !isLocal(executorChoice.apiUrl));
  const directorDestination = cloudPhone ? cloudPhoneDestination : c.provider === 'codex' ? 'Codex 订阅服务' : c.provider === 'api' ? host(c.apiUrl) : '本机 GPU';
  const destination = hybrid ? `${directorDestination} 指导 → ${executorChoice.provider === 'local' ? '本机 GPU' : executorChoice.provider === 'codex' ? 'Codex 订阅服务' : host(executorChoice.apiUrl)} 执行` : directorDestination;
  const selectedModel = hybrid ? `${c.provider === 'api' ? c.apiModel : c.model} 指导 → ${executorChoice.provider === 'local' ? profile?.label || c.localModel : executorChoice.model} 执行` : c.provider === 'local' ? profile?.label || c.localModel : c.provider === 'codex' ? c.model : c.apiModel || '尚未填写';
  async function loadTargets() {
    setLoadingTargets(true);
    try {
      const result = await api<{windows: {pid: number; window_id: number; title?: string; app_name?: string}[];
        apps: {name?: string; launch_path: string}[]}>('/api/cua/windows', {});
      const refreshed = [...result.windows.filter(w => Number.isInteger(w.pid) && w.pid > 0 && Number.isInteger(w.window_id) && w.window_id > 0)
        .map(w => ({label: `${w.title || '无标题窗口'} · ${w.app_name || w.pid}`,
        value: {pid: w.pid, window_id: w.window_id}})),
        ...result.apps.map(a => ({label: `${a.name || a.launch_path} · 应用`, value: {launch_path: a.launch_path}}))];
      setChoices([...new Map(refreshed.map(t => [JSON.stringify(t.value), t])).values()]);
    } catch (e) { onError(String(e)); } finally { setLoadingTargets(false); }
  }
  function validate() {
    if (hybrid && c.executor === 'browser') return '混合模式首版支持 Android 手机和 Cua 桌面；请选择对应操作范围。';
    if (hybrid && Object.values(hybridLimits).some(value => !Number.isInteger(value) || value < 1 || value > 500))
      return '混合模式的四项运行限制均须填写 1–500 的整数。';
    if (hybrid && executorChoice.provider === 'local' && (!profile || !profile.engines.includes(c.inference))) return '请选择已接入的本地执行模型与兼容推理引擎。';
    if (hybrid && executorChoice.provider === 'local' && (!catalog.service.reachable || catalog.service.backend !== c.inference))
      return '请先在「模型与连接」启动混合模式选择的本地推理引擎。';
    if (hybrid && executorChoice.provider === 'codex') {const error = codexChoiceError(state.agent_options, executorChoice.model, executorChoice.effort); if (error) return error;}
    if (hybrid && executorChoice.provider === 'api' && (!executorChoice.model.trim() || !executorChoice.apiUrl.trim())) return '请填写主要执行模型的 API 地址和模型 ID。';
    if (c.provider === 'codex') {const error = codexChoiceError(state.agent_options, c.model, c.effort); if (error) return error;}
    if (dictationActive()) return '请先结束语音输入并等待转写完成。';
    if (promptDirty) return '当前模型的提示词有未保存修改，请在「设置与工具 → 模型提示词」保存或恢复默认后再开始任务。';
    if (!instruction.trim()) return '请先写下任务目标。';
    if (c.experience === AUTO_MEMORY && (c.provider === 'local' || c.mobileforge))
      return '自动经验暂不支持本地 GUI 专用模型或 MobileForge；请关闭自动经验或选择通用代理。';
    if (c.androidModelProfile && !cloudPhone) return '3090 预设已被修改，请重新应用 3090 配置或切换到自定义配置。';
    if (c.provider === 'api' && (!c.apiModel.trim() || !c.apiUrl.trim())) return '请填写视觉模型 ID 和 API 地址。';
    if ((c.executor === 'android') !== (c.scope === 'android')) return 'Android 需要同时选择手机范围和 ADB 执行后端。';
    if (c.executor === 'android') {
      if (!Number.isInteger(budget.max_actions) || budget.max_actions < 1 || budget.max_actions > 500 ||
          !Number.isInteger(budget.max_seconds) || budget.max_seconds < 30 || budget.max_seconds > 7200)
        return '任务预算：动作数为 1–500，时间为 0.5–120 分钟。';
      if (c.provider === 'local' && c.mobileforge) return 'MobileForge 仅支持 Codex / 视觉 API；本地模型请关闭 MobileForge。';
      if (c.experience && c.experience !== AUTO_MEMORY) {
        const trace = state.trace_representations.find(t => c.experience === `trace-library/${t.id}/model-input.txt`);
        if (c.mobileforge || trace?.representation !== 'B' || trace?.source_scope !== 'android' || trace?.source_package !== android.package)
          return 'Android 经验需要同一授权应用的运行方法 B，并使用通用视觉代理。';
      }
      if (!android.adb.trim() || !android.serial.trim() || !android.package.trim()) return '请刷新并选择已连接设备及授权应用；特殊连接可在高级设置中配置。';
      if (c.mobileforge && c.provider === 'api' && !isLocal(c.apiUrl)) return 'MobileForge API 仅允许本机端点；远端 GPU 请使用本机 SSH 转发。';
    }
    if ((c.executor === 'browser') !== (c.scope === 'browser')) return 'Ancient Beast 需要同时选择独立浏览器范围和 Browser 执行后端；不会自动改为桌面操作。';
    if (c.executor === 'browser' && c.provider === 'local') return '浏览器首版仅支持 Codex 或视觉模型 API；本地 GUI 模型尚未验证。';
    if (c.scope === 'selected_windows' && (c.executor !== 'cua' || !c.targets.length || c.targets.length > 12)) return '指定窗口需要 Cua，并明确选择 1–12 个目标。';
    if (c.localModel === 'trained_d' && c.provider === 'local' && c.experience) return 'D-5970 冻结协议不能使用 A/B/C/D 经验。';
    if (c.provider === 'local' && profile && (!catalog.service.reachable || catalog.service.backend !== c.inference))
      return '请先在「模型与连接」启动所选推理引擎；不会静默切换到其他引擎。';
    return '';
  }
  async function start() {
    setSubmitting(true);
    try { const run = await api<Run>('/api/jobs', requestFor({...c, android}, instruction.trim(), knowledge)); setConfirm(false); onRun(run); }
    catch (e) { onError(String(e)); setConfirm(false); } finally { setSubmitting(false); }
  }
  return <>
    <section className="composer surface">
      {!hybrid && !nitrogen && <CloudPhoneConnection config={c} setConfig={setConfig} busy={busy || submitting || discovering || probing} onError={onError}/>}
      {!nitrogen && <>
      <div className="composer-top"><span className="section-label"><span className="step-marker">01</span>任务目标</span><div className="button-row"><Dictation disabled={busy} onError={onError} onAppend={text => setInstruction(value => `${value} ${text}`.trim().slice(0, 2000))}/></div></div>
      <Textarea className="task-textarea" aria-label="任务指令" placeholder={cloudPhone ? '描述手机任务，例如：继续当前暂停的秒表，不要重置。' : '描述你想完成的事，例如：把今天的待办整理到记事本，按优先级排序。'}
        value={instruction} onChange={(_, d) => setInstruction(d.value)} maxLength={2000} resize="vertical" disabled={busy}/>
      </>}
      <div className="execution-setup">
      <div className="setup-heading"><span className="section-label"><span className="step-marker">02</span>执行配置</span>
      <div className="execution-mode"><Field label="执行模式"><Choice value={config.executionMode || 'original'} disabled={busy}
        onChange={e => setConfig({...config, executionMode: e.target.value as Config['executionMode']})}>
        <option value="original">原模式 · 单模型执行</option><option value="hybrid">混合模型 · 独立指导与执行</option>
      </Choice></Field></div>
      <div className="button-row">
        {(c.provider === 'codex' || hybrid && executorChoice.provider === 'codex') && <Button appearance="subtle" size="small" disabled={busy} title="模型和思考强度来自当前 Codex 客户端" onClick={() => void onRefreshCodex()}>刷新订阅模型</Button>}
        <Button appearance="subtle" size="small" onClick={onModels}>模型连接 <Icon name="arrow" size={14}/></Button></div></div>
      {hybrid && <p className="quiet">指导模型根据任务目标和当前截图判断下一步，可直接纠正操作，也可把子任务交给主要执行模型。两端可独立选择。</p>}
      <div className={`composer-fields${c.provider === 'codex' ? ' codex-fields' : ''}`}>
        <Field label={hybrid ? '指导模型来源' : '模型来源'}><Choice value={c.provider} disabled={busy} onChange={e => update('provider', e.target.value)}>
          <option value="codex">Codex 订阅</option>{!hybrid && <option value="local">本地模型</option>}<option value="api">视觉模型 API · 本地 / 远程</option>
        </Choice></Field>
        <Field label={hybrid ? '指导模型' : '执行模型'}>{c.provider === 'api' ? <Input value={c.apiModel} disabled={busy} placeholder="模型 ID"
          onChange={(_, d) => update('apiModel', d.value)}/> : <Choice disabled={busy} value={c.provider === 'codex' ? c.model : nitrogen ? 'nitrogen' : c.localModel}
          onChange={e => {
            if (c.provider === 'local') {
              setNitrogenSelected(e.target.value === 'nitrogen');
              if (e.target.value === 'nitrogen') return;
            }
            update(c.provider === 'codex' ? 'model' : 'localModel', e.target.value);
          }}>
          {c.provider === 'codex' ? <CodexModelOptions options={state.agent_options} value={c.model}/> : <>
            {catalog.models.map(p => <option value={p.id} key={p.id}>{p.label}{p.maturity === 'experimental' ? ' · 实验' : ''}</option>)}
            <option value="nitrogen">NitroGen · 游戏手柄</option>
          </>}
        </Choice>}</Field>
        {c.provider === 'codex' && <Field label="思考强度"><Choice value={c.effort} disabled={busy} onChange={e => update('effort', e.target.value)}>
          {!codexEfforts(state.agent_options, c.model).includes(c.effort) && <option value={c.effort} disabled>请重新选择</option>}
          {codexEfforts(state.agent_options, c.model).map(e => <option key={e}>{e}</option>)}
        </Choice></Field>}
        {!nitrogen && <Field label="参考经验"><Choice value={c.experience} disabled={busy} onChange={e => update('experience', e.target.value)}>
          <option value={AUTO_MEMORY} disabled={c.provider === 'local' || !!c.mobileforge}>自动选择 · Agent 检索并加载方法 B</option>
          <option value="">不使用经验</option>{state.trace_representations.filter(usableTrace).map(t =>
            <option key={t.id} value={`trace-library/${t.id}/model-input.txt`}>{versionLabel(t)}</option>)}
        </Choice></Field>}
      </div>
      {nitrogen ? <NitrogenTask workbench busy={busy} onRun={onRun} onError={onError}/> : <>
      {hybrid && c.provider === 'api' && <div className="composer-fields">
        <Field label="指导 API 地址"><Input value={c.apiUrl} disabled={busy} onChange={(_, d) => update('apiUrl', d.value)}/></Field>
        <Field label="指导 API 密钥"><Input type="password" value={c.apiKey} disabled={busy} onChange={(_, d) => update('apiKey', d.value)}/></Field>
        <Field label="指导 API 响应格式"><Choice value={c.apiFormat} disabled={busy} onChange={e => update('apiFormat', e.target.value)}><option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option></Choice></Field>
      </div>}
      {hybrid && <div className="composer-fields">
        <Field label="主要执行模型来源"><Choice value={executorChoice.provider} disabled={busy} onChange={e => updateExecutor({provider: e.target.value as 'local' | 'codex' | 'api'})}>
          <option value="local">本地模型</option><option value="codex">Codex 订阅</option><option value="api">视觉模型 API · 本地 / 远程</option>
        </Choice></Field>
        {executorChoice.provider === 'local' ? <><Field label="主要执行模型"><Choice value={c.localModel} disabled={busy} onChange={e => update('localModel', e.target.value)}>
          {catalog.models.map(p => <option value={p.id} key={p.id}>{p.label}</option>)}
        </Choice></Field>
        </> : executorChoice.provider === 'codex' ? <>
          <Field label="主要执行模型"><Choice value={executorChoice.model} disabled={busy} onChange={e => updateExecutor({model: e.target.value})}><CodexModelOptions options={state.agent_options} value={executorChoice.model}/></Choice></Field>
          <Field label="执行模型思考强度"><Choice value={executorChoice.effort} disabled={busy} onChange={e => updateExecutor({effort: e.target.value})}>{codexEfforts(state.agent_options, executorChoice.model).map(e => <option key={e}>{e}</option>)}</Choice></Field>
        </> : <>
          <Field label="主要执行模型"><Input value={executorChoice.model} disabled={busy} placeholder="模型 ID" onChange={(_, d) => updateExecutor({model: d.value})}/></Field>
          <Field label="执行 API 地址"><Input value={executorChoice.apiUrl} disabled={busy} onChange={(_, d) => updateExecutor({apiUrl: d.value})}/></Field>
          <Field label="执行 API 密钥"><Input type="password" value={executorChoice.apiKey} disabled={busy} onChange={(_, d) => updateExecutor({apiKey: d.value})}/></Field>
          <Field label="执行 API 响应格式"><Choice value={executorChoice.apiFormat || 'json_schema'} disabled={busy} onChange={e => updateExecutor({apiFormat: e.target.value})}><option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option></Choice></Field>
        </>}
        <Field label="本地推理引擎"><Choice value={c.inference} disabled={busy} onChange={e => update('inference', e.target.value)}>
          {(profile?.engines || []).map(id => <option value={id} key={id}>{catalog.inference_backends.find(e => e.id === id)?.label || id}</option>)}
        </Choice></Field>
      </div>}
      {hybrid && <>
        <div className="composer-fields hybrid-limit-fields">
          {HYBRID_LIMIT_FIELDS.map(([key, label, hint]) =>
            <Field key={key} label={label} hint={hint}>
              <Input type="number" min={1} max={500} step={1} value={String(hybridLimits[key] || '')}
                disabled={busy || submitting} onChange={(_, d) => update('hybridLimits', {...hybridLimits, [key]: Number(d.value)})}/>
            </Field>)}
        </div>
        <p className="quiet">总预算和停止按钮仍有效。</p>
      </>}
      {hybrid && <p className="quiet">指导端也可使用本地兼容 API 的视觉模型。两端使用独立会话。
        {executorChoice.provider === 'local' && c.inference === 'slotread' && ' GUI-Decisions 原生接口只读当前截图，保留动作文字历史，不支持历史图片。'}
        {c.experience && ' 参考经验是本地扩展，会额外提供给指导模型。'}</p>}
      {c.provider === 'codex' && state.agent_options.catalog_error && <p role="status" className="quiet">{state.agent_options.catalog_error}</p>}
      {c.experience === AUTO_MEMORY ? <p className="quiet">Agent 先检索方法摘要，再决定加载一份 B 或不使用经验；操作前重新截图。检索和加载过程记入运行记录。</p>
        : (c.provider === 'local' || c.mobileforge) && <p className="quiet">当前策略不支持自动经验，自动模式已关闭；本地 GUI 模型仍可按现有协议手动选择经验。</p>}
      <div className="scope-row">
        <Field label="操作范围"><Choice value={c.scope} disabled={busy} onChange={e => update('scope', e.target.value)}>
          <option value="desktop">全桌面 · 可跨应用</option><option value="selected_windows">仅指定窗口 / 应用</option>
          <option value="browser">Ancient Beast · 独立浏览器</option>
          <option value="android">Android · 指定手机与应用</option>
        </Choice></Field>
        <Field label="执行后端"><Choice value={c.executor} disabled={busy || c.scope === 'android'} onChange={e => update('executor', e.target.value)}>
          <option value="cua">Cua · 按范围执行</option>
          <option value="browser">Browser · 隔离后台（实验）</option>
          <option value="android">ADB · 远程手机（实验）</option>
        </Choice></Field>
        <span className="scope-note"><Icon name="shield" size={16}/>仅在确认的范围内执行</span>
      </div>
      {(c.scope === 'android' || c.executor === 'android') && <div className="target-picker">
        <div className="setup-heading"><strong>手机连接</strong><span className="tag">{c.mobileforge ? 'MobileForge' : '通用视觉代理'} · ADB</span></div>
        {c.provider === 'local' && <p className="quiet">本地模型读取手机截图，通过 ADB 执行各模型可表达的点击、长按、滑动、返回和等待；暂不支持文字输入、滚轮或多指操作。</p>}
        <AndroidDevices value={android} onChange={value => {setDeviceStatus(''); setConfig({...c, android: value});}}
          busy={busy || probing} onError={onError} autoConnect={c.scope === 'android'} onPendingChange={setDiscovering}/>
        <div className="composer-fields">
          <Field label="动作预算"><Input type="number" min={1} max={500} value={String(budget.max_actions)} disabled={busy}
            onChange={(_, d) => update('androidBudget', {...budget, max_actions: Number(d.value)})}/></Field>
          <Field label="时间预算（分钟）"><Input type="number" min={0.5} max={120} step={0.5} value={String(budget.max_seconds / 60)} disabled={busy}
            onChange={(_, d) => update('androidBudget', {...budget, max_seconds: Number(d.value) * 60})}/></Field>
        </div>
        <p className="quiet">任一预算耗尽即停止，任务不视为完成；等待也计入动作数。时间包含加载、截图和模型推理，已提交的调用可能需要等待返回。</p>
        <details className="advanced-connection"><summary>手动连接与高级设置</summary>
        <div className="composer-fields">
          <AndroidChoice label="设备 serial" value={android.serial} disabled={busy || probing || discovering}
            options={[]}
            onChange={value => updateAndroid('serial', value)}/>
          <AndroidChoice label="授权应用包名" value={android.package} disabled={busy || probing || discovering}
            options={[[ 'com.android.deskclock', '时钟（预设）'], ['com.android.calculator2', '计算器（预设）'],
              ['com.android.calendar', '日历（预设）'], ['com.miHoYo.hkrpg', '崩坏：星穹铁道（预设）']]}
            onChange={value => updateAndroid('package', value)}/>
          <Field label="Android 策略"><Choice value={c.mobileforge ? 'mobileforge' : 'chat'} disabled={busy} onChange={e => update('mobileforge', e.target.value === 'mobileforge' ? 'D:/Tools/trace2task-android/MobileForge' : '')}>
            <option value="chat">通用视觉代理 + ADB</option><option value="mobileforge">MobileForge + ADB</option>
          </Choice></Field>
        </div>
        <details className="advanced-connection"><summary>高级连接设置 <span>ADB 路径 · 端口{c.mobileforge ? ' · 源码目录' : ''}</span></summary>
          <div className="composer-fields">
            <AndroidChoice label="ADB 路径" value={android.adb} disabled={busy || probing || discovering}
              options={[['D:/Tools/trace2task-android/sdk/platform-tools/adb.exe', '本机安装路径（预设）']]}
              onChange={value => updateAndroid('adb', value)}/>
            <Field label="ADB 服务端口"><Input type="number" value={String(android.server_port)} disabled={busy || probing || discovering} onChange={(_, d) => updateAndroid('server_port', Number(d.value))}/></Field>
            {!!c.mobileforge && <Field label="MobileForge 源码目录"><Input value={c.mobileforge} disabled={busy} onChange={(_, d) => update('mobileforge', d.value)}/></Field>}
          </div>
        </details>
        <Checkbox checked={!!android.launch_app} disabled={busy || probing || discovering} label="确认任务后自动打开授权应用（不重置）" onChange={(_, d) => updateAndroid('launch_app', d.checked === true)}/>
        <div className="button-row"><Button disabled={busy || probing || discovering} onClick={() => void prepareDevice('connect')}>连接云手机</Button>
          <Button disabled={busy || probing || discovering} onClick={() => void prepareDevice('check')}>启动前检查</Button>
          <Button disabled={busy || probing || discovering} onClick={probeDevice}>{probing ? '正在检查…' : '只读检查设备'}</Button></div>
        {deviceStatus && <p role="status">{deviceStatus}</p>}
        <p className="quiet">仅在设备未出现在列表时手动配置。连接按钮只连接填写的地址，不重启红手指或全局 ADB 服务。</p>
        </details>
        <details className="connection-help"><summary>连接说明与能力边界</summary><p>预设不代表设备在线或应用已安装；不同手机的应用包名可能不同。连接按钮只连接指定地址；运行中断线会停止，不自动重连。不重置或强停应用，不回退桌面；仅单指操作，不支持文本输入和跨应用操作。每轮截图交给模型判断，本机校验动作、通过 ADB 执行并再次截图；推理结果保存在运行记录中。</p></details>
      </div>}
      {(c.scope === 'browser' || c.executor === 'browser') && <p className="inline-warning">
        仅打开 ancientbeast.com 的全新无头 Edge 页面；不占用系统键鼠，不读取 DOM 或游戏内部状态，不回退前台。
        每次任务重新开局，结束即关闭页面。首版支持 Codex / 视觉 API，需要源码版 Node、Playwright 和 Edge。
      </p>}
      {knowledgeSupported && (c.scope === 'android' ? matchedKnowledge : desktopKnowledge.length > 0) &&
      <Field label="应用底层知识" hint={c.scope === 'android' ? '按授权应用包名匹配。' : '按应用手动选择，在经验库维护。'}>
        <Choice value={knowledge?.id || ''} disabled={busy} onChange={e => c.scope === 'android'
          ? update('androidKnowledgeOff', !e.target.value) : update('desktopKnowledgeId', e.target.value)}>
          <option value="">不使用底层知识</option>
          {(c.scope === 'android' ? matchedKnowledge ? [matchedKnowledge] : [] : desktopKnowledge).map(item =>
            <option key={item.id} value={item.id}>{item.application} · v{item.revision}</option>)}
        </Choice>
      </Field>}
      {c.scope === 'selected_windows' && <div className="target-picker">
        <div className="row-between"><strong>明确授权目标</strong><Button size="small" disabled={busy || loadingTargets} onClick={loadTargets}>
          {loadingTargets ? '正在读取…' : '刷新窗口 / 应用'}</Button></div>
        {c.executor !== 'cua' && <p className="inline-warning">此范围需要手动选择 Cua 执行后端。</p>}
        <Input aria-label="搜索窗口" placeholder="搜索标题或应用名" value={search} onChange={(_, d) => setSearch(d.value)}/>
        <div className="target-options">{[...new Map([...c.targets, ...choices].map(t => [JSON.stringify(t.value), t])).values()]
          .filter(t => c.targets.some(s => JSON.stringify(s.value) === JSON.stringify(t.value)) || t.label.toLowerCase().includes(search.toLowerCase())).map(t => {
          const key = JSON.stringify(t.value); const selected = c.targets.some(s => JSON.stringify(s.value) === key);
          return <Checkbox key={key} label={t.label} checked={selected} disabled={busy || (!selected && c.targets.length >= 12)}
            onChange={(_, d) => {const targets = d.checked ? [...c.targets, t] : c.targets.filter(s => JSON.stringify(s.value) !== key);
              setConfig({...c, targets, initialTarget: Math.min(c.initialTarget, Math.max(0, targets.length - 1))});}}/>;
        })}</div>
        <Field label={`初始目标 · 已选 ${c.targets.length}/12`}><Choice value={c.initialTarget} disabled={busy || !c.targets.length}
          onChange={e => update('initialTarget', Number(e.target.value))}>{c.targets.map((t, i) => <option key={JSON.stringify(t.value)} value={i}>{t.label}</option>)}</Choice></Field>
        <p className="quiet">后台优先；驱动明确拒绝时，同一窗口会改用前台输入。搜索和刷新不会扩大已选授权。</p>
      </div>}
      </>}
      </div>
      {!nitrogen && <div className="composer-footer"><span className="quiet"><Icon name="shield" size={16}/>{remote ? `画面与经验将发送到 ${destination}` : '画面由本机服务处理'}</span>
        <Button appearance="primary" size="large" icon={<Icon name="arrow" size={18}/>} iconPosition="after" disabled={busy || submitting || discovering || probing || !instruction.trim()}
          onClick={() => {const problem = validate(); if (problem) onError(problem); else setConfirm(true);}}>开始任务</Button></div>}
    </section>
    {!nitrogen && <details className="context-strip"><summary>当前推理连接 · {engine?.label || '推理引擎'}</summary><p>{engine?.context_description}</p></details>}
    <Dialog open={confirm} onOpenChange={(_, d) => {if (!submitting) setConfirm(d.open);}}>
      <DialogSurface className="confirmation-surface"><DialogBody className="confirmation-body"><DialogTitle className="confirmation-title">开始这一次任务？</DialogTitle><DialogContent className="confirmation-content">
        <p>{instruction}</p><dl className="facts-list"><dt>模型</dt><dd>{selectedModel}</dd><dt>操作范围</dt><dd>{c.scope === 'android' ? `仅 Android · ${android.serial} · ${android.package} · ${c.mobileforge ? 'MobileForge' : '通用视觉代理'} · 最多 ${budget.max_actions} 个动作 / ${budget.max_seconds / 60} 分钟` : c.scope === 'browser' ? '仅 Ancient Beast · 独立无头浏览器 · 不占用系统键鼠' : c.scope === 'desktop' ? '整个主显示器 · 占用键鼠' : c.targets.map(t => t.label).join('；')}</dd>
          <dt>经验方式</dt><dd>{c.experience === AUTO_MEMORY ? 'Agent 自动检索摘要，按需加载一份方法 B；也可不加载' : c.experience ? '手动固定版本' : '不使用经验'}</dd>
          {knowledge && <><dt>应用底层知识</dt><dd>{knowledge.application} · v{knowledge.revision} · 随任务提供给{hybrid ? '指导模型和执行模型' : '执行模型'}</dd></>}
          <dt>数据去向</dt><dd>{destination} {remote ? (c.experience === AUTO_MEMORY ? '· 发送截图、检索到的经验摘要和选中的方法全文' : '· 发送截图与相关经验') : '· 本机处理'}</dd></dl>
        <p className="inline-warning">开始后连续执行，无需逐步确认。请先关闭敏感内容；使用顶部停止按钮或 F9 中止后续动作。当前驱动调用可能需要等待返回。</p>
        {c.executor === 'android' && <p>{android.launch_app ? `将打开授权应用 ${android.package}，不重置、不强停；随后截图推理。` : '不会自动打开应用；授权应用必须已在前台。'}提交前会重新检查手机连接与应用。</p>}
      </DialogContent><DialogActions className="confirmation-actions"><Button disabled={submitting} onClick={() => setConfirm(false)}>返回修改</Button>
        <Button appearance="primary" disabled={submitting} onClick={start}>{submitting ? '正在启动…' : '确认开始'}</Button></DialogActions></DialogBody></DialogSurface>
    </Dialog>
  </>;
}

function AndroidChoice({label, value, options, disabled, onChange}: {
  label: string; value: string; options: [string, string][]; disabled: boolean; onChange: (value: string) => void;
}) {
  return <Field label={label}><Combobox freeform value={value} selectedOptions={value ? [value] : []}
    disabled={disabled} placeholder="选择预设或手动输入" style={{minWidth: 0, width: '100%'}}
    onChange={event => onChange(event.target.value)}
    onOptionSelect={(_, data) => {if (data.optionValue !== undefined) onChange(data.optionValue);}}>
    {options.map(([id, name]) => <Option key={id} value={id} text={id}>{name} · {id}</Option>)}
  </Combobox></Field>;
}

export function host(url: string) {try {return new URL(url).hostname;} catch {return '尚未配置的 API';}}
export function isLocal(url: string) {return ['127.0.0.1', 'localhost', '[::1]'].includes(host(url));}
