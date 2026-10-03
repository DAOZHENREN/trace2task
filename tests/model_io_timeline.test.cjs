const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8');

function fakeDocument() {
  return {
    createElement(tagName) {
      const node = {
        tagName,
        children: [],
        className: '',
        textContent: '',
        listeners: {},
        append(...children) { this.children.push(...children); },
        addEventListener(name, handler) { this.listeners[name] = handler; },
        get childElementCount() { return this.children.length; },
      };
      return node;
    },
  };
}

function allNodes(node) {
  return [node, ...node.children.flatMap(allNodes)];
}

test('first-round visible bubbles include task and experience even in old image-only incremental archives', () => {
  const context = vm.createContext({document: fakeDocument(), encodeURIComponent});
  vm.runInContext(source.slice(source.indexOf('function modelIoStatusLabel'),
    source.indexOf('function renderLibrary')), context);
  const task = '按会议通知整理交付文件夹。\n完整任务经验：保持原文。';
  const chat = context.makeModelChat([{status: 'predicted', input: {
    client_submission: {conversation_start: true},
    messages: [{role: 'system', content: 'system'}, {role: 'user', content: [
      {type: 'text', text: task}, {type: 'image'}]}],
    new_messages: [{role: 'user', content: [{type: 'image'}]}],
  }}]);
  // Ignore folded complete-input audit: the default visible bubble must have text.
  const visible = chat.children[0].children.filter(node => node.tagName !== 'details');
  assert.ok(visible.flatMap(allNodes).some(node => node.textContent.includes(task)));
});

test('image gallery distinguishes current, previous, missing paths and load errors', () => {
  const context = vm.createContext({document: fakeDocument(), encodeURIComponent});
  vm.runInContext(source.slice(source.indexOf('function modelIoStatusLabel'),
    source.indexOf('function renderLibrary')), context);
  const messages = [{role: 'user', content: [{type: 'image'}, {type: 'image'}]}];
  const chat = context.makeModelChat([{status: 'completed', screenshot: 'current.png',
    previous_screenshot: 'before.png', input: {messages}}]);
  const nodes = allNodes(chat);
  assert.equal(nodes.filter(node => node.tagName === 'img').length, 2);
  assert.ok(nodes.some(node => node.textContent.includes('本轮图片：2 张 · 可预览 2 张')));
  assert.ok(nodes.some(node => node.textContent === '对照截图 · 上一次动作前，仅供比较'));
  nodes.find(node => node.tagName === 'img').listeners.error();
  assert.ok(nodes.some(node => node.textContent.includes('图片加载失败')));
  assert.ok(context.modelRoundImages({screenshot: 'first.png'}).summary.includes('无动作前对照图'));
  assert.ok(context.modelRoundImages({screenshot: 'only.png', input: {messages}}).summary.includes('1 张缺少预览路径'));
  assert.ok(context.modelRoundImages({}).summary.includes('无法确认'));
});

