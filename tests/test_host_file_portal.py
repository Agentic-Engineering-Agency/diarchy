from __future__ import annotations

import json
import os
import runpy
import stat
import subprocess
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "host" / "diarchy-file-portal"
REMOTE = ROOT / "host" / "diarchy-file-portal-remote"


def encode(record: dict) -> bytes:
    payload = json.dumps(record, separators=(",", ":")).encode()
    return struct.pack("!I", len(payload)) + payload


def receive(connection: socket.socket) -> dict:
    header = connection.recv(4)
    length = struct.unpack("!I", header)[0]
    body = bytearray()
    while len(body) < length:
        body.extend(connection.recv(length - len(body)))
    return json.loads(body)


class HostFilePortalTests(unittest.TestCase):
    def setUp(self) -> None:
        module = runpy.run_path(str(PORTAL))
        self.parse_uri_list = module["parse_uri_list"]
        self.send_stage_request = module["send_stage_request"]
        self.summarize_stage_response = module["summarize_stage_response"]
        self.portal_visual_mode = module["portal_visual_mode"]
        self.portal_accepts_drop = module["portal_accepts_drop"]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_gui_uses_hidden_edge_trigger_and_delayed_reveal_state(self):
        source = PORTAL.read_text()
        self.assertIn("TRIGGER_WIDTH = 3", source)
        self.assertIn("REVEALED_WIDTH = 52", source)
        self.assertIn("EXPANDED_WIDTH = 72", source)
        self.assertIn("HIDE_DELAY_MS = 500", source)
        self.assertIn('self.connect("enter-notify-event"', source)
        self.assertIn('self.connect("leave-notify-event"', source)
        self.assertIn("GLib.timeout_add(HIDE_DELAY_MS", source)
        self.assertIn("self.set_size_request(TRIGGER_WIDTH, PORTAL_HEIGHT)", source)
        self.assertIn("self.label.hide()", source)

    def test_portal_state_blocks_overlapping_drops_and_resets_when_unavailable(self):
        self.assertFalse(self.portal_accepts_drop(sending=True, available=True))
        self.assertFalse(self.portal_accepts_drop(sending=False, available=False))
        self.assertTrue(self.portal_accepts_drop(sending=False, available=True))
        self.assertEqual(
            self.portal_visual_mode(
                pointer_inside=True,
                drag_active=True,
                sending=True,
                available=False,
            ),
            "unavailable",
        )
        self.assertEqual(
            self.portal_visual_mode(
                pointer_inside=False,
                drag_active=False,
                sending=True,
                available=True,
            ),
            "revealed",
        )
        self.assertEqual(
            self.portal_visual_mode(
                pointer_inside=False,
                drag_active=True,
                sending=False,
                available=True,
            ),
            "drag",
        )

    def test_parse_uri_list_decodes_multiple_paths_comments_and_crlf(self):
        raw = (
            b"# GTK drag payload\r\n"
            b"file:///home/user/R%C3%A9sum%C3%A9%202026.txt\r\n"
            b"file://localhost/tmp/folder%20one\n\n"
        )
        self.assertEqual(
            self.parse_uri_list(raw),
            [Path("/home/user/Résumé 2026.txt"), Path("/tmp/folder one")],
        )

    def test_parse_uri_list_rejects_non_file_and_remote_file_uris(self):
        with self.assertRaisesRegex(ValueError, "file URI"):
            self.parse_uri_list(b"https://example.com/file.txt\n")
        with self.assertRaisesRegex(ValueError, "remote file URI"):
            self.parse_uri_list(b"file://other-host/tmp/file.txt\n")
        with self.assertRaisesRegex(ValueError, "no file URLs"):
            self.parse_uri_list(b"# only a comment\n")

    def test_send_stage_request_uses_exact_shape_and_returns_response(self):
        socket_path = self.root / "portal.sock"
        observed: list[dict] = []
        ready = threading.Event()

        def server() -> None:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                listener.listen(1)
                ready.set()
                connection, _ = listener.accept()
                with connection:
                    observed.append(receive(connection))
                    connection.sendall(encode({"type": "queued", "items": [{"id": "1"}], "errors": []}))

        thread = threading.Thread(target=server)
        thread.start()
        ready.wait(2)
        response = self.send_stage_request(
            [Path("/tmp/one file"), Path("/tmp/two")], socket_path
        )
        thread.join(2)
        self.assertEqual(
            observed,
            [{"type": "stage", "paths": ["/tmp/one file", "/tmp/two"]}],
        )
        self.assertEqual(response["type"], "queued")

    def test_send_stage_request_reports_missing_socket_and_helper_error(self):
        socket_path = self.root / "missing.sock"
        with self.assertRaisesRegex(ConnectionError, "Mac receiver unavailable"):
            self.send_stage_request([Path("/tmp/item")], socket_path)

        ready = threading.Event()

        def server() -> None:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                listener.listen(1)
                ready.set()
                connection, _ = listener.accept()
                with connection:
                    receive(connection)
                    connection.sendall(encode({"type": "error", "error": "bad drop"}))

        thread = threading.Thread(target=server)
        thread.start()
        ready.wait(2)
        with self.assertRaisesRegex(RuntimeError, "bad drop"):
            self.send_stage_request([Path("/tmp/item")], socket_path)
        thread.join(2)

    def test_send_stage_request_bounds_drop_count(self):
        with self.assertRaisesRegex(ValueError, "1-32"):
            self.send_stage_request([], self.root / "unused.sock")
        with self.assertRaisesRegex(ValueError, "1-32"):
            self.send_stage_request([Path(f"/tmp/{index}") for index in range(33)], self.root / "unused.sock")

    def test_stage_summary_names_queued_and_rejected_items(self):
        summary, detail, error = self.summarize_stage_response(
            {
                "type": "queued",
                "items": [{"name": "report.txt"}, {"name": "photos"}],
                "errors": [{"path": "existing.txt", "error": "destination exists"}],
            }
        )
        self.assertEqual(summary, "Transfer partly queued")
        self.assertEqual(
            detail,
            "Queued report.txt, photos. Rejected existing.txt: destination exists. 2 queued; 1 rejected",
        )
        self.assertTrue(error)
        summary, detail, error = self.summarize_stage_response(
            {
                "type": "queued",
                "items": [],
                "errors": [
                    {"path": "one", "error": "first"},
                    {"path": "two", "error": "second"},
                ],
            }
        )
        self.assertEqual(summary, "Transfer rejected")
        self.assertEqual(detail, "Rejected one: first (+1 more). 0 queued; 2 rejected")
        self.assertTrue(error)

        self.assertEqual(
            self.summarize_stage_response(
                {"type": "queued", "items": [{"name": "report.txt"}], "errors": []}
            ),
            ("Sending to Mac", "Queued report.txt", False),
        )


