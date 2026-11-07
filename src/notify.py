import os
from typing import List

from . import telegram
from . import mailer


def _channels() -> List[str]:
    """当前配置了哪些发送渠道。"""
    chans = []
    if mailer.email_configured():
        chans.append("email")
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        chans.append("telegram")
    return chans


def send_report(text: str, subject: str = "AI 早报", channels=None, on_result=None) -> dict:
    """Attempt every selected channel independently; return delivery evidence.

    SMTP errors are conservatively unknown: the server may have accepted the
    message before the connection failed. Never expose exception text/secrets.
    """
    chans = _channels() if channels is None else channels
    if not chans:
        raise RuntimeError("未配置任何发送渠道")
    results = {}
    for channel in chans:
        try:
            if channel == "email":
                mailer.send_to_email(subject, text)
                result = {"status": "sent"}
            elif channel == "telegram":
                result = telegram.send_to_telegram(text)
            else:
                raise ValueError("Unknown notification channel")
        except Exception as exc:
            result = {"status": "unknown", "reason": type(exc).__name__}
        results[channel] = result
        print(f"  通知 {channel}: {result['status']}")
        if on_result is not None:
            on_result(channel, result)
    return results


def send_alert(text: str, subject: str = "AI 早报 ⚠️") -> dict:
    """Existing configured destinations only; no exception escapes the alert."""
    chans = _channels()
    return send_report(text, subject=subject, channels=chans) if chans else {}
