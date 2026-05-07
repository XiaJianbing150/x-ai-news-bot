import os
import requests
from datetime import datetime, timezone, timedelta

from .config import DEEPSEEK_API_URL, DEEPSEEK_MODEL

SYSTEM_PROMPT = (
    "你是资深 AI 行业分析师,擅长把英文推文整理成精炼、干货十足的中文早报。"
    "你的读者是中文 AI 从业者,他们只看你这一份早报就能掌握昨夜今晨全球 AI 圈大事。"
)

USER_TEMPLATE = """下面是过去 {hours} 小时全球 AI 大佬和机构在 X (Twitter) 上的推文,共 {n} 条。请整理成今日「AI 早报」。

【输出要求】
1. 全部用中文,可保留必要的英文专有名词
2. 按主题分组(只保留有内容的分组,无内容的分组不要出现):
   🚀 模型 / 产品发布
   🧪 论文 / 研究
   💼 行业 / 融资 / 人事
   💬 大佬观点
   🛠️ 工具 / 开源
   📌 其他值得一看
3. 每条 1-2 行,要点先行,把数字、模型名、公司名加粗 <b>...</b>
4. 重要项后面附原文链接 <a href="URL">原文</a>(同主题多条尽量去重)
5. 整体使用 Telegram HTML 格式,只能用 <b> <i> <a href> 三种标签,不要 markdown,不要 ```
6. 开头第一行: <b>📰 AI 早报 · {date}</b>
7. 结尾另起一段: <i>今日关键词:xxx / xxx / xxx</i>(3-5 个最热关键词)
8. 总长度控制在 3500 字以内

【推文原文】
{tweets_block}
"""


def _build_tweets_block(tweets):
    lines = []
    for i, t in enumerate(tweets, 1):
        text = t["text"].replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:500] + "..."
        lines.append(f"{i}. @{t['user']}: {text}\n   链接: {t['link']}")
    return "\n\n".join(lines)


def generate_morning_report(tweets):
    api_key = os.environ["DEEPSEEK_API_KEY"]

    # 北京时间日期
    bj_now = datetime.now(timezone.utc) + timedelta(hours=8)
    date_str = bj_now.strftime("%Y-%m-%d %A")

    user_msg = USER_TEMPLATE.format(
        hours=os.environ.get("HOURS_LOOKBACK", "24"),
        n=len(tweets),
        date=date_str,
        tweets_block=_build_tweets_block(tweets),
    )

    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    r = requests.post(DEEPSEEK_API_URL, json=body, headers=headers, timeout=180)
    if not r.ok:
        raise RuntimeError(f"DeepSeek API error {r.status_code}: {r.text[:500]}")
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()
