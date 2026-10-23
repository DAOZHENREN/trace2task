import {test, expect, type Page} from '@playwright/test';

async function fixture(page: Page) {
  const source = {job_id: 'battle-source', task_id: 'Android', instruction: '帮我完成精灵对战',
    kind: 'agent', status: 'completed', model: 'test-model', provider: 'codex',
    created_at: '2026-10-22T14:21:00Z', updated_at: '2026-10-22T14:34:00Z',
    result: {stop_reason: 'visual_completion_reviewed'}};
  const generated = {...source, job_id: 'experience-job', kind: 'experience_compilation',
    instruction: '提炼运行经验：帮我完成精灵对战', result: {experience_draft_id: 'draft-1'}};
  const summary = {run_id: source.job_id, instruction: source.instruction, status: source.status,
    outcome: '模型视觉复核完成 · 非独立验证', stop_reason: 'visual_completion_reviewed', scope: 'android',
    package: 'com.example.game', model: 'test-model', round_count: 32, delivered_count: 30, image_count: 4};
  const images = [0, 10, 20, 31].map(i => ({id: `frame-${i}`, step_index: i, file: `evidence/${i}.png`, preview_path: `runs/demo/${i}.png`}));
  let saved: string | null = null;
  const content = '# 对战过程中的状态判断\n在同一应用的对战界面内，根据当前观察选择下一步。\n## 适用条件\n已进入对战；沿用当前任务授权，不预设阵容和对手。\n## 步骤\n1. 等待画面加载并判断是否进入可交互阶段。[第 1 轮]\n2. 根据可见选项选择行动，不复用历史点击坐标。[第 11 轮]\n3. 在行动结束后重新观察，确认是否还需要继续。[第 21 轮]\n## 完成判断\n核对结算界面；本次只有模型视觉复核，不是独立成功验证。\n## 注意与局限\n此为界面测试文案，不是实际游戏经验，也不能据此认定策略最优。';
  const writes: {path: string; body: any}[] = [];
  page.on('request', r => {if (r.method() === 'POST') writes.push({path: new URL(r.url()).pathname, body: r.postDataJSON()});});
  await page.route('**/api/state?compact=1', async route => {
    const data = await (await route.fetch()).json();
    return route.fulfill({json: {...data, active_job: null, trace_representations: saved ? [{id: saved, trace_name: '对战方法',
      representation: 'B', description: '测试', source_kind: 'agent_run', source_scope: 'android', source_package: 'com.example.game'}] : []}});
  });
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [source]}}));
  await page.route('**/api/workbench/runs/*?after=*', route => route.fulfill({json: {
    session: route.request().url().includes('experience-job') ? generated : source, events: [], cursor: 0, has_more: false}}));
  await page.route('**/api/run-experience/preview', route => route.fulfill({json: {...summary, images,
    source_sha256: 'source-digest', drafts: [], input: '{"evidence":"only projected history"}'}}));
  await page.route('**/api/run-experience/generate', route => route.fulfill({json: generated}));
  await page.route('**/api/run-experience/draft', route => route.fulfill({json: {draft_id: 'draft-1', draft_sha256: 'draft-digest',
    content, trace_name: '对战过程中的状态判断', description: '测试', saved_trace_id: saved, summary, compiler_images: images}}));
  await page.route('**/api/run-experience/save', route => {saved = 'saved-method'; return route.fulfill({json: {id: saved}});});
  await page.route('**/api/local-image?*', route => route.fulfill({contentType: 'image/svg+xml', body:
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="110"><rect width="200" height="110" fill="#c8d7c1"/><rect x="12" y="20" width="176" height="70" rx="8" fill="#ecf1e6"/><text x="100" y="61" font-size="12" text-anchor="middle" fill="#668357">TEST OBSERVATION</text></svg>'}));
  await page.goto('/');
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.locator('.history-row').filter({hasText: source.instruction}).click();
  return {source, writes};
}

test('preview discloses real source and sends nothing until generate; cancellation is safe', async ({page}, info) => {
  const f = await fixture(page);
  await page.getByRole('button', {name: '提炼为经验', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('审计 SHA、文件路径与来源校验留在本地');
  await expect(page.getByRole('dialog').getByText('查看实际发送的任务证据', {exact: true})).toBeVisible();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText(f.source.instruction);
  await expect(dialog).toContainText('模型视觉复核完成 · 非独立验证');
  await expect(dialog).toContainText('Codex 订阅服务');
  await expect(dialog.getByAltText('源运行第 32 轮截图')).toBeVisible();
  await dialog.screenshot({path: info.outputPath('experience-source.png'), animations: 'disabled'});
  await dialog.getByRole('button', {name: '暂不提炼', exact: true}).click();
  expect(f.writes.every(w => w.path === '/api/run-experience/preview')).toBe(true);
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('@auto');
});

test('draft is digest-bound, saved only after review, never selected or executed automatically', async ({page}, info) => {
  const f = await fixture(page);
  await page.getByRole('button', {name: '提炼为经验', exact: true}).click();
  await page.getByRole('button', {name: '生成经验草稿', exact: true}).click();
  await page.getByRole('button', {name: '审阅经验草稿', exact: true}).click();
  await expect(page.locator('.experience-invitation')).toHaveCount(1);
  await expect(page.getByRole('button', {name: '提炼为经验', exact: true})).toHaveCount(0);
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('对战过程中的状态判断');
  await expect(dialog.getByRole('button', {name: '保存到经验库', exact: true})).toBeDisabled();
  await dialog.getByRole('checkbox').check();
  await dialog.screenshot({path: info.outputPath('experience-draft.png'), animations: 'disabled'});
  await dialog.getByRole('button', {name: '保存到经验库', exact: true}).click();
  await expect(dialog.getByRole('status')).toContainText('没有启动新任务');
  expect(f.writes.find(w => w.path.endsWith('/generate'))?.body).toMatchObject({run_id: f.source.job_id, source_sha256: 'source-digest'});
  expect(f.writes.find(w => w.path.endsWith('/save'))?.body).toEqual({draft_id: 'draft-1', draft_sha256: 'draft-digest', confirmed: true});
  await dialog.getByRole('button', {name: '稍后再看', exact: true}).click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('@auto');
  expect(f.writes.every(w => w.path.startsWith('/api/run-experience/'))).toBe(true);
  await page.getByRole('button', {name: '审阅经验草稿', exact: true}).click();
  await expect(page.getByRole('button', {name: '到经验库查看', exact: true})).toBeVisible();
});

test('source preview error does not expose generation or leak old source', async ({page}) => {
  const f = await fixture(page);
  // A previous successful preview must not remain usable after a failed retry.
  await page.getByRole('button', {name: '提炼为经验', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText(f.source.instruction);
  await page.getByRole('button', {name: '暂不提炼', exact: true}).click();
  await page.route('**/api/run-experience/preview', route => route.fulfill({status: 400,
    json: {error: '父经验快照已变化，不能据此提炼'}}));
  await page.getByRole('button', {name: '提炼为经验', exact: true}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('alert')).toContainText('父经验快照已变化');
  await expect(dialog.getByRole('alert')).toBeInViewport();
  await expect(dialog).not.toContainText(f.source.instruction);
  await expect(dialog.getByRole('button', {name: '生成经验草稿', exact: true})).toBeDisabled();
  expect(f.writes).toHaveLength(2);
  await page.unroute('**/api/run-experience/preview');
  await page.route('**/api/run-experience/preview', route => route.fulfill({json: {
    run_id: f.source.job_id, instruction: f.source.instruction, scope: 'android', package: 'com.example.game',
    outcome: '运行未完成', round_count: 113, delivered_count: 112, image_count: 0,
    source_sha256: 'retry-digest', images: [], drafts: [], input: '{}'}}));
  await dialog.getByRole('button', {name: '重新读取', exact: true}).click();
  await expect(dialog.getByRole('alert')).toHaveCount(0);
  await expect(dialog).toContainText(f.source.instruction);
  await expect(dialog.getByRole('button', {name: '生成经验草稿', exact: true})).toBeEnabled();
  expect(f.writes.every(w => w.path === '/api/run-experience/preview')).toBe(true);
  await dialog.getByRole('button', {name: '暂不提炼', exact: true}).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('preview opens immediately with a local loading state before the response arrives', async ({page}) => {
  const f = await fixture(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/run-experience/preview', async route => {
    await held;
    await route.fallback();
  });
  try {
    await page.getByRole('button', {name: '提炼为经验', exact: true}).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('status')).toContainText('正在读取并校验本次轨迹');
    await expect(dialog.getByRole('button', {name: '生成经验草稿', exact: true})).toBeDisabled();
    await dialog.getByRole('button', {name: '关闭', exact: true}).click();
    await expect(dialog).toHaveCount(0);
  } finally {release();}
  await expect(page.getByRole('button', {name: '提炼为经验', exact: true})).toBeEnabled();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(f.writes.every(w => w.path === '/api/run-experience/preview')).toBe(true);
});

test('studio fits a narrow window and leaves footer actions reachable', async ({page}, info) => {
  await page.setViewportSize({width: 520, height: 780});
  await fixture(page);
  await page.getByRole('button', {name: '提炼为经验', exact: true}).click();
  await expect(page.getByRole('button', {name: '生成经验草稿', exact: true})).toBeInViewport();
  await page.screenshot({path: info.outputPath('experience-narrow.png'), fullPage: true, animations: 'disabled'});
  const overflow = await page.locator('.experience-studio').evaluate(el => ({
    width: el.clientWidth, scroll: el.scrollWidth,
    wide: [...el.querySelectorAll('*')].filter(c => c.getBoundingClientRect().width > el.clientWidth + 1)
      .map(c => ({tag: c.tagName, classes: c.className, width: c.getBoundingClientRect().width})),
  }));
  expect(overflow.scroll, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.width + 1);
});
