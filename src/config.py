import os

# 关注的 X (Twitter) AI 大 V / 机构 (handle, 不带 @)
# 强约束: 只订阅 Anthropic 和 OpenAI 强相关账号 + 两个高频使用这两家产品的实战派
# 内容偏好: 最新、可操作的产品更新和实战玩法,其它 AI 公司/通用资讯一律不要
BLOGGERS = [
    # 🅰️ Anthropic 系
    # 注意: AnthropicAI / claudeai 在 SKIP_X_ACCOUNTS 里,因为 X 抓取只能拿到 pinned 老闻,
    # 同样内容会从官博更准更新地拿到
    "AnthropicAI",     # Anthropic 官方
    "claudeai",        # Claude 产品账号
    "darioamodei",     # Dario Amodei - CEO
    "alexalbert__",    # Alex Albert - DevRel,常发 Claude 实战技巧
    "AmandaAskell",    # Amanda Askell - Claude 行为/性格

    # 🅾️ OpenAI 系
    "OpenAI",          # OpenAI 官方 (同样在 SKIP_X_ACCOUNTS)
    "sama",            # Sam Altman - CEO
    "gdb",             # Greg Brockman - 总裁

    # 🧰 重度使用者(只挑实操、上手向)
    "simonw",          # Simon Willison - LLM/Claude/GPT 详尽实战博客
    "karpathy",        # Andrej Karpathy - 高密度实操观察
]

# 这几个账号跳过 X 抓取,因为 X.com 对未登录访问只返回 pinned + featured 老闻
# (Jina 拿到的是几个月前的 Sonnet 4.5 / Haiku 4.5 / MCP 等已发布产品)。
# 这些公司的产品更新有更权威、更新的官博源 (fetch_anthropic_news / fetch_openai_blog)。
SKIP_X_ACCOUNTS = {"AnthropicAI", "claudeai", "OpenAI"}

# 抓推文模板 - 按顺序 fallback,失败自动切下一个。
# 2026 年公共 RSSHub 对 Twitter 普遍 503,所以优先 Nitter 系镜像。
# {user} 会被替换成 handle。
TWITTER_FEED_TEMPLATES = [
    # Nitter 系(2026 年仍存活的镜像)
    "https://xcancel.com/{user}/rss",
    "https://nitter.privacydev.net/{user}/rss",
    "https://nitter.poast.org/{user}/rss",
    "https://nitter.tiekoetter.com/{user}/rss",
    "https://nitter.space/{user}/rss",
    # RSSHub 公共实例(后备)
    "https://rsshub.app/twitter/user/{user}",
    "https://rsshub.rssforever.com/twitter/user/{user}",
    "https://rsshub.pseudoyu.com/twitter/user/{user}",
]

# 终极兜底:Jina Reader 把 x.com 页面渲染成 markdown,稳定但无逐条时间戳
# 设 JINA_API_KEY (可选) 提升速率限制
JINA_READER_URL = "https://r.jina.ai/https://x.com/{user}"

# 兜底:即使 Twitter 全挂,也能从 arXiv 抓到当日 AI 论文,保证早报有内容
ARXIV_FEEDS = [
    "http://export.arxiv.org/rss/cs.AI",
    "http://export.arxiv.org/rss/cs.CL",
    "http://export.arxiv.org/rss/cs.LG",
]
ARXIV_MAX_PAPERS = 8

HOURS_LOOKBACK = int(os.environ.get("HOURS_LOOKBACK", "168"))
MAX_TWEETS_PER_BLOGGER = int(os.environ.get("MAX_TWEETS_PER_BLOGGER", "5"))

DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
# 深度思考开关。2026-08-29 实测 v4-pro/v4-flash 开 thinking 会把推理独白
# 混进 content (开头/中段随机出现,无法可靠切割),甚至整单返回空。
# 提示词本身足够强,默认关闭;需要时设 DEEPSEEK_THINKING=1 重新开启。
DEEPSEEK_THINKING = os.environ.get("DEEPSEEK_THINKING", "0") in ("1", "true", "True")

REQUEST_TIMEOUT = 25
