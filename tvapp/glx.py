"""Just enough X11 + GLX + OpenGL 2.1 through ctypes for the GPU visualizer (gl_ambient.py). No PyOpenGL.

GL entry points beyond 1.1 are fetched with glXGetProcAddressARB (libglvnd's libGL only exports the old ones).
"""
import ctypes
import ctypes.util

c_int, c_uint, c_ulong, c_long, c_float, c_char_p, c_void_p = (ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
                                                               ctypes.c_long, ctypes.c_float, ctypes.c_char_p,
                                                               ctypes.c_void_p)
c_short, c_ushort, c_ubyte = ctypes.c_short, ctypes.c_ushort, ctypes.c_ubyte


def _lib(soname, short):
    try:
        return ctypes.CDLL(soname)
    except OSError:
        found = ctypes.util.find_library(short)
        if not found:
            raise
        return ctypes.CDLL(found)


x11 = _lib("libX11.so.6", "X11")
xext = _lib("libXext.so.6", "Xext")
libgl = _lib("libGL.so.1", "GL")


class XVisualInfo(ctypes.Structure):
    _fields_ = [("visual", c_void_p), ("visualid", c_ulong), ("screen", c_int), ("depth", c_int),
                ("c_class", c_int), ("red_mask", c_ulong), ("green_mask", c_ulong), ("blue_mask", c_ulong),
                ("colormap_size", c_int), ("bits_per_rgb", c_int)]


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [("background_pixmap", c_ulong), ("background_pixel", c_ulong), ("border_pixmap", c_ulong),
                ("border_pixel", c_ulong), ("bit_gravity", c_int), ("win_gravity", c_int),
                ("backing_store", c_int), ("backing_planes", c_ulong), ("backing_pixel", c_ulong),
                ("save_under", c_int), ("event_mask", c_long), ("do_not_propagate_mask", c_long),
                ("override_redirect", c_int), ("colormap", c_ulong), ("cursor", c_ulong)]


class XColor(ctypes.Structure):
    _fields_ = [("pixel", c_ulong), ("red", c_ushort), ("green", c_ushort), ("blue", c_ushort),
                ("flags", ctypes.c_char), ("pad", ctypes.c_char)]


class XRectangle(ctypes.Structure):
    _fields_ = [("x", c_short), ("y", c_short), ("width", c_ushort), ("height", c_ushort)]


def _sig(lib, name, restype, *argtypes):
    f = getattr(lib, name)
    f.restype = restype
    f.argtypes = list(argtypes)
    return f


XOpenDisplay = _sig(x11, "XOpenDisplay", c_void_p, c_char_p)
XCloseDisplay = _sig(x11, "XCloseDisplay", c_int, c_void_p)
XDefaultScreen = _sig(x11, "XDefaultScreen", c_int, c_void_p)
XRootWindow = _sig(x11, "XRootWindow", c_ulong, c_void_p, c_int)
XDisplayWidth = _sig(x11, "XDisplayWidth", c_int, c_void_p, c_int)
XDisplayHeight = _sig(x11, "XDisplayHeight", c_int, c_void_p, c_int)
XCreateColormap = _sig(x11, "XCreateColormap", c_ulong, c_void_p, c_ulong, c_void_p, c_int)
XCreateWindow = _sig(x11, "XCreateWindow", c_ulong, c_void_p, c_ulong, c_int, c_int, c_uint, c_uint, c_uint,
                     c_int, c_uint, c_void_p, c_ulong, ctypes.POINTER(XSetWindowAttributes))
XMapWindow = _sig(x11, "XMapWindow", c_int, c_void_p, c_ulong)
XMapRaised = _sig(x11, "XMapRaised", c_int, c_void_p, c_ulong)
XUnmapWindow = _sig(x11, "XUnmapWindow", c_int, c_void_p, c_ulong)
XDestroyWindow = _sig(x11, "XDestroyWindow", c_int, c_void_p, c_ulong)
XFlush = _sig(x11, "XFlush", c_int, c_void_p)
XSync = _sig(x11, "XSync", c_int, c_void_p, c_int)
XFree = _sig(x11, "XFree", c_int, c_void_p)
XCreateBitmapFromData = _sig(x11, "XCreateBitmapFromData", c_ulong, c_void_p, c_ulong, c_char_p, c_uint, c_uint)
XCreatePixmapCursor = _sig(x11, "XCreatePixmapCursor", c_ulong, c_void_p, c_ulong, c_ulong,
                           ctypes.POINTER(XColor), ctypes.POINTER(XColor), c_uint, c_uint)
