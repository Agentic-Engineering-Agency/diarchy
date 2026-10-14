from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "client" / "diarchy-config.sh"
HEADER = "# Managed by Diarchy"
KEYS = [
    "DIARCHY_SSH_USER", "DIARCHY_LAN_HOST", "DIARCHY_TAILSCALE_HOST",
    "DIARCHY_SSH_PORT", "DIARCHY_SSH_IDENTITY_FILE", "DIARCHY_HOST_KEY_ALIAS",
    "DIARCHY_KVM_SIDE", "DIARCHY_HOST_FINGERPRINT", "DIARCHY_STREAM_RESOLUTION",
    "DIARCHY_STREAM_FPS", "DIARCHY_STREAM_OVERLAY", "DIARCHY_STREAM_OUTPUT",
]


class ConfigLoaderTests(unittest.TestCase):
    def run_loader(self, content=None, env=None, mode=None, driver=None):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            config = home / "diarchy.conf"
            if content is not None:
                config.write_bytes(content.encode() if isinstance(content, str) else content)
                if mode is not None:
                    config.chmod(mode)
            marker = home / "MARKER"
            base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
                    "LOADER": str(LOADER), "DIARCHY_CONFIG": str(config), "MARKER": str(marker)}
            if env:
                base.update(env)
            script = driver or (
                'source "$LOADER"; diarchy_config_load; rc=$?; printf "sentinel\\n"; '
                'printf "RC=%s\\n" "$rc"; env | sort'
            )
            result = subprocess.run(["/bin/bash", "-c", script], env=base,
                                    text=True, capture_output=True)
            return result, marker

    def valid(self, **values):
        defaults = {"DIARCHY_SSH_USER": "alice", "DIARCHY_LAN_HOST": "192.0.2.10"}
        defaults.update(values)
        return HEADER + "\n" + "\n".join(f"{k}={v}" for k, v in defaults.items()) + "\n"

    def assert_reason(self, result, reason, rc=1):
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"diarchy-config: {reason}\n", result.stderr)
        self.assertEqual(result.stderr.count("diarchy-config:"), 1)
        self.assertIn(f"RC={rc}\n", result.stdout)

    def exported(self, result):
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def test_missing_file_without_environment_is_not_configured(self):
        result, _ = self.run_loader()
        self.assert_reason(result, "Diarchy is not configured (open the app to set it up)")

    def test_environment_can_configure_without_file_and_defaults_apply(self):
        result, _ = self.run_loader(env={"DIARCHY_SSH_USER": "alice", "DIARCHY_LAN_HOST": "192.0.2.10"})
        values = self.exported(result)
        self.assertEqual(values["DIARCHY_SSH_PORT"], "22")
        self.assertEqual(values["DIARCHY_KVM_SIDE"], "left")
        self.assertEqual(values["DIARCHY_STREAM_RESOLUTION"], "1920x1080")
        self.assertEqual(values["DIARCHY_STREAM_FPS"], "60")
        self.assertEqual(values["DIARCHY_STREAM_OVERLAY"], "0")
        self.assertEqual(values["DIARCHY_STREAM_OUTPUT"], "auto")

    def test_environment_user_without_host_is_not_configured(self):
        result, _ = self.run_loader(env={"DIARCHY_SSH_USER": "alice"})
        self.assert_reason(result, "Diarchy is not configured (open the app to set it up)")

    def test_unreadable_file(self):
        if os.geteuid() == 0:
            self.skipTest("mode-000 unreadability is unavailable to root")
        result, _ = self.run_loader(self.valid(), mode=0)
        config = Path(result.stdout.split("DIARCHY_CONFIG=", 1)[1].split("\n", 1)[0])
        self.assert_reason(result, f"cannot read configuration file: {config}")

    def test_directory_at_config_path_is_a_single_cannot_read_line(self):
        result, _ = self.run_loader(
            driver='DIARCHY_CONFIG="$HOME/a-directory"; mkdir "$DIARCHY_CONFIG"; '
                   'source "$LOADER"; diarchy_config_load; rc=$?; printf "sentinel\\n"; printf "RC=%s\\n" "$rc"')
        home = Path(result.stderr.split("cannot read configuration file: ", 1)[1].rsplit("/a-directory", 1)[0])
        self.assertEqual(result.stderr, f"diarchy-config: cannot read configuration file: {home}/a-directory\n")
        self.assertIn("RC=1\n", result.stdout)

    def test_identity_file_from_environment_rejects_newline_and_double_quote(self):
        for value in ("/tmp/a\nb", "/tmp/a\n", '/tmp/a"b', '/tmp/"'):
            result, _ = self.run_loader(self.valid(), env={"DIARCHY_SSH_IDENTITY_FILE": value})
            self.assert_reason(result, "DIARCHY_SSH_IDENTITY_FILE is invalid")

    def test_identity_file_with_space_is_accepted(self):
        result, _ = self.run_loader(self.valid(), env={"DIARCHY_SSH_IDENTITY_FILE": "/tmp/path with space/id"})
        self.assertEqual(self.exported(result)["DIARCHY_SSH_IDENTITY_FILE"], "/tmp/path with space/id")

    def test_empty_file_fails_header_check(self):
        result, _ = self.run_loader("", env={"DIARCHY_SSH_USER": "alice", "DIARCHY_LAN_HOST": "192.0.2.10"})
        self.assert_reason(result, 'first line must be "# Managed by Diarchy"')

    def test_second_call_in_same_shell_does_not_reuse_stale_values(self):
        with tempfile.TemporaryDirectory() as td:
            first = Path(td) / "first.conf"
            second = Path(td) / "second.conf"
            first.write_text(self.valid(DIARCHY_SSH_PORT="2222", DIARCHY_STREAM_FPS="90"))
            second.write_text(self.valid())
            driver = (
                f'source "$LOADER"; DIARCHY_CONFIG={first} diarchy_config_load; '
                'echo "first=$DIARCHY_SSH_PORT,$DIARCHY_STREAM_FPS"; '
                'unset DIARCHY_SSH_USER DIARCHY_LAN_HOST DIARCHY_TAILSCALE_HOST DIARCHY_SSH_PORT '
                'DIARCHY_SSH_IDENTITY_FILE DIARCHY_HOST_KEY_ALIAS DIARCHY_KVM_SIDE DIARCHY_HOST_FINGERPRINT '
                'DIARCHY_STREAM_RESOLUTION DIARCHY_STREAM_FPS DIARCHY_STREAM_OVERLAY DIARCHY_STREAM_OUTPUT; '
                f'DIARCHY_CONFIG={second} diarchy_config_load; echo "second=$DIARCHY_SSH_PORT,$DIARCHY_STREAM_FPS"'
            )
            result, _ = self.run_loader(driver=driver)
            self.assertIn("first=2222,90\n", result.stdout)
            self.assertIn("second=22,60\n", result.stdout)

    def test_valid_values_are_accepted(self):
        fp = ":".join(["AB", "0F", "9C", "10"] * 8)
        cases = [
            ("DIARCHY_HOST_FINGERPRINT", fp, fp),
            ("DIARCHY_KVM_SIDE", "left", "left"), ("DIARCHY_KVM_SIDE", "right", "right"),
            ("DIARCHY_KVM_SIDE", "top", "top"), ("DIARCHY_KVM_SIDE", "bottom", "bottom"),
            ("DIARCHY_SSH_IDENTITY_FILE", "/Users/user/.ssh/id_ed25519", "/Users/user/.ssh/id_ed25519"),
            ("DIARCHY_SSH_IDENTITY_FILE", "", ""),
            ("DIARCHY_SSH_PORT", "0022", "0022"), ("DIARCHY_SSH_PORT", "08080", "08080"),
            ("DIARCHY_SSH_PORT", "65535", "65535"),
            ("DIARCHY_STREAM_FPS", "060", "060"), ("DIARCHY_STREAM_FPS", "089", "089"),
            ("DIARCHY_STREAM_FPS", "30", "30"), ("DIARCHY_STREAM_FPS", "240", "240"),
            ("DIARCHY_STREAM_OVERLAY", "1", "1"), ("DIARCHY_STREAM_RESOLUTION", "2560x1440", "2560x1440"),
            ("DIARCHY_STREAM_OUTPUT", "auto", "auto"), ("DIARCHY_STREAM_OUTPUT", "DP-1", "DP-1"),
            ("DIARCHY_STREAM_OUTPUT", "eDP-1", "eDP-1"), ("DIARCHY_STREAM_OUTPUT", "A" * 32, "A" * 32),
        ]
        for key, value, expected in cases:
            with self.subTest(key=key, value=value):
                result, _ = self.run_loader(self.valid(**{key: value}))
                self.assertIn("RC=0\n", result.stdout, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(self.exported(result)[key], expected)

    def test_header_blank_and_bom_are_rejected(self):
        for first in ("wrong", "", "\ufeff" + HEADER):
            result, _ = self.run_loader(first + "\nDIARCHY_SSH_USER=alice\nDIARCHY_LAN_HOST=192.0.2.10\n")
            self.assert_reason(result, 'first line must be "# Managed by Diarchy"')
    def test_crlf_reports_line_number(self):
        result, _ = self.run_loader(HEADER + "\nDIARCHY_SSH_USER=alice\r\n")
        self.assert_reason(result, "line 2: carriage returns are not allowed")

    def test_missing_trailing_newline_parses(self):
        result, _ = self.run_loader(self.valid().rstrip("\n"))
        self.assertEqual(self.exported(result)["DIARCHY_SSH_USER"], "alice")

    def test_parse_errors_and_quoted_values(self):
        for line, reason in [("KEY =x", "line 2: expected KEY=VALUE"), ("=x", "line 2: expected KEY=VALUE"),
                             ("junk", "line 2: expected KEY=VALUE"), ("NOPE=x", "line 2: unknown key NOPE"),
                             ("DIARCHY_SSH_USER=alice\nDIARCHY_SSH_USER=bob", "line 3: duplicate key DIARCHY_SSH_USER"),
                             ("DIARCHY_STREAM_OUTPUT=DP-1\nDIARCHY_STREAM_OUTPUT=eDP-1", "line 3: duplicate key DIARCHY_STREAM_OUTPUT"),
                             ('DIARCHY_SSH_USER="alice"', "quoted values are not allowed"),
                             ("DIARCHY_SSH_USER='alice'", "quoted values are not allowed")]:
            result, _ = self.run_loader(HEADER + "\n" + line + "\nDIARCHY_LAN_HOST=192.0.2.10\n")
            self.assert_reason(result, reason)

    def test_comments_and_blank_lines_are_ignored(self):
        result, _ = self.run_loader(HEADER + "\n\n# comment\nDIARCHY_SSH_USER=alice\n# another\nDIARCHY_LAN_HOST=192.0.2.10\n")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.exported(result)["DIARCHY_SSH_USER"], "alice")

    def test_validation_rejects_invalid_values(self):
        cases = [
            ("DIARCHY_SSH_USER", "Root"), ("DIARCHY_SSH_USER", "a" * 33),
            ("DIARCHY_LAN_HOST", "-a"), ("DIARCHY_LAN_HOST", "a_b"), ("DIARCHY_LAN_HOST", "a" * 254),
            ("DIARCHY_SSH_PORT", "0"), ("DIARCHY_SSH_PORT", "65536"), ("DIARCHY_SSH_PORT", "22a"),
            ("DIARCHY_SSH_IDENTITY_FILE", "relative"), ("DIARCHY_SSH_IDENTITY_FILE", "/tmp/../id"),
            ("DIARCHY_HOST_KEY_ALIAS", "a_b"), ("DIARCHY_KVM_SIDE", "middle"),
            ("DIARCHY_HOST_FINGERPRINT", "aa:" + "AA:" * 30 + "AA"),
            ("DIARCHY_HOST_FINGERPRINT", "AA:" * 30 + "AA"),
            ("DIARCHY_HOST_FINGERPRINT", "AA:" * 32 + "AA"),
            ("DIARCHY_HOST_FINGERPRINT", "AA" * 32),
            ("DIARCHY_STREAM_RESOLUTION", "1920*1080"), ("DIARCHY_STREAM_RESOLUTION", "99x99"),
            ("DIARCHY_STREAM_FPS", "29"), ("DIARCHY_STREAM_FPS", "241"), ("DIARCHY_STREAM_FPS", "029"),
            ("DIARCHY_STREAM_FPS", "0300"), ("DIARCHY_STREAM_FPS", "abc"), ("DIARCHY_STREAM_FPS", "0"),
            ("DIARCHY_STREAM_OVERLAY", "2"),
            ("DIARCHY_STREAM_OUTPUT", "a_b"), ("DIARCHY_STREAM_OUTPUT", "A" * 33),
            ("DIARCHY_STREAM_OUTPUT", "DP 1"), ("DIARCHY_STREAM_OUTPUT", "DP-1;ls"),
        ]
        for key, value in cases:
            values = {"DIARCHY_SSH_USER": "alice", "DIARCHY_LAN_HOST": "192.0.2.10", key: value}
            result, _ = self.run_loader(HEADER + "\n" + "\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
            self.assert_reason(result, key + " is invalid")

    def test_required_user_and_at_least_one_host(self):
        result, _ = self.run_loader(HEADER + "\nDIARCHY_LAN_HOST=192.0.2.10\n")
        self.assert_reason(result, "DIARCHY_SSH_USER is required")
        result, _ = self.run_loader(HEADER + "\nDIARCHY_SSH_USER=alice\n")
        self.assert_reason(result, "at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required")

    def test_alias_defaults_to_tailscale_then_lan_and_explicit_wins(self):
        for values, expected in [({"DIARCHY_TAILSCALE_HOST": "100.64.0.10"}, "100.64.0.10"),
                                 ({}, "192.0.2.10"),
                                 ({"DIARCHY_TAILSCALE_HOST": "100.64.0.10", "DIARCHY_HOST_KEY_ALIAS": "alias"}, "alias")]:
            result, _ = self.run_loader(self.valid(**values))
            self.assertEqual(self.exported(result)["DIARCHY_HOST_KEY_ALIAS"], expected)

    def test_nonempty_environment_overrides_file_but_empty_does_not(self):
        result, _ = self.run_loader(self.valid(DIARCHY_SSH_PORT="2222"), env={"DIARCHY_SSH_PORT": "2200"})
        self.assertEqual(self.exported(result)["DIARCHY_SSH_PORT"], "2200")
        result, _ = self.run_loader(self.valid(DIARCHY_SSH_PORT="2222"), env={"DIARCHY_SSH_PORT": ""})
        self.assertEqual(self.exported(result)["DIARCHY_SSH_PORT"], "2222")

    def test_injection_is_not_executed_and_valid_file_has_no_side_effects(self):
        for value in ("$(touch MARKER)", "`touch MARKER`"):
            result, marker = self.run_loader(self.valid(DIARCHY_LAN_HOST=value))
            self.assert_reason(result, "DIARCHY_LAN_HOST is invalid")
            self.assertFalse(marker.exists())
        result, marker = self.run_loader(self.valid())
        self.assertEqual(result.returncode, 0)
        self.assertFalse(marker.exists())

    def test_failure_does_not_export_partial_values(self):
        result, _ = self.run_loader(HEADER + "\nDIARCHY_SSH_USER=alice\nDIARCHY_LAN_HOST=-bad\n")
        values = self.exported(result)
        self.assertFalse(any(key in values for key in KEYS))

    def test_failure_returns_and_driver_continues(self):
        result, _ = self.run_loader(driver='source "$LOADER"; diarchy_config_load; rc=$?; echo sentinel; echo RC=$rc')
        self.assertIn("sentinel\n", result.stdout)
        self.assert_reason(result, "Diarchy is not configured (open the app to set it up)")


if __name__ == "__main__":
    unittest.main()
