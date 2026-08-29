import sys
import traceback
from datetime import datetime, timezone, timedelta

from src.fetcher import (
    fetch_all_tweets,
    fetch_arxiv_papers,
    fetch_github_trending,
    format_trending_block,
    translate_trending_descriptions,
    fetch_openai_blog,
    fetch_anthropic_news,
    fetch_claude_code_releases,
)
from src.trending import enrich_trending
from src.summarizer import generate_morning_report
from src.text_utils import normalize_text
from src.notify import send_report, send_alert
from src.archive import save_archive_and_cleanup


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
        send_alert(f"⚠️ AI 早报失败: 抓取 X 推文异常\n<code>{str(e)[:500]}</code>")
        sys.exit(1)

    print("  --- Anthropic 官博 ---")
    try:
        items.extend(fetch_anthropic_news())
    except Exception as e:
        print(f"  Anthropic news 失败(忽略): {e!r}")

    print("  --- Claude Code 版本说明 ---")
    try:
        items.extend(fetch_claude_code_releases())
    except Exception as e:
        print(f"  Claude Code releases 失败(忽略): {e!r}")

    print("  --- OpenAI 官博 ---")
    try:
        items.extend(fetch_openai_blog())
    except Exception as e:
        print(f"  OpenAI blog 失败(忽略): {e!r}")

    print(f"\n共抓到 {len(items)} 条内容")
    if not items:
        send_alert(
            "⚠️ <b>AI 早报</b>\n所有数据源都失败了,稍后会自动重试。"
        )
        sys.exit(0)

    # 2. 总结
    print("\n[2/3] 调用 DeepSeek 生成早报...")
    try:
        report = generate_morning_report(items)
        if not report or not report.strip():
            raise RuntimeError("早报正文为空 (DeepSeek 返回空内容)")
    except Exception as e:
        traceback.print_exc()
        send_alert(f"⚠️ AI 早报失败: DeepSeek 调用异常\n<code>{str(e)[:500]}</code>")
        sys.exit(1)
    print(f"早报长度: {len(report)} 字符")
    print("---- 预览 ----")
    print(report[:500])
    print("--------------")

    # 3.5 抓 GitHub Trending 本周 top5,按 stars 增量降序,描述翻译成中文后拼到末尾
    print("\n抓取 GitHub Trending 本周 Top 5...")
    try:
        trending = fetch_github_trending(top_n=5, since="weekly")
        trending = translate_trending_descriptions(trending)  # 兜底: 中文简介
        trending = enrich_trending(trending)                  # 详情: 功能/开发者价值/稳定性
        trending_block = format_trending_block(trending)
        if trending_block:
            report = report + "\n\n" + trending_block
            print(f"  附加 {len(trending)} 个 trending 仓库,总长度 {len(report)} 字符")
    except Exception as e:
        print(f"GitHub Trending 抓取失败(忽略): {e!r}")

    # 3.75 统一清理: DeepSeek 时而输出 HTML 时而 markdown,残留 ** / 混合符号,
    # 这里归一成干净 markdown,邮件 HTML 渲染 / Telegram / 归档三处共用
    report = normalize_text(report)
    print(f"清理后报告长度: {len(report)} 字符")

    # 4. 推送 (QQ 邮箱为主,Telegram 若配置了也发)
    print("\n[3/3] 推送报告...")
    subject = f"AI 早报 · {_bj_now_str().split(' ')[0]}"
    try:
        send_report(report, subject=subject)
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)

    # 5. 存档到 archive/,只保留最近 7 天
    print("\n保存归档...")
    try:
        save_archive_and_cleanup(report)
    except Exception as e:
        print(f"归档失败(忽略): {e!r}")

    print("\n✅ 完成")


if __name__ == "__main__":
    main()
