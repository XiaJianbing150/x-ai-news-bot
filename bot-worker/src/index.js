import {
  decodeBase64Utf8,
  fetchWithRetry,
  missingRequiredEnv,
  parseGitHubRepo,
  selectRecentMarkdownFiles,
  splitTelegramHtml,
} from './utils.js';

const SYSTEM_PROMPT =
  '你是一个聚焦 Anthropic Claude 和 OpenAI ChatGPT/GPT 的中文 AI 助手。' +
  '对话风格简洁、信息密度高。除非用户问到,不主动谈其它公司的产品。' +
  '回复使用 Telegram HTML(<b> <i> <a>),不要用 markdown。';

const HELP_TEXT =
  '<b>AI 早报 Bot 指令</b>\n\n' +
  '/news     立即触发跑一次今日早报(2-5 分钟后推送)\n' +
  '/search 关键字  在最近 7 天早报存档里检索并回答\n' +
  '/query 内容     对早报里某条内容做细节展开\n' +
  '/clear    清空当前对话上下文\n' +
  '/help     显示本帮助\n\n' +
  '直接发消息即可多轮聊天,默认带 10 轮历史。';

const QUERY_SYSTEM_PROMPT =
  '你是 Anthropic Claude 和 OpenAI ChatGPT/GPT 的资深观察者。' +
  '请结合早报存档和可靠的原始资料，对用户指定内容做结构化深度解读。' +
  '输出包含：一句话总结、它是什么、核心功能、怎么用、典型场景、限制、对比、进一步阅读。' +
  '回复使用 Telegram HTML，不要使用 Markdown；拿不准的事实明确标注推测，不要编造链接。';

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === 'GET' && (url.pathname === '/' || url.pathname === '/health')) {
      const missing = missingRequiredEnv(env);
      return Response.json(
        { ok: missing.length === 0, service: 'ai-news-bot-worker', missing },
        { status: missing.length === 0 ? 200 : 503 }
      );
    }

    if (request.method !== 'POST' || url.pathname !== '/tg') {
      return new Response('not found', { status: 404 });
    }

    if (request.headers.get('X-Telegram-Bot-Api-Secret-Token') !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response('forbidden', { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response('bad json', { status: 400 });
    }

    ctx.waitUntil(
      handleUpdate(update, env).catch(async (error) => {
        console.error('handleUpdate failed', error);
        const chatId = update?.message?.chat?.id || update?.edited_message?.chat?.id;
        if (chatId) {
          await tgSend(env, String(chatId), '⚠️ 请求处理失败，请稍后重试。');
        }
      })
    );
    return new Response('ok');
  },
};

async function handleUpdate(update, env) {
  const msg = update.message || update.edited_message;
  if (!msg?.text) return;
  const chatId = String(msg.chat.id);
  const text = msg.text.trim();

  if (chatId !== String(env.ALLOWED_CHAT_ID)) {
    await tgSend(env, chatId, '未授权的对话,请联系机器人管理员。');
    return;
  }
  if (text === '/start' || text === '/help') return tgSend(env, chatId, HELP_TEXT);
  if (text === '/clear') {
    await env.CHAT_HISTORY.delete(`chat:${chatId}`);
    return tgSend(env, chatId, '已清空对话上下文。');
  }
  if (text === '/news') return cmdNews(env, chatId);
  if (text.startsWith('/search')) {
    const query = text.slice(7).trim();
    return query ? cmdSearch(env, chatId, query) : tgSend(env, chatId, '用法: <code>/search 关键字</code>');
  }
  if (text.startsWith('/query')) {
    const topic = text.slice(6).trim();
    return topic ? cmdQuery(env, chatId, topic) : tgSend(env, chatId, '用法: <code>/query 主题</code>');
  }
  return cmdChat(env, chatId, text);
}

function githubHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'User-Agent': 'ai-news-bot-worker',
    'X-GitHub-Api-Version': '2022-11-28',
  };
}

async function cmdNews(env, chatId) {
  const response = await fetchWithRetry(
    fetch,
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`,
    {
      method: 'POST',
      headers: githubHeaders(env),
      body: JSON.stringify({ ref: 'main', inputs: { hours_lookback: '168' } }),
      signal: AbortSignal.timeout(10000),
    },
    { retries: 1 }
  );
  if (response.status === 204) {
    return tgSend(env, chatId, `已触发早报任务,通常 2-5 分钟后推送。\nhttps://github.com/${env.GITHUB_REPO}/actions`);
  }
  throw new Error(`GitHub workflow dispatch failed: HTTP ${response.status}`);
}

