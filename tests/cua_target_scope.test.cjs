const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8');
const helpers = source.slice(
  source.indexOf('function cuaTargetKey'),
  source.indexOf('function selectedCuaKeys'),
);

function target(pid, windowId) {
  return {pid, window_id: windowId};
}

test('Cua serializes only explicitly selected targets, never unrelated catalog entries', () => {
  const context = vm.createContext({});
  vm.runInContext(helpers, context);
  const calculator = target(10, 100);
  const realtek = target(20, 200);
  const entries = [
    {key: context.cuaTargetKey(calculator), target: calculator, label: '窗口：Calculator'},
    {key: context.cuaTargetKey(realtek), target: realtek, label: '窗口：Realtek Audio Console'},
  ];
  const config = context.cuaTargetConfig(entries, new Set([entries[0].key]), entries[0].key);
  assert.deepEqual(JSON.parse(JSON.stringify(config)), {targets: [calculator], initial_index: 0});
  assert.equal(JSON.stringify(config).includes('Realtek'), false);
  assert.equal(JSON.stringify(config).includes('200'), false);
});

test('Cua preserves selected order and indexes the chosen initial target in that scoped list', () => {
  const context = vm.createContext({});
  vm.runInContext(helpers, context);
  const notes = target(1, 11);
  const calculator = target(2, 22);
  const app = {launch_path: 'C:\\Windows\\System32\\notepad.exe'};
  const entries = [
    {key: context.cuaTargetKey(notes), target: notes},
    {key: context.cuaTargetKey(calculator), target: calculator},
    {key: context.cuaTargetKey(app), target: app},
  ];
  const config = context.cuaTargetConfig(
    entries,
    new Set([entries[0].key, entries[2].key]),
    entries[2].key,
  );
  assert.deepEqual(JSON.parse(JSON.stringify(config)), {targets: [notes, app], initial_index: 1});
});

test('Cua target labels use textContent rather than HTML insertion for catalog names', () => {
  assert.match(source, /text\.textContent = entry\.label/);
  assert.doesNotMatch(source.slice(source.indexOf('function renderCuaCatalog'), source.indexOf('function cuaTargetSelection')), /innerHTML/);
});
