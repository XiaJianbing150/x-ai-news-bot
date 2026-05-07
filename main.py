import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

from src.fetcher import fetch_all_tweets
from src.summarizer import generate_morning_report
from src.telegram import send_to_telegram


def _bj_now_str():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def main():
    print(f"=== AI 早报 Bot 启动  ({_bj_now_str()} 北京时间) ===")

    # 1. 抓推
    print("\n[1/3] 抓取 X 推文...")
    try:
        tweets = fetch_all_tweets()
    except Exception as e:
        print(f"抓取阶段异常: {e}")
        traceback.print_exc()
        send_to_telegram(f"⚠️ AI 早报失败:抓取推文异常\n<code>{e}</code>")
        sys.exit(1)

    print(f"\n共抓到 {len(tweets)} 条推文")
    if not tweets:
        send_to_telegram(
            "⚠️ <b>AI 早报</b>\n今日未抓到任何推文,可能是 RSSHub 实例全部不可用,稍后会重试。"
        )
        sys.exit(0)

    # 2. 总结
    print("\n[2/3] 调用 Zhipu 生成早报...")
    try:
        report = generate_morning_report(tweets)
    except Exception as e:
        print(f"总结阶段异常: {e}")
        traceback.print_exc()
        send_to_telegram(f"⚠️ AI 早报失败:Zhipu 调用异常\n<code>{str(e)[:500]}</code>")
        sys.exit(1)

    print(f"早报长度: {len(report)} 字符")
    print("---- 预览 ----")
    print(report[:500])
    print("--------------")

    # 3. 推送
    print("\n[3/3] 推送到 Telegram...")
    try:
        send_to_telegram(report)
    except Exception as e:
        print(f"推送阶段异常: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n✅ 完成")


if __name__ == "__main__":
    main()
