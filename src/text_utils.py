"""把 DeepSeek 输出的 HTML/markdown 混合体统一成干净、可读的 markdown。

DeepSeek 时而遵守提示词输出 Telegram HTML (<b>/<a>),时而又输出 markdown **,
甚至留下残尾星号、混合符号列表。所有发送渠道 (QQ 邮箱 / Telegram / 归档)
共用这一份清理结果,保证到处都干净。
"""
import re

# HTML 实体反转义
_ENTITIES = (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
             ("&quot;", '"'), ("&#39;", "'"), ("&#x27;", "'"), ("&#x2F;", "/"))


def _html_to_markdown(text: str) -> str:
    """Telegram HTML -> markdown 等价物,其余标签剥掉。"""
    t = text
    t = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", t, flags=re.DOTALL)
    t = re.sub(r"</?strong>", "**", t)
    t = re.sub(r"</?b>", "**", t)
    t = re.sub(r"</?em>", "*", t)
    t = re.sub(r"</?i>", "*", t)
    t = re.sub(r"</?code>", "`", t)
    t = re.sub(r"<[^>]+>", "", t)
    for k, v in _ENTITIES:
        t = t.replace(k, v)
    return t


def _fix_line(line: str) -> str:
    """修单行: 平衡 **、统一列表符号、去行尾空白。"""
    line = line.rstrip()
    s = line.strip()
    if not s:
        return ""
    # 奇数个 ** 说明有残渣,删掉最后一个 ** (通常是行尾多出来的)
    if line.count("**") % 2 == 1:
        idx = line.rfind("**")
        if idx >= 0:
            line = line[:idx] + line[idx + 2:]
    s = line.strip()
    if not s:
        return ""
    # 项目符号统一成 "- "
    if s[0] in "•◦▪▸" or s.startswith("* ") or s.startswith("- "):
        if not s.startswith("- ") or s[0] in "•◦▪▸":
            line = "- " + s.lstrip("•◦▪▸*").lstrip()
    # 空列表项 (只有符号没内容) 直接丢弃
    if line.strip() in ("-", "*", "•"):
        return ""
    return line


def normalize_text(text: str) -> str:
    """统一清理: HTML->markdown、修复星号残渣、统一列表、折叠空行。"""
    t = _html_to_markdown(text or "")
    lines = [_fix_line(line) for line in t.split("\n")]

    # 折叠连续空行(最多留 1 个)
    out, prev_blank = [], False
    for line in lines:
        if not line:
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        out.append(line)
    return "\n".join(out).strip()
