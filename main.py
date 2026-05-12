import sys
import traceback
from datetime import datetime, timezone, timedelta

from src.fetcher import (
    fetch_all_tweets,
    fetch_arxiv_papers,
    fetch_github_trending,
    format_trending_block,
    fetch_openai_blog,
    fetch_anthropic_news,
)
from src.summarizer import generate_morning_report
from src.telegram import send_to_telegram


def _bj_now_str():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def main():
    print(f"=== AI 早报 Bot 启动  ({_bj_now_str()} 北京时间) ===")

    # 1. 抓推文 + 官博 (官博是产品更新的权威源,优先级高于 X)
    print("\n[1/3] 抓取数据源...")
    items = []

    print("  --- X 推文 ---")
    try:
        tweets = fetch_all_tweets()
        items.extend(tweets)
    except Exception as e:
        traceback.print_exc()
        send_to_telegram(f"⚠️ AI 早报失败: 抓取 X 推文异常\n<code>{str(e)[:500]}</code>")
        sys.exit(1)

    print("  --- Anthropic 官博 ---")
    try:
        items.extend(fetch_anthropic_news())
    except Exception as e:
        print(f"  Anthropic news 失败(忽略): {e!r}")

    print("  --- OpenAI 官博 ---")
    try:
        items.extend(fetch_openai_blog())
    except Exception as e:
        print(f"  OpenAI blog 失败(忽略): {e!r}")

    print(f"\n共抓到 {len(items)} 条内容")
    if not items:
        send_to_telegram(
            "⚠️ <b>AI 早报</b>\n所有数据源都失败了,稍后会自动重试。"
        )
        sys.exit(0)

    # 3. 总结
    print("\n[2/3] 调用 DeepSeek 生成早报...")
    try:
        report = generate_morning_report(items)
    except Exception as e:
        traceback.print_exc()
        send_to_telegram(f"⚠️ AI 早报失败: DeepSeek 调用异常\n<code>{str(e)[:500]}</code>")
        sys.exit(1)
    print(f"早报长度: {len(report)} 字符")
    print("---- 预览 ----")
    print(report[:500])
    print("--------------")

    # 3.5 抓 GitHub Trending top5,直接拼到末尾(不过 LLM,精确不走样)
    print("\n抓取 GitHub Trending Top 5...")
    try:
        trending = fetch_github_trending(top_n=5, since="daily")
        trending_block = format_trending_block(trending)
        if trending_block:
            report = report + "\n\n" + trending_block
            print(f"  附加 {len(trending)} 个 trending 仓库,总长度 {len(report)} 字符")
    except Exception as e:
        print(f"GitHub Trending 抓取失败(忽略): {e!r}")

    # 4. 推送
    print("\n[3/3] 推送到 Telegram...")
    try:
        send_to_telegram(report)
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
    print("\n✅ 完成")


if __name__ == "__main__":
    main()
