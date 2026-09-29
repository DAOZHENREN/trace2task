const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('component UI submits explicit choices and renders errors as text', async () => {
  const elements = new Map();
  const select = (id) => {
    if (!elements.has(id)) elements.set(id, {value: '', addEventListener() {}});
    return elements.get(id);
  };
  const requests = [];
  const context = {
    document: {querySelector: select, getElementById: id => select('#' + id)},
    confirm: () => true, setInterval() {},
    request: async (url, options) => {
      requests.push({url, options});
      return {status: 'failed', message: '<script>untrusted</script>', python: 'own/python.exe',
        models: 'own/models', default_directory: 'D:/components', log: 'download failed'};
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/trace2task/web/components.js'), 'utf8'), context);
  select('#component-directory').value = 'D:/my components';
  select('#component-model').value = 'qwen3-vl-2b';
  await select('#component-runtime').onclick();
  assert.equal(JSON.parse(requests[0].options.body).directory, 'D:/my components');
  assert.equal(JSON.parse(requests[0].options.body).action, 'runtime');
  assert.match(select('#component-status').textContent, /<script>untrusted/);
  select('#component-d-bundle').value = 'D:/private model';
  await select('#component-import-d').onclick();
  assert.equal(JSON.parse(requests[2].options.body).directory, 'D:/private model');
  assert.equal(select('#component-cancel').disabled, true);
});
