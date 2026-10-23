import {test, expect, type Page} from '@playwright/test';

async function fixture(page: Page, archived = false) {
  const state: any = {supported: true, phase: 'running', revision: 0, version: 1, assisted_by_user: false,
    commands: [], takeovers: [], message: '可以追加指令，或先暂停再接管手机。'};
  const run: any = {job_id: 'android-live', task_id: 'Android', kind: 'agent', instruction: '完成支援任务',
    model: 'fake-model', provider: 'codex', status: archived ? 'interrupted' : 'running', mode: 'execute',
    created_at: '2026-10-23T07:00:00Z', updated_at: '2026-10-23T07:00:00Z',
    executor_backend: 'android', run_facts: {operation_scope: 'android', destination: '测试模型服务'}, interventions: state};
  const requests: any[] = [];
  let failOnce = false;
  function update(phase: string, message: string) {Object.assign(state, {phase, message, version: state.version + 1});}
  if (archived) {
    state.assisted_by_user = true; state.phase = 'paused';
    state.commands.push({id: 'old-pause', index: 1, kind: 'pause', text: '', status: 'paused', requested_at: run.created_at});
    state.takeovers.push({id: 'takeover-1', status: 'active', before: {path: 'runs/test/before.png'}, after: null, note: ''});
  }
  await page.route('**/api/state?compact=1', async route => {
    const data = await (await route.fetch()).json();
    return route.fulfill({json: {...data, active_job: null}});
  });
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [run]}}));
  await page.route('**/api/workbench/runs/*?after=*', route => route.fulfill({json: {session: run, events: [], cursor: 0, has_more: false}}));
  await page.route('**/api/jobs/android-live/interventions', async route => {
    const body = route.request().postDataJSON();
    requests.push(body);
    if (!state.commands.some((c: any) => c.id === body.request_id)) {
      state.commands.push({id: body.request_id, index: state.commands.length + 1, kind: body.kind, text: body.text,
        requested_at: run.created_at, revision: ++state.revision, status: 'queued'});
      state.assisted_by_user = true;
      if (body.kind === 'pause') {
        update('pausing', '正在停止后续自动输入并记录接管前画面；请暂勿操作手机。');
        state.takeovers.push({id: 'takeover-1', status: 'requested', before: null, after: null, note: ''});
      } else if (body.kind === 'resume') {
        state.takeovers[0].note = body.text;
        update('resuming', '正在核对授权应用并记录交还画面；请停止手动操作。');
      } else update(state.phase, '补充指令已收到；下一轮重新规划。');
    }
    if (failOnce) {failOnce = false; return route.abort('failed');}
    return route.fulfill({json: state});
  });
  await page.route('**/api/local-image?*', route => route.fulfill({contentType: 'image/svg+xml', body:
    '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="135"><rect width="240" height="135" fill="#d4e3dc"/></svg>'}));
  await page.goto('/');
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.locator('.history-row').filter({hasText: run.instruction}).click();
  const panel = page.getByRole('region', {name: '运行中干预', exact: true});
  await expect(panel).toBeVisible();
  return {state, run, requests, panel, update, failNext: () => {failOnce = true;}};
}

test('instructions and handoff notes stay separate; pause acknowledgement and return are explicit', async ({page}, info) => {
  const f = await fixture(page);
  await f.panel.getByLabel('补充指令', {exact: true}).fill('不要消耗体力，先检查支援任务');
  await f.panel.getByRole('button', {name: '发送指令', exact: true}).click();
  await expect(f.panel).toContainText('不要消耗体力，先检查支援任务');
  await expect(f.panel.getByLabel('补充指令', {exact: true})).toHaveValue('');
  await expect(f.panel).toContainText('测试模型服务');
  await f.panel.getByRole('button', {name: '我要接管', exact: true}).click();
  await expect(f.panel).toContainText('正在交接 · 暂勿操作手机');
  await expect(f.panel.getByRole('button', {name: '交还模型', exact: true})).toHaveCount(0);
  f.state.takeovers[0].before = {path: 'runs/test/before.png'};
  f.state.takeovers[0].status = 'active';
  f.update('paused', '自动输入已暂停，可以手动操作。原预算和 F9 仍然生效。');
  await expect(f.panel.getByRole('button', {name: '交还模型', exact: true})).toBeEnabled();
  await f.panel.getByLabel('接管说明（可选）', {exact: true}).fill('我已经回到任务页，请从这里继续');
  await f.panel.getByRole('button', {name: '交还模型', exact: true}).click();
  await expect(f.panel).toContainText('正在交还 · 请停止手动操作');
  await expect(f.panel.getByRole('button', {name: '交还模型', exact: true})).toBeDisabled();
  f.state.takeovers[0].after = {path: 'runs/test/after.png'};
  f.state.takeovers[0].status = 'returned';
  f.update('running', '交还画面已保存；旧计划作废，将从新截图重新规划。');
  await expect(f.panel.getByRole('button', {name: '我要接管', exact: true})).toBeEnabled();
  await f.panel.getByText('人工接管 1 · 已交还 · 查看前后画面', {exact: true}).click();
  await expect(f.panel.getByAltText('人工接管 1 · 接管前', {exact: true})).toBeVisible();
  await expect(f.panel.getByAltText('人工接管 1 · 交还后', {exact: true})).toBeVisible();
  await f.panel.screenshot({path: info.outputPath('handoff-returned.png'), animations: 'disabled'});
  expect(f.requests.map(r => [r.kind, r.text])).toEqual([
    ['instruction', '不要消耗体力，先检查支援任务'], ['pause', ''], ['resume', '我已经回到任务页，请从这里继续']]);
  expect(new Set(f.requests.map(r => r.request_id)).size).toBe(3);
});

