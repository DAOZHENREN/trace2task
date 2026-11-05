import {test, expect} from '@playwright/test';

test('slow screenshot stays visible above folded logs and failure retains phase timings', async ({page}, info) => {
  let failed = false;
  const run = {job_id: 'capture-latency-fixture', task_id: 'Android', instruction: '截图延迟诊断',
    kind: 'agent', status: 'running', provider: 'trained_d', model: 'gui-decisions-2b',
    created_at: '2026-10-28T07:31:00Z', updated_at: '2026-10-28T07:32:00Z'};
  await page.route('**/api/state?compact=1', async route => {
    const data = await (await route.fetch()).json();
    return route.fulfill({json: {...data, active_job: null}});
  });
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [run]}}));
  await page.route(`**/api/workbench/runs/${run.job_id}?after=*`, route => route.fulfill({json: {
    session: failed ? {...run, status: 'failed', result: {stop_reason: 'error', performance: {
      capture_ms: 183000, model_roundtrip_ms: 1300, action_ms: 500, total_elapsed_ms: 185000,
    }}} : run,
    cursor: 1, has_more: false,
    events: route.request().url().endsWith('after=0') ? [{name: 'run.log', seq: 1,
      attributes: {message: '正在读取手机截图，已等待 55 秒（本次上限 60 秒）；可停止。'}}] : [],
  }}));
  await page.goto('/');
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.locator('.history-row').filter({hasText: run.instruction}).click();
  const status = page.locator('.notice[role="status"]');
  await expect(status).toBeVisible();
  await expect(status).toContainText('正在读取手机截图，已等待 55 秒');
  await expect(page.locator('.log-disclosure')).not.toHaveAttribute('open');
  await page.screenshot({path: info.outputPath('capture-wait.png'), fullPage: true});
  failed = true;
  await expect(page.getByLabel('阶段耗时')).toContainText('截图 183.00 秒 · 模型往返 1.30 秒');
  await expect(page.getByLabel('阶段耗时')).toContainText('总计 185.00 秒');
  await expect(status).toHaveCount(0);
});