XFreePixmap = _sig(x11, "XFreePixmap", c_int, c_void_p, c_ulong)
XDefineCursor = _sig(x11, "XDefineCursor", c_int, c_void_p, c_ulong, c_ulong)
XInternAtom = _sig(x11, "XInternAtom", c_ulong, c_void_p, c_char_p, c_int)
XChangeProperty = _sig(x11, "XChangeProperty", c_int, c_void_p, c_ulong, c_ulong, c_ulong, c_int, c_int,
                       c_void_p, c_int)
XShapeCombineRectangles = _sig(xext, "XShapeCombineRectangles", None, c_void_p, c_ulong, c_int, c_int, c_int,
                               ctypes.POINTER(XRectangle), c_int, c_int, c_int)
XShapeQueryExtension = _sig(xext, "XShapeQueryExtension", c_int, c_void_p, ctypes.POINTER(c_int),
                            ctypes.POINTER(c_int))
_XErrorHandler = ctypes.CFUNCTYPE(c_int, c_void_p, c_void_p)
XSetErrorHandler = _sig(x11, "XSetErrorHandler", c_void_p, _XErrorHandler)
XSetIOErrorHandler = _sig(x11, "XSetIOErrorHandler", c_void_p, ctypes.CFUNCTYPE(c_int, c_void_p))

glXChooseVisual = _sig(libgl, "glXChooseVisual", ctypes.POINTER(XVisualInfo), c_void_p, c_int,
                       ctypes.POINTER(c_int))
glXCreateContext = _sig(libgl, "glXCreateContext", c_void_p, c_void_p, ctypes.POINTER(XVisualInfo), c_void_p,
                        c_int)
glXDestroyContext = _sig(libgl, "glXDestroyContext", None, c_void_p, c_void_p)
glXMakeCurrent = _sig(libgl, "glXMakeCurrent", c_int, c_void_p, c_ulong, c_void_p)
glXSwapBuffers = _sig(libgl, "glXSwapBuffers", None, c_void_p, c_ulong)
glXIsDirect = _sig(libgl, "glXIsDirect", c_int, c_void_p, c_void_p)
glXQueryExtensionsString = _sig(libgl, "glXQueryExtensionsString", c_char_p, c_void_p, c_int)
glXGetProcAddressARB = _sig(libgl, "glXGetProcAddressARB", c_void_p, c_char_p)

# X / GLX constants
CWBackPixmap, CWBorderPixel, CWOverrideRedirect, CWColormap, CWCursor = 1 << 0, 1 << 3, 1 << 9, 1 << 13, 1 << 14
InputOutput = 1
ShapeBounding, ShapeInput = 0, 2
ShapeSet, ShapeSubtract = 0, 3
GLX_RGBA, GLX_DOUBLEBUFFER, GLX_RED_SIZE, GLX_GREEN_SIZE, GLX_BLUE_SIZE = 4, 5, 8, 9, 10
GLX_SAMPLE_BUFFERS, GLX_SAMPLES = 100000, 100001

# GL constants
GL_COLOR_BUFFER_BIT = 0x4000
GL_TRIANGLES, GL_TRIANGLE_STRIP = 0x0004, 0x0005
GL_BLEND, GL_MULTISAMPLE = 0x0BE2, 0x809D
GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0302, 0x0303
GL_TEXTURE_2D, GL_TEXTURE0, GL_TEXTURE1 = 0x0DE1, 0x84C0, 0x84C1
GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR = 0x2801, 0x2800, 0x2601
GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE = 0x2802, 0x2803, 0x812F
GL_RGB8, GL_BGRA, GL_UNSIGNED_BYTE, GL_FLOAT = 0x8051, 0x80E1, 0x1401, 0x1406
GL_UNPACK_ROW_LENGTH, GL_UNPACK_ALIGNMENT = 0x0CF2, 0x0CF5
GL_ARRAY_BUFFER, GL_STREAM_DRAW, GL_STATIC_DRAW = 0x8892, 0x88E0, 0x88E4
GL_VERTEX_SHADER, GL_FRAGMENT_SHADER = 0x8B31, 0x8B30
GL_COMPILE_STATUS, GL_LINK_STATUS = 0x8B81, 0x8B82
GL_RENDERER, GL_VENDOR, GL_VERSION = 0x1F01, 0x1F00, 0x1F02


