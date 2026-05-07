import os
import time
import html
import requests

TG_LIMIT = 4000  # 留点余量,Telegram 上限 4096


def _chunks(text: str, size: int = TG_LIMIT):
    if len(text) <= size:
        return [text]
    out, buf, n = [], [], 0
    for line in text.split("\n"):
        ln = len(line) + 1
        if n + ln > size and buf:
            out.append("\n".join(buf))
            buf, n = [line], ln
        else:
            buf.append(line)
            n += ln
    if buf:
        out.append("\n".join(buf))
    return out


def send_to_telegram(text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    parts = _chunks(text)
    for i, part in enumerate(parts):
        payload = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        r = requests.post(url, data=payload, timeout=30)
        if not r.ok:
            # HTML 解析失败兜底:转义后纯文本再发一次
            print(f"  HTML send failed: {r.status_code} {r.text[:200]}")
            payload2 = {
                "chat_id": chat_id,
                "text": html.escape(part),
                "disable_web_page_preview": "true",
            }
            r2 = requests.post(url, data=payload2, timeout=30)
            r2.raise_for_status()
        if i < len(parts) - 1:
            time.sleep(1)
