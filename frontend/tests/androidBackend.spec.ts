import {test, expect} from '@playwright/test';

test.beforeEach(async ({page}) => {
  await page.route('**/api/android/devices', route => route.fulfill({json: {devices: [], warnings: []}}));
});

test('scope selects the backend and Android locks ADB; Win32 is retired', async ({page}) => {
  const jobs: unknown[] = [];
  await page.route('**/api/jobs', route => {
    if (route.request().method() === 'POST') jobs.push(route.request().postDataJSON());
    return route.fulfill({json: {jobs: []}});
  });
  await page.goto('/');
  const backend = page.getByLabel('执行后端', {exact: true});
  const scope = page.getByLabel('操作范围', {exact: true});
  await expect(backend).toHaveValue('cua');
  await expect(backend.locator('option[value="win32"]')).toHaveCount(0);
  await scope.selectOption('android');
  await expect(backend).toHaveValue('android');
  await expect(backend).toBeDisabled();
  await expect(page.getByLabel('动作预算', {exact: true})).toBeVisible();
  await scope.selectOption('desktop');
  await expect(backend).toHaveValue('cua');
  await expect(backend).toBeEnabled();
  await scope.selectOption('browser');
  await expect(backend).toHaveValue('browser');
  await scope.selectOption('selected_windows');
  await expect(backend).toHaveValue('cua');
  expect(jobs).toHaveLength(0);
});

