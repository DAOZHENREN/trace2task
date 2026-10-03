import {createContext, useCallback, useContext, useEffect, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle} from '@fluentui/react-components';
import {Icon} from './ui';

type Confirmation = {title: string; body: ReactNode; confirmLabel?: string; cancelLabel?: string;
  intent?: 'normal' | 'danger'; note?: string};
type Ask = (options: Confirmation) => Promise<boolean>;
const ConfirmationContext = createContext<Ask | null>(null);

export function ConfirmProvider({children}: {children: ReactNode}) {
  const [options, setOptions] = useState<Confirmation | null>(null);
  const pending = useRef<((answer: boolean) => void) | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const confirm = useCallback<Ask>(value => {
    // A second click must not replace an unanswered request or start two writes.
    if (pending.current) return Promise.resolve(false);
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setOptions(value);
    return new Promise(resolve => {pending.current = resolve;});
  }, []);
  const settle = (answer: boolean) => {
    const resolve = pending.current; pending.current = null; setOptions(null);
    resolve?.(answer);
    requestAnimationFrame(() => {if (trigger.current?.isConnected) trigger.current.focus();});
  };
  useEffect(() => {if (options) cancel.current?.focus();}, [options]);
  useEffect(() => () => {pending.current?.(false); pending.current = null;}, []);
  return <ConfirmationContext.Provider value={confirm}>{children}
    <Dialog open={!!options} onOpenChange={(_, data) => {if (!data.open) settle(false);}}>
      <DialogSurface className={`confirmation-surface ${options?.intent === 'danger' ? 'confirmation-danger' : ''}`}>
        <DialogBody className="confirmation-body">
          <DialogTitle className="confirmation-title"><span className="confirmation-symbol"><Icon name={options?.intent === 'danger' ? 'stop' : 'library'} size={23}/></span>
            <span><span className="confirmation-eyebrow">TRACE2TASK · 操作确认</span>{options?.title}</span></DialogTitle>
          <DialogContent className="confirmation-content"><div className="confirmation-copy">{options?.body}</div>
            {options?.note && <div className="confirmation-note"><Icon name="shield" size={17}/><span>{options.note}</span></div>}
          </DialogContent>
          <DialogActions className="confirmation-actions"><Button ref={cancel} onClick={() => settle(false)}>{options?.cancelLabel || '取消'}</Button>
            <Button appearance="primary" className={options?.intent === 'danger' ? 'danger-action' : undefined} onClick={() => settle(true)}>{options?.confirmLabel || '确认继续'}</Button></DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  </ConfirmationContext.Provider>;
}

export function useConfirm() {
  const confirm = useContext(ConfirmationContext);
  if (!confirm) throw new Error('Confirmation provider is missing');
  return confirm;
}
