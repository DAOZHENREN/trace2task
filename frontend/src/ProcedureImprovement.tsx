import {useState} from 'react';
import {Button} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import {versionTime} from './experience';
import {active} from './types';
import type {AppState, CompilationConfirmation, ModelChoice, Run} from './types';
import {CompilerChoice, useAction} from './workflow';

export function ProcedureImprovement({run, state, busy, onRun, onError}: {
  run: Run; state: AppState; busy: boolean; onRun: (run: Run) => void; onError: (text: string) => void;
}) {
  const confirm = useConfirm();
  const [model, setModel] = useState<ModelChoice>(state.agent_options.compiler_defaults || state.agent_options.defaults);
  const {pending, perform} = useAction(onError);
  const selected = run.memory_selection?.selected || run.result?.memory_selection?.selected;
  if (run.kind !== 'agent' || active(run) || !['B', 'R'].includes(selected?.representation || run.run_facts?.experience_representation || '')) return null;
  async function generate() {
    if (!await confirm({title: '从这次运行生成改进候选？',
      body: `源运行：${run.job_id}\n将它实际使用的父方法、执行反馈与最多 8 张归档观察交给 Codex（${model.model} / ${model.reasoning_effort}）。固定改进器最多调用一次，不执行桌面操作。`,
      confirmLabel: '生成改进候选', note: '生成独立 R 版本，需另行审查才可试用；不覆盖 B/C，不自动选用，不能据此认定效果提升。'})) return;
    await perform('evolve', async () => {
      type Reply = Run | CompilationConfirmation;
      const payload = {run_id: run.job_id, ...model};
      let reply = await api<Reply>('/api/traces/evolve', payload);
      if ('confirmation_required' in reply) {
        if (!await confirm({title: '再次生成独立候选？', body: <><p>{reply.message}</p>
          <ul>{reply.existing_versions.map(v => <li key={v.id}>{v.trace_name} · {versionTime(v)} · {v.id}</li>)}</ul></>,
          confirmLabel: '再次生成', note: '会再次调用模型；已有候选和审查决定均保留。'})) return;
        reply = await api<Reply>('/api/traces/evolve', {...payload, confirm_duplicate: true});
      }
      if ('confirmation_required' in reply) throw new Error('仍需重复确认，请刷新后重试。');
      onRun(reply);
    });
  }
  return <details className="data-disclosure"><summary>运行后改进 · 固定改进器</summary>
    <p>从这次运行的实际反馈提出下一版方法。完成声明不等于独立成功；当前阶段不修改改进器自身。</p>
    <CompilerChoice state={state} value={model} onChange={setModel} disabled={busy || !!pending}/>
    <Button appearance="primary" disabled={busy || !!pending} onClick={() => void generate()}>生成改进候选</Button>
  </details>;
}
