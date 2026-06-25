import os
import re
import time
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .config import (
    BLOGGERS,
    SKIP_X_ACCOUNTS,
    TWITTER_FEED_TEMPLATES,
    JINA_READER_URL,
    ARXIV_FEEDS,
    ARXIV_MAX_PAPERS,
    HOURS_LOOKBACK,
    MAX_TWEETS_PER_BLOGGER,
    REQUEST_TIMEOUT,
)

UA = "Mozilla/5.0 (compatible; x-ai-news-bot/1.0; +https://github.com/)"

# RSS 垃圾检测:这些字符串出现在 title/description 里说明是反爬或白名单错误页
RSS_GARBAGE_MARKERS = (
    "rss reader not yet whitelist",
    "making sure you",
    "verifying your browser",
    "just a moment",
    "attention required",
    "access denied",
    "rate limit",
)


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+\n", "\n", s)
    return s.strip()


def _entry_datetime(entry):
    pub = entry.get("published_parsed") or entry.get("updated_parsed")
    if not pub:
        return None
    return datetime(*pub[:6], tzinfo=timezone.utc)


def _http_get(url: str, headers=None):
    h = {"User-Agent": UA, "Accept": "application/rss+xml,application/atom+xml,*/*"}
    if headers:
        h.update(headers)
    return requests.get(url, timeout=REQUEST_TIMEOUT, headers=h)


def _looks_like_garbage_feed(feed) -> bool:
    """检测 feed 是不是反爬页 / 白名单错误 / 1971 年伪 entry。"""
    chan_title = (feed.feed.get("title", "") or "").lower()
    chan_desc = (feed.feed.get("description", "") or "").lower()
    for marker in RSS_GARBAGE_MARKERS:
        if marker in chan_title or marker in chan_desc:
            return True
    # 全部 entry 的 pubDate 都早于 2010 → 多半是占位
    valid_dates = []
    for e in feed.entries[:5]:
        dt = _entry_datetime(e)
        if dt:
            valid_dates.append(dt)
    if valid_dates and all(d.year < 2010 for d in valid_dates):
        return True
    # 仅 1 个 entry 且 title 含 garbage marker
    if len(feed.entries) <= 1:
        for e in feed.entries:
            t = (e.get("title", "") or "").lower()
            if any(m in t for m in RSS_GARBAGE_MARKERS):
                return True
    return False


def _try_rss(username: str):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_LOOKBACK)
    last_err = None
    for tpl in TWITTER_FEED_TEMPLATES:
        url = tpl.format(user=username)
        try:
            r = _http_get(url)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            # 内容判别:HTML 反爬页直接跳过
            head = r.content[:200].lower()
            if b"<!doctype html" in head or b"<html" in head[:100]:
                last_err = "anti-bot HTML"
                continue
            feed = feedparser.parse(r.content)
            if not feed.entries:
                last_err = "no entries"
                continue
            if _looks_like_garbage_feed(feed):
                last_err = "garbage/whitelist page"
                continue

            tweets = []
            for entry in feed.entries:
                pub_dt = _entry_datetime(entry)
                if pub_dt and pub_dt < cutoff:
                    continue
                content = entry.get("summary", "") or entry.get("description", "") or entry.get("title", "")
                text = _strip_html(content)
                if not text or len(text) < 5:
                    continue
                tweets.append({
                    "user": username,
                    "text": text,
                    "link": entry.get("link", ""),
                    "published": pub_dt.isoformat() if pub_dt else "",
                    "source": "rss",
                })
                if len(tweets) >= MAX_TWEETS_PER_BLOGGER:
                    break
            if tweets:
                return tweets, url, None
            last_err = "all entries out of window"
        except Exception as e:
            last_err = repr(e)[:120]
            continue
    return [], None, last_err


