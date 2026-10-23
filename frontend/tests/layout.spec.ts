import {test, expect} from '@playwright/test';

test('workspace has clear sections and stays usable at compact widths', async ({page}, info) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {name: '任务工作台', exact: true})).toBeVisible();
  await expect(page.getByText('任务目标', {exact: false}).first()).toBeVisible();
  await expect(page.getByRole('button', {name: '开始任务', exact: true})).toBeVisible();
  await page.screenshot({path: info.outputPath('workspace-desktop.png'), animations: 'disabled'});
  await page.getByLabel('操作范围', {exact: true}).selectOption('android');
  await expect(page.getByLabel('执行后端', {exact: true})).toHaveValue('android');
  await expect(page.getByLabel('执行后端', {exact: true})).toBeDisabled();
  await expect(page.getByText('手机连接', {exact: true})).toBeVisible();
  await expect(page.getByText(/预设不代表设备在线/)).not.toBeVisible();
  await page.getByText('连接说明与能力边界', {exact: true}).click();
  await expect(page.getByText(/预设不代表设备在线/)).toBeVisible();
  await page.getByText('连接说明与能力边界', {exact: true}).click();
  await page.setViewportSize({width: 1440, height: 1100});
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path: info.outputPath('workspace-android.png'), animations: 'disabled'});
  await page.setViewportSize({width: 600, height: 900});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', {name: '开始任务', exact: true}).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', {name: '开始任务', exact: true})).toBeVisible();
  await page.screenshot({path: info.outputPath('workspace-compact.png'), animations: 'disabled'});
});
