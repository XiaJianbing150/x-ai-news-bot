export function selectRecentMarkdownFiles(files, limit = 7) {
  return files
    .filter((file) => file?.name?.endsWith('.md') && file.download_url)
    .sort((a, b) => b.name.localeCompare(a.name))
    .slice(0, Math.max(0, limit));
}

export function parseGitHubRepo(text) {
  const match = text.match(/(?:https?:\/\/github\.com\/)?([\w.-]+)\/([\w.-]+)/i);
  if (!match) return null;
  return { owner: match[1], repo: match[2].replace(/\.git$/i, '') };
}

export function decodeBase64Utf8(value) {
  const binary = atob(value.replace(/\n/g, ''));
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

export function splitTelegramHtml(text, maxLength = 4000) {
  if (!text) return [];
  const chunks = [];
  let remaining = text;

  while (remaining.length > maxLength) {
    let cut = remaining.lastIndexOf('\n', maxLength);
    if (cut < Math.floor(maxLength / 2)) cut = remaining.lastIndexOf(' ', maxLength);
    if (cut < Math.floor(maxLength / 2)) cut = maxLength;

    const lastLt = remaining.lastIndexOf('<', cut);
    const lastGt = remaining.lastIndexOf('>', cut);
    if (lastLt > lastGt) cut = lastLt;
    if (cut <= 0) cut = maxLength;

    chunks.push(remaining.slice(0, cut));
    remaining = remaining.slice(cut).replace(/^\n+/, '');
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}

export function missingRequiredEnv(env) {
  const required = [
    'TELEGRAM_BOT_TOKEN',
    'TELEGRAM_WEBHOOK_SECRET',
    'DEEPSEEK_API_KEY',
    'GITHUB_TOKEN',
    'GITHUB_REPO',
    'GITHUB_WORKFLOW',
    'ALLOWED_CHAT_ID',
  ];
  if (!env.CHAT_HISTORY) required.push('CHAT_HISTORY');
  return required.filter((name) => !env[name]);
}

export async function fetchWithRetry(fetchFn, url, init = {}, options = {}) {
  const retries = options.retries ?? 2;
  const retryStatuses = options.retryStatuses ?? [429, 500, 502, 503, 504];
  let lastError;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await fetchFn(url, init);
      if (!retryStatuses.includes(response.status) || attempt === retries) return response;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
      if (attempt === retries) throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 150 * 2 ** attempt));
  }
  throw lastError;
}
