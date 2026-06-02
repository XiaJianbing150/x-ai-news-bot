// Cloudflare Worker: AI 早报 Bot 的 Telegram 双向对话入口
//
// 路由:
//   POST /tg        Telegram webhook 回调
//   GET  /          健康检查
//
// 支持指令:
//   /news           触发一次 GitHub Actions 跑早报(立刻入队)
//   /search 关键字  在最近 7 天 archive/*.md 里检索并让模型回答
//   /clear          清空当前对话历史
//   /help           显示帮助
//   其它任意文本    多轮闲聊(带历史上下文)
//
// secrets (用 wrangler secret put 注入,不要写进代码):
//   TELEGRAM_BOT_TOKEN
//   TELEGRAM_WEBHOOK_SECRET     注册 webhook 时一并提交,防伪造
//   DEEPSEEK_API_KEY
//   GITHUB_TOKEN                fine-grained PAT,scope = Actions:write + Contents:read

const SYSTEM_PROMPT =
  "你是一个聚焦 Anthropic Claude 和 OpenAI ChatGPT/GPT 的中文 AI 助手。" +
  "对话风格简洁、信息密度高。除非用户问到,不主动谈其它公司的产品。" +
  "回复使用 Telegram HTML(<b> <i> <a>),不要用 markdown。";

const HELP_TEXT =
  "<b>AI 早报 Bot 指令</b>\n\n" +
  "/news     立即触发跑一次今日早报(2-5 分钟后推送)\n" +
  "/search 关键字  在最近 7 天早报存档里检索并回答\n" +
  "/clear    清空当前对话上下文\n" +
  "/help     显示本帮助\n\n" +
  "直接发消息即可多轮聊天,默认带 10 轮历史。";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/") {
      return new Response("ai-news-bot worker ok", { status: 200 });
    }

    if (request.method !== "POST" || url.pathname !== "/tg") {
      return new Response("not found", { status: 404 });
    }

    // 校验 Telegram secret_token,挡住伪造请求
    const got = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (got !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad json", { status: 400 });
    }

    // 异步处理,先返回 200,避免 Telegram 重试
    ctx.waitUntil(handleUpdate(update, env).catch((e) => console.error(e)));
    return new Response("ok", { status: 200 });
  },
};

async function handleUpdate(update, env) {
  const msg = update.message || update.edited_message;
  if (!msg || !msg.text) return;

  const chatId = String(msg.chat.id);
  const text = msg.text.trim();

  // 只允许授权 chat,防止白嫖
  if (chatId !== String(env.ALLOWED_CHAT_ID)) {
    await tgSend(env, chatId, "未授权的对话,请联系机器人管理员。");
    return;
  }

  // 命令分发
  if (text === "/start" || text === "/help") {
    return tgSend(env, chatId, HELP_TEXT);
  }
  if (text === "/clear") {
    await env.CHAT_HISTORY.delete(`chat:${chatId}`);
    return tgSend(env, chatId, "已清空对话上下文。");
  }
  if (text === "/news") {
    return cmdNews(env, chatId);
  }
  if (text.startsWith("/search")) {
    const q = text.slice(7).trim();
    if (!q) return tgSend(env, chatId, "用法: <code>/search 关键字</code>");
    return cmdSearch(env, chatId, q);
  }

  // 默认:多轮闲聊
  return cmdChat(env, chatId, text);
}

// --------- /news: 触发 GitHub Actions ---------
async function cmdNews(env, chatId) {
  const [owner, repo] = env.GITHUB_REPO.split("/");
  const ref = "main";
  const r = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "ai-news-bot-worker",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref, inputs: { hours_lookback: "168" } }),
    }
  );
  if (r.status === 204) {
    return tgSend(
      env,
      chatId,
      "已触发早报任务,通常 2-5 分钟后推送。\n查看进度: https://github.com/" +
        env.GITHUB_REPO +
        "/actions"
    );
  }
  const body = await r.text();
  return tgSend(
    env,
    chatId,
    `触发失败 HTTP ${r.status}\n<code>${escapeHtml(body.slice(0, 300))}</code>`
  );
}

