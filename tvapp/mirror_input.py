"""Remux timestamped Android H.264 and PCM packets to a live Matroska pipe."""
import struct
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

HEADER = struct.Struct(">BIQ")
MAX_PACKET = 2 * 1024**2


def main():
    Gst.init(None)
    audio = sys.argv[1] == "audio"
    pipeline = Gst.parse_launch(
        "matroskamux name=mux streamable=true min-cluster-duration=0 ! fdsink fd=1 sync=false "
        "appsrc name=video format=time is-live=true block=true "
        "caps=video/x-h264,stream-format=byte-stream,alignment=au ! h264parse ! queue ! mux.video_0 "
        + ("appsrc name=sound format=time is-live=true block=true "
           "caps=audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=2 ! queue ! mux.audio_0"
           if audio else "")
    )
    sources = [pipeline.get_by_name("video"), pipeline.get_by_name("sound")]
    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    base = None
    config = b""
    try:
        while header := sys.stdin.buffer.read(HEADER.size):
            error = bus.pop_filtered(Gst.MessageType.ERROR)
            if error:
                raise RuntimeError(error.parse_error()[0])
            kind, length, timestamp = HEADER.unpack(header)
            if kind not in (0, 1, 2, 3) or not 0 < length <= MAX_PACKET:
                raise ValueError("Invalid mirror packet")
            data = sys.stdin.buffer.read(length)
            if len(data) != length:
                raise ValueError("Incomplete mirror packet")
            if kind == 3:
                continue
            if kind == 2:
                config = data
                continue
            if not sources[kind]:
                raise ValueError("Unexpected audio packet")
            if base is None:
                base = timestamp
            if kind == 0 and config:
                data = config + data
                config = b""
            buffer = Gst.Buffer.new_wrapped(data)
            buffer.pts = max(0, timestamp - base) * 1000
            if kind == 1:
                buffer.duration = len(data) * Gst.SECOND // (4 * 48000)
            if sources[kind].emit("push-buffer", buffer) != Gst.FlowReturn.OK:
                break
    finally:
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    main()
