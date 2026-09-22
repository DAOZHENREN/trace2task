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
      model_roundtrip_ms: 4410,
      timings: {load_ms: 0, preprocess_ms: 20, generate_ms: 4405, decode_ms: 0.1, total_ms: 4579},
      tokens: {input_tokens: 123, output_tokens: 4},
      memory: {
        before: {allocated_bytes: Math.round(3.99 * 1024 ** 3), reserved_bytes: Math.round(4 * 1024 ** 3)},
        peak: {peak_allocated_bytes: Math.round(4.41 * 1024 ** 3), peak_reserved_bytes: Math.round(10.61 * 1024 ** 3)},
        after_cleanup: {allocated_bytes: Math.round(3.99 * 1024 ** 3), reserved_bytes: Math.round(4 * 1024 ** 3)},
      },
    },
    {step_index: 1, status: 'error', error: 'network failed', model_roundtrip_ms: 5},
    {step_index: 2, status: 'cancelled', model_roundtrip_ms: 6},
    {step_index: 3, status: 'cancel_pending', model_roundtrip_ms: 7},
  ]);
  const labels = allNodes(timeline).map(node => node.textContent);
  assert.ok(labels.includes('查看模型输入与输出 · 4 回合'));
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
  assert.ok(!labels.some(label => label.includes('任务成功')));

  const openInput = allNodes(timeline).find(node => node.textContent === '打开输入归档');
  openInput.listeners.click();
  assert.deepEqual(opened, ['runs/a/model-io/0000/input.json']);
});
