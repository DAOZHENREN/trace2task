import {useEffect, useState} from 'react';
import {Button} from '@fluentui/react-components';
import {api} from './api';
import type {Config} from './types';

export const cloudPhoneDestination = '3090 服务器 · 经本机 SSH 隧道';
export const cloudPhoneSelected = (c: Config) => c.androidModelProfile === 'qwen3-vl-3090'
  && c.provider === 'api' && c.executor === 'android' && c.scope === 'android'
  && c.apiModel === 'Qwen3-VL-8B-Instruct' && c.apiUrl.replace(/\/$/, '') === 'http://127.0.0.1:18998/v1' && !!c.mobileforge;
type Profile = {id: string; model: string; base_url: string; mobileforge: string;
  timeout_seconds: number; context_window_tokens: number; android: NonNullable<Config['android']>};

export function CloudPhoneConnection({config: c, setConfig, busy, onError}: {
  config: Config; setConfig: (c: Config) => void; busy: boolean; onError: (message: string) => void;
}) {
  const [pending, setPending] = useState('');
  const [message, setMessage] = useState('');
  const selected = cloudPhoneSelected(c);
  useEffect(() => {setMessage('');}, [c.androidModelProfile, c.apiUrl, c.apiModel]);
  async function apply() {
    setPending('profile');
    try {
      const p = await api<Profile>('/api/android/model-profile');
      setConfig({...c, provider: 'api', executor: 'android', scope: 'android', experience: '',
        androidModelProfile: p.id, apiModel: p.model, apiUrl: p.base_url, apiKey: 'local-only',
        apiFormat: 'json_object', apiBudget: p.context_window_tokens, apiTimeout: p.timeout_seconds,
        mobileforge: p.mobileforge, android: {...p.android, ...c.android}});
    } catch (e) {onError(String(e));} finally {setPending('');}
  }
  async function connection(action: 'check' | 'connect') {
    setPending(action); setMessage('');
    try {
      const value = await api<{connected: boolean; message?: string; error?: string}>('/api/android/model-connection',
        {action, profile: c.androidModelProfile});
      setMessage(value.connected ? value.message || '3090 模型已连接' : value.error || '模型未就绪');
    } catch (e) {setMessage(String(e));} finally {setPending('');}
  }
  return <div className={`cloud-phone-connection${selected ? '' : ' compact'}`}>
    <div className="row-between"><div><strong>云手机控制台</strong>{selected && <p className="quiet">Trace2Task 下达任务 · 3090 模型推理 · 本机 ADB 执行</p>}</div>
      <div className="button-row"><Button disabled={busy || !!pending} appearance={selected ? 'secondary' : 'primary'} onClick={() => void apply()}>
        {pending === 'profile' ? '正在读取配置…' : selected ? '重新应用 3090 配置' : '使用 3090 控制云手机'}</Button>
        {c.androidModelProfile && <Button disabled={busy || !!pending} onClick={() => setConfig({...c, androidModelProfile: ''})}>自定义配置</Button>}</div></div>
    {selected && <>
      <p>Qwen3-VL-8B-Instruct · BF16 · <code>{c.apiUrl}</code></p>
      <div className="button-row"><Button disabled={busy || !!pending} onClick={() => void connection('check')}>检查模型连接</Button>
        <Button disabled={busy || !!pending} onClick={() => void connection('connect')}>{pending === 'connect' ? '正在连接…' : '连接 / 复用 SSH 隧道'}</Button></div>
      {message && <p role="status">{message}</p>}
      <p className="quiet">先连接 VPN，再检查模型与手机。选择配置或连接隧道不会下发任务、点击手机或重启服务器模型；截图仅在确认任务后送往 3090。</p>
    </>}
  </div>;
}
