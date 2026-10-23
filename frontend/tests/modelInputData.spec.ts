import {test, expect} from '@playwright/test';
import {modelInput} from '../src/modelInputData';

test('uses recorded messages verbatim, separates history/system, and omits request settings', () => {
  const text = '当前真实输入\n<script>只是文字</script>\n' + '原文'.repeat(50000) + '\n末尾';
  const view = modelInput({input: {messages: [
    {role: 'system', content: '系统原文'}, {role: 'user', content: '历史任务'},
    {role: 'assistant', content: '历史回答'}, {role: 'user', content: [{type: 'text', text}]},
  ], configuration: {temperature: .1}, task: '不应该取这份替代任务'}, prompt: '不应重复添加此摘要'});
  expect(view.current).toEqual([{index: 3, role: 'user', parts: [{kind: 'text', text}]}]);
  expect(view.copyText).toBe(text);
  expect(view.history.map(m => m.role)).toEqual(['user', 'assistant']);
  expect(view.system[0].parts).toEqual([{kind: 'text', text: '系统原文'}]);
  expect(JSON.stringify(view)).not.toContain('temperature');
});

test('first local round prefers assembled messages over old image-only incremental records', () => {
  const view = modelInput({input: {client_submission: {conversation_start: true},
    new_messages: [{role: 'user', content: [{type: 'image'}]}], messages: [
      {role: 'system', content: '系统'}, {role: 'user', content: [{type: 'text', text: '完整任务与经验'}, {type: 'image'}]},
    ]}});
  expect(view.copyText).toBe('完整任务与经验');
  expect(view.imageCount).toBe(1);
});

test('maps local context images in message order, distinguishing experience/current/history', () => {
  const view = modelInput({output_directory: 'C:\\runs\\round-1', input: {
    messages: [{role: 'user', content: [{type: 'image'}, {type: 'image'}]},
      {role: 'assistant', content: 'old'}, {role: 'user', content: [{type: 'image'}]}],
    image_sources: [
      {filename: 'evidence.png', kind: 'experience', evidence_id: '0003'},
      {filename: 'old.png', kind: 'history', step_index: 0},
      {filename: 'current.png', kind: 'current', step_index: 1},
    ]}});
  expect(view.imageCount).toBe(3);
  expect(view.current[0].parts).toEqual([{kind: 'image', label: '图片 3 · 当前截图', path: 'C:\\runs\\round-1/current.png'}]);
  expect(view.history[0].parts[0]).toMatchObject({label: '图片 1 · 示范截图 · 0003'});
  expect(view.history[0].parts[1]).toMatchObject({label: '图片 2 · 历史观察 · 第 1 轮'});
});

test('does not guess missing images or auto-load remote/data URLs and rejects archive traversal', () => {
  const view = modelInput({screenshot: 'not-proof.png', output_directory: '/run/0', input: {
    messages: [{role: 'user', content: [{type: 'image_url', image_url: {url: 'https://invalid.example/track.png'}},
      {type: 'image_url', image_url: {url: 'data:image/png;base64,abc'}}]}],
    image_sources: [{filename: '../other.png'}, {filename: 'https://invalid.example/track.png'}],
  }});
  expect(view.current[0].parts.every(p => p.kind === 'image' && p.path === undefined)).toBe(true);
  expect(JSON.stringify(view)).not.toContain('base64');
  expect(JSON.stringify(view)).not.toContain('not-proof');
});

test('Codex shows only submitted text/images and discloses unavailable server context', () => {
  const view = modelInput({system_managed_externally: true, input: {messages: [{role: 'user', content: [
    {type: 'text', text: '本轮文本'}, {type: 'localImage', path: 'C:/run/1.png'},
  ]}]}});
  expect(view.copyText).toBe('本轮文本');
  expect(view.current[0].parts[1]).toMatchObject({path: 'C:/run/1.png'});
  expect(view.notices.join('')).toContain('不代表完整上下文');
  expect(view.history).toHaveLength(0);
});

test('prepared, missing, incremental and old prompt-only records are labeled honestly', () => {
  expect(modelInput({input: {input_stage: 'prepared_not_yet_inferred', messages: [{role: 'user', content: 'test'}]}}).notices.join('')).toContain('不能视为已发送');
  expect(modelInput({error: 'network failed'}).notices.join('')).toContain('尚未记录');
  expect(modelInput({prompt: '原始 prompt'}).notices.join('')).toContain('没有完整消息');
  expect(modelInput({input: {task: '客户端任务'}}).notices.join('')).toContain('不是最终模型消息');
  expect(modelInput({input: {new_messages: [{role: 'user', content: '增量'}]}}).notices.join('')).toContain('只有本轮增量');
  // An explicit empty messages array is authoritative; do not invent content from prompt/settings.
  expect(modelInput({input: {messages: []}, prompt: '替代文字'}).current).toHaveLength(0);
});

test('keeps structured message blocks and tool replies without losing role/order', () => {
  const view = modelInput({input_messages: [
    {role: 'developer', content: '开发者指令'},
    {role: 'assistant', content: null, tool_calls: [{id: 'call-1', function: {name: 'inspect'}}]},
    {role: 'tool', content: '工具返回'}, {role: 'user', content: [{type: 'input_text', text: '继续'}]},
  ]});
  expect(view.system[0].role).toBe('developer');
  expect(view.history[0].parts[0]).toMatchObject({kind: 'other'});
  expect(view.current.map(m => m.role)).toEqual(['tool', 'user']);
  expect(view.copyText).toBe('工具返回\n\n继续');
});

test('old archived placeholders are not claimed as full original text', () => {
  const view = modelInput({input: {messages: [{role: 'user', content: '[image or oversized payload archived in events.jsonl]'}]}});
  expect(view.notices.join('')).toContain('占位符');
});
