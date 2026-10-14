from __future__ import annotations

import signal
import os
import time
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIARCHY_SSH = ROOT / "client" / "diarchy-ssh"
LAN_HOST = "192.0.2.10"
TAILSCALE_HOST = "100.64.0.10"
SSH_USER = "labuser"
HOST_KEY_ALIAS = "lab-host-key"
KEY_B64 = "AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PINNED_LINE = f"{HOST_KEY_ALIAS} ssh-ed25519 {KEY_B64}\n"
IDENTITY = "/keys/lab_ed25519"
UNREACHABLE = "diarchy-route: Host is unreachable on LAN and Tailscale\n"

FAKE_SSH = """#!/usr/bin/env python3
import os, sys
with open(os.environ['FAKE_ARGV'], 'wb') as out:
    for arg in sys.argv[1:]:
        out.write(os.fsencode(arg) + b'\\0')
with open(os.environ['FAKE_STDIN'], 'wb') as out:
    out.write(sys.stdin.buffer.read())
stderr = os.environ.get('FAKE_SSH_STDERR')
if stderr:
    sys.stderr.write(stderr)
raise SystemExit(int(os.environ.get('FAKE_SSH_EXIT', '0')))
"""

FAKE_PING = """#!/usr/bin/env python3
import os, sys
if os.environ.get('FAKE_LS_DIR'):
    with open(os.environ['FAKE_LS_OUT'], 'w') as out:
        out.write('\\n'.join(os.listdir(os.environ['FAKE_LS_DIR'])))
host = sys.argv[-1]
if host not in os.environ['FAKE_REACHABLE'].split(','):
    print('4 packets transmitted, 0 packets received, 100.0% packet loss')
    raise SystemExit(2)
for index, value in enumerate((3.0, 4.0, 5.0, 6.0), 1):
    print(f'64 bytes from {host}: icmp_seq={index} ttl=64 time={value} ms')
print('4 packets transmitted, 4 packets received, 0% packet loss')
"""

FAKE_ROUTE = """#!/usr/bin/env python3
import sys
print(f'   route to: {sys.argv[-1]}')
print('  interface: en0')
"""

FAKE_NC = """#!/usr/bin/env python3
import os, sys
raise SystemExit(0 if sys.argv[-2] in os.environ['FAKE_REACHABLE'].split(',') else 1)
"""


class Result:
    def __init__(
        self,
        completed: subprocess.CompletedProcess,
        argv: list[str],
        stdin: bytes,
        tmp_entries: list[str],
    ):
        self.returncode = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        self.argv = argv
        self.stdin = stdin
        self.tmp_entries = tmp_entries


def write_config(path: Path, identity: str | None, lan: str, tailscale: str) -> None:
    lines = ["# Managed by Diarchy", f"DIARCHY_SSH_USER={SSH_USER}"]
    if lan:
        lines.append(f"DIARCHY_LAN_HOST={lan}")
    if tailscale:
        lines.append(f"DIARCHY_TAILSCALE_HOST={tailscale}")
    lines.append(f"DIARCHY_HOST_KEY_ALIAS={HOST_KEY_ALIAS}")
    lines.append("DIARCHY_SSH_PORT=2222")
    if identity is not None:
        lines.append(f"DIARCHY_SSH_IDENTITY_FILE={identity}")
    path.write_text("\n".join(lines) + "\n")


