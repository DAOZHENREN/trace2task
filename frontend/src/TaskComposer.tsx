import {useState} from 'react';
import {Button, Checkbox, Combobox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Input, Option, Textarea} from '@fluentui/react-components';
import {api, requestFor} from './api';
import {Dictation, dictationActive} from './audio';
import {AUTO_MEMORY, usableTrace, versionLabel} from './experience';
import type {AppState, Catalog, Config, Run, Target} from './types';
import {Choice, Field, Icon} from './ui';
import {CloudPhoneConnection, cloudPhoneDestination, cloudPhoneSelected} from './CloudPhoneConnection';
import {AndroidDevices} from './AndroidDevices';

type Props = {config: Config; setConfig: (next: Config) => void; state: AppState; catalog: Catalog;
  busy: boolean; promptDirty: boolean; onRun: (run: Run) => void; onModels: () => void; onError: (message: string) => void};

export function TaskComposer({config: c, setConfig, state, catalog, busy, promptDirty, onRun, onModels, onError}: Props) {
  const [instruction, setInstruction] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [choices, setChoices] = useState<{label: string; value: Target}[]>([]);
  const [search, setSearch] = useState('');
  const [loadingTargets, setLoadingTargets] = useState(false);
  const [deviceStatus, setDeviceStatus] = useState('');
  const [probing, setProbing] = useState(false);
  const android = c.android || {adb: 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe', serial: '', package: '', server_port: 5037};
  const budget = c.androidBudget || {max_actions: 80, max_seconds: 1200};
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
  const remote = cloudPhone || c.provider === 'codex' || (c.provider === 'api' && !isLocal(c.apiUrl));
  const destination = cloudPhone ? cloudPhoneDestination : c.provider === 'codex' ? 'Codex 订阅服务' : c.provider === 'api' ? host(c.apiUrl) : '本机 GPU';
  const selectedModel = c.provider === 'local' ? profile?.label || c.localModel : c.provider === 'codex' ? c.model : c.apiModel || '尚未填写';
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
    if (dictationActive()) return '请先结束语音输入并等待转写完成。';
    if (promptDirty) return '当前模型的提示词有未保存修改，请在「设置与工具 → 模型提示词」保存或恢复默认后再开始任务。';
    if (!instruction.trim()) return '请先写下任务目标。';
    if (c.experience === AUTO_MEMORY && (c.provider === 'local' || c.mobileforge))
      return '自动经验暂不支持本地 GUI 专用模型或 MobileForge；请关闭自动经验或选择通用代理。';
    if (c.androidModelProfile && !cloudPhone) return '3090 预设已被修改，请重新应用 3090 配置或切换到自定义配置。';
    if (c.provider === 'api' && !c.apiModel.trim()) return '请到「模型与连接」填写视觉模型 ID 和 API 地址。';
    if ((c.executor === 'android') !== (c.scope === 'android')) return 'Android 需要同时选择手机范围和 ADB 执行后端。';
    if (c.executor === 'android') {
      if (!Number.isInteger(budget.max_actions) || budget.max_actions < 1 || budget.max_actions > 500 ||
          !Number.isInteger(budget.max_seconds) || budget.max_seconds < 30 || budget.max_seconds > 7200)
        return '任务预算：动作数为 1–500，时间为 0.5–120 分钟。';
      if (c.provider === 'local') return 'Android 支持 Codex / 视觉 API；暂不支持桌面本地 GUI 模型。';
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
    try { const run = await api<Run>('/api/jobs', requestFor({...c, android}, instruction.trim())); setConfirm(false); onRun(run); }
    catch (e) { onError(String(e)); setConfirm(false); } finally { setSubmitting(false); }
  }
  return <>
    <section className="composer surface">
      <CloudPhoneConnection config={c} setConfig={setConfig} busy={busy || submitting} onError={onError}/>
      <div className="composer-top"><span className="section-label"><span className="step-marker">01</span>任务目标</span><div className="button-row"><Dictation disabled={busy} onError={onError} onAppend={text => setInstruction(value => `${value} ${text}`.trim().slice(0, 2000))}/></div></div>
      <Textarea className="task-textarea" aria-label="任务指令" placeholder={cloudPhone ? '描述手机任务，例如：继续当前暂停的秒表，不要重置。' : '描述你想完成的事，例如：把今天的待办整理到记事本，按优先级排序。'}
        value={instruction} onChange={(_, d) => setInstruction(d.value)} maxLength={2000} resize="vertical" disabled={busy}/>
      <div className="execution-setup">
      <div className="setup-heading"><span className="section-label"><span className="step-marker">02</span>执行配置</span><Button appearance="subtle" size="small" onClick={onModels}>模型连接 <Icon name="arrow" size={14}/></Button></div>
      <div className="composer-fields">
        <Field label="模型来源"><Choice value={c.provider} disabled={busy} onChange={e => update('provider', e.target.value)}>
          <option value="codex">Codex 订阅</option><option value="local">本地模型</option><option value="api">视觉模型 API</option>
        </Choice></Field>
        <Field label="执行模型">{c.provider === 'api' ? <Input value={c.apiModel} disabled={busy} placeholder="模型 ID"
          onChange={(_, d) => update('apiModel', d.value)}/> : <Choice disabled={busy} value={c.provider === 'codex' ? c.model : c.localModel}
          onChange={e => update(c.provider === 'codex' ? 'model' : 'localModel', e.target.value)}>
          {c.provider === 'codex' ? state.agent_options.models.map(m => <option key={m}>{m}</option>) : <>
            {catalog.models.map(p => <option value={p.id} key={p.id}>{p.label}{p.maturity === 'experimental' ? ' · 实验' : ''}</option>)}
          </>}
        </Choice>}</Field>
        <Field label="参考经验"><Choice value={c.experience} disabled={busy} onChange={e => update('experience', e.target.value)}>
          <option value={AUTO_MEMORY} disabled={c.provider === 'local' || !!c.mobileforge}>自动选择 · Agent 检索并加载方法 B</option>
          <option value="">不使用经验</option>{state.trace_representations.filter(usableTrace).map(t =>
            <option key={t.id} value={`trace-library/${t.id}/model-input.txt`}>{versionLabel(t)}</option>)}
        </Choice></Field>
      </div>
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
        <AndroidDevices value={android} onChange={value => {setDeviceStatus(''); setConfig({...c, android: value});}} busy={busy || probing} onError={onError}/>
        <div className="composer-fields">
          <Field label="动作预算"><Input type="number" min={1} max={500} value={String(budget.max_actions)} disabled={busy}
            onChange={(_, d) => update('androidBudget', {...budget, max_actions: Number(d.value)})}/></Field>
          <Field label="时间预算（分钟）"><Input type="number" min={0.5} max={120} step={0.5} value={String(budget.max_seconds / 60)} disabled={busy}
            onChange={(_, d) => update('androidBudget', {...budget, max_seconds: Number(d.value) * 60})}/></Field>
        </div>
        <p className="quiet">任一预算耗尽即停止，任务不视为完成；等待也计入动作数。时间包含加载、截图和模型推理，已提交的调用可能需要等待返回。</p>
        <details className="advanced-connection"><summary>手动连接与高级设置</summary>
        <div className="composer-fields">
          <AndroidChoice label="设备 serial" value={android.serial} disabled={busy || probing}
            options={[]}
            onChange={value => updateAndroid('serial', value)}/>
          <AndroidChoice label="授权应用包名" value={android.package} disabled={busy || probing}
            options={[[ 'com.android.deskclock', '时钟（预设）'], ['com.android.calculator2', '计算器（预设）'],
              ['com.android.calendar', '日历（预设）'], ['com.miHoYo.hkrpg', '崩坏：星穹铁道（预设）']]}
            onChange={value => updateAndroid('package', value)}/>
          <Field label="Android 策略"><Choice value={c.mobileforge ? 'mobileforge' : 'chat'} disabled={busy} onChange={e => update('mobileforge', e.target.value === 'mobileforge' ? 'D:/Tools/trace2task-android/MobileForge' : '')}>
            <option value="chat">通用视觉代理 + ADB</option><option value="mobileforge">MobileForge + ADB</option>
          </Choice></Field>
        </div>
        <details className="advanced-connection"><summary>高级连接设置 <span>ADB 路径 · 端口{c.mobileforge ? ' · 源码目录' : ''}</span></summary>
          <div className="composer-fields">
            <AndroidChoice label="ADB 路径" value={android.adb} disabled={busy || probing}
              options={[['D:/Tools/trace2task-android/sdk/platform-tools/adb.exe', '本机安装路径（预设）']]}
              onChange={value => updateAndroid('adb', value)}/>
            <Field label="ADB 服务端口"><Input type="number" value={String(android.server_port)} disabled={busy || probing} onChange={(_, d) => updateAndroid('server_port', Number(d.value))}/></Field>
            {!!c.mobileforge && <Field label="MobileForge 源码目录"><Input value={c.mobileforge} disabled={busy} onChange={(_, d) => update('mobileforge', d.value)}/></Field>}
          </div>
        </details>
        <Checkbox checked={!!android.launch_app} disabled={busy || probing} label="确认任务后自动打开授权应用（不重置）" onChange={(_, d) => updateAndroid('launch_app', d.checked === true)}/>
        <div className="button-row"><Button disabled={busy || probing} onClick={() => void prepareDevice('connect')}>连接云手机</Button>
          <Button disabled={busy || probing} onClick={() => void prepareDevice('check')}>启动前检查</Button>
          <Button disabled={busy || probing} onClick={probeDevice}>{probing ? '正在检查…' : '只读检查设备'}</Button></div>
        {deviceStatus && <p role="status">{deviceStatus}</p>}
        <p className="quiet">仅在设备未出现在列表时手动配置。连接按钮只连接填写的地址，不重启红手指或全局 ADB 服务。</p>
        </details>
        <details className="connection-help"><summary>连接说明与能力边界</summary><p>预设不代表设备在线或应用已安装；不同手机的应用包名可能不同。连接按钮只连接指定地址；运行中断线会停止，不自动重连。不重置或强停应用，不回退桌面；仅单指操作，不支持文本输入和跨应用操作。每轮截图交给模型判断，本机校验动作、通过 ADB 执行并再次截图；推理结果保存在运行记录中。</p></details>
      </div>}
      {(c.scope === 'browser' || c.executor === 'browser') && <p className="inline-warning">
        仅打开 ancientbeast.com 的全新无头 Edge 页面；不占用系统键鼠，不读取 DOM 或游戏内部状态，不回退前台。
        每次任务重新开局，结束即关闭页面。首版支持 Codex / 视觉 API，需要源码版 Node、Playwright 和 Edge。
      </p>}
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
      </div>
      <div className="composer-footer"><span className="quiet"><Icon name="shield" size={16}/>{remote ? `画面与经验将发送到 ${destination}` : '画面由本机服务处理'}</span>
        <Button appearance="primary" size="large" icon={<Icon name="arrow" size={18}/>} iconPosition="after" disabled={busy || submitting || !instruction.trim()}
          onClick={() => {const problem = validate(); if (problem) onError(problem); else setConfirm(true);}}>开始任务</Button></div>
    </section>
    <details className="context-strip"><summary>当前推理连接 · {engine?.label || '推理引擎'}</summary><p>{engine?.context_description}</p></details>
    <Dialog open={confirm} onOpenChange={(_, d) => {if (!submitting) setConfirm(d.open);}}>
      <DialogSurface className="confirmation-surface"><DialogBody className="confirmation-body"><DialogTitle className="confirmation-title">开始这一次任务？</DialogTitle><DialogContent className="confirmation-content">
        <p>{instruction}</p><dl className="facts-list"><dt>模型</dt><dd>{selectedModel}</dd><dt>操作范围</dt><dd>{c.scope === 'android' ? `仅 Android · ${android.serial} · ${android.package} · ${c.mobileforge ? 'MobileForge' : '通用视觉代理'} · 最多 ${budget.max_actions} 个动作 / ${budget.max_seconds / 60} 分钟` : c.scope === 'browser' ? '仅 Ancient Beast · 独立无头浏览器 · 不占用系统键鼠' : c.scope === 'desktop' ? '整个主显示器 · 占用键鼠' : c.targets.map(t => t.label).join('；')}</dd>
          <dt>经验方式</dt><dd>{c.experience === AUTO_MEMORY ? 'Agent 自动检索摘要，按需加载一份方法 B；也可不加载' : c.experience ? '手动固定版本' : '不使用经验'}</dd>
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