class GL:
    """The GL functions, resolved once a context is current."""

    _protos = {
        "glClearColor": (None, c_float, c_float, c_float, c_float),
        "glClear": (None, c_uint),
        "glViewport": (None, c_int, c_int, c_int, c_int),
        "glEnable": (None, c_uint),
        "glDisable": (None, c_uint),
        "glBlendFunc": (None, c_uint, c_uint),
        "glGetString": (c_char_p, c_uint),
        "glGetError": (c_uint,),
        "glFinish": (None,),
        "glGenTextures": (None, c_int, ctypes.POINTER(c_uint)),
        "glDeleteTextures": (None, c_int, ctypes.POINTER(c_uint)),
        "glBindTexture": (None, c_uint, c_uint),
        "glTexParameteri": (None, c_uint, c_uint, c_int),
        "glPixelStorei": (None, c_uint, c_int),
        "glTexImage2D": (None, c_uint, c_int, c_int, c_int, c_int, c_int, c_uint, c_uint, c_void_p),
        "glDrawArrays": (None, c_uint, c_int, c_int),
        "glActiveTexture": (None, c_uint),
        "glCreateShader": (c_uint, c_uint),
        "glShaderSource": (None, c_uint, c_int, ctypes.POINTER(c_char_p), ctypes.POINTER(c_int)),
        "glCompileShader": (None, c_uint),
        "glGetShaderiv": (None, c_uint, c_uint, ctypes.POINTER(c_int)),
        "glGetShaderInfoLog": (None, c_uint, c_int, ctypes.POINTER(c_int), c_char_p),
        "glCreateProgram": (c_uint,),
        "glAttachShader": (None, c_uint, c_uint),
        "glBindAttribLocation": (None, c_uint, c_uint, c_char_p),
        "glLinkProgram": (None, c_uint),
        "glGetProgramiv": (None, c_uint, c_uint, ctypes.POINTER(c_int)),
        "glGetProgramInfoLog": (None, c_uint, c_int, ctypes.POINTER(c_int), c_char_p),
        "glUseProgram": (None, c_uint),
        "glGetUniformLocation": (c_int, c_uint, c_char_p),
        "glUniform1f": (None, c_int, c_float),
        "glUniform2f": (None, c_int, c_float, c_float),
        "glUniform1i": (None, c_int, c_int),
        "glGenBuffers": (None, c_int, ctypes.POINTER(c_uint)),
        "glBindBuffer": (None, c_uint, c_uint),
        "glBufferData": (None, c_uint, ctypes.c_ssize_t, c_void_p, c_uint),
        "glVertexAttribPointer": (None, c_uint, c_int, c_uint, c_ubyte, c_int, c_void_p),
        "glEnableVertexAttribArray": (None, c_uint),
        "glDisableVertexAttribArray": (None, c_uint),
    }

    def __init__(self):
        for name, (res, *args) in self._protos.items():
            addr = None
            try:
                f = getattr(libgl, name)
                addr = ctypes.cast(f, c_void_p).value
            except AttributeError:
                pass
            if not addr:
                addr = glXGetProcAddressARB(name.encode())
            if not addr:
                raise RuntimeError("missing GL function " + name)
            setattr(self, name[2:], ctypes.CFUNCTYPE(res, *args)(addr))

    def shader(self, kind, src):
        sh = self.CreateShader(kind)
        buf = c_char_p(src.encode())
        self.ShaderSource(sh, 1, ctypes.byref(buf), None)
        self.CompileShader(sh)
        ok = c_int(0)
        self.GetShaderiv(sh, GL_COMPILE_STATUS, ctypes.byref(ok))
        if not ok.value:
            log = ctypes.create_string_buffer(4096)
            self.GetShaderInfoLog(sh, 4096, None, log)
            raise RuntimeError("shader: " + log.value.decode(errors="replace"))
        return sh

    def program(self, vs, fs, attribs):
        prog = self.CreateProgram()
        self.AttachShader(prog, self.shader(GL_VERTEX_SHADER, vs))
        self.AttachShader(prog, self.shader(GL_FRAGMENT_SHADER, fs))
        for i, name in enumerate(attribs):
            self.BindAttribLocation(prog, i, name.encode())
        self.LinkProgram(prog)
        ok = c_int(0)
        self.GetProgramiv(prog, GL_LINK_STATUS, ctypes.byref(ok))
        if not ok.value:
            log = ctypes.create_string_buffer(4096)
            self.GetProgramInfoLog(prog, 4096, None, log)
            raise RuntimeError("link: " + log.value.decode(errors="replace"))
        return prog

    def uniform(self, prog, name):
        return self.GetUniformLocation(prog, name.encode())
