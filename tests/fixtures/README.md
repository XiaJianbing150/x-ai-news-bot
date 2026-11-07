# Anthropic news regression fixture

`anthropic_news_jina.md` is a reduced version of an HTTP 200 response captured
from `https://r.jina.ai/https://www.anthropic.com/news` on 2026-11-07.
The original response was 28,343 bytes (SHA-256
`505d2f8e7c8339aaff20e74cf27bee33364e5809dc11afb8e2ecd3eed90bf376`).

Article text and image addresses are replaced with placeholders. Dates,
categories, article URLs, heading levels, whitespace, linked images and the
duplicate desktop/mobile row layout are retained. The fixture covers dates
outside links, featured cards, different categories and old pinned content.
The page's own date is not an article publication date.

The unmodified capture contained 29 unique dated articles, most recently
2026-10-28. With a 216-hour lookback on 2026-11-07, returning no articles is
correct. This is distinct from failing to recognize any dated article.
