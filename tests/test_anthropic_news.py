"""Regression tests use the actual Jina layout, with editorial text replaced."""
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import fetcher
from src.health import get_health, reset_health
from tests.test_source_health import response


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 29, 12, tzinfo=timezone.utc).astimezone(tz)


class AnthropicNewsTests(unittest.TestCase):
    def setUp(self):
        reset_health()
        self.fixture = Path(__file__).with_name('fixtures').joinpath('anthropic_news_jina.md').read_text()

    def fetch(self, text, hours=48):
        with patch('src.fetcher.requests.get', return_value=response(text)), patch('src.fetcher.datetime', FrozenDateTime):
            return fetcher.fetch_anthropic_news(hours_lookback=hours)

    def test_captured_list_dates_are_outside_links_and_duplicates_are_removed(self):
        posts = self.fetch(self.fixture)
        self.assertEqual({p['link']: p['published'] for p in posts}, {
            'https://www.anthropic.com/news/ai-for-science-expands-foundations-research-institutions': '2026-10-28T23:59:00+00:00',
            'https://www.anthropic.com/news/expanding-partnership-gsa': '2026-10-27T23:59:00+00:00',
            'https://www.anthropic.com/research/early-progress-drug-development': '2026-10-27T23:59:00+00:00',
        })
        self.assertEqual(len(posts), 3)
        self.assertTrue(all(p['source'] == 'anthropic-news' for p in posts))
        self.assertEqual(get_health()[0]['status'], 'ok')
        self.assertEqual(get_health()[0]['count'], 3)

    def test_categories_do_not_restrict_news_and_feature_image_is_not_a_title(self):
        posts = self.fetch(self.fixture, hours=24 * 90)
        by_link = {p['link']: p for p in posts}
        for slug in ['donating-agent-skills-to-the-agentic-ai-foundation', 'anthropic-cyber-mission', '2026-usage-policy-update']:
            self.assertIn('https://www.anthropic.com/news/' + slug, by_link)
        self.assertEqual(by_link['https://www.anthropic.com/news/public-benefit-fund']['text'], 'Article public-benefit-fund')
        self.assertEqual(by_link['https://www.anthropic.com/claude-opus-5-5']['published'], '2026-09-22T23:59:00+00:00')
        self.assertEqual(len(posts), len(by_link))
        self.assertFalse(any(p['text'] in {'Category', 'Image'} for p in posts))

    def test_page_clock_does_not_refresh_old_pinned_cards(self):
        self.assertEqual(self.fetch(self.fixture, hours=1), [])
        self.assertEqual(get_health()[0]['status'], 'empty')

    def test_unrecognized_page_still_reports_failure(self):
        self.assertEqual(self.fetch('Title: Newsroom\nNovember 7, 2026\n## All news\n[Undated](https://www.anthropic.com/news/undated)'), [])
        self.assertEqual(get_health()[0]['status'], 'failed')
        self.assertIn('format', get_health()[0]['attempts'][0]['error'])

    def test_invalid_calendar_dates_are_failure_not_empty(self):
        self.assertEqual(self.fetch('[Feb 30, 2026 Broken date](https://www.anthropic.com/news/broken)'), [])
        self.assertEqual(get_health()[0]['status'], 'failed')

    def test_inline_and_multiline_feature_dates_and_full_month(self):
        text = '''[New category](https://www.anthropic.com/news/new-category)
October 28, 2026

## [Multiline card](https://www.anthropic.com/news/multiline)
[Another category](https://www.anthropic.com/news/new-category)
Oct 27, 2026### [Inline card](https://www.anthropic.com/research/inline)
'''
        posts = self.fetch(text)
        self.assertEqual({p['text']: p['published'] for p in posts}, {
            'Multiline card': '2026-10-28T23:59:00+00:00',
            'Inline card': '2026-10-27T23:59:00+00:00',
        })

    def test_undated_card_cannot_borrow_another_cards_date(self):
        text = '''Oct 28, 2026### [Dated](https://www.anthropic.com/news/dated)
### [Undated](https://www.anthropic.com/news/undated)
*   ### [Also undated](https://www.anthropic.com/news/also-undated)
*   ### [Second dated](https://www.anthropic.com/news/second) [Other category](https://www.anthropic.com/news/other) Oct 27, 2026
'''
        self.assertEqual({p['text'] for p in self.fetch(text)}, {'Dated', 'Second dated'})

    def test_legacy_combined_date_link_remains_supported(self):
        posts = self.fetch('[Announcements Oct 28, 2026 #### Earlier rendering](https://www.anthropic.com/news/legacy)')
        self.assertEqual([p['text'] for p in posts], ['Earlier rendering'])

    def test_indented_list_rows_do_not_share_dates(self):
        text = '''  * ### [Undated](https://www.anthropic.com/news/undated)
  * ### [Dated](https://www.anthropic.com/news/dated) Oct 28, 2026
  * ### [Previous day](https://www.anthropic.com/news/previous) Oct 27, 2026
'''
        self.assertEqual({p['text']: p['published'] for p in self.fetch(text)}, {
            'Dated': '2026-10-28T23:59:00+00:00',
            'Previous day': '2026-10-27T23:59:00+00:00',
        })

    def test_description_dates_do_not_override_wrapped_row_metadata(self):
        text = '''* ### [Dated](https://www.anthropic.com/news/dated)
    [A program established Jan 1, 2024](https://www.anthropic.com/news/dated)
    [New category](https://www.anthropic.com/news/category) Oct 28, 2026
'''
        self.assertEqual([p['published'] for p in self.fetch(text)], ['2026-10-28T23:59:00+00:00'])


if __name__ == '__main__':
    unittest.main()