def _parse_jina_markdown(username: str, md: str):
    """把 Jina Reader 返回的 markdown 切成推文段落。
    无逐条时间戳(Jina 不保留),取最新 N 条。
    """
    # 锁定 ## XXX's posts 之后的内容
    m = re.search(r"\n##\s+[^\n]*posts?[^\n]*\n+(.+)", md, re.DOTALL | re.IGNORECASE)
    body = m.group(1) if m else md

    raw_chunks = re.split(r"\n\s*\n", body)
    tweets = []
    for chunk in raw_chunks:
        # 去图片
        cleaned = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", "", chunk)
        cleaned = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", cleaned)
        # markdown 链接保留可见文本
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            continue
        # 跳过明显的元数据/噪音 (放宽到 8 字符,留住 "Keep thinking." 这种短推)
        if len(cleaned) < 8:
            continue
        low = cleaned.lower()
        if low in ("quote", "show this thread", "claude", username.lower()):
            continue
        # 跳过纯日期/时间戳行
        if re.fullmatch(r"[\d:apm\s/,.-]+", cleaned, re.IGNORECASE):
            continue
        # 截断单条
        if len(cleaned) > 600:
            cleaned = cleaned[:600] + "..."
        tweets.append({
            "user": username,
            "text": cleaned,
            "link": f"https://x.com/{username}",
            "published": "",
            "source": "jina",
        })
        if len(tweets) >= MAX_TWEETS_PER_BLOGGER:
            break
    return tweets


def _try_jina(username: str):
    url = JINA_READER_URL.format(user=username)
    headers = {"Accept": "text/markdown"}
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = _http_get(url, headers=headers)
    except Exception as e:
        return [], f"jina exception: {repr(e)[:100]}"
    if r.status_code != 200:
        return [], f"jina HTTP {r.status_code}"
    text = r.text
    if "Title:" not in text[:200] or len(text) < 300:
        return [], "jina empty/blocked"
    tweets = _parse_jina_markdown(username, text)
    if not tweets:
        return [], "jina parsed 0 chunks"
    return tweets, None


def fetch_user_tweets(username: str):
    tweets, source, err = _try_rss(username)
    if tweets:
        return tweets, source

    # RSS 全失败,转 Jina
    tweets, jerr = _try_jina(username)
    if tweets:
        return tweets, "r.jina.ai"

    print(f"  [WARN] @{username} 全部失败: rss={err} | jina={jerr}")
    return [], None


def fetch_all_tweets():
    all_tweets = []
    for u in BLOGGERS:
        if u in SKIP_X_ACCOUNTS:
            print(f"  @{u:<18} -> SKIP (X 只返回 pinned 老闻,改用官博源)")
            continue
        tweets, source = fetch_user_tweets(u)
        host = source.split("/")[2] if source and "//" in source else (source or "n/a")
        print(f"  @{u:<18} -> {len(tweets):>2} tweets  ({host})")
        all_tweets.extend(tweets)
        # 用 Jina 时降速避免 rate limit
        time.sleep(2.0)
    return all_tweets


def fetch_openai_blog(hours_lookback: int = None):
    """从 OpenAI 官博 RSS 抓产品更新 (权威源)。
    比推文窗口多 48h: 官博发布常比 X 公告晚 1-2 天,且不希望漏掉边界日。
    """
    if hours_lookback is None:
        hours_lookback = HOURS_LOOKBACK + 48
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_lookback)
    try:
        r = _http_get("https://openai.com/news/rss.xml")
    except Exception as e:
        print(f"  OpenAI blog 抓取失败: {e!r}")
        return []
    if r.status_code != 200:
        print(f"  OpenAI blog HTTP {r.status_code}")
        return []
    items = re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL)
    out = []
    for it in items:
        tm = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", it) or re.search(r"<title>(.*?)</title>", it)
        title = tm.group(1).strip() if tm else ""
        lm = re.search(r"<link>(.*?)</link>", it)
        link = lm.group(1).strip() if lm else ""
        pm = re.search(r"<pubDate>(.*?)</pubDate>", it)
        dm = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>", it, re.DOTALL) or re.search(r"<description>(.*?)</description>", it, re.DOTALL)
        desc = _strip_html(dm.group(1)) if dm else ""
        if not (title and link and pm):
            continue
        try:
            pub_dt = parsedate_to_datetime(pm.group(1)).astimezone(timezone.utc)
        except Exception:
            continue
        if pub_dt < cutoff:
            continue
        text = title if not desc else f"{title}\n{desc[:400]}"
        out.append({
            "user": "OpenAI官博",
            "text": text,
            "link": link,
            "published": pub_dt.isoformat(),
            "source": "openai-blog",
        })
    print(f"  OpenAI blog -> {len(out)} posts")
    return out


