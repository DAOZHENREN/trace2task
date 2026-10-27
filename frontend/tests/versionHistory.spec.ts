import {createHash} from 'node:crypto';
import {test, expect} from '@playwright/test';

test('current-only library and selector retain readable history and explicit rollback', async ({page}, info) => {
  let current = 'new', revision = 2;
  const content = '# 历史保存方法\n\n核对文件名后保存。\n';
  const writes: any[] = [];
  const versions = () => [
    {id: 'new', description: '修订后的保存方法', created_at: '2026-10-27T02:00:00Z'},
    {id: 'old', description: '原始保存方法', created_at: '2026-10-26T02:00:00Z'},
  ].map(v => ({...v, trace_name: '保存文档', representation: 'B', source_kind: 'agent_run',
    family_id: 'old', current_version_id: current, is_current: v.id === current,
    version_count: 2, version_revision: revision}));
  await page.route('**/api/state?compact=1', async route => {
    const state = await (await route.fetch()).json();
    await route.fulfill({json: {...state, trace_representations: versions()}});
  });
  await page.route('**/api/traces/model-input', route => {
    expect(route.request().postDataJSON().id).toBe('old');
    return route.fulfill({json: {content, format: 'procedure-markdown-v1'}});
  });
  await page.route('**/api/traces/body', route => route.fulfill({json: {representation: 'B'}}));
  await page.route('**/api/traces/rollback', route => {
    const body = route.request().postDataJSON(); writes.push(body);
    expect(body).toEqual({id: 'old', expected_revision: 2,
      model_input_sha256: createHash('sha256').update(content).digest('hex')});
    current = 'old'; revision++;
    return route.fulfill({json: {id: current, previous_id: 'new', version_revision: revision}});
  });
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  const cards = page.locator('article.experience-card');
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText('修订后的保存方法');
  const selector = page.getByLabel('参考经验', {exact: true});
  await expect(selector.locator('option[value="trace-library/old/model-input.txt"]')).toHaveCount(0);
  await cards.getByRole('button', {name: '用于新任务', exact: true}).click();
  await expect(selector).toHaveValue('trace-library/new/model-input.txt');
  await page.getByRole('button', {name: '经验库', exact: true}).click();
  await cards.getByRole('button', {name: '查看版本历史', exact: true}).click();
  await expect(cards).toHaveCount(2);
  await expect(page.getByRole('button', {name: '批量移到回收站'})).toHaveCount(0);
  const old = cards.filter({hasText: '原始保存方法'});
  await expect(old.getByRole('button', {name: '用于新任务'})).toBeDisabled();
  await old.getByRole('button', {name: '查看模型原文'}).click();
  const detail = page.locator('.detail-workspace');
  expect(await detail.locator('.experience-preview').textContent()).toBe(content);
  await detail.getByRole('button', {name: '回滚为当前版本', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('回滚目标：old');
  await expect(page.getByRole('dialog')).toContainText('当前版本：new');
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  expect(writes).toHaveLength(0);
  await page.screenshot({path: info.outputPath('version-history.png'), animations: 'disabled'});
  await detail.getByRole('button', {name: '回滚为当前版本', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: '确认回滚', exact: true}).click();
  await expect(page.getByText('已回滚为当前版本。后续自动选择使用此版本，其他版本保留在版本历史中。')).toBeVisible();
  await expect(selector).toHaveValue(''); // Clear the no-longer-current manually selected version.
  await expect(selector.locator('option[value="trace-library/new/model-input.txt"]')).toHaveCount(0);
  await expect(selector.locator('option[value="trace-library/old/model-input.txt"]')).toHaveCount(1);
  await expect(detail.getByRole('button', {name: '回滚为当前版本'})).toHaveCount(0);
  await page.getByRole('tab', {name: '经验版本 · 1', exact: true}).click();
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText('原始保存方法');
  await page.reload(); await page.getByRole('button', {name: '经验库', exact: true}).click();
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText('原始保存方法');
  await page.getByRole('tab', {name: '版本历史 · 1', exact: true}).click();
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText('修订后的保存方法');
  expect(writes).toHaveLength(1);
  await page.unrouteAll({behavior: 'wait'});
});