test('lost HTTP response retries the same request ID without duplicating an instruction', async ({page}) => {
  const f = await fixture(page);
  f.failNext();
  await f.panel.getByLabel('补充指令', {exact: true}).fill('只检查奖励，不再次领取');
  await f.panel.getByRole('button', {name: '发送指令', exact: true}).click();
  await expect(f.panel.getByRole('alert')).toContainText('复用请求标识');
  // A different button between retries must not lose the instruction's dedupe ID.
  await f.panel.getByRole('button', {name: '我要接管', exact: true}).click();
  await expect(f.panel).toContainText('正在交接 · 暂勿操作手机');
  await f.panel.getByRole('button', {name: '发送指令', exact: true}).click();
  await expect(f.panel.getByLabel('补充指令', {exact: true})).toHaveValue('');
  expect(f.requests).toHaveLength(3);
  expect(f.requests[0]).toEqual(f.requests[2]);
  expect(f.state.commands.filter((c: any) => c.kind === 'instruction')).toHaveLength(1);
});

test('archived tasks offer no resume and do not fabricate a missing return image', async ({page}) => {
  const f = await fixture(page, true);
  await expect(f.panel).toContainText('运行已结束');
  await expect(f.panel.getByRole('button')).toHaveCount(0);
  await f.panel.getByText('人工接管 1 · 未完成交还 · 查看前后画面', {exact: true}).click();
  await expect(f.panel).toContainText('交还后：没有可用截图');
  expect(f.requests).toHaveLength(0);
});

test('handoff panel remains usable in a narrow native-sized window', async ({page}, info) => {
  await page.setViewportSize({width: 520, height: 780});
  const f = await fixture(page);
  await f.panel.getByLabel('补充指令', {exact: true}).fill('检查当前任务'.repeat(50));
  await f.panel.getByRole('button', {name: '发送指令', exact: true}).click();
  await f.panel.screenshot({path: info.outputPath('handoff-narrow.png'), animations: 'disabled'});
  const overflow = await f.panel.evaluate(el => ({width: el.clientWidth, scroll: el.scrollWidth}));
  expect(overflow.scroll).toBeLessThanOrEqual(overflow.width + 1);
  await expect(f.panel.getByRole('button', {name: '我要接管', exact: true})).toBeEnabled();
});

test('experience preview distinguishes manual evidence and explains its transmission', async ({page}) => {
  const f = await fixture(page, true);
  await page.route('**/api/run-experience/preview', route => route.fulfill({json: {
    run_id: f.run.job_id, instruction: f.run.instruction, status: 'interrupted', outcome: '人工辅助 · 运行未完成',
    scope: 'android', package: 'com.example.game', model: 'fake', round_count: 2, delivered_count: 1,
    image_count: 2, assisted_by_user: true, intervention_count: 2, takeover_count: 1,
    source_sha256: 'fixture-hash', drafts: [], input: 'synthetic human evidence', images: [
      {id: 'round', step_index: 0, file: 'round.png', preview_path: 'runs/test/round.png'},
      {id: 'manual-before', label: '人工接管 1 · 接管前', file: 'human.png', preview_path: 'runs/test/before.png'},
    ],
  }}));
  await page.getByRole('button', {name: '提炼为经验', exact: true}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('人工辅助来源 · 2 条干预、1 段接管');
  await expect(dialog.getByAltText('人工接管 1 · 接管前', {exact: true})).toBeVisible();
  await expect(dialog).toContainText('人工指令与接管说明');
  await expect(dialog).toContainText('不能算作自主成功');
  await dialog.getByRole('button', {name: '暂不提炼', exact: true}).click();
  expect(f.requests).toHaveLength(0);
});
