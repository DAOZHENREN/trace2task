import {useState} from 'react';
import {Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Input, Textarea} from '@fluentui/react-components';
import {api, requestFor} from './api';
import {Dictation, dictationActive} from './audio';
import {usableTrace, versionLabel} from './experience';
import type {AppState, Catalog, Config, Run, Target} from './types';
import {Choice, Field, Icon} from './ui';

type Props = {config: Config; setConfig: (next: Config) => void; state: AppState; catalog: Catalog;
  busy: boolean; promptDirty: boolean; onRun: (run: Run) => void; onModels: () => void; onError: (message: string) => void};

export function TaskComposer({config: c, setConfig, state, catalog, busy, promptDirty, onRun, onModels, onError}: Props) {
  const [instruction, setInstruction] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [choices, setChoices] = useState<{label: string; value: Target}[]>([]);
  const [search, setSearch] = useState('');
  const [loadingTargets, setLoadingTargets] = useState(false);
  const update = (key: keyof Config, value: unknown) => {
    const next = {...c, [key]: value};
    if (key === 'localModel') {
      const p = catalog.models.find(p => p.id === value);
      if (p && !p.engines.includes(c.inference)) next.inference = p.engines[0];
    }
    setConfig(next);
  };
  const profile = catalog.models.find(p => p.id === c.localModel);
  const engine = catalog.inference_backends.find(e => e.id === (c.provider === 'codex' ? 'codex' : c.provider === 'api'
    ? 'chat-completions' : c.localModel === 'trained_d' ? 'frozen-d' : c.inference));
  const remote = c.provider === 'codex' || (c.provider === 'api' && !isLocal(c.apiUrl));
  const destination = c.provider === 'codex' ? 'Codex 订阅服务' : c.provider === 'api' ? host(c.apiUrl) : '本机 GPU';
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
    if (c.provider === 'api' && !c.apiModel.trim()) return '请到「模型与连接」填写视觉模型 ID 和 API 地址。';
    if (c.scope === 'selected_windows' && (c.executor !== 'cua' || !c.targets.length || c.targets.length > 12)) return '指定窗口需要 Cua，并明确选择 1–12 个目标。';
    if (c.localModel === 'trained_d' && c.provider === 'local' && c.experience) return 'D-5970 冻结协议不能使用 A/D 经验。';
    if (c.provider === 'local' && profile && (!catalog.service.reachable || catalog.service.backend !== c.inference))
      return '请先在「模型与连接」启动所选推理引擎；不会静默切换到其他引擎。';
    return '';
  }
  async function start() {
    setSubmitting(true);
    try { const run = await api<Run>('/api/jobs', requestFor(c, instruction.trim())); setConfirm(false); onRun(run); }
    catch (e) { onError(String(e)); setConfirm(false); } finally { setSubmitting(false); }
  }
  return <>
    <section className="composer surface">
      <div className="composer-top"><span className="section-label">新任务</span><div className="button-row"><span className="quiet">以当前画面为准 · 不回放旧坐标</span><Dictation disabled={busy} onError={onError} onAppend={text => setInstruction(value => `${value} ${text}`.trim().slice(0, 2000))}/></div></div>
      <Textarea className="task-textarea" aria-label="任务指令" placeholder="描述你想完成的事，例如：把今天的待办整理到记事本，按优先级排序。"
        value={instruction} onChange={(_, d) => setInstruction(d.value)} maxLength={2000} resize="vertical" disabled={busy}/>
      <div className="composer-fields">
        <Field label="模型来源"><Choice value={c.provider} disabled={busy} onChange={e => update('provider', e.target.value)}>
          <option value="codex">Codex 订阅</option><option value="local">本地模型</option><option value="api">视觉模型 API</option>
        </Choice></Field>
        <Field label="执行模型">{c.provider === 'api' ? <Input value={c.apiModel} disabled={busy} placeholder="模型 ID"
          onChange={(_, d) => update('apiModel', d.value)}/> : <Choice disabled={busy} value={c.provider === 'codex' ? c.model : c.localModel}
          onChange={e => update(c.provider === 'codex' ? 'model' : 'localModel', e.target.value)}>
          {c.provider === 'codex' ? state.agent_options.models.map(m => <option key={m}>{m}</option>) : <>
            {catalog.models.map(p => <option value={p.id} key={p.id}>{p.label}{p.maturity === 'experimental' ? ' · 实验' : ''}</option>)}
            <option value="trained_d">D-5970（研究）</option>
          </>}
        </Choice>}</Field>
        <Field label="参考经验"><Choice value={c.experience} disabled={busy} onChange={e => update('experience', e.target.value)}>
          <option value="">不使用经验</option>{state.trace_representations.filter(usableTrace).map(t =>
            <option key={t.id} value={`trace-library/${t.id}/model-input.txt`}>{versionLabel(t)}</option>)}
        </Choice></Field>
      </div>
      <div className="scope-row">
        <Field label="操作范围"><Choice value={c.scope} disabled={busy} onChange={e => update('scope', e.target.value)}>
          <option value="desktop">全桌面 · 可跨应用</option><option value="selected_windows">仅指定窗口 / 应用</option>
        </Choice></Field>
        <Field label="执行后端"><Choice value={c.executor} disabled={busy} onChange={e => update('executor', e.target.value)}>
          <option value="win32">Win32 · 前台</option><option value="cua">Cua · 按范围执行</option>
        </Choice></Field>
        <Button appearance="subtle" className="connection-link" onClick={onModels}>连接与引擎 <Icon name="arrow" size={16}/></Button>
      </div>
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
      <div className="composer-footer"><span className="quiet"><Icon name="shield" size={16}/>{remote ? `画面与经验将发送到 ${destination}` : '画面由本机服务处理'}</span>
        <Button appearance="primary" size="large" icon={<Icon name="arrow" size={18}/>} iconPosition="after" disabled={busy || submitting || !instruction.trim()}
          onClick={() => {const problem = validate(); if (problem) onError(problem); else setConfirm(true);}}>开始任务</Button></div>
    </section>
    <div className="context-strip"><Icon name="models" size={17}/><span><strong>{engine?.label || '推理引擎'}</strong> · {engine?.context_description}</span></div>
    <Dialog open={confirm} onOpenChange={(_, d) => {if (!submitting) setConfirm(d.open);}}>
      <DialogSurface className="confirmation-surface"><DialogBody className="confirmation-body"><DialogTitle className="confirmation-title">开始这一次任务？</DialogTitle><DialogContent className="confirmation-content">
        <p>{instruction}</p><dl className="facts-list"><dt>模型</dt><dd>{selectedModel}</dd><dt>操作范围</dt><dd>{c.scope === 'desktop' ? '整个主显示器 · 占用键鼠' : c.targets.map(t => t.label).join('；')}</dd>
          <dt>数据去向</dt><dd>{destination} {remote ? '· 发送截图与相关经验' : '· 本机处理'}</dd></dl>
        <p className="inline-warning">开始后连续执行，无需逐步确认。请先关闭敏感内容；使用顶部停止按钮或 F9 中止后续动作。当前驱动调用可能需要等待返回。</p>
      </DialogContent><DialogActions className="confirmation-actions"><Button disabled={submitting} onClick={() => setConfirm(false)}>返回修改</Button>
        <Button appearance="primary" disabled={submitting} onClick={start}>{submitting ? '正在启动…' : '确认开始'}</Button></DialogActions></DialogBody></DialogSurface>
    </Dialog>
  </>;
}

export function host(url: string) {try {return new URL(url).hostname;} catch {return '尚未配置的 API';}}
export function isLocal(url: string) {return ['127.0.0.1', 'localhost', '[::1]'].includes(host(url));}
