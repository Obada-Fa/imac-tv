"""Phone microphone as the TV's default input.

The phone streams raw s16le PCM to POST /api/mic. While a stream is live a
virtual PulseAudio/PipeWire source becomes the default input, so apps such as
Discord pick the phone up; the previous default is restored when it ends."""
import logging
import subprocess
import threading

LOG = logging.getLogger("imactv.phonemic")
SINK = "imactv_phone_mic"
SOURCE = "imactv_phone_mic_source"
RATES = (8000, 16000, 24000, 32000, 44100, 48000)


def _pactl(*args, timeout=3):
    out = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "pactl failed").strip()[:200])
    return out.stdout.strip()


class PhoneMic:
    def __init__(self, pactl=_pactl, popen=subprocess.Popen):
        self._pactl = pactl
        self._popen = popen
        self._lock = threading.Lock()
        self._token = 0
        self._proc = None
        self._modules = []
        self._format = None
        self._previous = None

    @property
    def active(self):
        with self._lock:
            return self._proc is not None

    def start(self, rate, channels):
        """Create the virtual mic and return a (write, stop) pair; a newer stream replaces this one."""
        if rate not in RATES or channels not in (1, 2):
            raise ValueError("Unsupported microphone format")
        with self._lock:
            self._teardown()
            self._token += 1
            token = self._token
            try:
                self._previous = self._pactl("get-default-source") or None
                if self._previous == SOURCE:
                    self._previous = None
                fmt = f"rate={rate} channels={channels}"
                if self._modules and self._format != fmt:
                    self._unload_modules()
                if not self._modules:
                    # The virtual devices outlive a dropped stream (see _teardown), so only build them once.
                    self._cleanup_stale()
                    self._modules.append(self._pactl(
                        "load-module", "module-null-sink", f"sink_name={SINK}", fmt,
                        "sink_properties=device.description=Phone_Microphone"))
                    self._modules.append(self._pactl(
                        "load-module", "module-remap-source", f"master={SINK}.monitor",
                        f"source_name={SOURCE}", "source_properties=device.description=Phone_Microphone"))
                    self._format = fmt
                self._proc = self._popen(
                    ["pacat", "--playback", f"--device={SINK}", "--format=s16le",
                     f"--rate={rate}", f"--channels={channels}", "--latency-msec=60",
                     "--client-name=imactv-phone-mic"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._pactl("set-default-source", SOURCE)
                self._move_inputs()
            except Exception:
                self._teardown()
                self._unload_modules()
                raise
            proc = self._proc
        LOG.info("Phone microphone is now the default input")

        def write(data):
            proc.stdin.write(data)
            proc.stdin.flush()

        def stop():
            with self._lock:
                if self._token == token:
                    self._teardown()

        return write, stop

    def _cleanup_stale(self):
        try:
            listing = self._pactl("list", "short", "modules")
        except Exception:
            return
        for line in listing.splitlines():
            fields = line.split("\t")
            if len(fields) > 2 and (f"sink_name={SINK}" in fields[2] or f"source_name={SOURCE}" in fields[2]):
                try:
                    self._pactl("unload-module", fields[0])
                except Exception:
                    pass

    def _move_inputs(self):
        # Streams that were already recording stay on their old device unless moved.
        try:
            for line in self._pactl("list", "short", "source-outputs").splitlines():
                parts = line.split("\t")
                if parts and parts[0].isdigit():
                    try:
                        self._pactl("move-source-output", parts[0], SOURCE)
                    except Exception:
                        pass
        except Exception:
            pass

    def close(self):
        """Server shutdown: remove the virtual devices as well."""
        with self._lock:
            self._teardown()
            self._unload_modules()

    def _teardown(self):
        """End the stream and give the real microphone back as the default input. The virtual
        devices stay loaded: Discord pins the device it was told to use and loses it for good if
        it vanishes while the phone reconnects. They just stay silent until the phone returns."""
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        previous, self._previous = self._previous, None
        if self._modules:
            try:
                if self._pactl("get-default-source") == SOURCE and previous:
                    self._pactl("set-default-source", previous)
            except Exception:
                pass
            LOG.info("Phone microphone stream ended; default input restored")

    def _unload_modules(self):
        modules, self._modules = self._modules, []
        self._format = None
        for module in reversed(modules):
            try:
                self._pactl("unload-module", module)
            except Exception:
                pass
