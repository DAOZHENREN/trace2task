import type {Trace} from './types';

export function versionTime(version: Pick<Trace, 'created_at' | 'created_at_source'>) {
  if (!version.created_at || Number.isNaN(new Date(version.created_at).getTime())) return '生成时间未记录';
  const label = version.created_at_source === 'file_mtime' ? '生成时间（旧版文件时间）' : '生成时间';
  const value = new Date(version.created_at).toLocaleString('zh-CN', {year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3, hourCycle: 'h23'});
  return `${label}：${value}`;
}

export function versionLabel(trace: Trace) {
  return `${trace.trace_name} · ${trace.representation} · ${versionTime(trace)} · ${trace.id.slice(0, 8)}`;
}

export const usableTrace = (trace: Trace) => ['A', 'D'].includes(trace.representation);
export const traceAssetUrl = (id: string, file: string) => `/api/traces/asset?id=${encodeURIComponent(id)}&file=${encodeURIComponent(file)}`;
