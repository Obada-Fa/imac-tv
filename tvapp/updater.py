"""Git-based app updates. The apply worker runs outside tvremote's service cgroup."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

INTERVAL = 30 * 60
BUSY = {"checking", "queued", "updating", "restarting"}


class UpdateError(RuntimeError):
    pass


class Updater:
    def __init__(self, root, data, running=None, notify=None):
        self.root, self.data = Path(root).resolve(), Path(data).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / "update.json"
        self.next_check = 0
        self.notify = notify
        self.running = running or ""
        if running is None:
            self.capture_running()

    def capture_running(self):
        try:
            self.running = self.git("rev-parse", "HEAD")
        except UpdateError:
            self.running = ""

    def git(self, *args, message="Could not read the app checkout."):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -oBatchMode=yes -oConnectTimeout=15")
        try:
            result = subprocess.run(["git", "-C", str(self.root), *args], env=env,
                                    capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateError(message) from exc
        if result.returncode:
            raise UpdateError(message)
        return result.stdout.strip()

    @contextlib.contextmanager
    def locked(self):
        with (self.data / "update.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UpdateError("An update operation is already running.") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def status(self):
        try:
            state = json.loads(self.path.read_text())
            if not isinstance(state, dict):
                raise ValueError()
        except (OSError, ValueError):
            state = {"status": "idle", "message": "Updates are checked automatically every 30 minutes."}
        # A killed worker must not leave the controls disabled forever.
        if state.get("status") in BUSY and time.time() - state.get("changed_at", 0) > 600:
            state.update(status="error", message="The update was interrupted. Check again to retry.", can_update=False)
        return dict(state, busy=state.get("status") in BUSY, running=self.running[:8])

    def save(self, **values):
        state = self.status()
        state.pop("busy", None)
        state.pop("running", None)
        state.update(values, changed_at=time.time())
        tmp = self.path.with_name("update.%s.%s.tmp" % (os.getpid(), threading.get_ident()))
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            json.dump(state, out)
        os.replace(tmp, self.path)

    def inspect(self):
        if self.git("rev-parse", "--show-toplevel",
                    message="Updates need a Git checkout. Install from the GitHub repository first.") != str(self.root):
            raise UpdateError("Updates need a Git checkout. Install from the GitHub repository first.")
        if self.git("branch", "--show-current") != "main":
            raise UpdateError("Switch the app checkout to main before updating.")
        if self.git("status", "--porcelain", "--untracked-files=all"):
            raise UpdateError("Local changes need to be saved or removed before updating. Nothing was overwritten.")
        self.git("fetch", "--no-tags", "--no-recurse-submodules", "origin",
                 "+refs/heads/main:refs/remotes/origin/main",
                 message="Could not contact GitHub through origin. Check the TV's internet connection and Git access.")
        current = self.git("rev-parse", "HEAD")
        target = self.git("rev-parse", "refs/remotes/origin/main")
        self.git("merge-base", "--is-ancestor", current, target,
                 message="Local main has commits outside origin/main. Resolve them before updating; nothing was overwritten.")
        count = int(self.git("rev-list", "--count", "HEAD..refs/remotes/origin/main"))
        return current, target, count

    def _check(self):
        try:
            with self.locked():
                if self.status()["status"] in {"queued", "updating", "restarting"}:
                    return
                self.save(status="checking", message="Checking GitHub…", can_update=False)
                try:
                    current, target, count = self.inspect()
                    restart = bool(self.running and self.running != current)
                    available = count > 0 or restart
                    self.save(status="available" if available else "current", available=available,
                              can_update=available, current=current[:8], latest=target[:8], commits=count,
                              checked_at=time.time(),
                              message=("%d new commit%s available. Update restarts the TV app." % (count, "" if count == 1 else "s"))
                              if count else ("Restart to finish applying the downloaded update." if restart else "Your TV is up to date."))
                    if available and self.notify and self.status().get("notified") != target:
                        if self.notify():
                            self.save(notified=target)
                except (UpdateError, OSError, ValueError) as exc:
                    self.save(status="error", can_update=False, available=False, message=str(exc))
        except UpdateError:
            pass  # another controller/worker owns the lock

    def check(self):
        if self.status()["busy"]:
            return self.status()
        self.next_check = time.monotonic() + INTERVAL
        threading.Thread(target=self._check, daemon=True).start()
        return self.status()

    def tick(self):
        if time.monotonic() >= self.next_check:
            self.check()

    def apply(self):
        with self.locked():
            state = self.status()
            if state["busy"]:
                raise UpdateError("An update operation is already running.")
            if not state.get("can_update"):
                raise UpdateError("Check for updates before installing.")
            self.save(status="queued", can_update=False, message="Starting update… The TV app will restart.")
            try:
                # A child of tvremote would be killed when deployment restarts that service.
                result = subprocess.run([
                    "systemd-run", "--user", "--collect", "--unit=imactv-update",
                    sys.executable, str(Path(__file__).resolve()), "--apply",
                    "--repo", str(self.root), "--data", str(self.data),
                ], capture_output=True, text=True, timeout=10)
                if result.returncode:
                    raise UpdateError("Could not start the updater. Make sure the TV's user systemd session is running.")
            except (OSError, subprocess.TimeoutExpired, UpdateError) as exc:
                self.save(status="error", can_update=True, message="Could not start the updater. Check the TV's user systemd session and retry.")
                raise UpdateError(self.status()["message"]) from exc
        return self.status()

    def validate(self, target):
        """Check Python and deployment syntax before changing the checkout."""
        files = self.git("ls-tree", "-r", "--name-only", target, "tvapp").splitlines()
        for name in files:
            if name.endswith(".py"):
                try:
                    # Use bytes to respect Python source encoding declarations.
                    source = subprocess.run(["git", "-C", str(self.root), "show", target + ":" + name],
                                            capture_output=True, check=True, timeout=10).stdout
                    compile(source, name, "exec")
                except (SyntaxError, ValueError, subprocess.SubprocessError) as exc:
                    raise UpdateError("The downloaded update failed validation. The installed version was kept.") from exc
        script = self.git("show", target + ":tvapp/deploy/install.sh")
        if subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, timeout=10).returncode:
            raise UpdateError("The downloaded installer failed validation. The installed version was kept.")

    def install(self):
        # Give the HTTP response time to reach the phone/TV and apply() time to release its lock.
        time.sleep(1)
        try:
            with self.locked():
                try:
                    self.save(status="updating", message="Downloading and validating the update…", can_update=False)
                    current, target, _count = self.inspect()
                    self.validate(target)
                    # Recheck after validation: never discard edits made while the worker was checking.
                    if (self.git("status", "--porcelain", "--untracked-files=all")
                            or self.git("rev-parse", "HEAD") != current
                            or self.git("branch", "--show-current") != "main"):
                        raise UpdateError("The checkout changed during the update. Nothing was overwritten; check again.")
                    self.git("merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", target,
                             message="Could not apply the update safely. Nothing was discarded; check the checkout.")
                    self.save(status="restarting", message="Update downloaded. Restarting the TV app…", latest=target[:8])
                    result = subprocess.run(["bash", str(self.root / "tvapp/deploy/install.sh")],
                                            env=dict(os.environ, TVAPP_DIR=str(self.root / "tvapp")), timeout=120)
                    if result.returncode:
                        raise UpdateError("The update downloaded, but the TV app could not restart. Check the service and retry.")
                    self.save(status="updated", available=False, can_update=False, commits=0,
                              current=target[:8], latest=target[:8], checked_at=time.time(),
                              message="Update installed. Your TV is up to date.")
                except (UpdateError, OSError, subprocess.SubprocessError) as exc:
                    self.save(status="error", can_update=True, message=str(exc))
        except UpdateError:
            return 1
        return 0 if self.status()["status"] == "updated" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(Updater(args.repo, args.data).install())
