from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_KEY = ROOT / "client" / "diarchy-host-key"
LAN_HOST = "192.0.2.10"
ALIAS = "lab-host-key"

FAKE_PING = """#!/usr/bin/env python3
import sys
print('64 bytes from x: icmp_seq=1 ttl=64 time=3.0 ms')
print('1 packets transmitted, 1 packets received, 0% packet loss')
"""
FAKE_ROUTE = """#!/usr/bin/env python3
print('  interface: en0')
"""
FAKE_NC = """#!/usr/bin/env python3
raise SystemExit(0)
"""
# Prints the key from KEYSCAN_PUB with the scanned host in field 1, like ssh-keyscan.
FAKE_KEYSCAN = """#!/usr/bin/env python3
import os, sys
with open(os.environ['KEYSCAN_ARGV'], 'w') as out:
    out.write('\\0'.join(sys.argv[1:]))
if os.environ.get('KEYSCAN_FAIL'):
    sys.stderr.write('connection refused\\n')
    raise SystemExit(1)
host = sys.argv[-1]
key = open(os.environ['KEYSCAN_PUB']).read().split()
print(f'# {host}:22 SSH-2.0-OpenSSH')
print(f'{host} {key[0]} {key[1]}')
"""


def make_key(directory: Path, name: str) -> tuple[Path, str]:
    path = directory / name
    subprocess.run(
        ["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)],
        check=True,
    )
    fingerprint = subprocess.run(
        ["/usr/bin/ssh-keygen", "-lf", f"{path}.pub", "-E", "sha256"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[1]
    return Path(f"{path}.pub"), fingerprint


class HostKeyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.temp = Path(self._tmp.name)
        self.conf_dir = self.temp / "config dir"
        self.conf_dir.mkdir()
        self.config = self.conf_dir / "diarchy.conf"
        self.config.write_text(
            "# Managed by Diarchy\n"
            "DIARCHY_SSH_USER=labuser\n"
            f"DIARCHY_LAN_HOST={LAN_HOST}\n"
            f"DIARCHY_HOST_KEY_ALIAS={ALIAS}\n"
            "DIARCHY_SSH_PORT=2222\n"
        )
        self.known_hosts = self.conf_dir / "known_hosts"
        self.pending = self.conf_dir / "known_hosts.pending"
        for name, body in (("ping", FAKE_PING), ("route", FAKE_ROUTE), ("nc", FAKE_NC), ("keyscan", FAKE_KEYSCAN)):
            path = self.temp / name
            path.write_text(body)
            path.chmod(0o755)
        self.pub_a, self.fp_a = make_key(self.temp, "key_a")
        self.pub_b, self.fp_b = make_key(self.temp, "key_b")
        self.keyscan_argv = self.temp / "keyscan_argv"

    def run_host_key(self, *args: str, pub: Path | None = None, fail_scan: bool = False, reachable: bool = True):
        env = {k: v for k, v in os.environ.items() if not k.startswith("DIARCHY_")}
        env.update(
            {
                "HOME": str(self.temp),
                "TMPDIR": str(self.temp),
                "DIARCHY_CONFIG": str(self.config),
                "PING_BIN": str(self.temp / "ping"),
                "ROUTE_BIN": str(self.temp / "route"),
                "CONNECT_BIN": str(self.temp / ("nc" if reachable else "false")),
                "SSH_KEYSCAN_BIN": str(self.temp / "keyscan"),
                "SSH_KEYGEN_BIN": "/usr/bin/ssh-keygen",
                "KEYSCAN_PUB": str(pub or self.pub_a),
                "KEYSCAN_ARGV": str(self.keyscan_argv),
            }
        )
        if fail_scan:
            env["KEYSCAN_FAIL"] = "1"
        completed = subprocess.run([str(HOST_KEY), *args], env=env, capture_output=True, text=True, check=False)
        values = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
        return completed, values

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_scan_writes_pending_key_under_alias_and_prints_fingerprint(self):
        result, values = self.run_host_key("scan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values, {"status": "scanned", "fingerprint": self.fp_a})
        self.assertEqual(self.pending.read_text().split()[0], ALIAS)
        self.assertEqual(self.mode(self.pending), 0o600)
        self.assertFalse(self.known_hosts.exists())
        argv = self.keyscan_argv.read_text().split("\0")
        self.assertEqual(argv, ["-T", "5", "-t", "ed25519", "-p", "2222", LAN_HOST])

    def test_scan_failure_reports_reason_and_writes_nothing(self):
        result, values = self.run_host_key("scan", fail_scan=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(values["status"], "failed")
        self.assertTrue(values["reason"])
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.known_hosts.exists())

    def test_scan_fails_when_host_is_unreachable(self):
        result, values = self.run_host_key("scan", reachable=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(values["status"], "failed")
        self.assertFalse(self.keyscan_argv.exists())

    def test_trust_with_wrong_fingerprint_fails_and_keeps_pinned_file_unwritten(self):
        self.run_host_key("scan")
        result, values = self.run_host_key("trust", self.fp_b)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(values["status"], "failed")
        self.assertFalse(self.known_hosts.exists())
        self.assertTrue(self.pending.exists())

    def test_trust_with_wrong_fingerprint_keeps_existing_pinned_entry(self):
        self.known_hosts.write_text(f"{ALIAS} ssh-ed25519 AAAAexisting\n")
        before = self.known_hosts.read_text()
        self.run_host_key("scan")
        result, _ = self.run_host_key("trust", self.fp_b)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.known_hosts.read_text(), before)

    def test_trust_with_right_fingerprint_writes_pinned_file_mode_0600(self):
        self.run_host_key("scan")
        result, values = self.run_host_key("trust", self.fp_a)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["status"], "trusted")
        lines = self.known_hosts.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split()[0], ALIAS)
        self.assertEqual(lines[0].split()[1], self.pub_a.read_text().split()[0])
        self.assertEqual(self.mode(self.known_hosts), 0o600)
        self.assertFalse(self.pending.exists())

    def test_trust_replaces_alias_entry_and_keeps_other_hosts(self):
        self.known_hosts.write_text(
            "other-host ssh-ed25519 AAAAother\n"
            f"{ALIAS} ssh-ed25519 AAAAold\n"
        )
        self.run_host_key("scan", pub=self.pub_b)
        result, _ = self.run_host_key("trust", self.fp_b)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.known_hosts.read_text().splitlines()
        self.assertEqual(lines[0], "other-host ssh-ed25519 AAAAother")
        self.assertEqual([l for l in lines if l.startswith(f"{ALIAS} ")].__len__(), 1)
        self.assertNotIn("AAAAold", self.known_hosts.read_text())

    def test_trust_without_pending_file_fails(self):
        result, values = self.run_host_key("trust", self.fp_a)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(values["status"], "failed")
        self.assertFalse(self.known_hosts.exists())

    def test_trust_rejects_malformed_fingerprint(self):
        self.run_host_key("scan")
        for bad in ("", "MD5:aa:bb", "SHA256:", "SHA256:a b", self.fp_a.removeprefix("SHA256:")):
            with self.subTest(bad=bad):
                result, values = self.run_host_key("trust", bad)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(values["status"], "failed")
        self.assertFalse(self.known_hosts.exists())

    def test_status_missing_then_trusted(self):
        result, values = self.run_host_key("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values, {"status": "missing"})
        self.run_host_key("scan")
        self.run_host_key("trust", self.fp_a)
        result, values = self.run_host_key("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values, {"status": "trusted", "fingerprint": self.fp_a})

    def test_status_ignores_key_of_other_alias(self):
        self.known_hosts.write_text(f"other-host {self.pub_a.read_text().split()[0]} {self.pub_a.read_text().split()[1]}\n")
        _, values = self.run_host_key("status")
        self.assertEqual(values, {"status": "missing"})

    def test_usage_error_for_unknown_command(self):
        result, _ = self.run_host_key("bogus")
        self.assertEqual(result.returncode, 64)
        result, _ = self.run_host_key()
        self.assertEqual(result.returncode, 64)

    def test_missing_config_reports_failed(self):
        self.config.unlink()
        result, values = self.run_host_key("scan")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(values["status"], "failed")


if __name__ == "__main__":
    unittest.main()
