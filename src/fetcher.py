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


def fetch_github_trending(top_n: int = 5, since: str = "daily"):
    """抓 github.com/trending top N,通过 Jina Reader。
    since: daily | weekly | monthly
    """
    url = f"https://r.jina.ai/https://github.com/trending?since={since}"
    headers = {"Accept": "text/markdown"}
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = _http_get(url, headers=headers)
    except Exception as e:
        print(f"  GitHub trending fetch failed: {e!r}")
        return []
    if r.status_code != 200:
        print(f"  GitHub trending: HTTP {r.status_code}")
        return []

    text = r.text
    repos = []
    for m in _GH_REPO_HEAD.finditer(text):
        owner, name, repo_url, desc = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        # 跳过明显的导航假命中(GitHub trending 真正的 repo URL 一定是 /owner/repo)
        if repo_url.count("/") != 4:
            continue
        chunk = text[m.end():m.end() + 1500]
        stars_today = ""
        sm = re.search(r"(\d[\d,]*)\s*stars?\s*today", chunk)
        if sm:
            stars_today = sm.group(1)
        lang_m = re.search(r"\n([A-Z][\w+#-]*)\[", chunk)
        lang = lang_m.group(1) if lang_m else ""
        repos.append({
            "owner": owner,
            "name": name,
            "url": repo_url,
            "desc": desc[:200],
            "lang": lang,
            "stars_today": stars_today,
        })
        if len(repos) >= top_n:
            break
    print(f"  GitHub trending -> {len(repos)} repos")
    return repos


def format_trending_block(repos):
    """把 trending 列表渲染成 Telegram HTML,作为早报附录。"""
    if not repos:
        return ""
    lines = ["", "<b>🔥 GitHub Trending · 今日 Top 5</b>"]
    for i, r in enumerate(repos, 1):
        meta_parts = []
        if r.get("lang"):
            meta_parts.append(r["lang"])
        if r.get("stars_today"):
            meta_parts.append(f"⭐ +{r['stars_today']}/today")
        meta = "  ".join(meta_parts)
        # 转义描述里的 HTML 特殊字符
        desc = r["desc"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(
            f'{i}. <a href="{r["url"]}"><b>{r["owner"]}/{r["name"]}</b></a>'
            + (f"  <i>{meta}</i>" if meta else "")
        )
        if desc:
            lines.append(f"   {desc}")
    return "\n".join(lines)
