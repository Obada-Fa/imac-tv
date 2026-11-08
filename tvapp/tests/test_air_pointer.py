"""The phone app's air pointer: /api/air reads chunked 5-byte records and hands them straight to XTest."""
import socket
import struct
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from test_torbox import TorboxCase, server


def record(kind, dx=0, dy=0):
    return struct.pack("<Bhh", kind, dx, dy)


class AirPointerTests(TorboxCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.pointer = mock.Mock()
        self.pointer.move.return_value = True
        self.pointer.click.return_value = True
        self.control = mock.Mock(pointer=self.pointer)
        for patch in (mock.patch.object(server, "CONTROL", self.control),
                      mock.patch.object(server.Handler, "_note_presence", lambda self: None)):
            patch.start()
            self.addCleanup(patch.stop)

    def stream(self, chunks, paired=True):
        with mock.patch.object(server.Handler, "authorised", return_value=paired):
            s = socket.create_connection(("127.0.0.1", self.httpd.server_port), timeout=5)
            s.sendall(b"POST /api/air HTTP/1.1\r\nHost: tv\r\nTransfer-Encoding: chunked\r\n\r\n")
            for data in chunks:
                s.sendall(b"%x\r\n" % len(data) + data + b"\r\n")
            s.sendall(b"0\r\n\r\n")
            answer = s.recv(200)
            s.close()
            time.sleep(0.05)
            return answer

    def test_records_move_and_click(self):
        # A record may be split across chunks: the handler keeps the remainder.
        moves = record(1) + record(0, 5, -3) + record(0, -300, 2)
        self.stream([moves[:7], moves[7:] + record(2) + record(3) + record(4)])
        self.control.air_begin.assert_called_once()
        self.pointer.move.assert_called_once_with([(5, -3), (-300, 2)])
        self.assertEqual(self.pointer.click.call_args_list, [mock.call(1), mock.call(3)])

    def test_unpaired_phone_is_refused(self):
        answer = self.stream([record(0, 9, 9)], paired=False)
        self.assertIn(b" 401 ", answer)
        self.pointer.move.assert_not_called()

    def test_moves_are_clamped(self):
        self.stream([record(0, 32000, -32000)])
        self.pointer.move.assert_called_once_with([(300, -300)])


if __name__ == "__main__":
    unittest.main()
