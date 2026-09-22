const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8');

test('task selector integrates baseline and preserves its target across refreshes', () => {
  const node = () => ({children: [], value: '', append(child) {this.children.push(child);},
    replaceChildren() {this.children = [];},
    get options() {return this.children.flatMap(c => c.children.length ? c.children : [c]);}});
  const selector = node();
  const elements = {taskpack: selector, executionScope: {value: 'desktop'}};
  const context = vm.createContext({elements, taskpacks: [], document: {createElement: node},
    renderTaskMeta() {}, renderLibrary() {}});
  vm.runInContext('function populateTaskpacks' + source.split('function populateTaskpacks')[1].split('let modelApiAvailable')[0], context);
  const records = [{path:'task.yaml',task_id:'Task',confirmed:true,execution_scope:'window'}];
  context.populateTaskpacks(records);
  assert.equal(selector.value, '__baseline__');
  selector.value = 'task.yaml';
  context.populateTaskpacks(records);
  assert.equal(selector.value, 'task.yaml');
  elements.executionScope.value = 'window';
  selector.value = 'baseline:task.yaml';
  context.populateTaskpacks(records);
  assert.equal(selector.value, 'baseline:task.yaml');
  vm.runInContext('function selectedTask' + source.split('function selectedTask')[1].split('function selectedWindow')[0], context);
  assert.equal(context.selectedTask().path, 'task.yaml');
});
