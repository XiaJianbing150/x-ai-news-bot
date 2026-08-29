import os
import re
import html
import smtplib
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465


def email_configured() -> bool:
    """QQ 邮箱渠道是否配齐了必需的环境变量。"""
    return all(
        os.environ.get(k)
        for k in ("QQ_SMTP_USER", "QQ_SMTP_AUTH_CODE", "MAIL_TO")
    )


def _inline(s: str) -> str:
    """行内 Markdown → HTML (输入已 html.escape,不会注入原始标签)。"""
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        s,
    )
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _md_to_html(text: str) -> str:
    """把早报的轻量 Markdown 转成简单 HTML (标题/列表/链接/加粗/分隔线)。

    先整体 escape 再转换,LLM 偶然输出的 <tag> 不会泄漏成真实 HTML。
    """
    out = []
    in_ul = in_ol = False

    def close_list():
        nonlocal in_ul, in_ol
        if in_ul or in_ol:
            out.append("</ul>" if in_ul else "</ol>")
            in_ul = in_ol = False

    for raw in html.escape(text).split("\n"):
        line = raw.rstrip()
        if not line.strip():
            close_list()
            continue

        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            close_list()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            continue

        if re.match(r"^-{3,}\s*$", line.strip()):
            close_list()
            out.append("<hr>")
            continue

        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            if not in_ul:
                close_list()
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + _inline(m.group(1)) + "</li>")
            continue

        m = re.match(r"^\s*(\d+)[.)]\s+(.*)$", line)
        if m:
            if not in_ol:
                close_list()
                out.append("<ol>")
                in_ol = True
            out.append("<li>" + _inline(m.group(2)) + "</li>")
            continue

        close_list()
        out.append(_inline(line) + "<br>")

    close_list()
    return "\n".join(out)


def send_to_email(subject: str, text: str, to: Optional[str] = None) -> None:
    """通过 QQ 邮箱 SMTP 发送早报,正文同时带纯文本和 HTML 两种格式。"""
    user = os.environ["QQ_SMTP_USER"]
    auth_code = os.environ["QQ_SMTP_AUTH_CODE"]
    to = to or os.environ["MAIL_TO"]
    recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
    if not recipients:
        raise ValueError("MAIL_TO 为空")

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((str(Header("AI 早报", "utf-8")), user))
    msg["To"] = to
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(_md_to_html(text), "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.login(user, auth_code)
        server.sendmail(user, recipients, msg.as_string())
