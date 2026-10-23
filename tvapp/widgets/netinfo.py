"""Network + pairing helpers shared by Home's header chip, the pairing widget and the pairing dialog."""
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screens  # noqa: E402

try:
    import qrcode
except ImportError:
    qrcode = None


def get_network_info():
    """Discover active LAN IP, connection type (Wi-Fi/Ethernet), and Wi-Fi signal/SSID."""
    info = {"connected": False, "type": "none", "ssid": "", "signal": 0, "ip": "127.0.0.1"}

    try:
        route_out = subprocess.check_output(["ip", "route", "get", "1.1.1.1"], text=True, timeout=1)
        m = re.search(r"src\s+([0-9\.]+)", route_out)
        if m:
            info["ip"] = m.group(1)
    except Exception:
        pass
    if info["ip"] == "127.0.0.1":
        try:
            ips = subprocess.check_output(["hostname", "-I"], text=True, timeout=1).split()
            for ip in ips:
                if ip.startswith(("192.168.", "10.", "172.")) and not ip.startswith(("172.17.", "172.18.")):
                    info["ip"] = ip
                    break
        except Exception:
            pass

    try:
        wf_out = subprocess.check_output(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no"],
            text=True, timeout=1
        )
        for line in wf_out.strip().splitlines():
            if line.startswith("yes:"):
                parts = line.split(":")
                if len(parts) >= 3:
                    info["connected"] = True
                    info["type"] = "wifi"
                    info["ssid"] = parts[1]
                    try:
                        info["signal"] = int(parts[2])
                    except ValueError:
                        info["signal"] = 75
                    break
    except Exception:
        pass

    if info["type"] != "wifi":
        try:
            dev_out = subprocess.check_output(
                ["nmcli", "-t", "-f", "TYPE,STATE", "device"],
                text=True, timeout=1
            )
            for line in dev_out.strip().splitlines():
                if line.startswith("ethernet:connected"):
                    info["connected"] = True
                    info["type"] = "ethernet"
                    break
        except Exception:
            pass

    if info["type"] == "none" and info["ip"] != "127.0.0.1":
        info["connected"] = True

    return info


def make_qr_pixbuf(text, size=320):
    """Generate in-memory GdkPixbuf QR code."""
    if not qrcode:
        return None
    try:
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        loader = GdkPixbuf.PixbufLoader()
        loader.write(buf.getvalue())
        loader.close()
        pb = loader.get_pixbuf()
        if size and (pb.get_width() != size or pb.get_height() != size):
            return pb.scale_simple(size, size, GdkPixbuf.InterpType.NEAREST)
        return pb
    except Exception:
        return None


def current_code():
    """Active pairing code from pairing.json, else the controller API, else '------'."""
    try:
        info = json.loads((screens.DATA / "pairing.json").read_text())
        if time.time() < info.get("expires", 0):
            return str(info.get("code", "------"))
    except (OSError, ValueError, KeyError):
        pass
    res = screens.local_api("/api/pair-status")
    if res and "code" in res:
        return str(res["code"])
    return "------"


def fmt_code(code):
    return f"{code[:3]} {code[3:]}" if len(code) == 6 else code


def paired_phones(data_dir=None):
    """Phones paired so far: devices.json holds every session token, one of which is the TV's own."""
    try:
        d = json.loads(((data_dir or screens.DATA) / "devices.json").read_text())
        return max(0, len(d) - 1) if isinstance(d, dict) else 0
    except (OSError, ValueError):
        return 0


def net_line(net):
    if net.get("type") == "wifi" and net.get("connected"):
        return "📶 %s" % (net.get("ssid") or "Wi-Fi")
    if net.get("type") == "ethernet" and net.get("connected"):
        return "🖧 Wired"
    return "🌐 Online" if net.get("connected") else "⚠ Offline"
