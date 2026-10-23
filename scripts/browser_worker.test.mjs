import test from 'node:test';
import assert from 'node:assert/strict';
import { allowedURL, validateCommand, openGame, GAME_URL } from '../src/trace2task/browser_worker.mjs';

test('only exact official HTTPS host family is admitted', () => {
  for (const url of [GAME_URL, 'https://play.ancientbeast.com/']) assert.equal(allowedURL(url), true);
  for (const url of ['http://ancientbeast.com', 'https://ancientbeast.com.evil.test/',
    'https://evilancientbeast.com/', 'https://user@ancientbeast.com/',
    'https://ancientbeast.com:8080', 'file:///x', 'javascript:alert(1)']) assert.equal(allowedURL(url), false);
});

test('wire protocol rejects scripts, URLs, modifiers, extra fields and bad coordinates', () => {
  const packet = action => ({ id: 1, op: 'action', generation: 1, action });
  for (const action of [
    { kind: 'key', key: 'F12' }, { kind: 'key', key: 'Control+l' },
    { kind: 'evaluate', text: 'game' }, { kind: 'goto', url: GAME_URL },
    { kind: 'move', x: -1, y: 0 }, { kind: 'move', x: 1280, y: 0 },
    { kind: 'move', x: .1, y: 0 }, { kind: 'move', x: true, y: 0 },
    { kind: 'move', x: 0, y: NaN }, { kind: 'move', x: 0, y: 800 },
    { kind: 'click', x: 1, y: 1, button: 'middle', count: 1 },
    { kind: 'click', x: 1, y: 1, button: 'left', count: 3 },
    { kind: 'scroll', dx: 4001, dy: 0 },
  ]) assert.throws(() => validateCommand(packet(action)));
  assert.throws(() => validateCommand({ id: 1, op: 'observe', script: 'game' }));
  assert.throws(() => validateCommand(null));
  assert.doesNotThrow(() => validateCommand(packet({ kind: 'key', key: 's' })));
  assert.doesNotThrow(() => validateCommand(packet({ kind: 'move', x: 1279, y: 799 })));
});

function fixture({ failNavigation = false } = {}) {
  const events = {}, operations = [], routes = [];
  const page = {
    on: (name, callback) => { events[name] = callback; },
    goto: async url => { operations.push(['goto', url]); if (failNavigation) throw new Error('network'); },
    url: () => GAME_URL, isClosed: () => false,
    mainFrame: () => 'main-frame',
    mouse: { click: async (...args) => operations.push(['click', ...args]),
      move: async (...args) => operations.push(['move', ...args]),
      wheel: async (...args) => operations.push(['wheel', ...args]) },
    keyboard: { press: async key => operations.push(['key', key]) },
    screenshot: async () => Buffer.from('synthetic image'),
  };
  const context = { route: async (_, callback) => { routes.push(callback); },
    on: (name, callback) => { events['context:' + name] = callback; }, newPage: async () => page };
  const browser = { newContext: async options => { operations.push(['context', options]); return context; },
    close: async () => operations.push(['closed']), version: () => 'synthetic-test' };
  const chromium = { launch: async options => { operations.push(['launch', options]); return browser; } };
  return { chromium, page, operations, events, routes };
}

test('isolated session rejects stale input and stops on out-of-scope navigation', async () => {
  const f = fixture();
  const game = await openGame(f.chromium, 'unused-test-path');
  assert.equal(f.operations[0][1].headless, true);
  assert.equal(f.operations[1][1].acceptDownloads, false);
  assert.equal(f.operations[1][1].serviceWorkers, 'block');
  const packet = { id: 1, op: 'action', generation: 0, action: { kind: 'key', key: 's' } };
  const reply = await game.handle(packet);
  assert.equal(reply.effect, 'unverifiable');
  assert.deepEqual(f.operations.at(-1), ['key', 's']);
  f.events.framenavigated('main-frame');
  assert.deepEqual(await game.handle(packet), { stale: true, executed: false });
  assert.equal(f.operations.filter(op => op[0] === 'key').length, 1);
  let aborted = false;
  await f.routes[0]({ request: () => ({ isNavigationRequest: () => true, url: () => 'https://other.test', frame: () => 'main-frame' }),
    abort: async () => { aborted = true; }, continue: async () => assert.fail('must not navigate') });
  assert.ok(aborted);
  assert.throws(game.state, /out-of-scope/);
  await game.close(); assert.deepEqual(f.operations.at(-1), ['closed']);
});

test('browser startup failure closes owned browser', async () => {
  const f = fixture({ failNavigation: true });
  await assert.rejects(openGame(f.chromium, 'unused-test-path'), /network/);
  assert.deepEqual(f.operations.at(-1), ['closed']);
});

test('navigation during observation retries only screenshots and has a strict bound', async () => {
  const f = fixture();
  const game = await openGame(f.chromium, 'unused-test-path');
  let captures = 0;
  f.page.screenshot = async () => {
    if (++captures === 1) f.events.framenavigated('main-frame');
    return Buffer.from('synthetic image');
  };
  const observed = await game.handle({ id: 1, op: 'observe' });
  assert.equal(captures, 2);
  assert.equal(observed.generation, 1);
  assert.equal(observed.filename, 'browser-0001.png');
  captures = 0;
  f.page.screenshot = async () => {
    captures++;
    f.events.framenavigated('main-frame');
    return Buffer.from('synthetic image');
  };
  await assert.rejects(game.handle({ id: 2, op: 'observe' }), /three screenshots/);
  assert.equal(captures, 3);
  assert.equal(f.operations.filter(op => ['key', 'click', 'move', 'wheel'].includes(op[0])).length, 0);
  await game.close();
});

test('third-party frame is blocked without authorizing it or invalidating main page', async () => {
  const f = fixture();
  const game = await openGame(f.chromium, 'unused-test-path');
  let aborted = false;
  await f.routes[0]({ request: () => ({ isNavigationRequest: () => true,
    url: () => 'https://embed.test', frame: () => 'embedded-frame' }),
    abort: async () => { aborted = true; }, continue: async () => assert.fail('must not allow embed') });
  assert.ok(aborted);
  assert.equal(game.state().url, GAME_URL);
  await game.close();
});

test('popups and downloads close without acting on another page', async () => {
  const f = fixture();
  const game = await openGame(f.chromium, 'unused-test-path');
  let closed = false, cancelled = false, dismissed = false;
  f.events['context:page']({ close: async () => { closed = true; } });
  f.events.download({ cancel: async () => { cancelled = true; } });
  f.events.dialog({ dismiss: async () => { dismissed = true; } });
  assert.ok(closed && cancelled && dismissed);
  await game.close();
});
