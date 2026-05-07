import sys
import traceback
from datetime import datetime, timezone, timedelta

from src.fetcher import fetch_all_tweets, fetch_arxiv_papers
from src.summarizer import generate_morning_report
from src.telegram import send_to_telegram


def _bj_now_str():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def main():
    print(f"=== AI 早报 Bot 启动  ({_bj_now_str()} 北京时间) ===")

    # 1. 抓推文
    print("\n[1/3] 抓取 X 推文...")
    try:
        tweets = fetch_all_tweets()
    except Exception as e:
        traceback.print_exc()
        send_to_telegram(f"⚠️ AI 早报失败: 抓取推文异常\n<code>{str(e)[:500]}</code>")
        sys.exit(1)
    print(f"\n共抓到 {len(tweets)} 条推文")

    # 2. 推文不够时,补一份 arXiv 论文兜底
    items = list(tweets)
    if len(tweets) < 5:
        print("\n推文太少,补抓 arXiv 论文兜底...")
        try:
            papers = fetch_arxiv_papers()
            items.extend(papers)
        except Exception as e:
            print(f"arXiv 兜底失败(忽略): {e!r}")

    if not items:
        send_to_telegram(
            "⚠️ <b>AI 早报</b>\n今日所有数据源都失败了 (Nitter/RSSHub/arXiv 全挂),稍后会自动重试。"
        )
        sys.exit(0)

    # 3. 总结
    print("\n[2/3] 调用 Zhipu 生成早报...")
    try:
        report = generate_morning_report(items)
    except Exception as e:
        traceback.print_exc()
        send_to_telegram(f"⚠️ AI 早报失败: Zhipu 调用异常\n<code>{str(e)[:500]}</code>")
        sys.exit(1)
    print(f"早报长度: {len(report)} 字符")
    print("---- 预览 ----")
    print(report[:500])
    print("--------------")

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
