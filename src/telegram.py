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
    """Prefer line boundaries, but also bound a single long line."""
    parts = []
    while len(text) > size:
        split = text.rfind("\n", 0, size + 1)
        if split <= 0:
            split = size
            parts.append(text[:split])
            text = text[split:]
        else:
            parts.append(text[:split])
            text = text[split + 1:]
    if text:
        parts.append(text)
    return parts


def _strip_all_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def _md_to_tg(text: str) -> str:
    """清理后的 markdown -> Telegram HTML (加粗/斜体/行内代码/链接/列表标签)。"""
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    # 非贪婪: 加粗内容里可能含单个 * (如 **Bash(git * main)**)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # 「功能介绍:」这类标签加粗;• 前缀更紧凑
    text = re.sub(
        r"^[-•]\s+([^：:]{1,16}[：:])",
        r"• <b>\1</b>",
        text,
        flags=re.MULTILINE,
    )
    return text


def _post(url, payload):
    return requests.post(url, data=payload, timeout=(10, 30))


def _send_part(url, payload):
    """Retry only known non-delivery: connect timeout or explicit 429 rejection.

    Telegram sendMessage has no idempotency key. A read timeout, disconnected
    response or 5xx can mean the message arrived, so never retry those blindly.
    """
    for attempt in range(1, 4):
        try:
            response = _post(url, payload)
        except requests.ConnectTimeout:
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            return {"status": "failed", "reason": "connect_timeout", "attempts": attempt}
        except requests.RequestException as exc:
            return {"status": "unknown", "reason": type(exc).__name__, "attempts": attempt}

        try:
            data = response.json()
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code == 200 and data.get("ok") is True:
            message_id = (data.get("result") or {}).get("message_id")
            if message_id is not None:
                return {"status": "sent", "message_id": message_id, "attempts": attempt}
            return {"status": "unknown", "reason": "missing_receipt", "attempts": attempt}
        if response.status_code >= 500 or response.status_code < 400:
            return {"status": "unknown", "reason": "unconfirmed_response", "http_status": response.status_code, "attempts": attempt}
        if response.status_code == 429 and data.get("ok") is False:
            delay = (data.get("parameters") or {}).get("retry_after", 1)
            if isinstance(delay, (int, float)) and 0 <= delay <= 30 and attempt < 3:
                time.sleep(max(1, delay))
                continue
        description = str(data.get("description", "")).lower()
        parse_error = response.status_code == 400 and data.get("ok") is False and any(
            marker in description for marker in ("can't parse entities", "unsupported start tag", "can't find end tag")
        )
        return {"status": "failed", "reason": "html_parse_error" if parse_error else "api_rejected",
                "http_status": response.status_code, "attempts": attempt}


def send_to_telegram(text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    parts = _chunks(_sanitize_html(_md_to_tg(text)))
    result = {"status": "sent", "sent_parts": 0, "message_ids": [], "attempts": 0}
    if not parts:
        return {**result, "status": "failed", "reason": "empty_message"}

    for i, part in enumerate(parts):
        payload = {"chat_id": chat_id, "text": part, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        delivery = _send_part(url, payload)
        result["attempts"] += delivery["attempts"]
        if delivery.get("reason") == "html_parse_error":
            # Only retry plain text after Telegram explicitly rejected the HTML.
            payload.pop("parse_mode")
            payload["text"] = _strip_all_tags(part)
            delivery = _send_part(url, payload)
            result["attempts"] += delivery["attempts"]
        if delivery["status"] != "sent":
            result.update({key: value for key, value in delivery.items() if key != "attempts"})
            result["failed_part"] = i + 1
            return result
        result["sent_parts"] += 1
        result["message_ids"].append(delivery["message_id"])
        if i < len(parts) - 1:
            time.sleep(1)
    return result
