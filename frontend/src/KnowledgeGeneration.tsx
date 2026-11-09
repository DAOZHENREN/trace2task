import {useEffect, useState} from 'react';
import {Button, Checkbox, Input, Textarea} from '@fluentui/react-components';
import {api} from './api';
import {Choice, Field} from './ui';
import type {AppState, KnowledgeProfile, ModelChoice} from './types';
import {CompilerChoice} from './workflow';
import {codexChoiceError} from './codexModels';
import {KnowledgeApplicationPicker} from './KnowledgeApplicationPicker';

export type KnowledgeDraft = {
  draft_id: string; content: string; created_at: string;
  request: {scope: KnowledgeProfile['scope']; application: string};
  sources: {title: string; url: string; supports: string; version: string}[];
  open_questions: string[];
};
const focusOptions = [
  {label: '通用基础（推荐）', value: '通用基础：界面布局、常用功能、核心机制、状态判断和限制'},
  {label: '界面与常用操作', value: '界面与操作：UI区域、控件含义、常用功能、导航方式和状态判断'},
  {label: '游戏与战斗机制', value: '游戏机制：战斗规则、资源、角色行动、自动战斗条件和限制'},
  {label: '办公与文件处理', value: '办公操作：文档编辑、文件管理、保存、导入导出和格式限制'},
  {label: '自定义范围', value: 'custom'},
];
type Job = {job_id: string; status: string; error?: string; kind: string};

export function KnowledgeGeneration({state, busy, profiles, onDraft, onError}: {
  state: AppState; busy: boolean; profiles: KnowledgeProfile[]; onDraft: (draft: KnowledgeDraft) => void; onError: (message: string) => void;
}) {
  const [model, setModel] = useState<ModelChoice>(state.agent_options.compiler_defaults || state.agent_options.defaults);
  const choiceError = codexChoiceError(state.agent_options, model.model, model.reasoning_effort);
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<KnowledgeProfile['scope']>('desktop');
  const [application, setApplication] = useState('');
  const [name, setName] = useState('');
  const [version, setVersion] = useState('');
  const [focus, setFocus] = useState<string[]>([focusOptions[0].value]);
  const [customFocus, setCustomFocus] = useState('');
  const [references, setReferences] = useState('');
  const [drafts, setDrafts] = useState<KnowledgeDraft[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [pending, setPending] = useState(false);
  const running = !!job && ['queued', 'running', 'stopping'].includes(job.status);
  useEffect(() => {
    let disposed = false;
    void Promise.all([api<KnowledgeDraft[]>('/api/application-knowledge/drafts', {}),
      api<{active_job?: Job}>('/api/state?compact=1')]).then(([items, state]) => {
        if (disposed) return;
        setDrafts(items);
        if (state.active_job?.kind === 'knowledge_generation') setJob(state.active_job);
      }).catch(e => {if (!disposed) onError(String(e));});
    return () => {disposed = true;};
  }, []);
  useEffect(() => {
    if (!running || !job) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<Job>('/api/jobs/' + job!.job_id);
        if (disposed) return;
        setJob(next);
        if (['completed', 'failed', 'stopped'].includes(next.status)) {
          setDrafts(await api<KnowledgeDraft[]>('/api/application-knowledge/drafts', {}));
          return;
        }
      } catch (e) {if (!disposed) onError(String(e));}
      if (!disposed) timer = setTimeout(() => void poll(), 2000);
    }
    timer = setTimeout(() => void poll(), 1000);
    return () => {disposed = true; clearTimeout(timer);};
  }, [job?.job_id, running]);
  async function start() {
    setPending(true);
    try {
      setJob(await api<Job>('/api/application-knowledge/generate',
        {scope, application, display_name: name, version, ...model,
          focus: focus.map(value => value === 'custom' ? customFocus.trim() : value).join('\n'), references}));
    } catch (e) {onError(String(e));} finally {setPending(false);}
  }
  return <div className="surface">
    <Button disabled={pending} onClick={() => {
      if (!open) {setApplication(''); setName(''); setVersion(''); setReferences('');}
      setOpen(!open);
    }}>基础知识生成</Button>
    {open && <>
      <p className="quiet">选择应用即可生成初版，核对并保存后供任务使用。</p>
      <Field label="平台"><Choice value={scope} disabled={running || pending} onChange={e => {
        setScope(e.target.value as typeof scope); setApplication(''); setName(''); setVersion(''); setReferences('');
      }}>
        <option value="desktop">Windows</option><option value="android">Android</option>
      </Choice></Field>
      <KnowledgeApplicationPicker key={scope} scope={scope} profiles={profiles} disabled={busy || running || pending}
        onChange={(id, label) => {setApplication(id); setName(label); setVersion(''); setReferences('');}}/>
      <fieldset disabled={running || pending}>
        <legend>知识范围（可多选）</legend>
        <p className="quiet">决定资料检索和正文的重点，所选范围会合并成一份知识。</p>
        <div className="button-row">{focusOptions.map(option =>
          <Checkbox key={option.value} label={option.label} checked={focus.includes(option.value)}
            onChange={(_, data) => setFocus(values => data.checked
              ? [...values, option.value] : values.filter(value => value !== option.value))}/>
        )}</div>
        {focus.includes('custom') && <Field label="希望了解什么"><Textarea value={customFocus} maxLength={3500}
          disabled={running || pending} onChange={(_, d) => setCustomFocus(d.value)}/></Field>}
      </fieldset>
      <details><summary>补充信息（可选）</summary>
        <Field label="检索名称补充"><Input value={name} maxLength={200}
          disabled={running || pending} onChange={(_, d) => setName(d.value)}/></Field>
        <Field label="指定版本"><Input value={version} placeholder="留空时由模型核实资料适用版本" maxLength={200}
          disabled={running || pending} onChange={(_, d) => setVersion(d.value)}/></Field>
        <Field label="参考链接（每行一个）"><Textarea value={references} maxLength={4000}
          disabled={running || pending} onChange={(_, d) => setReferences(d.value)}/></Field>
      </details>
      <CompilerChoice state={state} value={model} onChange={setModel} disabled={busy || running || pending}
        modelLabel="生成模型" effortLabel="思考强度"/>
      {choiceError && <p role="status">{choiceError}</p>}
      <Button appearance="primary" disabled={!!choiceError || busy || running || pending || !application.trim() || (!focus.length || (focus.includes('custom') && !customFocus.trim()))}
        onClick={() => void start()}>生成草稿</Button>
    </>}
    {running && <p role="status">正在检索资料并生成基础知识草稿…
      <Button onClick={() => void api('/api/jobs/' + job!.job_id + '/stop', {}).catch(e => onError(String(e)))}>停止生成</Button></p>}
    {job?.status === 'failed' && <p role="alert">{job.error || '生成失败，请重试。'}</p>}
    {job?.status === 'stopped' && <p role="status">生成已停止。</p>}
    {drafts.length > 0 && <details open={job?.status === 'completed'}><summary>基础知识草稿（{drafts.length}）</summary>
      {drafts.map(draft => <div className="button-row" key={draft.draft_id}>
        <span>{draft.request.application} · {new Date(draft.created_at).toLocaleString()}</span>
        <Button disabled={busy || running || pending} onClick={() => onDraft(draft)}>查看草稿</Button>
      </div>)}</details>}
  </div>;
}
