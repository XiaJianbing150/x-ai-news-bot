import re
import time
import requests
import feedparser
from datetime import datetime, timedelta, timezone

from .config import (
    BLOGGERS,
    TWITTER_FEED_TEMPLATES,
    ARXIV_FEEDS,
    ARXIV_MAX_PAPERS,
    HOURS_LOOKBACK,
    MAX_TWEETS_PER_BLOGGER,
    REQUEST_TIMEOUT,
)

UA = "Mozilla/5.0 (compatible; x-ai-news-bot/1.0; +https://github.com/)"


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


def _http_get(url: str):
    return requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": UA, "Accept": "application/rss+xml,application/atom+xml,*/*"},
    )


def fetch_user_tweets(username: str):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_LOOKBACK)
    last_err = None
    for tpl in TWITTER_FEED_TEMPLATES:
        url = tpl.format(user=username)
        try:
            r = _http_get(url)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            feed = feedparser.parse(r.content)
            if not feed.entries:
                last_err = "no entries"
                continue

            tweets = []
            for entry in feed.entries:
                pub_dt = _entry_datetime(entry)
                if pub_dt and pub_dt < cutoff:
                    continue
                content = entry.get("summary", "") or entry.get("description", "") or entry.get("title", "")
                text = _strip_html(content)
                if not text:
                    continue
                tweets.append({
                    "user": username,
                    "text": text,
                    "link": entry.get("link", ""),
                    "published": pub_dt.isoformat() if pub_dt else "",
                    "source": "twitter",
                })
                if len(tweets) >= MAX_TWEETS_PER_BLOGGER:
                    break
            return tweets, url
        except Exception as e:
            last_err = repr(e)[:100]
            continue
    print(f"  [WARN] all providers failed for @{username}: {last_err}")
    return [], None


def fetch_all_tweets():
    all_tweets = []
    for u in BLOGGERS:
        tweets, source = fetch_user_tweets(u)
        host = source.split("/")[2] if source else "n/a"
        print(f"  @{u:<18} -> {len(tweets):>2} tweets  ({host})")
        all_tweets.extend(tweets)
        time.sleep(0.5)
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