_ANTHROPIC_NEWS_RE = re.compile(
    r"\[(?:Product\s+|Announcements\s+|Policy\s+|Interpretability\s+|Society\s+|Safety\s+)?"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4})"
    r"\s+(?:Product\s+|Announcements\s+|Policy\s+|Interpretability\s+|Society\s+|Safety\s+)?"
    r"(?:####\s+)?"
    r"([^\[\]]+?)\]\((https://www\.anthropic\.com/[^\)]+)\)",
    re.IGNORECASE,
)


def fetch_anthropic_news(hours_lookback: int = None):
    """从 anthropic.com/news 抓产品/公告 (Jina 渲染 markdown)。
    +48h 缓冲避免边界日被误过滤。
    """
    if hours_lookback is None:
        hours_lookback = HOURS_LOOKBACK + 48
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_lookback)
    url = "https://r.jina.ai/https://www.anthropic.com/news"
    headers = {"Accept": "text/markdown"}
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = _http_get(url, headers=headers)
    except Exception as e:
        print(f"  Anthropic news 抓取失败: {e!r}")
        return []
    if r.status_code != 200:
        print(f"  Anthropic news HTTP {r.status_code}")
        return []
    text = r.text
    out, seen = [], set()
    for m in _ANTHROPIC_NEWS_RE.finditer(text):
        date_str, title, link = m.group(1), m.group(2).strip(), m.group(3)
        if link in seen:
            continue
        seen.add(link)
        try:
            # Anthropic news 只给日期(无时分),按当日 23:59 处理,避免边界日被误过滤
            pub_dt = datetime.strptime(date_str, "%b %d, %Y").replace(
                hour=23, minute=59, tzinfo=timezone.utc,
            )
        except Exception:
            continue
        if pub_dt < cutoff:
            continue
        # title 可能带描述,截一段
        title = title[:400]
        out.append({
            "user": "Anthropic官博",
            "text": title,
            "link": link,
            "published": pub_dt.isoformat(),
            "source": "anthropic-news",
        })
    print(f"  Anthropic news -> {len(out)} posts")
    return out


