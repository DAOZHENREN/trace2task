import {useEffect, useState} from 'react';
import {Button, Input} from '@fluentui/react-components';
import {api} from './api';
import {Choice, Field} from './ui';
import type {KnowledgeProfile} from './types';

type Device = {serial: string; server_port: number; adb: string; model: string; state: string; source?: string};
type App = {id: string; name: string};
const deviceKey = (d: Device) => d.server_port + '/' + d.serial;

export function KnowledgeApplicationPicker({scope, profiles, disabled, onChange}: {
  scope: KnowledgeProfile['scope']; profiles: KnowledgeProfile[]; disabled: boolean;
  onChange: (id: string, name: string) => void;
}) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [device, setDevice] = useState('');
  const [apps, setApps] = useState<App[]>([]);
  const [selected, setSelected] = useState('');
  const [custom, setCustom] = useState('');
  const [customName, setCustomName] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (disabled) return;
    const abort = new AbortController();
    setLoading(true); setError('');
    async function discover() {
      try {
        if (scope === 'desktop') {
          const result = await api<{apps: {name: string}[]}>('/api/cua/windows', {}, abort.signal);
          if (!abort.signal.aborted) setApps(result.apps.filter(a => a.name).map(a => ({id: a.name, name: a.name})));
        } else {
          const result = await api<{devices: Device[]}>('/api/android/devices', {}, abort.signal);
          if (abort.signal.aborted) return;
          const connected = result.devices.filter(d => ['device', 'forwarded'].includes(d.state));
          setDevices(connected);
          setDevice(old => connected.some(d => deviceKey(d) === old) ? old :
            connected.length === 1 ? deviceKey(connected[0]) : '');
        }
      } catch (e) {if (!abort.signal.aborted) setError('读取应用失败，可刷新重试或手动添加。' + String(e));}
      finally {if (!abort.signal.aborted) setLoading(false);}
    }
    void discover();
    return () => abort.abort();
  }, [scope, revision, disabled]);
  useEffect(() => {
    if (scope !== 'android' || disabled || !device) return;
    const found = devices.find(d => deviceKey(d) === device);
    if (!found) return;
    const abort = new AbortController();
    setLoading(true); setError(''); setApps([]);
    void api<{apps: {package: string; label: string}[]}>('/api/android/device-apps', {
      android_config: {adb: found.adb, serial: found.serial, server_port: found.server_port,
        package: '', launch_app: false}, source: found.source,
    }, abort.signal).then(result => {
      if (!abort.signal.aborted) setApps(result.apps.map(a => ({id: a.package, name: a.label || a.package})));
    }).catch(e => {if (!abort.signal.aborted) setError('读取手机应用失败，可刷新重试或手动添加。' + String(e));})
      .finally(() => {if (!abort.signal.aborted) setLoading(false);});
    return () => abort.abort();
  }, [device, devices, scope, disabled]);
  const options = new Map<string, App>();
  profiles.filter(p => p.scope === scope).forEach(p => options.set(p.application, {id: p.application, name: p.application}));
  apps.forEach(a => options.set(a.id, a));
  const visible = [...options.values()].filter(a => a.id === selected ||
    (a.name + ' ' + a.id).toLowerCase().includes(query.trim().toLowerCase()));
  function choose(id: string) {
    setSelected(id);
    setCustom(''); setCustomName('');
    onChange(id === '__manual__' ? '' : id, options.get(id)?.name || '');
  }
  return <>
    {scope === 'android' && <Field label="已连接手机"><Choice value={device} disabled={disabled || loading}
      onChange={e => {setDevice(e.target.value); setApps([]); choose('');}}>
      <option value="">{devices.length ? '选择手机' : '未发现已连接手机，可选择已有知识或手动添加'}</option>
      {devices.map(d => <option key={deviceKey(d)} value={deviceKey(d)}>{d.model || 'Android'} · {d.serial}</option>)}
    </Choice></Field>}
    <Field label="应用或游戏">
      <Input aria-label="搜索应用或游戏" placeholder="搜索应用名称" value={query} disabled={disabled}
        onChange={(_, d) => setQuery(d.value)}/>
      <Choice aria-label="选择应用或游戏" value={selected} disabled={disabled || loading}
        onChange={e => choose(e.target.value)}>
        <option value="">{loading ? '正在读取应用…' : '请选择应用或游戏'}</option>
        {selected && selected !== '__manual__' && !options.has(selected) && <option value={selected}>{selected}</option>}
        {visible.map(a => <option key={a.id} value={a.id}>{a.name}{a.name !== a.id ? ' · ' + a.id : ''}</option>)}
        <option value="__manual__">找不到应用，手动添加</option>
      </Choice>
    </Field>
    <Button disabled={disabled || loading} onClick={() => setRevision(v => v + 1)}>刷新应用列表</Button>
    {error && <p role="status">{error}</p>}
    {selected === '__manual__' && <>
      <Field label={scope === 'android' ? '应用包名' : '应用或游戏名称'}><Input value={custom} maxLength={120} disabled={disabled}
        onChange={(_, d) => {setCustom(d.value); onChange(d.value, scope === 'desktop' ? d.value : customName);}}/></Field>
      {scope === 'android' && <Field label="应用或游戏名称"><Input value={customName} maxLength={200} disabled={disabled}
        onChange={(_, d) => {setCustomName(d.value); onChange(custom, d.value);}}/></Field>}
    </>}
  </>;
}
