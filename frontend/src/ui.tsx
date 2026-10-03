import {Button, Field as FluentField, Select} from '@fluentui/react-components';
import type {ComponentProps, ReactNode} from 'react';

export function Icon({name, size = 20}: {name: string; size?: number}) {
  const paths: Record<string, string> = {
    tasks: 'M4 5h7M4 10h5M4 15h5M15 6l6 5-6 5V6Z', record: 'M6 3h12v18H6V3ZM10 8h4M10 12h4M10 16h2',
    library: 'M3 5h6l3 3h9v12H3V5ZM3 11h18', models: 'M8 8h8v8H8V8ZM8 2v3m8-3v3M8 19v3m8-3v3M2 8h3m-3 8h3M19 8h3m-3 8h3',
    history: 'M4 11a8 8 0 1 1 2 6M4 5v6h6M12 7v5l3 2', settings: 'M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6',
    arrow: 'M5 12h14m-6-6 6 6-6 6', plus: 'M12 5v14M5 12h14', stop: 'M6 6h12v12H6V6Z',
    refresh: 'M20 11a8 8 0 0 0-14-5L3 9m0-6v6h6M4 13a8 8 0 0 0 14 5l3-3m0 6v-6h-6',
    experiments: 'M9 3h6M10 3v7L4 20h16l-6-10V3M8 14h8', check: 'm5 12 4 4L19 6',
    screen: 'M3 4h18v13H3V4ZM8 21h8m-4-4v4', shield: 'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Zm-4 9 3 3 5-6',
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.tasks}/></svg>;
}
export function Field({label, hint, children}: {label: string; hint?: string; children: ReactNode}) {
  return <FluentField className="field" label={label} hint={hint}>{children}</FluentField>;
}
export function Choice(props: ComponentProps<typeof Select>) { return <Select {...props}/>; }
export function PageHeading({eyebrow, title, copy, action}: {eyebrow: string; title: string; copy: string; action?: ReactNode}) {
  return <header className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{copy}</p></div>{action}</header>;
}
export function Empty({title, text, icon = 'tasks', action}: {title: string; text: string; icon?: string; action?: ReactNode}) {
  return <div className="empty"><div className="empty-icon"><Icon name={icon} size={26}/></div><h3>{title}</h3><p>{text}</p>{action}</div>;
}
export function Refresh({onClick, busy = false}: {onClick: () => void; busy?: boolean}) {
  return <Button appearance="subtle" icon={<Icon name="refresh" size={17}/>} disabled={busy} onClick={onClick}>刷新</Button>;
}
