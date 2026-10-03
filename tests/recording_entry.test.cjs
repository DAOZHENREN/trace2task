const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

for (const confirm of [false, true]) {
  test(`legacy duplicate compilation ${confirm ? 'confirmation' : 'cancellation'} preserves the two-phase contract`, async () => {
    const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8').replace(/\r\n/g, '\n');
    const requests = [];
    const context = {window: {confirm: message => {assert.match(message, /已有版本/); return confirm;}},
      formatTimestamp: value => value,
      request: async (endpoint, options) => {
        const body = JSON.parse(options.body); requests.push(body);
        return body.confirm_duplicate === true ? {id: 'new-version'} : {confirmation_required: true,
          message: '已有版本，确认继续？', existing_versions: [{trace_name: 'same', created_at: '2026-10-03', id: 'old'}]};
      }};
    vm.createContext(context);
    for (const name of ['traceVersionTime', 'requestCompilation']) {
      const start = source.indexOf(`${name === 'requestCompilation' ? 'async ' : ''}function ${name}(`);
      vm.runInContext(source.slice(start, source.indexOf('\n}\n', start) + 3), context);
    }
    const result = await context.requestCompilation('/api/traces/generate-d', {trace_path: 'runs/demo/events.jsonl'});
    assert.equal(requests.length, confirm ? 2 : 1);
    assert.equal(result?.id || null, confirm ? 'new-version' : null);
    if (confirm) assert.equal(requests[1].confirm_duplicate, true);
    assert.equal(requests[0].confirm_duplicate, undefined);
  });
}

test('recording entry defaults to OpenCUA without a window selector', () => {
  const root = path.join(__dirname, '../src/trace2task/web');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const js = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
  const choices = html.match(/<select id="record-source">([\s\S]*?)<\/select>/)[1];
  assert.deepEqual([...choices.matchAll(/value="([^"]+)"/g)].map(m => m[1]), ['opencua', 'waa']);
  assert.doesNotMatch(html, /id="record-window"|id="refresh-windows"/);
  assert.doesNotMatch(js, /selectedWindow\(|elements\.recordWindow|localWindows/);
  const start = js.split('async function startRecording()')[1].split('async function upgradeTask')[0];
  assert.match(start, /recording_backend: "opencua"/);
  assert.doesNotMatch(start, /windowInfo|handle:/);
  assert.match(js, /D · 精简动作序列/);
  assert.match(js, /本地查看/);
  assert.match(js, /recording\.derivation\?\.status === "completed"/);
});
