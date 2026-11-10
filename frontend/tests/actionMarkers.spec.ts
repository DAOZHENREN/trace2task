import {test, expect} from '@playwright/test';
import {actionMarkers} from '../src/actionMarkers';

test('marks normalized points and drag endpoints, not keyboard actions or pixel guesses', () => {
  const markers = actionMarkers({prediction: {actions: [
    {skill: 'click', args: {x: .25, y: .75}},
    {skill: 'hotkey', args: {keys: ['CTRL', 'C']}},
    {skill: 'drag', args: {start_x: 0, start_y: 1, end_x: .5, end_y: .2}},
    {skill: 'click', args: {x: 250, y: 750}},
  ]}});
  expect(markers.map(m => [m.x, m.y])).toEqual([[.25, .75], [0, 1], [.5, .2]]);
  expect(actionMarkers(null)).toEqual([]);
});

for (const field of ['guidance', 'notices'] as const) {
test(`trajectory renders ${field} and positions markers on the image`, async ({page}) => {
  const run = {job_id:'marker-test', status:'completed', kind:'agent', instruction:'Test trajectory'};
  await page.route('**/api/workbench/runs', r => r.fulfill({json:{runs:[run]}}));
  await page.route('**/api/workbench/runs/marker-test?after=*', r => r.fulfill({json:{session:run,cursor:1,has_more:false,events:[
    {seq:1,name:'model.round',attributes:{step_index:0,status:'predicted',artifact_ref:'/api/marker-record',screenshot:'test.png',
      subtask:{goal:'Copy file',completion_hint:'File is visible',fail_conditions:['Conflict appears'],[field]:['Use Copy to from the context menu.']}}}
  ]}}));
  await page.route('**/api/marker-record', r => r.fulfill({json:{raw_output:'actual model output',prediction:{actions:[{skill:'click',args:{x:.25,y:.75}}]}}}));
  await page.route('**/api/local-image?path=*', r => r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="400"/>'}));
  await page.goto('/');
  await page.getByRole('button',{name:'查看运行轨迹'}).click();
  await expect(page.getByText('actual model output',{exact:true})).toBeVisible();
  await expect(page.getByText('Copy file',{exact:true})).toBeVisible();
  await expect(page.getByText('Conflict appears',{exact:true})).not.toBeVisible();
  await page.getByText('完成条件与提示', {exact:true}).click();
  await expect(page.getByRole('list', {name:'子任务操作指导'})).toContainText('Use Copy to from the context menu.');
  const marker=page.locator('.round-action-marker');
  await expect(marker).toBeVisible();
  await expect(marker).toHaveText('');
  await expect(marker).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('details').filter({has:page.locator('summary', {hasText:'本轮模型输入'})}).last()).toHaveAttribute('open','');
  const image=await page.locator('.round-marked-image img').boundingBox();
  const point=await marker.boundingBox();
  expect(Math.abs(point!.x+point!.width/2 - (image!.x+image!.width*.25))).toBeLessThan(1);
  expect(Math.abs(point!.y+point!.height/2 - (image!.y+image!.height*.75))).toBeLessThan(1);
});

}
