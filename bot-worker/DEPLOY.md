# Bot Worker 部署清单

把 Telegram bot 变成双向对话入口。你只需要按下面顺序操作一遍,以后不用碰。

## 你需要操作的步骤

### 0. 准备一台能装 npm 的机器(本机也行)

```
node -v   # ≥ 18
npm -v
```

### 1. 注册 Cloudflare(免费,只填邮箱)

打开 https://dash.cloudflare.com/sign-up 注册。不需要绑卡。

### 2. 装并登录 wrangler

```
npm install -g wrangler
wrangler login    # 浏览器跳转授权一次
```

### 3. 进 worker 目录

```
cd bot-worker
npm install
```

### 4. 创建 KV(存对话历史)

```
wrangler kv namespace create CHAT_HISTORY
```

输出会有一段 `id = "xxxxx"`,**复制 id**,粘到 `wrangler.toml` 里替换 `REPLACE_WITH_KV_ID`。

### 5. 生成一个 GitHub Personal Access Token

打开 https://github.com/settings/personal-access-tokens/new ,选 fine-grained:

- Repository access: 只选 `xiajianbing150/x-ai-news-bot`
- Permissions:
  - **Actions: Read and write**(触发 workflow)
  - **Contents: Read**(读 archive/)
- 有效期建议 90 天或自定义

生成后复制 token,**只显示一次**。

### 6. 生成一个 webhook 随机密钥

随便一段 40 字符随机串都行,比如:

```
openssl rand -hex 20
```

### 7. 把 4 个 secret 注入 Worker

```
wrangler secret put TELEGRAM_BOT_TOKEN
# 粘 7683245442:AAFvXz8joj98m3B7bDkc0WcnceVeGumdUIA (或新 token)

wrangler secret put DEEPSEEK_API_KEY
# 粘你的 DeepSeek key

wrangler secret put GITHUB_TOKEN
# 粘第 5 步的 PAT

wrangler secret put TELEGRAM_WEBHOOK_SECRET
# 粘第 6 步的随机串
```

### 8. 部署

```
wrangler deploy
```

输出最后会有一行:
```
Published ai-news-bot (x.xx sec)
  https://ai-news-bot.<你的用户名>.workers.dev
```
**复制这个 URL**。

### 9. 把 webhook 注册到 Telegram(一次性)

把下面命令里的 `<TOKEN>`、`<WORKER_URL>`、`<SECRET>` 替换后执行:

```
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=<WORKER_URL>/tg" \
  -d "secret_token=<SECRET>"
```

返回 `{"ok":true,...}` 就成功了。

### 10. 在 BotFather 里设置命令菜单(可选,体验好)

打开 @BotFather,发 `/setcommands`,选你的 bot,粘:

```
news - 立即触发跑一次今日早报
search - 在最近 7 天早报存档里检索
query - 对早报里某条内容做细节展开
clear - 清空对话上下文
help - 显示帮助
```

## 验证

打开你的 bot,发 `/help`,看到帮助说明就成了。再试:

- `你好` → 闲聊
- `/news` → 会回复"已触发",2-5 分钟后早报到达
- `/search Claude 新功能` → 翻最近 7 天 archive 回答
- `/clear` → 清上下文

## 排查

```
wrangler tail            # 实时看 Worker 日志
```

webhook 状态:
```
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

如果想暂停 bot:
```
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

## 改动后重新部署

只要改了 `src/index.js`,跑:
```
wrangler deploy
```
立刻生效,不用动 webhook。
