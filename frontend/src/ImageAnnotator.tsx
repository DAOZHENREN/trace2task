import {useEffect, useRef, useState, type PointerEvent} from 'react';
import {Button} from '@fluentui/react-components';
import {api} from './api';
import type {AnnotationStroke, HandoffPicture, VisualInput, VisualSource} from './types';

type Source = {url: string; width: number; height: number; input: VisualSource; label: string};
const maxBytes = 4 * 1024 * 1024;
const tools = {circle: '画圈', cross: '打叉', pen: '画笔'} as const;

/** The overlay shares the image's exact bounds; coordinates stay normalized after resizing. */
export function ImageAnnotator({picture, takeoverId, runId, disabled, onChange, onRemove}: {
  picture: HandoffPicture | null; takeoverId: string; runId: string; disabled: boolean;
  onChange: (value: VisualInput | null) => void; onRemove: () => void;
}) {
  const captured = (): Source | null => picture ? {url: `/api/local-image?path=${encodeURIComponent(picture.path)}`,
    width: picture.width || 1, height: picture.height || 1,
    input: {takeover_id: takeoverId, source_sha256: picture.sha256}, label: '接管时截图'} : null;
  const [source, setSource] = useState<Source | null>(captured);
  const [extras, setExtras] = useState<Source[]>([]);
  const [adding, setAdding] = useState(false);
  const [strokes, setStrokes] = useState<AnnotationStroke[]>([]);
  const [draft, setDraft] = useState<AnnotationStroke | null>(null);
  const [tool, setTool] = useState<AnnotationStroke['tool']>('circle');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);
  const active = useRef<AnnotationStroke | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const loadVersion = useRef(0);
  useEffect(() => () => {loadVersion.current++;}, []);
  useEffect(() => {onChange(source && ready && !loading ? {...source.input, strokes,
    ...(extras.length ? {extras: extras.map(extra => ({...extra.input, strokes: [] as []}))} : {})} : null);},
    [source, strokes, extras, loading, ready, onChange]);
  function replace(next: Source) {
    setReady(next.url === source?.url && ready); setSource(next); setStrokes([]); setDraft(null); active.current = null; setError('');
  }
  function accept(next: Source) {
    if (adding && source) {
      if (extras.length >= 7) {setError('一次最多添加 8 张截图。'); return;}
      setExtras(old => [...old, next]); setError('');
    } else replace(next);
    setAdding(false);
  }
  async function capture() {
    if (disabled || loading) return;
    const version = ++loadVersion.current;
    setLoading(true); setError('');
    try {
      const result = await api<{capture_id: string; picture: HandoffPicture}>(`/api/jobs/${runId}/interventions`,
        {kind: 'capture', takeover_id: takeoverId});
      const p = result.picture;
      const url = `/api/local-image?path=${encodeURIComponent(p.path)}`;
      const image = new Image(); image.src = url; await image.decode();
      if (version !== loadVersion.current) return;
      accept({url, width: p.width, height: p.height, label: '实时截图',
        input: {takeover_id: takeoverId, capture_id: result.capture_id, source_sha256: p.sha256}});
    } catch (e) {if (version === loadVersion.current) setError(`实时截图失败，原截图已保留：${String(e)}`);}
    finally {if (version === loadVersion.current) setLoading(false);}
  }
  async function upload(file: File) {
    if (disabled || loading) return;
    if (file.size > maxBytes || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      setError('请选择不超过 4 MB 的 PNG、JPEG 或 WebP 截图。'); return;
    }
    const version = ++loadVersion.current;
    setLoading(true); setError('');
    try {
      const url = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error('读取图片失败'));
        reader.readAsDataURL(file);
      });
      const image = new Image(); image.src = url; await image.decode();
      if (Math.min(image.width, image.height) < 16 || Math.max(image.width, image.height) > 4096 ||
          image.width * image.height > 12_000_000) throw new Error('图片边长需在 16..4096 像素内，最多 1200 万像素');
      if (version !== loadVersion.current) return;
      accept({url, width: image.width, height: image.height, input: {upload_data_url: url}, label: '上传的参考截图'});
    } catch (e) {if (version === loadVersion.current) setError(`无法读取截图：${String(e)}`);}
    finally {if (version === loadVersion.current) setLoading(false);}
  }
  function point(event: PointerEvent<SVGSVGElement>): [number, number] {
    const rect = event.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))];
  }
  function begin(event: PointerEvent<SVGSVGElement>) {
    if (disabled || loading || !ready || event.button !== 0 || !event.isPrimary || active.current) return;
    if (strokes.length >= 64 || strokes.reduce((n, s) => n + s.points.length, 0) >= 1998) {
      setError('标注已达上限，请撤销或清空部分标注。'); return;
    }
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
    const p = point(event); active.current = {tool, points: [p, p]}; setDraft(active.current);
  }
  function move(event: PointerEvent<SVGSVGElement>) {
    const current = active.current;
    if (!current) return;
    const p = point(event);
    const limit = Math.min(512, 2000 - strokes.reduce((n, s) => n + s.points.length, 0));
    const points = current.tool === 'pen' ? [...current.points.slice(0, limit - 1), p] : [current.points[0], p];
    active.current = {...current, points}; setDraft(active.current);
  }
  function finish(event: PointerEvent<SVGSVGElement>) {
    if (!active.current || !source) return;
    move(event);
    let stroke = active.current;
    const [a, b] = [stroke.points[0], stroke.points[stroke.points.length - 1]];
    if (stroke.tool !== 'pen' && Math.hypot(a[0] - b[0], a[1] - b[1]) < .005) {
      const radius = Math.min(source.width, source.height) * .04;
      const dx = radius / source.width, dy = radius / source.height;
      stroke = {...stroke, points: [[Math.max(0, a[0] - dx), Math.max(0, a[1] - dy)],
        [Math.min(1, a[0] + dx), Math.min(1, a[1] + dy)]]};
    }
    setStrokes(old => [...old, stroke]); setDraft(null); active.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function mark(stroke: AnnotationStroke, index: number) {
    if (!source) return null;
    const points = stroke.points.map(([x, y]) => [x * source.width, y * source.height]);
    const [a, b] = [points[0], points[points.length - 1]];
    const left = Math.min(a[0], b[0]), top = Math.min(a[1], b[1]);
    const width = Math.abs(a[0] - b[0]), height = Math.abs(a[1] - b[1]);
    const weight = Math.max(2, Math.round(Math.min(source.width, source.height) * .006));
    return <g key={index} data-annotation={stroke.tool} fill="none" strokeLinecap="round" strokeLinejoin="round">
      {['white', '#e5243b'].map((color, i) => <g key={color} stroke={color} strokeWidth={weight + (i ? 0 : 2)}>
        {stroke.tool === 'circle' ? <ellipse cx={left + width / 2} cy={top + height / 2} rx={width / 2} ry={height / 2}/> :
          stroke.tool === 'cross' ? <path d={`M${left},${top}l${width},${height} M${left},${top + height}l${width},${-height}`}/> :
            <polyline points={points.map(p => p.join(',')).join(' ')}/>}
      </g>)}
    </g>;
  }
  return <div className="image-annotator" role="group" aria-label="截图标注编辑器" tabIndex={0}
    onPaste={event => {
      const file = Array.from(event.clipboardData.items).find(item => item.type.startsWith('image/'))?.getAsFile();
      if (file) {event.preventDefault(); void upload(file);}
    }}>
    <div className="annotation-toolbar">
      <strong>截图与标注</strong>
      <Button size="small" disabled={disabled || loading} onClick={() => void capture()}>实时截图</Button>
      <Button size="small" disabled={disabled || loading} onClick={() => fileInput.current?.click()}>上传截图</Button>
      {picture && <Button size="small" disabled={disabled || loading} onClick={() => accept(captured()!)}>使用接管截图</Button>}
      <Button size="small" disabled={disabled || loading || !ready || extras.length >= 7}
        aria-pressed={adding} onClick={() => setAdding(value => !value)}>{adding ? '取消添加' : '添加截图'}</Button>
      <Button size="small" disabled={disabled} onClick={onRemove}>移除截图</Button>
      <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" hidden aria-label="选择截图文件"
        onChange={event => {const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = '';}}/>
    </div>
    <p className="quiet">{adding ? '请选择实时截图、使用接管截图，或上传 / 粘贴图片，作为附加截图。主图和标注会保留。' :
      '实时截图会立即重新截取手机画面。添加截图可附带多张图片（最多 8 张），仅主图支持标注。上传或粘贴会替换主图。'}</p>
    <div className="annotation-toolbar" role="toolbar" aria-label="标注工具">
      {Object.entries(tools).map(([key, label]) => <Button key={key} size="small" aria-pressed={tool === key}
        appearance={tool === key ? 'primary' : 'secondary'} disabled={disabled || !ready || loading}
        onClick={() => setTool(key as AnnotationStroke['tool'])}>{label}</Button>)}
      <Button size="small" disabled={disabled || !strokes.length} onClick={() => setStrokes(old => old.slice(0, -1))}>撤销</Button>
      <Button size="small" disabled={disabled || !strokes.length} onClick={() => setStrokes([])}>清空标注</Button>
      <span className="quiet">{strokes.length} / 64 个标注</span>
    </div>
    {source ? <>
      <div className="annotation-surface" style={{width: `min(100%, ${520 * source.width / source.height}px)`}}>
        <img key={source.url} src={source.url} alt="待标注截图" draggable={false}
          onLoad={event => {const image = event.currentTarget; setSource(old => old && {...old, width: image.naturalWidth, height: image.naturalHeight}); setReady(true);}}
          onError={() => {setReady(false); setError('截图无法显示，请上传一张参考截图或移除截图。');}}/>
        <svg viewBox={`0 0 ${source.width} ${source.height}`} role="img" aria-label="在截图上绘制标注"
          onPointerDown={begin} onPointerMove={move} onPointerUp={finish}
          onPointerCancel={() => {active.current = null; setDraft(null);}}>
          {strokes.map(mark)}{draft && mark(draft, -1)}
        </svg>
      </div>
      <p className="quiet">{source.label} · 参考画面不会随手机更新；继续时模型会重新观察实际界面。</p>
    </> : <p className="quiet">没有可用的接管截图，可以上传或粘贴参考截图。</p>}
    {!!extras.length && <div className="annotation-extras" aria-label="附加截图">
      {extras.map((extra, i) => <figure key={`${i}-${extra.url}`}>
        <a href={extra.url} target="_blank" rel="noreferrer"><img src={extra.url} alt={`附加截图 ${i + 1}`}/></a>
        <figcaption>附图 {i + 1} · {extra.label}</figcaption>
        <Button size="small" disabled={disabled || loading} onClick={() => setExtras(old => old.filter((_, n) => n !== i))}>移除附图 {i + 1}</Button>
      </figure>)}
    </div>}
    {loading && <p role="status">正在读取截图…</p>}
    {error && <p role="alert" className="inline-warning">{error}</p>}
  </div>;
}
