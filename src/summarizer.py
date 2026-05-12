import os
import requests
from datetime import datetime, timezone, timedelta

from .config import DEEPSEEK_API_URL, DEEPSEEK_MODEL, DEEPSEEK_THINKING, HOURS_LOOKBACK

SYSTEM_PROMPT = (
    "你是一位 AI 应用方向的资深行业分析师,擅长在海量推文里挑出真正有用的「新工具 / 新玩法 / 新产品」"
    "整理成精炼的中文速报。读者是中文 AI 重度用户(Anthropic Claude 付费会员),只关心能上手用的东西,"
    "不关心学术论文、benchmark 数字、招聘动态。"
)

USER_TEMPLATE = """今天是 {date}。下面是过去 {window} 全球 AI 大 V 和官方账号在 X (Twitter) 上的推文,共 {n} 条。
请整理成「AI 应用速报」。

【内容筛选规则 — 极其重要】
A. **必须聚焦近期内容**: 只整理过去 7 天内发生的事。如果一条内容明显是 2024、2025 或更早的老新闻
   (如 "Claude 桌面应用上线"、"MCP 协议发布"、"Sonnet 3.5"、"Sonnet 4.5"、"Haiku 4.5" 这些已经是几个月到一年前的旧闻),
   **直接丢弃,不要写进速报**。判断不准时,优先丢弃,宁缺毋滥。
B. **重点话题**(优先收录):
   - 新发布的 AI 工具 / 应用 / 软件,可以马上去试的
   - 实战玩法、prompt 技巧、Agent 用法、新工作流
   - 让人眼前一亮的 demo、用例、创意
   - Anthropic 旗下产品 (Claude, Claude Code, MCP 生态等) 的所有新动态、新功能、新技巧 — 用户是付费会员,任何小更新都要写
C. **直接丢弃**(不要出现在输出里):
   - 学术论文、研究方法、benchmark 数字
   - 纯粹的人事变动、融资额、招聘
   - 已经发过几个月的旧产品 / 旧功能(见 A)

【输出结构】
按下面的板块组织(没有内容的板块整段省略,不要写"暂无内容"):

🅰️ Claude 产品更新详解 ← 重点章节,见下方"特别处理"
⭐ Anthropic 公司动态(@AnthropicAI / @darioamodei / @alexalbert__ / @AmandaAskell 的非产品类内容)
🚀 新发布 / 新产品(其它公司)
🛠️ 新工具 / 新玩法
💬 启发性观点(只挑真正有新意的,过滤鸡汤)
📌 其他值得一看

【🅰️ Claude 产品更新详解 — 特别处理】
来源: @claudeai 账号下,且明显是过去 7 天内的产品更新/新功能。
对每一条用下面三段式展开(必须三段都写,缺则用 "—"):

<b>{{功能名}}</b>
• 功能介绍: 一句话说清这是什么、做了什么改动
• 效果: 用户能拿它干什么、解决什么场景、和老版本/同类产品的差异
• 使用限制: 哪些档位可用 (Free / Pro / Max / Team / Enterprise / API)、哪些平台 (Web / Mac / iOS / Android / Claude Code)、是否有用量限制、是否需要等候名单
最后跟一行 <a href="原文URL">原文</a>

如果信息不全,基于上下文合理推测但标注 "(推测)"。
如果 @claudeai 这一周没有可写的产品更新,这个章节整段省略。
"Sonnet 4.5 / Haiku 4.5 / 桌面应用上线 / MCP 协议发布"等老闻,即使 @claudeai 在置顶展示也**严禁**写进这个章节。

【格式要求】
1. 全部中文,英文专有名词原样保留
2. 普通条目: 1-2 行,产品/公司/模型名加粗 <b>...</b>,结尾附 <a href="原文URL">原文</a>
   (URL 必须是有效非空的 https 链接,从"链接:"字段取)
3. 同一话题多人转发只保留一条最权威的
4. 仅用 Telegram HTML 标签: <b> <i> <a href="...">,不要 markdown,不要 ```
5. 开头第一行: <b>📰 AI 应用速报 · {date}</b>
6. 结尾另起一段: <i>今日关键词:xxx / xxx / xxx</i>(3-5 个)
7. 总长度 ≤ 4500 字(给 Claude 章节多留些空间)

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
