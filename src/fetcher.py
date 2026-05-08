import os
import re
import time
import requests
import feedparser
from datetime import datetime, timedelta, timezone

from .config import (
    BLOGGERS,
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
        # 跳过明显的元数据/噪音
        if len(cleaned) < 20:
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
        tweets, source = fetch_user_tweets(u)
        host = source.split("/")[2] if source and "//" in source else (source or "n/a")
        print(f"  @{u:<18} -> {len(tweets):>2} tweets  ({host})")
        all_tweets.extend(tweets)
        # 用 Jina 时降速避免 rate limit
        time.sleep(2.0)
    return all_tweets


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
