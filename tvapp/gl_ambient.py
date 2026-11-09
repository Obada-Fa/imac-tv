"""Ambient Home drawn on the GPU (home.json "gpu_viz", see gpu.py): the quiet floating particles, the sky-line
and doodle shapes, and the music visualizer.

Home keeps running the simulations (shapes.Field at 10 Hz, the morph at 15 Hz, shapes.Viz at ~30 Hz) and sends
each frame's geometry here. This renderer runs as its own process (a crashing driver can't take the TV app down) and draws into a native X child
window of Home's toplevel with OpenGL at the display's refresh rate, interpolating between Home's frames, so the
motion is smooth while Home's CPU stays low. Under the dots it draws the same photo (cross-fade and music dim
included) that the cairo canvas shows. GTK widgets that stay up in ambient (the clock, islands) get holes in the
window (X Shape), so GTK keeps drawing them; Home's cairo path still paints the visualizer inside those holes.

The window has a blank cursor and is mapped only while ambient's stage shows (the tiles faded out). Not GtkGLArea: that reads every
frame back through cairo (it was measured at 40-50% of a core); this draws straight to the window.

Protocol (Home -> renderer, stdin): 4-byte big-endian length + pickle of a tuple:
  ("photo", key, w, h, stride, bytes)   a cairo RGB24 photo; kept until a frame no longer names it
  ("holes", [(x, y, w, h), ...])        where GTK widgets show through
  ("frame", dict)                       see GLLink.frame
  ("show", bool)                        map / unmap
  ("quit",)
Renderer -> Home (stdout lines): "ready <GL_RENDERER>", "error <text>".

Run `python3 gl_ambient.py --demo 20` for a stand-alone check with fake music (scripts/gl-check.sh).
"""
from array import array
import collections
import math
import os
import pickle
import select
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIM_MS = 33             # Home steps the simulation at ~30 Hz while the GPU draws; this file draws every vsync
STALE_S = 0.4           # no new frame for this long: the picture has settled (frame_at), stop drawing
JUMP_PX = 120           # a particle that moved further than this between two frames left and came back in: no lerp
MUSIC_DIM = 0.22        # = homescreen.MUSIC_DIM
FRAME_KEYS = ("t", "scale", "pts", "tails", "alphas", "links", "lv", "fade", "dim", "cur", "prev", "pal", "hub",
              "glow_r", "viz", "field")   # + "e", "centre", "rmax" for a shape without the visualizer


def pack(msg):
    data = pickle.dumps(msg, protocol=pickle.HIGHEST_PROTOCOL)
    return struct.pack(">I", len(data)) + data


class Unpacker:
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= 4:
            n = struct.unpack(">I", self.buf[:4])[0]
            if len(self.buf) < 4 + n:
                break
            out.append(pickle.loads(bytes(self.buf[4:4 + n])))
            del self.buf[:4 + n]
        return out


# ---- Home's side ----------------------------------------------------------------------------------------------

