import {test, expect} from '@playwright/test';
import type {TrashItem} from '../src/types';
import type {Page} from '@playwright/test';

async function acceptConfirmation(page: Page, label: string) {
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', {name: label, exact: true}).click();
  await expect(dialog).toHaveCount(0);
}

test.beforeEach(async ({page}) => {page.on('dialog', async dialog => {
  await dialog.dismiss(); throw new Error(`Unexpected browser-native confirmation: ${dialog.message()}`);
});});

test.afterEach(async ({page}) => {await page.unrouteAll({behavior: 'wait'});});

test('C combines the explicitly selected B, previews evidence and requires explicit use', async ({page}, info) => {
  const b = {id: 'b-selected', trace_name: '保存文档', representation: 'B', description: '选定版本', created_at: '2026-10-08T10:00:00Z'};
  const other = {...b, id: 'b-other', description: '另一版本', created_at: '2026-10-07T10:00:00Z'};
  const c = {...b, id: 'c-combined', representation: 'C', description: 'B 原文与 A 证据'};
  const traces: object[] = [b, other]; const writes: string[] = []; const requests: any[] = [];
  const content = '# 组合经验 C\n\n# 保存文档\n用途原文。\n## 步骤\n1. 保存。\n\n## A 原始证据（原文）\n冻结的动作文本';
  const image = {id: '0000', file: 'evidence/images/0000.png', sha256: 'test'};
  page.on('request', r => {if (r.method() === 'POST') writes.push(new URL(r.url()).pathname);});
  await page.route('**/api/state?compact=1', async route => {const state = await (await route.fetch()).json();
    await route.fulfill({json: {...state, trace_representations: traces}});});
  await page.route('**/api/traces/generate-c', route => {
    const body = route.request().postDataJSON(); requests.push(body); expect(body.b_id).toBe(b.id);
    if (traces.some((t: any) => t.id === c.id)) return route.fulfill({json: {confirmation_required: true,
      representation: 'C', message: '此 B 已有 C，是否生成新版本？', existing_versions: [c]}});
    traces.push(c); return route.fulfill({json: {id: c.id, representation: 'C', source_b_id: b.id}});
  });
  await page.route('**/api/traces/model-input', route => {expect(route.request().postDataJSON().id).toBe(c.id);
    return route.fulfill({json: {content, format: 'procedure-with-evidence-v1', images: [image], image_selection: {rule: '沿用 B 冻结的 A 图片'}}});});
  await page.route('**/api/traces/body', route => route.fulfill({json: {representation: 'C', source_b_id: b.id, frames: []}}));
  await page.route('**/api/traces/asset?*', route => route.fulfill({contentType: 'image/png',
    body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64')}));
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  const source = page.locator('article').filter({hasText: '选定版本'});
  await source.getByRole('button', {name: '生成组合证据 C', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText(b.id);
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click(); expect(requests).toHaveLength(0);
  await source.getByRole('button', {name: '生成组合证据 C', exact: true}).click(); await acceptConfirmation(page, '生成组合证据 C');
  await expect(page.getByText(/已生成组合证据 C（c-combined）/)).toBeVisible();
  const combined = page.locator('article').filter({hasText: '可执行参考 · 组合证据 C'});
  await combined.getByRole('button', {name: '查看模型原文', exact: true}).click();
  await expect(page.getByText('组合证据 C · 同一 B 原文 + 对应 A 证据', {exact: true})).toBeVisible();
  expect(await page.locator('.detail-workspace .experience-preview').textContent()).toBe(content);
  await expect(page.getByRole('heading', {name: '实际发送的历史图片 · 1 张'})).toBeVisible();
  await page.screenshot({path: info.outputPath('combined-c.png'), animations: 'disabled'});
  await source.getByRole('button', {name: '生成组合证据 C', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: '生成组合证据 C', exact: true}).click();
  await expect(page.getByRole('dialog', {name: '重新生成组合证据 C？'})).toBeVisible();
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  expect(traces).toHaveLength(3); expect(requests).toHaveLength(2);
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('@auto');
  await combined.getByRole('button', {name: '用于新任务', exact: true}).click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('trace-library/c-combined/model-input.txt');
  expect(writes.every(path => ['/api/traces/generate-c', '/api/traces/model-input', '/api/traces/body'].includes(path))).toBe(true);
});

test('bulk recycle scopes selection, cancels safely, reports partial failure and restores run history', async ({page}, info) => {
  let traces = ['经验一', '经验二'].map((name, i) => ({id: `bulk-${i}`, trace_name: name, description: '批量测试', representation: 'D'}));
  let runs = ['运行一', '运行二'].map((name, i) => ({job_id: `run-${i}`, task_id: name, instruction: name,
    kind: 'agent', model: 'test', provider: 'codex', status: 'completed', created_at: '2026-10-08T10:00:00Z'}));
  const originalRuns = [...runs]; const trash: TrashItem[] = []; const writes: string[] = [];
  let failSecond = true;
  const recording = {task_id: '录制批量测试', trace_path: 'runs/bulk/events.jsonl', local_path: 'runs/bulk',
    recording_backend: 'opencua', success: true, input_events: 2};
  await page.route('**/api/state?compact=1', async route => {const state = await (await route.fetch()).json();
    await route.fulfill({json: {...state, active_job: null, trace_representations: traces, recordings: [recording], trash}});});
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs}}));
  await page.route('**/api/traces/delete', route => {
    const {id} = route.request().postDataJSON(); writes.push(id);
    const trace = traces.find(t => t.id === id)!;
    trash.push({path: `trace-library/.trash/${id}`, name: trace.trace_name, kind: 'trace_experience', can_restore: true, can_delete: true});
    traces = traces.filter(t => t.id !== id); return route.fulfill({json: {deleted: true}});
  });
  await page.route('**/api/workbench/runs/delete', route => {
    const {id} = route.request().postDataJSON(); writes.push(id);
    if (id === 'run-1' && failSecond) return route.fulfill({status: 409, json: {error: '模拟记录被占用'}});
    const run = runs.find(r => r.job_id === id)!;
    trash.push({path: `workbench-run/${id}`, name: run.instruction, kind: 'run', can_restore: true, can_delete: true, can_open: false});
    runs = runs.filter(r => r.job_id !== id); return route.fulfill({json: {deleted: true}});
  });
  await page.route('**/api/trash/restore', route => {
    const {path} = route.request().postDataJSON(); writes.push('restore');
    runs.push(originalRuns.find(r => `workbench-run/${r.job_id}` === path)!);
    trash.splice(trash.findIndex(t => t.path === path), 1); return route.fulfill({json: {restored: true}});
  });
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  await expect(page.getByRole('button', {name: '移到回收站', exact: true})).toHaveCount(0);
  await page.getByRole('checkbox', {name: '全选当前列表', exact: true}).check();
  await page.getByRole('textbox', {name: '搜索经验'}).fill('经验一');
  await expect(page.getByText('已选 1 项', {exact: true})).toBeVisible();
  await page.getByRole('textbox', {name: '搜索经验'}).fill('');
  await page.getByRole('checkbox', {name: '全选当前列表', exact: true}).check();
  await page.getByRole('button', {name: '批量移到回收站', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click(); expect(writes).toEqual([]);
  await page.getByRole('button', {name: '批量移到回收站', exact: true}).click(); await acceptConfirmation(page, '批量移到回收站');
  await expect(page.getByText('已移到回收站 2 项。可在全局回收站恢复。', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.getByRole('checkbox', {name: '全选当前列表', exact: true}).check();
  await page.screenshot({path: info.outputPath('bulk-history.png'), animations: 'disabled'});
  await page.getByRole('button', {name: '批量移到回收站', exact: true}).click(); await acceptConfirmation(page, '批量移到回收站');
  await expect(page.getByText(/已移到回收站 1 项，失败 1 项/)).toBeVisible();
  await expect(page.getByRole('checkbox', {name: '选择 运行二', exact: true})).toBeChecked();
  failSecond = false;
  await page.getByRole('button', {name: '批量移到回收站', exact: true}).click(); await acceptConfirmation(page, '批量移到回收站');
  await expect(page.getByRole('checkbox', {name: '选择 运行二', exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: '回收站', exact: true}).click();
  await expect(page.getByRole('heading', {name: '全局回收站', exact: true})).toBeVisible();
  const runCard = page.locator('article').filter({has: page.getByRole('heading', {name: '运行一', exact: true})});
  await runCard.getByRole('button', {name: '恢复', exact: true}).click(); await acceptConfirmation(page, '确认恢复');
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await expect(page.getByRole('heading', {name: '运行一', exact: true})).toBeVisible();
  await page.getByRole('navigation', {name: '主导航'}).getByRole('button', {name: '录制示范', exact: true}).click();
  await expect(page.getByRole('checkbox', {name: '选择 录制批量测试', exact: true})).toBeVisible();
  expect(writes).toEqual(['bulk-0', 'bulk-1', 'run-0', 'run-1', 'run-1', 'restore']);
});

test('Agent compiles B as a separate Markdown version, with disclosure, duplicate gate and explicit use', async ({page}, info) => {
  const record = {task_id: '保存会议纪要示范', trace_path: 'runs/procedure-demo/events.jsonl', local_path: 'runs/procedure-demo',
    recording_backend: 'opencua', success: true, input_events: 16, compilation_supported: false, derivation: {status: 'completed'}};
  const procedure = '# 保存会议纪要\n将会议正文保存到指定文件。\n\n输入：{正文}、{文件名}，由当前任务提供。\n\n## 步骤\n1. 在编辑器输入 {正文}。\n2. 选择保存，填写 {文件名} 并确认。\n3. 核对目标文件名；截图不能证明落盘结果，执行时还需检查文件。\n';
  const trace = {id: 'procedure-version-1', trace_name: '保存会议纪要', representation: 'B', description: '将会议正文保存到指定文件。',
    created_at: '2026-10-08T10:00:00+00:00', created_at_source: 'metadata'};
  const traces: object[] = []; const requests: any[] = []; const writes: string[] = [];
  let run: any = null; let finished = false;
  const id = 'b'.repeat(32);
  const snapshot = () => run && {...run, status: finished ? 'completed' : 'running',
    ...(finished ? {result: {representation: 'B', compiled_trace_id: trace.id}} : {})};
  page.on('request', r => {if (r.method() === 'POST') writes.push(new URL(r.url()).pathname);});
  await page.route('**/api/state?compact=1', async route => {const state = await (await route.fetch()).json();
    state.agent_options.compiler_defaults = {model: 'gpt-6.1-sol', reasoning_effort: 'high'};
    await route.fulfill({json: {...state, recordings: [record], trace_representations: traces}});});
  await page.route('**/api/traces/generate-b', route => {
    const body = route.request().postDataJSON(); requests.push(body);
    expect(body).toMatchObject({trace_path: record.trace_path, model: 'gpt-6.1-sol', reasoning_effort: 'high'});
    if (traces.length) return route.fulfill({json: {confirmation_required: true, representation: 'B',
      existing_versions: traces, message: '已有方法 B；继续将生成新版本，已有版本保留。'}});
    run = {job_id: id, kind: 'compilation', instruction: `Agent 编译 Procedure B：${record.task_id}`, model: body.model,
      provider: 'codex', status: 'running', created_at: new Date().toISOString()};
    return route.fulfill({status: 202, json: snapshot()});
  });
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: run ? [snapshot()] : []}}));
  await page.route(`**/api/workbench/runs/${id}?*`, route => route.fulfill({json: {session: snapshot(), events: [], cursor: 0, has_more: false}}));
  await page.route('**/api/traces/model-input', route => route.fulfill({json: {content: procedure, format: 'procedure-markdown-v1', images: [],
    compiler: {model: 'gpt-6.1-sol', reasoning_effort: 'high', prompt_version: 'procedure-markdown-2026-10-08',
      instructions: '固定提示词：仅输出最终 Procedure。', input: '历史示范动作，非当前任务。', images: [], image_selection: {rule: '最多 8 张历史截图，包含首尾。'}}}}));
  await page.route('**/api/traces/body', route => route.fulfill({json: {representation: 'B', runtime_images: []}}));
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('tab', {name: /原始录制/}).click();
  await page.getByRole('button', {name: 'Agent 编译方法 B', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('最多 8 张选中历史截图');
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  expect(requests).toHaveLength(0);
  await page.getByRole('button', {name: 'Agent 编译方法 B', exact: true}).click();
  await acceptConfirmation(page, '开始 Agent 编译');
  expect(requests).toHaveLength(1);
  await expect(page.getByRole('heading', {name: run.instruction, exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: /停止任务/})).toBeVisible();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('@auto');
  traces.push(trace); finished = true;
  await expect(page.getByRole('button', {name: '到经验库查看 Procedure B'})).toBeVisible();
  await page.getByRole('button', {name: '到经验库查看 Procedure B'}).click();
  await expect(page.getByText('Agent 方法 B · 执行时仅发送下方完整 Markdown Procedure')).toBeVisible();
  expect(await page.locator('.detail-workspace .experience-preview').first().textContent()).toBe(procedure);
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('@auto');
  await page.screenshot({path: info.outputPath('agent-procedure-b.png'), animations: 'disabled'});
  await page.getByText('Agent 编译输入与来源（不发送给执行模型）', {exact: true}).click();
  await expect(page.getByText('固定提示词：仅输出最终 Procedure。', {exact: true})).toBeVisible();
  await page.getByRole('tab', {name: /原始录制/}).click();
  await page.getByRole('button', {name: 'Agent 编译方法 B', exact: true}).click();
  await page.getByRole('dialog', {name: '通过 Agent 编译方法 B？'}).getByRole('button', {name: '开始 Agent 编译'}).click();
  await expect(page.getByRole('dialog', {name: '重新生成Agent 方法 B？'})).toBeVisible();
  await expect(page.getByRole('dialog')).toContainText('保存会议纪要');
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  expect(requests).toHaveLength(2); expect(traces).toHaveLength(1);
  await page.getByRole('tab', {name: /经验版本/}).click();
  await page.locator('article').filter({hasText: '可执行参考 · Agent 方法 B'}).getByRole('button', {name: '用于新任务'}).click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue(`trace-library/${trace.id}/model-input.txt`);
  await expect(page.getByLabel('参考经验', {exact: true}).locator('option:checked')).toContainText(' · B · ');
  expect(writes.every(path => ['/api/traces/generate-b', '/api/traces/model-input', '/api/traces/body'].includes(path))).toBe(true);
});

test('service card hides D and refreshes only on opening or explicit request', async ({page}, info) => {
  let services: any[] = [
    {id: 'gui', label: '本地 GUI 服务', endpoint: 'http://127.0.0.1:8768', reachable: true,
      status: 'running', model: 'gui-owl-2b', backend: 'llama-server', pid: 29064},
    {id: 'trained_d', label: 'D-5970 预览服务', endpoint: 'http://127.0.0.1:8767', reachable: true,
      status: 'running', model: 'trained_d', backend: 'frozen-checkpoint'},
  ];
  let checks = 0; let unavailable = false; const writes: string[] = [];
  page.on('request', r => {if (r.method() === 'POST') writes.push(r.url());});
  await page.route('**/api/local-model/status', route => {
    checks++;
    return route.fulfill(unavailable ? {status: 503, json: {error: 'test status unavailable'}}
      : {json: {services, checked_at: '2026-10-08T09:00:00Z'}});
  });
  await page.goto('/');
  await expect(page.locator('.local-runtime-pill')).toContainText('GUI-Owl');
  await expect(page.locator('.local-runtime-pill')).not.toContainText('D-5970');
  await page.locator('.local-runtime-pill').click();
  const panel = page.getByRole('region', {name: '实际后台服务'});
  await expect(panel.locator('.local-service-row.running')).toHaveCount(1);
  await expect(panel.getByText(/D-5970/)).toHaveCount(0);
  await expect(panel.getByText(/PID 29064/)).not.toBeVisible();
  await panel.getByText('连接详情与诊断', {exact: true}).click();
  await expect(panel.getByText(/PID 29064/)).toBeVisible();
  await panel.getByText('连接详情与诊断', {exact: true}).click();
  const original = page.locator('.model-card').filter({has: page.locator('.running-model-tag')});
  await expect(original).toHaveCount(1);
  const other = page.locator('.model-card').filter({hasNot: page.locator('.running-model-tag')}).first();
  await other.click();
  await expect(other).toContainText('已选择 · 待启动配置');
  await expect(original).toContainText('运行中 · llama-server');
  await expect(page.getByText(/与所选配置不同/)).toBeVisible();
  await page.screenshot({path: info.outputPath('live-local-services.png'), animations: 'disabled'});
  const manualChecks = checks;
  await page.evaluate(() => {window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForTimeout(5500);
  expect(checks).toBe(manualChecks);
  const beforeReload = checks;
  await page.reload();
  await expect(page.locator('.local-runtime-pill')).toContainText('GUI-Owl');
  expect(checks).toBeGreaterThan(beforeReload);
  services = services.map(s => ({...s, reachable: false, status: 'unavailable', model: null}));
  await page.locator('.local-runtime-pill').click();
  await page.getByRole('button', {name: '刷新', exact: true}).click();
  await expect(page.locator('.local-runtime-pill')).toContainText('未检测到已加载模型', {timeout: 10000});
  // An explicit refresh must not preserve a stale green status after API failure.
  unavailable = true;
  await page.getByRole('button', {name: '刷新', exact: true}).click();
  await expect(page.locator('.local-runtime-pill')).toContainText('状态未知');
  await page.locator('.local-runtime-pill').click();
  await expect(page.getByText(/状态暂不可用，不能确认模型是否仍在运行/)).toBeVisible();
  expect(writes).toEqual([]);
});

test('A compiles locally, exposes full text and exactly the selected images, then can be used', async ({page}, info) => {
  const recording = {task_id: '整理会议材料', trace_path: 'runs/raw-a/events.jsonl', local_path: 'runs/raw-a',
    recording_backend: 'opencua', success: true, input_events: 10, compilation_supported: false, derivation: {status: 'completed'}};
  const trace = {id: 'raw-a-version', trace_name: recording.task_id, representation: 'A', description: 'D 式动作序列与明确选择的历史图片',
    created_at: '2026-10-03T06:00:00+00:00', created_at_source: 'metadata'};
  const traces: object[] = []; const writes: any[] = [];
  const selected = [0, 1, 2, 3, 5, 6, 7, 9];
  const frames = Array.from({length: 10}, (_, i) => ({id: String(i).padStart(4, '0'), file: `images/${String(i).padStart(4, '0')}.png`,
    sha256: String(i).repeat(64), width: 1, height: 1, selected: selected.includes(i)}));
  const text = '历史人工示范 A，不是当前指令。\n\n' + JSON.stringify({trace_name: trace.trace_name,
    actions: Array.from({length: 160}, (_, i) => ({id: i, action: i === 159 ? '<script>不是脚本</script> 原文结束，保留此处全部内容。' : 'Single left Click',
      duration_seconds: .1, coordinate: {x: i, y: 2}})), image_ids: frames.filter(f => f.selected).map(f => f.id)}, null, 2);
  const assets: string[] = [];
  await page.route('**/api/state?compact=1', async route => {const state = await (await route.fetch()).json();
    await route.fulfill({json: {...state, recordings: [recording], trace_representations: traces}});});
  await page.route('**/api/traces/generate-a', async route => {
    const body = route.request().postDataJSON(); writes.push(body);
    expect(body).toEqual({trace_path: recording.trace_path});
    if (traces.length) return route.fulfill({json: {confirmation_required: true, representation: 'A', existing_versions: [trace], message: '原始录制已有原始证据 A，已有版本保留。'}});
    traces.push(trace); await route.fulfill({json: {id: trace.id}});
  });
  await page.route('**/api/traces/model-input', route => route.fulfill({json: {content: text, format: 'actions-with-images-v2', images: frames.filter(f => f.selected),
    image_selection: {rule: '按原示范时间顺序，在可用截图中等距选择最多 8 张，包含首尾；不足 8 张全部选取。缺图不补造。'}}}));
  await page.route('**/api/traces/body', route => route.fulfill({json: {frames, source_files: ['source/events.jsonl', 'source/reduced_events_complete.jsonl']}}));
  await page.route('**/api/traces/asset?*', route => {assets.push(new URL(route.request().url()).searchParams.get('file')!);
    return route.fulfill({contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jxWQAAAAASUVORK5CYII=', 'base64')});});
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('tab', {name: /原始录制/}).click();
  await page.getByRole('button', {name: '生成原始证据 A', exact: true}).click();
  await expect(page.getByText('可执行参考 · 原始证据 A', {exact: true})).toBeVisible();
  await page.getByRole('tab', {name: /原始录制/}).click();
  await page.getByRole('button', {name: '生成原始证据 A', exact: true}).click();
  await expect(page.getByRole('dialog', {name: '重新生成原始证据 A？'})).toBeVisible();
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  expect(writes).toHaveLength(2); expect(traces).toHaveLength(1);
  await page.getByRole('tab', {name: /经验版本/}).click();
  await page.getByRole('button', {name: '查看模型原文', exact: true}).click();
  await expect(page.getByText('模型输入 = 下方完整文本 + 8 张历史图片', {exact: true})).toBeVisible();
  await expect(page.getByText('文本以 D 式动作序列为主，只引用选中的历史截图。原始事件、文件路径和校验信息仅供审计，不塞入模型文本。', {exact: true})).toBeVisible();
  expect(await page.locator('.experience-preview').textContent()).toBe(text);
  await expect(page.locator('.experience-preview')).not.toContainText('sha256');
  await expect(page.locator('.experience-preview')).not.toContainText('source/events.jsonl');
  await expect(page.locator('.evidence-gallery img')).toHaveCount(8);
  const pictureFiles = await page.locator('.evidence-gallery img').evaluateAll(images => images.map(img => new URL((img as HTMLImageElement).src).searchParams.get('file')));
  expect(pictureFiles).toEqual(frames.filter(f => f.selected).map(f => f.file));
  await page.getByText('实际发送的历史图片 · 8 张', {exact: true}).scrollIntoViewIfNeeded();
  await page.screenshot({path: info.outputPath('a-evidence-review.png'), animations: 'disabled'});
  await page.getByText('完整证据归档与未发送图片（不额外发送给模型）', {exact: true}).click();
  await expect(page.getByRole('link', {name: 'source/events.jsonl', exact: true})).toBeVisible();
  await expect(page.getByText('审计元数据与完整索引（不额外发送给模型）', {exact: true})).toBeVisible();
  await expect(page.locator('.evidence-index li')).toHaveCount(10);
  await expect(page.locator('.evidence-index li').filter({hasText: '仅归档，未发送'})).toHaveCount(2);
  expect(assets).not.toContain('images/0004.png'); expect(assets).not.toContain('images/0008.png');
  await page.route('**/api/traces/model-input', route => route.fulfill({json: {content: '旧版原始日志全文', format: 'raw-evidence-v1', images: []}}));
  await page.getByRole('button', {name: '查看模型原文', exact: true}).click();
  await expect(page.getByText('这是旧版 A，模型文本包含完整原始日志与截图索引。可回到原始录制重新生成精简格式的 A；此版本不会被自动改写。', {exact: true})).toBeVisible();
  expect(await page.locator('.experience-preview').textContent()).toBe('旧版原始日志全文');
  await page.locator('.detail-workspace').getByRole('button', {name: '用于新任务', exact: true}).click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue(`trace-library/${trace.id}/model-input.txt`);
  await expect(page.getByLabel('参考经验', {exact: true}).locator('option:checked')).toContainText(' · A · ');
});

test.describe('compiled versions', () => {
  test.use({timezoneId: 'Asia/Shanghai'});
  test('same-named versions display dates and duplicate generation can be cancelled or confirmed', async ({page}, info) => {
    const traces = [
      {id: 'new-version', trace_name: '通关植物大战僵尸', description: '动作序列', representation: 'D',
        created_at: '2026-10-02T09:00:00.123000+00:00', created_at_source: 'metadata'},
      {id: 'old-version', trace_name: '通关植物大战僵尸', description: '动作序列', representation: 'D',
        created_at: '2026-09-30T09:00:00.000000+00:00', created_at_source: 'file_mtime'},
    ];
    const record = {task_id: traces[0].trace_name, trace_path: 'runs/same-source/events.jsonl', local_path: 'runs/same-source',
      recording_backend: 'opencua', success: true, input_events: 2, compilation_supported: false, derivation: {status: 'completed'}};
    const requests: any[] = []; let generated = 0;
    await page.route('**/api/state?compact=1', async route => {const state = await (await route.fetch()).json();
      await route.fulfill({json: {...state, recordings: [record], trace_representations: traces}});});
    await page.route('**/api/traces/generate-d', async route => {
      const body = route.request().postDataJSON(); requests.push(body);
      expect(body.trace_path).toBe(record.trace_path);
      if (body.confirm_duplicate !== true) return route.fulfill({json: {confirmation_required: true,
        representation: 'D', message: '同一原始录制已有精简序列 D。继续将生成新版本，已有版本保留。', existing_versions: traces}});
      generated++;
      traces.unshift({...traces[0], id: 'confirmed-new-version', created_at: '2026-10-03T09:00:00.456000+00:00'});
      await route.fulfill({json: {id: 'confirmed-new-version'}});
    });
    await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
    await expect(page.getByRole('heading', {name: record.task_id, exact: true})).toHaveCount(2);
    await expect(page.locator('.version-time').filter({hasText: '生成时间：2026/10/02 17:00:00.123'})).toBeVisible();
    await expect(page.locator('.version-time').filter({hasText: '生成时间（旧版文件时间）：2026/09/30 17:00:00.000'})).toBeVisible();
    await page.screenshot({path: info.outputPath('version-timestamps.png'), animations: 'disabled'});
    await page.getByRole('button', {name: '任务工作台', exact: true}).click();
    const options = await page.getByLabel('参考经验', {exact: true}).locator('option').allTextContents();
    expect(options.some(label => label.includes('2026/10/02 17:00:00.123'))).toBe(true);
    expect(options.some(label => label.includes('2026/09/30 17:00:00.000'))).toBe(true);
    expect(new Set(options).size).toBe(options.length);
    await page.getByRole('button', {name: '经验库', exact: true}).click();
    await page.getByRole('tab', {name: /原始录制/}).click();
    await page.getByRole('button', {name: '生成精简序列 D', exact: true}).click();
    const dialog = page.getByRole('dialog', {name: '重新生成精简序列 D？'});
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('.confirmation-versions li')).toHaveCount(2);
    await expect(dialog).toContainText('2026/10/02 17:00:00.123');
    await expect(dialog.getByRole('button', {name: '取消', exact: true})).toBeFocused();
    await page.screenshot({path: info.outputPath('duplicate-dialog.png'), animations: 'disabled'});
    await page.setViewportSize({width: 960, height: 640});
    const box = (await dialog.boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(0); expect(box.y + box.height).toBeLessThanOrEqual(640);
    await page.screenshot({path: info.outputPath('duplicate-dialog-compact.png'), animations: 'disabled'});
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(page.getByText('已取消重复编译，未生成新版本。', {exact: true})).toBeVisible();
    expect(requests).toHaveLength(1); expect(generated).toBe(0); expect(traces).toHaveLength(2);
    await page.setViewportSize({width: 1366, height: 768});
    await page.getByRole('button', {name: '生成精简序列 D', exact: true}).click();
    await acceptConfirmation(page, '生成新版本');
    await expect(page.getByRole('heading', {name: record.task_id, exact: true})).toHaveCount(3);
    await expect(page.getByText('新版本已生成；已有版本保留，未自动切换当前任务经验。', {exact: true})).toBeVisible();
    expect(requests.map(body => body.confirm_duplicate)).toEqual([undefined, undefined, true]);
    expect(generated).toBe(1);
    await page.getByRole('button', {name: '任务工作台', exact: true}).click();
    await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('@auto');
  });
});

test('trash removes active assets, restores safely and requires permanent-delete confirmation', async ({page}, info) => {
  const trace = {id: 'trash-D', trace_name: '待移除序列', description: '测试序列', representation: 'D'};
  const recording = {task_id: '待移除录制', trace_path: 'runs/demo/events.jsonl', local_path: 'runs/demo', success: true,
    input_events: 2, recording_backend: 'opencua', compilation_supported: false, derivation: {status: 'completed'}};
  let traces = [trace], recordings = [recording];
  const trash: TrashItem[] = []; const writes: {url: string; body: any}[] = [];
  const history = {job_id: 'retained-history', kind: 'recording', task_id: '保留的录制审计', status: 'completed', created_at: '2026-10-03'};
  await page.route('**/api/state?compact=1', async route => {const state = await (await route.fetch()).json();
    await route.fulfill({json: {...state, recordings, trace_representations: traces, trash}});});
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [history]}}));
  for (const endpoint of ['/api/traces/delete', '/api/recordings/delete', '/api/trash/restore', '/api/trash/delete']) {
    await page.route(`**${endpoint}`, async route => {
      const body = route.request().postDataJSON(); writes.push({url: endpoint, body});
      if (endpoint === '/api/traces/delete') {
        expect(body).toEqual({id: trace.id}); traces = [];
        trash.push({path: 'trace-library/.trash/example-D', name: trace.trace_name, kind: 'trace_experience',
          original_path: `trace-library/${trace.id}`, can_restore: true, can_delete: true});
      } else if (endpoint === '/api/recordings/delete') {
        expect(body).toEqual({trace_path: recording.trace_path}); recordings = [];
        trash.push({path: 'runs/.trash/recordings/example-R', name: recording.task_id, kind: 'recording',
          original_path: 'runs/demo', can_restore: true, can_delete: true});
      } else {
        const index = trash.findIndex(t => t.path === body.path); expect(index).toBeGreaterThanOrEqual(0);
        if (endpoint === '/api/trash/restore') {
          expect(body.confirmed).toBeUndefined();
          if (trash[index].kind === 'trace_experience') traces = [trace]; else recordings = [recording];
        } else expect(body.confirmed).toBe(true);
        trash.splice(index, 1);
      }
      await route.fulfill({json: {pending_cleanup: false}});
    });
  }
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('button', {name: '用于新任务', exact: true}).click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('trace-library/trash-D/model-input.txt');
  await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('checkbox', {name: `选择 ${trace.trace_name}`, exact: true}).check();
  await page.getByRole('button', {name: '批量移到回收站', exact: true}).click(); await acceptConfirmation(page, '批量移到回收站');
  await expect(page.getByRole('heading', {name: trace.trace_name, exact: true})).toHaveCount(0);
  await expect(page.getByText(/已移到回收站 1 项/)).toBeVisible();
  await page.getByRole('button', {name: '任务工作台', exact: true}).click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('');
  await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('button', {name: '回收站', exact: true}).click();
  await expect(page.getByRole('heading', {name: trace.trace_name, exact: true})).toBeVisible();
  await page.getByRole('button', {name: '恢复', exact: true}).click(); await acceptConfirmation(page, '确认恢复');
  await expect(page.getByText(/未自动启用为当前任务经验/)).toBeVisible();
  await page.getByRole('button', {name: '任务工作台', exact: true}).click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('');
  await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('tab', {name: /经验版本/}).click();
  await expect(page.getByRole('heading', {name: trace.trace_name, exact: true})).toBeVisible();
  await page.getByRole('tab', {name: /原始录制/}).click();
  await page.getByRole('checkbox', {name: `选择 ${recording.task_id}`, exact: true}).check();
  await page.getByRole('button', {name: '批量移到回收站', exact: true}).click(); await acceptConfirmation(page, '批量移到回收站');
  await expect(page.getByRole('heading', {name: recording.task_id, exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: '回收站', exact: true}).click();
  const before = writes.length;
  await page.getByRole('button', {name: '永久删除', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveClass(/confirmation-danger/);
  await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  expect(writes).toHaveLength(before);
  await expect(page.getByRole('heading', {name: recording.task_id, exact: true})).toBeVisible();
  await page.screenshot({path: info.outputPath('trash.png'), animations: 'disabled'});
  await page.getByRole('button', {name: '永久删除', exact: true}).click(); await acceptConfirmation(page, '永久删除');
  await expect(page.getByText(/无法从本回收站恢复/)).toBeVisible();
  await expect(page.getByRole('heading', {name: '回收站是空的', exact: true})).toBeVisible();
  expect(writes.map(w => w.url)).toEqual(['/api/traces/delete', '/api/trash/restore', '/api/recordings/delete', '/api/trash/delete']);
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await expect(page.getByRole('heading', {name: history.task_id, level: 3, exact: true})).toBeVisible();
  trash.push({path: 'runs/.trash/recordings/pending', name: '占用中的安全副本', kind: 'recording',
    pending_cleanup: true, can_restore: true, can_delete: false},
    {path: 'trace-library/.trash/conflict', name: '同名冲突', kind: 'trace_experience',
      can_restore: false, restore_blocked_reason: '原位置已有内容', can_delete: true});
  await page.getByRole('button', {name: '回收站', exact: true}).click();
  await expect(page.locator('article').filter({hasText: '占用中的安全副本'}).getByRole('button', {name: '永久删除', exact: true})).toBeDisabled();
  await expect(page.locator('article').filter({hasText: '同名冲突'}).getByRole('button', {name: '恢复', exact: true})).toBeDisabled();
});

test('Qwen 8B uses the shared llama model service and local task contract', async ({page}) => {
  let serviceStarted = false, started = false;
  await page.route('**/api/local-model/status', route => route.fulfill({json: {
    checked_at: new Date().toISOString(), services: [{id: 'gui', label: '本地 GUI 服务', endpoint: 'http://127.0.0.1:8768',
      reachable: serviceStarted, status: serviceStarted ? 'running' : 'unavailable', backend: 'llama-server', model: serviceStarted ? 'qwen3-vl-8b-instruct' : null}],
  }}));
  const id = '8'.repeat(32);
  const run = {job_id: id, kind: 'agent', model: 'qwen3-vl-8b-instruct', provider: 'trained_d',
    instruction: '仅验证 8B 路由', status: 'running', created_at: new Date().toISOString()};
  await page.route('**/api/runtime-catalog', async route => {
    const catalog = await (await route.fetch()).json();
    // Exercise engine selection from a different previous setting. No real service is started.
    catalog.configured_backend = 'transformers';
    catalog.service = {reachable: serviceStarted, backend: 'llama-server', model: 'qwen3-vl-8b-instruct'};
    catalog.models.find((p: {id: string}) => p.id === 'qwen3-vl-8b-instruct').availability['llama-server'] = {files_present: true, missing: []};
    await route.fulfill({json: catalog});
  });
  await page.route('**/api/local-model/service', async route => {
    expect(route.request().postDataJSON()).toEqual({action: 'start', model: 'qwen3-vl-8b-instruct', backend: 'llama-server'});
    serviceStarted = true; await route.fulfill({json: {message: '隔离测试：统一服务已就绪'}});
  });
  await page.route('**/api/jobs', async route => {
    const body = route.request().postDataJSON();
    expect(body).toMatchObject({provider: 'trained_d', model: 'qwen3-vl-8b-instruct', inference_backend: 'llama-server',
      executor_backend: 'cua', continuous: true, mode: 'execute'});
    expect(body.api).toBeUndefined(); started = true; await route.fulfill({json: run});
  });
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: started ? [run] : []}}));
  await page.route(`**/api/workbench/runs/${id}?*`, route => route.fulfill({json: {session: run, events: [], cursor: 0, has_more: false}}));
  await page.goto('/');
  await page.getByRole('button', {name: '模型与连接', exact: true}).click();
  await page.getByRole('button', {name: /Qwen3-VL · 8B/}).click();
  await expect(page.getByLabel('推理引擎', {exact: true})).toHaveValue('llama-server');
  await expect(page.getByLabel('推理引擎', {exact: true}).locator('option')).toHaveCount(1);
  await page.getByRole('button', {name: '启动所选服务', exact: true}).click();
  await expect(page.getByText('隔离测试：统一服务已就绪')).toBeVisible();
  await page.getByRole('button', {name: '任务工作台', exact: true}).click();
  await expect(page.getByLabel('执行模型', {exact: true})).toHaveValue('qwen3-vl-8b-instruct');
  await expect(page.getByLabel('执行模型', {exact: true}).locator('option[value="qwen3-vl-8b-instruct"]')).toHaveCount(1);
  await expect(page.locator('.context-strip')).toContainText('32K');
  await page.getByRole('textbox', {name: '任务指令'}).fill('仅验证 8B 路由');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  expect(started).toBe(false);
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect(page.getByRole('button', {name: /停止任务/})).toBeVisible();
  expect(started).toBe(true);
});

test('first screen, workflow navigation and compact layouts', async ({page, browser}, info) => {
  const errors: string[] = [];
  const oldPages: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => {if (/\/(legacy|app\.js|components\.js|workbench-legacy\.css)(\?|$)/.test(r.url())) oldPages.push(r.url());});
  await page.goto('/');
  const instruction = page.getByRole('textbox', {name: '任务指令'});
  await expect(instruction).toBeVisible();
  const start = page.getByRole('button', {name: '开始任务', exact: true});
  await instruction.fill('只在临时记事本中输入验证文本');
  await expect(start).toBeEnabled();
  expect((await start.boundingBox())!.y + (await start.boundingBox())!.height).toBeLessThan(768);
  await page.screenshot({path: info.outputPath('workbench-1366.png'), animations: 'disabled'});
  await page.getByRole('button', {name: '模型与连接', exact: true}).click();
  await expect(page.getByRole('heading', {name: '选模型，也选对运行方式。'})).toBeVisible();
  await expect(page.getByRole('button', {name: /Qwen3-VL · 2B/})).toBeVisible();
  await page.screenshot({path: info.outputPath('models.png'), animations: 'disabled'});
  await page.getByRole('navigation', {name: '主导航'}).getByRole('button', {name: '录制示范', exact: true}).click();
  await expect(page.getByLabel('经验名称', {exact: true})).toBeVisible();
  await page.getByLabel('经验名称', {exact: true}).fill('切换页面保留的录制草稿');
  await page.screenshot({path: info.outputPath('recording.png'), animations: 'disabled'});
  await page.getByRole('button', {name: '设置与工具', exact: true}).click();
  await page.getByRole('tab', {name: '模型提示词', exact: true}).click();
  const guidance = page.getByLabel('自定义执行指导', {exact: true});
  await expect(guidance).toBeEnabled(); await guidance.fill('未保存的提示词草稿');
  await page.getByRole('tab', {name: '组件与模型文件', exact: true}).click();
  await expect(page.getByLabel('组件安装目录', {exact: true})).toBeEnabled();
  await page.screenshot({path: info.outputPath('components.png'), animations: 'disabled'});
  await page.getByRole('tab', {name: '任务恢复', exact: true}).click();
  await expect(page.getByRole('heading', {name: '显式恢复未完成任务'})).toBeVisible();
  await page.getByRole('button', {name: '实验室', exact: true}).click();
  await expect(page.getByRole('heading', {name: '有界自主练习'})).toBeVisible();
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await expect(page.getByRole('heading', {name: '每一步，都可以回看。'})).toBeVisible();
  await page.getByRole('button', {name: '经验库', exact: true}).click();
  await expect(page.getByRole('heading', {name: '经验积累下来，下一次更从容。'})).toBeVisible();
  for (const name of ['原始录制', '旧版状态图', '反馈审查']) await page.getByRole('tab', {name: new RegExp(name)}).click();
  await page.getByRole('navigation', {name: '主导航'}).getByRole('button', {name: '录制示范', exact: true}).click();
  await expect(page.getByLabel('经验名称', {exact: true})).toHaveValue('切换页面保留的录制草稿');
  await page.getByRole('button', {name: '设置与工具', exact: true}).click();
  await page.getByRole('tab', {name: '模型提示词', exact: true}).click();
  await expect(guidance).toHaveValue('未保存的提示词草稿');
  await page.getByRole('button', {name: '任务工作台', exact: true}).click();
  await expect(instruction).toHaveValue('只在临时记事本中输入验证文本');
  await start.click();
  await expect(page.getByRole('alert')).toContainText('提示词有未保存修改');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', {name: '关闭', exact: true}).click();
  await page.setViewportSize({width: 960, height: 640});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: info.outputPath('workbench-960.png')});
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => document.activeElement !== document.body)).toBe(true);
  for (const scale of [1.25, 1.5]) {
    const context = await browser.newContext({viewport: {width: 960, height: 640}, deviceScaleFactor: scale});
    const compact = await context.newPage(); await compact.goto('http://127.0.0.1:8775/');
    await expect(compact.getByRole('textbox', {name: '任务指令'})).toBeVisible();
    expect(await compact.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await context.close();
  }
  expect(errors).toEqual([]);
  expect(oldPages).toEqual([]);
  await expect(page.locator('iframe')).toHaveCount(0);
});

test('native recording and library use existing contracts without an old page', async ({page}) => {
  const recording = {task_id: '示范 A', trace_path: 'runs/demo/events.jsonl', local_path: 'runs/demo', success: true,
    input_events: 3, recording_backend: 'opencua', compilation_supported: false, derivation: {status: 'completed', action_count: 2}};
  const traces: object[] = []; const writes: {url: string; body: any}[] = [];
  const candidate = {candidate_id: 'feedback-1', local_path: 'runs/feedback-1', task_id: '候选 A', status: 'pending_review',
    revision: {status: 'draft', summary: '保留原始证据', rules: []}, task_model_revision: {status: 'draft', proposed_revision: 2, blocking_issue_count: 1}};
  await page.route('**/api/state?compact=1', async route => {const state = await (await route.fetch()).json();
    await route.fulfill({json: {...state, recordings: [recording], trace_representations: traces, candidates: [candidate]}});});
  await page.route('**/api/traces/generate-d', async route => {writes.push({url: route.request().url(), body: route.request().postDataJSON()});
    traces.push({id: 'trace-1', trace_name: '示范 A · D', description: '本地序列', representation: 'D'}); await route.fulfill({json: {id: 'trace-1'}});});
  await page.route('**/api/traces/model-input', route => route.fulfill({json: {content: '保留完整原文，不重写'}}));
  await page.route('**/api/traces/body', route => route.fulfill({json: {steps: []}}));
  await page.route('**/api/candidates/revisions/summary', async route => {candidate.revision.summary = route.request().postDataJSON().summary; await route.fulfill({json: {status: 'saved'}});});
  await page.route('**/api/candidates/revisions/confirm', async route => {writes.push({url: route.request().url(), body: route.request().postDataJSON()}); candidate.revision.status = 'confirmed'; await route.fulfill({json: {status: 'confirmed'}});});
  await page.goto('/'); await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('tab', {name: /原始录制/}).click();
  await expect(page.getByRole('button', {name: '语义编译', exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Agent 编译方法 B', exact: true})).toBeEnabled();
  await page.getByRole('button', {name: '生成精简序列 D', exact: true}).click();
  await expect(page.getByRole('heading', {name: '示范 A · D', exact: true})).toBeVisible();
  expect(writes[0].body).toEqual({trace_path: 'runs/demo/events.jsonl'});
  await page.getByRole('button', {name: '查看模型原文', exact: true}).click();
  await expect(page.getByText('保留完整原文，不重写', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '用于新任务', exact: true}).first().click();
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue('trace-library/trace-1/model-input.txt');
  await page.getByRole('button', {name: '经验库', exact: true}).click();
  await page.getByRole('tab', {name: /反馈审查/}).click(); await page.getByRole('button', {name: '审查与反馈', exact: true}).click();
  await expect(page.getByRole('button', {name: '确认任务图版本', exact: true})).toBeDisabled();
  await page.getByLabel('修订摘要', {exact: true}).fill('人工核对后的摘要');
  await expect(page.getByRole('button', {name: '确认反馈版本', exact: true})).toBeDisabled();
  await page.getByRole('button', {name: '保存摘要', exact: true}).click(); await acceptConfirmation(page, '确认修改');
  await expect(page.getByRole('button', {name: '确认反馈版本', exact: true})).toBeEnabled();
  await page.getByRole('button', {name: '确认反馈版本', exact: true}).click(); await acceptConfirmation(page, '确认修改');
  await expect.poll(() => writes.length).toBe(2); expect(writes[1].body).toEqual({path: 'runs/feedback-1'});
  const id = 'c'.repeat(32); let run: any = null;
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: run ? [run] : []}}));
  await page.route(`**/api/workbench/runs/${id}?*`, route => route.fulfill({json: {session: run, events: [], cursor: 0, has_more: false}}));
  await page.route('**/api/recordings', async route => {const body = route.request().postDataJSON();
    expect(body).toMatchObject({recording_backend: 'opencua', execution_scope: 'desktop', task_id: '新的示范', narrated: false, defer_compilation: true});
    run = {job_id: id, task_id: body.task_id, kind: 'recording', status: 'running', defer_compilation: true}; await route.fulfill({json: run});});
  await page.getByRole('navigation', {name: '主导航'}).getByRole('button', {name: '录制示范', exact: true}).click();
  await page.getByLabel('经验名称', {exact: true}).fill('新的示范');
  await page.getByRole('button', {name: '开始录制', exact: true}).click();
  await acceptConfirmation(page, '确认开始录制');
  await expect(page.getByRole('button', {name: /停止任务/})).toBeVisible();
  await expect(page.locator('iframe')).toHaveCount(0);
});