def fetch_claude_code_releases(hours_lookback: int = None):
    """从 GitHub releases atom feed 抓 Claude Code 版本说明 (权威源)。
    覆盖 Anthropic 官博不会发的产品小功能更新 (如 Agent View / /goal 等)。
    +48h 缓冲避免边界日。
    """
    if hours_lookback is None:
        hours_lookback = HOURS_LOOKBACK + 48
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_lookback)
    try:
        r = _http_get("https://github.com/anthropics/claude-code/releases.atom")
    except Exception as e:
        print(f"  Claude Code releases 抓取失败: {e!r}")
        return []
    if r.status_code != 200:
        print(f"  Claude Code releases HTTP {r.status_code}")
        return []
    entries = re.findall(r"<entry>(.*?)</entry>", r.text, re.DOTALL)
    out = []
    for e in entries:
        tm = re.search(r"<title>([^<]+)</title>", e)
        um = re.search(r"<updated>([^<]+)</updated>", e)
        lm = re.search(r'<link[^>]*href="([^"]+)"', e)
        cm = re.search(r"<content[^>]*>(.*?)</content>", e, re.DOTALL)
        if not (tm and um and lm and cm):
            continue
        try:
            pub_dt = datetime.fromisoformat(um.group(1).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        if pub_dt < cutoff:
            continue
        version = tm.group(1).strip()
        link = lm.group(1)
        # content 是 HTML-entity-escaped HTML,先 unescape 再 strip tags
        import html as _html
        body = _html.unescape(cm.group(1))
        body = _strip_html(body)
        # 截一段,完整 changelog 可能上千字
        body = body[:1500]
        out.append({
            "user": "ClaudeCode官方",
            "text": f"Claude Code {version} 发布\n{body}",
            "link": link,
            "published": pub_dt.isoformat(),
            "source": "claude-code-releases",
        })
    print(f"  Claude Code releases -> {len(out)} versions")
    return out


def fetch_arxiv_papers():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_LOOKBACK + 24)
    seen_ids = set()
    papers = []
    for url in ARXIV_FEEDS:
        try:
            r = _http_get(url)
            if r.status_code != 200:
                print(f"  arXiv {url}: HTTP {r.status_code}")
                continue
            feed = feedparser.parse(r.content)
            for entry in feed.entries:
                pub_dt = _entry_datetime(entry)
                if pub_dt and pub_dt < cutoff:
                    continue
                arxiv_id = entry.get("id", entry.get("link", ""))
                if arxiv_id in seen_ids:
                    continue
                seen_ids.add(arxiv_id)
                papers.append({
                    "user": "arXiv",
                    "text": f"{entry.get('title','').strip()}\n{_strip_html(entry.get('summary',''))[:400]}",
                    "link": entry.get("link", ""),
                    "published": pub_dt.isoformat() if pub_dt else "",
                    "source": "arxiv",
                })
        except Exception as e:
            print(f"  arXiv {url} failed: {e!r}")
    papers = papers[:ARXIV_MAX_PAPERS]
    print(f"  arXiv -> {len(papers)} papers")
    return papers


_GH_REPO_HEAD = re.compile(
    r'##\s*\[([\w\-_.]+)\s*/\s*([\w\-_.]+)\]\((https://github\.com/[\w\-_./]+)\)\s*\n+([^\n]+)',
    re.MULTILINE,
)

# HTML 直抓的 user-agent;github 不会因为没有 Mozilla 拒绝,但加上更稳
_GH_DIRECT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/120.0"


def fetch_github_trending(top_n: int = 5, since: str = "weekly"):
    """抓 github.com/trending top N,直接 HTTPS 拉 HTML 解析。
    历史上走过 r.jina.ai,但 jina 对 github.com 长期返回 HTTP 451(Unavailable
    For Legal Reasons),所以现在直接连 github,自己解析 HTML。
    since: daily | weekly | monthly
    """
    import html as _html
    url = f"https://github.com/trending?since={since}"
    headers = {"User-Agent": _GH_DIRECT_UA, "Accept": "text/html,application/xhtml+xml"}
    try:
        r = _http_get(url, headers=headers)
    except Exception as e:
        print(f"  GitHub trending fetch failed: {e!r}")
        return []
    if r.status_code != 200:
        print(f"  GitHub trending: HTTP {r.status_code}")
        return []

    text = r.text
    # trending 页面每个 repo 是一个 <article class="Box-row">
    blocks = re.split(r'<article\s+class="Box-row"', text)
    repos = []
    for blk in blocks[1:]:
        m = re.search(r'<h2[^>]*class="[^"]*h3[^"]*"[^>]*>\s*<a[^>]+href="/([\w\-_.]+)/([\w\-_.]+)"', blk)
        if not m:
            continue
        owner, name = m.group(1), m.group(2)

        # 描述在 <p class="col-9 ...">
        m = re.search(r'<p[^>]+col-9[^"]*"[^>]*>([^<]+?)</p>', blk, re.DOTALL)
        desc = _html.unescape(m.group(1).strip()) if m else ""

        # 主语言
        m = re.search(r'<span itemprop="programmingLanguage">\s*([^<]+?)\s*</span>', blk)
        lang = m.group(1).strip() if m else ""

        # 本周/今日/本月新增 stars
        m = re.search(r'([\d,]+)\s*stars?\s+(today|this\s+week|this\s+month)', blk, re.IGNORECASE)
        if m:
            stars_inc = m.group(1)
            try:
                stars_num = int(stars_inc.replace(",", ""))
            except ValueError:
                stars_num = 0
        else:
            stars_inc, stars_num = "", 0

        repos.append({
            "owner": owner,
            "name": name,
            "url": f"https://github.com/{owner}/{name}",
            "desc": desc[:200],
            "lang": lang,
            "stars_inc": stars_inc,
            "stars_num": stars_num,
            "since": since,
        })

    repos.sort(key=lambda x: x["stars_num"], reverse=True)
    repos = repos[:top_n]
    print(f"  GitHub trending ({since}) -> {len(repos)} repos (direct fetch)")
    return repos


