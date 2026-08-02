import test from 'node:test';
import assert from 'node:assert/strict';
import {
  selectRecentMarkdownFiles,
  parseGitHubRepo,
  splitTelegramHtml,
  missingRequiredEnv,
  fetchWithRetry,
} from '../src/utils.js';

test('selectRecentMarkdownFiles keeps only the newest requested markdown files', () => {
  const files = [
    { name: '2026-07-01.md', download_url: '1' },
    { name: 'notes.txt', download_url: 'x' },
    { name: '2026-07-03.md', download_url: '3' },
    { name: '2026-07-02.md', download_url: '2' },
  ];
  assert.deepEqual(selectRecentMarkdownFiles(files, 2).map((f) => f.name), [
    '2026-07-03.md',
    '2026-07-02.md',
  ]);
});

test('parseGitHubRepo accepts repository names and GitHub URLs', () => {
  assert.deepEqual(parseGitHubRepo('openai/openai-node'), { owner: 'openai', repo: 'openai-node' });
  assert.deepEqual(parseGitHubRepo('https://github.com/cloudflare/workers-sdk.git'), {
    owner: 'cloudflare',
    repo: 'workers-sdk',
  });
});

test('splitTelegramHtml does not cut inside an HTML tag', () => {
  const text = `${'a'.repeat(35)}<a href="https://example.com">link</a>${'b'.repeat(35)}`;
  const chunks = splitTelegramHtml(text, 40);
  assert.ok(chunks.length > 1);
  assert.ok(chunks.every((chunk) => chunk.lastIndexOf('<') <= chunk.lastIndexOf('>') || !chunk.includes('<')));
  assert.equal(chunks.join(''), text);
});

test('missingRequiredEnv reports missing bindings', () => {
  const env = { TELEGRAM_BOT_TOKEN: 'token', CHAT_HISTORY: {} };
  assert.ok(missingRequiredEnv(env).includes('DEEPSEEK_API_KEY'));
  assert.ok(!missingRequiredEnv(env).includes('CHAT_HISTORY'));
});

test('fetchWithRetry retries transient responses', async () => {
  let attempts = 0;
  const response = await fetchWithRetry(async () => {
    attempts += 1;
    return new Response('', { status: attempts < 3 ? 503 : 200 });
  }, 'https://example.com', {}, { retries: 2 });
  assert.equal(response.status, 200);
  assert.equal(attempts, 3);
});
