import {useEffect, useRef, useState} from 'react';
import {Tab, TabList} from '@fluentui/react-components';
import type {AppState, ModelChoice} from './types';
import {Choice, Field} from './ui';
import {chooseCodexModel, codexEfforts, CodexModelOptions} from './codexModels';

export function useAction(onError: (message: string) => void) {
  const [pending, setPending] = useState('');
  const working = useRef(false);
  async function perform(name: string, action: () => Promise<void>) {
    if (working.current) return;
    working.current = true; setPending(name);
    try {await action();} catch (e) {onError(String(e));}
    finally {working.current = false; setPending('');}
  }
  return {pending, perform};
}
export function Sections({value, onChange, items, label}: {value: string; onChange: (value: string) => void;
  items: {id: string; label: string}[]; label: string}) {
  return <TabList className="section-tabs" selectedValue={value} onTabSelect={(_, d) => onChange(String(d.value))} aria-label={label}>
    {items.map(item => <Tab key={item.id} value={item.id}>{item.label}</Tab>)}</TabList>;
}
export function CompilerChoice({state, value, onChange, disabled}: {state: AppState; value: ModelChoice;
  onChange: (choice: ModelChoice) => void; disabled: boolean}) {
  const options = state.agent_options;
  useEffect(() => {
    if (!value.model && options.defaults.model) onChange(options.compiler_defaults || options.defaults);
  }, [value.model, options.defaults.model]);
  return <><div className="two-fields"><Field label="编译 / 修订模型"><Choice value={value.model} disabled={disabled}
    onChange={e => onChange(chooseCodexModel(options, e.target.value, value.reasoning_effort))}><CodexModelOptions options={options} value={value.model}/></Choice></Field>
    <Field label="编译 / 修订思考强度"><Choice value={value.reasoning_effort} disabled={disabled}
      onChange={e => onChange({...value, reasoning_effort: e.target.value})}>
      {!codexEfforts(options, value.model).includes(value.reasoning_effort) && <option value={value.reasoning_effort} disabled>请重新选择</option>}
      {codexEfforts(options, value.model).map(e => <option key={e}>{e}</option>)}</Choice></Field></div>
    {options.catalog_error && <p role="status">{options.catalog_error}</p>}</>;
}
export function JsonDetails({label, value, open = false}: {label: string; value: unknown; open?: boolean}) {
  return <details className="data-disclosure" open={open || undefined}><summary>{label}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
export function EvidenceImage({path, label}: {path?: string; label: string}) {
  if (!path) return null;
  const url = `/api/local-image?path=${encodeURIComponent(path)}`;
  return <figure className="evidence-image"><a href={url} target="_blank" rel="noreferrer"><img loading="lazy" src={url} alt={label}/></a><figcaption>{label}</figcaption></figure>;
}