class NotifySendTests(unittest.TestCase):
    """SEC-19: user-controlled text never becomes a notify-send option or markup."""

    HOSTILE_SUMMARY = "-u critical"
    HOSTILE_DETAIL = "<a href='https://example.com'>x</a> & \x1b]0;t\x07line\nbreak"

    def run_notify(self, script: Path, function: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "argv"
            fake = Path(tmp) / "notify-send"
            fake.write_text(
                "#!/usr/bin/env python3\nimport sys\n"
                f"open({str(log)!r}, 'w').write(chr(0).join(sys.argv[1:]))\n"
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            old_path = os.environ["PATH"]
            os.environ["PATH"] = f"{tmp}{os.pathsep}{old_path}"
            try:
                runpy.run_path(str(script))[function](self.HOSTILE_SUMMARY, self.HOSTILE_DETAIL)
            finally:
                os.environ["PATH"] = old_path
            return log.read_text().split("\0")

    def check(self, argv: list[str]) -> None:
        self.assertEqual(argv[0], "--")
        self.assertEqual(argv[1], "Diarchy file portal")
        self.assertEqual(argv[2], "-u critical")
        body = argv[3]
        self.assertNotIn("<a", body)
        self.assertIn("&lt;a href=", body)
        self.assertIn("&amp;", body)
        self.assertTrue(all(ch.isprintable() for ch in body), body)

    def test_the_remote_notification_ends_options_and_escapes_text(self) -> None:
        self.check(self.run_notify(REMOTE, "_notify"))

    def test_the_gui_notification_ends_options_and_escapes_text(self) -> None:
        self.check(self.run_notify(PORTAL, "notify"))


if __name__ == "__main__":
    unittest.main()
