from __future__ import annotations

import itertools
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / "client" / "diarchy-kvm-configure"
LOADER = ROOT / "client" / "diarchy-config.sh"
MARKER = "# Managed by diarchy"
LAN_HOST = "192.0.2.10"
TAILSCALE_HOST = "100.64.0.10"
FINGERPRINT_UPPER = ":".join(["AB", "CD", "EF", "01"] * 8)
FINGERPRINT_LOWER = FINGERPRINT_UPPER.lower()
SIDES = ("left", "right", "top", "bottom")
HOST_SETS = {
    "both": (LAN_HOST, TAILSCALE_HOST),
    "lan": (LAN_HOST, ""),
    "tailscale": ("", TAILSCALE_HOST),
}


def expected_toml(side: str, ips: list[str], fingerprint: str | None) -> str:
    lines = [
        MARKER,
        'release_bind = ["KeyA", "KeyS", "KeyD", "KeyF"]',
        "port = 4242",
        "",
        "[authorized_fingerprints]",
    ]
    if fingerprint:
        lines.append(f'"{fingerprint}" = "host"')
    lines += [
        "",
        "[[clients]]",
        f'position = "{side}"',
        'hostname = "host"',
        "activate_on_startup = true",
        "ips = [" + ", ".join(f'"{ip}"' for ip in ips) + "]",
        "port = 4242",
    ]
    return "\n".join(lines) + "\n"


class KvmConfigureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(CONFIGURE.exists(), "client/diarchy-kvm-configure must exist")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.config_dir = self.home / ".config" / "lan-mouse"
        self.target = self.config_dir / "config.toml"
        self.diarchy_conf = self.home / "diarchy.conf"
        self.fake_bin = self.home / "fakebin"
        self.fake_bin.mkdir()

    def write_conf(
        self,
        side: str = "left",
        lan: str = LAN_HOST,
        tailscale: str = TAILSCALE_HOST,
        fingerprint: str = "",
    ) -> None:
        lines = [
            "# Managed by Diarchy",
            "DIARCHY_SSH_USER=labuser",
            f"DIARCHY_LAN_HOST={lan}",
            f"DIARCHY_TAILSCALE_HOST={tailscale}",
            f"DIARCHY_KVM_SIDE={side}",
        ]
        if fingerprint:
            lines.append(f"DIARCHY_HOST_FINGERPRINT={fingerprint}")
        self.diarchy_conf.write_text("\n".join(lines) + "\n")

    def environment(self) -> dict[str, str]:
        return {
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "DIARCHY_CONFIG": str(self.diarchy_conf),
        }

    def run_configure(
        self, *, umask: str | None = None, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = self.environment()
        if extra_env:
            env.update(extra_env)
        command = [str(CONFIGURE)]
        if umask is not None:
            command = ["/bin/sh", "-c", f'umask {umask}; exec "$0"', str(CONFIGURE)]
        return subprocess.run(
            command, env=env, text=True, capture_output=True, check=False
        )

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def backups(self) -> list[Path]:
        return sorted(self.config_dir.glob("config.toml.backup.*"))

    def test_renders_byte_exact_for_every_side_host_set_and_fingerprint(self) -> None:
        for side, (host_name, (lan, tail)), fingerprint in itertools.product(
            SIDES, HOST_SETS.items(), (FINGERPRINT_UPPER, "")
        ):
            with self.subTest(side=side, hosts=host_name, fingerprint=bool(fingerprint)):
                if self.config_dir.exists():
                    for child in self.config_dir.iterdir():
                        child.unlink()
                self.write_conf(side, lan, tail, fingerprint)
                result = self.run_configure()
                self.assertEqual(result.returncode, 0, result.stderr)
                ips = [ip for ip in (lan, tail) if ip]
                expected = expected_toml(
                    side, ips, FINGERPRINT_LOWER if fingerprint else None
                )
                self.assertEqual(self.target.read_text(), expected)
                self.assertEqual(
                    result.stdout.splitlines()[-1],
                    f"[ok] Lan Mouse configuration written (side: {side})",
                )

    def test_fingerprint_is_lowercase_in_toml_even_when_conf_is_uppercase(self) -> None:
        self.write_conf(fingerprint=FINGERPRINT_UPPER)
        result = self.run_configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.target.read_text()
        self.assertIn(f'"{FINGERPRINT_LOWER}" = "host"', text)
        self.assertNotIn(FINGERPRINT_UPPER, text)

    def test_creates_config_dir_0700_and_file_0600_under_open_umask(self) -> None:
        self.write_conf()
        result = self.run_configure(umask="000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mode(self.config_dir), 0o700)
        self.assertEqual(self.mode(self.target), 0o600)

    def test_honours_lan_mouse_config_override(self) -> None:
        self.write_conf()
        override = self.home / "elsewhere" / "nested" / "custom.toml"
        result = self.run_configure(extra_env={"LAN_MOUSE_CONFIG": str(override)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(override.exists())
        self.assertFalse(self.target.exists())

    def test_honours_xdg_config_home(self) -> None:
        self.write_conf()
        xdg = self.home / "xdg"
        result = self.run_configure(extra_env={"XDG_CONFIG_HOME": str(xdg)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((xdg / "lan-mouse" / "config.toml").exists())

    def test_second_run_is_idempotent_and_creates_no_backup(self) -> None:
        self.write_conf(fingerprint=FINGERPRINT_UPPER)
        first = self.run_configure()
        content = self.target.read_text()
        second = self.run_configure()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.target.read_text(), content)
        self.assertEqual(self.backups(), [])
        self.assertNotIn("[info]", second.stdout)
        self.assertEqual(self.mode(self.target), 0o600)

    def test_unmanaged_file_is_backed_up_with_0600_before_replacement(self) -> None:
        self.write_conf()
        self.config_dir.mkdir(parents=True)
        original = 'port = 9999\n# hand written\n'
        self.target.write_text(original)
        self.target.chmod(0o644)
        result = self.run_configure(umask="000")
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertRegex(
            backups[0].name, r"^config\.toml\.backup\.\d{8}T\d{6}Z$"
        )
        self.assertEqual(backups[0].read_text(), original)
        self.assertEqual(self.mode(backups[0]), 0o600)
        self.assertEqual(
            self.target.read_text(), expected_toml("left", [LAN_HOST, TAILSCALE_HOST], None)
        )
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[0], f"[info] Preserved existing Lan Mouse config at {backups[0]}"
        )
        self.assertEqual(lines[-1], "[ok] Lan Mouse configuration written (side: left)")

    def test_two_unmanaged_runs_within_one_second_do_not_clobber_backups(self) -> None:
        self.write_conf()
        fake_date = self.fake_bin / "date"
        fake_date.write_text("#!/bin/sh\nprintf '%s\\n' 20260101T000000Z\n")
        fake_date.chmod(0o755)
        self.config_dir.mkdir(parents=True)
        for index, content in enumerate(("first = 1\n", "second = 2\n", "third = 3\n")):
            self.target.write_text(content)
            result = self.run_configure()
            self.assertEqual(result.returncode, 0, result.stderr)
        base = self.config_dir / "config.toml.backup.20260101T000000Z"
        self.assertEqual(base.read_text(), "first = 1\n")
        self.assertEqual(Path(f"{base}.1").read_text(), "second = 2\n")
        self.assertEqual(Path(f"{base}.2").read_text(), "third = 3\n")
        self.assertEqual(len(self.backups()), 3)
        for backup in self.backups():
            self.assertEqual(self.mode(backup), 0o600)

    def test_missing_config_reports_loader_reason_and_leaves_target_untouched(self) -> None:
        self.config_dir.mkdir(parents=True)
        self.target.write_text("untouched = true\n")
        env = self.environment()
        env["DIARCHY_CONFIG"] = str(self.home / "absent.conf")
        result = subprocess.run(
            [str(CONFIGURE)], env=env, text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr.strip(),
            "diarchy-config: Diarchy is not configured (open the app to set it up)",
        )
        self.assertNotIn("[ok]", result.stdout)
        self.assertEqual(self.target.read_text(), "untouched = true\n")
        self.assertEqual([p.name for p in self.config_dir.iterdir()], ["config.toml"])

    def test_invalid_config_reports_loader_reason_and_creates_nothing(self) -> None:
        self.diarchy_conf.write_text("not the header\n")
        result = self.run_configure()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr.strip(),
            'diarchy-config: first line must be "# Managed by Diarchy"',
        )
        self.assertFalse(self.config_dir.exists())

    def test_leaves_no_temporary_files_behind(self) -> None:
        self.write_conf(fingerprint=FINGERPRINT_UPPER)
        self.config_dir.mkdir(parents=True)
        self.target.write_text("unmanaged = true\n")
        for _ in range(2):
            result = self.run_configure()
            self.assertEqual(result.returncode, 0, result.stderr)
        names = sorted(p.name for p in self.config_dir.iterdir())
        self.assertEqual(len(names), 2, names)
        self.assertIn("config.toml", names)
        self.assertTrue(
            any(n.startswith("config.toml.backup.") for n in names), names
        )

    def test_honours_diarchy_config_sh_override(self) -> None:
        self.write_conf()
        fake_loader = self.home / "fake-loader.sh"
        fake_loader.write_text(
            "diarchy_config_load() {\n"
            "  export DIARCHY_LAN_HOST=198.51.100.7 DIARCHY_TAILSCALE_HOST= "
            "DIARCHY_KVM_SIDE=top DIARCHY_HOST_FINGERPRINT=\n"
            "}\n"
        )
        result = self.run_configure(extra_env={"DIARCHY_CONFIG_SH": str(fake_loader)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.target.read_text(), expected_toml("top", ["198.51.100.7"], None)
        )

    def test_default_loader_is_the_sibling_script(self) -> None:
        self.assertTrue(LOADER.exists())
        self.write_conf(side="bottom")
        result = self.run_configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('position = "bottom"', self.target.read_text())


    @unittest.skipIf(os.geteuid() == 0, "permission checks are meaningless as root")
    def test_unreadable_unmanaged_config_fails_cleanly_without_junk_backups(self) -> None:
        self.write_conf()
        self.config_dir.mkdir(parents=True)
        self.target.write_text("unmanaged = true\n")
        self.target.chmod(0o000)
        self.addCleanup(self.target.chmod, 0o600)
        result = self.run_configure()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            f"diarchy-kvm-configure: cannot back up existing configuration: {self.target}\n",
        )
        self.assertNotIn("[ok]", result.stdout)
        self.assertEqual([p.name for p in self.config_dir.iterdir()], ["config.toml"])
        self.target.chmod(0o600)
        self.assertEqual(self.target.read_text(), "unmanaged = true\n")

    def test_config_target_that_is_a_directory_is_rejected(self) -> None:
        self.write_conf()
        self.target.mkdir(parents=True)
        result = self.run_configure()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr, f"diarchy-kvm-configure: {self.target} is a directory\n"
        )
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual([p.name for p in self.config_dir.iterdir()], ["config.toml"])

    def test_failed_atomic_replace_reports_reason_and_leaves_no_temp_file(self) -> None:
        self.write_conf()
        self.config_dir.mkdir(parents=True)
        self.target.write_text(f"{MARKER}\nold = true\n")
        fake_mv = self.fake_bin / "mv"
        fake_mv.write_text("#!/bin/sh\necho 'mv: simulated failure' >&2\nexit 1\n")
        fake_mv.chmod(0o755)
        result = self.run_configure()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            f"diarchy-kvm-configure: cannot write configuration: {self.target}\n",
        )
        self.assertNotIn("[ok]", result.stdout)
        self.assertEqual(self.target.read_text(), f"{MARKER}\nold = true\n")
        self.assertEqual([p.name for p in self.config_dir.iterdir()], ["config.toml"])

    def test_failed_mode_change_on_stage_reports_reason_and_leaves_target_untouched(self) -> None:
        # chmod is the first command of the render step; a failure there used to be masked
        # because the render group ran inside an `||` list, where set -e is ignored.
        self.write_conf()
        self.config_dir.mkdir(parents=True)
        self.target.write_text(f"{MARKER}\nold = true\n")
        fake_chmod = self.fake_bin / "chmod"
        fake_chmod.write_text("#!/bin/sh\necho 'chmod: simulated failure' >&2\nexit 1\n")
        fake_chmod.chmod(0o755)
        result = self.run_configure()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            f"diarchy-kvm-configure: cannot write configuration: {self.target}\n",
        )
        self.assertNotIn("[ok]", result.stdout)
        self.assertEqual(self.target.read_text(), f"{MARKER}\nold = true\n")
        self.assertEqual([p.name for p in self.config_dir.iterdir()], ["config.toml"])

    @unittest.skipIf(os.geteuid() == 0, "permission checks are meaningless as root")
    def test_unwritable_config_dir_reports_reason(self) -> None:
        self.write_conf()
        self.config_dir.mkdir(parents=True)
        self.config_dir.chmod(0o500)
        self.addCleanup(self.config_dir.chmod, 0o700)
        result = self.run_configure()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            f"diarchy-kvm-configure: cannot write configuration: {self.target}\n",
        )
        self.assertEqual(list(self.config_dir.iterdir()), [])

    @unittest.skipIf(os.geteuid() == 0, "permission checks are meaningless as root")
    def test_uncreatable_config_dir_reports_reason(self) -> None:
        self.write_conf()
        locked = self.home / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        result = self.run_configure(
            extra_env={"LAN_MOUSE_CONFIG": str(locked / "sub" / "config.toml")}
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            f"diarchy-kvm-configure: cannot create configuration directory: {locked / 'sub'}\n",
        )

if __name__ == "__main__":
    unittest.main()
