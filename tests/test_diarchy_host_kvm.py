from __future__ import annotations

import stat
import unittest

from tests.test_diarchy_host_support import AgentCase, FP_A, FP_B

UNIT_KVM = "diarchy-kvm.service"
UNIT_DISPLAY = "diarchy-display.service"
UNIT_SUNSHINE = "diarchy-sunshine.service"
MIXED_FP = ":".join(["aA"] * 32)
RESTART = f"--user restart {UNIT_KVM}"


class KvmCase(AgentCase):
    def setUp(self) -> None:
        super().setUp()
        self.fake_sudo()
        self.fake_hyprctl()  # a live Hyprland session unless a test says otherwise
        self.fake_display()
        self.config_dir = self.home / ".config" / "lan-mouse"
        self.config = self.config_dir / "config.toml"

    def tearDown(self) -> None:
        self.assertEqual(self.sudo_calls(), [], "the agent must never invoke sudo")
        super().tearDown()

    def install_unit_file(self) -> None:
        unit = self.home / ".config" / "systemd" / "user" / UNIT_KVM
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("[Service]\n")

    def fake_display(self, exit_code: int = 0, stderr: str = "") -> None:
        """A `diarchy-display` that logs `display <argv>` to FAKE_LOG, writes `stderr`, exits `exit_code`."""
        self.tool("diarchy-display", "import os, sys\n"
                  "open(os.environ['FAKE_LOG'], 'a').write('display ' + ' '.join(sys.argv[1:]) + '\\n')\n"
                  f"sys.stderr.write({stderr!r})\nsys.exit({exit_code})\n", "DIARCHY_DISPLAY_BIN")

    def stub_systemctl(self, source: str) -> None:
        """A systemctl that logs argv, then runs `source` (with `sys` and `argv` in scope)."""
        self.tool("systemctl", "import os, sys\nargv = sys.argv[1:]\n"
                  "open(os.environ['FAKE_LOG'], 'a').write(' '.join(argv) + '\\n')\n" + source, "SYSTEMCTL_BIN")

    def run_ok(self, *args: str) -> dict:
        result, body = self.agent(*args)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertIs(body["ok"], True)
        return body

    def run_error(self, code: str, exit_code: int, *args: str) -> dict:
        result, body = self.agent(*args)
        self.assertEqual(result.returncode, exit_code, (result.stdout, result.stderr))
        self.assertIs(body["ok"], False)
        self.assertEqual(body["error"]["code"], code)
        return body

    def entry_lines(self) -> list[str]:
        return [line for line in self.config.read_text().splitlines() if line.endswith('= "mac"')]

    def restarts(self) -> list[str]:
        return [line for line in self.calls() if " restart " in f" {line} "]


