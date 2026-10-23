import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';

async function fixture(page: Page) {
  const parent = {id: 'parent-b', trace_name: '保存文档', representation: 'B', description: '原始冻结方法'};
  const candidate = {id: 'candidate-r', trace_name: '保存文档改进候选', representation: 'R',
    description: '核对目录', review_status: 'pending_review'};
  const traces = [parent, candidate];
  const source = {job_id: 'source-run', task_id: '保存文档', instruction: '保存到本次指定目录',
    kind: 'agent', status: 'completed', model: 'test-model', provider: 'codex',
    created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:01:00Z',
    run_facts: {experience_id: parent.id, experience_representation: 'B', experience_sha256: 'b'.repeat(64)}};
  const generated = {...source, job_id: 'improvement-run', kind: 'improvement', instruction: '生成方法改进候选',
    run_facts: {...source.run_facts, execution_backend: 'none'},
    result: {representation: 'R', compiled_trace_id: candidate.id}};
  const runs = [source];
  const writes: string[] = []; const generations: any[] = []; const reviews: any[] = [];
  let review = {decision: 'pending_review', candidate_sha256: 'a'.repeat(64)};
  page.on('request', request => {if (request.method() === 'POST') writes.push(new URL(request.url()).pathname);});
  await page.route('**/api/state?compact=1', async route => {
    const original = await (await route.fetch()).json();
    await route.fulfill({json: {...original, active_job: null, trace_representations: traces}});
  });
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs}}));
  await page.route('**/api/workbench/runs/*?after=*', route => route.fulfill({json: {
    session: new URL(route.request().url()).pathname.includes('improvement-run') ? generated : source,
    events: [], cursor: 0, has_more: false}}));
  await page.route('**/api/traces/evolve', route => {
    generations.push(route.request().postDataJSON()); runs.unshift(generated);
    return route.fulfill({json: generated});
  });
  await page.route('**/api/traces/model-input', route => {
    expect(route.request().postDataJSON().id).toBe(candidate.id);
    return route.fulfill({json: {content: '# 保存文档\n用途。\n## 步骤\n1. 核对目录后保存。',
      parent_content: '# 保存文档\n用途。\n## 步骤\n1. 保存。', images: [], review,
      evolution: {parent_id: parent.id, source_run_id: source.job_id, generation: 1,
        condition: 'trace-compile+fixed-updater'}}});
  });
  await page.route('**/api/traces/body', route => route.fulfill({json: {representation: 'R'}}));
  await page.route('**/api/traces/evolution/review', route => {
    const payload = route.request().postDataJSON(); reviews.push(payload);
    expect(payload.id).toBe(candidate.id); expect(payload.candidate_sha256).toBe(review.candidate_sha256);
    review = {...review, decision: payload.decision}; candidate.review_status = payload.decision;
    return route.fulfill({json: review});
  });
  return {source, candidate, writes, generations, reviews};
}

test('run-linked generation needs confirmation and never starts a desktop task', async ({page}) => {
  const f = await fixture(page);
  await page.goto('/');
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.locator('.history-row').filter({hasText: f.source.instruction}).click();
  await page.getByText('运行后改进 · 固定改进器', {exact: true}).click();
  await page.getByRole('button', {name: '生成改进候选', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText(f.source.job_id);
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  expect(f.generations).toHaveLength(0);
  await page.getByRole('button', {name: '生成改进候选', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: '生成改进候选', exact: true}).click();
  await expect(page.getByRole('button', {name: '到经验库审查改进候选'})).toBeVisible();
  expect(f.generations).toHaveLength(1); expect(f.generations[0].run_id).toBe(f.source.job_id);
  await page.getByRole('button', {name: '到经验库审查改进候选'}).click();
  await expect(page.getByText('R · 运行后改进候选 · 效果尚未评测', {exact: true})).toBeVisible();
  await expect(page.locator('.detail-workspace').getByRole('button', {name: '用于新任务', exact: true})).toBeDisabled();
  expect(f.writes.every(path => ['/api/traces/evolve', '/api/traces/model-input', '/api/traces/body'].includes(path))).toBe(true);
});

test('review binds candidate hash, leaves selection unchanged, and exposes the parent', async ({page}, info) => {
  const f = await fixture(page);
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.locator('article').filter({hasText: f.candidate.trace_name})
    .getByRole('button', {name: '查看模型原文', exact: true}).click();
  const panel = page.locator('.detail-workspace');
  await panel.getByText('父方法全文（生成前）', {exact: true}).click();
  await expect(panel).toContainText('1. 保存。');
  await expect(panel.getByRole('button', {name: '用于新任务', exact: true})).toBeDisabled();
  await page.getByRole('button', {name: '审查后允许试用', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  expect(f.reviews).toHaveLength(0);
  await page.getByRole('button', {name: '审查后允许试用', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: '确认允许试用', exact: true}).click();
  await expect(panel).toContainText('已允许试用，未证明改进');
  await expect(panel.getByRole('button', {name: '用于新任务', exact: true})).toBeEnabled();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('@auto');
  expect(f.reviews).toEqual([{id: f.candidate.id, candidate_sha256: 'a'.repeat(64), decision: 'approved_for_trial'}]);
  await page.screenshot({path: info.outputPath('procedure-candidate-review.png'), animations: 'disabled'});
  await panel.getByRole('button', {name: '用于新任务', exact: true}).click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue(`trace-library/${f.candidate.id}/model-input.txt`);
  expect(f.writes.every(path => ['/api/traces/model-input', '/api/traces/body', '/api/traces/evolution/review'].includes(path))).toBe(true);
});

test('rejected candidate remains inspectable but cannot be selected', async ({page}) => {
  const f = await fixture(page);
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.locator('article').filter({hasText: f.candidate.trace_name})
    .getByRole('button', {name: '查看模型原文', exact: true}).click();
  await page.getByRole('button', {name: '拒绝候选', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: '确认拒绝', exact: true}).click();
  const panel = page.locator('.detail-workspace');
  await expect(panel).toContainText('已拒绝，禁止执行');
  await expect(panel.getByRole('button', {name: '用于新任务', exact: true})).toBeDisabled();
  await expect(page.getByRole('button', {name: '审查后允许试用', exact: true})).toHaveCount(0);
  expect(f.reviews[0].decision).toBe('rejected');
});
