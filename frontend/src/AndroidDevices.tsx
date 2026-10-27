import {useEffect, useRef, useState} from 'react';
import {Button} from '@fluentui/react-components';
import {api} from './api';
import type {Config} from './types';
import {Choice, Field} from './ui';

type Android = NonNullable<Config['android']>;
type Device = {serial: string; state: string; model: string; server_port: number; adb: string; source?: string};
type Apps = {apps: {package: string; label: string}[]; current_package: string};
const key = (d: {serial: string; server_port: number}) => `${d.server_port}/${d.serial}`;

export function AndroidDevices({value, onChange, busy, onError, autoConnect, onPendingChange}: {
  value: Android; onChange: (value: Android) => void; busy: boolean; onError: (message: string) => void;
  autoConnect: boolean; onPendingChange: (pending: boolean) => void;
}) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [apps, setApps] = useState<Apps['apps']>([]);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const automaticStarted = useRef(false);
  const latest = useRef({value, onChange, onError, onPendingChange});
  latest.current = {value, onChange, onError, onPendingChange};
  const working = (next: boolean) => {setPending(next); latest.current.onPendingChange(next);};
  const current = (token: number, signal: AbortSignal) => token === generation.current && !signal.aborted;
  function begin() {
    request.current?.abort();
    const abort = new AbortController(); request.current = abort;
    working(true); setApps([]);
    return {token: ++generation.current, signal: abort.signal};
  }
  async function loadDevice(device: Device, token: number, signal: AbortSignal, cached?: Apps) {
    const previous = latest.current.value;
    const next = {...previous, adb: device.adb, serial: device.serial, server_port: device.server_port, package: '', launch_app: false};
    latest.current.onChange(next); setMessage('正在读取所选手机的应用…');
    const result = cached || await api<Apps>('/api/android/device-apps', {android_config: next, source: device.source}, signal);
    if (!current(token, signal)) return;
    setApps(result.apps);
    const kept = key(previous) === key(device) && result.apps.some(app => app.package === previous.package);
    // Refreshing the same phone preserves an explicitly chosen installed app.
    // A launcher is not the app the user intends to authorize.
    const selected = kept ? previous.package : /launcher|systemui/.test(result.current_package) ? '' : result.current_package;
    latest.current.onChange({...next, package: selected, launch_app: kept && !!previous.launch_app});
    setMessage('手机已连接 · 请核对授权应用后发任务');
  }
  async function select(device: Device) {
    const {token, signal} = begin();
    try {await loadDevice(device, token, signal);}
    catch (e) {if (current(token, signal)) {setMessage('读取应用失败，请重新刷新设备'); latest.current.onError(String(e));}}
    finally {if (current(token, signal)) working(false);}
  }
  async function refresh(connect: boolean) {
    const {token, signal} = begin();
    const previous = latest.current.value;
    setMessage('正在刷新设备与云手机通道…');
    try {
      const result = await api<{devices: Device[]; warnings: string[]}>('/api/android/devices', {android_config: previous}, signal);
      if (!current(token, signal)) return;
      const found = result.devices.map(device => ({...device}));
      const connectedApps = new Map<string, Apps>();
      const failures: string[] = [];
      for (const device of found) {
        if (!connect || device.state !== 'forwarded' || device.source !== 'redfinger') continue;
        if (!current(token, signal)) return;
        setMessage(`正在连接云手机 ${device.serial}…`);
        try {
          // The existing endpoint rechecks vendor ownership before connecting.
          const apps = await api<Apps>('/api/android/device-apps', {source: device.source,
            android_config: {...previous, adb: device.adb, serial: device.serial, server_port: device.server_port,
              package: '', launch_app: false}}, signal);
          if (!current(token, signal)) return;
          connectedApps.set(key(device), apps);
          device.state = 'device'; device.source = undefined;
        } catch (e) {if (!current(token, signal)) return; failures.push(`${device.serial}：${String(e)}`);}
      }
      setDevices(found);
      const available = found.filter(device => device.state === 'device');
      const existing = available.find(device => key(device) === key(previous));
      const selected = existing || (available.length === 1 ? available[0] : undefined);
      if (selected) await loadDevice(selected, token, signal, connectedApps.get(key(selected)));
      else {
        latest.current.onChange({...previous, serial: '', package: '', launch_app: false});
        setMessage(available.length ? `发现 ${available.length} 台在线设备，请选择本次使用的手机。`
          : '未发现已连接设备。请先在红手指开启 ADB 通道，再刷新。'
            + (result.warnings.some(w => w.includes('版本不兼容')) ? ' 检测到不兼容的 ADB 服务，未修改它。' : ''));
      }
      if (current(token, signal) && failures.length) setMessage(text => `${text} 未连接通道：${failures.join('；')}`);
    } catch (e) {if (current(token, signal)) {setDevices([]); setMessage('设备刷新失败，可点击刷新重试'); latest.current.onError(String(e));}}
    finally {if (current(token, signal)) working(false);}
  }
  useEffect(() => {
    if (busy) {
      ++generation.current; request.current?.abort(); working(false);
      return;
    }
    if (autoConnect && !busy && !automaticStarted.current) {
      automaticStarted.current = true;
      void refresh(true);
    }
  }, [autoConnect, busy]);
  useEffect(() => () => {
    ++generation.current; request.current?.abort(); automaticStarted.current = false;
    latest.current.onPendingChange(false);
  }, []);
  return <>
    <div className="button-row"><Button disabled={busy || pending} onClick={() => void refresh(autoConnect)}>{pending ? '正在读取设备…' : '刷新已连接设备'}</Button></div>
    <div className="composer-fields">
      <Field label="已连接设备"><Choice value={value.serial ? key(value) : ''} disabled={busy || pending} onChange={e => {
        const device = devices.find(d => key(d) === e.target.value);
        if (device && ['device', 'forwarded'].includes(device.state)) void select(device);
      }}>
        <option value="">请选择已连接的云手机</option>
        {value.serial && !devices.some(d => key(d) === key(value)) && <option value={key(value)}>上次 / 手动设备 · {value.serial}（请刷新）</option>}
        {devices.map(d => <option key={key(d)} value={key(d)} disabled={!['device', 'forwarded'].includes(d.state)}>
          {d.model || 'Android'} · {d.serial} · ADB {d.server_port}{d.state === 'forwarded' ? ' · 选择后重试连接' : d.state === 'device' ? '' : ` · ${d.state}`}
        </option>)}
      </Choice></Field>
      <Field label="授权应用"><Choice value={value.package} disabled={busy || pending || !value.serial} onChange={e => onChange({...value, package: e.target.value, launch_app: true})}>
        <option value="">选择手机上的应用</option>
        {value.package && !apps.some(a => a.package === value.package) && <option value={value.package}>{value.package}（请刷新）</option>}
        {apps.map(a => <option key={a.package} value={a.package}>{a.label}{a.label !== a.package ? ` · ${a.package}` : ''}</option>)}
      </Choice></Field>
    </div>
    {message && <p role="status">{message}</p>}
    <p className="quiet">选择 Android 范围后自动刷新设备并接入红手指可用通道；仅一台在线时自动选中，多台时由你选择。任务确认框会显示手机与授权应用。</p>
  </>;
}
