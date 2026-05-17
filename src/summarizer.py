import os
import requests
from datetime import datetime, timezone, timedelta

from .config import DEEPSEEK_API_URL, DEEPSEEK_MODEL, DEEPSEEK_THINKING, HOURS_LOOKBACK

SYSTEM_PROMPT = (
    "你是 Anthropic Claude 和 OpenAI ChatGPT/GPT API 两家产品的资深观察者。"
    "读者只关心这两家公司近期发布的新功能、可以马上试的新玩法,以及高手分享的实操技巧。"
    "其它 AI 公司(Google / Meta / DeepSeek / Mistral / Perplexity 等)的产品/动态一律不出现。"
)

USER_TEMPLATE = """今天是 {date}。下面是过去 {window} 内容,共 {n} 条。

【来源 & 时效】每条前会有 tag 标注:
- [官博·权威·近7天]: 来自 Anthropic / OpenAI 官方博客,或 Claude Code 官方版本说明,**确保是近 7 天发布,内容准确,优先采用**。Claude Code 版本说明里会有 agent view / /goal / /scroll-speed 这类小功能,务必全部写进 🅰️ Claude 详解
- [X·RSS·有日期]: 来自 X 推文,RSS 通道,**已按 7 天窗口过滤,内容时效可信**
- [X·Jina·时效未知,可能是数月前的pinned推文]: 来自 X 但 Jina 抓取,**没有时间戳。X.com 对未登录访问只返回 pinned/featured,实测内容多为 2025-09/10 的老闻。**对这类内容请极度警惕,只有当内容明显是新的(新功能/未发布过的产品/有时间用语如"今天""this week")才能用,任何看起来像已发布的产品(Sonnet 4.5、Haiku 4.5、Opus 4.x、Claude 桌面应用、MCP 协议等)一律丢弃

请整理成「Anthropic & OpenAI 应用速报」。

【硬性筛选规则 — 必须严格遵守】
A. **只保留 Anthropic / OpenAI 强相关内容**。包括:
   - Claude / Claude Code / Anthropic API / Anthropic 旗下任何产品的新功能、新模型、新限额、新定价
   - ChatGPT / GPT-x / Codex / OpenAI API / OpenAI 旗下任何产品的同上
   - 高频使用者分享的、关于上面两家产品的实战技巧、prompt、工作流、demo
   - Anthropic / OpenAI 公司层面的重要动态(安全计划、合作、定价策略)
B. **直接丢弃**(完全不要出现):
   - 其它公司的产品: Google / Meta / DeepSeek / Mistral / Perplexity / Cursor / LangChain / Vercel / Thinking Machines 等
     即使在推文里被提到也别写(除非是和 Claude/GPT 互操作的实操技巧)
   - 学术论文、benchmark、研究方法
   - 招聘、融资、人事变动
   - 老新闻: Sonnet 4.5 / Haiku 4.5 / MCP 协议发布 / Claude 桌面应用上线 / GPT-4 系列旧闻 等 2024-2025 老新闻
   - 鸡汤、口号式发言、没具体信息的预告
C. 判断不准时,优先丢弃。宁缺毋滥 — 输出短没关系,不要凑数。

【输出结构】(板块若无内容,整段省略,不写"暂无")

🅰️ Claude 产品更新详解
🅾️ OpenAI 产品更新详解
⭐ 公司动态(Anthropic / OpenAI 非产品类: 安全计划、合作伙伴、政策等)
🧰 Claude / GPT 实战玩法(simonw / karpathy / alexalbert__ 等分享的、关于这两家产品的具体操作)
📌 其他值得一看(必须仍是 Anthropic/OpenAI 相关)

【🅰️ Claude / 🅾️ OpenAI 产品更新详解 — 特别处理】
**优先收录**所有 user="Anthropic官博" / "OpenAI官博" / "ClaudeCode官方" 的条目(这三个权威源近一周的所有产品/公告/版本说明),X 推文作为补充。
对这两个章节里每一条,用下面三段式展开(三段都要,无信息则填 "—"):

<b>{{功能名}}</b>
• 功能介绍: 一句话说清是什么、做了什么改动
• 效果: 用户拿它干什么、解决什么场景、和老版本/对家的差异
• 使用限制: 档位 (Free / Pro / Max / Team / Enterprise / API)、平台 (Web / Mac / iOS / Android / Claude Code / ChatGPT App / Codex CLI)、用量上限、是否需要等候名单
<a href="原文URL">原文</a>

信息不全时基于上下文合理推测并标注 "(推测)"。
若该公司这一周没有产品更新,对应章节整段省略。

【实战玩法章节要求】
- 必须是「我做了 X,结果 Y」式的具体操作,不要泛泛而谈
- 必须明确关联 Claude 或 GPT 中的至少一个
- 必须是过去 7 天内的新分享

【格式】
1. 中文,英文专有名词原样保留
2. 产品/模型/公司名加粗 <b>...</b>
3. 每条结尾附 <a href="原文URL">原文</a>,URL 必须是从"链接:"字段取的有效 https
4. 同一话题多人转只保留一条
5. 仅用 Telegram HTML 标签: <b> <i> <a href="...">,不要 markdown,不要 ```
6. 开头第一行: <b>📰 Anthropic & OpenAI 速报 · {date}</b>
7. 结尾另起一段: <i>今日关键词:xxx / xxx / xxx</i>(3-5 个,仅限 Anthropic/OpenAI 相关概念)
8. 总长度 ≤ 4500 字。内容不够就短,不要凑

【推文原文】
{tweets_block}
"""


def _build_tweets_block(tweets):
    lines = []
    for i, t in enumerate(tweets, 1):
        text = t["text"].replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:500] + "..."
        # 标注来源,提示 LLM 区分权威 vs 时效不确定
        src = t.get("source", "")
        if src in ("openai-blog", "anthropic-news"):
            tag = "[官博·权威·近7天]"
        elif src == "rss":
            tag = "[X·RSS·有日期]"
        elif src == "jina":
            tag = "[X·Jina·时效未知,可能是数月前的pinned推文]"
        else:
            tag = ""
        lines.append(f"{i}. @{t['user']} {tag}: {text}\n   链接: {t['link']}")
    return "\n\n".join(lines)


def generate_morning_report(tweets):
    api_key = os.environ["DEEPSEEK_API_KEY"]

    # 北京时间日期
    bj_now = datetime.now(timezone.utc) + timedelta(hours=8)
    date_str = bj_now.strftime("%Y-%m-%d %A")

    if HOURS_LOOKBACK % 24 == 0 and HOURS_LOOKBACK >= 24:
        window = f"{HOURS_LOOKBACK // 24} 天"
    else:
        window = f"{HOURS_LOOKBACK} 小时"

    user_msg = USER_TEMPLATE.format(
        window=window,
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
        "max_tokens": 8192,
        "stream": False,
    }
    # v4-pro / reasoner 类模型开启深度思考
    use_thinking = DEEPSEEK_THINKING and ("pro" in DEEPSEEK_MODEL or "reasoner" in DEEPSEEK_MODEL)
    if use_thinking:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "high"
        # thinking 模型 temperature 通常忽略,删掉避免警告
        body.pop("temperature", None)
    print(f"  model={DEEPSEEK_MODEL}  thinking={'on (effort=high)' if use_thinking else 'off'}")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    r = requests.post(DEEPSEEK_API_URL, json=body, headers=headers, timeout=600)
    if not r.ok:
        raise RuntimeError(f"DeepSeek API error {r.status_code}: {r.text[:500]}")
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()
