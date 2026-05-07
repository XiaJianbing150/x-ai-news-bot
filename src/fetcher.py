import re
import time
import requests
import feedparser
from datetime import datetime, timedelta, timezone

from .config import (
    BLOGGERS,
    RSSHUB_INSTANCES,
    HOURS_LOOKBACK,
    MAX_TWEETS_PER_BLOGGER,
    REQUEST_TIMEOUT,
)

UA = "Mozilla/5.0 (compatible; x-ai-news-bot/1.0)"


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


def fetch_user_tweets(username: str):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_LOOKBACK)
    last_err = None
    for instance in RSSHUB_INSTANCES:
        url = f"{instance.rstrip('/')}/twitter/user/{username}"
        try:
            r = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": UA, "Accept": "application/rss+xml,*/*"},
            )
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
                })
                if len(tweets) >= MAX_TWEETS_PER_BLOGGER:
                    break
            return tweets, instance
        except Exception as e:
            last_err = repr(e)
            continue
    print(f"  [WARN] all instances failed for @{username}: {last_err}")
    return [], None


def fetch_all_tweets():
    all_tweets = []
    for u in BLOGGERS:
        tweets, instance = fetch_user_tweets(u)
        src = instance.split("//")[-1] if instance else "n/a"
        print(f"  @{u:<18} -> {len(tweets)} tweets  ({src})")
        all_tweets.extend(tweets)
        time.sleep(0.5)
    return all_tweets
