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


def send_report(text: str, subject: str = "AI 早报") -> None:
    """正式早报: 发给所有已配置的渠道。至少一个渠道必须配好,否则抛错。"""
    chans = _channels()
    if not chans:
        raise RuntimeError(
            "未配置任何发送渠道: 请设置 QQ_SMTP_USER / QQ_SMTP_AUTH_CODE / MAIL_TO "
            "(QQ 邮箱),或 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (Telegram)"
        )
    if "email" in chans:
        mailer.send_to_email(subject, text)
        print("  ✅ 已发送到 QQ 邮箱")
    if "telegram" in chans:
        telegram.send_to_telegram(text)
        print("  ✅ 已发送到 Telegram")


def send_alert(text: str, subject: str = "AI 早报 ⚠️") -> None:
    """告警消息: 发给已配置的渠道,单个渠道失败不影响其他渠道,也不抛错。"""
    for c in _channels():
        try:
            if c == "email":
                mailer.send_to_email(subject, text)
            else:
                telegram.send_to_telegram(text)
            print(f"  ✅ 告警已发送 ({c})")
        except Exception as e:
            print(f"  ❌ 告警发送失败 ({c}): {e!r}")
