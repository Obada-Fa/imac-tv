"""Bluetooth from the couch: Settings -> Network on the TV and the phone's Settings. Talks to BlueZ over the
system D-Bus (the same on every BlueZ 5; bluetoothctl's own commands changed between versions). Pairing has no
agent of its own, so it covers what pairs without a code: speakers, headphones, controllers, mice and most new
keyboards. A device that wants a code typed has to be paired once from the PC's own Bluetooth settings."""
import re
import subprocess
import threading
import time

ADAPTER, DEVICE, BATTERY = "org.bluez.Adapter1", "org.bluez.Device1", "org.bluez.Battery1"
SCAN_S = 25
KINDS = (("audio", "audio"), ("input-keyboard", "keyboard"), ("input-mouse", "mouse"), ("input-tablet", "mouse"),
         ("input-gaming", "gamepad"), ("phone", "phone"), ("computer", "computer"), ("video-display", "tv"))


class Bus:
    """The few D-Bus calls needed, so tests can stand in for BlueZ."""

    def __init__(self):
        import gi
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def call(self, path, iface, method, signature=None, values=None, timeout=10):
        args = self.GLib.Variant(signature, values) if signature else None
        res = self.conn.call_sync("org.bluez", path, iface, method, args, None, self.Gio.DBusCallFlags.NONE,
                                  int(timeout * 1000), None)
        return res.unpack() if res is not None else ()

    def objects(self):
        return self.call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")[0]

    def set(self, path, iface, name, signature, value):
        self.call(path, "org.freedesktop.DBus.Properties", "Set", "(ssv)",
                  (iface, name, self.GLib.Variant(signature, value)))


def friendly(exc):
    """BlueZ's error in words for the TV."""
    text = str(exc)
    low = text.lower()
    if "authentication" in low and ("failed" in low or "rejected" in low or "canceled" in low):
        return "Pairing was refused. Put the device in pairing mode and try again"
    if "timeout" in low or "connectionattemptfailed" in low or "page-timeout" in low or "host-down" in low:
        return "It didn't answer. Is it on, close by and in pairing mode?"
    if "notready" in low:
        return "Bluetooth is off"
    if "profile-unavailable" in low or "notavailable" in low:
        return "This PC can't use that kind of device"
    if "doesnotexist" in low or "unknownobject" in low:
        return "That device is out of range now"
    if "serviceunknown" in low or "name has no owner" in low:
        return "Bluetooth isn't running on this PC"
    m = re.search(r"org\.bluez\.Error\.\w+: (.+)$", text)
    return (m.group(1) if m else text.strip().splitlines()[-1] if text.strip() else "Bluetooth error")[:160]


def kind_of(icon):
    return next((kind for prefix, kind in KINDS if (icon or "").startswith(prefix)), "device")


def device_of(path, ifaces):
    d = ifaces[DEVICE]
    battery = (ifaces.get(BATTERY) or {}).get("Percentage")
    return {"address": d.get("Address", ""), "name": d.get("Name") or d.get("Alias") or d.get("Address", ""),
            "named": bool(d.get("Name")), "kind": kind_of(d.get("Icon")), "paired": bool(d.get("Paired")),
            "connected": bool(d.get("Connected")), "rssi": d.get("RSSI"), "battery": battery, "path": path}


