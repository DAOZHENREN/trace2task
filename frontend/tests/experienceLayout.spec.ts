import {test, expect} from '@playwright/test';

test('legacy JSON shows compact runtime text separately from its unchanged archive and hashes', async ({page}) => {
  const trace = {id: 'legacy-d', trace_name: '历史动作序列', representation: 'D', description: '保留原文与版本'};
  const original = '历史示范\n\n{\n  "actions": [{"text": "保留  空格"}]\n}\n';
  const runtime = '历史示范\n\n{"actions":[{"text":"保留  空格"}]}\n';
  const writes: string[] = [];
  page.on('request', r => {if (r.method() === 'POST') writes.push(new URL(r.url()).pathname);});
  await page.route('**/api/state?compact=1', async route => {
    const state = await (await route.fetch()).json();
    await route.fulfill({json: {...state, active_job: null, trace_representations: [trace]}});
  });
  await page.route('**/api/traces/model-input', route => route.fulfill({json: {
    content: original, runtime_content: runtime, formatting: {kind: 'compact-json-v1',
      source_sha256: 'a'.repeat(64), runtime_sha256: 'b'.repeat(64),
      source_bytes: new TextEncoder().encode(original).length, runtime_bytes: new TextEncoder().encode(runtime).length}}}));
  await page.route('**/api/traces/body', route => route.fulfill({json: {trace_name: trace.trace_name, actions: []}}));
  await page.goto('/');
  await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('button', {name: '查看模型原文', exact: true}).click();
  const panel = page.locator('.detail-workspace');
  await expect(panel).toContainText('执行时仅去除 JSON 排版空白');
  expect(await panel.locator('.experience-preview').first().textContent()).toBe(runtime);
  await expect(panel.locator('.experience-preview').nth(1)).not.toBeVisible();
  await panel.getByText('归档原文与输入哈希（历史文件未改写）', {exact: true}).click();
  expect(await panel.locator('.experience-preview').nth(1).textContent()).toBe(original);
  await expect(panel).toContainText('归档 SHA256：' + 'a'.repeat(64));
  await expect(panel).toContainText('实际输入 SHA256：' + 'b'.repeat(64));
  expect(writes.every(path => ['/api/traces/model-input', '/api/traces/body'].includes(path))).toBe(true);
});
