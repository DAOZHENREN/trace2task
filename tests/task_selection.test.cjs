const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8').replace(/\r\n/g, '\n');

test('task selector integrates baseline and preserves its target across refreshes', () => {
  const node = () => ({children: [], value: '', append(child) {this.children.push(child);},
    replaceChildren() {this.children = [];},
    get options() {return this.children.flatMap(c => c.children.length ? c.children : [c]);}});
  const selector = node();
  const elements = {taskpack: selector, executionScope: {value: 'desktop'}};
  const context = vm.createContext({elements, taskpacks: [], document: {createElement: node},
    traceRepresentations: [{id: 'sequence-one', trace_name: 'Task', representation: 'D',
      created_at: '2026-10-03T01:02:03Z', created_at_source: 'metadata'}],
    formatTimestamp: value => value, renderTaskMeta() {}, renderLibrary() {}});
  for (const name of ['sequenceExperiences', 'traceVersionTime', 'populateTaskpacks', 'selectedTask']) {
    const start = source.indexOf(`function ${name}(`);
    vm.runInContext(source.slice(start, source.indexOf('\n}\n', start) + 3), context);
  }
  const records = [{path:'task.yaml',task_id:'Task',confirmed:true,execution_scope:'window'}];
  context.populateTaskpacks(records);
  assert.equal(selector.value, '__baseline__');
  assert.equal(selector.options.some(option => option.value === 'task.yaml'), false);
  const sequencePath = 'trace-library/sequence-one/model-input.txt';
  assert.match(selector.options.find(option => option.value === sequencePath).textContent, /2026-10-03T01:02:03Z/);
  selector.value = sequencePath;
  context.populateTaskpacks(records);
  assert.equal(selector.value, sequencePath);
  assert.equal(context.selectedTask().representation, 'D');
  elements.executionScope.value = 'window';
  selector.value = 'baseline:task.yaml';
  context.populateTaskpacks(records);
  assert.equal(selector.value, 'baseline:task.yaml');
  assert.equal(context.selectedTask().path, 'task.yaml');
});
