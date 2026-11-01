from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audio_output  # noqa: E402

SINKS = """Sink #58
\tState: SUSPENDED
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo
\tDescription: Built-in Audio Analog Stereo
\tPorts:
\t\tanalog-output-speaker: Speakers (type: Speaker, priority: 10000, availability unknown)
\t\tanalog-output-headphones: Headphones (type: Headphones, priority: 9900, not available)
\tActive Port: analog-output-speaker
\tFormats:
\t\tpcm
Sink #60
\tName: alsa_output.usb-Old-00.analog-stereo
\tDescription: Unplugged Thing
\tPorts:
\t\tanalog-output: Analog (type: Line, priority: 1, not available)
\tActive Port: analog-output
Sink #61
\tName: imactv_phone_speaker
\tDescription: Phone_Speaker
"""
CARDS = """Card #42
\tName: alsa_card.pci-0000_00_1f.3
\tProperties:
\t\tdevice.description = "Built-in Audio"
\tProfiles:
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\t\toutput:analog-stereo: Analog Stereo Output (sinks: 1, sources: 0, priority: 6500, available: yes)
\t\toutput:hdmi-stereo: Digital Stereo (HDMI) Output (sinks: 1, sources: 0, priority: 5900, available: yes)
\t\toutput:hdmi-stereo+input:analog-stereo: Digital Stereo (HDMI) Output + Analog Stereo Input (sinks: 1, sources: 1, priority: 5965, available: yes)
\t\toutput:hdmi-stereo-extra1: Digital Stereo (HDMI 2) Output (sinks: 1, sources: 0, priority: 5700, available: no)
\t\toutput:hdmi-surround: Digital Surround 5.1 (HDMI) Output (sinks: 1, sources: 0, priority: 800, available: yes)
\tActive Profile: output:analog-stereo
"""
ANALOG = "alsa_output.pci-0000_00_1f.3.analog-stereo"
HDMI = "alsa_output.pci-0000_00_1f.3.hdmi-stereo"
HDMI_ID = "profile:alsa_card.pci-0000_00_1f.3:output:hdmi-stereo+input:analog-stereo"


class FakePactl:
    def __init__(self):
        self.default = ANALOG
        self.calls = []
        self.short_sinks = "58\t%s\tPipeWire\n70\timactv_phone_mic\tPipeWire" % ANALOG

    def __call__(self, *args):
        self.calls.append(args)
        if args == ("list", "sinks"):
            return SINKS
        if args == ("list", "cards"):
            return CARDS
        if args == ("get-default-sink",):
            return self.default
        if args == ("list", "short", "sinks"):
            return self.short_sinks
        if args == ("list", "short", "sink-inputs"):
            return "5\t58\t1\tprotocol\tfloat\n6\t70\t1\tprotocol\tfloat"
        if args[0] == "set-card-profile":
            self.short_sinks += "\n71\t%s\tPipeWire" % HDMI
        return ""

    def changes(self):
        return [c for c in self.calls if c[0] in ("set-card-profile", "set-default-sink", "move-sink-input")]


class OutputsTest(unittest.TestCase):
    def test_lists_live_sinks_and_profiles_people_can_use(self):
        self.assertEqual(audio_output.outputs(FakePactl()), [
            {"id": "sink:" + ANALOG, "name": "Built-in Audio Analog Stereo", "current": True},
            {"id": HDMI_ID, "name": "Built-in Audio Digital Stereo (HDMI)", "current": False}])

    def test_unplugged_default_stays_listed(self):
        pactl = FakePactl()
        pactl.default = "alsa_output.usb-Old-00.analog-stereo"
        self.assertEqual(audio_output.outputs(pactl)[0]["name"], "Unplugged Thing")

    def test_select_sink_moves_streams_but_not_the_phone_mic(self):
        pactl = FakePactl()
        audio_output.select("sink:" + ANALOG, pactl)
        self.assertEqual(pactl.changes(), [("set-default-sink", ANALOG), ("move-sink-input", "5", ANALOG)])

    def test_select_profile_switches_the_card_first(self):
        pactl = FakePactl()
        audio_output.select(HDMI_ID, pactl, sleep=lambda _s: None)
        self.assertEqual(pactl.changes()[:2], [("set-card-profile", "alsa_card.pci-0000_00_1f.3",
                                               "output:hdmi-stereo+input:analog-stereo"), ("set-default-sink", HDMI)])

    def test_select_rejects_anything_not_listed(self):
        pactl = FakePactl()
        for bad in ("sink:imactv_phone_speaker", "sink:other", "", "profile:x:y"):
            with self.assertRaises(ValueError):
                audio_output.select(bad, pactl)
        self.assertEqual(pactl.changes(), [])
