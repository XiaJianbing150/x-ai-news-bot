import os

# 关注的 X (Twitter) AI 大 V / 机构账号 (handle, 不带 @)
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
    "GoogleDeepMind",
    "GoogleAI",
    "MistralAI",
    # 资讯/聚合
    "_akhaliq",        # AK - 论文搬运工
    "rohanpaul_ai",
    "omarsar0",        # Elvis (DAIR.AI)
]

# RSSHub 公共实例 - 按顺序 fallback,失败自动切下一个
RSSHUB_INSTANCES = [
    "https://rsshub.app",
    "https://rsshub.rssforever.com",
    "https://rss.shab.fun",
    "https://rsshub.pseudoyu.com",
]

HOURS_LOOKBACK = int(os.environ.get("HOURS_LOOKBACK", "24"))
MAX_TWEETS_PER_BLOGGER = int(os.environ.get("MAX_TWEETS_PER_BLOGGER", "5"))

ZHIPU_MODEL = os.environ.get("ZHIPU_MODEL", "glm-4-plus")
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

REQUEST_TIMEOUT = 25
