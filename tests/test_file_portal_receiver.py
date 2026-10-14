from __future__ import annotations

import io
import json
import os
import signal
import runpy
import stat
import sys
import struct
import tempfile
import unittest
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIVER = ROOT / "client" / "diarchy-file-portal-receiver"


def encode(record: dict) -> bytes:
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode()
    return struct.pack("!I", len(payload)) + payload


def decode_all(raw: bytes) -> list[dict]:
    stream = io.BytesIO(raw)
    records = []
    while header := stream.read(4):
        length = struct.unpack("!I", header)[0]
        records.append(json.loads(stream.read(length)))
    return records


class FilePortalReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        module = runpy.run_path(str(RECEIVER))
        self.module = module
        self.process_records = module["process_records"]
        self.resolve_ssh_argv = module["_resolve_ssh_argv"]
        self.receiver_session = module["receiver_session"]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_ready_records_invoke_argument_safe_get_and_ack_each_result(self):
        records = (
            encode({"type": "ready", "id": "one", "name": "café report.txt", "kind": "file", "size": 3, "mtime": 1})
            + encode({"type": "ready", "id": "two", "name": "folder", "kind": "directory", "size": 4, "mtime": 2})
        )
        calls: list[list[str]] = []

        class Result:
            returncode = 0
            stdout = "get name=x bytes=3 route=lan status=ok\n"
            stderr = ""

        def runner(argv: list[str], **_kwargs: object) -> Result:
            calls.append(argv)
            return Result()

        output = io.BytesIO()
        result = self.process_records(io.BytesIO(records), output, ["/bin/diarchy-file"], runner)
        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [
                ["/bin/diarchy-file", "get", "--", "café report.txt"],
                ["/bin/diarchy-file", "get", "--", "folder"],
            ],
        )
        self.assertEqual(
            decode_all(output.getvalue()),
            [
                {"type": "ack", "id": "one", "status": "ok"},
                {"type": "ack", "id": "two", "status": "ok"},
            ],
        )

    def run_ready(self, size: int, free: int) -> tuple[int, list[list[str]], list[dict]]:
        record = encode({"type": "ready", "id": "one", "name": "item.bin", "kind": "file", "size": size, "mtime": 1})
        calls: list[list[str]] = []

        class Result:
            returncode = 0
            stdout = "get name=item.bin bytes=1 route=lan status=ok\n"
            stderr = ""

        def runner(argv: list[str], **_kwargs: object) -> Result:
            calls.append(argv)
            return Result()

        output = io.BytesIO()
        result = self.process_records(
            io.BytesIO(record), output, ["diarchy-file"], runner, free_bytes=lambda: free
        )
        return result, calls, decode_all(output.getvalue())

    def test_sec06_ready_record_above_size_cap_is_rejected_without_get(self):
        limit = self.module["MAX_ITEM_BYTES"]
        self.assertEqual(limit, 2 * 1024**3)
        result, calls, acks = self.run_ready(limit + 1, free=10**15)
        self.assertEqual(result, 1)
        self.assertEqual(calls, [])
        self.assertEqual(acks[0]["status"], "failed")
        self.assertIn("size limit", acks[0]["error"])

    def test_sec06_ready_record_at_size_cap_is_accepted(self):
        limit = self.module["MAX_ITEM_BYTES"]
        result, calls, acks = self.run_ready(limit, free=10**15)
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(acks[0]["status"], "ok")

    def test_sec06_ready_record_without_free_space_is_rejected_without_get(self):
        reserve = self.module["FREE_SPACE_RESERVE_BYTES"]
        result, calls, acks = self.run_ready(1000, free=999 + reserve)
        self.assertEqual(result, 1)
        self.assertEqual(calls, [])
        self.assertIn("free space", acks[0]["error"])
        result, calls, _acks = self.run_ready(1000, free=1000 + reserve)
        self.assertEqual((result, len(calls)), (0, 1))

    def test_failed_get_sends_failed_ack_with_bounded_error(self):
        record = encode({"type": "ready", "id": "bad", "name": "item.txt", "kind": "file", "size": 3, "mtime": 1})

        class Result:
            returncode = 1
            stdout = "get name=item.txt bytes=0 route=lan status=skipped-exists\n"
            stderr = "collision" * 1000

        output = io.BytesIO()
        result = self.process_records(io.BytesIO(record), output, ["diarchy-file"], lambda *_a, **_k: Result())
        ack = decode_all(output.getvalue())[0]
        self.assertEqual(result, 1)
        self.assertEqual(ack["status"], "failed")
        self.assertIn("skipped-exists", ack["error"])
        self.assertLessEqual(len(ack["error"]), 1024)

    def test_unknown_record_duplicate_id_and_invalid_name_terminate(self):
        valid = {"type": "ready", "id": "same", "name": "item", "kind": "file", "size": 1, "mtime": 1}

        class Result:
            returncode = 0
            stdout = "status=ok"
            stderr = ""

        cases = [
            encode({"type": "unknown"}),
            encode(valid) + encode(valid),
            encode({**valid, "name": "../escape"}),
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.process_records(io.BytesIO(raw), io.BytesIO(), ["diarchy-file"], lambda *_a, **_k: Result())


    def test_session_readiness_marker_exists_only_after_remote_ready(self):
        ready_file = self.root / "runtime" / "receiver.ready"
        records = encode({"type": "session", "status": "ready"}) + encode(
            {
                "type": "ready",
                "id": "one",
                "name": "item.txt",
                "kind": "file",
                "size": 3,
                "mtime": 1,
            }
        )

        class Result:
            returncode = 0
            stdout = "status=ok"
            stderr = ""

        def runner(*_args: object, **_kwargs: object) -> Result:
            self.assertTrue(ready_file.is_file())
            self.assertEqual(stat.S_IMODE(ready_file.stat().st_mode), 0o600)
            return Result()

        output = io.BytesIO()
        result = self.receiver_session(
            [],
            ["diarchy-file"],
            stdin=io.BytesIO(records),
            stdout=output,
            ready_file=ready_file,
            runner=runner,
        )

        self.assertEqual(result, 0)
        self.assertFalse(ready_file.exists())
        self.assertEqual(decode_all(output.getvalue()), [{"type": "ack", "id": "one", "status": "ok"}])

    def test_session_marker_is_never_created_without_ready_handshake(self):
        ready_file = self.root / "runtime" / "receiver.ready"
        with self.assertRaisesRegex(EOFError, "before session readiness"):
            self.receiver_session(
                [],
                ["diarchy-file"],
                stdin=io.BytesIO(),
                stdout=io.BytesIO(),
                ready_file=ready_file,
            )
        self.assertFalse(ready_file.exists())

    def test_route_uses_installed_remote_helper_path(self):
        route = self.root / "diarchy-route.sh"
        route.write_text(
            "select_diarchy_route() {\n"
            "  DIARCHY_SSH_ALIAS=diarchy-test\n"
            "  diarchy_ssh_options=(-o BatchMode=yes -p 2222)\n"
            "}\n"
        )
        original = os.environ.get("SSH_BIN")
        os.environ["SSH_BIN"] = "/usr/bin/ssh-test"
        try:
            argv = self.resolve_ssh_argv(route)
        finally:
            if original is None:
                os.environ.pop("SSH_BIN", None)
            else:
                os.environ["SSH_BIN"] = original
        self.assertEqual(
            argv,
            [
                "/usr/bin/ssh-test",
                "-o",
                "BatchMode=yes",
                "-p",
                "2222",
                "diarchy-test",
                "~/.local/bin/diarchy-file-portal-remote",
            ],
        )


    def test_stop_request_terminates_active_get_process_group(self):
        ready = self.root / "get-ready"
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
        fake_get = self.root / "fake-get"
        fake_get.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import os, signal, subprocess, sys, time
                from pathlib import Path
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                child = subprocess.Popen([
                    sys.executable,
                    {str(grandchild)!r},
                    {str(grandchild_ready)!r},
                    {str(grandchild_stopped)!r},
                ])
                while not Path({str(grandchild_ready)!r}).exists():
                    time.sleep(0.01)
                Path({str(ready)!r}).write_text(f"{{os.getpid()}} {{child.pid}}")
                time.sleep(30)
                """
            )
        )
        fake_get.chmod(0o755)
        records = io.BytesIO(
            encode(
                {
                    "type": "ready",
                    "id": "cancel",
                    "name": "item.txt",
                    "kind": "file",
                    "size": 1,
                    "mtime": 1,
                }
            )
        )
        acknowledgements = io.BytesIO()
        worker = threading.Thread(
            target=lambda: self.process_records(
                records,
                acknowledgements,
                [str(fake_get)],
            )
        )
        worker.start()
        deadline = time.monotonic() + 5
        while not ready.exists():
            if time.monotonic() > deadline:
                self.fail("timed out waiting for receiver get child")
            time.sleep(0.01)
        get_pid = int(ready.read_text().split()[0])

        def cleanup_process_group():
            try:
                os.killpg(get_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            worker.join(timeout=2)

        self.addCleanup(cleanup_process_group)

        self.module["_request_stop"]()
        worker.join(timeout=7)

        self.assertFalse(worker.is_alive())
        self.assertEqual(decode_all(acknowledgements.getvalue())[0]["status"], "failed")
        deadline = time.monotonic() + 5
        while not grandchild_stopped.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(grandchild_stopped.exists())

if __name__ == "__main__":
    unittest.main()
