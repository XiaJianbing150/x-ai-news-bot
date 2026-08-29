"""QQ 邮箱 SMTP 发信: 把已清理的 markdown 早报渲染成排版友好的 HTML 邮件。

设计:
- 纯文本部分 = 清理后的 markdown (干净可读)
- HTML 部分 = 带内联样式的排版: 章节标题 / 产品更新卡片 / 原文按钮 / 列表
- 所有样式内联,QQ 邮箱、Foxmail、网页端都能正常显示
"""
import os
import re
import smtplib
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465

# 章节 emoji -> 主题色
_SECTION_COLORS = {
    "🅰": "#d97706",  # 琥珀
    "🅾": "#10a37f",  # OpenAI 绿
    "⭐": "#b7791f",
    "🧰": "#7c3aed",  # 紫
    "📌": "#dc2626",  # 红
    "🔥": "#ea580c",  # 橙
}

_CSS = {
    "wrap": "max-width:680px;margin:0 auto;padding:20px 16px;"
            "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;"
            "font-size:15px;line-height:1.75;color:#1f2328;background:#ffffff;",
    "h1": "font-size:21px;margin:0 0 6px;color:#111;",
    "h2": "margin:28px 0 10px;padding:8px 14px;border-left:4px solid {color};"
          "background:#fff7e6;border-radius:4px;font-size:17px;color:#1f2328;",
    "card": "margin:12px 0;padding:12px 16px;border:1px solid #e6e8eb;"
            "border-left:3px solid #d97706;border-radius:8px;background:#fafbfc;",
    "h3": "margin:0 0 8px;font-size:15px;color:#1f2328;",
    "banner": "margin:18px 0;padding:12px 16px;background:linear-gradient(90deg,#eef4ff,#ffffff);"
              "border:1px solid #d6e2f5;border-radius:8px;font-size:16px;font-weight:600;",
    "p": "margin:8px 0;",
    "ul": "margin:6px 0;padding-left:22px;",
    "ol": "margin:8px 0;padding-left:24px;",
    "li": "margin:5px 0;",
    "hr": "border:none;border-top:1px solid #e6e8eb;margin:20px 0;",
    "a": "color:#1f6feb;text-decoration:none;",
    "linkbtn": "display:inline-block;margin-top:8px;padding:5px 14px;background:#1f6feb;"
               "color:#ffffff !important;border-radius:16px;font-size:13px;text-decoration:none;",
    "meta": "margin:4px 0 0;font-size:13px;color:#6e7781;",
}


def email_configured() -> bool:
    """QQ 邮箱渠道是否配齐了必需的环境变量。"""
    return all(
        os.environ.get(k)
        for k in ("QQ_SMTP_USER", "QQ_SMTP_AUTH_CODE", "MAIL_TO")
    )


def _inline(s: str) -> str:
    """行内 markdown -> HTML (输入已 html.escape,不会注入原始标签)。"""
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" style="{a}">\1</a>'.format(a=_CSS["a"]),
        s,
    )
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # 列表行里的「功能介绍:」这类标签加粗,读起来更清晰
    s = re.sub(r"^([^：:]{1,16}[：:])", r"<b>\1</b>", s)
    return s


def _section_color(inner: str) -> str:
    for k, v in _SECTION_COLORS.items():
        if inner.startswith(k):
            return v
    return "#57606a"


