import {useMemo, useState} from 'react';
import {Button} from '@fluentui/react-components';
import {modelInput} from './modelInputData';
import type {InputMessage, RawRound} from './modelInputData';

const roles: Record<string, string> = {user: '用户', assistant: '助手', system: '系统', developer: '开发者', tool: '工具'};

function InputPicture({path, label}: {path?: string; label: string}) {
  const [failed, setFailed] = useState(false);
  const url = path ? `/api/local-image?path=${encodeURIComponent(path)}` : '';
  return <figure className="model-input-image"><figcaption>{label}</figcaption>
    {path && !failed ? <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={label}
      loading="lazy" onError={() => setFailed(true)}/><span>打开原图</span></a>
      : <p className="quiet">{failed ? '图片加载失败，原记录仍保留。' : '记录包含此图片，但未保存可用的预览路径。'}</p>}
  </figure>;
}

function Messages({messages}: {messages: InputMessage[]}) {
  return <div className="model-input-messages">{messages.map(message => <div className="model-input-message" key={message.index}>
    <div className="model-input-role">{roles[message.role] || message.role} <span>· 消息 {message.index + 1}</span></div>
    {message.parts.map((part, index) => part.kind === 'text' ? <pre className="model-input-text" key={index}>{part.text}</pre>
      : part.kind === 'image' ? <InputPicture key={`${index}:${part.path}`} path={part.path} label={part.label}/>
      : <details key={index}><summary>其他消息内容（原文）</summary><pre>{part.text}</pre></details>)}
    {!message.parts.length && <p className="quiet">空消息</p>}
  </div>)}</div>;
}

export function ModelInput({raw, provider}: {raw: RawRound; provider?: string}) {
  const view = useMemo(() => modelInput(raw, provider), [raw, provider]);
  const [copyStatus, setCopyStatus] = useState('');
  async function copy() {
    try {await navigator.clipboard.writeText(view.copyText); setCopyStatus('已复制本轮文字（不含图片）');}
    catch {setCopyStatus('复制失败，请直接选中文字复制。');}
  }
  return <section className="model-input" aria-label="模型输入">
    <div className="row-between"><h4>模型输入</h4>{view.copyText && <Button size="small" appearance="subtle" onClick={() => void copy()}>复制本轮文字</Button>}</div>
    <p className="model-input-hint">按记录展示原文，不二次摘要或截断；历史消息和系统提示词单独展开。{view.imageCount > 0 && ` 记录含 ${view.imageCount} 张图片。`}</p>
    {view.notices.map(notice => <p className="model-input-notice" key={notice}>{notice}</p>)}
    <Messages messages={view.current}/>
    {copyStatus && <p className="quiet" role="status">{copyStatus}</p>}
    {view.history.length > 0 && <details className="model-input-extra"><summary>本轮携带的历史消息 · {view.history.length} 条</summary><Messages messages={view.history}/></details>}
    {view.system.length > 0 && <details className="model-input-extra"><summary>系统 / 开发者提示词 · {view.system.length} 条</summary><Messages messages={view.system}/></details>}
  </section>;
}