async function fetchArchiveDocs(env, limit, maxChars) {
  const listResponse = await fetchWithRetry(
    fetch,
    `https://api.github.com/repos/${env.GITHUB_REPO}/contents/archive`,
    { headers: githubHeaders(env), signal: AbortSignal.timeout(10000) }
  );
  if (!listResponse.ok) throw new Error(`读取 archive 失败: HTTP ${listResponse.status}`);

  const files = selectRecentMarkdownFiles(await listResponse.json(), limit);
  const results = await Promise.allSettled(
    files.map(async (file) => {
      const response = await fetchWithRetry(fetch, file.download_url, {
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) throw new Error(`${file.name}: HTTP ${response.status}`);
      return `## ${file.name}\n${await response.text()}`;
    })
  );
  const docs = results.filter((result) => result.status === 'fulfilled').map((result) => result.value);
  results
    .filter((result) => result.status === 'rejected')
    .forEach((result) => console.warn('archive file skipped', result.reason));
  return docs.join('\n\n---\n\n').slice(0, maxChars);
}

async function cmdSearch(env, chatId, query) {
  await tgSend(env, chatId, `🔎 正在检索最近 7 天早报: <i>${escapeHtml(query)}</i>`);
  const corpus = await fetchArchiveDocs(env, 7, 50000);
  if (!corpus) return tgSend(env, chatId, 'archive 是空的,先跑一次 /news');
  const answer = await deepseekChat(env, [
    {
      role: 'system',
      content: '下面是最近 7 天的 AI 早报存档。请准确回答，引用具体日期；找不到就明说。回复用 Telegram HTML。',
    },
    { role: 'user', content: `存档:\n${corpus}\n\n问题:${query}` },
  ]);
  return tgSend(env, chatId, answer);
}

async function fetchGitHubReadme(env, topic) {
  const parsed = parseGitHubRepo(topic);
  if (!parsed) return '';
  const response = await fetchWithRetry(
    fetch,
    `https://api.github.com/repos/${parsed.owner}/${parsed.repo}/readme`,
    { headers: githubHeaders(env), signal: AbortSignal.timeout(10000) }
  );
  if (!response.ok) return '';
  const data = await response.json();
  if (!data.content) return '';
  return decodeBase64Utf8(data.content).slice(0, 6000);
}

async function cmdQuery(env, chatId, topic) {
  await tgSend(env, chatId, `🧠 正在展开: <i>${escapeHtml(topic)}</i>\n通常 8-20 秒,请稍候。`);
  const days = Math.max(1, Number.parseInt(env.QUERY_ARCHIVE_DAYS || '3', 10));
  const [archiveText, repoText] = await Promise.all([
    fetchArchiveDocs(env, days, 15000).catch((error) => {
      console.warn('query archive fetch failed', error);
      return '';
    }),
    fetchGitHubReadme(env, topic).catch((error) => {
      console.warn('query README fetch failed', error);
      return '';
    }),
  ]);
  const context = [
    archiveText && `【最近早报存档】\n${archiveText}`,
    repoText && `【GitHub README】\n${repoText}`,
    `【用户想展开的内容】\n${topic}`,
  ].filter(Boolean).join('\n\n');
  const answer = await deepseekChat(
    env,
    [{ role: 'system', content: QUERY_SYSTEM_PROMPT }, { role: 'user', content: context }],
    { model: env.QUERY_MODEL, temperature: 0.3, max_tokens: 3000 }
  );
  return tgSend(env, chatId, answer);
}

async function cmdChat(env, chatId, text) {
  const key = `chat:${chatId}`;
  const turns = Number.parseInt(env.HISTORY_TURNS || '10', 10);
  let history = [];
  try {
    const raw = await env.CHAT_HISTORY.get(key);
    if (raw) history = JSON.parse(raw);
  } catch (error) {
    console.warn('history read failed', error);
  }

  const nextHistory = [...history, { role: 'user', content: text }];
  const reply = await deepseekChat(env, [{ role: 'system', content: SYSTEM_PROMPT }, ...nextHistory]);
  nextHistory.push({ role: 'assistant', content: reply });
  const trimmed = nextHistory.slice(-turns * 2);
  await env.CHAT_HISTORY.put(key, JSON.stringify(trimmed), { expirationTtl: 60 * 60 * 24 * 7 });
  return tgSend(env, chatId, reply);
}

async function deepseekChat(env, messages, opts = {}) {
  const response = await fetchWithRetry(
    fetch,
    'https://api.deepseek.com/v1/chat/completions',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.DEEPSEEK_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: opts.model || env.DEEPSEEK_MODEL || 'deepseek-chat',
        messages,
        temperature: opts.temperature ?? 0.4,
        max_tokens: opts.max_tokens ?? 2048,
        stream: false,
      }),
      signal: AbortSignal.timeout(opts.timeout_ms ?? 25000),
    },
    { retries: 2 }
  );
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`DeepSeek API HTTP ${response.status}: ${body.slice(0, 160)}`);
  }
  const data = await response.json();
  const content = data.choices?.[0]?.message?.content?.trim();
  if (!content) throw new Error('DeepSeek API returned an empty response');
  return content;
}

async function tgSend(env, chatId, text) {
  for (const part of splitTelegramHtml(text, 4000)) {
    const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text: part,
        parse_mode: 'HTML',
        disable_web_page_preview: true,
      }),
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) {
      const stripped = part.replace(/<[^>]+>/g, '');
      await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, text: stripped }),
        signal: AbortSignal.timeout(10000),
      });
    }
  }
}

function escapeHtml(value) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
