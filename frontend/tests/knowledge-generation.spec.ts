import {test, expect} from '@playwright/test';

test('knowledge generation selects applications and clears identity when changing platform', async ({page}) => {
  const requests: Record<string, string>[] = [];
  await page.route('**/api/application-knowledge/drafts', r => r.fulfill({json: []}));
  await page.route('**/api/cua/windows', r => r.fulfill({json: {apps: [{name: 'Notepad'}], windows: []}}));
  await page.route('**/api/android/devices', r => r.fulfill({json: {devices: [
    {serial: 'phone', server_port: 5037, adb: 'adb', model: 'Test phone', state: 'device'},
  ]}}));
  await page.route('**/api/android/device-apps', r => r.fulfill({json: {apps: [
    {package: 'com.example.game', label: '示例游戏'},
  ]}}));
  await page.route('**/api/application-knowledge/generate', r => {
    requests.push(r.request().postDataJSON());
    return r.fulfill({json: {job_id: 'test', status: 'completed', kind: 'knowledge_generation'}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('tab', {name: /底层知识/}).click();
  await page.getByRole('button', {name: '基础知识生成', exact: true}).click();
  const apps = page.getByRole('combobox', {name: '选择应用或游戏'});
  await expect(apps).toBeEnabled();
  await apps.selectOption('Notepad');
  await page.getByLabel('生成模型', {exact: true}).selectOption('gpt-6-luna');
  await page.getByRole('region', {name: '底层知识管理'}).getByLabel('思考强度', {exact: true}).selectOption('high');
  await page.getByRole('button', {name: '生成草稿', exact: true}).click();
  expect(requests[0]).toMatchObject({scope: 'desktop', application: 'Notepad', display_name: 'Notepad', model: 'gpt-6-luna', reasoning_effort: 'high'});
  await page.getByLabel('平台', {exact: true}).selectOption('android');
  await expect(page.getByRole('button', {name: '生成草稿', exact: true})).toBeDisabled();
  await expect(apps.locator('option', {hasText: '示例游戏'})).toHaveCount(1);
  await apps.selectOption('com.example.game');
  await page.getByRole('button', {name: '生成草稿', exact: true}).click();
  expect(requests[1]).toMatchObject({scope: 'android', application: 'com.example.game', display_name: '示例游戏'});
  await expect(page.getByLabel('指定版本')).not.toBeVisible();
  await page.getByRole('checkbox', {name: '游戏与战斗机制'}).check();
  await page.getByRole('checkbox', {name: '自定义范围', exact: true}).check();
  await expect(page.getByRole('button', {name: '生成草稿', exact: true})).toBeDisabled();
  await page.getByLabel('希望了解什么').fill('自动战斗的限制');
  await page.getByRole('button', {name: '生成草稿', exact: true}).click();
  expect(requests[2].focus).toContain('通用基础');
  expect(requests[2].focus).toContain('游戏机制');
  expect(requests[2].focus).toContain('自动战斗的限制');
  await page.getByRole('checkbox', {name: '通用基础（推荐）', exact: true}).uncheck();
  await page.getByRole('checkbox', {name: '游戏与战斗机制'}).uncheck();
  await page.getByRole('checkbox', {name: '自定义范围', exact: true}).uncheck();
  await expect(page.getByRole('button', {name: '生成草稿', exact: true})).toBeDisabled();
});
