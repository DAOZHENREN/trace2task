import {test, expect} from '@playwright/test';

for (const motor of ['api', 'codex']) test(`API director and ${motor} executor have independent selections`, async ({page}) => {
  const jobs: any[] = [];
  await page.route('**/api/jobs', route => {
    jobs.push(route.request().postDataJSON());
    return route.fulfill({json: {job_id: 'independent', status: 'completed', kind: 'agent', provider: 'api', model: 'director'}});
  });
  await page.goto('/');
  await page.getByLabel('执行模式', {exact: true}).selectOption('hybrid');
  await page.getByLabel('指导模型来源', {exact: true}).selectOption('api');
  await page.getByLabel('指导模型', {exact: true}).fill('director-vision');
  await page.getByLabel('指导 API 地址', {exact: true}).fill('http://localhost:8811/v1');
  await page.getByLabel('指导 API 密钥', {exact: true}).fill('director-secret');
  await page.getByLabel('主要执行模型来源', {exact: true}).selectOption(motor);
  if (motor === 'api') {
    await page.getByLabel('主要执行模型', {exact: true}).fill('executor-vision');
    await page.getByLabel('执行 API 地址', {exact: true}).fill('http://localhost:8812/v1');
    await page.getByLabel('执行 API 密钥', {exact: true}).fill('executor-secret');
  } else await page.getByLabel('主要执行模型', {exact: true}).selectOption('gpt-6-luna');
  await expect(page.getByLabel('指导模型', {exact: true})).toHaveValue('director-vision');
  await page.getByLabel('参考经验', {exact: true}).selectOption('');
  await page.getByRole('textbox', {name: '任务指令'}).fill('Open menu');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect.poll(() => jobs.length).toBe(1);
  expect(jobs[0]).toMatchObject({provider: 'api', model: 'director-vision',
    api: {base_url: 'http://localhost:8811/v1', api_key: 'director-secret'},
    hybrid: {provider: motor, model: motor === 'api' ? 'executor-vision' : 'gpt-6-luna'}});
  if (motor === 'api') expect(jobs[0].hybrid.api).toMatchObject({base_url: 'http://localhost:8812/v1', api_key: 'executor-secret'});
  else expect(jobs[0].hybrid.api).toBeUndefined();
});

test('switching mode preserves the chosen model even when Terra is available', async ({page}) => {
  await page.route('**/api/state?compact=1', async route => {
    const response = await route.fetch();
    const state = await response.json();
    state.agent_options.models.push('gpt-6.1-terra');
    if (state.agent_options.model_details) state.agent_options.model_details.push({
      model: 'gpt-6.1-terra', display_name: 'GPT-6.1-Terra', description: 'Test catalog',
      reasoning_efforts: ['low'], default_reasoning_effort: 'low', is_default: false, input_modalities: ['image', 'text']});
    await route.fulfill({json: state});
  });
  await page.goto('/');
  const original = await page.getByLabel('执行模型', {exact: true}).inputValue();
  const experience = await page.getByLabel('参考经验', {exact: true}).inputValue();
  await page.getByLabel('执行模式', {exact: true}).selectOption('hybrid');
  await expect(page.getByLabel('指导模型', {exact: true})).toHaveValue(original);
  await expect(page.getByLabel('思考强度', {exact: true})).toHaveValue('low');
  await expect(page.getByLabel('参考经验', {exact: true})).toHaveValue(experience);
  await expect(page.getByLabel('最多执行决策', {exact: true})).toHaveValue('120');
  await expect(page.getByLabel('最多指导次数', {exact: true})).toHaveValue('12');
  await expect(page.getByLabel('复查间隔（决策次数）', {exact: true})).toHaveValue('24');
  await expect(page.getByLabel('重复动作交回阈值', {exact: true})).toHaveValue('3');
});

