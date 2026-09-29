const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

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
  assert.match(js, /打开动作图文目录/);
  assert.match(js, /recording\.derivation\?\.status === "completed"/);
});