test('one task confirmation, explicit driver scope and stop from every page', async ({page}) => {
  const id = 'a'.repeat(32);
  const run = {job_id: id, task_id: 'test', instruction: '仅验证 UI', model: 'test-vision', provider: 'codex',
    kind: 'agent', status: 'running', created_at: new Date().toISOString(), stop_requested: false,
    run_facts: {model: 'test-vision', inference_backend: 'codex', execution_backend: 'cua', operation_scope: 'desktop',
      data_flow: 'remote', destination: 'Codex 订阅服务', context_description: '服务端管理', experience_path: ''}};
  let started = false, stopped = false;
  await page.route('**/api/jobs', async route => {
    const body = route.request().postDataJSON();
    expect(body.cua_target).toEqual({kind: 'desktop', display_id: 'primary'});
    expect(body.executor_backend).toBe('cua'); expect(body.continuous).toBe(true);
    started = true; await route.fulfill({json: run});
  });
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: started ? [run] : []}}));
  await page.route(`**/api/workbench/runs/${id}?*`, route => route.fulfill({json: {session: run, events: [], cursor: 0, has_more: false}}));
  await page.route(`**/api/jobs/${id}/stop`, async route => {stopped = true; await route.fulfill({json: {...run, status: 'stopping', stop_requested: true}});});
  await page.goto('/');
  await page.getByRole('textbox', {name: '任务指令'}).fill('仅验证 UI');
  await page.getByLabel('执行后端', {exact: true}).selectOption('cua');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  expect(started).toBe(false);
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect(page.getByRole('button', {name: /停止任务/})).toBeVisible();
  await page.getByRole('button', {name: '模型与连接', exact: true}).click();
  await page.getByRole('button', {name: /停止任务/}).click();
  expect(stopped).toBe(true);
});