class GLLink:
    """Home's handle on the renderer process. Writes go through a thread so a busy renderer never stalls GTK;
    frames coalesce (only the newest unsent one is kept)."""

    failed = False          # the renderer failed once this session: Home stays on cairo until the app restarts

    def __init__(self, parent_xid, width, height, display=None):
        env = dict(os.environ)
        if display:
            env["DISPLAY"] = display
        self.ready = False
        self.renderer = ""
        self.error = None
        self.proc = subprocess.Popen([sys.executable, "-I", str(HERE / "gl_ambient.py"), "--parent", str(parent_xid),
                                      "--width", str(width), "--height", str(height)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, close_fds=True)
        self.cond = threading.Condition()
        self.queue = collections.deque()
        self.pending_frame = None
        self.closed = False
        threading.Thread(target=self._writer, name="gl-writer", daemon=True).start()
        threading.Thread(target=self._reader, name="gl-reader", daemon=True).start()

    def _reader(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("ready"):
                self.renderer = line[6:]
                self.ready = True
            elif line.startswith("error"):
                self.error = line[6:] or "error"
                print("gpu visualizer:", self.error, file=sys.stderr)
        self.proc.stdout.close()

    def _writer(self):
        while True:
            with self.cond:
                while not self.queue and self.pending_frame is None and not self.closed:
                    self.cond.wait()
                if self.queue:
                    msg = self.queue.popleft()
                elif self.pending_frame is not None:
                    msg, self.pending_frame = self.pending_frame, None
                else:
                    try:
                        self.proc.stdin.close()
                    except OSError:
                        pass
                    return
            try:
                self.proc.stdin.write(pack(msg))
                self.proc.stdin.flush()
            except (OSError, ValueError):
                self.error = self.error or "renderer pipe closed"
                return

    def send(self, msg):
        with self.cond:
            self.queue.append(msg)
            self.cond.notify()

    def frame(self, data):
        with self.cond:
            self.pending_frame = ("frame", data)
            self.cond.notify()

    def show(self, on):
        self.send(("show", bool(on)))

    def alive(self):
        return self.proc.poll() is None and self.error is None

    def stop(self):
        with self.cond:
            self.queue.clear()
            self.pending_frame = None
            self.queue.append(("quit",))
            self.cond.notify()
        threading.Thread(target=self._reap, daemon=True).start()

    def _reap(self):
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        with self.cond:
            self.closed = True
            self.cond.notify()


# ---- interpolation (renderer side, pure) ----------------------------------------------------------------------

def _lerp_pts(a, b, u):
    return [(ax + (bx - ax) * u, ay + (by - ay) * u) for (ax, ay), (bx, by) in zip(a, b)]


def interpolate_field(a, b, u):
    """The particles between two frames: positions and alphas blended, links faded in and out by (i, j). A
    particle that jumped (left the screen and re-entered elsewhere) takes b's place at once; it is invisible
    at the edges anyway."""
    if a is None or b is None or len(a["pts"]) != len(b["pts"]):
        return b
    pts, al = [], []
    for (ax, ay), (bx, by), aa, ba in zip(a["pts"], b["pts"], a["alpha"], b["alpha"]):
        if abs(bx - ax) + abs(by - ay) > JUMP_PX:
            pts.append((bx, by))
            al.append(ba)
        else:
            pts.append((ax + (bx - ax) * u, ay + (by - ay) * u))
            al.append(aa + (ba - aa) * u)
    la = {(i, j): x for i, j, x in a["links"]}
    lb = {(i, j): x for i, j, x in b["links"]}
    links = []
    for k in sorted(la.keys() | lb.keys()):
        x = la.get(k, 0.0) + (lb.get(k, 0.0) - la.get(k, 0.0)) * u
        if x > 0.01:
            links.append((k[0], k[1], x))
    return dict(b, pts=pts, alpha=al, links=links)


def interpolate(fa, fb, u):
    """A frame between Home's frames fa and fb (u = 0..1). Positions, alphas, levels, fade/dim and the clock are
    blended; the rest (links, glyphs, pulses, colours) comes from fb. Different point counts: fb's shape as it
    is (the particles are blended on their own)."""
    if fa is None or u >= 1.0:
        return fb
    if u <= 0.0:
        return fa
    if len(fa["pts"]) != len(fb["pts"]):
        if not (fa.get("field") and fb.get("field")):
            return fb
        return dict(fb, field=interpolate_field(fa["field"], fb["field"], u))
    out = dict(fb)
    out["t"] = fa["t"] + (fb["t"] - fa["t"]) * u
    if fb.get("field"):
        out["field"] = interpolate_field(fa.get("field"), fb["field"], u)
    if "e" in fa and "e" in fb:
        out["e"] = fa["e"] + (fb["e"] - fa["e"]) * u
    out["pts"] = _lerp_pts(fa["pts"], fb["pts"], u)
    if fa.get("tails") and fb.get("tails") and len(fa["tails"]) == len(fb["tails"]):
        out["tails"] = _lerp_pts(fa["tails"], fb["tails"], u)
    out["alphas"] = [x + (y - x) * u for x, y in zip(fa["alphas"], fb["alphas"])]
    out["lv"] = {k: fa["lv"].get(k, v) + (v - fa["lv"].get(k, v)) * u for k, v in fb["lv"].items()}
    for k in ("fade", "dim"):
        out[k] = fa[k] + (fb[k] - fa[k]) * u
    return out


def pick(frames, rt):
    """(older, newer, u) for render time rt from a short list of frames sorted by their "t"."""
    if not frames:
        return None, None, 1.0
    if rt >= frames[-1]["t"] or len(frames) == 1:
        return None, frames[-1], 1.0
    if rt <= frames[0]["t"]:
        return None, frames[0], 1.0
    for fa, fb in zip(frames, frames[1:]):
        if fa["t"] <= rt <= fb["t"]:
            span = fb["t"] - fa["t"]
            return fa, fb, (rt - fa["t"]) / span if span > 1e-6 else 1.0
    return None, frames[-1], 1.0


class Painter:
    """vizpaint's painter into a flat vertex list: x, y, r, g, b, a, u, v per vertex (u < -5: solid fill, else the
    glow's radial profile over u, v in -1..1). One draw call per frame; MSAA does the edges."""

    def __init__(self, glow_r):
        self.v = []
        self.r = glow_r

    def _tri(self, p0, p1, p2, rgb, a):
        r, g, b = rgb
        self.v += (p0[0], p0[1], r, g, b, a, -9.0, 0.0, p1[0], p1[1], r, g, b, a, -9.0, 0.0,
                   p2[0], p2[1], r, g, b, a, -9.0, 0.0)

    def _quad(self, x0, y0, x1, y1, hw, rgb, a):
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        if ln < 1e-6:
            ux, uy = 1.0, 0.0
        else:
            ux, uy = dx / ln, dy / ln
        nx, ny = -uy * hw, ux * hw
        ex, ey = ux * hw, uy * hw                  # round caps, near enough: square caps half a width long
        ax, ay, bx, by = x0 - ex, y0 - ey, x1 + ex, y1 + ey
        p0, p1, p2, p3 = (ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)
        self._tri(p0, p1, p2, rgb, a)
        self._tri(p0, p2, p3, rgb, a)

    def segs(self, segs, _steps, amax):
        for rgb, a, wd, x0, y0, x1, y1 in segs:
            if a > 0:
                self._quad(x0, y0, x1, y1, wd / 2.0, rgb, min(a, amax))

    def fill(self, poly, rgb, a):
        if a <= 0:
            return
        p0 = poly[0]
        for k in range(1, len(poly) - 1):
            self._tri(p0, poly[k], poly[k + 1], rgb, a)

    def outline(self, poly, rgb, a, wd):
        if a <= 0:
            return
        for k in range(len(poly)):
            x0, y0 = poly[k - 1]
            x1, y1 = poly[k]
            self._quad(x0, y0, x1, y1, wd / 2.0, rgb, a)

    def glow(self, x, y, rgb, a):
        if a <= 0:
            return
        r = self.r
        cr, cg, cb = rgb
        x0, y0, x1, y1 = x - r, y - r, x + r, y + r
        self.v += (x0, y0, cr, cg, cb, a, -1.0, -1.0, x1, y0, cr, cg, cb, a, 1.0, -1.0,
                   x1, y1, cr, cg, cb, a, 1.0, 1.0, x0, y0, cr, cg, cb, a, -1.0, -1.0,
                   x1, y1, cr, cg, cb, a, 1.0, 1.0, x0, y1, cr, cg, cb, a, -1.0, 1.0)


# ---- the renderer process -------------------------------------------------------------------------------------

PHOTO_VS = """#version 120
attribute vec2 a_pos;
varying vec2 v_uv;
void main() { v_uv = vec2(a_pos.x * 0.5 + 0.5, 0.5 - a_pos.y * 0.5); gl_Position = vec4(a_pos, 0.0, 1.0); }
"""
PHOTO_FS = """#version 120
uniform sampler2D u_cur;
uniform sampler2D u_prev;
uniform float u_has_cur, u_has_prev, u_fade, u_dim;
uniform vec2 u_size;
varying vec2 v_uv;
vec3 gradient(vec2 uv) {        // paint_background's calm gradient when there is no photo
    vec2 p = uv * u_size;
    float t = clamp(dot(p, u_size) / dot(u_size, u_size), 0.0, 1.0);
    vec3 a = vec3(0.05, 0.07, 0.12), b = vec3(0.07, 0.09, 0.15), c = vec3(0.02, 0.03, 0.06);
    return t < 0.55 ? mix(a, b, t / 0.55) : mix(b, c, (t - 0.55) / 0.45);
}
void main() {
    vec3 col = u_has_cur > 0.5 ? texture2D(u_cur, v_uv).rgb : gradient(v_uv);
    if (u_has_prev > 0.5 && u_fade < 1.0) {
        float e = u_fade * u_fade * (3.0 - 2.0 * u_fade);
        col = mix(texture2D(u_prev, v_uv).rgb, col, e);
    }
    col = mix(col, vec3(0.01, 0.015, 0.035), %f * u_dim);
    gl_FragColor = vec4(col, 1.0);
}
""" % MUSIC_DIM
GEOM_VS = """#version 120
attribute vec2 a_pos;
attribute vec4 a_col;
attribute vec2 a_uv;
uniform vec2 u_size;
varying vec4 v_col;
varying vec2 v_uv;
void main() {
    v_col = a_col; v_uv = a_uv;
    gl_Position = vec4(a_pos.x / u_size.x * 2.0 - 1.0, 1.0 - a_pos.y / u_size.y * 2.0, 0.0, 1.0);
}
"""
GEOM_FS = """#version 120
varying vec4 v_col;
varying vec2 v_uv;
void main() {
    float k = 1.0;
    if (v_uv.x > -5.0) {        // the glow sprite: 1 at the centre, 0.45 at a quarter, 0 at the rim
        float r = length(v_uv);
        k = r < 0.25 ? mix(1.0, 0.45, r / 0.25) : mix(0.45, 0.0, clamp((r - 0.25) / 0.75, 0.0, 1.0));
    }
    gl_FragColor = vec4(v_col.rgb, v_col.a * k);
}
"""


class Renderer:
    def __init__(self, parent, width, height):
        import ctypes
        import glx
        self.ct, self.x = ctypes, glx
        X = glx
        self._err = X._XErrorHandler(self._x_error)     # keep a reference: a BadWindow must not kill us silently
        X.XSetErrorHandler(self._err)
        self.dpy = X.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError("cannot open the X display")
        scr = X.XDefaultScreen(self.dpy)
        root = X.XRootWindow(self.dpy, scr)
        self.own = not parent
        if self.own:
            width, height = X.XDisplayWidth(self.dpy, scr), X.XDisplayHeight(self.dpy, scr)
        self.w, self.h = width, height
        vis = None
        for samples in (4, 2, 0):
            attrs = [X.GLX_RGBA, X.GLX_DOUBLEBUFFER, X.GLX_RED_SIZE, 8, X.GLX_GREEN_SIZE, 8, X.GLX_BLUE_SIZE, 8]
            if samples:
                attrs += [X.GLX_SAMPLE_BUFFERS, 1, X.GLX_SAMPLES, samples]
            arr = (ctypes.c_int * (len(attrs) + 1))(*attrs, 0)
            vis = X.glXChooseVisual(self.dpy, scr, arr)
            if vis:
                self.samples = samples
                break
        if not vis:
            raise RuntimeError("no GLX visual")
        info = vis.contents
        swa = X.XSetWindowAttributes()
        swa.colormap = X.XCreateColormap(self.dpy, root, info.visual, 0)
        swa.border_pixel = 0
        swa.background_pixmap = 0            # None: X never clears it, so no flash before the first frame
        swa.override_redirect = 1
        swa.cursor = self._blank_cursor(root)
        mask = X.CWBackPixmap | X.CWBorderPixel | X.CWColormap | X.CWCursor
        if self.own:
            mask |= X.CWOverrideRedirect
        self.win = X.XCreateWindow(self.dpy, parent or root, 0, 0, width, height, 0, info.depth, X.InputOutput,
                                   info.visual, mask, ctypes.byref(swa))
        self.ctx = X.glXCreateContext(self.dpy, vis, None, 1)
        if not self.ctx or not X.glXMakeCurrent(self.dpy, self.win, self.ctx):
            raise RuntimeError("cannot make a GL context current")
        self.direct = bool(X.glXIsDirect(self.dpy, self.ctx))
        self.gl = gl = X.GL()
        self.renderer = (gl.GetString(X.GL_RENDERER) or b"?").decode(errors="replace")
        exts = (X.glXQueryExtensionsString(self.dpy, scr) or b"").decode(errors="replace")
        self.vsync = False
        if "GLX_EXT_swap_control" in exts:
            addr = X.glXGetProcAddressARB(b"glXSwapIntervalEXT")
            if addr:
                ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int)(addr)(self.dpy, self.win, 1)
                self.vsync = True
        for ext, name in (("GLX_MESA_swap_control", b"glXSwapIntervalMESA"),
                          ("GLX_SGI_swap_control", b"glXSwapIntervalSGI")):
            if not self.vsync and ext in exts:
                addr = X.glXGetProcAddressARB(name)
                if addr and ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(addr)(1) == 0:
                    self.vsync = True
        self.shape_ok = bool(X.XShapeQueryExtension(self.dpy, ctypes.byref(ctypes.c_int()),
                                                     ctypes.byref(ctypes.c_int())))
        gl.Viewport(0, 0, width, height)
        gl.Enable(X.GL_BLEND)
        gl.BlendFunc(X.GL_SRC_ALPHA, X.GL_ONE_MINUS_SRC_ALPHA)
        if self.samples:
            gl.Enable(X.GL_MULTISAMPLE)
        self.photo_prog = gl.program(PHOTO_VS, PHOTO_FS, ["a_pos"])
        self.geom_prog = gl.program(GEOM_VS, GEOM_FS, ["a_pos", "a_col", "a_uv"])
        self.u = {n: gl.uniform(self.photo_prog, n) for n in
                  ("u_cur", "u_prev", "u_has_cur", "u_has_prev", "u_fade", "u_dim", "u_size")}
        self.u_geom_size = gl.uniform(self.geom_prog, "u_size")
        bufs = (ctypes.c_uint * 2)()
        gl.GenBuffers(2, bufs)
        self.quad_buf, self.geom_buf = bufs[0], bufs[1]
        quad = (ctypes.c_float * 8)(-1, -1, 1, -1, -1, 1, 1, 1)
        gl.BindBuffer(X.GL_ARRAY_BUFFER, self.quad_buf)
        gl.BufferData(X.GL_ARRAY_BUFFER, ctypes.sizeof(quad), quad, X.GL_STATIC_DRAW)
        self.textures = {}          # photo key -> GL texture
        self.frames = []            # Home's latest frames, oldest first
        self.fed_at = 0.0           # when the newest frame arrived
        self.shown = False
        self.want = False
        self.holes = None
        self.x_failed = None
        X.XSync(self.dpy, 0)

    def _x_error(self, _dpy, _ev):
        self.x_failed = "X error"
        return 0

    def _blank_cursor(self, root):
        X, ct = self.x, self.ct
        pix = X.XCreateBitmapFromData(self.dpy, root, b"\x00", 1, 1)
        black = X.XColor()
        cur = X.XCreatePixmapCursor(self.dpy, pix, pix, ct.byref(black), ct.byref(black), 0, 0)
        X.XFreePixmap(self.dpy, pix)
        return cur

    # -- messages
    def handle(self, msg):
        kind = msg[0]
        if kind == "photo":
            self.upload(*msg[1:])
        elif kind == "holes":
            self.set_holes(msg[1])
        elif kind == "frame":
            f = msg[1]
            self.frames = [x for x in self.frames if x["t"] < f["t"]][-2:] + [f]
            self.fed_at = time.monotonic()
            if self.want and not self.shown:
                self.set_shown(True)                  # "show" can overtake the first frame
            keep = {f.get("cur"), f.get("prev")}
            for key in [k for k in self.textures if k not in keep]:
                tex = (self.ct.c_uint * 1)(self.textures.pop(key))
                self.gl.DeleteTextures(1, tex)
        elif kind == "show":
            self.want = bool(msg[1])
            self.set_shown(self.want)
        elif kind == "quit":
            raise SystemExit(0)

    def upload(self, key, w, h, stride, data):
        X, gl, ct = self.x, self.gl, self.ct
        tex = self.textures.get(key)
        if tex is None:
            arr = (ct.c_uint * 1)()
            gl.GenTextures(1, arr)
            tex = self.textures[key] = arr[0]
        gl.BindTexture(X.GL_TEXTURE_2D, tex)
        for p, v in ((X.GL_TEXTURE_MIN_FILTER, X.GL_LINEAR), (X.GL_TEXTURE_MAG_FILTER, X.GL_LINEAR),
                     (X.GL_TEXTURE_WRAP_S, X.GL_CLAMP_TO_EDGE), (X.GL_TEXTURE_WRAP_T, X.GL_CLAMP_TO_EDGE)):
            gl.TexParameteri(X.GL_TEXTURE_2D, p, v)
        gl.PixelStorei(X.GL_UNPACK_ALIGNMENT, 4)
        gl.PixelStorei(X.GL_UNPACK_ROW_LENGTH, stride // 4)
        buf = (ct.c_ubyte * len(data)).from_buffer_copy(data)
        gl.TexImage2D(X.GL_TEXTURE_2D, 0, X.GL_RGB8, w, h, 0, X.GL_BGRA, X.GL_UNSIGNED_BYTE, buf)
        gl.PixelStorei(X.GL_UNPACK_ROW_LENGTH, 0)

    def set_holes(self, holes):
        holes = [tuple(int(v) for v in r) for r in holes]
        if holes == self.holes or not self.shape_ok:
            return
        self.holes = holes
        X, ct = self.x, self.ct
        full = (X.XRectangle * 1)(X.XRectangle(0, 0, self.w, self.h))
        X.XShapeCombineRectangles(self.dpy, self.win, X.ShapeBounding, 0, 0, full, 1, X.ShapeSet, 0)
        if holes:
            arr = (X.XRectangle * len(holes))(*[X.XRectangle(max(-32768, x), max(-32768, y), max(0, w), max(0, h))
                                                for x, y, w, h in holes])
            X.XShapeCombineRectangles(self.dpy, self.win, X.ShapeBounding, 0, 0, arr, len(holes), X.ShapeSubtract, 0)
        empty = (X.XRectangle * 1)(X.XRectangle(0, 0, 0, 0))       # never takes input: keys go to GTK
        X.XShapeCombineRectangles(self.dpy, self.win, X.ShapeInput, 0, 0, empty, 0, X.ShapeSet, 0)
        X.XFlush(self.dpy)

    def set_shown(self, on):
        if on == self.shown:
            return
        if on:
            if not self.frames:
                return
            self.draw(time.monotonic())                 # the first frame is ready before the window shows
            self.x.XMapRaised(self.dpy, self.win)
            self.x.glXSwapBuffers(self.dpy, self.win)
        else:
            self.x.XUnmapWindow(self.dpy, self.win)
            self.frames = self.frames[-1:]
        self.x.XFlush(self.dpy)
        self.shown = on

    def drawing(self, now=None):
        """Shown and still being fed. Once Home stops sending frames the picture can't change any more
        (frame_at settles on the newest one), so the window keeps its last image and nothing is drawn: Home
        stopping its timer without a "show" False (another app in front) never leaves the GPU busy."""
        if not self.shown:
            return False
        return (time.monotonic() if now is None else now) - self.fed_at < STALE_S

    # -- drawing
    def frame_at(self, now):
        if not self.frames:
            return None
        newest = self.frames[-1]
        span = newest["t"] - self.frames[-2]["t"] if len(self.frames) > 1 else SIM_MS / 1000.0
        rt = now - min(0.3, max(0.016, 1.2 * span))     # a step behind, with room for a late timer: always between two frames
        fa, fb, u = pick(self.frames, rt)
        return interpolate(fa, fb, u)

    def draw(self, now):
        import vizpaint
        X, gl, ct = self.x, self.gl, self.ct
        f = self.frame_at(now)
        if f is None:
            return
        gl.UseProgram(self.photo_prog)
        cur, prev = self.textures.get(f.get("cur")), self.textures.get(f.get("prev"))
        gl.ActiveTexture(X.GL_TEXTURE0)
        gl.BindTexture(X.GL_TEXTURE_2D, cur or 0)
        gl.ActiveTexture(X.GL_TEXTURE1)
        gl.BindTexture(X.GL_TEXTURE_2D, prev or 0)
        gl.ActiveTexture(X.GL_TEXTURE0)
        u = self.u
        gl.Uniform1i(u["u_cur"], 0)
        gl.Uniform1i(u["u_prev"], 1)
        gl.Uniform1f(u["u_has_cur"], 1.0 if cur else 0.0)
        gl.Uniform1f(u["u_has_prev"], 1.0 if prev else 0.0)
        gl.Uniform1f(u["u_fade"], float(f.get("fade", 1.0)))
        gl.Uniform1f(u["u_dim"], float(f.get("dim", 1.0)))
        gl.Uniform2f(u["u_size"], float(self.w), float(self.h))
        gl.Disable(X.GL_BLEND)
        gl.BindBuffer(X.GL_ARRAY_BUFFER, self.quad_buf)
        gl.EnableVertexAttribArray(0)
        gl.DisableVertexAttribArray(1)
        gl.DisableVertexAttribArray(2)
        gl.VertexAttribPointer(0, 2, X.GL_FLOAT, 0, 8, None)
        gl.DrawArrays(X.GL_TRIANGLE_STRIP, 0, 4)
        gl.Enable(X.GL_BLEND)
        p = Painter(f.get("glow_r", 22))
        if f.get("field"):
            vizpaint.paint_field(p, f["field"])
        if f.get("pts"):
            if f.get("viz") is not None:
                vizpaint.paint(p, f, f["pal"], f.get("hub", "hexagon"))
            elif "e" in f:
                vizpaint.paint_still(p, f)
        n = len(p.v) // 8
        if n:
            arr = array("f", p.v)                   # ~5x faster than a ctypes array built from the list
            addr, count = arr.buffer_info()
            gl.UseProgram(self.geom_prog)
            gl.Uniform2f(self.u_geom_size, float(self.w), float(self.h))
            gl.BindBuffer(X.GL_ARRAY_BUFFER, self.geom_buf)
            gl.BufferData(X.GL_ARRAY_BUFFER, count * arr.itemsize, addr, X.GL_STREAM_DRAW)
            for i in range(3):
                gl.EnableVertexAttribArray(i)
            gl.VertexAttribPointer(0, 2, X.GL_FLOAT, 0, 32, None)
            gl.VertexAttribPointer(1, 4, X.GL_FLOAT, 0, 32, ct.c_void_p(8))
            gl.VertexAttribPointer(2, 2, X.GL_FLOAT, 0, 32, ct.c_void_p(24))
            gl.DrawArrays(X.GL_TRIANGLES, 0, n)

    def present(self):
        t0 = time.monotonic()
        self.draw(t0)
        self.x.glXSwapBuffers(self.dpy, self.win)
        # Never faster than ~60 fps, swap control or not: a swap the X server doesn't hold until the next
        # refresh (no swap control, or a window that isn't on screen) would otherwise spin a core.
        left = 1 / 60 - (time.monotonic() - t0)
        if left > 0:
            time.sleep(left)


def serve(parent, width, height):
    sys.path[:0] = [str(HERE), str(HERE / "widgets")]
    try:
        r = Renderer(parent, width, height)
    except Exception as exc:                                   # noqa: BLE001 - Home falls back to cairo
        print("error", str(exc).replace("\n", " ")[:300], flush=True)
        return 1
    print("ready", r.renderer, flush=True)
    fd = sys.stdin.fileno()
    os.set_blocking(fd, False)
    unpack = Unpacker()
    while True:
        ready, _, _ = select.select([fd], [], [], 0 if r.drawing() else None)
        if ready:
            while True:
                try:
                    data = os.read(fd, 1 << 20)
                except BlockingIOError:
                    break
                if not data:
                    return 0                                   # Home went away
                for msg in unpack.feed(data):
                    r.handle(msg)
        if r.x_failed:
            print("error", r.x_failed, flush=True)
            return 1
        if r.drawing():
            r.present()


# ---- stand-alone check (scripts/gl-check.sh) -------------------------------------------------------------------

def _cpu_ticks(pid):
    try:
        f = open("/proc/%s/stat" % pid).read().rsplit(")", 1)[1].split()
        return int(f[11]) + int(f[12])
    except (OSError, IndexError, ValueError):
        return None


def demo(seconds):
    """Its own full-screen window, Home's simulations in-process: the first half the quiet particles (10 Hz, as
    Home sends them), the second half the visualizer with fake music (30 Hz). Prints fps and CPU for each."""
    sys.path[:0] = [str(HERE), str(HERE / "widgets")]
    import random
    import shapes
    r = Renderer(0, 0, 0)
    print("renderer:", r.renderer, "| direct:", r.direct, "| vsync:", r.vsync, "| msaa:", r.samples, flush=True)
    sw, sh = r.w, r.h
    scale = sh / 1080.0
    xorg = [p for p in os.listdir("/proc") if p.isdigit() and _comm(p) in ("Xorg", "X", "Xwayland")]
    common = {"tails": None, "fade": 1.0, "cur": None, "prev": None, "hub": "hexagon",
              "glow_r": max(8, round(22 * scale)), "pal": list(shapes.DEFAULT_PALETTE)}
    field = shapes.Field(sw, sh, random.Random(2))

    def quiet(now, dt):
        field.step(dt, 1.0)
        fr = field.frame(now, reach=0.16 * sw)
        return dict(common, t=now, scale=scale, pts=[], alphas=[], links=[], lv={}, viz=None, dim=0.0,
                    field={"scale": scale, "pts": fr["pts"], "alpha": fr["alpha"], "links": fr["links"]})

    base = shapes.wave_shape()
    base["pts"] = [(x * sw, y * sh) for x, y in base["pts"]]
    viz = shapes.Viz(base, sw, sh, random.Random(1))
    st = {"lv": {"bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0}, "prev": None, "t0": None}

    def music(now, dt):
        st["t0"] = st["t0"] or now
        t = now - st["t0"]
        tgt = {"bass": 0.5 + 0.5 * math.sin(t * 3.1), "mid": 0.5 + 0.4 * math.sin(t * 1.7),
               "treble": 0.5 + 0.5 * math.sin(t * 5.3), "beat": max(0.0, 1.0 - ((t * 1.8) % 1.0) * 2.5)}
        lv = st["lv"] = shapes.ease_levels(st["lv"], tgt, dt)
        viz.step(dt, lv, beat=(t * 1.8) % 1.0 < dt * 1.8, t=now, auto=True)
        fr = viz.frame(now, lv)
        pts, prev, tails = fr["pts"], st["prev"], None
        if prev and len(prev) == len(pts):
            k = 0.4 / dt
            tails = [(x - k * (x - px), y - k * (y - py)) for (x, y), (px, py) in zip(pts, prev)]
        st["prev"] = pts
        return dict(common, t=now, scale=scale, pts=pts, tails=tails if fr["trail"] > 0.02 else None,
                    alphas=list(fr["alpha"]), links=fr["links"], lv=dict(lv), dim=1.0, pal=viz.palette, viz=fr)

    hz = os.sysconf("SC_CLK_TCK")
    for name, step, period in (("quiet particles", quiet, 0.1), ("music visualizer", music, SIM_MS / 1000.0)):
        r.frames = []
        last_sim, start, drawn = 0.0, time.monotonic(), 0
        t_cpu0, x_cpu0 = _cpu_ticks(os.getpid()), {p: _cpu_ticks(p) for p in xorg}
        while time.monotonic() - start < seconds / 2:
            now = time.monotonic()
            if now - last_sim >= period:
                dt = min(0.2, now - last_sim) if last_sim else period
                last_sim = now
                r.handle(("frame", step(now, dt)))
                if not r.shown:
                    r.set_shown(True)
            r.present()
            drawn += 1
        secs = time.monotonic() - start
        own = (_cpu_ticks(os.getpid()) - t_cpu0) / hz / secs * 100
        print("== %s: %d frames drawn (%.1f fps), renderer + simulation CPU %.1f%% of one core"
              % (name, drawn, drawn / secs, own))
        for p in xorg:
            a, b = x_cpu0.get(p), _cpu_ticks(p)
            if a is not None and b is not None:
                print("   Xorg (pid %s) CPU: %.1f%% of one core" % (p, (b - a) / hz / secs * 100))


def _comm(pid):
    try:
        return open("/proc/%s/comm" % pid).read().strip()
    except OSError:
        return ""


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent", type=lambda v: int(v, 0), default=0)
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    ap.add_argument("--demo", type=float, default=0)
    a = ap.parse_args(argv)
    if a.demo:
        demo(a.demo)
        return 0
    return serve(a.parent, a.width, a.height)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
