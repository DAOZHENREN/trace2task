const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/index.html'), 'utf8');
const js = fs.readFileSync(path.join(__dirname, '../src/trace2task/web/app.js'), 'utf8');

test('backend is visible beside model and incompatible scope cannot start', () => {
  assert.ok(html.includes('id="operation-scope"'));
  assert.ok(html.includes('value="selected_windows"'));
  assert.ok(html.includes('id="execution-backend-settings"'));
  assert.ok(!html.includes('id="execution-backend-settings" hidden'));
  assert.ok(html.indexOf('id="execution-backend-settings"') < html.indexOf('id="cua-target-settings"'));
  assert.ok(html.indexOf('id="cua-target-settings"') < html.indexOf('id="trained-model-settings"'));
  assert.ok(html.includes('id="execution-backend-help"'));
  assert.ok(js.includes('grid.append(models);\n  const backendSettings'));
  assert.ok(js.includes('grid.append(backendSettings);'));
  assert.ok(js.includes('document.querySelector("#local-executor").addEventListener("change"'));
  assert.ok(js.includes('elements.executeButton.disabled = busy || !backendMatchesScope()'));
  assert.ok(js.includes('if (!backendMatchesScope()) {\n    return showError('));
  assert.ok(!js.includes('backend.value = selected ? "cua" : "win32"'));
  assert.ok(js.includes('executor_backend: executorBackend'));
  assert.ok(js.includes('cua_target: cuaJobTarget(cuaSelection)'));
});

test('plan-only action is removed from the execution surface', () => {
  assert.ok(!html.includes('id="plan-button"'));
  assert.ok(!html.includes('只生成计划'));
  assert.ok(!js.includes('elements.planButton'));
  assert.ok(!js.includes('previewTrainedModel'));
});

test('experience is selected explicitly and quarantined rules are visible', () => {
  assert.ok(js.includes('"请选择任务经验"'));
  assert.ok(js.includes('请手动选择已语义编译的经验'));
  assert.ok(!js.includes('将从 ${taskpacks.filter'));
  assert.ok(js.includes('task.guidance_review_pending?.length'));
  assert.ok(js.includes('暂停生效的旧规则'));
  assert.ok(!js.includes('经验族：${task.experience_family_id}'));
});
