import os
import re
import time
import html
import requests

TG_LIMIT = 4000  # 留点余量,Telegram 上限 4096

# Telegram 仅支持这几种标签
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a"}


def _sanitize_html(text: str) -> str:
    """清洗 LLM 输出,适配 Telegram HTML 模式。
    - 不在 _ALLOWED_TAGS 里的标签整体丢弃(保留外部文本)
    - <a> 必须有非空 href 才保留;开标签丢了的话对应的 </a> 也丢
    - 标签内除 href 外的属性一律去掉
    """
    out = []
    pos = 0
    a_open_valid = 0  # 当前还未闭合的、有效 <a> 的层数
    for m in re.finditer(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)([^>]*)>", text):
        out.append(text[pos:m.start()])
        pos = m.end()
        slash = m.group(1) or ""
        tag = m.group(2).lower()
        attrs = m.group(3) or ""

        if tag not in _ALLOWED_TAGS:
            continue  # 丢弃整个标签,保留内部文本

        if tag == "a":
            if slash:
                if a_open_valid > 0:
                    out.append("</a>")
                    a_open_valid -= 1
                # 否则是孤儿 </a>,丢
                continue
            href_m = re.search(r'href\s*=\s*["\']([^"\']*)["\']', attrs, re.IGNORECASE)
            if not href_m or not href_m.group(1).strip():
                continue  # 丢弃残缺 <a>,对应 </a> 后续也会因 a_open_valid==0 被丢
            href = href_m.group(1).strip().replace('"', "%22").replace("<", "%3C").replace(">", "%3E")
            out.append(f'<a href="{href}">')
            a_open_valid += 1
        else:
            out.append(f"<{slash}{tag}>")

    out.append(text[pos:])
    return "".join(out)


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


def _strip_all_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def _post(url, payload):
    return requests.post(url, data=payload, timeout=30)


def send_to_telegram(text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    cleaned = _sanitize_html(text)
    parts = _chunks(cleaned)

    for i, part in enumerate(parts):
        payload = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        r = _post(url, payload)
        if not r.ok:
            print(f"  HTML send failed: {r.status_code} {r.text[:300]}")
            # 兜底:剥光所有 tag 当纯文本发,按需重新分段
            plain = _strip_all_tags(part)
            for sub in _chunks(plain):
                r2 = _post(url, {
                    "chat_id": chat_id,
                    "text": sub,
                    "disable_web_page_preview": "true",
                })
                if not r2.ok:
                    print(f"  plain send failed: {r2.status_code} {r2.text[:300]}")
                    r2.raise_for_status()
                time.sleep(0.5)
        if i < len(parts) - 1:
            time.sleep(1)
