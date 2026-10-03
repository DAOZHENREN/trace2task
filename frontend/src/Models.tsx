import {useState} from 'react';
import {Button, Input, Spinner} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import type {Catalog, Config} from './types';
import {Choice, Field, Icon, PageHeading, Refresh} from './ui';

export function Models({catalog, config: c, setConfig, busy, refresh, onError, onComponents}: {
  catalog: Catalog; config: Config; setConfig: (c: Config) => void; busy: boolean; refresh: () => Promise<void>;
  onError: (e: string) => void; onComponents: () => void}) {
  const confirm = useConfirm();
  const [pending, setPending] = useState('');
  const [message, setMessage] = useState('');
  const [saved, setSaved] = useState(false);
  const update = (key: keyof Config, value: unknown) => setConfig({...c, [key]: value});
  async function service(action: string) {
    setPending(action); setMessage('');
    try {const result = await api<{message: string}>('/api/local-model/service', {action, model: c.localModel, backend: c.inference});
      await refresh(); setMessage(result.message);} catch (e) {onError(String(e));} finally {setPending('');}
  }
  async function saveApi() {
    setPending('api');
    try {await api('/api/model-settings/save', {base_url: c.apiUrl, api_key: c.apiKey, model: c.apiModel,
      response_format: c.apiFormat, timeout_seconds: c.apiTimeout, context_window_tokens: c.apiBudget, reasoning_effort: 'default'});
      setConfig({...c, apiKey: ''}); setSaved(true); setMessage('API 配置已由当前 Windows 账户加密保存；密钥未写入浏览器存储。');}
    catch (e) {onError(String(e));} finally {setPending('');}
  }
  async function clearApi() {
    if (!await confirm({title: '清除 API 配置？', body: '将清除本机保存的 API 配置和加密密钥。已归档的运行记录不变，之后需要重新配置。', confirmLabel: '清除配置', intent: 'danger'})) return;
    setPending('api');
    try {await api('/api/model-settings/clear', {}); setConfig({...c, apiKey: ''}); setSaved(false); setMessage('已清除本机加密配置；页面中的地址和模型仅为未保存草稿。');}
    catch (e) {onError(String(e));} finally {setPending('');}
  }
  const profile = catalog.models.find(p => p.id === c.localModel);
  const availability = profile?.availability[c.inference];
  return <>
    <PageHeading eyebrow="MODELS & CONNECTIONS" title="选模型，也选对运行方式。" copy="模型负责理解，推理引擎负责计算，执行后端负责操作。三者可以独立选择。"
      action={<Refresh busy={!!pending} onClick={() => void refresh().catch(e => onError(String(e)))}/>}/>
    <p className="quiet">本地模型默认优先 llama-server，已有明确配置保留。只展示已实现的兼容引擎；MAI-UI 暂仍为 Transformers 实验适配。</p>
    <div className="service-banner"><div className={`status-dot ${catalog.service.reachable ? '' : 'muted'}`}/><div>
      <strong>{catalog.service.reachable ? '本地 GUI 服务在线' : '本地 GUI 服务未连接'}</strong>
      <p>{catalog.service.reachable ? `${catalog.service.backend} · 当前加载 ${catalog.service.model || '尚无模型'}` : '云端模型不依赖本地 GPU 服务。启动本地模型前会校验文件和固定版本。'}</p>
    </div><span className="mono">127.0.0.1:8768</span></div>
    <div className="model-grid">{catalog.models.map(p => <button key={p.id} className={`model-card ${c.localModel === p.id ? 'selected' : ''}`}
      disabled={busy || !!pending} onClick={() => setConfig({...c, provider: 'local', localModel: p.id,
        inference: p.engines.includes(c.inference) ? c.inference : p.engines[0]})}>
      <div className="row-between"><div className="model-icon"><Icon name="models" size={24}/></div><span className="tag">{p.maturity === 'experimental' ? '实验适配' : '已接入'}</span></div>
      <h2>{p.label}</h2><p>{p.repository}</p><div className="engine-tags">{p.engines.map(b => <span key={b}>{b}</span>)}</div>
      <div className="model-card-foot">{Object.values(p.availability).some(a => a.files_present) ? '检测到模型文件 · 启动时校验' : '尚缺运行文件'}<Icon name={c.localModel === p.id ? 'check' : 'arrow'} size={17}/></div>
    </button>)}</div>
    <section className="surface engine-config"><div><span className="section-label">本地运行配置</span><h2>{profile?.label || c.localModel}</h2>
      <p>{profile?.note || '独立服务保持现有协议与生命周期。'}</p></div>
      <div className="engine-form"><Field label="推理引擎"><Choice aria-label="推理引擎" disabled={busy || !!pending || !profile} value={c.inference}
        onChange={e => update('inference', e.target.value)}>{(profile?.engines || [c.inference]).map(b => <option key={b}>{b}</option>)}</Choice></Field>
        <div className="button-row"><Button appearance="primary" disabled={busy || !!pending || availability?.files_present === false} onClick={() => void service('start')}>启动所选服务</Button>
          <Button disabled={busy || !!pending} onClick={() => void service('stop')}>关闭所选服务</Button></div>
      </div>
      {availability && !availability.files_present && <div className="missing-files"><strong>先准备这些文件</strong>{availability.missing.map(p => <code key={p}>{p}</code>)}
        <p>GGUF 使用注册表中的固定版本：2B 需本机转换，8B 使用官方量化文件；不会覆盖已有 GGUF。组件页可安装环境、下载模型和登记已有 D 模型。</p><Button size="small" onClick={onComponents}>管理组件与模型文件</Button></div>}
      {c.inference === 'llama-server' && !catalog.summary_model_present && <p className="inline-warning">摘要模型尚未找到；长对话压缩不可用，达到预算时会明确报错，不会声称全部文本永久保留。</p>}
      <p className="quiet">{catalog.inference_backends.find(b => b.id === c.inference)?.context_description} 文件存在不等于真实任务能力已通过验收。</p>
    </section>
    <section className="surface api-config"><div className="panel-heading"><div><span className="section-label">云端与兼容服务</span><h2>视觉模型 API</h2></div><span className="tag">OpenAI 兼容协议</span></div>
      <div className="two-fields"><Field label="API 地址"><Input type="url" value={c.apiUrl} disabled={busy || !!pending} onChange={(_, d) => update('apiUrl', d.value)}/></Field>
        <Field label="视觉模型 ID"><Input value={c.apiModel} disabled={busy || !!pending} onChange={(_, d) => update('apiModel', d.value)} placeholder="服务商提供的模型 ID"/></Field></div>
      <Field label="API Key" hint="只保存在当前页面内存；点击保存后使用 Windows 账户加密。不写入任务日志或浏览器存储。"><Input type="password" value={c.apiKey}
        autoComplete="off" disabled={busy || !!pending} placeholder={saved ? '已加密保存 · 留空沿用' : '输入密钥，或留空沿用已保存配置'} onChange={(_, d) => update('apiKey', d.value)}/></Field>
      <details><summary>兼容参数与预算</summary><div className="three-fields"><Field label="响应格式"><Choice value={c.apiFormat} disabled={busy || !!pending} onChange={e => update('apiFormat', e.target.value)}>
        <option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option></Choice></Field>
        <Field label="上下文预算 / tokens"><Input type="number" min={2048} value={String(c.apiBudget)} disabled={busy || !!pending} onChange={(_, d) => update('apiBudget', Number(d.value))}/></Field>
        <Field label="请求超时 / 秒"><Input type="number" min={1} max={600} value={String(c.apiTimeout)} disabled={busy || !!pending} onChange={(_, d) => update('apiTimeout', Number(d.value))}/></Field></div></details>
      <div className="row-between"><p className="quiet">截图与所选经验会发送到配置的服务。模型需支持图片输入，费用由服务商计算。</p>
        <div className="button-row"><Button disabled={busy || !!pending} onClick={() => void clearApi()}>清除已保存配置</Button><Button appearance="primary" disabled={busy || !!pending || !c.apiModel} onClick={() => void saveApi()}>加密保存配置</Button></div></div>
    </section>
    {pending && <div className="notice" role="status"><Spinner size="tiny" label={pending === 'start' ? '正在校验并加载模型，可能需要一两分钟…' : '正在处理…'}/></div>}
    {message && <div className="notice" role="status">{message}</div>}
  </>;
}
