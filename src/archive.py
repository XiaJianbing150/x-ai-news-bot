"""每天的早报内容存档到 archive/ 目录,只保留最近 7 天。"""
import os
import re
from datetime import datetime, timezone, timedelta

ARCHIVE_DIR = "archive"
RETENTION_DAYS = 7


def _html_to_text(html: str) -> str:
    """把 Telegram HTML 转成人类友好的 Markdown 文本。"""
    # <a href="x">y</a> -> [y](x)
    text = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", html, flags=re.DOTALL)
    # 加粗/斜体
    text = re.sub(r"</?b>", "**", text)
    text = re.sub(r"</?strong>", "**", text)
    text = re.sub(r"</?i>", "*", text)
    text = re.sub(r"</?em>", "*", text)
    # 其它标签直接剥掉
    text = re.sub(r"<[^>]+>", "", text)
    # HTML 实体反转义
    text = (text.replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&#39;", "'"))
    return text


def save_archive_and_cleanup(report: str) -> str:
    """保存 report 到 archive/YYYY-MM-DD.md(北京日期),清理超 7 天旧文件。
    返回保存的文件路径。
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    bj_now = datetime.now(timezone.utc) + timedelta(hours=8)
    date_str = bj_now.strftime("%Y-%m-%d")
    path = os.path.join(ARCHIVE_DIR, f"{date_str}.md")

    body = _html_to_text(report)
    header = f"# AI 早报 · {date_str}\n\n生成时间: {bj_now.strftime('%Y-%m-%d %H:%M')} (北京时间)\n\n---\n\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + body + "\n")
    print(f"  saved archive -> {path} ({len(body)} chars)")

    # 清理超期文件:保留含今天在内的最近 RETENTION_DAYS 天 (天数严格 ≤ 7)
    cutoff = bj_now.date() - timedelta(days=RETENTION_DAYS - 1)
    removed = []
    for name in os.listdir(ARCHIVE_DIR):
        if not name.endswith(".md"):
            continue
        try:
            file_date = datetime.strptime(name[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            os.remove(os.path.join(ARCHIVE_DIR, name))
            removed.append(name)
    if removed:
        print(f"  cleaned {len(removed)} old archives: {', '.join(sorted(removed))}")
    return path