class ConfigureTests(KvmCase):
    def test_configure_without_existing_config(self) -> None:
        self.fake_systemctl()
        body = self.run_ok("kvm", "configure")
        self.assertEqual(body, {"schema": 1, "ok": True, "command": "kvm configure", "authorized": None})
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertTrue(self.config.read_text().startswith("# Managed by diarchy\n"))
        self.assertEqual(self.entry_lines(), [])
        self.assertEqual(self.restarts(), [])

    def test_configure_preserves_authorized_fingerprint(self) -> None:
        self.fake_systemctl()
        self.run_ok("kvm", "authorize", "--fingerprint", FP_A)
        body = self.run_ok("kvm", "configure")
        self.assertEqual(body["authorized"], FP_A)
        self.assertEqual(self.entry_lines(), [f'"{FP_A.lower()}" = "mac"'])

    def test_configure_over_unmanaged_file_backs_it_up(self) -> None:
        self.fake_systemctl()
        self.config_dir.mkdir(parents=True)
        original = '[[clients]]\nhostname = "x"\n'
        self.config.write_text(original)
        body = self.run_ok("kvm", "configure")
        self.assertIsNone(body["authorized"])
        backups = list(self.config_dir.glob("config.toml.backup.*"))
        self.assertEqual([path.read_text() for path in backups], [original])

    def test_configure_restarts_active_kvm_unit(self) -> None:
        self.fake_systemctl(active=[UNIT_KVM])
        self.run_ok("kvm", "configure")
        self.assertIn(RESTART, self.calls())

    def test_configure_and_authorize_restart_activating_kvm_unit(self) -> None:
        for argv in (("kvm", "configure"), ("kvm", "authorize", "--fingerprint", FP_A)):
            with self.subTest(argv=argv):
                self.fake_systemctl(activating=[UNIT_KVM])
                self.log.unlink(missing_ok=True)
                self.run_ok(*argv)
                self.assertIn(RESTART, self.calls())

    def test_configure_restart_failure_is_service_failed(self) -> None:
        self.fake_systemctl(active=[UNIT_KVM], fail=[f"restart {UNIT_KVM}"])
        body = self.run_error("service_failed", 1, "kvm", "configure")
        self.assertEqual(body["command"], "kvm configure")
        self.assertTrue(self.config.read_text().startswith("# Managed by diarchy\n"))

    def test_configure_with_extra_argument_is_usage(self) -> None:
        self.fake_systemctl()
        self.run_error("usage", 64, "kvm", "configure", "extra")
        self.assertFalse(self.config_dir.exists())

    def test_preexisting_config_dir_is_tightened_to_0700(self) -> None:
        self.fake_systemctl()
        for argv in (("kvm", "configure"), ("kvm", "authorize", "--fingerprint", FP_A)):
            with self.subTest(argv=argv):
                self.config_dir.mkdir(parents=True, exist_ok=True)
                self.config_dir.chmod(0o755)
                self.run_ok(*argv)
                self.assertEqual(stat.S_IMODE(self.config_dir.stat().st_mode), 0o700)


class AuthorizeTests(KvmCase):
    def test_authorize_lowercase_input_writes_lowercase_returns_uppercase(self) -> None:
        self.fake_systemctl()
        body = self.run_ok("kvm", "authorize", "--fingerprint", FP_A.lower())
        self.assertEqual(body, {"schema": 1, "ok": True, "command": "kvm authorize", "authorized": FP_A})
        self.assertEqual(self.entry_lines(), [f'"{FP_A.lower()}" = "mac"'])
        self.assertEqual(self.config.read_text().count(FP_A.lower()), 1)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_authorize_accepts_mixed_case(self) -> None:
        self.fake_systemctl()
        body = self.run_ok("kvm", "authorize", "--fingerprint", MIXED_FP)
        self.assertEqual(body["authorized"], MIXED_FP.upper())
        self.assertEqual(self.entry_lines(), [f'"{MIXED_FP.lower()}" = "mac"'])

    def test_authorize_restarts_only_when_kvm_unit_active(self) -> None:
        self.fake_systemctl()
        self.run_ok("kvm", "authorize", "--fingerprint", FP_A)
        self.assertEqual(self.restarts(), [])

        self.fake_systemctl(active=[UNIT_KVM])
        self.run_ok("kvm", "authorize", "--fingerprint", FP_B)
        self.assertEqual(self.restarts(), [RESTART])

    def test_malformed_fingerprint_touches_nothing(self) -> None:
        self.fake_systemctl()
        for value in ("zz", ":".join(["AA"] * 31), ":".join(["ZZ"] * 32), FP_A + ":AA", FP_A.replace(":", ""), ""):
            with self.subTest(value=value):
                body = self.run_error("invalid_fingerprint", 1, "kvm", "authorize", "--fingerprint", value)
                self.assertEqual(body["command"], "kvm authorize")
                self.assertFalse(self.config_dir.exists())
                self.assertEqual(self.calls(), [])

    def test_malformed_arguments_are_usage_errors(self) -> None:
        self.fake_systemctl()
        cases = (
            ("kvm", "authorize"),
            ("kvm", "authorize", "--fingerprint"),
            ("kvm", "authorize", FP_A),
            ("kvm", "authorize", "--fingerprint", FP_A, "extra"),
            ("kvm", "authorize", "--fingerprint", FP_A, "--fingerprint", FP_B),
            ("kvm", "authorize", "--other", FP_A),
            ("kvm", "authorize", f"--fingerprint={FP_A}"),
        )
        for argv in cases:
            with self.subTest(argv=argv):
                body = self.run_error("usage", 64, *argv)
                self.assertEqual(body["command"], "kvm authorize")
                self.assertFalse(self.config_dir.exists())
                self.assertEqual(self.calls(), [])

    def test_authorize_restart_failure_is_service_failed_but_keeps_the_written_config(self) -> None:
        self.fake_systemctl(active=[UNIT_KVM], fail=[f"restart {UNIT_KVM}"])
        body = self.run_error("service_failed", 1, "kvm", "authorize", "--fingerprint", FP_A)
        self.assertEqual(body["command"], "kvm authorize")
        self.assertEqual(self.entry_lines(), [f'"{FP_A.lower()}" = "mac"'])

    def test_authorize_over_unmanaged_file_backs_it_up(self) -> None:
        self.fake_systemctl()
        self.config_dir.mkdir(parents=True)
        original = f'[authorized_fingerprints]\n"{FP_B.lower()}" = "other"\n'
        self.config.write_text(original)
        self.run_ok("kvm", "authorize", "--fingerprint", FP_A)
        backups = list(self.config_dir.glob("config.toml.backup.*"))
        self.assertEqual([path.read_text() for path in backups], [original])
        self.assertEqual(self.entry_lines(), [f'"{FP_A.lower()}" = "mac"'])


