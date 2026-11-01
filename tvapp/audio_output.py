"""Which device the TV's sound comes out of, shared by the phone remote and the TV's settings.

An output is a live sink, or a card profile that would create one: a laptop's HDMI sound only exists as a
sink after its card switches profile. Choosing one makes it the default and moves everything playing onto it.
"""
import os
import re
import subprocess
import time

import phonemic
import phonespeaker

OWN_SINKS = (phonemic.SINK, phonespeaker.SINK)     # the phone features' private sinks, never a choice here
PROFILE = re.compile(r"^\s+(\S+): (.*) \(sinks: (\d+), sources: \d+, priority: (\d+), available: (yes|no)\)$")
PROFILE_WAIT = 3.0


def _pactl(*args):
    out = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=3,
                         env={**os.environ, "LC_ALL": "C"})     # the parsers below read pactl's English
    if out.returncode:
        raise RuntimeError((out.stderr or "pactl failed").strip()[:200])
    return out.stdout.strip()


def _blocks(text, header):
    return [b for b in re.split(r"^(?=%s #)" % header, text, flags=re.M) if b.startswith(header)]


def _field(block, name):
    found = re.search(r"^\s*%s: (.*)$" % name, block, flags=re.M)
    return found.group(1).strip() if found else ""


def parse_sinks(text):
    """`pactl list sinks` -> [{name, description, plugged}]; plugged is False when every port is not available."""
    sinks = []
    for block in _blocks(text, "Sink"):
        ports = re.search(r"^\s*Ports:\n((?:\t\t.*\n?)*)", block, flags=re.M)
        lines = ports.group(1).splitlines() if ports else []
        sinks.append({"name": _field(block, "Name"), "description": _field(block, "Description"),
                      "plugged": not lines or any("not available" not in line for line in lines)})
    return sinks


def parse_cards(text):
    """`pactl list cards` -> [{name, description, profiles: [{name, description, sinks, priority, available}]}]."""
    cards = []
    for block in _blocks(text, "Card"):
        described = re.search(r'device\.description = "(.*)"', block)
        profiles = []
        for line in block.splitlines():
            found = PROFILE.match(line)
            if found:
                profiles.append({"name": found.group(1), "description": found.group(2), "sinks": int(found.group(3)),
                                 "priority": int(found.group(4)), "available": found.group(5) == "yes"})
        cards.append({"name": _field(block, "Name"), "description": described.group(1) if described else "",
                      "profiles": profiles})
    return cards


def sink_of(card, part):
    return "%s.%s" % (card.replace("alsa_card.", "alsa_output.", 1), part)


def _profile_outputs(card, sink_names):
    """The card's stereo outputs with no sink yet, each through its best profile (one that keeps the mic if any)."""
    best = {}
    for profile in card["profiles"]:
        outs = [p[len("output:"):] for p in profile["name"].split("+") if p.startswith("output:")]
        if len(outs) != 1 or not profile["sinks"] or not profile["available"] or "surround" in outs[0]:
            continue
        if sink_of(card["name"], outs[0]) in sink_names:
            continue
        if outs[0] not in best or profile["priority"] > best[outs[0]]["priority"]:
            best[outs[0]] = profile
    for part, profile in best.items():
        kind = re.sub(r" (Output|Duplex)\b.*$", "", profile["description"].split(" + ")[0])
        yield {"id": "profile:%s:%s" % (card["name"], profile["name"]), "part": part,
               "name": " ".join(x for x in (card["description"], kind) if x), "current": False}


def build(sinks, cards, default):
    """Pure: parsed sinks and cards plus the default sink's name -> [{id, name, current}], the current one first."""
    live = [s for s in sinks if s["name"] not in OWN_SINKS and (s["plugged"] or s["name"] == default)]
    found = [{"id": "sink:" + s["name"], "name": s["description"] or s["name"], "current": s["name"] == default}
             for s in live]
    names = {s["name"] for s in sinks}
    for card in cards:
        found.extend({k: v for k, v in o.items() if k != "part"} for o in _profile_outputs(card, names))
    return sorted(found, key=lambda o: not o["current"])


def outputs(pactl=_pactl):
    return build(parse_sinks(pactl("list", "sinks")), parse_cards(pactl("list", "cards")), pactl("get-default-sink"))


def _short(pactl, what):
    return [line.split("\t") for line in pactl("list", "short", what).splitlines() if line.strip()]


def select(output_id, pactl=_pactl, sleep=time.sleep):
    """Make an entry of outputs() the default output and move what is playing onto it."""
    if not any(o["id"] == output_id for o in outputs(pactl)):
        raise ValueError("That sound output is not available")
    kind, _, target = output_id.partition(":")
    if kind == "profile":
        card, _, profile = target.partition(":")
        part = next(p[len("output:"):] for p in profile.split("+") if p.startswith("output:"))
        target = sink_of(card, part)
        pactl("set-card-profile", card, profile)
        waited = 0
        while target not in [row[1] for row in _short(pactl, "sinks")]:
            if waited >= PROFILE_WAIT:
                raise RuntimeError("That sound output did not start")
            sleep(0.2)
            waited += 0.2
    pactl("set-default-sink", target)
    # The phone mic's feed plays into its private sink and has to stay there.
    mic = [row[0] for row in _short(pactl, "sinks") if row[1] == phonemic.SINK]
    for stream in _short(pactl, "sink-inputs"):
        if stream[1] not in mic:
            try:
                pactl("move-sink-input", stream[0], target)
            except RuntimeError:     # a stream that ended meanwhile
                pass
