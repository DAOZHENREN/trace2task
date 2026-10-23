import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(`runs/browser-probe-guards-${Date.now()}`);
function run(input) {
  return new Promise((done, reject) => {
    const child = spawn(process.execPath, ['scripts/browser_game_probe.mjs', output],
      { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    let stdout = '', stderr = '';
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.on('data', data => { stderr += data; });
    child.on('error', reject);
    child.on('exit', code => done({ code, stdout, stderr }));
    child.stdin.end(input.map(command => JSON.stringify(command)).join('\n') + '\n');
  });
}

test('headless probe rejects invalid commands, captures pixels and closes', { timeout: 30000 }, async () => {
  const result = await run([
    null,
    { op: 'goto', url: 'https://example.com/' },
    { op: 'click', x: -1, y: 0 },
    { op: 'key', key: 'F12' },
    { op: 'evaluate', expression: 'window.game' },
    { op: 'observe', wait_ms: 5001 },
    { op: 'observe', wait_ms: 0 },
    { op: 'close' },
  ]);
  assert.equal(result.code, 0, result.stderr);
  const entries = result.stdout.trim().split('\n').map(line => JSON.parse(line));
  assert.equal(entries.filter(entry => entry.kind === 'error').length, 6);
  assert.equal(entries[0].headless, true);
  const steps = entries.filter(entry => entry.kind === 'step');
  assert.equal(steps.length, 1);
  assert.equal(steps[0].url, 'about:blank');
  assert.match(steps[0].sha256, /^[a-f0-9]{64}$/);
  assert.ok((await readFile(steps[0].screenshot)).length > 0);
  assert.equal(entries.at(-1).kind, 'closed');
});

test('probe refuses to overwrite an existing evidence directory', { timeout: 30000 }, async () => {
  const result = await run([]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /EEXIST/);
});