class ConfigWriteFailureTests(KvmCase):
    def test_unwritable_config_location_is_config_write_failed(self) -> None:
        self.fake_systemctl(active=[UNIT_KVM])
        (self.home / ".config").write_text("a file where the config directory must go")
        for argv in (("kvm", "configure"), ("kvm", "authorize", "--fingerprint", FP_A)):
            with self.subTest(argv=argv):
                self.run_error("config_write_failed", 1, *argv)
        self.assertEqual(self.restarts(), [])


class StartTests(KvmCase):
    def test_start_stops_active_stream_units_before_starting_kvm(self) -> None:
        self.install_unit_file()
        self.fake_systemctl(active=[UNIT_SUNSHINE, UNIT_DISPLAY])
        body = self.run_ok("kvm", "start")
        self.assertEqual(body, {"schema": 1, "ok": True, "command": "kvm start", "service_active": True})
        calls = self.calls()
        stop_sunshine = calls.index(f"--user stop {UNIT_SUNSHINE}")
        stop_display = calls.index(f"--user stop {UNIT_DISPLAY}")
        start_kvm = calls.index(f"--user start {UNIT_KVM}")
        self.assertLess(stop_sunshine, stop_display)
        self.assertLess(stop_display, start_kvm)

    def test_start_restores_the_display_after_the_stream_stops_and_before_kvm_starts(self) -> None:
        self.install_unit_file()
        self.fake_systemctl(active=[UNIT_SUNSHINE, UNIT_DISPLAY])
        self.run_ok("kvm", "start")
        calls = self.calls()
        self.assertEqual(calls.count("display restore"), 1)
        self.assertLess(calls.index(f"--user stop {UNIT_DISPLAY}"), calls.index("display restore"))
        self.assertLess(calls.index("display restore"), calls.index(f"--user start {UNIT_KVM}"))

    def test_failed_display_restore_does_not_start_kvm(self) -> None:
        self.install_unit_file()
        self.fake_systemctl()
        self.fake_display(exit_code=1, stderr="diarchy-display: hyprctl failed")
        body = self.run_error("service_failed", 1, "kvm", "start")
        self.assertIn("diarchy-display: hyprctl failed", body["error"]["message"])
        self.assertNotIn(f"--user start {UNIT_KVM}", self.calls())

    def test_start_stops_activating_stream_units_before_starting_kvm(self) -> None:
        self.install_unit_file()
        self.fake_systemctl(activating=[UNIT_SUNSHINE])
        self.run_ok("kvm", "start")
        calls = self.calls()
        self.assertLess(calls.index(f"--user stop {UNIT_SUNSHINE}"), calls.index(f"--user start {UNIT_KVM}"))

    def test_start_with_inactive_stream_units_does_not_stop_them(self) -> None:
        self.install_unit_file()
        self.fake_systemctl()
        body = self.run_ok("kvm", "start")
        self.assertIs(body["service_active"], True)
        self.assertEqual([line for line in self.calls() if " stop " in f" {line} "], [])
        self.assertIn(f"--user start {UNIT_KVM}", self.calls())

    def test_start_looks_for_the_unit_file_under_xdg_config_home(self) -> None:
        self.fake_systemctl()
        xdg = self.root / "xdg"
        self.env["XDG_CONFIG_HOME"] = str(xdg)
        self.install_unit_file()  # under $HOME/.config: not where the installer puts it when XDG_CONFIG_HOME is set
        self.run_error("not_installed", 1, "kvm", "start")
        unit = xdg / "systemd" / "user" / UNIT_KVM
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\n")
        self.run_ok("kvm", "start")

    def test_start_without_unit_file_is_not_installed(self) -> None:
        self.fake_systemctl(active=[UNIT_SUNSHINE])
        body = self.run_error("not_installed", 1, "kvm", "start")
        self.assertEqual(body["command"], "kvm start")
        self.assertEqual(self.calls(), [])

    def test_start_failure_is_service_failed(self) -> None:
        self.install_unit_file()
        self.fake_systemctl(fail=[f"start {UNIT_KVM}"])
        self.run_error("service_failed", 1, "kvm", "start")

    def test_start_that_does_not_become_active_is_service_failed(self) -> None:
        self.install_unit_file()
        self.stub_systemctl("sys.exit(3 if 'is-active' in argv else 0)\n")
        self.run_error("service_failed", 1, "kvm", "start")
        self.assertIn(f"--user start {UNIT_KVM}", self.calls())

    def test_failed_stream_stop_prevents_kvm_start(self) -> None:
        self.install_unit_file()
        for failing, untouched in ((UNIT_SUNSHINE, UNIT_DISPLAY), (UNIT_DISPLAY, None)):
            with self.subTest(failing=failing):
                self.fake_systemctl(active=[UNIT_SUNSHINE, UNIT_DISPLAY], fail=[f"stop {failing}"])
                self.log.unlink(missing_ok=True)
                self.run_error("service_failed", 1, "kvm", "start")
                self.assertEqual([line for line in self.calls() if f"start {UNIT_KVM}" in line], [])
                if untouched:
                    self.assertNotIn(f"--user stop {untouched}", self.calls())

    def test_start_with_extra_argument_is_usage(self) -> None:
        self.install_unit_file()
        self.fake_systemctl()
        self.run_error("usage", 64, "kvm", "start", "now")
        self.assertEqual(self.calls(), [])

    def test_start_without_a_hyprland_session_is_no_session_and_touches_no_unit(self) -> None:
        self.install_unit_file()
        self.fake_systemctl(active=[UNIT_SUNSHINE, UNIT_DISPLAY])
        unusable = {
            "no instance": "[]",
            "greeter-style entry without wl_socket": '[{"pid": 7, "instance": "g", "wl_socket": ""}]',
            "entry without instance": '[{"pid": 7, "instance": "", "wl_socket": "wayland-1"}]',
            "dead pid": '[{"pid": 0, "instance": "a", "wl_socket": "wayland-1"}]',
            "non-JSON": "not json",
            "not a list": '{"instance": "a"}',
        }
        for name, output in unusable.items():
            with self.subTest(name=name):
                self.fake_hyprctl(output)
                self.log.unlink(missing_ok=True)
                body = self.run_error("no_session", 1, "kvm", "start")
                self.assertEqual(body["command"], "kvm start")
                self.assertEqual(body["error"]["message"], "no Hyprland session is running for this user")
                self.assertEqual(self.calls(), [])
        self.fake_hyprctl(exit_code=1)
        self.run_error("no_session", 1, "kvm", "start")
        self.assertEqual(self.calls(), [])

    def test_start_without_a_hyprctl_binary_is_no_session(self) -> None:
        self.install_unit_file()
        self.fake_systemctl()
        self.env["HYPRCTL_BIN"] = str(self.root / "missing-hyprctl")
        self.run_error("no_session", 1, "kvm", "start")
        self.assertEqual(self.calls(), [])

    def test_start_picks_a_live_session_among_unusable_entries(self) -> None:
        self.install_unit_file()
        self.fake_systemctl()
        self.fake_hyprctl('[{"pid": 0, "instance": "old", "wl_socket": "wayland-0"},'
                          ' {"pid": 9, "instance": "new", "wl_socket": "wayland-1"}]')
        self.run_ok("kvm", "start")


