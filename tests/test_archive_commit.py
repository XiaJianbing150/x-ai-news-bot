import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/commit-archive.sh"


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "requires git and bash")
class ArchiveCommitTests(unittest.TestCase):
    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.work, check=True, capture_output=True, text=True).stdout.strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / "work"
        self.remote = Path(self.temp.name) / "remote.git"
        self.work.mkdir()
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        (self.work / "base.txt").write_text("baseline")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "origin", "main")
        (self.work / "archive").mkdir()

    def run_script(self):
        return subprocess.run(["bash", str(SCRIPT), "test"], cwd=self.work, capture_output=True, text=True)

    def test_archive_and_reservation_are_pushed_to_main(self):
        (self.work / "archive/2026-11-07.md").write_text("news")
        (self.work / "archive/2026-11-07.status.json").write_text('{"email":"unknown"}')
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stderr)
        remote = subprocess.run(["git", "--git-dir", str(self.remote), "show", "main:archive/2026-11-07.status.json"], check=True, capture_output=True, text=True)
        self.assertEqual('{"email":"unknown"}', remote.stdout)
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(0, self.run_script().returncode)
        self.assertEqual(head, self.git("rev-parse", "HEAD"))

    def test_unavailable_remote_fails_instead_of_allowing_send(self):
        self.git("remote", "set-url", "origin", str(Path(self.temp.name) / "missing.git"))
        (self.work / "archive/2026-11-07.md").write_text("news")
        self.assertNotEqual(0, self.run_script().returncode)
