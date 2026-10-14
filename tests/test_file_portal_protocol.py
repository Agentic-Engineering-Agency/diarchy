from __future__ import annotations

import io
import os
import runpy
import socket
import stat
import struct
import subprocess
import tempfile
import unittest
import threading
from unittest import mock
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = ROOT / "host" / "diarchy-file-portal-remote"


class FilePortalProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = runpy.run_path(str(REMOTE))
        self.encode_record = self.module["encode_record"]
        self.decode_record = self.module["decode_record"]
        self.validate_drop_path = self.module["validate_drop_path"]
        self.stage_path = self.module["stage_path"]
        self.publish_no_replace = self.module["_publish_no_replace"]
        self.complete_staged = self.module["complete_staged"]
        self.PortalRemote = self.module["PortalRemote"]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_record_round_trip_and_split_reads(self):
        payload = self.encode_record({"type": "ready", "id": "abc", "name": "ñ.txt"})

        class SplitReader(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                return super().read(1 if size < 0 else min(size, 1))

        self.assertEqual(
            self.decode_record(SplitReader(payload), {"ready"}),
            {"type": "ready", "id": "abc", "name": "ñ.txt"},
        )

    def test_decode_rejects_invalid_lengths_json_and_type(self):
        with self.assertRaisesRegex(ValueError, "record length"):
            self.decode_record(io.BytesIO(struct.pack("!I", 0)), {"ready"})
        with self.assertRaisesRegex(ValueError, "record length"):
            self.decode_record(io.BytesIO(struct.pack("!I", 65537)), {"ready"})
        with self.assertRaisesRegex(ValueError, "JSON"):
            self.decode_record(io.BytesIO(struct.pack("!I", 1) + b"{"), {"ready"})
        wrong = self.encode_record({"type": "ack", "id": "abc", "status": "ok"})
        with self.assertRaisesRegex(ValueError, "record type"):
            self.decode_record(io.BytesIO(wrong), {"ready"})

    def test_decode_returns_none_only_for_clean_eof(self):
        self.assertIsNone(self.decode_record(io.BytesIO(), {"ready"}))
        with self.assertRaisesRegex(EOFError, "truncated"):
            self.decode_record(io.BytesIO(b"\x00\x00"), {"ready"})

    def test_validate_accepts_regular_file_directory_and_unicode(self):
        regular = self.root / "café.txt"
        regular.write_text("payload")
        directory = self.root / "folder"
        directory.mkdir()
        self.assertEqual(self.validate_drop_path(regular), ("café.txt", "file"))
        self.assertEqual(self.validate_drop_path(directory), ("folder", "directory"))

    def test_validate_rejects_relative_missing_symlink_and_special_file(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.validate_drop_path(Path("relative.txt"))
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.validate_drop_path(self.root / "missing")
        target = self.root / "target"
        target.write_text("x")
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.validate_drop_path(link)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular file or directory"):
            self.validate_drop_path(fifo)

    def test_stage_file_is_private_atomic_and_never_overwrites(self):
        source = self.root / "report.txt"
        source.write_text("first")
        source_mtime = 1_700_000_000_123_456_789
        os.utime(source, ns=(source_mtime, source_mtime))
        outbox = self.root / "outbox"
        staged = self.stage_path(source, outbox, "request-1")
        self.assertEqual(staged.name, "report.txt")
        self.assertEqual(staged.read_text(), "first")
        self.assertEqual(staged.stat().st_mtime_ns, source_mtime)
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o600)
        self.assertFalse(any(path.name.startswith(".portal-stage-") for path in outbox.iterdir()))
        source.write_text("second")
        with self.assertRaises(FileExistsError):
            self.stage_path(source, outbox, "request-2")
        self.assertEqual(staged.read_text(), "first")

    def test_directory_publish_fails_closed_without_atomic_no_replace(self):
        staged = self.root / "staged-directory"
        staged.mkdir()
        (staged / "payload.txt").write_text("payload")
        destination = self.root / "published-directory"
        ctypes_module = self.publish_no_replace.__globals__["ctypes"]

        with mock.patch.object(ctypes_module, "CDLL", return_value=object()):
            with self.assertRaisesRegex(OSError, "atomic no-replace"):
                self.publish_no_replace(staged, destination)

        self.assertTrue(staged.is_dir())
        self.assertFalse(destination.exists())

    def test_stage_directory_normalizes_modes_and_rejects_symlink_tree(self):
        source = self.root / "tree"
        source.mkdir(mode=0o755)
        nested = source / "nested"
        nested.mkdir(mode=0o755)
        child = nested / "data.txt"
        child.write_text("data")
        child.chmod(0o644)
        outbox = self.root / "outbox"
        staged = self.stage_path(source, outbox, "request-tree")
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((staged / "nested").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((staged / "nested" / "data.txt").stat().st_mode), 0o600)
        bad = self.root / "bad-tree"
        bad.mkdir()
        (bad / "link").symlink_to(child)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.stage_path(bad, outbox, "request-bad")

        fifo = self.root / "fifo-tree"
        fifo.mkdir()
        os.mkfifo(fifo / "pipe")
        with self.assertRaisesRegex(ValueError, "special file"):
            self.stage_path(fifo, outbox, "request-fifo")

    def test_stage_rejects_symlink_outbox(self):
        source = self.root / "source.txt"
        source.write_text("payload")
        real_outbox = self.root / "real-outbox"
        real_outbox.mkdir()
        linked_outbox = self.root / "linked-outbox"
        linked_outbox.symlink_to(real_outbox)

        with self.assertRaisesRegex(ValueError, "unsafe portal outbox"):
            self.stage_path(source, linked_outbox, "request-link")

    def test_stage_request_bounds_items_errors_and_response_size(self):
        remote = self.PortalRemote(self.root / "portal.sock", self.root / "outbox")
        with self.assertRaisesRegex(ValueError, "1-32"):
            remote._stage_request({"type": "stage", "paths": ["/missing"] * 33})

        response = remote._stage_request(
            {
                "type": "stage",
                "paths": [f"/missing/{'x' * 400}-{index}" for index in range(32)],
            }
        )

        self.assertEqual(len(response["errors"]), 32)
        self.assertLessEqual(max(len(error["path"]) for error in response["errors"]), 255)
        self.assertLessEqual(max(len(error["error"]) for error in response["errors"]), 512)
        self.assertLessEqual(len(self.encode_record(response)) - 4, 65_536)

    def test_success_ack_removes_staged_copy_and_failure_retains_it(self):
        staged = self.root / "outbox" / "item.txt"
        staged.parent.mkdir()
        staged.write_text("payload")
        self.complete_staged(staged, "ok")
        self.assertFalse(staged.exists())
        staged.write_text("payload")
        self.complete_staged(staged, "failed")
        self.assertTrue(staged.exists())

    def test_disconnect_join_waits_for_staging_handler_cleanup(self):
        remote = self.PortalRemote(self.root / "portal.sock", self.root / "outbox")
        connection, peer = socket.socketpair()
        self.addCleanup(peer.close)
        started = threading.Event()
        release = threading.Event()
        partial = self.root / "outbox" / ".portal-stage-slow"

        def slow_handler(active_connection: socket.socket) -> None:
            partial.parent.mkdir()
            partial.write_text("partial")
            started.set()
            release.wait(2)
            partial.unlink(missing_ok=True)
            active_connection.close()

        remote._handle_client = slow_handler
        remote._start_handler(connection)
        self.assertTrue(started.wait(1))
        joiner = threading.Thread(target=remote.join_handlers)
        joiner.start()
        time.sleep(0.05)
        self.assertTrue(joiner.is_alive())

        release.set()
        joiner.join(2)

        self.assertFalse(joiner.is_alive())
        self.assertFalse(partial.exists())
        self.assertEqual(remote.handlers, set())

    def test_disconnect_closes_idle_handler_before_join(self):
        remote = self.PortalRemote(self.root / "portal.sock", self.root / "outbox")
        connection, peer = socket.socketpair()
        self.addCleanup(peer.close)
        remote._start_handler(connection)

        deadline = time.monotonic() + 1
        while not remote.handlers and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(remote.handlers)

        shutdown = threading.Thread(target=remote.stop_handlers)
        shutdown.start()
        shutdown.join(2)

        self.assertFalse(shutdown.is_alive())
        self.assertEqual(remote.handlers, set())

    def test_disconnect_reports_retained_staged_items(self):
        remote = self.PortalRemote(self.root / "portal.sock", self.root / "outbox")
        remote.pending = {
            "one": self.root / "report.txt",
            "two": self.root / "photos",
        }
        notifications: list[tuple[str, str]] = []
        original = remote.notify_pending_disconnect.__func__.__globals__["_notify"]
        remote.notify_pending_disconnect.__func__.__globals__["_notify"] = (
            lambda summary, detail: notifications.append((summary, detail))
        )
        self.addCleanup(
            lambda: remote.notify_pending_disconnect.__func__.__globals__.__setitem__(
                "_notify", original
            )
        )

        remote.notify_pending_disconnect()

        self.assertEqual(
            notifications,
            [
                (
                    "Mac receiver disconnected",
                    "Retained 2 staged item(s): report.txt, photos",
                )
            ],
        )

    def test_helper_socket_stages_emits_ready_and_removes_after_ack(self):
        home = self.root / "home"
        runtime = self.root / "runtime"
        source = home / "Desktop" / "portal.txt"
        source.parent.mkdir(parents=True)
        source.write_text("portal payload")
        runtime.mkdir(mode=0o700)
        process = subprocess.Popen(
            [str(REMOTE)],
            env=os.environ | {"HOME": str(home), "XDG_RUNTIME_DIR": str(runtime)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        socket_path = runtime / "diarchy-file-portal.sock"
        deadline = time.monotonic() + 3
        while not socket_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(socket_path.is_socket(), process.stderr.read().decode() if process.poll() is not None else "")
        assert process.stdout is not None and process.stdin is not None
        session = self.decode_record(process.stdout, {"session", "error"})
        self.assertEqual(session, {"type": "session", "status": "ready"})
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            stream = connection.makefile("rwb", buffering=0)
            stream.write(self.encode_record({"type": "stage", "paths": [str(source)]}))
            response = self.decode_record(stream, {"queued", "error"})
        self.assertEqual(response["type"], "queued")
        ready = self.decode_record(process.stdout, {"ready", "error"})
        self.assertEqual(ready["type"], "ready")
        staged = home / "Drop" / "to-mac" / "portal.txt"
        self.assertEqual(staged.read_text(), "portal payload")
        process.stdin.write(
            self.encode_record({"type": "ack", "id": ready["id"], "status": "ok"})
        )
        process.stdin.flush()
        deadline = time.monotonic() + 3
        while staged.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(staged.exists())
        process.stdin.close()
        self.assertEqual(process.wait(timeout=3), 0)
        process.stdout.close()
        assert process.stderr is not None
        process.stderr.close()
        self.assertFalse(socket_path.exists())


if __name__ == "__main__":
    unittest.main()