test('explicit connection and launch consent survive task confirmation; failed preflight is shown', async ({page}) => {
  const preparations: any[] = []; const jobs: any[] = [];
  await page.route('**/api/android/prepare', route => {
    preparations.push(route.request().postDataJSON());
    return route.fulfill({json: {message: '手机已连接、授权应用已安装；确认任务后自动打开应用'}});
  });
  await page.route('**/api/jobs', route => {
    jobs.push(route.request().postDataJSON());
    return route.fulfill({status: 409, json: {error: '手机未连接；请先点击「连接云手机」'}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '使用 3090 控制云手机', exact: true}).click();
  await page.getByText('手动连接与高级设置', {exact: true}).click();
  await page.getByRole('combobox', {name: '设备 serial', exact: true}).fill('127.0.0.1:52130');
  await page.getByRole('combobox', {name: '授权应用包名', exact: true}).fill('com.miHoYo.hkrpg');
  await page.getByRole('checkbox', {name: '确认任务后自动打开授权应用（不重置）'}).check();
  await page.getByRole('button', {name: '连接云手机', exact: true}).click();
  await expect(page.getByText('手机已连接、授权应用已安装；确认任务后自动打开应用', {exact: true})).toBeVisible();
  expect(preparations[0]).toMatchObject({action: 'connect', android_config: {serial: '127.0.0.1:52130', launch_app: true}});
  expect(jobs).toHaveLength(0);
  await page.getByRole('button', {name: '启动前检查', exact: true}).click();
  await expect.poll(() => preparations.length).toBe(2);
  expect(preparations[1].action).toBe('check');
  await page.getByRole('textbox', {name: '任务指令'}).fill('打开授权游戏');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('将打开授权应用 com.miHoYo.hkrpg');
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect(page.getByRole('alert')).toContainText('手机未连接');
  expect(jobs[0].android_config.launch_app).toBe(true);
});

test('3090 preset checks connections without executing and confirms the phone task route', async ({page}, info) => {
  const jobs: any[] = []; const checks: any[] = [];
  await page.route('**/api/android/model-connection', route => {
    checks.push(route.request().postDataJSON());
    return route.fulfill({json: {connected: true, message: '3090 模型已连接；未发送截图或设备输入'}});
  });
  await page.route('**/api/android/probe', route => route.fulfill({json: {
    state: {width: 720, height: 1280, focus: 'com.android.deskclock/.DeskClock'}}}));
  await page.route('**/api/jobs', route => {
    jobs.push(route.request().postDataJSON());
    return route.fulfill({json: {job_id: 'cloud-phone-fixture', status: 'completed', kind: 'agent', provider: 'api', model: 'Qwen3-VL-8B-Instruct'}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '使用 3090 控制云手机', exact: true}).click();
  await page.getByText('手动连接与高级设置', {exact: true}).click();
  await page.getByRole('combobox', {name: '设备 serial', exact: true}).fill('127.0.0.1:62273');
  await expect(page.getByLabel('操作范围', {exact: true})).toHaveValue('android');
  await expect(page.getByLabel('执行后端', {exact: true})).toHaveValue('android');
  await expect(page.getByLabel('执行模型', {exact: true})).toHaveValue('Qwen3-VL-8B-Instruct');
  await expect(page.getByRole('combobox', {name: '授权应用包名', exact: true})).toHaveValue('');
  await page.getByRole('button', {name: '检查模型连接', exact: true}).click();
  await expect(page.getByText('3090 模型已连接；未发送截图或设备输入', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '连接 / 复用 SSH 隧道', exact: true}).click();
  await expect.poll(() => checks.length).toBe(2);
  expect(checks).toEqual([{action: 'check', profile: 'qwen3-vl-3090'}, {action: 'connect', profile: 'qwen3-vl-3090'}]);
  expect(jobs).toHaveLength(0);
  await page.getByRole('textbox', {name: '任务指令'}).fill('继续当前暂停的秒表，不要重置。');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0); // no package authorization yet
  await page.getByRole('combobox', {name: '授权应用包名', exact: true}).fill('com.android.deskclock');
  await page.getByRole('alert').getByRole('button', {name: '关闭', exact: true}).click();
  await page.getByRole('button', {name: '只读检查设备'}).click();
  await expect(page.getByText(/已连接 · 720×1280/)).toBeVisible();
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('3090 服务器');
  await expect(page.getByRole('dialog')).toContainText('com.android.deskclock');
  expect(jobs).toHaveLength(0);
  await page.getByRole('button', {name: '返回修改', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path: info.outputPath('3090-cloud-phone-console.png'), fullPage: true});
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect.poll(() => jobs.length).toBe(1);
  expect(jobs[0]).toMatchObject({provider: 'api', model: 'Qwen3-VL-8B-Instruct',
    executor_backend: 'android', execution_scope: 'android', android_model_profile: 'qwen3-vl-3090',
    mobileforge: 'D:/Tools/trace2task-android/MobileForge', use_experience: false, cua_target: null,
    api: {base_url: 'http://127.0.0.1:18998/v1', api_key: 'local-only', context_window_tokens: 4096},
    android_config: {serial: '127.0.0.1:62273', package: 'com.android.deskclock'}});
});

test('Android config is explicit; probe sends no job; confirmation preserves target', async ({page}, info) => {
  const requests: any[] = [];
  await page.route('**/api/android/probe', route => route.fulfill({json: {state: {width: 720, height: 1280, focus: 'com.android.deskclock/.DeskClock'}}}));
  await page.route('**/api/jobs', route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({json: {job_id: 'android-test', status: 'completed', kind: 'agent', model: 'test', provider: 'codex'}});
  });
  await page.goto('/');
  await page.getByRole('textbox', {name: '任务指令'}).fill('暂停秒表');
  await page.getByLabel('执行后端', {exact: true}).selectOption('android');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByLabel('操作范围', {exact: true}).selectOption('android');
  await page.getByText('手动连接与高级设置', {exact: true}).click();
  const serial = page.getByRole('combobox', {name: '设备 serial', exact: true});
  await serial.fill('127.0.0.1:62273');
  await expect(serial).toHaveValue('127.0.0.1:62273');
  await serial.fill('192.168.1.10:5555');
  await expect(serial).toHaveValue('192.168.1.10:5555');
  await serial.fill('127.0.0.1:62273');
  const app = page.getByRole('combobox', {name: '授权应用包名', exact: true});
  await app.click();
  await page.getByRole('option', {name: /时钟（预设/}).click();
  await expect(app).toHaveValue('com.android.deskclock');
  await app.fill('com.example.custom');
  await expect(app).toHaveValue('com.example.custom');
  await app.fill('com.android.deskclock');
  await page.getByText('高级连接设置', {exact: false}).click();
  await page.getByRole('combobox', {name: 'ADB 路径', exact: true}).fill('C:/custom/platform-tools/adb.exe');
  await page.getByLabel('ADB 服务端口', {exact: true}).fill('5038');
  await page.getByLabel('Android 策略', {exact: true}).selectOption('mobileforge');
  await page.getByRole('button', {name: '只读检查设备'}).click();
  await expect(page.getByText(/已连接 · 720×1280/)).toBeVisible();
  expect(requests).toHaveLength(0);
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('com.android.deskclock · MobileForge');
  await page.getByRole('button', {name: '返回修改'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(requests).toHaveLength(0);
  await page.screenshot({path: info.outputPath('android-workbench.png'), fullPage: true});
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toMatchObject({executor_backend: 'android', execution_scope: 'android',
    android_config: {adb: 'C:/custom/platform-tools/adb.exe', serial: '127.0.0.1:62273', package: 'com.android.deskclock', server_port: 5038},
    mobileforge: 'D:/Tools/trace2task-android/MobileForge', cua_target: null, use_experience: false});
});

test('Android scope refreshes automatically, selects the only online device, and preserves custom budgets', async ({page}) => {
  const jobs: any[] = []; const inspected: any[] = [];
  let online = true;
  await page.route('**/api/android/devices', route => route.fulfill({json: {warnings: [], devices: online ? [
    {serial: '127.0.0.1:54146', server_port: 5037, adb: 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe', state: 'device', model: 'Cloud Phone'},
    {serial: 'offline-phone', server_port: 5038, adb: 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe', state: 'offline', model: 'Other'},
  ] : []}}));
  await page.route('**/api/android/device-apps', route => {
    inspected.push(route.request().postDataJSON());
    return route.fulfill({json: {current_package: 'com.tencent.nrc', apps: [{package: 'com.tencent.nrc', label: '洛克王国世界'}]}});
  });
  await page.route('**/api/android/prepare', () => {throw new Error('reuse must not call connect');});
  await page.route('**/api/jobs', route => {jobs.push(route.request().postDataJSON()); return route.fulfill({json: {job_id: 'reuse-test', status: 'completed'}});});
  await page.goto('/');
  await page.getByRole('button', {name: '使用 3090 控制云手机', exact: true}).click();
  await expect(page.getByRole('combobox', {name: '设备 serial', exact: true})).not.toBeVisible();
  await expect(page.getByLabel('已连接设备', {exact: true})).toHaveValue('5037/127.0.0.1:54146');
  await expect(page.getByLabel('已连接设备', {exact: true}).locator('option[value="5038/offline-phone"]')).toBeDisabled();
  await expect(page.getByLabel('授权应用', {exact: true})).toHaveValue('com.tencent.nrc');
  expect(inspected[0].android_config.server_port).toBe(5037);
  expect(jobs).toHaveLength(0);
  await page.getByRole('textbox', {name: '任务指令'}).fill('只观察当前画面，不操作手机');
  await expect(page.getByLabel('动作预算', {exact: true})).toHaveValue('160');
  await expect(page.getByLabel('时间预算（分钟）', {exact: true})).toHaveValue('40');
  await page.getByLabel('动作预算', {exact: true}).fill('120');
  await page.getByLabel('时间预算（分钟）', {exact: true}).fill('30');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('com.tencent.nrc');
  await expect(page.getByRole('dialog')).toContainText('120 个动作 / 30 分钟');
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect.poll(() => jobs.length).toBe(1);
  expect(jobs[0].android_config).toMatchObject({serial: '127.0.0.1:54146', server_port: 5037, package: 'com.tencent.nrc'});
  expect(jobs[0].android_budget).toEqual({max_actions: 120, max_seconds: 1800});
  online = false;
  await page.getByRole('button', {name: '刷新已连接设备', exact: true}).click();
  await expect(page.getByLabel('已连接设备', {exact: true})).toHaveValue('');
  await expect(page.getByLabel('授权应用', {exact: true})).toHaveValue('');
  await expect(page.getByText(/未发现已连接设备/)).toBeVisible();
});

test('Android scope connects the available RedFinger channel without starting a task', async ({page}) => {
  const requests: any[] = [];
  const jobs: any[] = [];
  await page.route('**/api/jobs', route => {jobs.push(route.request().postDataJSON()); return route.fulfill({json: {job_id: 'auto-forwarded', status: 'completed'}});});
  await page.route('**/api/android/devices', route => route.fulfill({json: {warnings: [], devices: [
    {serial: '127.0.0.1:60378', server_port: 5038, adb: 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe', state: 'forwarded', model: '红手指现有通道', source: 'redfinger'},
  ]}}));
  await page.route('**/api/android/device-apps', route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({json: {current_package: '', apps: [{package: 'com.tencent.nrc', label: '洛克王国世界'}]}});
  });
  await page.goto('/');
  await page.getByLabel('操作范围', {exact: true}).selectOption('android');
  await expect(page.getByLabel('已连接设备', {exact: true})).toHaveValue('5038/127.0.0.1:60378');
  await expect(page.getByLabel('已连接设备', {exact: true}).locator('option[value="5038/127.0.0.1:60378"]')).toBeEnabled();
  await expect(page.getByLabel('授权应用', {exact: true})).toBeEnabled();
  await page.getByLabel('授权应用', {exact: true}).selectOption('com.tencent.nrc');
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({source: 'redfinger', android_config: {serial: '127.0.0.1:60378', server_port: 5038}});
  expect(jobs).toHaveLength(0);
  await page.getByRole('textbox', {name: '任务指令'}).fill('观察画面');
  await page.getByRole('button', {name: '开始任务', exact: true}).click();
  await expect(page.getByRole('dialog')).toContainText('将打开授权应用 com.tencent.nrc');
  await expect(page.getByRole('dialog')).toContainText('160 个动作 / 40 分钟');
  await page.getByRole('button', {name: '确认开始', exact: true}).click();
  await expect.poll(() => jobs.length).toBe(1);
  expect(jobs[0].android_budget).toEqual({max_actions: 160, max_seconds: 2400});
});

test('multiple cloud channels connect once and leave device choice to the user', async ({page}) => {
  const requests: any[] = [];
  const attached = new Set<string>();
  const serials = ['127.0.0.1:60101', '127.0.0.1:60102'];
  await page.route('**/api/android/devices', route => route.fulfill({json: {warnings: [], devices: serials.map(serial => (
    {serial, server_port: 5038, adb: 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe',
      state: attached.has(serial) ? 'device' : 'forwarded', source: attached.has(serial) ? undefined : 'redfinger', model: 'Cloud Phone'}))}}));
  await page.route('**/api/android/device-apps', route => {
    const body = route.request().postDataJSON(); requests.push(body);
    if (body.source === 'redfinger') attached.add(body.android_config.serial);
    return route.fulfill({json: {current_package: 'com.example.current', apps: [
      {package: 'com.example.current', label: '当前应用'}, {package: 'com.example.selected', label: '指定应用'},
    ]}});
  });
  await page.goto('/');
  await page.getByLabel('操作范围', {exact: true}).selectOption('android');
  await expect(page.getByRole('button', {name: '刷新已连接设备', exact: true})).toBeEnabled();
  expect(requests.filter(r => r.source === 'redfinger')).toHaveLength(2);
  await expect(page.getByLabel('已连接设备', {exact: true})).toHaveValue('');
  await expect(page.getByText('发现 2 台在线设备，请选择本次使用的手机。')).toBeVisible();
  await page.getByLabel('已连接设备', {exact: true}).selectOption('5038/127.0.0.1:60102');
  await expect(page.getByLabel('授权应用', {exact: true})).toBeEnabled();
  await page.getByLabel('授权应用', {exact: true}).selectOption('com.example.selected');
  await page.getByRole('button', {name: '刷新已连接设备', exact: true}).click();
  await expect(page.getByLabel('授权应用', {exact: true})).toBeEnabled();
  await expect(page.getByLabel('已连接设备', {exact: true})).toHaveValue('5038/127.0.0.1:60102');
  await expect(page.getByLabel('授权应用', {exact: true})).toHaveValue('com.example.selected');
  expect(requests.filter(r => r.source === 'redfinger')).toHaveLength(2);
});

test('leaving Android invalidates an in-flight refresh and reentering refreshes again', async ({page}) => {
  let discoveryCount = 0, oldHandled = false;
  let releaseOld!: () => void;
  const oldResponse = new Promise<void>(resolve => {releaseOld = resolve;});
  const inspected: string[] = [];
  await page.route('**/api/android/devices', async route => {
    const count = ++discoveryCount;
    if (count === 1) await oldResponse;
    await route.fulfill({json: {warnings: [], devices: [{serial: count === 1 ? 'stale-phone' : 'current-phone',
      server_port: 5038, adb: 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe', state: 'device', model: 'Fixture'}]}});
    if (count === 1) oldHandled = true;
  });
  await page.route('**/api/android/device-apps', route => {
    inspected.push(route.request().postDataJSON().android_config.serial);
    return route.fulfill({json: {current_package: 'com.example.game', apps: [{package: 'com.example.game', label: 'Game'}]}});
  });
  await page.goto('/');
  await page.getByLabel('操作范围', {exact: true}).selectOption('android');
  await expect.poll(() => discoveryCount).toBe(1);
  await page.getByRole('textbox', {name: '任务指令'}).fill('合成任务');
  await expect(page.getByRole('button', {name: '开始任务', exact: true})).toBeDisabled();
  await page.getByLabel('动作预算', {exact: true}).fill('211');
  await page.getByLabel('时间预算（分钟）', {exact: true}).fill('55');
  await page.getByLabel('操作范围', {exact: true}).selectOption('desktop');
  await page.getByLabel('操作范围', {exact: true}).selectOption('android');
  await expect(page.getByLabel('已连接设备', {exact: true})).toHaveValue('5038/current-phone');
  await expect(page.getByLabel('授权应用', {exact: true})).toBeEnabled();
  releaseOld();
  await expect.poll(() => oldHandled).toBe(true);
  await expect(page.getByLabel('已连接设备', {exact: true})).toHaveValue('5038/current-phone');
  await expect(page.getByLabel('动作预算', {exact: true})).toHaveValue('211');
  await expect(page.getByLabel('时间预算（分钟）', {exact: true})).toHaveValue('55');
  expect(inspected).toEqual(['current-phone']);
});
