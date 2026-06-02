// Cloudflare Worker: AI 早报 Bot 的 Telegram 双向对话入口
//
// 路由:
//   POST /tg        Telegram webhook 回调
//   GET  /          健康检查
//
// 支持指令:
//   /news           触发一次 GitHub Actions 跑早报(立刻入队)
//   /search 关键字  在最近 7 天 archive/*.md 里检索并让模型回答
//   /query 主题     用强模型对某个主题/产品/项目做结构化深度介绍
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
  "/query 内容     对早报里某条内容做细节展开(读最近 3 天存档当上下文)\n" +
  "                例: <code>/query Claude Code Agent View</code>\n" +
  "                    <code>/query mattpocock/skills</code>\n" +
  "/clear    清空当前对话上下文\n" +
  "/help     显示本帮助\n\n" +
  "直接发消息即可多轮聊天,默认带 10 轮历史。";

const QUERY_SYSTEM_PROMPT =
  "你是 Anthropic Claude 和 OpenAI ChatGPT/GPT 的资深观察者。" +
  "用户读完每天的 AI 早报后,想就早报里的某条内容做细节补充。\n\n" +
  "你的任务:\n" +
  "1. 先在「早报存档」里精确定位用户问的那条内容(可能是某个产品功能 / GitHub 项目 / 玩法)\n" +
  "2. 把那条内容从「短句速报」展开成「可直接拿来用的深度解读」\n" +
  "3. 保留早报里的原始链接,允许基于常识补充更多细节(标注「(推测)」)\n" +
  "4. 如果早报里完全没出现这个话题,先说明「早报中未提及」,再用常识做一段简短介绍\n\n" +
  "【输出结构】(每段都要,无信息就写「资料未提及」)\n" +
  "<b>📌 一句话总结</b>\n" +
  "<b>🎯 它是什么</b>:背景、定位、解决什么问题\n" +
  "<b>⚙️ 核心功能 / 改动点</b>:列 3-7 条,每条带一句话说明\n" +
  "<b>🛠 怎么用 / 上手</b>:安装命令、入口、关键配置、最小示例\n" +
  "<b>💡 典型场景</b>:2-4 个具体案例\n" +
  "<b>⚠️ 限制与注意</b>:档位 (Free/Pro/Max/Team/API) / 平台 / 配额 / 已知坑\n" +
  "<b>🆚 对比与替代</b>:同赛道选项,差异在哪\n" +
  "<b>🔗 进一步阅读</b>:官方链接(优先用早报里的原文 URL)\n\n" +
  "【风格】\n" +
  "- 中文为主,英文专有名词原样保留\n" +
  "- 加粗用 <b>...</b>,链接用 <a href=\"URL\">文字</a>,不要 markdown,不要 ```\n" +
  "- 直接进内容,别说「这是一篇关于...的介绍」\n" +
  "- 拿不准的事实标注「(推测)」,不要瞎编 URL\n" +
  "- 总长度 3500 字以内,密度高过长度";

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
  if (text.startsWith("/query")) {
    const q = text.slice(6).trim();
    if (!q)
      return tgSend(
        env,
        chatId,
        "用法: <code>/query 主题</code>\n例: <code>/query mattpocock/skills</code>"
      );
    return cmdQuery(env, chatId, q);
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

// --------- /query: 对早报里某条内容做细节展开 ---------
async function cmdQuery(env, chatId, topic) {
  await tgSend(
    env,
    chatId,
    `🧠 正在基于最近早报展开: <i>${escapeHtml(topic)}</i>\n通常 10-30 秒,请稍候。`
  );
  const [owner, repo] = env.GITHUB_REPO.split("/");

  // 1. 拉最近 N 天早报存档当上下文
  let archiveText = "";
  try {
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
    if (listR.ok) {
      const files = (await listR.json())
        .filter((f) => f.name.endsWith(".md"))
        .sort((a, b) => b.name.localeCompare(a.name))
        .slice(0, parseInt(env.QUERY_ARCHIVE_DAYS || "3", 10));
      const docs = [];
      for (const f of files) {
        const r = await fetch(f.download_url);
        if (r.ok) docs.push(`## 早报 ${f.name}\n${await r.text()}`);
      }
      archiveText = docs.join("\n\n---\n\n").slice(0, 40000);
    }
  } catch (e) {
    console.error("query archive fetch failed", e);
  }

  // 2. 如果话题像 owner/repo,顺手抓 GitHub README,补充原始资料
  let repoText = "";
  const m = topic.match(/([\w.-]+)\/([\w.-]+)/);
  if (m) {
    try {
      const rr = await fetch(
        `https://r.jina.ai/https://github.com/${m[1]}/${m[2]}`,
        { headers: { Accept: "text/markdown" } }
      );
      if (rr.ok) {
        const t = await rr.text();
        const i = t.indexOf("Markdown Content:");
        repoText = (i > 0 ? t.slice(i) : t).slice(0, 10000);
      }
    } catch {}
  }

  const parts = [];
  if (archiveText) parts.push(`【最近早报存档】\n${archiveText}`);
  if (repoText) parts.push(`【该项目的 GitHub README 摘要】\n${repoText}`);
  parts.push(`【用户想展开的内容】\n${topic}`);
  parts.push("请基于早报里这条内容,按系统提示输出结构化深度解读。");

  const answer = await deepseekChat(
    env,
    [
      { role: "system", content: QUERY_SYSTEM_PROMPT },
      { role: "user", content: parts.join("\n\n") },
    ],
    {
      model: env.QUERY_MODEL || "deepseek-v4-pro",
      temperature: 0.3,
      max_tokens: 4096,
    }
  );
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
async function deepseekChat(env, messages, opts = {}) {
  const r = await fetch("https://api.deepseek.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.DEEPSEEK_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: opts.model || env.DEEPSEEK_MODEL || "deepseek-v4-flash",
      messages,
      temperature: opts.temperature ?? 0.4,
      max_tokens: opts.max_tokens ?? 2048,
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
