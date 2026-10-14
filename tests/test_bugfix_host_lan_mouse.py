from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

from tests import test_lan_mouse_host as base

NEW_BINARY = "#!/usr/bin/env bash\nif [[ ${1:-} == --version ]]; then echo lan-mouse 0.11.0; fi\n"


class LanMouseInstallerCase(unittest.TestCase):
    # Reuse the sandbox builder of the existing suite without inheriting its tests.
    make_environment = base.LanMouseHostTests.make_environment

    def run_installer(self, home_setup=None, *, systemctl: str | None = None, curl_args_log: bool = False):
        home, environment = self.make_environment()
        tools = Path(environment["CURL_BIN"]).parent
        if systemctl is not None:
            Path(environment["SYSTEMCTL_BIN"]).write_text("#!/usr/bin/env bash\n" + systemctl)
        if curl_args_log:
            Path(environment["CURL_BIN"]).write_text(
                "#!/usr/bin/env bash\n"
                'echo "$*" >>"$FAKE_CURL_LOG"\n'
                'quiet=; out=\n'
                'while (( $# )); do case $1 in --output) out=$2; shift;; -sS) quiet=1;; esac; shift; done\n'
                '[[ -n $quiet ]] || echo "  % Total    % Received" >&2\n'
                "printf '#!/usr/bin/env bash\\nif [[ ${1:-} == --version ]]; then echo lan-mouse 0.11.0; fi\\n' >\"$out\"\n"
            )
            environment["FAKE_CURL_LOG"] = str(tools.parent / "curl.log")
        if home_setup:
            home_setup(home)
        result = subprocess.run([str(base.INSTALLER)], env=environment, text=True, capture_output=True, check=False)
        return home, result


class DownloadTests(LanMouseInstallerCase):
    def test_download_prints_no_progress_meter(self) -> None:  # B-10
        _, result = self.run_installer(curl_args_log=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("% Total", result.stderr)


class BackupTests(LanMouseInstallerCase):
    def test_foreign_lan_mouse_binary_is_backed_up(self) -> None:  # B-12
        def setup(home: Path) -> None:
            (home / ".local/bin").mkdir(parents=True)
            (home / ".local/bin/lan-mouse").write_text("#!/bin/sh\necho my own build\n")

        home, result = self.run_installer(setup)
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = sorted((home / ".local/bin").glob("lan-mouse.diarchy-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "#!/bin/sh\necho my own build\n")
        self.assertIn(str(backups[0]), result.stdout)

    def test_binary_from_an_earlier_diarchy_version_is_not_backed_up(self) -> None:  # B-12
        def setup(home: Path) -> None:
            old = home / ".local/opt/lan-mouse-0.10.0"
            old.mkdir(parents=True)
            (old / "lan-mouse").write_text("#!/bin/sh\necho 0.10.0\n")
            (home / ".local/bin").mkdir(parents=True)
            (home / ".local/bin/lan-mouse").write_text("#!/bin/sh\necho 0.10.0\n")

        home, result = self.run_installer(setup)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list((home / ".local/bin").glob("lan-mouse.diarchy-backup-*")), [])


class PruneTests(LanMouseInstallerCase):
    def seed_old(self, home: Path) -> None:
        (home / ".local/opt/lan-mouse-0.10.0").mkdir(parents=True)
        (home / ".local/opt/lan-mouse-custom").mkdir()

    def test_old_versions_are_removed_after_a_verified_install(self) -> None:  # B-32
        home, result = self.run_installer(self.seed_old)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((home / ".local/opt/lan-mouse-0.10.0").exists())
        self.assertIn(str(home / ".local/opt/lan-mouse-0.10.0"), result.stdout)
        self.assertTrue((home / ".local/opt/lan-mouse-0.11.0/lan-mouse").exists())
        self.assertTrue((home / ".local/opt/lan-mouse-custom").exists())

    def test_old_versions_stay_when_the_service_does_not_start(self) -> None:  # B-32
        home, result = self.run_installer(
            self.seed_old,
            systemctl='[[ $2 == is-active ]] && exit 3\nexit 0\n',
        )
        self.assertEqual(result.returncode, 1)
        self.assertTrue((home / ".local/opt/lan-mouse-0.10.0").exists())


class FailureTextTests(LanMouseInstallerCase):
    def test_inactive_service_error_names_the_journal_command(self) -> None:  # B-22
        _, result = self.run_installer(systemctl='[[ $2 == is-active ]] && exit 3\nexit 0\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not become active", result.stderr)
        self.assertIn("journalctl --user -u diarchy-kvm.service", result.stderr)

    def test_failed_restart_prints_a_fail_line(self) -> None:  # B-11
        _, result = self.run_installer(systemctl='[[ $2 == restart ]] && exit 1\nexit 0\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn("install-lan-mouse.sh: systemctl --user restart diarchy-kvm.service failed", result.stderr)
        self.assertIn("journalctl --user -u diarchy-kvm.service", result.stderr)


if __name__ == "__main__":
    unittest.main()
