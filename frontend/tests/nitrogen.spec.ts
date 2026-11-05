import {test, expect} from '@playwright/test';

test('NitroGen confirms the exact Windows process and uses its own run route', async ({page}) => {
  await page.route('**/api/windows', route => route.fulfill({json: {windows: [
    {title: '蔚蓝', process_name: 'celeste.exe', process_id: 10},
    {title: 'Another Game', process_name: 'another.exe', process_id: 20},
  ]}}));
  await page.route('**/api/nitrogen/start', async route => {
    expect(route.request().postDataJSON()).toEqual({process: 'celeste.exe', seconds: 60});
    await route.fulfill({json: {job_id: 'nitrogen-test', model: 'nvidia/NitroGen', kind: 'nitrogen', status: 'stopped'}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '模型与连接', exact: true}).click();
  const search = page.getByRole('combobox', {name: '游戏进程', exact: true});
  await search.fill('蔚蓝');
  await expect(page.getByRole('button', {name: '启动 NitroGen', exact: true})).toBeDisabled();
  await expect(page.getByRole('option', {name: /another.exe/})).toHaveCount(0);
  await page.getByRole('option', {name: '蔚蓝 · celeste.exe'}).click();
  await page.getByRole('button', {name: '刷新运行中的程序'}).click();
  await expect(page.getByRole('button', {name: '刷新运行中的程序'})).toBeEnabled();
  await expect(page.getByRole('button', {name: '启动 NitroGen', exact: true})).toBeDisabled();
  await search.fill('CELESTE');
  await page.getByRole('option', {name: '蔚蓝 · celeste.exe'}).click();
  await page.getByRole('button', {name: '启动 NitroGen', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('celeste.exe');
  const started = page.waitForResponse('**/api/nitrogen/start');
  await page.getByRole('button', {name: '开始游戏控制', exact: true}).click();
  expect((await started).ok()).toBe(true);
});

test('NitroGen can start from the local model task composer and show its round', async ({page}) => {
  const id = 'nitrogen-workbench';
  const artifact = `/api/workbench/runs/${id}/rounds/0`;
  const session = {job_id: id, instruction: 'NitroGen · celeste.exe', model: 'nvidia/NitroGen',
    kind: 'nitrogen', provider: 'nitrogen', status: 'stopped'};
  await page.route('**/api/windows', route => route.fulfill({json: {windows: [
    {title: '蔚蓝', process_name: 'celeste.exe', process_id: 10},
  ]}}));
  await page.route('**/api/nitrogen/start', route => {
    expect(route.request().postDataJSON()).toEqual({process: 'celeste.exe', seconds: 60, task_text: '穿过右门'});
    return route.fulfill({json: session});
  });
  await page.route(`**/api/workbench/runs/${id}?*`, route => route.fulfill({json: {
    session, cursor: 1, has_more: false, events: [{name: 'model.round', attributes: {
      step_index: 0, status: 'predicted', tokens: {}, screenshot: 'runs/input.png',
      after_screenshot: 'runs/after.png', submitted_steps: 18, artifact_ref: artifact,
    }}],
  }}));
  await page.route(`**${artifact}`, route => route.fulfill({json: {
    raw_output: '{"j_left": [[0.2, 0.0]], "buttons": [[1]]}', button_order: ['SOUTH'],
  }}));
  await page.goto('/');
  await page.getByLabel('模型来源', {exact: true}).selectOption('local');
  await page.getByLabel('执行模型', {exact: true}).selectOption('nitrogen');
  await expect(page.getByRole('textbox', {name: '任务指令'})).toHaveCount(0);
  await page.getByLabel('截图任务文字（可选）', {exact: true}).fill('穿过右门');
  await page.getByRole('combobox', {name: '游戏进程', exact: true}).fill('蔚蓝');
  await page.getByRole('option', {name: '蔚蓝 · celeste.exe'}).click();
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await page.getByRole('button', {name: '开始游戏控制', exact: true}).click();
  await expect(page.getByText('已提交 18 帧手柄输入 · 游戏效果待核对')).toBeVisible();
  await expect(page.getByText('第 1 轮 · 动作后画面')).toBeVisible();
  await page.getByText('模型输入、输出与执行回执', {exact: true}).click();
  await expect(page.getByText(/"j_left":/)).toBeVisible();
});