test('model IO timeline renders timing, archives, cancellation, and errors without success claims', () => {
  const helpers = source.slice(
    source.indexOf('function makeMiniButton'),
    source.indexOf('function waaConditionLabel'),
  );
  const subject = source.slice(
    source.indexOf('function modelIoStatusLabel'),
    source.indexOf('function renderLibrary'),
  );
  const opened = [];
  const context = vm.createContext({
    document: fakeDocument(),
    encodeURIComponent,
    openLocal(pathToOpen) { opened.push(pathToOpen); },
  });
  vm.runInContext(`${helpers}\n${subject}`, context);
  const timeline = context.makeModelIoTimeline([
    {
      step_index: 0,
      request_id: 'round-1',
      status: 'predicted',
      input_path: 'runs/a/model-io/0000/input.json',
      response_path: 'runs/a/model-io/0000/output.json',
      output_directory: 'runs/a/model-io/0000',
      screenshot: 'runs/a/0000.png',
      input: {task: '计算 234 乘以 567', history: []},
      raw_output: '{"actions": []}',
      prediction: {actions: []},
      execution: {status: 'delivered', executed: true, effect: 'unverifiable'},
      protocol_normalizations: ['complete_json_missing_tool_call_close'],
      model_roundtrip_ms: 4410,
      timings: {load_ms: 0, preprocess_ms: 20, generate_ms: 4405, decode_ms: 0.1, total_ms: 4579},
      tokens: {input_tokens: 123, output_tokens: 4},
      memory: {
        before: {allocated_bytes: Math.round(3.99 * 1024 ** 3), reserved_bytes: Math.round(4 * 1024 ** 3)},
        peak: {peak_allocated_bytes: Math.round(4.41 * 1024 ** 3), peak_reserved_bytes: Math.round(10.61 * 1024 ** 3)},
        after_cleanup: {allocated_bytes: Math.round(3.99 * 1024 ** 3), reserved_bytes: Math.round(4 * 1024 ** 3)},
      },
    },
    {step_index: 1, status: 'error', error: 'network failed', model_roundtrip_ms: 5,
      execution: {status: 'rejected', executed: false, reason: 'no input target'}},
    {step_index: 2, status: 'cancelled', model_roundtrip_ms: 6,
      execution: {status: 'no_progress', executed: false}},
    {step_index: 3, status: 'cancel_pending', model_roundtrip_ms: 7,
      execution: {status: 'driver_refused', executed: false, receipt: {code: 'background_unavailable'}}},
    {step_index: 4, status: 'reviewed', purpose: 'verify_completion', model_roundtrip_ms: 8,
      verification: {verdict: 'incomplete', evidence: '输入框为空', missing: '未输入文字'}},
  ]);
  const labels = allNodes(timeline).map(node => node.textContent);
  assert.ok(labels.includes('查看模型输入与输出 · 5 回合'));
  assert.ok(labels.some(label => label.includes('模型回合 1 · 模型回合已归档 · 4.41 秒')));
  assert.ok(labels.some(label => label.includes('模型调用失败')));
  assert.ok(labels.some(label => label.includes('已取消；未执行未返回的计划')));
  assert.ok(labels.some(label => label.includes('正在等待模型调用取消；未执行未返回的计划')));
  assert.ok(labels.includes('输入 Token'));
  assert.ok(labels.includes('显存峰值 · 已分配'));
  assert.ok(labels.includes('3.99 GiB'));
  assert.ok(labels.includes('4.41 GiB'));
  assert.ok(labels.some(label => label.includes('分段耗时：载入 0 毫秒 · 预处理 20 毫秒 · 生成 4.41 秒 · 解码 0 毫秒 · 本轮总计 4.58 秒')));
  assert.ok(labels.includes('查看实际请求内容'));
  assert.ok(!labels.includes('未记录'));
  assert.ok(!labels.some(label => label.includes('任务已成功')));
  assert.ok(labels.includes('查看实际执行结果'));
  assert.ok(labels.includes('查看解码后的预测（不是执行结果）'));
  assert.ok(labels.some(label => label.includes('动作已送达，效果待确认')));
  assert.ok(labels.some(label => label.includes('动作未执行，预检拒绝')));
  assert.ok(labels.some(label => label.includes('驱动拒绝后台输入，未记为已送达')));
  assert.ok(labels.some(label => label.includes('格式兼容：生成正常结束、JSON 完整')));
  assert.ok(labels.some(label => label.includes('只读完成核验')));
  assert.ok(labels.some(label => label.includes('重复操作未执行')));
  assert.ok(labels.some(label => label.includes('不是独立应用验证；本轮未执行动作')));
  assert.ok(labels.includes('查看只读核验证据'));

  const openInput = allNodes(timeline).find(node => node.textContent === '打开输入归档');
  openInput.listeners.click();
  assert.deepEqual(opened, ['runs/a/model-io/0000/input.json']);
});

test('model chat shows the actual system and user input, raw reply, and executor receipt', () => {
  const helpers = source.slice(source.indexOf('function makeMiniButton'), source.indexOf('function waaConditionLabel'));
  const subject = source.slice(source.indexOf('function modelIoStatusLabel'), source.indexOf('function renderLibrary'));
  const context = vm.createContext({document: fakeDocument(), encodeURIComponent, openLocal() {}});
  vm.runInContext(`${helpers}\n${subject}`, context);
  const chat = context.makeModelChat([{
    step_index: 0, status: 'predicted', screenshot: 'runs/test/0000.png',
    input: {messages: [
      {role: 'system', content: 'Custom system rules'},
      {role: 'user', content: [{type: 'image'}, {type: 'text', text: 'Instruction: open Notepad'}]},
    ]},
    raw_output: '<tool_call>{"action":"left_click"}</tool_call>',
    execution: {status: 'delivered', effect: 'unverifiable', executed: true},
  }]);
  const text = allNodes(chat).map(node => node.textContent);
  assert.ok(text.includes('Custom system rules'));
  assert.ok(text.some(value => value.includes('Instruction: open Notepad')));
  assert.ok(text.some(value => value.includes('left_click')));
  assert.ok(text.some(value => value.includes('unverifiable')));
  assert.ok(text.some(value => value.includes('第 1 轮')));
});

