import {test, expect} from '@playwright/test';

test('browser task needs explicit paired scope and confirmation; cancellation sends nothing', async ({page}, info) => {
  const requests: any[] = [];
  await page.route('**/api/jobs', route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({json: {job_id: 'browser-ui-test', task_id: 'Ancient Beast', instruction: '开始单机游戏',
      kind: 'agent', status: 'completed', provider: 'codex', model: 'test-model',
      created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:00:00Z',
      run_facts: {model: 'test-model', execution_backend: 'browser', operation_scope: 'browser',
        input_mode: 'isolated_browser', destination: 'Codex 订阅服务', experience_path: ''}}});
  });
  await page.goto('/');
  await page.getByRole('textbox', {name: '任务指令'}).fill('开始单机游戏');
  await page.getByLabel('执行后端', {exact: true}).selectOption('browser');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText(/需要同时选择独立浏览器范围/)).toBeVisible();
  expect(requests).toHaveLength(0);
  await page.getByLabel('操作范围', {exact: true}).selectOption('browser');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('仅 Ancient Beast · 独立无头浏览器 · 不占用系统键鼠');
  await page.getByRole('dialog').getByRole('button', {name: '返回修改'}).click();
  expect(requests).toHaveLength(0);
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await page.screenshot({path: info.outputPath('browser-confirmation.png'), fullPage: true});
  await page.getByRole('dialog').getByRole('button', {name: '确认开始'}).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toMatchObject({executor_backend: 'browser', execution_scope: 'browser',
    cua_target: null, use_experience: false, provider: 'codex', mode: 'execute'});
  expect(requests[0]).not.toHaveProperty('browser_url');
});

test('local model cannot silently fall back from browser to desktop', async ({page}) => {
  let posts = 0;
  await page.route('**/api/jobs', route => {posts++; return route.abort();});
  await page.goto('/');
  await page.getByRole('textbox', {name: '任务指令'}).fill('开始游戏');
  await page.getByLabel('操作范围', {exact: true}).selectOption('browser');
  await page.getByLabel('执行后端', {exact: true}).selectOption('browser');
  await page.getByLabel('模型来源', {exact: true}).selectOption('local');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByText(/浏览器首版仅支持 Codex 或视觉模型 API/)).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(posts).toBe(0);
});
