import {useEffect, useState} from 'react';
import {Button, Spinner} from '@fluentui/react-components';
import {api} from './api';

type BrowserState = {status: string; navigation: string; host: string; error: string;
  running: boolean; profile_present: boolean; model_connection: 'unverified'};

export function ChatGPTBrowser({busy}: {busy: boolean}) {
  const [state, setState] = useState<BrowserState | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    const read = async () => {
      try {
        const result = await api<BrowserState>('/api/chatgpt-browser', undefined, controller.signal);
        if (!controller.signal.aborted) {setState(result); setError('');}
      } catch (e) {if (!controller.signal.aborted) setError(String(e));}
    };
    void read();
    const timer = setInterval(() => void read(), 2500);
    return () => {controller.abort(); clearInterval(timer);};
  }, []);
  async function control(action: 'open' | 'close') {
    setPending(true); setError('');
    try {setState(await api<BrowserState>('/api/chatgpt-browser', {action}));}
    catch (e) {setError(String(e));} finally {setPending(false);}
  }
  const label = state?.status === 'starting' ? '正在打开浏览器…'
    : state?.status === 'error' || state?.navigation === 'failed' ? state.error || '页面加载失败'
      : !state?.running ? '窗口已关闭'
        : state.navigation === 'ready' ? '网页已加载，请在窗口内确认登录'
          : '正在加载网页…';
  return <section className="surface api-config chatgpt-browser" aria-label="ChatGPT 网页会话">
    <div className="panel-heading"><div><span className="section-label">网页会话</span>
      <h2>ChatGPT 内置浏览器</h2></div><span className="tag">实验功能 · 接口待验证</span></div>
    <p>在独立小窗口中登录 ChatGPT。登录会话保存在本机，关闭窗口后保留，下次打开继续使用。</p>
    <div className="row-between"><p role="status">{state ? label : '正在读取浏览器状态…'}
      {state?.running && state.host && <span className="quiet"> · {state.host}</span>}</p>
      <div className="button-row">
        {pending && <Spinner size="tiny"/>}
        <Button disabled={pending || !state?.running} onClick={() => void control('close')}>关闭浏览器窗口</Button>
        <Button appearance="primary" disabled={busy || pending} onClick={() => void control('open')}>
          {state?.running ? '显示 ChatGPT 窗口' : '打开 ChatGPT 登录窗口'}</Button>
      </div></div>
    <p className="quiet">目前用于登录和网页连接验证，尚未接入任务推理。网页加载成功不代表模型接口已连接。</p>
    {error && <p className="inline-warning" role="alert">{error}</p>}
  </section>;
}
