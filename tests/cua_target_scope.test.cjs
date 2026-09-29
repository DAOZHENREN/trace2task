const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/index.html'), 'utf8');
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

test('Cua search matches title, app name and launch path without changing authorization', () => {
  const context = vm.createContext({});
  vm.runInContext(helpers, context);
  const entry = {label: '窗口：CalculatorApp.exe · 计算器', target: {pid: 10, window_id: 20}};
  const app = {label: '启动：记事本', target: {launch_path: 'C:\\Windows\\notepad.exe'}};
  assert.equal(context.cuaCatalogMatches(entry, 'calculator'), true);
  assert.equal(context.cuaCatalogMatches(entry, '计算'), true);
  assert.equal(context.cuaCatalogMatches(app, 'NOTEPAD.EXE'), true);
  assert.equal(context.cuaCatalogMatches(entry, 'wechat'), false);
  assert.ok(html.includes('id="cua-target-search"'));
  assert.match(source, /row\.hidden = !checked &&/);
  assert.match(source, /document\.querySelector\("#cua-target-search"\)\.addEventListener\("input", filterCuaCatalog\)/);
});
