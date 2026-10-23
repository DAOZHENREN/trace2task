import {useEffect, useState} from 'react';
import {Button, Checkbox} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';

export type BatchItem = {id: string; name: string; endpoint: string; payload: object; disabled?: boolean};

// Selection is scoped to the visible category/filter. Every failed item remains selected.
export function useBatchTrash(items: BatchItem[], busy: boolean, refresh: () => Promise<void>, onError: (text: string) => void) {
  const confirm = useConfirm();
  const [selected, setSelected] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const eligible = items.filter(item => !item.disabled);
  const signature = JSON.stringify(eligible.map(item => item.id));
  useEffect(() => {setSelected(old => old.filter(id => eligible.some(item => item.id === id)));}, [signature]);
  const chosen = eligible.filter(item => selected.includes(item.id));
  const disabled = busy || pending;
  const checkbox = (id: string, name: string, compact = false) => <Checkbox aria-label={`选择 ${name}`} label={compact ? undefined : `选择 ${name}`} checked={selected.includes(id)}
    disabled={disabled || !eligible.some(item => item.id === id)} onChange={(_, data) => setSelected(old => data.checked ? [...old, id] : old.filter(key => key !== id))}/>;
  async function move() {
    if (!chosen.length || disabled) return;
    if (!await confirm({title: `将 ${chosen.length} 项移到回收站？`, confirmLabel: '批量移到回收站',
      body: <><p>仅处理以下勾选项，不连带删除关联录制、经验或运行记录。</p><ul>{chosen.map(item => <li key={item.id}>{item.name}</li>)}</ul></>,
      note: '可以在全局回收站恢复，不会永久删除。正在运行或录制时不可操作。'})) return;
    setPending(true); setMessage('');
    const succeeded: string[] = []; const failures: string[] = [];
    try {
      for (const item of chosen) {
        try {await api(item.endpoint, item.payload); succeeded.push(item.id);}
        catch (e) {failures.push(`${item.name}：${String(e)}`);}
      }
      setSelected(old => old.filter(id => !succeeded.includes(id)));
      setMessage(`已移到回收站 ${succeeded.length} 项${failures.length ? `，失败 ${failures.length} 项（保留勾选）` : ''}。可在全局回收站恢复。`);
      if (failures.length) onError(failures.join('\n'));
      await refresh();
    } catch (e) {onError(`操作已执行，但列表刷新失败，请刷新确认：${String(e)}`);}
    finally {setPending(false);}
  }
  return {checkbox, pending, toolbar: <><div className="batch-toolbar">
    <Checkbox label="全选当前列表" disabled={disabled || !eligible.length}
      checked={chosen.length === eligible.length && eligible.length > 0 ? true : chosen.length ? 'mixed' : false}
      onChange={(_, data) => setSelected(data.checked ? eligible.map(item => item.id) : [])}/>
    <span>已选 {chosen.length} 项</span>
    <Button disabled={disabled || !chosen.length} onClick={() => void move()}>{pending ? '正在移入…' : '批量移到回收站'}</Button>
  </div>{message && <p className="notice" role="status">{message}</p>}</>};
}
