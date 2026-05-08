import os

# 关注的 X (Twitter) AI 大 V / 机构 (handle, 不带 @)
# 关注重点: AI 应用 / 工具 / 新玩法 / Anthropic 产品 (用户是 Claude 付费会员)
BLOGGERS = [
    # ⭐ Anthropic 系(重点关注:用户是 Claude 会员)
    "AnthropicAI",     # Anthropic 官方
    "claudeai",        # Claude 产品账号
    "darioamodei",     # Dario Amodei - Anthropic CEO
    "alexalbert__",    # Alex Albert - Anthropic DevRel
    "AmandaAskell",    # Amanda Askell - Claude 行为/性格设计

    # 核心大佬观点(只挑高产、有新意的)
    "sama",            # Sam Altman (OpenAI CEO)
    "karpathy",        # Andrej Karpathy - "vibe coding"
    "demishassabis",   # Demis Hassabis (DeepMind CEO)
    "gdb",             # Greg Brockman (OpenAI 总裁)
    "drjimfan",        # Jim Fan (NVIDIA)

    # AI 应用 / 工具 / 新玩法 高产博主
    "simonw",          # Simon Willison - LLM 实战与工具
    "swyx",            # Shawn Wang - AI Engineer / Latent Space
    "mckaywrigley",    # Mckay Wrigley - build-in-public AI 应用
    "emollick",        # Ethan Mollick - 每日 AI 用例
    "levelsio",        # Pieter Levels - 独立 AI/SaaS
    "bilawalsidhu",    # Bilawal Sidhu - AI 创意工具/玩法
    "minchoi",         # Min Choi - AI demo / 新工具速报
    "bentossell",      # Ben Tossell - There's An AI For That
    "LinusEkenstam",   # Linus Ekenstam - AI 应用教程
    "hwchase17",       # Harrison Chase - LangChain
    "rauchg",          # Guillermo Rauch - Vercel / v0

    # 产品官方账号
    "OpenAI",
    "GoogleDeepMind",
    "huggingface",
    "perplexity_ai",
    "cursor_ai",
    "deepseek_ai",
    "LangChainAI",
]

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
# 是否启用深度思考(仅 v4-pro / reasoner 类模型支持,显著提升推理质量但更慢更贵)
DEEPSEEK_THINKING = os.environ.get("DEEPSEEK_THINKING", "1") not in ("0", "false", "False", "")

REQUEST_TIMEOUT = 25
