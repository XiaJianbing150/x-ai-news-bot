import os
import unittest
from unittest.mock import patch

import requests

from src import notify, telegram


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.ok = status < 400
        self.payload = payload if payload is not None else {"ok": True, "result": {"message_id": 42}}
        self.text = "response body must not be logged"

    def json(self):
        return self.payload


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "secret-token", "TELEGRAM_CHAT_ID": "123"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def send(self, responses, text="hello"):
        calls = []
        queue = iter(responses)

        def post(_url, payload):
            calls.append(dict(payload))
            result = next(queue)
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(telegram, "_post", side_effect=post), patch.object(telegram.time, "sleep"):
            result = telegram.send_to_telegram(text)
        return result, calls

    def test_read_timeout_is_unknown_and_never_retried(self):
        result, calls = self.send([requests.ReadTimeout("secret-token")])
        self.assertEqual("unknown", result["status"])
        self.assertEqual(1, len(calls))
        self.assertNotIn("secret-token", str(result))

    def test_connect_timeout_retries_and_returns_receipt(self):
        result, calls = self.send([requests.ConnectTimeout(), Response()])
        self.assertEqual("sent", result["status"])
        self.assertEqual([42], result["message_ids"])
        self.assertEqual(2, len(calls))

    def test_connect_retry_is_bounded(self):
        result, calls = self.send([requests.ConnectTimeout()] * 3)
        self.assertEqual("failed", result["status"])
        self.assertEqual(3, len(calls))

    def test_rate_limit_honors_short_retry_after(self):
        result, calls = self.send([Response(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 2}}), Response()])
        self.assertEqual("sent", result["status"])
        self.assertEqual(2, len(calls))

    def test_long_rate_limit_fails_without_retry_or_fallback(self):
        result, calls = self.send([Response(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 600}})])
        self.assertEqual("failed", result["status"])
        self.assertEqual(1, len(calls))

    def test_server_error_is_unknown_not_plain_fallback(self):
        result, calls = self.send([Response(502, {"ok": False})])
        self.assertEqual("unknown", result["status"])
        self.assertEqual(1, len(calls))

    def test_forbidden_is_not_retried_as_plain_text(self):
        result, calls = self.send([Response(403, {"ok": False, "description": "Forbidden"})])
        self.assertEqual("failed", result["status"])
        self.assertEqual(1, len(calls))

    def test_only_explicit_html_parse_error_falls_back(self):
        result, calls = self.send([Response(400, {"ok": False, "description": "Bad Request: can't parse entities"}), Response()], "<b>hello</b>")
        self.assertEqual("sent", result["status"])
        self.assertEqual("hello", calls[1]["text"])
        self.assertNotIn("parse_mode", calls[1])

    def test_ambiguous_second_chunk_keeps_first_receipt_and_stops(self):
        result, calls = self.send([Response(), requests.ReadTimeout()], "a" * 3000 + "\n" + "b" * 3000 + "\n" + "c" * 3000)
        self.assertEqual("unknown", result["status"])
        self.assertEqual(1, result["sent_parts"])
        self.assertEqual([42], result["message_ids"])
        self.assertEqual(2, len(calls))

    def test_single_long_line_is_split_under_limit(self):
        parts = telegram._chunks("x" * 8500)
        self.assertTrue(all(len(p) <= 4000 for p in parts))
        self.assertEqual("x" * 8500, "".join(parts))


class NotifyTests(unittest.TestCase):
    def test_email_failure_does_not_block_telegram(self):
        with patch.object(notify, "_channels", return_value=["email", "telegram"]), patch.object(notify.mailer, "send_to_email", side_effect=OSError("credential")), patch.object(notify.telegram, "send_to_telegram", return_value={"status": "sent", "sent_parts": 1}):
            result = notify.send_report("news")
        self.assertEqual("unknown", result["email"]["status"])
        self.assertEqual("sent", result["telegram"]["status"])
        self.assertNotIn("credential", str(result))

    def test_telegram_failure_preserves_email_result(self):
        with patch.object(notify, "_channels", return_value=["email", "telegram"]), patch.object(notify.mailer, "send_to_email"), patch.object(notify.telegram, "send_to_telegram", return_value={"status": "unknown", "sent_parts": 0}):
            result = notify.send_report("news")
        self.assertEqual("sent", result["email"]["status"])
        self.assertEqual("unknown", result["telegram"]["status"])
