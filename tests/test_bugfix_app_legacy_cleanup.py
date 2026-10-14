from __future__ import annotations

import plistlib
import subprocess
import unittest
from pathlib import Path

import test_lan_mouse_client as base

INSTALLER = base.INSTALLER


class LegacyCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reuse the installer harness of test_lan_mouse_client: fake curl/ditto/codesign, temp HOME.
        self.harness = base.LanMouseClientInstallerTests("run_installer")
        self.home, self.env = self.harness.make_environment()
        self.addCleanup(self.harness.doCleanups)
        self.bin = self.home / ".local" / "bin"
        self.apps = self.home / "Applications"
        self.bin.mkdir(parents=True)
        self.apps.mkdir(parents=True)

    def make_app(self, name: str, bundle_id: str) -> Path:
        app = self.apps / name
        (app / "Contents").mkdir(parents=True)
        with (app / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleIdentifier": bundle_id}, handle)
        return app

    def run_installer(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(INSTALLER)], env=self.env, text=True, capture_output=True, check=False, timeout=120
        )

    def test_removes_pre_rename_commands_and_apps_and_prints_each_path(self) -> None:
        old_commands = [self.bin / "homelab-kvm", self.bin / "homelab-ssh", self.bin / "homelab-mirror"]
        for path in old_commands:
            path.write_text("#!/bin/sh\n")
        link = self.bin / "homelab-file"
        link.symlink_to(self.bin / "homelab-kvm")
        old_apps = [
            self.make_app("Home Lab Mirror.app", "dev.sebastiangmz.homelab-mirror"),
            self.make_app("HomeLab File Portal.app", "dev.sebastiangmz.homelab-file-portal"),
            self.make_app("Home Lab Drop.app", "dev.sebastiangmz.homelab-drop"),
        ]
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in [*old_commands, link, *old_apps]:
            self.assertFalse(path.exists() or path.is_symlink(), path)
            self.assertIn(f"[ok] Removed legacy {path}", result.stdout)

    def test_keeps_unrelated_files_and_foreign_bundles(self) -> None:
        other_tool = self.bin / "other-tool"
        other_tool.write_text("#!/bin/sh\n")
        near_miss = self.bin / "homelabs"
        near_miss.write_text("x")
        foreign = self.make_app("HomeLab Dashboard.app", "com.example.dashboard")
        unrelated = self.make_app("Moonlight.app", "com.moonlight-stream.Moonlight")
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (other_tool, near_miss, foreign, unrelated):
            self.assertTrue(path.exists(), path)
        self.assertIn("Leaving", result.stdout + result.stderr)
        self.assertIn(str(foreign), result.stdout + result.stderr)

    def test_a_clean_home_prints_no_removal_line(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Removed legacy", result.stdout)


if __name__ == "__main__":
    unittest.main()
