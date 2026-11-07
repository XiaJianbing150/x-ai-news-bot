import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import fetcher
from src.health import get_health, reset_health


def response(text='', status=200):
    return SimpleNamespace(text=text, content=text.encode(), status_code=status)


class SourceHealthTests(unittest.TestCase):
    def setUp(self):
        reset_health()

    def test_official_http_failure_is_not_empty(self):
        with patch('src.fetcher.requests.get', return_value=response(status=503)):
            self.assertEqual(fetcher.fetch_openai_blog(), [])
        health = get_health()[0]
        self.assertEqual(health['status'], 'failed')
        self.assertEqual(health['attempts'][0]['http_status'], 503)

    def test_valid_empty_feed_is_empty(self):
        with patch('src.fetcher.requests.get', return_value=response('<rss version="2.0"><channel><title>News</title></channel></rss>')):
            fetcher.fetch_openai_blog()
        self.assertEqual(get_health()[0]['status'], 'empty')

    def test_html_instead_of_feed_is_failure(self):
        with patch('src.fetcher.requests.get', return_value=response('<html>Access denied</html>')):
            fetcher.fetch_openai_blog()
        self.assertEqual(get_health()[0]['status'], 'failed')

    def test_x_records_every_endpoint_and_jina_status(self):
        with patch.object(fetcher, 'TWITTER_FEED_TEMPLATES', ['https://one.test/{user}', 'https://two.test/{user}']), patch('src.fetcher.requests.get', return_value=response(status=403)):
            self.assertEqual(fetcher.fetch_user_tweets('example'), ([], None))
        health = get_health()[0]
        self.assertEqual(health['source'], 'x:example')
        self.assertEqual(health['status'], 'failed')
        self.assertEqual(len(health['attempts']), 3)
        self.assertTrue(all(a['http_status'] == 403 for a in health['attempts']))

    def test_old_x_feed_is_empty_not_a_reason_for_fallback(self):
        xml = '<rss version="2.0"><channel><title>News</title><item><title>Old news item</title><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item></channel></rss>'
        with patch('src.fetcher.requests.get', return_value=response(xml)) as get:
            fetcher.fetch_user_tweets('example')
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get_health()[0]['status'], 'empty')

    def test_skip_is_intentional(self):
        with patch.object(fetcher, 'BLOGGERS', ['example']), patch.object(fetcher, 'SKIP_X_ACCOUNTS', ['example']), patch('src.fetcher.requests.get') as get:
            fetcher.fetch_all_tweets()
        get.assert_not_called()
        self.assertEqual(get_health()[0]['status'], 'skipped')

    def test_arxiv_partial_failure(self):
        with patch.object(fetcher, 'ARXIV_FEEDS', ['https://one.test/rss', 'https://two.test/rss']), patch('src.fetcher.requests.get', side_effect=[response(status=403), response('<rss version="2.0"><channel><title>News</title></channel></rss>')]):
            fetcher.fetch_arxiv_papers()
        self.assertEqual(get_health()[0]['status'], 'partial')

    def test_jina_200_access_denied_page_is_not_news(self):
        blocked = 'Title: Access denied\n' + ('This request was blocked. ' * 30)
        with patch.object(fetcher, 'TWITTER_FEED_TEMPLATES', []), patch('src.fetcher.requests.get', return_value=response(blocked)):
            tweets, _ = fetcher.fetch_user_tweets('example')
        self.assertEqual(tweets, [])
        self.assertEqual(get_health()[0]['status'], 'failed')

    def test_success_has_count(self):
        xml = '<rss version="2.0"><channel><title>News</title><item><title>Current news item</title><link>https://x.com/example/status/1</link></item></channel></rss>'
        with patch('src.fetcher.requests.get', return_value=response(xml)):
            fetcher.fetch_user_tweets('example')
        self.assertEqual(get_health()[0]['status'], 'ok')
        self.assertEqual(get_health()[0]['count'], 1)

    def test_anthropic_old_posts_are_empty(self):
        text = '[Jan 1, 2024 Old announcement](https://www.anthropic.com/news/old)'
        with patch('src.fetcher.requests.get', return_value=response(text)):
            fetcher.fetch_anthropic_news()
        self.assertEqual(get_health()[0]['status'], 'empty')

    def test_invalid_trending_page_fails(self):
        with patch('src.fetcher.requests.get', return_value=response('<html>Sign in</html>')):
            fetcher.fetch_github_trending()
        self.assertEqual(get_health()[0]['status'], 'failed')

    def test_valid_empty_atom_is_empty(self):
        with patch('src.fetcher.requests.get', return_value=response('<feed xmlns="http://www.w3.org/2005/Atom"><title>Releases</title></feed>')):
            fetcher.fetch_claude_code_releases()
        self.assertEqual(get_health()[0]['status'], 'empty')

    def test_exception_details_cannot_leak_secrets(self):
        with patch('src.fetcher.requests.get', side_effect=RuntimeError('SECRET_TOKEN')):
            fetcher.fetch_openai_blog()
        self.assertNotIn('SECRET_TOKEN', str(get_health()))
        snapshot = get_health()
        snapshot[0]['attempts'].clear()
        self.assertTrue(get_health()[0]['attempts'])
        reset_health()
        self.assertEqual(get_health(), [])


if __name__ == '__main__':
    unittest.main()
