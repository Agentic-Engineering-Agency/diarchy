from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "host"
INSTALLER = HOST / "install-lan-mouse.sh"
DOCTOR = HOST / "diarchy-kvm-host-doctor"
UNIT = HOST / "systemd" / "diarchy-kvm.service"
VERSION = "0.11.0"
SHA256 = "c43d211ec1af3eaea0cd585ac5c415776000355c3fcb9742fa2513f32dab55ba"
MAC_FINGERPRINT = ":".join(["aa"] * 32)
MAC_FINGERPRINT_UPPER = MAC_FINGERPRINT.upper()
MAC_FINGERPRINT_MIXED = ":".join(["aA"] * 32)
AGENT = HOST / "diarchy-host"


class LanMouseHostTests(unittest.TestCase):
    def make_environment(
        self, *, checksum_ok: bool = True, agent: str = "real"
    ) -> tuple[Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        home = root / "home"
        tools = root / "tools"
        home.mkdir()
        tools.mkdir()

        curl = tools / "curl"
        checksum = tools / "sha256sum"
        uname = tools / "uname"
        systemctl = tools / "systemctl"
        systemctl_log = root / "systemctl.log"

        curl.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "output.write_text('#!/usr/bin/env bash\\n'"
            "                  'if [[ ${1:-} == --version ]]; then\\n'"
            "                  '  echo lan-mouse 0.11.0\\n'"
            "                  '  exit 0\\n'"
            "                  'fi\\n'"
            "                  'exit 0\\n')\n"
        )
        checksum.write_text(
            "#!/usr/bin/env bash\n"
            "cat >/dev/null\n"
            f"exit {0 if checksum_ok else 1}\n"
        )
        uname.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ ${1:-} == -s ]]; then echo Linux; else echo x86_64; fi\n"
        )
        systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >>\"$FAKE_SYSTEMCTL_LOG\"\n"
            'if [[ ${2:-} == show ]]; then\n'
            '  for unit in ${FAKE_RUNNING_UNITS:-}; do\n'
            '    [[ ${!#} == "$unit" ]] && echo active\n'
            "  done\n"
            "fi\n"
            "exit 0\n"
        )
        failing_agent = tools / "diarchy-host-failing"
        failing_agent.write_text(
            "#!/usr/bin/env bash\n"
            'printf \'%s\\n\' \'{"schema":1,"ok":false,"command":"kvm",'
            '"error":{"code":"config_write_failed","message":"boom"}}\'\n'
            "exit 1\n"
        )
        for executable in (curl, checksum, uname, systemctl, failing_agent):
            executable.chmod(0o755)

        agent_path = {
            "real": AGENT,
            "failing": failing_agent,
            "missing": tools / "diarchy-host-missing",
        }[agent]
        environment = dict(os.environ)
        for inherited in ("LAN_MOUSE_CONFIG", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            environment.pop(inherited, None)
        return home, environment | {
            "HOME": str(home),
            "CURL_BIN": str(curl),
            "SHA256_BIN": str(checksum),
            "UNAME_BIN": str(uname),
            "SYSTEMCTL_BIN": str(systemctl),
            "FAKE_SYSTEMCTL_LOG": str(systemctl_log),
            "DIARCHY_HOST_BIN": str(agent_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run_installer(
        self,
        *args: str,
        checksum_ok: bool = True,
        agent: str = "real",
    ) -> tuple[Path, subprocess.CompletedProcess[str], str]:
        self.assertTrue(INSTALLER.exists(), "host/install-lan-mouse.sh must exist")
        home, environment = self.make_environment(checksum_ok=checksum_ok, agent=agent)
        result = subprocess.run(
            [str(INSTALLER), *args],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        log = Path(environment["FAKE_SYSTEMCTL_LOG"])
        return home, result, log.read_text() if log.exists() else ""

    def test_dry_run_is_rootless_pinned_and_non_mutating(self) -> None:
        self.assertTrue(INSTALLER.exists(), "host/install-lan-mouse.sh must exist")
        with tempfile.TemporaryDirectory() as temp_home:
            result = subprocess.run(
                [str(INSTALLER), "--dry-run"],
                env=os.environ | {"HOME": temp_home},
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Lan Mouse {VERSION}", result.stdout)
            self.assertIn(SHA256, result.stdout)
            self.assertIn("UDP 4242", result.stdout)
            self.assertIn(".local/opt/lan-mouse-0.11.0", result.stdout)
            self.assertIn("through the diarchy-host agent", result.stdout)
            self.assertNotIn("sudo", result.stdout.lower())
            self.assertEqual(list(Path(temp_home).iterdir()), [])

    def test_rejects_malformed_fingerprint_before_mutation(self) -> None:
        home, result, systemctl_log = self.run_installer(
            "--authorize-fingerprint", "not-a-fingerprint"
        )

        self.assertEqual(result.returncode, 64)
        self.assertIn("fingerprint", result.stderr.lower())
        self.assertFalse((home / ".local").exists())
        self.assertFalse((home / ".config").exists())
        self.assertEqual(systemctl_log, "")

    def test_checksum_failure_prevents_installation(self) -> None:
        home, result, systemctl_log = self.run_installer(checksum_ok=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stderr.lower())
        self.assertFalse((home / ".local" / "bin" / "lan-mouse").exists())
        self.assertEqual(systemctl_log, "")

    def test_install_is_idempotent_and_writes_trusted_receiver_config(self) -> None:
        self.assertTrue(INSTALLER.exists(), "host/install-lan-mouse.sh must exist")
        home, environment = self.make_environment()
        commands = [str(INSTALLER), "--authorize-fingerprint", MAC_FINGERPRINT]

        first = subprocess.run(
            commands, env=environment, text=True, capture_output=True, check=False
        )
        second = subprocess.run(
            commands, env=environment, text=True, capture_output=True, check=False
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        binary = home / ".local" / "bin" / "lan-mouse"
        config_dir = home / ".config" / "lan-mouse"
        config = config_dir / "config.toml"
        unit = home / ".config" / "systemd" / "user" / "diarchy-kvm.service"
        doctor = home / ".local" / "bin" / "diarchy-kvm-host-doctor"
        self.assertTrue(binary.is_file())
        self.assertTrue(unit.is_file())
        self.assertTrue(doctor.is_file())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        source = config.read_text()
        self.assertIn("# Managed by diarchy", source)
        self.assertIn(f'"{MAC_FINGERPRINT}" = "mac"', source)
        self.assertIn("port = 4242", source)
        self.assertIn('capture_backend = "layer-shell"', source)
        self.assertNotIn("[[clients]]", source)
        self.assertEqual(sorted(path.name for path in config_dir.glob("config.toml*")), ["config.toml"])
        calls = Path(environment["FAKE_SYSTEMCTL_LOG"]).read_text().splitlines()
        reload_index = calls.index("--user daemon-reload")
        reenable_index = calls.index("--user reenable diarchy-kvm.service")
        restart_index = len(calls) - 1 - calls[::-1].index("--user restart diarchy-kvm.service")
        self.assertLess(reload_index, reenable_index)
        self.assertLess(reenable_index, restart_index)
        self.assertIn("--user is-active --quiet diarchy-kvm.service", calls)

    def test_without_flag_config_has_no_authorized_fingerprint_and_prints_hint(self) -> None:
        home, result, _ = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stderr)
        source = (home / ".config" / "lan-mouse" / "config.toml").read_text()
        self.assertIn("[authorized_fingerprints]", source)
        self.assertNotIn('= "mac"', source)
        self.assertIn(
            "[next] Pair this host from the Diarchy app (Settings → Connection → Pair).", result.stdout)
        self.assertNotIn("--authorize-fingerprint", result.stdout)

    def test_streaming_unit_running_skips_the_kvm_restart_but_still_reenables(self) -> None:
        for running in ("diarchy-sunshine.service", "diarchy-display.service"):
            with self.subTest(running=running):
                home, environment = self.make_environment()
                result = subprocess.run(
                    [str(INSTALLER)], env=environment | {"FAKE_RUNNING_UNITS": running},
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = Path(environment["FAKE_SYSTEMCTL_LOG"]).read_text().splitlines()
                self.assertIn("--user reenable diarchy-kvm.service", calls)
                self.assertNotIn("--user restart diarchy-kvm.service", calls)
                self.assertNotIn("--user is-active --quiet diarchy-kvm.service", calls)
                self.assertIn("Streaming is running", result.stdout)

    def test_without_flag_preserves_existing_authorized_fingerprint(self) -> None:
        home, environment = self.make_environment()
        authorized = subprocess.run(
            [str(INSTALLER), "--authorize-fingerprint", MAC_FINGERPRINT],
            env=environment, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stderr)

        again = subprocess.run(
            [str(INSTALLER)], env=environment, text=True, capture_output=True, check=False
        )

        self.assertEqual(again.returncode, 0, again.stderr)
        source = (home / ".config" / "lan-mouse" / "config.toml").read_text()
        self.assertIn(f'"{MAC_FINGERPRINT}" = "mac"', source)

    def test_mixed_case_fingerprint_is_accepted_and_normalized(self) -> None:
        home, result, _ = self.run_installer("--authorize-fingerprint", MAC_FINGERPRINT_MIXED)

        self.assertEqual(result.returncode, 0, result.stderr)
        source = (home / ".config" / "lan-mouse" / "config.toml").read_text()
        self.assertIn(f'"{MAC_FINGERPRINT}" = "mac"', source)

    def test_missing_agent_fails_before_any_mutation(self) -> None:
        home, result, systemctl_log = self.run_installer(agent="missing")

        self.assertEqual(result.returncode, 1)
        self.assertIn("diarchy-host agent is not installed", result.stderr)
        self.assertIn("install-host.sh", result.stderr)
        self.assertFalse((home / ".local").exists())
        self.assertFalse((home / ".config").exists())
        self.assertEqual(systemctl_log, "")

    def test_failing_agent_fails_the_installer_before_service_restart(self) -> None:
        _, result, systemctl_log = self.run_installer(agent="failing")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kvm configure failed", result.stderr)
        self.assertIn("config_write_failed", result.stderr)
        self.assertNotIn("restart", systemctl_log)
        self.assertNotIn("daemon-reload", systemctl_log)

    def test_unmanaged_config_is_backed_up_byte_for_byte(self) -> None:
        home, environment = self.make_environment()
        config_dir = home / ".config" / "lan-mouse"
        config_dir.mkdir(parents=True)
        original = '[[clients]]\nhostname = "example.invalid"\n'
        (config_dir / "config.toml").write_text(original)

        result = subprocess.run(
            [str(INSTALLER)], env=environment, text=True, capture_output=True, check=False
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list(config_dir.glob("config.toml.backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), original)
        self.assertIn("# Managed by diarchy", (config_dir / "config.toml").read_text())

    def test_refuses_foreign_version_directory(self) -> None:
        self.assertTrue(INSTALLER.exists(), "host/install-lan-mouse.sh must exist")
        home, environment = self.make_environment()
        target = home / ".local" / "opt" / f"lan-mouse-{VERSION}"
        target.mkdir(parents=True)
        sentinel = target / "foreign"
        sentinel.write_text("preserve me")

        result = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to replace", result.stderr.lower())
        self.assertEqual(sentinel.read_text(), "preserve me")

    def test_service_is_graphical_rootless_and_restart_bounded(self) -> None:
        self.assertTrue(UNIT.exists(), "host/systemd/diarchy-kvm.service must exist")
        source = UNIT.read_text()

        self.assertIn("After=graphical-session.target", source)
        self.assertIn("BindsTo=graphical-session.target", source)
        self.assertIn("ExecStart=%h/.local/bin/lan-mouse daemon", source)
        self.assertIn("Restart=on-failure", source)
        self.assertIn("WantedBy=graphical-session.target", source)
        self.assertNotIn("sudo", source.lower())

    def make_doctor_environment(self, root: Path) -> tuple[Path, dict[str, str]]:
        config = root / "config.toml"
        certificate = root / "lan-mouse.pem"
        binary = root / "lan-mouse"
        systemctl = root / "systemctl"
        sockets = root / "ss"
        openssl = root / "openssl"
        stat = root / "stat"
        agent = root / "diarchy-host"

        config.write_text(
            "port = 4242\n"
            'capture_backend = "layer-shell"\n'
            "[authorized_fingerprints]\n"
            f'"{MAC_FINGERPRINT}" = "mac"\n'
        )
        config.chmod(0o600)
        certificate.write_text("certificate")
        binary.write_text("#!/usr/bin/env bash\necho 'lan-mouse 0.11.0'\n")
        systemctl.write_text("#!/usr/bin/env bash\nexit 0\n")
        sockets.write_text(
            "#!/usr/bin/env bash\n"
            "echo 'UNCONN 0 0 0.0.0.0:4242 0.0.0.0:*'\n"
        )
        openssl.write_text(
            "#!/usr/bin/env bash\n"
            f"echo 'sha256 Fingerprint={MAC_FINGERPRINT_UPPER}'\n"
        )
        stat.write_text("#!/usr/bin/env bash\necho 600\n")
        agent.write_text(
            "#!/usr/bin/env bash\n"
            'printf \'%s\\n\' \'{"schema":1,"ok":true,"command":"version"}\'\n'
        )
        for executable in (binary, systemctl, sockets, openssl, stat, agent):
            executable.chmod(0o755)

        return config, os.environ | {
            "LAN_MOUSE_BIN": str(binary),
            "LAN_MOUSE_CONFIG": str(config),
            "LAN_MOUSE_CERT": str(certificate),
            "SYSTEMCTL_BIN": str(systemctl),
            "SS_BIN": str(sockets),
            "OPENSSL_BIN": str(openssl),
            "STAT_BIN": str(stat),
            "DIARCHY_HOST_BIN": str(agent),
        }

    def run_doctor(self, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DOCTOR)], env=environment, text=True, capture_output=True, check=False
        )

    def test_doctor_checks_runtime_security_contract(self) -> None:
        self.assertTrue(DOCTOR.exists(), "host/diarchy-kvm-host-doctor must exist")
        with tempfile.TemporaryDirectory() as temp_dir:
            config, environment = self.make_doctor_environment(Path(temp_dir))

            result = self.run_doctor(environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("diarchy-host agent present", result.stdout)
            self.assertIn("Diarchy KVM host is healthy", result.stdout)

            config.write_text(
                'port = 4242\ncapture_backend = "layer-shell"\n[authorized_fingerprints]\n'
            )
            rejected = self.run_doctor(environment)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("trusted Mac fingerprint is missing", rejected.stderr)

    def test_doctor_fails_without_the_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, environment = self.make_doctor_environment(root)
            missing = root / "absent-diarchy-host"

            result = self.run_doctor(environment | {"DIARCHY_HOST_BIN": str(missing)})

            self.assertEqual(result.returncode, 1)
            self.assertIn("diarchy-host agent is missing or unhealthy", result.stderr)
            self.assertIn(str(missing), result.stderr)

    def test_doctor_fails_when_the_agent_is_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, environment = self.make_doctor_environment(root)
            Path(environment["DIARCHY_HOST_BIN"]).write_text("#!/usr/bin/env bash\nexit 1\n")

            result = self.run_doctor(environment)

            self.assertEqual(result.returncode, 1)
            self.assertIn("diarchy-host agent is missing or unhealthy", result.stderr)


if __name__ == "__main__":
    unittest.main()