def run_diarchy_ssh(
    args: list[str],
    reachable: tuple[str, ...] = (LAN_HOST, TAILSCALE_HOST),
    identity: str | None = None,
    config: bool = True,
    stdin: bytes = b"",
    ssh_exit: int = 0,
    ssh_stderr: str = "",
    command: str | None = None,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
    pinned: bool = True,
    pinned_text: str | None = None,
) -> Result:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        fakes = {"ssh": FAKE_SSH, "ping": FAKE_PING, "route": FAKE_ROUTE, "nc": FAKE_NC}
        for name, body in fakes.items():
            path = temp / name
            path.write_text(body)
            path.chmod(0o755)

        config_path = temp / "diarchy.conf"
        if config:
            write_config(config_path, identity, LAN_HOST, TAILSCALE_HOST)
        argv_file = temp / "argv"
        if config:
            known_hosts = temp / "known_hosts"
            known_hosts.write_text(pinned_text if pinned_text is not None else (PINNED_LINE if pinned else ""))
        stdin_file = temp / "stdin"
        tmp_dir = temp / "tmp"
        tmp_dir.mkdir()

        env = {k: v for k, v in os.environ.items() if not k.startswith("DIARCHY_")}
        env.update(
            {
                "HOME": str(temp),
                "TMPDIR": str(tmp_dir),
                "DIARCHY_CONFIG": str(config_path),
                "SSH_BIN": str(temp / "ssh"),
                "PING_BIN": str(temp / "ping"),
                "ROUTE_BIN": str(temp / "route"),
                "CONNECT_BIN": str(temp / "nc"),
                "FAKE_REACHABLE": ",".join(reachable),
                "FAKE_ARGV": str(argv_file),
                "FAKE_STDIN": str(stdin_file),
                "FAKE_SSH_EXIT": str(ssh_exit),
                "FAKE_SSH_STDERR": ssh_stderr,
            }
        )
        for key, value in (extra_env or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        completed = subprocess.run(
            [command or str(DIARCHY_SSH), *args],
            env=env,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            check=False,
        )
        completed.stdout = completed.stdout.decode()
        completed.stderr = completed.stderr.decode()
        argv = (
            [a.decode() for a in argv_file.read_bytes().split(b"\0")[:-1]]
            if argv_file.exists()
            else []
        )
        sent = stdin_file.read_bytes() if stdin_file.exists() else b""
        return Result(completed, argv, sent, sorted(p.name for p in tmp_dir.iterdir()))


def option_values(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "-o"]


class DiarchySshTests(unittest.TestCase):
    def test_argv_contains_route_and_fixed_options(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "true"])
        self.assertEqual(result.returncode, 0, result.stderr)
        options = option_values(result.argv)
        for expected in (
            f"User={SSH_USER}",
            f"HostKeyAlias={HOST_KEY_ALIAS}",
            f"HostName={LAN_HOST}",
            "Port=2222",
            "BatchMode=yes",
            "ConnectTimeout=5",
            "StrictHostKeyChecking=yes",
        ):
            self.assertIn(expected, options)
        self.assertNotIn("StrictHostKeyChecking=accept-new", options)
        self.assertEqual(result.argv[-3:], ["diarchy", "--", "true"])

    def test_ssh_uses_pinned_known_hosts_next_to_config(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "true"])
        known = [o for o in option_values(result.argv) if o.startswith("UserKnownHostsFile=")]
        self.assertEqual(len(known), 1, result.argv)
        self.assertTrue(known[0].endswith("/known_hosts"), known)

    def test_empty_pinned_file_exits_255_with_exact_message(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "true"], pinned=False)
        self.assertEqual(result.returncode, 255)
        self.assertEqual(
            result.stderr,
            "diarchy-ssh: host key not confirmed. Open Diarchy and confirm the host key.\n",
        )
        self.assertEqual(result.argv, [])

    def test_pinned_key_for_other_alias_does_not_count(self):
        result = run_diarchy_ssh(
            ["--route", "lan", "--", "true"],
            pinned_text=f"other-alias ssh-ed25519 {KEY_B64}\n",
        )
        self.assertEqual(result.returncode, 255)
        self.assertIn("host key not confirmed", result.stderr)
        self.assertEqual(result.argv, [])

    def test_unset_tmpdir_falls_back_to_user_cache_not_tmp(self):
        with tempfile.TemporaryDirectory() as home:
            cache = Path(home) / "Library" / "Caches"
            cache.mkdir(parents=True)
            listing = Path(home) / "listing"
            result = run_diarchy_ssh(
                ["--route", "lan", "--", "true"],
                extra_env={
                    "TMPDIR": None,
                    "HOME": home,
                    "FAKE_LS_DIR": str(cache),
                    "FAKE_LS_OUT": str(listing),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            seen = listing.read_text().split()
            self.assertEqual(len(seen), 1, seen)
            self.assertTrue(seen[0].startswith("diarchy-ssh."), seen)
            self.assertEqual(list(cache.iterdir()), [])

    def test_print_route_prefers_lan_and_does_not_start_ssh(self):
        result = run_diarchy_ssh(["--print-route"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "route=lan\n")
        self.assertEqual(result.argv, [])

    def test_print_route_falls_back_to_tailscale(self):
        result = run_diarchy_ssh(["--print-route"], reachable=(TAILSCALE_HOST,))
        self.assertEqual(result.stdout, "route=tailscale\n")

    def test_print_route_none_when_nothing_reachable(self):
        result = run_diarchy_ssh(["--print-route"], reachable=())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "route=none\n")
        self.assertEqual(result.argv, [])

    def test_print_route_works_with_empty_pinned_file(self):
        result = run_diarchy_ssh(["--print-route"], pinned=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "route=lan\n")

    def test_no_identity_options_when_not_configured(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "true"])
        self.assertFalse([o for o in option_values(result.argv) if o.startswith("IdentityFile=")])
        self.assertNotIn("IdentitiesOnly=yes", option_values(result.argv))

    def test_identity_options_when_configured(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "true"], identity=IDENTITY)
        options = option_values(result.argv)
        self.assertIn(f"IdentityFile={IDENTITY}", options)
        self.assertIn("IdentitiesOnly=yes", options)

    def test_identity_with_space_is_double_quoted_in_argv(self):
        identity = "/path with space/id"
        result = run_diarchy_ssh(["--route", "lan", "--", "true"], identity=identity)
        options = option_values(result.argv)
        self.assertIn(f'IdentityFile="{identity}"', options)
        self.assertIn("IdentitiesOnly=yes", options)

    def test_works_with_cdpath_and_relative_invocation(self):
        with tempfile.TemporaryDirectory() as decoy:
            (Path(decoy) / "client").mkdir()
            result = run_diarchy_ssh(
                ["--route", "lan", "--", "true"],
                command="client/diarchy-ssh",
                cwd=ROOT,
                extra_env={"CDPATH": f"{decoy}:."},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.argv[-3:], ["diarchy", "--", "true"])

    def test_forced_tailscale_picks_tailscale_host(self):
        result = run_diarchy_ssh(["--route", "tailscale", "--", "true"])
        self.assertIn(f"HostName={TAILSCALE_HOST}", option_values(result.argv))

    def test_default_route_is_auto_and_prefers_lan(self):
        result = run_diarchy_ssh(["--", "true"])
        self.assertIn(f"HostName={LAN_HOST}", option_values(result.argv))

    def test_remote_words_arrive_as_exact_trailing_argv(self):
        words = ["echo", "a b", "it's \"quoted\"", "x;y", "$(touch /tmp/x)", "~/.local/bin/diarchy-host"]
        result = run_diarchy_ssh(["--route", "lan", "--", *words])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.argv[-len(words) :], words)
        self.assertEqual(result.argv[-len(words) - 2 : -len(words)], ["diarchy", "--"])

    def test_leading_dash_remote_word_is_not_an_ssh_option(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "-V"])
        self.assertEqual(result.argv[-3:], ["diarchy", "--", "-V"])

    def test_empty_string_remote_word_is_preserved(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "echo", "", "x"])
        self.assertEqual(result.argv[-5:], ["diarchy", "--", "echo", "", "x"])

    def test_no_temp_file_left_after_success(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "true"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.tmp_entries, [])

    def test_no_temp_file_left_after_route_failure(self):
        result = run_diarchy_ssh(["--", "true"], reachable=())
        self.assertEqual(result.returncode, 255)
        self.assertEqual(result.tmp_entries, [])

    def test_unwritable_tmpdir_exits_255(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {k: v for k, v in os.environ.items() if not k.startswith("DIARCHY_")}
            env["TMPDIR"] = str(Path(temp_dir) / "missing")
            completed = subprocess.run(
                [str(DIARCHY_SSH), "--", "true"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 255)
        self.assertEqual(completed.stderr, "diarchy-ssh: cannot create temporary file\n")

    def test_sigterm_during_probe_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tmp_dir = temp / "tmp"
            tmp_dir.mkdir()
            started = temp / "ping-started"
            ping = temp / "ping"
            ping.write_text(
                "#!/usr/bin/env python3\n"
                "import os, time\n"
                f"open({str(started)!r}, 'w').close()\n"
                "time.sleep(30)\n"
            )
            ping.chmod(0o755)
            # The probe reaches ping only after the TCP check and route lookup pass.
            connect = temp / "nc"
            connect.write_text("#!/bin/sh\nexit 0\n")
            connect.chmod(0o755)
            route = temp / "route"
            route.write_text("#!/bin/sh\necho '  interface: utun4'\n")
            route.chmod(0o755)
            config_path = temp / "diarchy.conf"
            write_config(config_path, None, LAN_HOST, TAILSCALE_HOST)
            env = {k: v for k, v in os.environ.items() if not k.startswith("DIARCHY_")}
            env.update(
                {
                    "HOME": str(temp),
                    "TMPDIR": str(tmp_dir),
                    "DIARCHY_CONFIG": str(config_path),
                    "PING_BIN": str(ping),
                    "CONNECT_BIN": str(connect),
                    "ROUTE_BIN": str(route),
                }
            )
            proc = subprocess.Popen(
                [str(DIARCHY_SSH), "--", "true"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not started.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(started.exists(), "fake ping never started")
                # Signal the whole group, as a terminal Ctrl-C would, so the
                # blocked probe dies and the script's trap can run.
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            self.assertEqual(proc.returncode, 255)
            self.assertEqual(list(tmp_dir.iterdir()), [])

    def test_stdin_reaches_ssh(self):
        payload = b"\x00binary\xffpayload\nline2"
        result = run_diarchy_ssh(["--route", "lan", "--", "cat"], stdin=payload)
        self.assertEqual(result.stdin, payload)

    def test_ssh_exit_code_is_propagated(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "false"], ssh_exit=7)
        self.assertEqual(result.returncode, 7)

    def test_success_drops_route_diagnostic(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "true"])
        self.assertEqual(result.stderr, "")

    def test_ssh_stderr_reaches_caller_unchanged(self):
        message = "Permission denied (publickey).\n"
        result = run_diarchy_ssh(
            ["--route", "lan", "--", "true"], ssh_exit=255, ssh_stderr=message
        )
        self.assertEqual(result.returncode, 255)
        self.assertEqual(result.stderr, message)

    def test_all_routes_down_exits_255_with_exact_reason(self):
        result = run_diarchy_ssh(["--", "true"], reachable=())
        self.assertEqual(result.returncode, 255)
        self.assertEqual(result.stderr, UNREACHABLE)
        self.assertEqual(result.argv, [])

    def test_forced_route_failure_exits_255(self):
        result = run_diarchy_ssh(["--route", "lan", "--", "true"], reachable=(TAILSCALE_HOST,))
        self.assertEqual(result.returncode, 255)
        self.assertIn("unreachable", result.stderr)
        self.assertNotIn("route=", result.stderr)

    def test_missing_config_exits_255_without_route_noise(self):
        result = run_diarchy_ssh(["--", "true"], config=False)
        self.assertEqual(result.returncode, 255)
        self.assertTrue(result.stderr.startswith("diarchy-config: "), result.stderr)
        self.assertNotIn("route=", result.stderr)
        self.assertEqual(result.argv, [])

    def test_missing_separator_is_usage_error(self):
        result = run_diarchy_ssh(["--route", "lan", "true"])
        self.assertEqual(result.returncode, 64)
        self.assertTrue(result.stderr.startswith("diarchy-ssh: usage: "), result.stderr)
        self.assertEqual(result.argv, [])

    def test_no_arguments_is_usage_error(self):
        result = run_diarchy_ssh([])
        self.assertEqual(result.returncode, 64)
        self.assertTrue(result.stderr.startswith("diarchy-ssh: usage: "), result.stderr)

    def test_invalid_route_is_usage_error(self):
        result = run_diarchy_ssh(["--route", "x", "--", "true"])
        self.assertEqual(result.returncode, 64)
        self.assertTrue(result.stderr.startswith("diarchy-ssh: usage: "), result.stderr)
        self.assertEqual(result.argv, [])

    def test_route_without_value_is_usage_error(self):
        result = run_diarchy_ssh(["--route"])
        self.assertEqual(result.returncode, 64)
        self.assertTrue(result.stderr.startswith("diarchy-ssh: usage: "), result.stderr)


if __name__ == "__main__":
    unittest.main()
