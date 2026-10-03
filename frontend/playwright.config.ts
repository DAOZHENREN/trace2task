import {defineConfig} from '@playwright/test';
import {resolve} from 'node:path';

export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1, timeout: 30000,
  use: {baseURL: 'http://127.0.0.1:8775', viewport: {width: 1366, height: 768},
    channel: process.platform === 'win32' ? 'msedge' : 'chromium', screenshot: 'only-on-failure'},
  webServer: {command: `"${process.platform === 'win32' ? resolve('../.venv/Scripts/python.exe') : 'python'}" "${resolve('../scripts/workbench_preview.py')}" --port 8775`,
    url: 'http://127.0.0.1:8775', reuseExistingServer: !process.env.CI, timeout: 30000},
});