class StopStatusTests(KvmCase):
    def test_stop_returns_inactive(self) -> None:
        self.fake_systemctl(active=[UNIT_KVM])
        body = self.run_ok("kvm", "stop")
        self.assertEqual(body, {"schema": 1, "ok": True, "command": "kvm stop", "service_active": False})
        self.assertIn(f"--user stop {UNIT_KVM}", self.calls())

    def test_stop_on_inactive_uninstalled_unit_is_idempotent(self) -> None:
        self.fake_systemctl()
        body = self.run_ok("kvm", "stop")
        self.assertEqual(body, {"schema": 1, "ok": True, "command": "kvm stop", "service_active": False})
        self.assertEqual([line for line in self.calls() if " stop " in f" {line} "], [])

    def test_stop_failure_is_service_failed(self) -> None:
        self.fake_systemctl(active=[UNIT_KVM], fail=[f"stop {UNIT_KVM}"])
        self.run_error("service_failed", 1, "kvm", "stop")

    def test_stop_that_leaves_the_unit_active_is_service_failed(self) -> None:
        self.stub_systemctl("print('active' if 'show' in argv else '')\nsys.exit(0)\n")
        self.run_error("service_failed", 1, "kvm", "stop")

    def test_stop_on_activating_unit_issues_stop(self) -> None:
        self.fake_systemctl(activating=[UNIT_KVM])
        body = self.run_ok("kvm", "stop")
        self.assertIs(body["service_active"], False)
        self.assertIn(f"--user stop {UNIT_KVM}", self.calls())

    def test_status_is_false_for_activating_unit(self) -> None:
        self.fake_systemctl(activating=[UNIT_KVM])
        body = self.run_ok("kvm", "status")
        self.assertIs(body["service_active"], False)

    def test_status_reports_both_states(self) -> None:
        self.fake_systemctl(active=[UNIT_KVM])
        body = self.run_ok("kvm", "status")
        self.assertEqual(body, {"schema": 1, "ok": True, "command": "kvm status", "service_active": True})
        (self.root / "state" / f"user:{UNIT_KVM}").unlink()
        body = self.run_ok("kvm", "status")
        self.assertIs(body["service_active"], False)

    def test_status_with_extra_argument_is_usage(self) -> None:
        self.fake_systemctl()
        self.run_error("usage", 64, "kvm", "status", "extra")


class UsageTests(KvmCase):
    def test_missing_or_unknown_subcommand_is_usage_with_bare_command(self) -> None:
        self.fake_systemctl()
        for argv in (("kvm",), ("kvm", "bogus"), ("kvm", "--help")):
            with self.subTest(argv=argv):
                body = self.run_error("usage", 64, *argv)
                self.assertEqual(body["command"], "kvm")
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
