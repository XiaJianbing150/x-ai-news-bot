import json
import io
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import main
from src import notify


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, old)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Synthetic failure scenarios must not create real Actions annotations
        # or append fake report/delivery results to the workflow summary.
        self.stack.enter_context(patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(main, "fetch_all_tweets", return_value=[]))
        self.stack.enter_context(patch.object(main, "fetch_anthropic_news", return_value=[]))
        self.stack.enter_context(patch.object(main, "fetch_claude_code_releases", return_value=[]))
        self.stack.enter_context(patch.object(main, "fetch_openai_blog", return_value=[{"text": "news"}]))
        self.stack.enter_context(patch.object(main, "fetch_github_trending", return_value=[]))
        self.stack.enter_context(patch.object(main, "enrich_trending", return_value=[]))
        self.stack.enter_context(patch.object(main, "translate_trending_descriptions", return_value=[]))
        self.stack.enter_context(patch.object(main, "generate_morning_report", return_value="Final news"))
        self.stack.enter_context(patch.object(notify, "_channels", return_value=["email"]))

    def status(self):
        return json.loads(next(Path("archive").glob("*.status.json")).read_text())

    def test_archive_exists_before_notification_and_remains_on_failure(self):
        observed = []

        def send(*args):
            observed.append(next(Path("archive").glob("*.md")).read_text())
            raise OSError("SMTP connection lost")

        with patch.object(notify.mailer, "send_to_email", side_effect=send):
            code = main.main()
        self.assertEqual(1, code)
        self.assertIn("Final news", observed[0])
        self.assertEqual("ready", self.status()["generation"])
        self.assertEqual("unknown", self.status()["notifications"]["email"]["status"])

    def test_dry_run_archives_without_any_notification_reservation(self):
        with patch.object(notify.mailer, "send_to_email", side_effect=AssertionError("must not send")):
            code = main.main(dry_run=True)
        self.assertEqual(0, code)
        self.assertEqual({}, self.status()["notifications"])

    def test_broken_x_does_not_block_official_sources(self):
        with patch.object(main, "fetch_all_tweets", side_effect=RuntimeError("X failed")):
            code = main.main(dry_run=True)
        self.assertEqual(0, code)
        self.assertEqual("ready", self.status()["generation"])

    def test_all_empty_sources_fail_instead_of_claiming_success(self):
        with patch.object(main, "fetch_openai_blog", return_value=[]):
            code = main.main(dry_run=True)
        self.assertEqual(1, code)
        self.assertEqual("failed", self.status()["generation"])

    def test_archive_failure_prevents_sending(self):
        with patch.object(main, "save_archive_and_cleanup", side_effect=OSError("disk full")), patch.object(notify.mailer, "send_to_email", side_effect=AssertionError("must not send")):
            code = main.main()
        self.assertEqual(1, code)
        self.assertEqual("failed", self.status()["generation"])

    def test_same_day_repeat_does_not_regenerate_or_resend(self):
        sent = []
        with patch.object(notify.mailer, "send_to_email", side_effect=lambda *args: sent.append(args)):
            self.assertEqual(0, main.main())
            with patch.object(main, "generate_morning_report", side_effect=AssertionError("must reuse")):
                self.assertEqual(0, main.main())
        self.assertEqual(1, len(sent))

    def test_workflow_stages_generate_reserve_then_send(self):
        sent = []
        with patch.object(notify.mailer, "send_to_email", side_effect=lambda *args: sent.append(args)):
            self.assertEqual(0, main.main(stage="generate"))
            self.assertEqual([], sent)
            self.assertEqual(0, main.main(stage="reserve"))
            self.assertEqual([], sent)
            self.assertEqual("unknown", self.status()["notifications"]["email"]["status"])
            self.assertEqual(0, main.main(stage="send"))
        self.assertEqual(1, len(sent))

    def test_captured_report_date_controls_archive_name_across_midnight(self):
        with patch.object(main, "_bj_now_str", return_value="2026-11-06 23:59"):
            self.assertEqual(0, main.main(dry_run=True))
        status = self.status()
        self.assertEqual("2026-11-06", status["date"])
        self.assertEqual("archive/2026-11-06.md", status["archive"])
        self.assertIn("AI 早报 · 2026-11-06", Path(status["archive"]).read_text())