test('incremental rounds keep disclosures open and fetch raw content only on demand', async ({page}) => {
  const id = 'b'.repeat(32); let reads = 0, polls = 0;
  const run = {job_id: id, instruction: 'UI 回执测试', model: 'fixture', status: 'running', kind: 'agent', created_at: new Date().toISOString()};
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [run]}}));
  await page.route(`**/api/workbench/runs/${id}?*`, async route => {
    polls++; const after = Number(new URL(route.request().url()).searchParams.get('after'));
    await route.fulfill({json: {session: run, cursor: after || 1, has_more: false,
      events: after ? [] : [{seq: 1, name: 'model.round', attributes: {step_index: 0, status: 'predicted', tokens: {},
        artifact_ref: `/api/workbench/runs/${id}/rounds/0`}}]}});
  });
  await page.route(`**/api/workbench/runs/${id}/rounds/0`, route => {reads++; return route.fulfill({json: {raw_output: '只读模型回答', execution: {executed: false}}});});
  await page.goto('/');
  await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.getByRole('button', {name: /UI 回执测试/}).click();
  await expect(page.locator('.round-card')).toBeVisible();
  expect(reads).toBe(0);
  await page.getByText('模型输入、输出与执行回执', {exact: true}).click();
  await expect(page.getByText('只读模型回答', {exact: true})).toBeVisible();
  await expect.poll(() => polls).toBeGreaterThan(1);
  await expect(page.locator('.round-content > details')).toHaveAttribute('open', '');
  expect(reads).toBe(1);
});

