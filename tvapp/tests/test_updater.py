"""Real Git histories with a local bare origin; never touch the installed checkout."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updater


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="imactv-update-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "app"
        self.root.mkdir()
        self.origin = self.base / "origin.git"
        subprocess.run(["git", "init", "--bare", str(self.origin)], check=True, capture_output=True)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Update test")
        self.git("config", "user.email", "update@example.test")
        (self.root / "tvapp/deploy").mkdir(parents=True)
        (self.root / "tvapp/deploy/install.sh").write_text("#!/bin/bash\nexit 0\n")
        self.source = self.root / "tvapp/app.py"
        self.source.write_text("VERSION = 1\n")
        (self.root / '.gitignore').write_text('ignored.txt\n')
        self.git("add", ".")
        self.git("commit", "-m", "Initial version")
        self.old = self.git("rev-parse", "HEAD")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "origin", "main")
        self.u = updater.Updater(self.root, self.base / "data")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              text=True, capture_output=True).stdout.strip()

    def publish(self, source="VERSION = 2\n"):
        self.source.write_text(source)
        self.git("add", ".")
        self.git("commit", "-m", "New version")
        target = self.git("rev-parse", "HEAD")
        self.git("push", "origin", "main")
        # This is a disposable generated fixture, with no user files.
        self.git("reset", "--hard", self.old)
        return target

    def test_detects_remote_commits_without_changing_checkout(self):
        target = self.publish()
        self.u._check()
        state = self.u.status()
        self.assertEqual(state["status"], "available")
        self.assertTrue(state["can_update"])
        self.assertEqual(state["commits"], 1)
        self.assertEqual(state["latest"], target[:8])
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)

    def test_current_and_manual_pull_needing_restart(self):
        self.u._check()
        self.assertEqual(self.u.status()["status"], "current")
        self.assertFalse(self.u.status()["can_update"])
        target = self.publish()
        self.git("merge", "--ff-only", target)
        self.u._check()
        self.assertTrue(self.u.status()["can_update"])
        self.assertIn("Restart", self.u.status()["message"])

    def test_desktop_notifies_once_per_new_version(self):
        self.u.notify = mock.Mock(return_value=True)
        self.publish()
        self.u._check()
        self.u._check()
        self.u.notify.assert_called_once_with()

    def test_ignored_local_files_cannot_be_overwritten_by_update(self):
        local = self.root / 'ignored.txt'
        local.write_text('upstream file\n')
        self.git('add', '-f', 'ignored.txt')
        self.git('commit', '-m', 'Track a previously ignored file')
        self.git('push', 'origin', 'main')
        self.git('reset', '--hard', self.old)
        local.write_text('local work\n')
        self.assertEqual(self.worker(), 1)
        self.assertEqual(local.read_text(), 'local work\n')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.old)

    def test_untracked_and_tracked_edits_are_preserved(self):
        self.publish()
        for path in (self.source, self.root / "notes.txt"):
            with self.subTest(path=path):
                before = path.read_text() if path.exists() else None
                path.write_text("my local work\n")
                self.u._check()
                self.assertEqual(self.u.status()["status"], "error")
                self.assertFalse(self.u.status()["can_update"])
                self.assertEqual(path.read_text(), "my local work\n")
                self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
                if before is None:
                    path.unlink()
                else:
                    path.write_text(before)

    def test_ahead_diverged_and_other_branch_refused(self):
        self.publish()
        self.source.write_text("LOCAL = True\n")
        self.git("commit", "-am", "Local work")
        local = self.git("rev-parse", "HEAD")
        self.u._check()
        self.assertIn("commits outside", self.u.status()["message"])
        self.assertEqual(self.git("rev-parse", "HEAD"), local)
        self.git("switch", "-c", "custom")
        self.u._check()
        self.assertIn("Switch", self.u.status()["message"])

    def test_network_failure_is_retryable_and_does_not_pull(self):
        self.git("remote", "set-url", "origin", str(self.base / "missing.git"))
        self.u._check()
        self.assertIn("internet", self.u.status()["message"])
        self.assertFalse(self.u.status()["busy"])
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
        self.git("remote", "set-url", "origin", str(self.origin))
        self.u._check()
        self.assertEqual(self.u.status()["status"], "current")

    def test_apply_spawns_independent_worker_and_rejects_duplicate(self):
        self.publish()
        self.u._check()
        with mock.patch.object(updater.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.u.apply()
            argv = run.call_args.args[0]
            self.assertEqual(argv[:2], ["systemd-run", "--user"])
            self.assertIn("--collect", argv)
            self.assertIn(str(self.root), argv)
            with self.assertRaises(updater.UpdateError):
                self.u.apply()
            self.assertEqual(run.call_count, 1)

    def test_failed_worker_launch_allows_retry(self):
        self.publish()
        self.u._check()
        with mock.patch.object(updater.subprocess, "run", side_effect=FileNotFoundError()):
            with self.assertRaises(updater.UpdateError):
                self.u.apply()
        self.assertFalse(self.u.status()["busy"])
        self.assertTrue(self.u.status()["can_update"])

    def test_file_lock_prevents_check_or_apply_overlap(self):
        self.publish()
        self.u._check()
        other = updater.Updater(self.root, self.u.data)
        with self.u.locked():
            other._check()
            self.assertEqual(other.status()["status"], "available")
            with self.assertRaises(updater.UpdateError):
                other.apply()

    def test_stale_worker_does_not_disable_controls_forever(self):
        self.u.path.write_text(json.dumps({"status": "updating", "changed_at": time.time() - 601}))
        self.assertEqual(self.u.status()["status"], "error")
        self.assertFalse(self.u.status()["busy"])

    def worker(self, deploy_result=0):
        real_run = subprocess.run

        def run(argv, **kwargs):
            if argv[:1] == ["bash"] and len(argv) > 1 and argv[1].endswith("deploy/install.sh"):
                return subprocess.CompletedProcess(argv, deploy_result)
            return real_run(argv, **kwargs)
        with mock.patch.object(updater.subprocess, "run", side_effect=run), mock.patch.object(updater.time, "sleep"):
            return self.u.install()

    def test_worker_fast_forwards_deploys_and_saves_success(self):
        target = self.publish()
        self.assertEqual(self.worker(), 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), target)
        self.assertEqual(self.u.status()["status"], "updated")
        self.assertFalse(self.u.status()["available"])

    def test_invalid_update_keeps_installed_code(self):
        self.publish("def broken(\n")
        self.assertEqual(self.worker(), 1)
        self.assertIn("failed validation", self.u.status()["message"])
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)

    def test_failed_restart_reports_error_and_can_retry(self):
        target = self.publish()
        self.assertEqual(self.worker(deploy_result=1), 1)
        self.assertEqual(self.git("rev-parse", "HEAD"), target)
        self.assertEqual(self.u.status()["status"], "error")
        self.assertTrue(self.u.status()["can_update"])
        self.assertEqual(self.worker(), 0)

    def test_worker_rechecks_edits_created_after_notification(self):
        self.publish()
        self.u._check()
        self.source.write_text("unsaved work\n")
        self.assertEqual(self.worker(), 1)
        self.assertEqual(self.source.read_text(), "unsaved work\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)

    def test_worker_rechecks_edits_during_validation(self):
        self.publish()
        with mock.patch.object(self.u, "validate", side_effect=lambda _: self.source.write_text("new edits\n")):
            self.assertEqual(self.worker(), 1)
        self.assertEqual(self.source.read_text(), "new edits\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
