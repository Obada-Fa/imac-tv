"""Phone speaker as the TV's default output.

While the phone is listening to GET /api/speaker a virtual PulseAudio/PipeWire
sink becomes the default output, so apps such as Discord play through the phone;
its monitor is streamed to the phone as raw s16le PCM. The previous default
output is restored when the phone hangs up."""
import logging
import subprocess
import threading

LOG = logging.getLogger("imactv.phonespeaker")
SINK = "imactv_phone_speaker"
RATE = 48000
CHANNELS = 2


def _pactl(*args, timeout=3):
    out = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "pactl failed").strip()[:200])
    return out.stdout.strip()


class PhoneSpeaker:
    def __init__(self, pactl=_pactl, popen=subprocess.Popen):
        self._pactl = pactl
        self._popen = popen
        self._lock = threading.Lock()
        self._token = 0
        self._proc = None
        self._module = None
        self._previous = None

    @property
    def active(self):
        with self._lock:
            return self._proc is not None

    def start(self):
        """Create the virtual output and return a (read, stop) pair; a newer stream replaces this one."""
        with self._lock:
            self._teardown()
            self._token += 1
            token = self._token
            try:
                self._previous = self._pactl("get-default-sink") or None
                if self._previous == SINK:
                    self._previous = None
                self._cleanup_stale()
                self._module = self._pactl(
                    "load-module", "module-null-sink", f"sink_name={SINK}",
                    f"rate={RATE} channels={CHANNELS}",
                    "sink_properties=device.description=Phone_Speaker")
                self._proc = self._popen(
                    ["parec", f"--device={SINK}.monitor", "--format=s16le", f"--rate={RATE}",
                     f"--channels={CHANNELS}", "--latency-msec=40", "--client-name=imactv-phone-speaker"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self._pactl("set-default-sink", SINK)
                self._move_outputs()
            except Exception:
                self._teardown()
                raise
            proc = self._proc
        LOG.info("Phone speaker is now the default output")

        def read():
            return proc.stdout.read1(8192) if hasattr(proc.stdout, "read1") else proc.stdout.read(8192)

        def stop():
            with self._lock:
                if self._token == token:
                    self._teardown()

        return read, stop

    def _cleanup_stale(self):
        try:
            listing = self._pactl("list", "short", "modules")
        except Exception:
            return
        for line in listing.splitlines():
            fields = line.split("\t")
            if len(fields) > 2 and f"sink_name={SINK}" in fields[2]:
                try:
                    self._pactl("unload-module", fields[0])
                except Exception:
                    pass

    def _move_outputs(self):
        # Streams that were already playing (a running Discord) stay on the old sink unless moved.
        try:
            for line in self._pactl("list", "short", "sink-inputs").splitlines():
                parts = line.split("\t")
                if parts and parts[0].isdigit():
                    try:
                        self._pactl("move-sink-input", parts[0], SINK)
                    except Exception:
                        pass
        except Exception:
            pass

    def _restore_outputs(self, previous):
        try:
            for line in self._pactl("list", "short", "sink-inputs").splitlines():
                parts = line.split("\t")
                if len(parts) > 1 and parts[0].isdigit():
                    try:
                        self._pactl("move-sink-input", parts[0], previous)
                    except Exception:
                        pass
        except Exception:
            pass

    def _teardown(self):
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        module, self._module = self._module, None
        previous, self._previous = self._previous, None
        if module:
            try:
                if previous:
                    if self._pactl("get-default-sink") == SINK:
                        self._pactl("set-default-sink", previous)
                    self._restore_outputs(previous)
            except Exception:
                pass
            try:
                self._pactl("unload-module", module)
            except Exception:
                pass
            LOG.info("Phone speaker released; default output restored")
