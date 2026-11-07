import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import notify


class DeliveryStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.old_cwd)
        Path("archive").mkdir()
        Path("archive/2026-11-07.md").write_text("# AI 早报\n\n---\n\nnews\n")

    def state_module(self):
        from src import run_state
        return run_state

    def prepare(self, channels):
        state = self.state_module()
        state.save_status("2026-11-07", {"date": "2026-11-07", "archive": "archive/2026-11-07.md", "generation": "ready", "notifications": {}})
        with patch.object(notify, "_channels", return_value=channels):
            return state.reserve_delivery("2026-11-07")

    def test_reservation_is_saved_before_any_external_send(self):
        state = self.state_module()
        self.prepare(["email", "telegram"])
        data = json.loads(Path("archive/2026-11-07.status.json").read_text())
        self.assertEqual("unknown", data["notifications"]["email"]["status"])
        self.assertEqual("reserved_before_send", data["notifications"]["telegram"]["reason"])

    def test_second_reservation_never_retries_uncertain_delivery(self):
        state = self.state_module()
        self.prepare(["email", "telegram"])
        with patch.object(notify, "_channels", return_value=["email", "telegram"]):
            plan = state.reserve_delivery("2026-11-07")
        self.assertEqual([], plan["channels"])

    def test_send_preserves_independent_results_and_replay_does_not_send(self):
        state = self.state_module()
        self.prepare(["email", "telegram"])
        sent = []
        with patch.object(notify.mailer, "send_to_email", side_effect=lambda *a: sent.append("email")), patch.object(notify.telegram, "send_to_telegram", return_value={"status": "unknown", "sent_parts": 0}):
            status = state.deliver_reserved()
            state.deliver_reserved()
        self.assertEqual(["email"], sent)
        self.assertEqual("sent", status["notifications"]["email"]["status"])
        self.assertEqual("unknown", status["notifications"]["telegram"]["status"])
        saved = json.loads(Path("archive/2026-11-07.status.json").read_text())
        self.assertEqual("sent", saved["notifications"]["email"]["status"])

    def test_changed_archive_is_not_sent(self):
        state = self.state_module()
        self.prepare(["email"])
        Path("archive/2026-11-07.md").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            state.deliver_reserved()

    def test_interrupted_send_cannot_replay_same_local_plan(self):
        state = self.state_module()
        self.prepare(["email"])
        accepted = []

        def interrupted(*args):
            accepted.append("accepted remotely")
            raise KeyboardInterrupt()

        with patch.object(notify.mailer, "send_to_email", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                state.deliver_reserved()
            state.deliver_reserved()
        self.assertEqual(["accepted remotely"], accepted)

    def test_unconfigured_channels_fail_before_reservation(self):
        state = self.state_module()
        with self.assertRaisesRegex(RuntimeError, "发送渠道"):
            self.prepare([])

    def test_missing_archive_fails_before_reservation(self):
        state = self.state_module()
        Path("archive/2026-11-07.md").unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare(["email"])

    def test_generation_failure_does_not_create_delivery_plan(self):
        state = self.state_module()
        state.save_status("2026-11-07", {"date": "2026-11-07", "generation": "failed", "notifications": {}})
        with patch.object(notify, "_channels", return_value=["email"]), self.assertRaisesRegex(RuntimeError, "ready"):
            state.reserve_delivery("2026-11-07")
        self.assertFalse(Path(".run/delivery-plan.json").exists())
