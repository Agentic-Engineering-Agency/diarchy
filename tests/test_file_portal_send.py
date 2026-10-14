from __future__ import annotations

import io
import os
import runpy
import signal
import stat
import sys
import tempfile
import unittest
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENDER = ROOT / "client" / "diarchy-file-portal-send"
INGEST = ROOT / "host" / "diarchy-file-portal-ingest"


class PortalSendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sender = runpy.run_path(str(SENDER))
        self.ingest = runpy.run_path(str(INGEST))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()
        self.destination.mkdir()

        def portable_rename(root_fd: int, source: str, destination: str) -> None:
            root_path = self.destination
            target = root_path / destination
            if os.path.lexists(target):
                raise FileExistsError(target)
            os.rename(root_path / source, target)

        self.ingest["receive_item"].__globals__["rename_noreplace"] = portable_rename

    def transfer(self, path: Path) -> tuple[str, str]:
        stream = io.BytesIO()
        self.sender["stream_path"](path, stream)
        stream.seek(0)
        return self.ingest["receive_item"](stream, self.destination)

    def test_file_and_directory_publish_privately_with_hash_and_mtime(self) -> None:
        regular = self.source / "café report.txt"
        regular.write_bytes(b"portal-file\n")
        os.utime(regular, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
        folder = self.source / "folder with spaces"
        nested = folder / "nested"
        nested.mkdir(parents=True)
        payload = nested / "payload.bin"
        payload.write_bytes(bytes(range(64)))

        self.assertEqual(self.transfer(regular), (regular.name, "ok"))
        self.assertEqual(self.transfer(folder), (folder.name, "ok"))

        received_file = self.destination / regular.name
        received_payload = self.destination / folder.name / "nested" / "payload.bin"
        self.assertEqual(received_file.read_bytes(), regular.read_bytes())
        self.assertEqual(received_payload.read_bytes(), payload.read_bytes())
        self.assertEqual(received_file.stat().st_mtime_ns, regular.stat().st_mtime_ns)
        self.assertEqual(stat.S_IMODE(received_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.destination / folder.name).stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(received_payload.stat().st_mode), 0o600)
        self.assertEqual(list(self.destination.glob(".diarchy-portal-stage-*")), [])

    def test_top_level_collision_never_modifies_existing_tree(self) -> None:
        source = self.source / "collision"
        (source / "new").mkdir(parents=True)
        (source / "new" / "item.txt").write_text("new")
        existing = self.destination / "collision"
        existing.mkdir()
        sentinel = existing / "sentinel.txt"
        sentinel.write_text("keep")

        self.assertEqual(self.transfer(source), ("collision", "skipped-exists"))
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertFalse((existing / "new").exists())
        self.assertEqual(list(self.destination.glob(".diarchy-portal-stage-*")), [])

    def test_nested_symlink_and_fifo_are_rejected_during_stream(self) -> None:
        folder = self.source / "unsafe"
        folder.mkdir()
        target = self.source / "target.txt"
        target.write_text("secret")
        (folder / "link").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "unsupported source item"):
            self.sender["stream_path"](folder, io.BytesIO())

        (folder / "link").unlink()
        fifo = folder / "pipe"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "unsupported source item"):
            self.sender["stream_path"](folder, io.BytesIO())

    def test_truncated_stream_cleans_hidden_stage(self) -> None:
        source = self.source / "partial.bin"
        source.write_bytes(b"abcdef")
        stream = io.BytesIO()
        self.sender["stream_path"](source, stream)
        truncated = io.BytesIO(stream.getvalue()[:-3])

        with self.assertRaises(EOFError):
            self.ingest["receive_item"](truncated, self.destination)
        self.assertFalse((self.destination / source.name).exists())
        self.assertEqual(list(self.destination.glob(".diarchy-portal-stage-*")), [])

    def test_installed_ingest_uses_fixed_drop_destination(self) -> None:
        source = INGEST.read_text()
        self.assertNotIn("DIARCHY_FROM_MAC_DIR", source)
        self.assertIn('Path.home() / "Drop" / "from-mac"', source)

    def test_destination_root_symlink_is_rejected(self) -> None:
        source = self.source / "item.txt"
        source.write_text("payload")
        stream = io.BytesIO()
        self.sender["stream_path"](source, stream)
        stream.seek(0)
        linked_destination = self.root / "linked-destination"
        linked_destination.symlink_to(self.destination)

        with self.assertRaisesRegex(ValueError, "unsafe portal destination root"):
            self.ingest["receive_item"](stream, linked_destination)

        self.assertFalse((self.destination / source.name).exists())

    def test_top_level_symlink_is_rejected_without_following(self) -> None:
        target = self.source / "real.txt"
        target.write_text("secret")
        link = self.source / "link.txt"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "unsupported source item"):
            self.sender["stream_path"](link, io.BytesIO())

    def test_stop_request_terminates_active_ssh_process_group(self) -> None:
        source = self.source / "cancel.txt"
        source.write_bytes(b"x" * (8 * 1024 * 1024))
        ready = self.root / "ssh-ready"
        grandchild_ready = self.root / "grandchild-ready"
        grandchild_stopped = self.root / "grandchild-stopped"
        grandchild = self.root / "grandchild.py"
        grandchild.write_text(
            textwrap.dedent(
                """\
                import os, signal, sys, time
                from pathlib import Path
                ready, stopped = map(Path, sys.argv[1:])
                def terminate(_signum, _frame):
                    stopped.write_text("stopped\\n")
                    raise SystemExit(0)
                signal.signal(signal.SIGTERM, terminate)
                ready.write_text(str(os.getpid()))
                while True:
                    time.sleep(1)
                """
            )
        )
        fake_ssh = self.root / "fake-ssh.py"
        fake_ssh.write_text(
            textwrap.dedent(
                """\
                import os, signal, subprocess, sys, time
                from pathlib import Path
                ready, child_ready, child_stopped, child_script = map(Path, sys.argv[1:])
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                child = subprocess.Popen([sys.executable, str(child_script), str(child_ready), str(child_stopped)])
                while not child_ready.exists():
                    time.sleep(0.01)
                ready.write_text(f"{os.getpid()} {child.pid}")
                while sys.stdin.buffer.read(65536):
                    time.sleep(0.1)
                time.sleep(30)
                """
            )
        )
        result: list[tuple[bool, str]] = []
        worker = threading.Thread(
            target=lambda: result.append(
                self.sender["send_one"](
                    source,
                    [
                        sys.executable,
                        str(fake_ssh),
                        str(ready),
                        str(grandchild_ready),
                        str(grandchild_stopped),
                        str(grandchild),
                    ],
                )
            )
        )
        worker.start()
        deadline = time.monotonic() + 5
        while not ready.exists():
            if time.monotonic() > deadline:
                self.fail("timed out waiting for sender SSH child")
            time.sleep(0.01)
        ssh_pid = int(ready.read_text().split()[0])

        def cleanup_process_group():
            try:
                os.killpg(ssh_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            worker.join(timeout=2)

        self.addCleanup(cleanup_process_group)

        self.sender["_request_stop"]()
        worker.join(timeout=7)

        self.assertFalse(worker.is_alive())
        self.assertTrue(result)
        self.assertFalse(result[0][0])
        deadline = time.monotonic() + 5
        while not grandchild_stopped.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(grandchild_stopped.exists())


if __name__ == "__main__":
    unittest.main()
