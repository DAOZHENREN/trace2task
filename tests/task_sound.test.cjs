// Run with: node --test tests/task_sound.test.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('task sounds: terminal transitions, mute, deduplication, no historical alerts', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8');
  const block = source.split('// Task-end audio:')[1].split('// End task-end audio.')[0];
  const elements = new Map();
  const context = vm.createContext({
    window: {},
    localStorage: {getItem: () => null},
    document: {
      addEventListener() {},
      querySelector(id) {
        if (!elements.has(id)) elements.set(id, {checked: true, addEventListener() {}});
        return elements.get(id);
      },
    },
  });
  vm.runInContext('// Task-end audio:' + block, context);
  vm.runInContext('globalThis.heard = []; playTaskSound = status => { if(taskSound.checked) heard.push(status); };', context);
  const send = (id, status) => vm.runInContext(`notifyTaskEnd(${JSON.stringify({job_id: id, status})})`, context);
  send('old', 'completed');
  assert.equal(context.heard.length, 0);
  for (const status of ['completed', 'failed', 'stopped', 'partial']) {
    send(status, 'running'); send(status, status); send(status, status);
  }
  assert.deepEqual(Array.from(context.heard), ['completed', 'failed', 'stopped', 'partial']);
  elements.get('#task-sound').checked = false;
  send('muted', 'queued'); send('muted', 'stopped');
  assert.equal(context.heard.length, 4);
  elements.get('#task-sound').checked = true;
  send('muted', 'stopped');
  assert.equal(context.heard.length, 4);
});