class Bluetooth:
    def __init__(self, bus=Bus, run=subprocess.run, clock=time.monotonic):
        self._factory, self._bus, self.run, self.clock = bus, None, run, clock
        self.lock = threading.Lock()
        self.scan_until = 0.0
        self.timer = None

    def bus(self):
        if self._bus is None:
            self._bus = self._factory()
        return self._bus

    def snapshot(self):
        """(adapter path, adapter properties, devices) or (None, {}, []) without Bluetooth."""
        try:
            objs = self.bus().objects()
        except Exception:
            self._bus = None                             # bluetoothd restarted or isn't there: ask afresh next time
            return None, {}, []
        adapter = next((p for p in sorted(objs) if ADAPTER in objs[p]), None)
        if adapter is None:
            return None, {}, []
        devices = [device_of(p, i) for p, i in objs.items() if DEVICE in i and p.startswith(adapter + "/")]
        return adapter, objs[adapter][ADAPTER], devices

    def status(self):
        """{"available", "enabled", "scanning", "devices"}: this PC's devices first (connected, then paired), then
        named devices in range, nearest first. Nameless beacons are left out."""
        adapter, props, devices = self.snapshot()
        out = {"available": adapter is not None, "enabled": bool(props.get("Powered")),
               "scanning": bool(props.get("Discovering")) and self.clock() < self.scan_until, "devices": []}
        if not out["enabled"]:
            return out
        shown = [d for d in devices if d["paired"] or d["connected"] or (d["named"] and d["rssi"] is not None)]
        shown.sort(key=lambda d: (not d["connected"], not d["paired"], -(d["rssi"] if d["rssi"] is not None else -200),
                                  d["name"].lower()))
        out["devices"] = [{k: v for k, v in d.items() if k not in ("path", "named")} for d in shown]
        return out

    def find(self, address):
        adapter, _, devices = self.snapshot()
        return adapter, next((d for d in devices if d["address"].lower() == (address or "").lower()), None)

    def power(self, on):
        if on:
            try:                                         # the keyboard's airplane key leaves it soft-blocked
                self.run(["rfkill", "unblock", "bluetooth"], capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        adapter, _, _ = self.snapshot()
        if adapter is None:
            return False, "No Bluetooth in this PC"
        try:
            self.bus().set(adapter, ADAPTER, "Powered", "b", bool(on))
        except Exception as exc:
            return False, friendly(exc)
        return True, "Bluetooth is " + ("on" if on else "off")

    def scan(self, seconds=SCAN_S):
        """Look for devices for `seconds`; asking again while it runs keeps it going."""
        adapter, props, _ = self.snapshot()
        if adapter is None or not props.get("Powered"):
            return False, "Bluetooth is off" if adapter else "No Bluetooth in this PC"
        with self.lock:
            try:
                self.bus().call(adapter, ADAPTER, "StartDiscovery")
            except Exception as exc:
                if "inprogress" not in str(exc).lower():
                    return False, friendly(exc)
            self.scan_until = self.clock() + seconds
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(seconds, self.stop_scan)
            self.timer.daemon = True
            self.timer.start()
        return True, "Looking for devices…"

    def stop_scan(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            self.scan_until = 0.0
            adapter, _, _ = self.snapshot()
            if adapter:
                try:
                    self.bus().call(adapter, ADAPTER, "StopDiscovery")
                except Exception:
                    pass

    def connect(self, address):
        """Pair if needed (no code), remember it so it reconnects by itself, and connect."""
        _, dev = self.find(address)
        if not dev:
            return False, "That device is out of range now"
        bus, path, name = self.bus(), dev["path"], dev["name"]
        if not dev["paired"]:
            try:
                bus.call(path, DEVICE, "Pair", timeout=60)
            except Exception as exc:
                if "alreadyexists" not in str(exc).lower():
                    return False, friendly(exc)
        try:
            bus.set(path, DEVICE, "Trusted", "b", True)
        except Exception:
            pass
        try:
            bus.call(path, DEVICE, "Connect", timeout=40)
        except Exception as exc:
            _, now = self.find(address)
            if not (now and now["connected"]):           # some mice and keyboards connect themselves once paired
                return False, ("Paired with %s, but it didn't connect: " % name if not dev["paired"] else "") + friendly(exc)
        return True, "Connected to " + name

    def disconnect(self, address):
        _, dev = self.find(address)
        if not dev:
            return False, "That device is out of range now"
        try:
            self.bus().call(dev["path"], DEVICE, "Disconnect", timeout=15)
        except Exception as exc:
            return False, friendly(exc)
        return True, "Disconnected " + dev["name"]

    def forget(self, address):
        adapter, dev = self.find(address)
        if not dev:
            return True, "Forgotten"
        try:
            self.bus().call(adapter, ADAPTER, "RemoveDevice", "(o)", (dev["path"],))
        except Exception as exc:
            return False, friendly(exc)
        return True, "Forgot " + dev["name"]


def describe(status):
    """One line for a settings row."""
    if not status.get("available"):
        return "No Bluetooth in this PC"
    if not status.get("enabled"):
        return "Off"
    names = [d["name"] for d in status.get("devices", []) if d.get("connected")]
    return ", ".join(names[:2]) + ("  +%d" % (len(names) - 2) if len(names) > 2 else "") if names else "On"


def device_line(d):
    """What stands under a device's name."""
    words = {"audio": "Speaker or headphones", "keyboard": "Keyboard", "mouse": "Mouse", "gamepad": "Controller",
             "phone": "Phone", "computer": "Computer", "tv": "Screen", "device": "Device"}[d.get("kind") or "device"]
    state = "Connected" if d.get("connected") else "Paired" if d.get("paired") else "In range"
    battery = "  ·  battery %d%%" % d["battery"] if d.get("battery") is not None else ""
    return "%s  ·  %s%s" % (words, state, battery)
