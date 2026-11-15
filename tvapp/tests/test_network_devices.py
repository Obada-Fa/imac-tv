"""Wi-Fi (nmcli) and Bluetooth (BlueZ) behind Settings -> Network, against stand-ins for both."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bluetooth  # noqa: E402
import wifi  # noqa: E402


class FakeNmcli:
    def __init__(self):
        self.calls = []
        self.saved = ["Home"]
        self.radio = "enabled"
        self.fail = {}

    def __call__(self, args, timeout=20):
        self.calls.append(list(args))
        text = " ".join(args)
        if "DEVICE,TYPE,STATE" in text:
            return 0, "wlp2s0:wifi:connected\nenp1s0:ethernet:unavailable\nlo:loopback:connected (externally)\n", ""
        if "WIFI radio" in text:
            return 0, self.radio + "\n", ""
        if "NAME,TYPE" in text:
            return 0, "".join("%s:802-11-wireless\n" % n for n in self.saved) + "Wired:802-3-ethernet\n", ""
        if "wifi list" in text:
            return 0, ("*:Home:71:WPA2\n :Home:40:WPA2\n :Cafe\\: guests:55:\n :Neighbour:88:WPA2 WPA3\n ::30:WPA2\n"), ""
        for key, err in self.fail.items():
            if key in text:
                return 4, "", err
        if "connection delete" in text and args[-1] in self.saved:
            self.saved.remove(args[-1])
        return 0, "", ""


class WifiTest(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNmcli()
        self.wifi = wifi.Wifi(run=self.nm, have=lambda: True)

    def test_split_unescapes_colons(self):
        self.assertEqual(wifi.split(" :Cafe\\: guests:55:"), [" ", "Cafe: guests", "55", ""])

    def test_status_lists_each_name_once_connected_first(self):
        s = self.wifi.status()
        self.assertTrue(s["available"] and s["enabled"])
        self.assertEqual((s["ssid"], s["signal"], s["ethernet"]), ("Home", 71, False))
        self.assertEqual([n["ssid"] for n in s["networks"]], ["Home", "Neighbour", "Cafe: guests"])
        home, neighbour, cafe = s["networks"]
        self.assertTrue(home["active"] and home["saved"] and home["secure"])
        self.assertFalse(neighbour["saved"])
        self.assertFalse(cafe["secure"])
        self.assertEqual(wifi.describe(s), "Home")

    def test_scan_asks_for_a_rescan(self):
        self.wifi.status(rescan=True)
        self.assertIn("yes", next(c for c in self.nm.calls if "list" in c))

    def test_radio_off_and_no_nmcli(self):
        self.nm.radio = "disabled"
        s = self.wifi.status()
        self.assertEqual((s["enabled"], s["networks"]), (False, []))
        self.assertEqual(wifi.describe(s), "Off")
        none = wifi.Wifi(run=self.nm, have=lambda: False).status()
        self.assertFalse(none["available"])

    def test_saved_network_connects_without_password(self):
        self.assertEqual(self.wifi.connect("Home"), (True, "Connected to Home"))
        self.assertEqual(self.nm.calls[-1], ["--wait", "30", "connection", "up", "id", "Home"])

    def test_new_password_replaces_saved_and_wrong_one_is_not_kept(self):
        self.nm.fail["device wifi connect"] = "Error: Connection activation failed: (7) Secrets were required, but not provided."
        ok, message = self.wifi.connect("Home", "wrong-pass")
        self.assertEqual((ok, message), (False, "That password didn't work"))
        deletes = [c for c in self.nm.calls if c[:2] == ["connection", "delete"]]
        self.assertEqual(len(deletes), 2)                                # the old profile, then the failed new one
        self.assertIn(["--wait", "30", "device", "wifi", "connect", "Home", "password", "wrong-pass"], self.nm.calls)

    def test_open_network_and_forget(self):
        self.assertTrue(self.wifi.connect("Cafe: guests")[0])
        self.assertEqual(self.nm.calls[-1], ["--wait", "30", "device", "wifi", "connect", "Cafe: guests"])
        self.assertEqual(self.wifi.forget("Home"), (True, "Forgot Home"))
        self.assertEqual(self.wifi.connect("")[0], False)


AD = "/org/bluez/hci0"


class FakeBus:
    def __init__(self):
        self.calls, self.sets, self.fail = [], [], {}
        self.objs = {
            "/org/bluez": {},
            AD: {bluetooth.ADAPTER: {"Powered": True, "Discovering": False}},
            AD + "/dev_AA": {bluetooth.DEVICE: {"Address": "AA:AA", "Name": "Couch Speaker", "Alias": "Couch Speaker",
                                                "Icon": "audio-headset", "Paired": True, "Connected": True},
                             bluetooth.BATTERY: {"Percentage": 80}},
            AD + "/dev_BB": {bluetooth.DEVICE: {"Address": "BB:BB", "Name": "Pad", "Alias": "Pad", "Icon": "input-gaming",
                                                "Paired": False, "Connected": False, "RSSI": -60}},
            AD + "/dev_CC": {bluetooth.DEVICE: {"Address": "CC:CC", "Alias": "CC-CC", "Paired": False, "Connected": False,
                                                "RSSI": -40}},
            AD + "/dev_DD": {bluetooth.DEVICE: {"Address": "DD:DD", "Name": "Old Mouse", "Alias": "Old Mouse",
                                                "Icon": "input-mouse", "Paired": True, "Connected": False}},
        }

    def objects(self):
        return self.objs

    def call(self, path, iface, method, signature=None, values=None, timeout=10):
        self.calls.append((path, method, values))
        if method in self.fail:
            raise RuntimeError(self.fail[method])
        dev = self.objs.get(path, {}).get(bluetooth.DEVICE)
        if method == "Pair":
            dev["Paired"] = True
        elif method == "Connect":
            dev["Connected"] = True
        elif method == "Disconnect":
            dev["Connected"] = False
        elif method == "RemoveDevice":
            del self.objs[values[0]]
        elif method == "StartDiscovery":
            self.objs[AD][bluetooth.ADAPTER]["Discovering"] = True
        elif method == "StopDiscovery":
            self.objs[AD][bluetooth.ADAPTER]["Discovering"] = False
        return ()

    def set(self, path, iface, name, signature, value):
        self.sets.append((path, name, value))
        self.objs[path][iface][name] = value


class BluetoothTest(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.now = [100.0]
        self.ran = []
        self.bt = bluetooth.Bluetooth(bus=lambda: self.bus, run=lambda *a, **k: self.ran.append(a[0]),
                                      clock=lambda: self.now[0])
        self.addCleanup(self.bt.stop_scan)

    def test_status_orders_own_devices_first_and_hides_nameless(self):
        s = self.bt.status()
        self.assertTrue(s["available"] and s["enabled"])
        self.assertEqual([d["name"] for d in s["devices"]], ["Couch Speaker", "Old Mouse", "Pad"])
        self.assertEqual(s["devices"][0]["battery"], 80)
        self.assertEqual([d["kind"] for d in s["devices"]], ["audio", "mouse", "gamepad"])
        self.assertNotIn("path", s["devices"][0])
        self.assertEqual(bluetooth.describe(s), "Couch Speaker")
        self.assertEqual(bluetooth.device_line(s["devices"][0]), "Speaker or headphones  ·  Connected  ·  battery 80%")

    def test_new_device_is_paired_trusted_and_connected(self):
        self.assertEqual(self.bt.connect("bb:bb"), (True, "Connected to Pad"))
        self.assertEqual([c[1] for c in self.bus.calls], ["Pair", "Connect"])
        self.assertIn((AD + "/dev_BB", "Trusted", True), self.bus.sets)

    def test_paired_device_is_not_paired_again(self):
        self.bt.connect("DD:DD")
        self.assertEqual([c[1] for c in self.bus.calls], ["Connect"])

    def test_refused_pairing_says_so(self):
        self.bus.fail["Pair"] = "GDBus.Error:org.bluez.Error.AuthenticationFailed: Authentication Failed"
        ok, message = self.bt.connect("BB:BB")
        self.assertFalse(ok)
        self.assertIn("pairing mode", message)

    def test_disconnect_forget_and_gone(self):
        self.assertEqual(self.bt.disconnect("AA:AA"), (True, "Disconnected Couch Speaker"))
        self.assertEqual(self.bt.forget("AA:AA"), (True, "Forgot Couch Speaker"))
        self.assertEqual(self.bus.calls[-1], (AD, "RemoveDevice", (AD + "/dev_AA",)))
        self.assertEqual(self.bt.connect("AA:AA"), (False, "That device is out of range now"))

    def test_scan_runs_then_reports_done(self):
        self.assertTrue(self.bt.scan(30)[0])
        self.assertTrue(self.bt.status()["scanning"])
        self.now[0] += 31
        self.assertFalse(self.bt.status()["scanning"])
        self.bt.stop_scan()
        self.assertEqual(self.bus.calls[-1][1], "StopDiscovery")

    def test_power_unblocks_and_sets(self):
        self.assertEqual(self.bt.power(True), (True, "Bluetooth is on"))
        self.assertEqual(self.ran, [["rfkill", "unblock", "bluetooth"]])
        self.bt.power(False)
        self.assertFalse(self.bt.status()["enabled"])
        self.assertEqual(self.bt.scan()[0], False)

    def test_no_bluez(self):
        def broken():
            raise RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown")
        bt = bluetooth.Bluetooth(bus=broken, run=lambda *a, **k: None)
        self.assertEqual(bt.status(), {"available": False, "enabled": False, "scanning": False, "devices": []})
        self.assertEqual(bluetooth.describe(bt.status()), "No Bluetooth in this PC")
        self.assertFalse(bt.power(True)[0])


if __name__ == "__main__":
    unittest.main()
