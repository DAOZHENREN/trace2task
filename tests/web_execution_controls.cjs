// Run with node tests/web_execution_controls.cjs; no browser or desktop actions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8')
  .replace(/\r\n/g, '\n');
const start = source.indexOf('function setBusy(busy) {');
const code = source.slice(start, source.indexOf('\n}\n', start) + 3);
for (const trained of [false, true]) {
  for (const busy of [false, true]) {
    for (const matches of [false, true]) {
      const elements = new Proxy({viewTabs: []}, {
        get(target, name) { return target[name] ??= {}; },
      });
      elements.executionScope.value = 'desktop';
      elements.operationScope.value = 'desktop';
      elements.useExperience.checked = true;
      elements.localModel.value = 'gui-owl-2b';
      const context = {
        elements,
        document: {querySelector: () => ({}), querySelectorAll: () => []},
        selectedTask: () => ({representation: 'D'}),
        backendMatchesScope: () => matches,
        usesTrainedModel: () => trained,
        usesModelApi: () => false,
        usesWaaRecording: () => false,
      };
      vm.createContext(context);
      vm.runInContext(code, context);
      context.setBusy(busy);
      assert.equal(elements.executeButton.disabled, busy || !matches);
    }
  }
}
console.log('PASS: D experience enabled when idle; busy/scope guards retained (8 cases).');
