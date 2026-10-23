import {useRef, useState} from 'react';
import {Button} from '@fluentui/react-components';
import {api} from './api';
import type {Config} from './types';
import {Choice, Field} from './ui';

type Android = NonNullable<Config['android']>;
type Device = {serial: string; state: string; model: string; server_port: number; adb: string; source?: string};
type Apps = {apps: {package: string; label: string}[]; current_package: string};
const key = (d: {serial: string; server_port: number}) => `${d.server_port}/${d.serial}`;

export function AndroidDevices({value, onChange, busy, onError}: {
  value: Android; onChange: (value: Android) => void; busy: boolean; onError: (message: string) => void;
}) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [apps, setApps] = useState<Apps['apps']>([]);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const generation = useRef(0);
  async function select(device: Device) {
    const token = ++generation.current;
    const next = {...value, adb: device.adb, serial: device.serial, server_port: device.server_port, package: '', launch_app: false};
    onChange(next); setApps([]); setPending(true); setMessage('正在读取所选手机的应用…');
    try {
      const result = await api<Apps>('/api/android/device-apps', {android_config: next, source: device.source});
      if (token !== generation.current) return;
      setApps(result.apps);
      const current = result.current_package;
      // A launcher is not the game/app the user intends to authorize.
      onChange({...next, package: /launcher|systemui/.test(current) ? '' : current});
      setMessage('已复用现有连接 · 请选择授权应用后发任务 · 未发送手机输入');
    } catch (e) {if (token === generation.current) {setMessage('读取应用失败，请重新刷新设备'); onError(String(e));}}
    finally {if (token === generation.current) setPending(false);}
  }
  async function refresh() {
    ++generation.current; setPending(true); setApps([]); setMessage('正在读取已有 ADB 服务…');
    try {
      const result = await api<{devices: Device[]; warnings: string[]}>('/api/android/devices', {android_config: value});
      setDevices(result.devices);
      const existing = result.devices.find(d => key(d) === key(value) && d.state === 'device');
      if (existing) {await select(existing); return;}
      onChange({...value, serial: '', package: '', launch_app: false});
      setMessage(result.devices.length ? '请选择一台在线设备；不会自动切换手机。'
        : '未发现已连接设备。请先在红手指建立 ADB 连接，再刷新。' + (result.warnings.some(w => w.includes('版本不兼容')) ? ' 检测到不兼容的 ADB 服务，未修改它。' : ''));
    } catch (e) {setDevices([]); onChange({...value, serial: '', package: ''}); setMessage('设备刷新失败'); onError(String(e));}
    finally {setPending(false);}
  }
  return <>
    <div className="button-row"><Button disabled={busy || pending} onClick={() => void refresh()}>{pending ? '正在读取设备…' : '刷新已连接设备'}</Button></div>
    <div className="composer-fields">
      <Field label="已连接设备"><Choice value={value.serial ? key(value) : ''} disabled={busy || pending} onChange={e => {
        const device = devices.find(d => key(d) === e.target.value);
        if (device && ['device', 'forwarded'].includes(device.state)) void select(device);
      }}>
        <option value="">请选择已连接的云手机</option>
        {value.serial && !devices.some(d => key(d) === key(value)) && <option value={key(value)}>上次 / 手动设备 · {value.serial}（请刷新）</option>}
        {devices.map(d => <option key={key(d)} value={key(d)} disabled={!['device', 'forwarded'].includes(d.state)}>
          {d.model || 'Android'} · {d.serial} · ADB {d.server_port}{d.state === 'forwarded' ? ' · 选择后接入' : d.state === 'device' ? '' : ` · ${d.state}`}
        </option>)}
      </Choice></Field>
      <Field label="授权应用"><Choice value={value.package} disabled={busy || pending || !value.serial} onChange={e => onChange({...value, package: e.target.value, launch_app: true})}>
        <option value="">选择手机上的应用</option>
        {value.package && !apps.some(a => a.package === value.package) && <option value={value.package}>{value.package}（请刷新）</option>}
        {apps.map(a => <option key={a.package} value={a.package}>{a.label}{a.label !== a.package ? ` · ${a.package}` : ''}</option>)}
      </Choice></Field>
    </div>
    {message && <p role="status">{message}</p>}
    <p className="quiet">复用红手指已有连接；若只有转发通道，选中后自动接入本机 ADB，不重启红手指。任务确认框会明确显示授权应用。</p>
  </>;
}
