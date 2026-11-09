import {useState} from 'react';
import {Button} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import type {ChildDesktopState} from './types';

type Command = 'refresh' | 'view' | 'control' | 'return' | 'hide' | 'close';

export function ChildDesktopConnection({desktop, busy, refresh}: {
  desktop?: ChildDesktopState; busy: boolean; refresh: () => Promise<void>;
}) {
  const confirm = useConfirm();
  const [pending, setPending] = useState<Command | null>(null);
  const [error, setError] = useState('');
  const status = desktop?.status || 'closed';
  const connected = !!desktop?.session_id && ['waiting_picture', 'idle', 'in_use'].includes(status);
  const manual = !!desktop?.manual_control;
  const statusText = {
    closed: desktop?.generation ? '后台桌面已关闭。' : '首次任务会启动后台桌面，也可以现在打开。',
    connecting: '正在连接后台桌面。如果 Windows 要求验证，请在登录窗口确认。',
    waiting_picture: '后台桌面已连接，可打开实时画面。',
    in_use: '模型正在操作后台桌面。',
    idle: `后台桌面已就绪（会话 ${desktop?.session_id}）。下一项任务可以继续使用已打开的应用。`,
    closing: '正在关闭后台桌面。',
    logoff_pending: `Windows 中仍有后台会话 ${desktop?.session_id}，正在等待注销确认。可刷新状态。`,
    logoff_unknown: `暂时无法从 Windows 核实后台会话 ${desktop?.session_id} 是否已注销。可刷新状态，或在任务管理器的「用户」页核对会话编号。`,
    disconnected: '后台桌面连接已断开，可以重新连接。',
  }[status];
  async function manage(command: Command) {
    if (pending) return;
    if (command === 'close' && !await confirm({title: '关闭后台桌面？',
      body: '将注销这个 Windows 子会话并关闭其中的应用。请先保存需要保留的内容。', confirmLabel: '确认关闭', intent: 'danger'})) return;
    setPending(command); setError('');
    try {
      if (command === 'close') await api('/api/child-desktop/close', {generation: desktop?.generation});
      else if (command !== 'refresh') await api('/api/child-desktop/viewer', {mode: command, generation: desktop?.generation ?? 0});
      await refresh();
    } catch (e) {setError(String(e));} finally {setPending(null);}
  }
  return <section className="target-picker" aria-label="后台桌面管理">
    <div className="setup-heading"><strong>后台桌面管理</strong>
      <span className="tag">{desktop?.session_id ? `Windows · 会话 ${desktop.session_id}` : 'Windows'}</span></div>
    <p className="quiet" role="status">{manual ? `你正在手动操作。${busy ? '交还模型后会重新截图。' : '已打开的应用会保留给下一个任务。'}` : statusText}</p>
    <div className="button-row">
      {['closed', 'disconnected'].includes(status) && <Button appearance="primary" disabled={!!pending || busy}
        onClick={() => void manage('control')}>{pending === 'control' ? '正在连接…' : status === 'closed' ? '打开后台桌面' : '重新连接'}</Button>}
      {connected && <>
        <Button appearance="primary" disabled={!!pending} onClick={() => void manage('view')}>查看实时桌面</Button>
        <Button disabled={!!pending} onClick={() => void manage(manual ? 'return' : 'control')}>
          {manual ? busy ? '交还模型' : '结束手动操作' : busy ? '接管桌面' : '手动操作'}</Button>
        <Button disabled={!!pending} onClick={() => void manage('hide')}>隐藏窗口</Button>
      </>}
      <Button disabled={!!pending} onClick={() => void manage('refresh')}>{pending === 'refresh' ? '正在刷新…' : '刷新状态'}</Button>
      {['idle', 'waiting_picture', 'disconnected'].includes(status) && <Button disabled={busy || !!pending}
        onClick={() => void manage('close')}>{pending === 'close' ? '正在关闭…' : '关闭后台桌面'}</Button>}
    </div>
    {error && <p className="error-inline" role="alert">{error}</p>}
  </section>;
}
