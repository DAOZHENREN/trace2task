// node tests/web_context_audit.cjs -- exercises the actual UI audit projections.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8')
  .replace(/\r\n/g, '\n');
const context = {};
vm.createContext(context);
for (const name of ['modelRoundContextSummary', 'modelRoundImages', 'incrementalModelMessages']) {
  const start = source.indexOf(`function ${name}(`);
  vm.runInContext(source.slice(start, source.indexOf('\n}\n', start) + 3), context);
}
const round = {
  output_directory: 'D:/runs/example/model-io/0002',
  tokens: {output_tokens: 81},
  input: {
    input_stage: 'generation_completed',
    client_submission: {new_image_count: 1, experience_sent: false, prompt_profile_sent: false},
    image_sources: [
      {filename: 'context-image-00-000.png', kind: 'history', step_index: 1},
      {filename: 'context-image-00-001.png', kind: 'current', step_index: 2},
    ],
    context_management: {
      input_tokens_before_cleanup: 8500, input_tokens_after_cleanup: 7400,
      removed_images: 1, removed_tokens: 1100, removed_text_messages: 0,
      history_images_after: 1,
      evictions: [{step_index: 0, input_tokens_before: 8500, input_tokens_after: 7400,
                  removed_tokens: 1100, reason: 'vram_pressure'}],
    },
  },
};
const summary = context.modelRoundContextSummary(round);
for (const expected of ['未重发系统提示词', '未重发经验', '7400 tokens', '81 tokens',
                        '减少 1100 tokens', '删除文本消息 0 条', '第 1 轮', '显存余量不足']) {
  assert.ok(summary.includes(expected), expected);
}
const images = context.modelRoundImages(round);
assert.equal(images.images.length, 2);
assert.equal(images.images[0].label, '历史截图 · 第 2 轮');
assert.equal(images.images[1].label, '当前截图 · 本轮唯一新增图片');
assert.ok(images.summary.includes('未额外发送操作前对照图'));
assert.equal(images.images[0].path, 'D:/runs/example/model-io/0002/context-image-00-000.png');
round.input.input_stage = 'prepared_not_yet_inferred';
assert.ok(context.modelRoundContextSummary(round).includes('不能视为已发送给模型'));
assert.ok(context.modelRoundContextSummary({input: {dropped_turns: 2}}).includes('未记录丢弃 token 数'));
const incremental = {input: {client_submission: {conversation_start: false},
  messages: [{role: 'system', content: 'pinned'}, {role: 'user', content: 'old'},
    {role: 'assistant', content: 'old reply'}, {role: 'user', content: 'new'}],
  new_messages: [{role: 'user', content: 'new'}]}};
assert.equal(JSON.stringify(context.incrementalModelMessages(incremental, 1)),
  JSON.stringify([{role: 'user', content: 'new'}]));
assert.equal(context.incrementalModelMessages(incremental, 0).length, 4);
const first = {input: {client_submission: {conversation_start: true},
  messages: [{role: 'system', content: 'fixed'}, {role: 'user', content: [
    {type: 'text', text: 'Original task and full experience'}, {type: 'image'}]}],
  new_messages: [{role: 'user', content: [{type: 'image'}]}]}};
assert.equal(JSON.stringify(context.incrementalModelMessages(first, 0)), JSON.stringify(first.input.messages));
assert.equal(context.incrementalModelMessages(first, 0)[1].content[0].text, 'Original task and full experience');
assert.equal(context.incrementalModelMessages({input: {messages: incremental.input.messages}}, 1).length, 4);
round.input.context_management.compaction = {status: 'completed'};
round.input.context_management.removed_text_messages = 12;
assert.ok(context.modelRoundContextSummary(round).includes('用摘要替换文本消息 12 条'));
console.log('PASS: actual input, incremental chat, compaction and image-token audit.');
