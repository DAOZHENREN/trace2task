import {useState} from 'react';
import {Button, Input, Textarea} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import type {AppState, KnowledgeProfile} from './types';
import {Choice, Empty, Field, Icon} from './ui';
import {useAction} from './workflow';
import {KnowledgeGeneration, type KnowledgeDraft} from './KnowledgeGeneration';

type Knowledge = KnowledgeProfile & {content: string};

export function ApplicationKnowledge({state, profiles, busy, refresh, onError}: {
  state: AppState; profiles: KnowledgeProfile[]; busy: boolean; refresh: () => Promise<void>; onError: (message: string) => void;
}) {
  const [selected, setSelected] = useState<Knowledge | null>(null);
  const [scope, setScope] = useState<KnowledgeProfile['scope']>('android');
  const [application, setApplication] = useState('');
  const [content, setContent] = useState('');
  const [message, setMessage] = useState('');
  const [filter, setFilter] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<KnowledgeDraft | null>(null);
  const {pending, perform} = useAction(onError);
  const confirm = useConfirm();
  const disabled = !!pending || busy;
  const query = filter.trim().toLowerCase();
  const visible = profiles.filter(item => `${item.application} ${item.scope === 'android' ? 'Android' : 'Windows'}`
    .toLowerCase().includes(query));
  function load(id: string) {
    void perform('read', async () => {
      const item = id ? await api<Knowledge>('/api/application-knowledge/read', {id}) : null;
      setDraft(null); setSelected(item); setScope(item?.scope || 'android'); setApplication(item?.application || '');
      setContent(item?.content || ''); setMessage(''); setEditing(true);
    });
  }
  async function back() {
    const changed = content !== (selected?.content || '') || (!selected && !!application.trim());
    if (changed && !await confirm({title: '放弃未保存的底层知识？',
      body: '返回列表会放弃这次编辑。', confirmLabel: '放弃编辑'})) return;
    setEditing(false);
  }
  function save() {
    void perform('save', async () => {
      const item = await api<Knowledge>('/api/application-knowledge/save', {
        scope, application, content, expected_sha256: selected?.sha256 || '',
      });
      setSelected(item); setApplication(item.application); setContent(item.content);
      await refresh();
      setMessage(`已保存 ${item.application} · v${item.revision}`);
    });
  }
  if (!editing) return <section aria-label="底层知识管理">
    <KnowledgeGeneration state={state} profiles={profiles} busy={disabled} onError={onError} onDraft={value => {
      void perform('read', async () => {
        const existing = profiles.find(p => p.scope === value.request.scope &&
          (p.scope === 'android' ? p.application === value.request.application :
            p.application.toLowerCase() === value.request.application.toLowerCase()));
        const item = existing ? await api<Knowledge>('/api/application-knowledge/read', {id: existing.id}) : null;
        setSelected(item); setScope(value.request.scope); setApplication(value.request.application);
        setContent(value.content); setDraft(value); setMessage('请核对资料与适用版本，再确认保存。'); setEditing(true);
      });
    }}/>
    <p className="quiet">按应用维护可跨任务复用的界面布局、功能与机制。</p>
    <div className="library-toolbar">
      <Input aria-label="搜索底层知识" placeholder="搜索应用名称、包名或平台" value={filter}
        onChange={(_, data) => setFilter(data.value)}/>
      <Button appearance="primary" disabled={disabled} onClick={() => load('')}>新建底层知识</Button>
    </div>
    {visible.length ? <div className="experience-grid">{visible.map(item =>
      <article className="surface experience-card" key={item.id}>
        <div className="row-between"><Icon name="library" size={25}/>
          <span className="tag">{item.scope === 'android' ? 'Android' : 'Windows'} · v{item.revision}</span></div>
        <h2>{item.application}</h2>
        <Button disabled={!!pending} aria-label={`打开 ${item.application}`} onClick={() => load(item.id)}>查看与编辑</Button>
      </article>)}</div> : <div className="surface">
      <Empty icon="library" title={profiles.length ? '没有匹配的底层知识' : '还没有底层知识'}
        text={profiles.length ? '可以按应用名称、包名或平台搜索。' : '新建应用知识，或从运行记录提炼一份知识草稿。'}/>
    </div>}
  </section>;
  return <section className="surface detail-workspace" aria-label="底层知识详情">
    <div className="row-between"><div><span className="section-label">应用底层知识</span>
      <h2>{selected ? `${selected.application} · v${selected.revision}` : '新建底层知识'}</h2></div>
      <Button disabled={!!pending} onClick={() => void back()}>返回列表</Button>
    </div>
    <div className="composer-fields">
      <Field label="知识平台"><Choice value={scope} disabled={disabled || !!selected} onChange={e => setScope(e.target.value as KnowledgeProfile['scope'])}>
        <option value="android">Android</option><option value="desktop">Windows</option>
      </Choice></Field>
      <Field label={scope === 'android' ? '应用包名' : 'Windows 应用名称'}>
        <Input value={application} maxLength={120} disabled={disabled || !!selected} onChange={(_, data) => setApplication(data.value)}/>
      </Field>
    </div>
    <Field label="底层知识正文"><Textarea rows={12} resize="vertical" value={content}
      disabled={disabled} onChange={(_, data) => {setContent(data.value); setMessage('');}}/></Field>
    {draft && <details open><summary>资料来源与待验证问题</summary>
      {draft.sources.map((source, i) => <p key={i}>[{i + 1}] {source.title} · {source.version}<br/>
        {source.supports}<br/>{source.url}</p>)}
      {draft.open_questions.map((question, i) => <p key={i}>待验证 · {question}</p>)}
      {selected && <details><summary>对照当前已保存版本</summary><pre>{selected.content}</pre></details>}
    </details>}
    <div className="button-row"><Button disabled={disabled || !application.trim() || !content.trim() || content === selected?.content}
      onClick={save}>{draft ? '确认保存基础知识' : '保存底层知识'}</Button>{message && <p role="status">{message}</p>}</div>
  </section>;
}
