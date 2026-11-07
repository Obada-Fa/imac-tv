"""Does this computer draw OpenGL in hardware? Decides where the ambient music visualizer is drawn.

home.json "gpu_viz": "auto" (default) draws it on the GPU when a hardware GL driver answers (integrated Intel
graphics count), and with cairo on the CPU when only a software rasterizer (llvmpipe, softpipe, swrast) or no GL
at all is there. "on" / "off" force it. IMACTV_GPU_VIZ=1/0 overrides both (test hook).

The GL check runs in a child process (`python3 -I gpu.py --probe`) through ctypes, so a crashing driver can never
take the TV app down, and no PyOpenGL is needed.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

SOFTWARE = re.compile(r"llvmpipe|softpipe|swrast|software", re.I)
PENDING = {"hw": False, "renderer": "", "vendor": "", "error": None, "pending": True}

_lock = threading.Lock()
_result = None
_thread = None


def data_dir():
    return Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))


def classify(renderer, direct=True):
    """True when `renderer` (GL_RENDERER) is real hardware."""
    return bool(renderer) and bool(direct) and not SOFTWARE.search(renderer)


def probe(display=None, timeout=5.0):
    env = dict(os.environ)
    if display:
        env["DISPLAY"] = display
    try:
        out = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--probe"], env=env,
                             capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"hw": False, "renderer": "", "vendor": "", "error": "timeout"}
    except OSError as exc:
        return {"hw": False, "renderer": "", "vendor": "", "error": str(exc)[:200]}
    try:
        res = json.loads(out.stdout.strip().splitlines()[-1])
        if not isinstance(res, dict):
            raise ValueError
    except (ValueError, IndexError):
        err = (out.stderr or "").strip().splitlines()
        return {"hw": False, "renderer": "", "vendor": "",
                "error": ("exit %s: %s" % (out.returncode, err[-1] if err else "no output"))[:200]}
    renderer, vendor = str(res.get("renderer") or ""), str(res.get("vendor") or "")
    error = res.get("error")
    return {"hw": error is None and classify(renderer, res.get("direct", False)), "renderer": renderer[:200],
            "vendor": vendor[:200], "error": str(error)[:200] if error else None}


def _store(res):
    global _result
    res = dict(res, probed_at=round(time.time()))
    with _lock:
        _result = res
    try:
        path = data_dir() / "gpu.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("gpu.json.%d.tmp" % os.getpid())
        tmp.write_text(json.dumps(res) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass
    return res


def start_detection(display=None):
    """Probe once, in the background. Safe to call again: only one probe ever runs per process."""
    global _thread
    with _lock:
        if _thread is not None:
            return _thread
        _thread = threading.Thread(target=lambda: _store(probe(display)), name="gpu-probe", daemon=True)
    _thread.start()
    return _thread


def detected():
    with _lock:
        if _result is not None:
            return dict(_result)
    try:
        res = json.loads((data_dir() / "gpu.json").read_text())
        if isinstance(res, dict) and isinstance(res.get("hw"), bool):
            return res
    except (OSError, ValueError):
        pass
    return dict(PENDING)


def use_gpu_viz(cfg):
    env = os.environ.get("IMACTV_GPU_VIZ")
    if env in ("1", "0"):
        return env == "1"
    mode = (cfg or {}).get("gpu_viz")
    if mode == "on":
        return True
    if mode == "off":
        return False
    return bool(detected().get("hw"))


def describe(cfg):
    """One line for Settings: what Auto picked, or what was forced."""
    mode = (cfg or {}).get("gpu_viz")
    if mode == "on":
        return "Drawn on the graphics chip"
    if mode == "off":
        return "Drawn on the processor"
    d = detected()
    if d.get("pending"):
        return "Auto · checking the graphics chip…"
    if d.get("hw"):
        return "Auto · graphics chip found (%s)" % (d.get("renderer") or "GPU")
    return "Auto · no graphics chip found, drawn on the processor"


# ---- the child process -------------------------------------------------------------------------------------

def _VisualInfo(ctypes):
    class XVisualInfo(ctypes.Structure):
        _fields_ = [("visual", ctypes.c_void_p), ("visualid", ctypes.c_ulong), ("screen", ctypes.c_int),
                    ("depth", ctypes.c_int), ("c_class", ctypes.c_int), ("red_mask", ctypes.c_ulong),
                    ("green_mask", ctypes.c_ulong), ("blue_mask", ctypes.c_ulong),
                    ("colormap_size", ctypes.c_int), ("bits_per_rgb", ctypes.c_int)]
    return XVisualInfo


def _SetWindowAttributes(ctypes):
    ul, i = ctypes.c_ulong, ctypes.c_int

    class XSetWindowAttributes(ctypes.Structure):
        _fields_ = [("background_pixmap", ul), ("background_pixel", ul), ("border_pixmap", ul),
                    ("border_pixel", ul), ("bit_gravity", i), ("win_gravity", i), ("backing_store", i),
                    ("backing_planes", ul), ("backing_pixel", ul), ("save_under", i), ("event_mask", ctypes.c_long),
                    ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", i), ("colormap", ul),
                    ("cursor", ul)]
    return XSetWindowAttributes


def _child():
    import ctypes
    import ctypes.util

    def lib(*names):
        for n in names:
            try:
                return ctypes.CDLL(n)
            except OSError:
                continue
        found = ctypes.util.find_library(names[0].split(".")[0][3:])
        if found:
            return ctypes.CDLL(found)
        raise OSError("cannot load " + names[0])

    out = {"renderer": "", "vendor": "", "direct": False, "error": None}
    try:
        x11 = lib("libX11.so.6")
        gl = lib("libGL.so.1")
        vp, cp = ctypes.c_void_p, ctypes.c_char_p
        x11.XOpenDisplay.restype = vp
        x11.XOpenDisplay.argtypes = [cp]
        x11.XDefaultScreen.argtypes = [vp]
        x11.XRootWindow.restype = ctypes.c_ulong
        x11.XRootWindow.argtypes = [vp, ctypes.c_int]
        gl.glXChooseVisual.restype = vp
        gl.glXChooseVisual.argtypes = [vp, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        gl.glXCreateContext.restype = vp
        gl.glXCreateContext.argtypes = [vp, vp, vp, ctypes.c_int]
        gl.glXIsDirect.argtypes = [vp, vp]
        gl.glXMakeCurrent.argtypes = [vp, ctypes.c_ulong, vp]
        gl.glGetString.restype = cp
        gl.glGetString.argtypes = [ctypes.c_uint]
        dpy = x11.XOpenDisplay(None)
        if not dpy:
            raise RuntimeError("no X display")
        scr = x11.XDefaultScreen(dpy)
        attrs = (ctypes.c_int * 3)(4, 5, 0)             # GLX_RGBA, GLX_DOUBLEBUFFER, None
        vis = gl.glXChooseVisual(dpy, scr, attrs)
        if not vis:
            raise RuntimeError("no GLX visual")
        info = ctypes.cast(vis, ctypes.POINTER(_VisualInfo(ctypes))).contents
        root = x11.XRootWindow(dpy, scr)
        x11.XCreateColormap.restype = ctypes.c_ulong
        x11.XCreateColormap.argtypes = [vp, ctypes.c_ulong, vp, ctypes.c_int]
        x11.XCreateWindow.restype = ctypes.c_ulong
        x11.XCreateWindow.argtypes = [vp, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                      ctypes.c_uint, ctypes.c_int, ctypes.c_uint, vp, ctypes.c_ulong, vp]
        swa = _SetWindowAttributes(ctypes)()
        swa.colormap = x11.XCreateColormap(dpy, root, info.visual, 0)
        swa.border_pixel = 0
        # a 16x16 window that is never mapped: only there so the context has a matching drawable
        win = x11.XCreateWindow(dpy, root, 0, 0, 16, 16, 0, info.depth, 1, info.visual,
                                (1 << 3) | (1 << 13), ctypes.byref(swa))       # CWBorderPixel | CWColormap
        ctx = gl.glXCreateContext(dpy, vis, None, 1)
        if not ctx:
            raise RuntimeError("no GLX context")
        out["direct"] = bool(gl.glXIsDirect(dpy, ctx))
        if not gl.glXMakeCurrent(dpy, win, ctx):
            raise RuntimeError("glXMakeCurrent failed")
        out["renderer"] = (gl.glGetString(0x1F01) or b"").decode("utf-8", "replace")     # GL_RENDERER
        out["vendor"] = (gl.glGetString(0x1F00) or b"").decode("utf-8", "replace")       # GL_VENDOR
        gl.glXMakeCurrent(dpy, 0, None)
    except Exception as exc:                                  # noqa: BLE001 - report, never crash the caller
        out["error"] = str(exc)[:200]
    print(json.dumps(out), flush=True)


if __name__ == "__main__" and "--probe" in sys.argv:
    _child()
