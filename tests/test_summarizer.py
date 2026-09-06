import os
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


# Production installs requests from requirements.txt. The test replaces only
# the external HTTP boundary, so importing summarizer needs no network package.
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    sys.modules["requests"] = types.SimpleNamespace(post=None)

from src import summarizer


VALID_REPORT = """<b>📰 Anthropic & OpenAI 速报 · 2026-09-06 Sunday</b>

<b>🅰️ Claude 产品更新详解</b>
• 功能介绍: 最终正文

<i>今日关键词:Claude / Codex / GPT</i>"""


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 5, 23, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.ok = True
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._payload


def completion(*, content, reasoning_content="", finish_reason="stop"):
    return FakeResponse(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "reasoning_content": reasoning_content,
                    },
                }
            ]
        }
    )


class GenerateMorningReportTests(unittest.TestCase):
    def _generate(self, responses):
        sent_bodies = []
        queue = list(responses)

        def fake_post(_url, *, json, headers, timeout):
            sent_bodies.append(json)
            return queue.pop(0)

        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}),
            patch.object(summarizer, "DEEPSEEK_THINKING", False),
            patch.object(summarizer, "datetime", FixedDateTime),
            patch.object(summarizer.requests, "post", side_effect=fake_post),
        ):
            report = summarizer.generate_morning_report(
                [{"user": "OpenAI官博", "text": "news", "link": "https://example.com", "source": "openai-blog"}]
            )
        return report, sent_bodies

    def test_reasoning_only_response_is_not_promoted_to_report(self):
        internal_plan = "分析中\n<b>📰 Anthropic & OpenAI 速报 · 示例</b>\n可能组织成……"
        report, sent_bodies = self._generate(
            [
                completion(content="", reasoning_content=internal_plan),
                completion(content=VALID_REPORT),
            ]
        )

        self.assertEqual(VALID_REPORT, report)
        self.assertNotIn("可能组织成", report)
        self.assertEqual(2, len(sent_bodies))

    def test_thinking_is_explicitly_disabled_on_every_non_thinking_attempt(self):
        _report, sent_bodies = self._generate(
            [
                completion(content="", reasoning_content="内部规划"),
                completion(content=VALID_REPORT),
            ]
        )

        self.assertEqual(
            [{"type": "disabled"}, {"type": "disabled"}],
            [body.get("thinking") for body in sent_bodies],
        )

    def test_length_truncated_response_is_retried(self):
        partial = "<b>📰 Anthropic & OpenAI 速报 · 2026-09-06 Sunday</b>\n未完成正文"
        report, sent_bodies = self._generate(
            [
                completion(content=partial, reasoning_content="内部规划", finish_reason="length"),
                completion(content=VALID_REPORT),
            ]
        )

        self.assertEqual(VALID_REPORT, report)
        self.assertEqual(2, len(sent_bodies))

    def test_planning_text_before_report_header_is_rejected(self):
        invalid = "我先组织一下。\n" + VALID_REPORT

        with self.assertRaisesRegex(RuntimeError, "完整的最终早报正文"):
            self._generate([completion(content=invalid)])

    def test_wrong_first_title_cannot_be_hidden_by_expected_title_later(self):
        invalid = "<b>📰 每周摘要</b>\n" + VALID_REPORT

        with self.assertRaisesRegex(RuntimeError, "完整的最终早报正文"):
            self._generate([completion(content=invalid)])

    def test_title_date_must_match_report_date(self):
        invalid = VALID_REPORT.replace("2026-09-06 Sunday", "2026-09-05 Saturday")

        with self.assertRaisesRegex(RuntimeError, "完整的最终早报正文"):
            self._generate([completion(content=invalid)])

    def test_keyword_footer_must_be_the_complete_last_line(self):
        invalid = VALID_REPORT.rsplit("\n", 1)[0] + "\n说明：今日关键词:Claude / Codex / GPT"

        with self.assertRaisesRegex(RuntimeError, "完整的最终早报正文"):
            self._generate([completion(content=invalid)])


if __name__ == "__main__":
    unittest.main()
