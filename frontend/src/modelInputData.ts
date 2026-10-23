// Read the recorded request, never reconstruct it from the current task/settings.
type RecordValue = Record<string, unknown>;
export type RawRound = {raw_output?: string; prediction?: unknown; execution?: unknown; verification?: unknown;
  input?: unknown; prompt?: unknown; input_messages?: unknown; error?: string; provider?: string;
  system_managed_externally?: boolean; output_directory?: string; [key: string]: unknown};
export type InputPart = {kind: 'text'; text: string} | {kind: 'image'; label: string; path?: string}
  | {kind: 'other'; text: string};
export type InputMessage = {index: number; role: string; parts: InputPart[]};

const record = (value: unknown): RecordValue => value !== null && typeof value === 'object' && !Array.isArray(value)
  ? value as RecordValue : {};
const imageTypes = new Set(['image', 'image_url', 'localImage', 'input_image']);
const textTypes = new Set(['text', 'input_text', 'output_text']);

function archiveImage(directory: unknown, filename: unknown): string | undefined {
  // Service images are flat archive members, not arbitrary URLs or relative traversal.
  if (typeof directory !== 'string' || typeof filename !== 'string' || !/^[\w.-]+\.(png|jpe?g|webp)$/i.test(filename)) return;
  return `${directory.replace(/[\\/]$/, '')}/${filename}`;
}

export function modelInput(raw: RawRound, provider?: string) {
  const input = record(raw.input);
  let source = 'messages';
  let messages: unknown[];
  if (Array.isArray(input.messages)) messages = input.messages;
  else if (Array.isArray(raw.input_messages)) messages = raw.input_messages;
  else if (Array.isArray(input.new_messages)) {messages = input.new_messages; source = 'incremental';}
  else if (typeof raw.prompt === 'string' || typeof input.prompt === 'string') {
    messages = [{role: 'user', content: raw.prompt ?? input.prompt}]; source = 'prompt';
  } else if (typeof input.task === 'string') {
    messages = [{role: 'user', content: input.task}]; source = 'task';
  } else messages = [];

  const sources = Array.isArray(input.image_sources) ? input.image_sources.map(record) : [];
  const order = Array.isArray(input.image_order) ? input.image_order : [];
  let imageIndex = 0;
  const projected: InputMessage[] = messages.map((item, index) => {
    const message = record(item);
    const content = message.content;
    const blocks = Array.isArray(content) ? content : [content];
    const parts: InputPart[] = blocks.flatMap((block): InputPart[] => {
      if (typeof block === 'string') return [{kind: 'text', text: block}];
      if (block == null) return [];
      const part = record(block);
      if (imageTypes.has(String(part.type))) {
        const number = imageIndex++;
        const image = sources[number] || {};
        const localPath = part.local_path ?? (part.type === 'localImage' ? part.path : undefined);
        const path = typeof localPath === 'string' ? localPath
          : archiveImage(raw.output_directory, image.filename ?? order[number]);
        const label = image.kind === 'current' ? '当前截图'
          : image.kind === 'experience' ? `示范截图${image.evidence_id == null ? '' : ` · ${image.evidence_id}`}`
          : image.kind === 'history' && typeof image.step_index === 'number' ? `历史观察 · 第 ${image.step_index + 1} 轮`
          : '图片附件';
        return [{kind: 'image', label: `图片 ${number + 1} · ${label}`, path}];
      }
      if (textTypes.has(String(part.type)) && typeof part.text === 'string') return [{kind: 'text', text: part.text}];
      // Tool calls/other multimodal content stay available without turning the request envelope into a prompt.
      return [{kind: 'other', text: JSON.stringify(block, null, 2)}];
    });
    for (const key of ['tool_calls', 'function_call']) {
      if (message[key] != null) parts.push({kind: 'other', text: JSON.stringify({[key]: message[key]}, null, 2)});
    }
    return {index, role: typeof message.role === 'string' ? message.role : 'unknown', parts};
  });

  const system = projected.filter(m => m.role === 'system' || m.role === 'developer');
  const conversation = projected.filter(m => m.role !== 'system' && m.role !== 'developer');
  // Prefer the trailing input after the last assistant reply. Full history remains inspectable.
  // In particular, do not trust old image-only new_messages on the first round.
  const lastAssistant = conversation.map(m => m.role).lastIndexOf('assistant');
  const boundary = lastAssistant >= 0 && lastAssistant < conversation.length - 1 ? lastAssistant + 1 : 0;
  const current = conversation.slice(boundary);
  const history = conversation.slice(0, boundary);
  const notices: string[] = [];
  if (input.input_stage === 'prepared_not_yet_inferred' || input.submission_kind === 'prepared_client_call_not_wire') {
    notices.push('这是准备输入；记录未确认进入模型推理，不能视为已发送。');
  }
  if (raw.system_managed_externally || raw.provider === 'codex' || provider === 'codex') {
    notices.push('Codex：这里只展示本程序提交的本轮内容；服务端保留的历史与内置系统指令未由接口返回，不代表完整上下文。');
  } else if (source === 'incremental') {
    notices.push('这份记录只有本轮增量消息，没有完整历史上下文。');
  } else if (source === 'prompt') {
    notices.push('这份记录只有提示词原文，没有完整消息或图片附件清单。');
  } else if (source === 'task') {
    notices.push('这份记录只有客户端任务文本，不是最终模型消息；不能据此还原完整输入。');
  }
  if (!projected.length) notices.push('这轮尚未记录可读的模型输入。可能仍在等待、调用提前失败，或旧记录缺少该内容；不会用当前设置补写。');
  if (JSON.stringify(messages).includes('[image or oversized payload archived in events.jsonl]')) {
    notices.push('旧记录中有内容被归档占位符替代；这里不会将占位符冒充完整原文，请查看对应 I/O 归档。');
  }
  return {current, history, system, notices, imageCount: imageIndex,
    copyText: current.flatMap(m => m.parts.filter(p => p.kind === 'text').map(p => p.text)).join('\n\n')};
}
