"""Security and privacy guards for the host product files (SEC-03, SEC-21, SEC-28, SEC-35)."""
from __future__ import annotations

import io
import json
import os
import re
import runpy
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = ROOT / "host"

_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(/\d{1,2})?(?![\d.])")
# Documentation values (RFC 5737 + the two tailnet examples the docs use). CIDR constants are always allowed.
_DOCUMENTATION = {"100.64.0.10", "100.64.0.20"}  # tailnet examples used by the docs


def ip(text: str) -> str:
    """Build a synthetic address at runtime so this file holds no private address literal itself."""
    return ".".join(text.split("/"))


def private_literals(text: str) -> list[str]:
    """RFC1918 and 100.64.0.0/10 address literals in `text`, minus documentation values and CIDR constants."""
    found = []
    for match in _IPV4.finditer(text):
        octets = [int(part) for part in match.groups()[:4]]
        literal = ".".join(str(octet) for octet in octets)
        if match.group(5) or literal in _DOCUMENTATION or any(octet > 255 for octet in octets):
            continue
        first, second = octets[0], octets[1]
        if first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168) \
                or (first == 100 and 64 <= second <= 127):
            found.append(literal)
    return found


def product_files() -> list[Path]:
    return [
        path for path in sorted(HOST.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]


class PrivateAddressGuardTests(unittest.TestCase):
    def test_host_product_files_carry_no_private_address_literal(self) -> None:
        leaks = {
            str(path.relative_to(ROOT)): private_literals(path.read_text(errors="replace"))
            for path in product_files()
        }
        self.assertEqual({name: found for name, found in leaks.items() if found}, {})

    def test_the_scan_flags_a_private_address_added_to_a_product_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "diarchy-sample"
            sample.write_text(f'HOST="{ip("10/9/8/7")}"\n')
            self.assertEqual(private_literals(sample.read_text()), [ip("10/9/8/7")])

    def test_the_scan_covers_every_private_range_and_spares_documentation_values(self) -> None:
        private = [ip(v) for v in ("172/16/0/1", "172/31/255/1", "192/168/1/5", "100/64/1/1", "100/127/0/9")]
        public = [ip(v) for v in ("100/128/0/1", "172/32/0/1")]
        self.assertEqual(private_literals(" ".join(private + public)), private)
        spared = [ip(v) for v in ("192/0/2/10", "198/51/100/7", "100/64/0/10", "100/64/0/20")]
        spared += [ip("100/64/0/0") + "/10", ip("192/168/0/0") + "/16", "127.0.0.1"]
        self.assertEqual(private_literals(" ".join(spared)), [])



class SunshineConfTests(unittest.TestCase):
    def test_lan_streams_must_be_encrypted(self) -> None:
        conf = (HOST / "sunshine.conf").read_text()
        self.assertRegex(conf, r"(?m)^lan_encryption_mode\s*=\s*2\s*$")
        self.assertNotRegex(conf, r"(?m)^lan_encryption_mode\s*=\s*[01]\s*$")


class SystemdHardeningTests(unittest.TestCase):
    OWNED = ("diarchy-file-portal.service", "diarchy-kvm.service", "diarchy-sunshine.service")

    def test_units_cannot_gain_privileges(self) -> None:
        for name in self.OWNED:
            with self.subTest(unit=name):
                self.assertRegex((HOST / "systemd" / name).read_text(), r"(?m)^NoNewPrivileges=yes$")


def frame(record: dict) -> bytes:
    payload = json.dumps(record).encode()
    return struct.pack("!I", len(payload)) + payload


def portal_stream(name: str, body: bytes) -> bytes:
    return (
        frame({"type": "start", "name": name, "kind": "file"})
        + frame({"type": "entry", "path": "", "kind": "file", "size": len(body), "mtime_ns": 0})
        + body
        + frame({"type": "finish", "entries": 1})
    )


class IngestDiskSafetyTests(unittest.TestCase):
    """SEC-26: a drop cannot fill the disk, and an abandoned stage directory does not linger."""

    def setUp(self) -> None:
        self.ingest = runpy.run_path(str(HOST / "diarchy-file-portal-ingest"))
        self.receive_item = self.ingest["receive_item"]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "from-mac"

    def set_free(self, free: int) -> None:
        def portable_rename(root_fd: int, source: str, destination: str) -> None:  # renameat2 is Linux-only
            os.rename(self.root / source, self.root / destination)

        self.receive_item.__globals__["rename_noreplace"] = portable_rename
        self.receive_item.__globals__["free_bytes"] = lambda fd: free

    def test_a_file_larger_than_the_free_space_is_refused_and_leaves_nothing(self) -> None:
        reserve = self.ingest["FREE_SPACE_RESERVE"]
        self.set_free(reserve + 10)
        with self.assertRaises(OSError) as raised:
            self.receive_item(io.BytesIO(portal_stream("big.bin", b"x" * 11)), self.root)
        self.assertEqual(raised.exception.errno, 28)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_file_that_fits_the_free_space_is_published(self) -> None:
        reserve = self.ingest["FREE_SPACE_RESERVE"]
        self.set_free(reserve + 10)
        self.assertEqual(self.receive_item(io.BytesIO(portal_stream("ok.bin", b"x" * 10)), self.root), ("ok.bin", "ok"))
        self.assertEqual((self.root / "ok.bin").read_bytes(), b"x" * 10)

    def test_only_abandoned_stage_directories_are_swept_at_start(self) -> None:
        self.root.mkdir(mode=0o700)
        old = self.root / ".diarchy-portal-stage-old"
        fresh = self.root / ".diarchy-portal-stage-fresh"
        keep = self.root / "report.pdf"
        for path in (old, fresh):
            path.mkdir()
            (path / "partial").write_bytes(b"x")
        keep.write_bytes(b"keep")
        long_ago = old.stat().st_mtime - 2 * self.ingest["STALE_STAGE_SECONDS"]
        os.utime(old, (long_ago, long_ago))
        self.set_free(1 << 40)
        self.receive_item(io.BytesIO(portal_stream("new.bin", b"y")), self.root)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual(keep.read_bytes(), b"keep")


class ClipboardRemoteTests(unittest.TestCase):
    """SEC-27: the host clipboard read is capped and the logs carry no payload digest."""

    def setUp(self) -> None:
        self.remote = runpy.run_path(str(HOST / "diarchy-clipboard-remote"))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def fake_wl_paste(self, body: str) -> None:
        fake = Path(self.temp.name) / "wl-paste"
        fake.write_text(f"#!{sys.executable}\n{body}")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.temp.name}{os.pathsep}{old_path}"
        self.addCleanup(os.environ.__setitem__, "PATH", old_path)

    def test_a_10_mb_selection_is_read_only_up_to_one_byte_past_the_cap(self) -> None:
        self.fake_wl_paste(
            "import sys\n"
            "chunk = b'a' * 65536\n"
            "try:\n"
            "    for _ in range(160):\n"
            "        sys.stdout.buffer.write(chunk)\n"
            "        sys.stdout.buffer.flush()\n"
            "except BrokenPipeError:\n"
            "    pass\n"
        )
        cap = self.remote["MAX_BYTES"]
        payload = self.remote["read_host_clipboard_raw"]()
        self.assertEqual(len(payload), cap + 1)
        self.assertIsNone(self.remote["read_host_clipboard"]())
        self.assertIsNone(self.remote["frame_for_local_change"](payload, None))

    def test_a_small_selection_and_a_failing_reader_keep_their_behaviour(self) -> None:
        self.fake_wl_paste("import sys\nsys.stdout.write('hello')\n")
        self.assertEqual(self.remote["read_host_clipboard_raw"](), b"hello")
        self.fake_wl_paste("import sys\nsys.stdout.write('hello')\nsys.exit(1)\n")
        self.assertIsNone(self.remote["read_host_clipboard_raw"]())

    def test_log_lines_carry_no_digest_of_the_payload(self) -> None:
        line = self.remote["diagnostic_message"]("sent", "remote", b"hunter2")
        self.assertEqual(line, "sent direction=remote bytes=7")
        self.assertNotIn("sha256", line)


class SpacedPathTests(unittest.TestCase):
    """SEC-29: a HOME with a space must not split a path into two words."""

    def test_the_kvm_doctor_runs_binaries_whose_path_contains_a_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "a b"
            (home / ".local/bin").mkdir(parents=True)
            fake = home / ".local/bin/lan-mouse"
            fake.write_text("#!/bin/sh\necho 'lan-mouse 0.11.0'\n")
            fake.chmod(0o755)
            result = subprocess.run(
                ["bash", str(HOST / "diarchy-kvm-host-doctor")],
                env={**os.environ, "HOME": str(home), "LAN_MOUSE_BIN": str(fake)},
                capture_output=True, text=True, timeout=30,
            )
        self.assertIn("[ok] lan-mouse 0.11.0", result.stdout)
        self.assertNotIn("cannot report its version", result.stderr)


if __name__ == "__main__":
    unittest.main()
