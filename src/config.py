import os

# 关注的 X (Twitter) AI 大 V / 机构 (handle, 不带 @)
BLOGGERS = [
    # 大佬
    "sama",            # Sam Altman (OpenAI CEO)
    "karpathy",        # Andrej Karpathy
    "ylecun",          # Yann LeCun (Meta)
    "AndrewYNg",       # Andrew Ng
    "demishassabis",   # Demis Hassabis (DeepMind CEO)
    "drjimfan",        # Jim Fan (NVIDIA)
    "ilyasut",         # Ilya Sutskever
    # 机构
    "OpenAI",
    "AnthropicAI",
    "claudeai",        # Claude / Anthropic 产品账号(测试探针:近期肯定有推)
    "GoogleDeepMind",
    "GoogleAI",
    "MistralAI",
    # 资讯/聚合
    "_akhaliq",
    "rohanpaul_ai",
    "omarsar0",
    # AI 应用 / 工程实践
    "simonw",          # Simon Willison - LLM 实战与工具
    "swyx",            # Shawn Wang - AI Engineer / Latent Space
    "mckaywrigley",    # Mckay Wrigley - build-in-public AI 应用
    "LangChainAI",     # LangChain 官方
    "perplexity_ai",   # Perplexity 官方
    # AI 应用热门讨论 (top 10)
    "emollick",        # Ethan Mollick - Wharton 教授,每日 AI 用例
    "levelsio",        # Pieter Levels - 独立 AI/SaaS 创业
    "gdb",             # Greg Brockman - OpenAI 总裁
    "alexalbert__",    # Alex Albert - Anthropic DevRel
    "hwchase17",       # Harrison Chase - LangChain 创始人
    "HamelHusain",     # AI 评测 / RAG / 工程深度
    "rauchg",          # Guillermo Rauch - Vercel / v0
    "huggingface",     # HuggingFace 官方
    "deepseek_ai",     # DeepSeek 官方
    "cursor_ai",       # Cursor 官方
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
