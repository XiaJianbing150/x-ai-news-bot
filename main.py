import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.fetcher import (
    fetch_all_tweets, fetch_github_trending, format_trending_block,
    translate_trending_descriptions, fetch_openai_blog,
    fetch_anthropic_news, fetch_claude_code_releases,
)
from src.trending import enrich_trending
from src.summarizer import generate_morning_report
from src.text_utils import normalize_text
from src.archive import save_archive_and_cleanup
from src.health import reset_health, get_health
from src.run_state import (
    CONTEXT_PATH, write_json, load_status, save_status,
    reserve_delivery, deliver_reserved, print_summary,
)


def _bj_now_str():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def _source_note(sources):
    failed = [source["source"] for source in sources if source["status"] == "failed"]
    partial = [source["source"] for source in sources if source["status"] == "partial"]
    lines = ["信息源状态：" + "；".join(f"{source['source']} {source['count']} 条 ({source['status']})"
                                    for source in sources if source["status"] != "skipped")]
    if failed:
        lines.append("⚠️ 本次未能读取：" + "、".join(failed) + "。正文不代表这些来源没有更新。")
    if partial:
        lines.append("部分端点不可用，已使用成功的备用来源：" + "、".join(partial) + "。")
    return "\n\n".join(lines) if sources else ""


def _generate(date):
    previous = load_status(date)
    if previous and previous.get("generation") == "ready" and Path(previous["archive"]).exists():
        print("Today's archive already exists; reuse it without resending prior attempts.")
        return previous
    # A legacy archive lacks delivery evidence. Do not guess whether it was sent.
    if Path(f"archive/{date}.md").exists() and not previous:
        raise RuntimeError("Today's archive has no delivery journal; reconcile existing deliveries first")
    status = {"date": date, "generation": "running", "notifications": (previous or {}).get("notifications", {})}
    save_status(date, status)
    reset_health()
    try:
        items = []
        for label, fetch in (("X", fetch_all_tweets), ("Anthropic", fetch_anthropic_news),
                             ("Claude Code", fetch_claude_code_releases), ("OpenAI", fetch_openai_blog)):
            try:
                items.extend(fetch())
            except Exception as exc:
                # One unavailable source must not suppress the official sources.
                print(f"::warning::Source {label} failed ({type(exc).__name__})")
        if not items:
            raise RuntimeError("No usable items from any source")
        print(f"Fetched {len(items)} items; generating report")
        report = generate_morning_report(items, report_date=date)
        if not report or not report.strip():
            raise RuntimeError("Generated report is empty")
        try:
            trending = fetch_github_trending(top_n=5, since="weekly")
            trending = translate_trending_descriptions(trending)
            trending = enrich_trending(trending)
            block = format_trending_block(trending)
            if block:
                report += "\n\n" + block
        except Exception as exc:
            print(f"::warning::GitHub Trending unavailable ({type(exc).__name__})")
        status["sources"] = get_health()
        note = _source_note(status["sources"])
        report = normalize_text(report) + ("\n\n" + note if note else "")
        # This must finish before any notification is attempted.
        status["archive"] = save_archive_and_cleanup(report, report_date=date)
        status["generation"] = "ready"
        save_status(date, status)
        return status
    except Exception as exc:
        status.update(generation="failed", error=type(exc).__name__, sources=get_health())
        save_status(date, status)
        print_summary(status)
        raise


def main(stage="all", dry_run=False):
    print(f"=== AI 早报 Bot ({_bj_now_str()} 北京时间), stage={stage} ===")
    try:
        if stage in ("all", "generate"):
            date = _bj_now_str().split(" ")[0]
            status = _generate(date)
            write_json(CONTEXT_PATH, {"date": date})
            print_summary(status)
            if dry_run or stage == "generate":
                return 0
        else:
            date = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))["date"]
        if stage in ("all", "reserve"):
            reserve_delivery(date)
            if stage == "reserve":
                return 0
        status = deliver_reserved()
        print_summary(status)
        return 0 if all(result["status"] == "sent" for result in status["notifications"].values()) else 1
    except Exception as exc:
        # Exception messages can contain credentials or provider response bodies.
        print(f"::error::Morning report stage {stage} failed ({type(exc).__name__})")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("all", "generate", "reserve", "send"), default="all")
    parser.add_argument("--dry-run", action="store_true", help="Generate/archive only; no notifications")
    args = parser.parse_args()
    if args.dry_run and args.stage not in ("all", "generate"):
        parser.error("--dry-run supports only all/generate")
    sys.exit(main(stage=args.stage, dry_run=args.dry_run))
