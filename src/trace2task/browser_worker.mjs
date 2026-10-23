// Source-build Ancient Beast driver. Only browser-local pixel input is exposed.
import { createRequire } from 'node:module';
import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

export const GAME_URL = 'https://ancientbeast.com/';
export const VIEWPORT = Object.freeze({ width: 1280, height: 800 });
export const KEYS = new Set(['Escape', 'Enter', 'Space', 'Tab', 'Backspace', 'Delete',
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown',
  ...'abcdefghijklmnopqrstuvwxyz0123456789']);

export function allowedURL(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port &&
      (url.hostname === 'ancientbeast.com' || url.hostname.endsWith('.ancientbeast.com'));
  } catch { return false; }
}

function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== [...fields].sort().join(',')) {
    throw new Error('Invalid command fields');
  }
}

export function validateCommand(command) {
  if (!command || typeof command !== 'object' || !Number.isInteger(command.id) || command.id < 1) {
    throw new Error('Invalid command identity');
  }
  if (['observe', 'state', 'close'].includes(command.op)) {
    exact(command, ['id', 'op']);
    return;
  }
  exact(command, ['id', 'op', 'generation', 'action']);
  if (command.op !== 'action' || !Number.isInteger(command.generation) || command.generation < 0) {
    throw new Error('Unsupported operation');
  }
  const action = command.action;
  if (!action || typeof action !== 'object') throw new Error('Missing action');
  if (action.kind === 'click') {
    exact(action, ['kind', 'x', 'y', 'button', 'count']);
    if (!['left', 'right'].includes(action.button) || ![1, 2].includes(action.count)) {
      throw new Error('Invalid click');
    }
  } else if (action.kind === 'move') exact(action, ['kind', 'x', 'y']);
  else if (action.kind === 'key') {
    exact(action, ['kind', 'key']);
    if (!KEYS.has(action.key)) throw new Error('Key is not in the game whitelist');
  } else if (action.kind === 'scroll') {
    exact(action, ['kind', 'dx', 'dy']);
    if (![action.dx, action.dy].every(value => Number.isInteger(value) && Math.abs(value) <= 4000)) {
      throw new Error('Invalid scroll');
    }
  } else throw new Error('Unsupported action');
  if (['click', 'move'].includes(action.kind) &&
      (!Number.isInteger(action.x) || !Number.isInteger(action.y) || action.x < 0 ||
       action.y < 0 || action.x >= VIEWPORT.width || action.y >= VIEWPORT.height)) {
    throw new Error('Coordinates outside the authorized viewport');
  }
}

export async function openGame(chromium, output) {
  const browser = await chromium.launch({ channel: 'msedge', headless: true, timeout: 30000 });
  try {
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1,
      acceptDownloads: false, serviceWorkers: 'block' });
    let page;
    let generation = 0;
    let screenshotIndex = 0;
    let blockedNavigation = false;
    // A pixel click cannot silently authorize a different website or another page.
    await context.route('**/*', async route => {
      if (route.request().isNavigationRequest() && !allowedURL(route.request().url())) {
        // Third-party embeds (for example videos) may be refused without invalidating
        // the game page. A top-level attempt to leave the authorized site stops the run.
        if (page && route.request().frame() === page.mainFrame()) blockedNavigation = true;
        process.stderr.write(`blocked navigation: ${new URL(route.request().url()).origin}\n`);
        await route.abort('blockedbyclient');
      } else await route.continue();
    });
    context.on('page', other => { if (page && other !== page) void other.close(); });
    page = await context.newPage();
    page.on('dialog', dialog => { void dialog.dismiss(); });
    page.on('download', download => { void download.cancel(); });
    page.on('framenavigated', frame => {
      if (frame === page.mainFrame() || allowedURL(frame.url())) generation += 1;
    });
    page.on('pageerror', error => { process.stderr.write(`game: ${error.message}\n`); });
    await page.goto(GAME_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });

    function state() {
      if (page.isClosed() || blockedNavigation || !allowedURL(page.url())) {
        throw new Error('Authorized game page closed or attempted an out-of-scope navigation');
      }
      return { generation, url: page.url(), width: VIEWPORT.width, height: VIEWPORT.height };
    }

    return {
      version: browser.version(),
      state,
      close: () => browser.close(),
      async handle(command) {
        validateCommand(command);
        if (command.op === 'state') return state();
        if (command.op === 'observe') {
          // A screenshot is read-only: discard a torn observation during initial iframe
          // loading, bounded to three captures. Input is never retried here.
          for (let attempt = 0; attempt < 3; attempt++) {
            const before = state();
            const filename = `browser-${String(screenshotIndex++).padStart(4, '0')}.png`;
            const bytes = await page.screenshot({ path: resolve(output, filename), timeout: 5000 });
            const after = state();
            if (before.generation === after.generation) {
              return { ...after, filename, sha256: createHash('sha256').update(bytes).digest('hex') };
            }
          }
          throw new Error('Navigation during three screenshots; no input sent');
        }
        const before = state();
        if (command.generation !== before.generation) return { stale: true, executed: false };
        const action = command.action;
        if (action.kind === 'click') await page.mouse.click(action.x, action.y,
          { button: action.button, clickCount: action.count });
        else if (action.kind === 'move') await page.mouse.move(action.x, action.y);
        else if (action.kind === 'key') await page.keyboard.press(action.key);
        else if (action.kind === 'scroll') await page.mouse.wheel(action.dx, action.dy);
        // Successful injection is not proof that the game accepted or completed the action.
        return { effect: 'unverifiable', delivery: { mode: 'isolated_browser' },
          route: 'playwright_pixel_input' };
      },
    };
  } catch (error) {
    await browser.close();
    throw error;
  }
}

async function main() {
  const require = createRequire(resolve(process.argv[2]));
  const { chromium } = require('playwright');
  const output = resolve(process.argv[3]);
  const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
  let game;
  let fatal = false;
  const deadline = setTimeout(() => { void game?.close().finally(() => process.exit(1)); }, 60 * 60 * 1000);
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  try {
    game = await openGame(chromium, output);
    emit({ id: 0, ok: true, headless: true, browser: game.version, ...game.state() });
    let previousId = 0;
    for await (const line of lines) {
      let command;
      try {
        command = JSON.parse(line);
        validateCommand(command);
        if (command.id <= previousId) throw new Error('Duplicate or out-of-order request; not replayed');
        previousId = command.id;
        if (command.op === 'close') { emit({ id: command.id, ok: true }); break; }
        emit({ id: command.id, ok: true, result: await game.handle(command) });
      } catch (error) {
        // An input may already have been delivered. End the worker; do not retry it.
        emit({ id: command?.id ?? -1, ok: false, error: error.message });
        fatal = true;
        break;
      }
    }
  } catch (error) { emit({ id: 0, ok: false, error: error.message }); fatal = true; }
  finally {
    clearTimeout(deadline);
    lines.close();
    await game?.close();
  }
  process.exitCode = fatal ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