// --------- /search: 查归档 ---------
async function cmdSearch(env, chatId, query) {
  await tgSend(env, chatId, `🔎 正在检索最近 7 天早报: <i>${escapeHtml(query)}</i>`);
  const [owner, repo] = env.GITHUB_REPO.split("/");
  // 列出 archive/ 目录里的 md 文件
  const listR = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/contents/archive`,
    {
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "ai-news-bot-worker",
      },
    }
  );
  if (!listR.ok) {
    return tgSend(env, chatId, `读取 archive 失败: HTTP ${listR.status}`);
  }
  const files = (await listR.json())
    .filter((f) => f.name.endsWith(".md"))
    .sort((a, b) => b.name.localeCompare(a.name));

  // 拉每个文件内容
  const docs = [];
  for (const f of files) {
    const r = await fetch(f.download_url);
    if (!r.ok) continue;
    const body = await r.text();
    docs.push(`## ${f.name}\n${body}`);
  }
  if (!docs.length) return tgSend(env, chatId, "archive 是空的,先跑一次 /news");

  const corpus = docs.join("\n\n---\n\n").slice(0, 50000);
  const answer = await deepseekChat(env, [
    {
      role: "system",
      content:
        "下面是最近 7 天的 AI 早报存档(Markdown)。" +
        "请根据它们准确回答用户问题,引用具体日期。如果找不到就明说,不要编。" +
        "回复用 Telegram HTML,不要 markdown。",
    },
    { role: "user", content: `存档:\n\n${corpus}\n\n问题:${query}` },
  ]);
  return tgSend(env, chatId, answer);
}

// --------- 默认对话 ---------
async function cmdChat(env, chatId, text) {
  const key = `chat:${chatId}`;
  const turns = parseInt(env.HISTORY_TURNS || "10", 10);
  let history = [];
  try {
    const raw = await env.CHAT_HISTORY.get(key);
    if (raw) history = JSON.parse(raw);
  } catch {}

  history.push({ role: "user", content: text });
  const messages = [{ role: "system", content: SYSTEM_PROMPT }, ...history];
  const reply = await deepseekChat(env, messages);
  history.push({ role: "assistant", content: reply });

  // 保留最近 N 轮 (N*2 条消息)
  if (history.length > turns * 2) history = history.slice(-turns * 2);
  // KV 写入,7 天 TTL,长期不聊就自动遗忘
  await env.CHAT_HISTORY.put(key, JSON.stringify(history), {
    expirationTtl: 60 * 60 * 24 * 7,
  });

  return tgSend(env, chatId, reply);
}

// --------- DeepSeek ---------
async function deepseekChat(env, messages) {
  const r = await fetch("https://api.deepseek.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.DEEPSEEK_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: env.DEEPSEEK_MODEL || "deepseek-v4-flash",
      messages,
      temperature: 0.4,
      max_tokens: 2048,
      stream: false,
    }),
  });
  if (!r.ok) {
    const t = await r.text();
    return `DeepSeek API 出错 HTTP ${r.status}\n${t.slice(0, 200)}`;
  }
  const data = await r.json();
  return (data.choices?.[0]?.message?.content || "").trim() || "(空回复)";
}

// --------- Telegram ---------
async function tgSend(env, chatId, text) {
  // Telegram 单条上限 4096,拆分
  const chunks = [];
  let remain = text;
  while (remain.length > 4000) {
    let cut = remain.lastIndexOf("\n", 4000);
    if (cut < 2000) cut = 4000;
    chunks.push(remain.slice(0, cut));
    remain = remain.slice(cut);
  }
  if (remain) chunks.push(remain);

  for (const part of chunks) {
    const r = await fetch(
      `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: chatId,
          text: part,
          parse_mode: "HTML",
          disable_web_page_preview: true,
        }),
      }
    );
    if (!r.ok) {
      // HTML 解析失败时退回纯文本,避免吞消息
      const stripped = part.replace(/<[^>]+>/g, "");
      await fetch(
        `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chat_id: chatId, text: stripped }),
        }
      );
    }
  }
}

function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