test('hybrid mode submits Codex director and selected local executor; original source survives switching', async ({page}, testInfo) => {
  const jobs: any[] = [];
  await page.route('**/api/state?compact=1', async route => {
    const state = await (await route.fetch()).json();
    return route.fulfill({json: {...state, application_knowledge: [
      {id: 'clock-knowledge', scope: 'android', application: 'com.android.deskclock', revision: 1, sha256: 'clock-sha'},
      {id: 'game-knowledge', scope: 'android', application: 'com.example.game', revision: 2, sha256: 'game-sha'},
    ]}});
  });
  await page.route('**/api/android/devices', route => route.fulfill({json: {devices: [], warnings: []}}));
  await page.route('**/api/local-model/status', route => route.fulfill({json: {
    checked_at: new Date().toISOString(), services: [{id: 'gui', label: '本地 GUI 服务',
      endpoint: 'http://127.0.0.1:8768', reachable: true, status: 'running', backend: 'slotread', model: 'gui-decisions-2b'}],
  }}));
  await page.route('**/api/jobs', route => {
    jobs.push(route.request().postDataJSON());
    return route.fulfill({json: {job_id: 'hybrid-fixture', status: 'completed', kind: 'agent', provider: 'codex', model: 'fake'}});
  });
  await page.goto('/');
  await expect(page.getByLabel('执行模式', {exact: true})).toHaveValue('original');
  await page.getByLabel('模型来源', {exact: true}).selectOption('api');
  await page.getByLabel('执行模式', {exact: true}).selectOption('hybrid');
  await expect(page.getByLabel('指导模型来源', {exact: true})).toHaveValue('codex');
  await expect(page.getByLabel('指导模型来源', {exact: true})).toBeEnabled();
  await expect(page.getByLabel('指导模型', {exact: true})).toBeVisible();
  await page.getByLabel('主要执行模型', {exact: true}).selectOption('gui-decisions-2b');
  await expect(page.getByLabel('本地推理引擎', {exact: true})).toHaveValue('slotread');
  await page.getByLabel('最多执行决策', {exact: true}).fill('180');
  await page.getByLabel('最多指导次数', {exact: true}).fill('16');
  await page.getByLabel('复查间隔（决策次数）', {exact: true}).fill('30');
  await page.getByLabel('重复动作交回阈值', {exact: true}).fill('5');
  await page.locator('.execution-setup').screenshot({path: testInfo.outputPath('hybrid-setup.png')});
  await page.getByLabel('执行模式', {exact: true}).selectOption('original');
  await expect(page.getByLabel('模型来源', {exact: true})).toHaveValue('api');
  await expect(page.getByLabel('模型来源', {exact: true})).toBeEnabled();
  await page.getByLabel('执行模式', {exact: true}).selectOption('hybrid');
  await page.getByLabel('操作范围', {exact: true}).selectOption('android');
  await page.getByText('手动连接与高级设置', {exact: true}).click();
  await page.getByRole('combobox', {name: '设备 serial', exact: true}).fill('127.0.0.1:62273');
  await page.getByRole('combobox', {name: '授权应用包名', exact: true}).fill('com.android.deskclock');
  await expect(page.getByLabel('应用底层知识', {exact: true})).toHaveValue('clock-knowledge');
  await page.getByRole('textbox', {name: '任务指令'}).fill('暂停秒表');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('Codex 订阅服务');
  await expect(page.getByRole('dialog')).toContainText('指导 →');
  await expect(page.getByRole('dialog')).toContainText('com.android.deskclock · v1');
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect.poll(() => jobs.length).toBe(1);
  expect(jobs[0]).toMatchObject({execution_mode: 'hybrid', provider: 'codex', executor_backend: 'android',
    hybrid: {local_model: 'gui-decisions-2b', inference_backend: 'slotread', controller_config: {
      max_decisions: 180, max_guide_calls: 16, review_interval: 30, repeated_no_change_limit: 5,
    }}, mobileforge: '', android_model_profile: '',
    knowledge_selection: {id: 'clock-knowledge', sha256: 'clock-sha'}});
  expect(jobs[0].api).toBeUndefined();
});

test('hybrid browser route is rejected before submitting a job', async ({page}) => {
  await page.goto('/');
  await page.getByLabel('执行模式', {exact: true}).selectOption('hybrid');
  await page.getByLabel('操作范围', {exact: true}).selectOption('browser');
  await page.getByRole('textbox', {name: '任务指令'}).fill('Start game');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByText('混合模式首版支持 Android 手机和 Cua 桌面；请选择对应操作范围。', {exact: true})).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