def _parse_blocks(md: str):
    """把清理后的 markdown 解析成渲染块列表 (线性扫描,不追求完备)。"""
    blocks = []
    lines = [l.rstrip() for l in md.split("\n")]
    n = len(lines)
    i = 0
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        # 分隔线
        if re.fullmatch(r"[-_*]{3,}", s):
            blocks.append(("hr", ""))
            i += 1
            continue
        # markdown 标题
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            blocks.append(("h" + str(len(m.group(1))), m.group(2)))
            i += 1
            continue
        # 新闻横幅
        if s.startswith("📰"):
            blocks.append(("banner", s))
            i += 1
            continue
        # 粗体行: 章节标题 or 卡片标题 (看下一行是不是列表项)
        m = re.fullmatch(r"\*\*(.+)\*\*", s)
        if m:
            inner = m.group(1)
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            nxt = lines[j].strip() if j < n else ""
            if nxt.startswith("- ") or nxt.startswith("[原文]"):
                blocks.append(("card", inner))
            else:
                blocks.append(("h2", inner))
            i += 1
            continue
        # 非粗体 emoji 短行 -> 章节标题
        if s[0] in "🅰🅾⭐🧰📌🔥" and len(s) <= 40:
            blocks.append(("h2", s))
            i += 1
            continue
        # 无序列表项
        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            blocks.append(("li", m.group(1)))
            i += 1
            continue
        # 有序列表项
        m = re.match(r"^(\d+)[.)]\s+(.*)$", s)
        if m:
            blocks.append(("oli", m.group(2)))
            i += 1
            continue
        # 原文链接 -> 按钮
        m = re.match(r"^\[原文\]\((https?://[^)\s]+)\)$", s)
        if m:
            blocks.append(("linkbtn", m.group(1)))
            i += 1
            continue
        # 普通段落
        blocks.append(("p", s))
        i += 1
    return blocks


def _render_html(md: str) -> str:
    """清理后的 markdown -> 完整 HTML 邮件正文。"""
    parts = ["<div style='{wrap}'>".format(wrap=_CSS["wrap"])]
    ul_open = ol_open = card_open = False

    def close_lists():
        nonlocal ul_open, ol_open
        if ul_open:
            parts.append("</ul>")
            ul_open = False
        if ol_open:
            parts.append("</ol>")
            ol_open = False

    def close_card():
        nonlocal card_open
        close_lists()
        if card_open:
            parts.append("</div>")
            card_open = False

    for kind, val in _parse_blocks(md):
        if kind == "hr":
            close_card()
            parts.append("<hr style='{hr}'>".format(hr=_CSS["hr"]))
        elif kind == "h1":
            close_card()
            parts.append("<h1 style='{h1}'>{t}</h1>".format(h1=_CSS["h1"], t=_inline(val)))
        elif kind == "h2":
            close_card()
            color = _section_color(val)
            parts.append(
                "<h2 style='{h2}'>{t}</h2>".format(
                    h2=_CSS["h2"].format(color=color), t=_inline(val)
                )
            )
        elif kind == "card":
            close_card()
            parts.append("<div style='{card}'><h3 style='{h3}'>{t}</h3>".format(
                card=_CSS["card"], h3=_CSS["h3"], t=_inline(val)))
            parts.append("<ul style='{ul}'>".format(ul=_CSS["ul"]))
            ul_open = card_open = True
        elif kind == "li":
            if not ul_open and not ol_open:
                parts.append("<ul style='{ul}'>".format(ul=_CSS["ul"]))
                ul_open = True
            parts.append("<li style='{li}'>{t}</li>".format(li=_CSS["li"], t=_inline(val)))
        elif kind == "oli":
            if not ol_open:
                close_lists()
                parts.append("<ol style='{ol}'>".format(ol=_CSS["ol"]))
                ol_open = True
            parts.append("<li style='{li}'>{t}</li>".format(li=_CSS["li"], t=_inline(val)))
        elif kind == "linkbtn":
            close_card()
            parts.append(
                '<a href="{u}" style="{b}">原文 ↗</a>'.format(u=val, b=_CSS["linkbtn"])
            )
        elif kind == "banner":
            close_card()
            parts.append("<div style='{b}'>{t}</div>".format(b=_CSS["banner"], t=_inline(val)))
        else:  # p
            close_card()
            parts.append("<p style='{p}'>{t}</p>".format(p=_CSS["p"], t=_inline(val)))

    close_card()
    parts.append("</div>")
    return "\n".join(parts)


def send_to_email(subject: str, text: str, to: Optional[str] = None) -> None:
    """通过 QQ 邮箱 SMTP 发送早报: 纯文本 + 排版 HTML 双格式。"""
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
    msg.attach(MIMEText(_render_html(text), "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.login(user, auth_code)
        server.sendmail(user, recipients, msg.as_string())
