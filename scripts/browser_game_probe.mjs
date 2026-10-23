// Isolated, pixel-only browser qualification. No DOM/game-state reads or OS input.
import { createRequire } from 'node:module';
import { mkdir, appendFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { createInterface } from 'node:readline';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');
const output = resolve(process.argv[2] || `runs/browser-game-${Date.now()}`);
await mkdir(dirname(output), { recursive: true });
await mkdir(output); // Refuse an existing evidence directory instead of overwriting screenshots.
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 },
  deviceScaleFactor: 1, acceptDownloads: false });
const page = await context.newPage();
page.setDefaultTimeout(15000);
let sequence = 0;
let closed = false;
let commands = 0;
const log = async (data) => {
  const entry = { time: new Date().toISOString(), ...data };
  await appendFile(resolve(output, 'actions.jsonl'), JSON.stringify(entry) + '\n');
  console.log(JSON.stringify(entry));
};
const snapshot = async () => {
  const path = resolve(output, `${String(sequence++).padStart(4, '0')}.png`);
  const bytes = await page.screenshot({ path, timeout: 15000 });
  return { screenshot: path, sha256: createHash('sha256').update(bytes).digest('hex'), url: page.url() };
};
const close = async () => {
  if (closed) return;
  closed = true;
  clearTimeout(expiry);
  await browser.close();
};
const expiry = setTimeout(() => { void close().then(() => process.exit(0)); }, 20 * 60 * 1000);
page.on('pageerror', error => { void log({ kind: 'pageerror', message: error.message }); });
page.on('popup', popup => { void popup.close(); });
process.on('SIGINT', () => { void close().then(() => process.exit(0)); });
await log({ kind: 'ready', headless: true, browser: browser.version(),
  policy: 'screenshots and browser-local inputs only; no DOM or game-state reads',
  viewport: { width: 1280, height: 800 }, output });
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
try {
  for await (const line of lines) {
    if (!line.trim()) continue;
    let command;
    try {
      command = JSON.parse(line);
      if (!command || typeof command !== 'object' || Array.isArray(command)) throw new Error('Expected command object');
      if (command.op === 'close') { await close(); await log({ kind: 'closed' }); break; }
      if (++commands > 120) { await log({ kind: 'limit', reason: '120 commands reached' }); break; }
      if (command.wait_ms !== undefined && (!Number.isFinite(command.wait_ms) || command.wait_ms < 0 || command.wait_ms > 5000)) throw new Error('wait_ms must be between 0 and 5000');
      await log({ kind: 'attempt', command });
      if (command.op === 'goto') {
        const url = new URL(command.url);
        if (url.protocol !== 'https:' || !(url.hostname === 'ancientbeast.com' || url.hostname.endsWith('.ancientbeast.com'))) {
          throw new Error('Only official Ancient Beast HTTPS pages are allowed');
        }
        await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 45000 });
      } else if (command.op === 'click' || command.op === 'move') {
        if (!Number.isFinite(command.x) || !Number.isFinite(command.y) || command.x < 0 || command.x >= 1280 || command.y < 0 || command.y >= 800) throw new Error('Coordinate outside viewport');
        await page.mouse[command.op](command.x, command.y);
      } else if (command.op === 'key') {
        if (!['Escape', 'Enter', 'Space', 'Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'w', 'e', 'r', 'q', 's', 'd'].includes(command.key)) throw new Error('Key not allowed');
        await page.keyboard.press(command.key);
      } else if (command.op !== 'observe') throw new Error('Unsupported operation');
      await page.waitForTimeout(Math.max(0, Math.min(5000, command.wait_ms ?? 700)));
      await log({ kind: 'step', command, ...await snapshot() });
    } catch (error) { await log({ kind: 'error', command, message: error.message }); }
  }
} finally { await close(); }