test('round input is readable before output with full text, folded history/system, images and copy', async ({page}, info) => {
  const id = 'e'.repeat(32);
  const run = {job_id: id, instruction: '逐轮输入展示测试', provider: 'api', model: 'fixture', status: 'completed', kind: 'agent', created_at: new Date().toISOString()};
  const prompt = '继续整理会议材料。\n先核对当前画面，再决定下一步。\n<script>这是输入原文，不是代码</script>\n' + '保留完整经验内容。'.repeat(1000) + '\n原文结束。';
  let reads = 0;
  await page.addInitScript(() => {Object.defineProperty(navigator, 'clipboard', {value: {writeText: async (text: string) => {(window as any).copiedInput = text;}}});});
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [run]}}));
  await page.route(`**/api/workbench/runs/${id}?*`, route => route.fulfill({json: {session: run, cursor: 1, has_more: false, events: [
    {seq: 1, name: 'model.round', attributes: {step_index: 0, status: 'format_rejected', executed: false, tokens: {input_tokens: 100085, output_tokens: 56},
      artifact_ref: `/api/workbench/runs/${id}/rounds/0`}},
  ]}}));
  await page.route(`**/api/workbench/runs/${id}/rounds/0`, route => {reads++; return route.fulfill({json: {
    input: {messages: [{role: 'system', content: '仅供展开查看的系统指令'}, {role: 'user', content: '早先发送的任务全文'},
      {role: 'assistant', content: '早先的模型回答'}, {role: 'user', content: [{type: 'text', text: prompt},
        {type: 'image', local_path: 'runs/fixture/current.png'}]}], configuration: {temperature: 0, max_new_tokens: 512}},
    raw_output: '这轮真实模型输出', execution: {executed: false},
  }});});
  await page.route('**/api/local-image?*', route => route.fulfill({contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jxWQAAAAASUVORK5CYII=', 'base64')}));
  await page.goto('/'); await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.getByRole('button', {name: /逐轮输入展示测试/}).click();
  expect(reads).toBe(0);
  await page.getByText('模型输入、输出与执行回执', {exact: true}).click();
  const input = page.getByRole('region', {name: '模型输入', exact: true});
  await expect(input).toBeVisible();
  expect(await input.locator('.model-input-text').first().textContent()).toBe(prompt);
  await expect(input.locator('.model-input-text script')).toHaveCount(0);
  await expect(input).not.toContainText('temperature');
  await expect(input.getByText('仅供展开查看的系统指令', {exact: true})).toBeHidden();
  await expect(input.getByText('早先发送的任务全文', {exact: true})).toBeHidden();
  await expect(input.getByRole('img', {name: '图片 1 · 图片附件'})).toBeVisible();
  await input.getByRole('button', {name: '复制本轮文字'}).click();
  expect(await page.evaluate(() => (window as any).copiedInput)).toBe(prompt);
  await expect(page.getByText('未发送操作输入', {exact: true})).toBeVisible();
  await expect(page.getByText('这轮真实模型输出', {exact: true})).toBeVisible();
  const order = await page.locator('.raw-round h4').allTextContents();
  expect(order).toEqual(['模型输入', '模型输出', '执行回执']);
  await input.getByText('本轮携带的历史消息 · 2 条', {exact: true}).click();
  await expect(input.getByText('早先发送的任务全文', {exact: true})).toBeVisible();
  await input.getByText('系统 / 开发者提示词 · 1 条', {exact: true}).click();
  await expect(input.getByText('仅供展开查看的系统指令', {exact: true})).toBeVisible();
  await input.getByText('本轮携带的历史消息 · 2 条', {exact: true}).click();
  await input.getByText('系统 / 开发者提示词 · 1 条', {exact: true}).click();
  await page.setViewportSize({width: 960, height: 900});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.setViewportSize({width: 1366, height: 1100});
  await input.evaluate(node => node.scrollIntoView({block: 'center'}));
  await input.screenshot({path: info.outputPath('round-model-input.png'), animations: 'disabled'});
  expect(reads).toBe(1);
});

test('round input discloses Codex context limits, missing images and unavailable input', async ({page}) => {
  const id = 'f'.repeat(32);
  const run = {job_id: id, instruction: '输入边界测试', provider: 'codex', status: 'completed', kind: 'agent', created_at: new Date().toISOString()};
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [run]}}));
  await page.route(`**/api/workbench/runs/${id}?*`, route => route.fulfill({json: {session: run, cursor: 2, has_more: false, events: [0, 1].map(step => (
    {seq: step + 1, name: 'model.round', attributes: {step_index: step, status: 'error', tokens: {}, artifact_ref: `/api/workbench/runs/${id}/rounds/${step}`}}))}}));
  await page.route(`**/api/workbench/runs/${id}/rounds/0`, route => route.fulfill({json: {input: {messages: [
    {role: 'user', content: [{type: 'text', text: '已归档的本轮文本'}, {type: 'image_url', image_url: {url: 'https://untrusted.invalid/no-load.png'}}]},
  ]}}}));
  await page.route(`**/api/workbench/runs/${id}/rounds/1`, route => route.fulfill({json: {error: '调用前失败'}}));
  const external: string[] = []; page.on('request', request => {if (request.url().includes('untrusted.invalid')) external.push(request.url());});
  await page.goto('/'); await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.getByRole('button', {name: /输入边界测试/}).click();
  await expect(page.locator('.round-card')).toHaveCount(2);
  for (const card of await page.locator('.round-card').all()) await card.getByText('模型输入、输出与执行回执', {exact: true}).click();
  const first = page.locator('.round-card').first();
  await expect(first.getByText('已归档的本轮文本', {exact: true})).toBeVisible();
  await expect(first.getByText(/不代表完整上下文/)).toBeVisible();
  await expect(first.getByText('记录包含此图片，但未保存可用的预览路径。')).toBeVisible();
  await expect(first.locator('.model-input img')).toHaveCount(0);
  await expect(page.locator('.round-card').last().getByText(/这轮尚未记录可读的模型输入/)).toBeVisible();
  expect(external).toEqual([]);
});

