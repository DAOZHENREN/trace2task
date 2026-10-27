import {test, expect, type Page} from '@playwright/test';

const details = [
  {model: 'official-wide', display_name: 'Official Wide', description: 'Model from the backend',
    reasoning_efforts: ['medium', 'ultra'], default_reasoning_effort: 'ultra', is_default: true, input_modalities: ['text', 'image']},
  {model: 'official-small', display_name: 'Official Small', description: '',
    reasoning_efforts: ['medium', 'high'], default_reasoning_effort: 'high', is_default: false, input_modalities: ['text', 'image']},
  {model: 'official-text', display_name: 'Official Text', description: '',
    reasoning_efforts: ['low'], default_reasoning_effort: 'low', is_default: false, input_modalities: ['text']},
];
const choice = {model: details[0].model, reasoning_effort: 'ultra'};
const options = {models: details.map(m => m.model), model_details: details, reasoning_efforts: ['medium', 'ultra', 'high', 'low'],
  defaults: choice, compiler_defaults: choice, catalog_status: 'ready', catalog_error: ''};

async function catalog(page: Page, value: unknown = options) {
  await page.route('**/api/state?compact=1', async route => {
    const state = await (await route.fetch()).json();
    await route.fulfill({json: {...state, active_job: null, agent_options: {...state.agent_options, ...(value as object)}}});
  });
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: []}}));
}

test('execution and compiler pickers follow official names and model-specific efforts', async ({page}) => {
  await catalog(page);
  await page.goto('/');
  const model = page.getByLabel('执行模型', {exact: true});
  const effort = page.getByLabel('思考强度', {exact: true});
  await expect(model).toHaveValue('official-wide');
  await expect(model.locator('option')).toHaveText(['Official Wide', 'Official Small', 'Official Text · 不支持截图']);
  await expect(model.locator('option[value="official-text"]')).toBeDisabled();
  await expect(effort.locator('option')).toHaveText(['medium', 'ultra']);
  await model.selectOption('official-small');
  await expect(effort).toHaveValue('high');
  await expect(effort.locator('option')).toHaveText(['medium', 'high']);
  await effort.selectOption('medium');
  await model.selectOption('official-wide');
  await expect(effort).toHaveValue('medium');
  await page.screenshot({path: '../artifacts/implementation-20261027/model-picker.png'});
  await page.getByRole('navigation', {name: '主导航'}).getByRole('button', {name: '录制示范', exact: true}).click();
  await page.getByLabel('录制环境', {exact: true}).selectOption('waa');
  const recording = page.locator('.workflow-card').filter({has: page.getByLabel('录制环境', {exact: true})});
  const compiler = recording.getByLabel('编译 / 修订模型', {exact: true});
  const compilerEffort = recording.getByLabel('编译 / 修订思考强度', {exact: true});
  await expect(compiler).toHaveValue('official-wide');
  await compiler.selectOption('official-small');
  await expect(compilerEffort).toHaveValue('high');
  await expect(compilerEffort.locator('option')).toHaveText(['medium', 'high']);
});

test('failed discovery has no invented fallback and refresh restores official defaults', async ({page}) => {
  await catalog(page, {models: [], model_details: [], reasoning_efforts: [], defaults: {model: '', reasoning_effort: ''},
    compiler_defaults: {model: '', reasoning_effort: ''}, catalog_status: 'unavailable', catalog_error: '模型目录暂不可用，请刷新。'});
  await page.route('**/api/codex/models?refresh=1', route => route.fulfill({json: options}));
  await page.goto('/');
  const model = page.getByLabel('执行模型', {exact: true});
  await expect(model).toHaveValue('');
  await expect(model.locator('option')).toHaveText(['尚未读取模型目录']);
  await expect(page.getByText('模型目录暂不可用，请刷新。', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '刷新订阅模型', exact: true}).click();
  await expect(model).toHaveValue('official-wide');
  await expect(page.getByLabel('思考强度', {exact: true})).toHaveValue('ultra');
});
