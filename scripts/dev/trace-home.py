"""Run home.py with redraw-causing GTK calls counted by caller. usage: trace-home.py DIR  -> /tmp/nexus-trace.txt"""
import sys, runpy, collections, traceback, time, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
DIR = sys.argv[1]; sys.argv = [DIR + "/home.py"]; sys.path.insert(0, DIR)
C = collections.Counter(); START = time.monotonic() + 30
def wrap(cls, name):
    try: orig = getattr(cls, name)
    except AttributeError: return
    def w(self, *a, **k):
        if time.monotonic() > START:
            st = traceback.extract_stack(limit=5)[:-1]
            C[(cls.__name__ + "." + name, type(self).__name__, " < ".join(f"{f.name}:{f.lineno}" for f in reversed(st)))] += 1
        return orig(self, *a, **k)
    setattr(cls, name, w)
for n in ("queue_draw", "queue_draw_area", "queue_draw_region", "queue_resize", "set_opacity", "set_visible", "show", "hide",
          "show_all", "set_child_visible", "set_size_request", "set_no_show_all", "set_sensitive", "set_tooltip_text", "set_name"):
    wrap(Gtk.Widget, n)
for n in ("set_text", "set_markup", "set_label"): wrap(Gtk.Label, n)
for n in ("move", "put"): wrap(Gtk.Fixed, n)
for n in ("add_class", "remove_class", "add_provider"): wrap(Gtk.StyleContext, n)
for n in ("load_from_data",): wrap(Gtk.CssProvider, n)
for n in ("set_from_pixbuf", "set_from_surface", "set_from_icon_name"): wrap(Gtk.Image, n)
for n in ("add", "remove"): wrap(Gtk.Container, n)
def dump():
    with open("/tmp/nexus-trace.txt", "w") as f:
        f.write(f"{time.monotonic() - START:.0f} s traced\n")
        for (call, typ, st), n in C.most_common(40): f.write(f"{n:5} {call} on {typ}: {st}\n")
    return True
GLib.timeout_add_seconds(5, dump)
runpy.run_path(DIR + "/home.py", run_name="__main__")