test('round observations expand in place without a shared sidebar or eager image/raw requests', async ({page}, info) => {
  const id = 'observation-inline'; let polls = 0, rawReads = 0;
  const images: string[] = [];
  const run = {job_id: id, instruction: '逐轮画面就地查看', provider: 'api', status: 'running', kind: 'agent', created_at: new Date().toISOString(),
    run_facts: {model: 'fixture', execution_backend: 'cua', inference_backend: 'api', operation_scope: 'desktop', destination: '隔离测试'}};
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [run]}}));
  await page.route(`**/api/workbench/runs/${id}?*`, route => {
    polls++; const after = Number(new URL(route.request().url()).searchParams.get('after'));
    const steps = after ? [7] : Array.from({length: 8}, (_, i) => i);
    return route.fulfill({json: {session: run, cursor: polls + 8, has_more: false, events: steps.map(step => ({
      seq: step + polls, name: 'model.round', attributes: {step_index: step, status: 'predicted', tokens: {},
        screenshot: `runs/fixture/round-${step}.png`, artifact_ref: `/api/workbench/runs/${id}/rounds/${step}`},
    }))}});
  });
  await page.route(`**/api/workbench/runs/${id}/rounds/*`, route => {rawReads++; return route.fulfill({json: {}});});
  await page.route('**/api/local-image?*', route => {
    const path = new URL(route.request().url()).searchParams.get('path')!; images.push(path);
    return route.fulfill({contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="430"><rect width="900" height="430" fill="#eaf1e6"/><rect x="90" y="60" width="720" height="310" rx="18" fill="white"/><text x="130" y="150" font-size="28" fill="#245943">Archived observation</text><text x="130" y="215" font-size="22" fill="#56704c">${path}</text><text x="130" y="305" font-size="18" fill="#56704c">Isolated UI fixture</text></svg>`});
  });
  await page.goto('/'); await page.getByRole('button', {name: '运行记录', exact: true}).click();
  await page.getByRole('button', {name: /逐轮画面就地查看/}).click();
  await expect(page.locator('.round-card')).toHaveCount(8);
  await expect(page.locator('.run-aside')).toHaveCount(0);
  expect(images).toEqual([]); expect(rawReads).toBe(0);
  const last = page.locator('.round-card').nth(7);
  await last.getByRole('button', {name: '查看该轮画面'}).click();
  await expect(last.getByRole('img', {name: '第 8 轮归档观察截图'})).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(500);
  await expect.poll(() => images.length).toBe(1);
  expect(images).toEqual(['runs/fixture/round-7.png']);
  const beforePoll = polls;
  await expect.poll(() => polls).toBeGreaterThan(beforePoll);
  await expect(last.getByRole('button', {name: '收起该轮画面'})).toHaveAttribute('aria-expanded', 'true');
  expect(images).toHaveLength(1); expect(rawReads).toBe(0);
  const previous = page.locator('.round-card').nth(6);
  await previous.getByRole('button', {name: '查看该轮画面'}).click();
  await expect(previous.getByRole('img')).toHaveAttribute('alt', '第 7 轮归档观察截图');
  await expect(last.getByRole('img')).toHaveAttribute('alt', '第 8 轮归档观察截图');
  await previous.getByRole('button', {name: '收起该轮画面'}).click();
  await expect(previous.locator('img')).toHaveCount(0);
  await page.setViewportSize({width: 960, height: 900});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.setViewportSize({width: 1366, height: 1100});
  await last.evaluate(node => node.scrollIntoView({block: 'center'}));
  await last.screenshot({path: info.outputPath('inline-round-observation.png'), animations: 'disabled'});
  await page.getByText('本次运行事实 · fixture · cua', {exact: true}).click();
  await expect(page.locator('.run-facts').getByText('整个主显示器', {exact: true})).toBeVisible();
});

test('WAA narration review survives navigation and submits the edited transcript', async ({page}) => {
  const id = 'd'.repeat(32); let body: any = null;
  let run: any = {job_id: id, task_id: 'WAA 讲解', kind: 'waa_recording', status: 'awaiting_narration', narrated: true,
    defer_compilation: true, result: {narration: {transcript: '已落盘的原始讲解', segments: [{text: '原始', start_ms: 0, end_ms: 200}], engine: 'faster_whisper:turbo'}}};
  await page.route('**/api/state?compact=1', async route => {const state = await (await route.fetch()).json(); await route.fulfill({json: {...state, active_job: run}});});
  await page.route('**/api/workbench/runs', route => route.fulfill({json: {runs: [run]}}));
  await page.route(`**/api/workbench/runs/${id}?*`, route => route.fulfill({json: {session: run, events: [], cursor: 0, has_more: false}}));
  await page.route(`**/api/jobs/${id}`, route => route.fulfill({json: run}));
  await page.route('**/api/recordings/narration', async route => {body = route.request().postDataJSON(); run = {...run, status: 'completed'}; await route.fulfill({json: run});});
  await page.goto('/');
  const text = page.getByLabel('讲解文字', {exact: true}); await expect(text).toHaveValue('已落盘的原始讲解');
  await text.fill('人工核对后的讲解');
  await page.getByRole('button', {name: '模型与连接', exact: true}).click();
  await page.getByRole('navigation', {name: '主导航'}).getByRole('button', {name: '录制示范', exact: true}).click();
  await expect(text).toHaveValue('人工核对后的讲解');
  await page.getByRole('button', {name: '保存讲解并继续', exact: true}).click();
  await expect.poll(() => body?.transcript).toBe('人工核对后的讲解');
  expect(body).toMatchObject({job_id: id, segments: [], transcription_engine: 'manual'});
  await expect(page.getByRole('button', {name: /停止任务/})).toHaveCount(0);
});

test('RSI review stays bound to the selected run and candidate digest', async ({page}) => {
  const rows = ['A', 'B'].map(name => ({id: name, state: 'completed', candidate_sha256: name.repeat(64), stop_requested: false,
    spec: {instruction: `练习 ${name}`, model: 'test-model', max_model_calls: 3, wall_seconds: 60, project_budget: 1}}));
  let review: any = null, resolveA!: () => void, requestedA = false;
  const deferredA = new Promise<void>(resolve => {resolveA = resolve;});
  await page.route('**/api/rsi/health', route => route.fulfill({json: {configured: true, ready: true, models: ['test-model'], reasoning_efforts: ['low'], checks: []}}));
  await page.route('**/api/rsi/runs?*', route => route.fulfill({json: {runs: rows}}));
  await page.route('**/api/rsi/events?*', route => route.fulfill({json: {events: []}}));
  await page.route('**/api/rsi/candidate?*', async route => {
    const id = new URL(route.request().url()).searchParams.get('run_id');
    if (id === 'A') {requestedA = true; await deferredA;}
    await route.fulfill({json: {candidate: {memory: {'notes.md': {text: `候选 ${id} 原文`}}, verification: {verdict: 'passed'}}}});
  });
  await page.route('**/api/rsi/review', async route => {review = route.request().postDataJSON(); await route.fulfill({json: {run: rows[1]}});});
  await page.goto('/'); await page.getByRole('button', {name: '实验室', exact: true}).click();
  await page.getByRole('button', {name: /练习 A completed/}).click();
  await page.getByRole('button', {name: '读取候选与验证证据', exact: true}).click();
  await expect.poll(() => requestedA).toBe(true);
  await page.getByRole('button', {name: /练习 B completed/}).click();
  await page.getByRole('button', {name: '读取候选与验证证据', exact: true}).click();
  await expect(page.getByText('候选 B 原文', {exact: true})).toBeVisible();
  resolveA();
  await page.getByLabel('候选审查意见', {exact: true}).fill('仅审查候选 B');
  await page.getByRole('button', {name: '归档为接受', exact: true}).click(); await acceptConfirmation(page, '确认归档');
  await expect.poll(() => review).toEqual({run_id: 'B', digest: 'B'.repeat(64), decision: 'accepted', note: '仅审查候选 B'});
  await expect(page.getByText('候选 A 原文', {exact: true})).toHaveCount(0);
});