_SINCE_LABEL_ZH = {"daily": "今日", "weekly": "本周", "monthly": "本月"}
_SINCE_LABEL_PERIOD = {"daily": "today", "weekly": "this week", "monthly": "this month"}


def format_trending_block(repos):
    """把 trending 列表渲染成 Telegram HTML,作为早报附录。
    优先用 desc_zh (中文翻译),没有则 fallback 原英文。
    """
    if not repos:
        return ""
    since = repos[0].get("since", "weekly")
    title_period = _SINCE_LABEL_ZH.get(since, "本周")
    star_period = _SINCE_LABEL_PERIOD.get(since, "this week")
    lines = ["", f"<b>🔥 GitHub Trending · {title_period} Top {len(repos)}</b>"]
    for i, r in enumerate(repos, 1):
        meta_parts = []
        if r.get("lang"):
            meta_parts.append(r["lang"])
        # 兼容老字段名 stars_today
        stars_inc = r.get("stars_inc") or r.get("stars_today", "")
        if stars_inc:
            meta_parts.append(f"⭐ +{stars_inc}/{star_period}")
        meta = "  ".join(meta_parts)
        # 优先用中文翻译
        desc = r.get("desc_zh") or r.get("desc", "")
        desc = desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(
            f'{i}. <a href="{r["url"]}"><b>{r["owner"]}/{r["name"]}</b></a>'
            + (f"  <i>{meta}</i>" if meta else "")
        )
        if desc:
            lines.append(f"   {desc}")
    return "\n".join(lines)


def translate_trending_descriptions(repos):
    """用 DeepSeek 把 trending repo 的英文 desc 翻译成中文,写回 repo['desc_zh']。
    失败时悄悄忽略,fallback 显示原英文。
    """
    if not repos:
        return repos
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return repos

    # 用编号绑定避免错位
    listing = "\n".join(
        f"{i+1}. {r.get('desc','').strip()}" for i, r in enumerate(repos) if r.get("desc")
    )
    if not listing:
        return repos

    from .config import DEEPSEEK_API_URL  # 避免循环引用,这里延迟导入
    prompt = (
        f"下面是 {len(repos)} 个 GitHub 项目的英文简介。请翻译成简洁的中文,"
        f"每条不超过 60 字,保留模型名/产品名/技术名等专有英文词。"
        f"严格按原编号输出,每行格式: \"N. 中文翻译\",不要加任何其它说明。\n\n"
        + listing
    )
    body = {
        "model": "deepseek-v4-flash",  # 翻译用轻量模型,省钱省时
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 1024,
        "stream": False,
    }
    try:
        r = requests.post(
            DEEPSEEK_API_URL,
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=60,
        )
        if not r.ok:
            print(f"  trending 翻译 HTTP {r.status_code}: {r.text[:200]}")
            return repos
        content = r.json()["choices"][0]["message"]["content"].strip()
        for line in content.split("\n"):
            m = re.match(r"^\s*(\d+)[.．。]?\s*(.+)$", line.strip())
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(repos):
                repos[idx]["desc_zh"] = m.group(2).strip()
        translated = sum(1 for r in repos if r.get("desc_zh"))
        print(f"  trending 翻译 -> {translated}/{len(repos)} 条")
    except Exception as e:
        print(f"  trending 翻译失败(忽略): {e!r}")
    return repos
