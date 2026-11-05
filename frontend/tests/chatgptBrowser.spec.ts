import {test, expect} from '@playwright/test';

test('ChatGPT login window reports loading failures without claiming model connectivity', async ({page}) => {
  const calls: string[] = [];
  let state = {status: 'closed', navigation: 'idle', host: '', error: '', running: false,
    profile_present: true, model_connection: 'unverified'};
  await page.route('**/api/chatgpt-browser', route => {
    if (route.request().method() === 'POST') {
      const {action} = route.request().postDataJSON();
      calls.push(action);
      state = action === 'open' ? {...state, status: 'open', navigation: 'failed', running: true,
        host: 'chatgpt.com', error: '页面加载失败：CannotConnect'}
        : {...state, status: 'closed', navigation: 'idle', running: false, error: ''};
    }
    return route.fulfill({json: state});
  });
  await page.goto('/');
  await page.getByRole('navigation', {name: '主导航'}).getByRole('button', {name: 'ChatGPT 网页', exact: true}).click();
  const section = page.getByRole('region', {name: 'ChatGPT 网页会话'});
  await expect(section).toContainText('接口待验证');
  await expect(section.getByRole('button', {name: '关闭浏览器窗口'})).toBeDisabled();
  await section.getByRole('button', {name: '打开 ChatGPT 登录窗口'}).click();
  await expect(section).toContainText('页面加载失败：CannotConnect');
  await expect(section.getByRole('button', {name: '显示 ChatGPT 窗口'})).toBeEnabled();
  await section.getByRole('button', {name: '关闭浏览器窗口'}).click();
  await expect(section).toContainText('窗口已关闭');
  expect(calls).toEqual(['open', 'close']);
});
