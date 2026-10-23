import type {Catalog, LocalService, ServiceSnapshot} from './types';
import {Icon} from './ui';

export const serviceLabels: Record<LocalService['status'], string> = {
  running: '运行中', busy: '正在处理', loading: '加载中', idle: '服务在线 · 未加载模型',
  reported: '服务报告已加载', unavailable: '未连接', unknown: '状态未知',
};
export const visibleService = (s: LocalService) => s.id !== 'trained_d' && s.model !== 'trained_d';
export const loadedService = (s: LocalService) => visibleService(s) && s.reachable && !!s.model && ['running', 'busy', 'reported'].includes(s.status);
export const modelLabel = (id: string | null | undefined, catalog: Catalog) =>
  catalog.models.find(p => p.id === id)?.label || id || '尚无模型';

export function LocalServices({snapshot, error, catalog}: {snapshot: ServiceSnapshot | null; error: string; catalog: Catalog}) {
  const services = snapshot?.services.filter(visibleService) || [];
  return <section className="surface local-services" aria-label="实际后台服务">
    <div className="service-heading"><span>本地服务</span><span className="service-updated">
      {error ? '检查失败' : snapshot ? `上次检查 ${new Date(snapshot.checked_at).toLocaleTimeString('zh-CN')}` : '正在检查…'} · 手动刷新</span></div>
    {error ? <p role="status" className="inline-warning">状态暂不可用，不能确认模型是否仍在运行。请点击右上角刷新。</p>
      : services.map(s => <div className={`local-service-row ${loadedService(s) ? 'running' : ''}`} key={s.id}>
        <div className="service-emblem"><Icon name="models" size={24}/></div>
        <div className="local-service-copy"><strong>{s.model ? modelLabel(s.model, catalog) : '本地 GUI 服务'}</strong>
          <p>{s.backend || '引擎待确认'}<span className="service-separator">/</span>本机运行</p></div>
        <span className="service-state"><span className={`status-dot ${loadedService(s) ? '' : 'muted'}`}/>{serviceLabels[s.status]}</span>
      </div>)}
    {!error && snapshot && !services.length && <p className="quiet">尚未检测到本地服务。</p>}
    <details className="service-details"><summary>连接详情与诊断</summary>
      {error ? <p>{error}</p> : services.map(s => <div key={s.id}><code>{s.endpoint}</code>{s.pid && <span> · PID {s.pid}</span>}
        {s.status === 'reported' && <p>旧版服务报告的模型；升级并重启服务后可核实工作进程状态。</p>}
        {!s.reachable && <p>未收到有效健康响应，不等于确认进程已退出。</p>}</div>)}
      <p>状态为上次检查的快照。切换所选模型不会切换后台服务，关闭工作台不会停止模型。</p>
    </details>
  </section>;
}
