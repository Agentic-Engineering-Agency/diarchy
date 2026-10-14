from __future__ import annotations

import unittest

from tests.test_install_host import LAN_MOUSE, InstallHostCase


class PreRenameMigrationTests(InstallHostCase):
    """B-07: an old `homelab-*` install is stopped, disabled and removed when the Diarchy installer runs."""

    def make_old_install(self) -> dict[str, str]:
        unit_dir = self.home / ".config/systemd/user"
        wants = unit_dir / "graphical-session.target.wants"
        wants.mkdir(parents=True)
        (unit_dir / "homelab-kvm.service").write_text("[Service]\nExecStart=%h/.local/bin/lan-mouse daemon\n")
        (unit_dir / "homelab-sunshine.service").write_text("[Service]\n")
        (wants / "homelab-kvm.service").symlink_to("../homelab-kvm.service")
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "homelab-host").write_text("#!/bin/sh\n")
        (bin_dir / "homelab-clipboard-remote").write_text("#!/bin/sh\n")
        (self.home / ".local/share/homelab-host/agent").mkdir(parents=True)
        (self.home / ".local/share/homelab").mkdir()
        (self.home / ".config/homelab-mirror").mkdir(parents=True)
        (self.home / ".config/homelab-mirror/display.env").write_text("x")
        keep = {
            "unit": unit_dir / "diarchy-kvm.service",
            "other_config": self.home / ".config/other/settings",
            "other_bin": bin_dir / "lan-mouse",
        }
        for path in keep.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("keep")
        return {name: str(path) for name, path in keep.items()}

    def test_old_units_files_and_folders_are_removed_and_each_path_is_printed(self) -> None:
        keep = self.make_old_install()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        removed = [
            ".config/systemd/user/homelab-kvm.service",
            ".config/systemd/user/homelab-sunshine.service",
            ".config/systemd/user/graphical-session.target.wants/homelab-kvm.service",
            ".local/bin/homelab-host",
            ".local/bin/homelab-clipboard-remote",
            ".local/share/homelab-host",
            ".local/share/homelab",
            ".config/homelab-mirror",
        ]
        for relative in removed:
            path = self.home / relative
            self.assertFalse(path.exists() or path.is_symlink(), relative)
            self.assertIn(str(path), result.stdout, relative)
        for path in keep.values():
            self.assertTrue(__import__("pathlib").Path(path).exists(), path)

    def test_each_old_unit_is_stopped_then_disabled_before_its_file_goes(self) -> None:
        self.make_old_install()
        self.run_install()
        calls = self.calls()
        for unit in ("homelab-kvm.service", "homelab-sunshine.service"):
            self.assertIn(f"--user stop {unit}", calls)
            self.assertIn(f"--user disable {unit}", calls)
            self.assertLess(calls.index(f"--user stop {unit}"), calls.index(f"--user disable {unit}"))
        self.assertIn("--user daemon-reload", calls)

    def test_migration_runs_before_the_component_installers(self) -> None:
        self.make_old_install()
        self.run_install()
        self.assertLess(self.calls().index("--user stop homelab-kvm.service"), self.calls().index(LAN_MOUSE))

    def test_a_host_without_an_old_install_prints_nothing_and_does_not_reload_systemd(self) -> None:
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("pre-rename", result.stdout)
        self.assertNotIn("--user daemon-reload", self.calls())

    def test_second_run_finds_nothing_left_to_remove(self) -> None:
        self.make_old_install()
        self.run_install()
        self.log.unlink()
        again = self.run_install()
        self.assertNotIn("pre-rename", again.stdout)
        self.assertEqual([c for c in self.calls() if "homelab" in c], [])

    def test_failed_stop_does_not_stop_the_migration(self) -> None:
        self.make_old_install()
        self.fake_systemctl(fail=["stop homelab-kvm.service"])
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / ".config/systemd/user/homelab-sunshine.service").exists())
        self.assertIn("homelab-kvm.service", result.stderr)


class FailureTextTests(InstallHostCase):
    def test_failed_component_installer_names_the_command_that_shows_the_full_output(self) -> None:  # B-22
        self.stub(LAN_MOUSE, code=1)
        result = self.run_install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] install-host.sh: install-lan-mouse.sh failed", result.stderr)
        self.assertIn("bash ", result.stderr)
        self.assertIn(LAN_MOUSE, result.stderr.split("failed", 1)[1])


if __name__ == "__main__":
    unittest.main()