test('model chat links native reply, normalized actions, exact executor requests and receipts in readable order', () => {
  const helpers = source.slice(source.indexOf('function makeMiniButton'), source.indexOf('function waaConditionLabel'));
  const subject = source.slice(source.indexOf('function modelIoStatusLabel'), source.indexOf('function renderLibrary'));
  const context = vm.createContext({document: fakeDocument(), encodeURIComponent, openLocal() {}});
  vm.runInContext(`${helpers}\n${subject}`, context);
  const chat = context.makeModelChat([{status: 'completed', raw_output: 'native click(500, 500)',
    execution: {status: 'delivered', normalized_plan: {actions: [
      {skill: 'click', args: {x: .5, y: .5, button: 'left'}},
      {skill: 'type_text', args: {text: 'hello'}},
    ]}, executor_requests: [{action_index: 1, status: 'delivered', request: {
      backend: 'cua', operation: 'click', delivery_mode: 'background', args: {x: 960, y: 540},
    }}], steps: [{receipt: {effect: 'unverifiable'}}],
      action: {skill: 'type_text'}, reason: '输入控件无法确认'},
  }]);
  const lines = allNodes(chat).map(node => node.textContent);
  assert.ok(lines.includes('native click(500, 500)'));
  assert.ok(lines.some(value => value.includes('动作 1 · 左键点击 画面位置 50%、50%')));
  assert.ok(lines.some(value => value.includes('Cua 后台 click · 像素坐标 960、540 · 已送达')));
  assert.ok(lines.some(value => value.includes('实际效果待看下一张截图')));
  assert.ok(lines.some(value => value.includes('动作 2 · 输入文字「hello」')));
  assert.ok(lines.some(value => value.includes('未发送到执行器：输入控件无法确认')));
  assert.ok(!lines.some(value => value.includes('"executor_requests"')));
});

test('structured-action model shows its native output instead of claiming no reply', () => {
  const helpers = source.slice(source.indexOf('function makeMiniButton'), source.indexOf('function waaConditionLabel'));
  const subject = source.slice(source.indexOf('function modelIoStatusLabel'), source.indexOf('function renderLibrary'));
  const context = vm.createContext({document: fakeDocument(), encodeURIComponent, openLocal() {}});
  vm.runInContext(`${helpers}\n${subject}`, context);
  const chat = context.makeModelChat([{status: 'predicted', raw_output_kind: 'native_structured_actions',
    raw_output: '[{"skill":"click"}]'}]);
  const labels = allNodes(chat).map(node => node.textContent);
  assert.ok(labels.some(value => value.includes('模型原生结构化输出')));
  assert.ok(labels.includes('[{"skill":"click"}]'));
  assert.ok(!labels.includes('模型未返回可用回答'));
});

test('model chat shows each actual API message once without duplicate full-array payload', () => {
  const helpers = source.slice(source.indexOf('function makeMiniButton'), source.indexOf('function waaConditionLabel'));
  const subject = source.slice(source.indexOf('function modelIoStatusLabel'), source.indexOf('function renderLibrary'));
  const context = vm.createContext({document: fakeDocument(), encodeURIComponent, openLocal() {}});
  vm.runInContext(`${helpers}\n${subject}`, context);
  const chat = context.makeModelChat([{status: 'completed', input: {messages: [
    {role: 'system', content: 'rules'},
    {role: 'user', content: 'old screenshot'},
    {role: 'assistant', content: 'old action'},
    {role: 'user', content: 'CURRENT screenshot'},
  ]}, raw_output: 'next action'}]);
  const labels = allNodes(chat).map(node => node.textContent);
  assert.ok(labels.includes('CURRENT screenshot'));
  assert.ok(labels.includes('old screenshot'));
  assert.ok(!labels.includes('查看本轮完整消息数组 · 4 条'));
  assert.equal(labels.filter(value => value === 'CURRENT screenshot').length, 1);
});

test('long chat text is displayed in full exactly once and Codex hidden system is labeled honestly', () => {
  const helpers = source.slice(source.indexOf('function makeMiniButton'), source.indexOf('function waaConditionLabel'));
  const subject = source.slice(source.indexOf('function modelIoStatusLabel'), source.indexOf('function renderLibrary'));
  const context = vm.createContext({document: fakeDocument(), encodeURIComponent, openLocal() {}});
  vm.runInContext(`${helpers}\n${subject}`, context);
  const full = 'visible content '.repeat(100);
  const chat = context.makeModelChat([{status: 'completed', provider: 'codex',
    input: {messages: [{role: 'user', content: full}]}, raw_output: 'answer'}]);
  const labels = allNodes(chat).map(node => node.textContent);
  assert.equal(labels.filter(value => value === full).length, 1);
  assert.ok(labels.some(value => value.includes('内置系统指令 · 未由 App Server 返回')));
  assert.ok(!labels.includes('展开完整内容'));
});

test('timeline separates job wall duration from agent loop and omits unmeasured phases', () => {
  const subject = source.slice(source.indexOf('function makePerformanceTimeline'), source.indexOf('function modelIoStatusLabel'));
  const context = vm.createContext({document: fakeDocument(), formatDuration(ms) {return `${ms} ms`;}});
  vm.runInContext(subject, context);
  const timeline = context.makePerformanceTimeline({total_elapsed_ms: 76230, planning_ms: 27482,
    model_roundtrip_ms: 27482, capture_ms: 10688, action_ms: 26292}, [],
    {created_at: '2026-09-23T13:50:00Z', updated_at: '2026-09-23T13:53:00Z', status: 'completed'});
  const labels = allNodes(timeline).map(node => node.textContent);
  assert.ok(labels.some(value => value.includes('任务提交至结束 180000 ms')));
  assert.ok(labels.includes('Agent 循环'));
  assert.ok(!labels.includes('模型生成等待'));
  assert.ok(!labels.includes('规划总计'));
});
