from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_diarchy_host_support import HOST, AgentCase

VERSION = "2026.516.143833"
LAUNCHER_MARKER = "# DIARCHY_LIBVA_PATH marks the launcher that Diarchy installed\n"

FAKE_APPIMAGE = """#!/usr/bin/env bash
if [[ ${1:-} == --appimage-extract ]]; then
  mkdir -p squashfs-root
  printf '#!/usr/bin/env bash\\necho "Sunshine version: %s"\\n' "$FAKE_VERSION" >squashfs-root/AppRun
  chmod +x squashfs-root/AppRun
fi
"""

FAKE_LAUNCHER = LAUNCHER_MARKER + """#!/usr/bin/env bash
case ${1:-} in
  --version) echo "Sunshine version: %s" ;;
  --creds)
    [[ -z ${FAKE_CREDS_FAIL:-} ]] || { echo "creds failed for password $3" >&2; exit 1; }
    echo creds >>"$FAKE_LOG"
    printf '{}' >"$HOME/.config/sunshine/sunshine_state.json"
    ;;
esac
""" % VERSION


class InstallerCase(AgentCase):
    """Runs the real host/install.sh from a temp payload on a temp HOME with fake system tools."""

    def setUp(self) -> None:
        super().setUp()
        self.payload = self.root / "payload" / "host"
        self.payload.mkdir(parents=True)
        for name in ("install.sh", "diarchy-host-doctor", "sunshine.conf", "apps.json", "diarchy-check-sunshine-log"):
            shutil.copy2(HOST / name, self.payload / name)
        shutil.copytree(HOST / "systemd", self.payload / "systemd")
        self.script("payload/host/sunshine", FAKE_LAUNCHER)
        self.script("payload/host/diarchy-display", '[[ -z ${FAKE_NO_HYPR:-} ]] || exit 1\necho eDP-1\n')
        self.script(
            "payload/host/diarchy-check-sunshine-service",
            '[[ -z ${FAKE_ENCODER_FAIL:-} ]] || exit 1\n[[ ${1:-} == --encoder ]] && echo vaapi || echo eDP-1\n',
        )
        self.script("payload/host/diarchy-host", "exit 0\n")
        self.script("tools/uname", '[[ ${1:-} == -m ]] && echo x86_64 || echo Linux\n')
        self.script("tools/sha256sum", "cat >/dev/null\n")
        self.script("tools/jq", "exit 0\n")
        self.script("tools/socat", "exit 0\n")
        self.script("tools/openssl", "echo hunter2secret\n")
        self.script("tools/hyprctl", "exit 0\n")
        self.script("tools/sleep", "exit 0\n")
        self.script("tools/systemd-analyze", '[[ -z ${FAKE_LID_IGNORED:-} ]] || echo HandleLidSwitch=ignore\n')
        self.script(
            "tools/curl",
            'echo "curl $*" >>"$FAKE_LOG"\n'
            'out=; quiet=\n'
            'while (( $# )); do case $1 in --output) out=$2; shift;; -sS|-s|--silent) quiet=1;; esac; shift; done\n'
            '[[ -n $quiet ]] || echo "  % Total    % Received % Xferd  Average Speed" >&2\n'
            '[[ -z ${FAKE_CURL_FAIL:-} ]] || exit 22\n'
            'cp "$FAKE_APPIMAGE" "$out"\n',
        )
        (self.root / "appimage").write_text(FAKE_APPIMAGE)
        self.dev = self.root / "dev"
        (self.dev / "dri").mkdir(parents=True)
        (self.dev / "dri/renderD128").write_text("")
        (self.dev / "uinput").write_text("")
        self.proc = self.root / "proc"
        self.systemctl()
        self.env.update(
            PATH=f"{self.root / 'tools'}:{self.tools}:/usr/bin:/bin",
            DIARCHY_DEV_ROOT=str(self.dev),
            DIARCHY_PROC_ROOT=str(self.proc),
            FAKE_APPIMAGE=str(self.root / "appimage"),
            FAKE_VERSION=VERSION,
            XDG_CACHE_HOME=str(self.root / "cache"),
        )
        self.opt = self.home / ".local/opt"
        self.bin = self.home / ".local/bin"
        self.config = self.home / ".config/sunshine"

    def script(self, relative: str, body: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = body if body.startswith("#!") or body.startswith("# ") else "#!/usr/bin/env bash\n" + body
        path.write_text(text)
        path.chmod(0o755)

    def systemctl(self, active: tuple[str, ...] = (), fail: tuple[str, ...] = ()) -> None:
        """A stateful `systemctl --user` that handles several units per call. `fail` holds "<verb> <unit>" pairs."""
        state = self.root / "state"
        shutil.rmtree(state, ignore_errors=True)
        state.mkdir()
        for unit in active:
            (state / unit).write_text("")
        (self.root / "systemctl.fail").write_text("\n".join(fail) + "\n")
        self.script(
            "tools/systemctl",
            f'state={str(state)!r}\n'
            'echo "$*" >>"$FAKE_LOG"\n'
            'args=("$@"); [[ ${args[0]} == --user ]] && args=("${args[@]:1}")\n'
            'verb=${args[0]:-}; units=("${args[@]:1}")\n'
            'units=("${units[@]/#--*/}")\n'
            f'for unit in "${{units[@]}}"; do [[ -n $unit ]] && grep -qx "$verb $unit" {str(self.root / "systemctl.fail")!r} && exit 1; done\n'
            'case $verb in\n'
            '  start|restart) for unit in "${units[@]}"; do [[ -n $unit ]] && : >"$state/$unit"; done;;\n'
            '  stop) for unit in "${units[@]}"; do rm -f "$state/$unit"; done;;\n'
            '  is-active) for unit in "${units[@]}"; do [[ -n $unit ]] && [[ -e $state/$unit ]] && exit 0; done; exit 3;;\n'
            'esac\n'
            'exit 0\n',
        )

    def add_lid(self) -> None:
        lid = self.proc / "acpi/button/lid/LID0"
        lid.mkdir(parents=True, exist_ok=True)
        (lid / "state").write_text("state:      open\n")

    def install(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.payload / "install.sh")],
            env=self.env | extra, text=True, capture_output=True, check=False, timeout=60,
        )

    def ok_install(self, **extra: str) -> subprocess.CompletedProcess[str]:
        result = self.install(**extra)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        return result

    def backups(self, directory: Path, name: str) -> list[Path]:
        return sorted(directory.glob(f"{name}.diarchy-backup-*"))


class DownloadAndFailureTextTests(InstallerCase):
    def test_download_prints_no_progress_meter(self) -> None:  # B-10
        result = self.ok_install()
        self.assertNotIn("% Total", result.stderr)

    def test_failed_download_names_the_step_and_the_next_action(self) -> None:  # B-10 / B-11
        result = self.install(FAKE_CURL_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] Could not download Sunshine", result.stderr)
        self.assertIn("run this installer again", result.stderr)

    def test_failed_credentials_step_prints_fail_line_without_the_password(self) -> None:  # B-11
        result = self.install(FAKE_CREDS_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] Sunshine rejected the Web UI credentials", result.stderr)
        self.assertNotIn("hunter2secret", result.stdout + result.stderr)
        self.assertIn("***", result.stderr)

    def test_failed_systemctl_restart_prints_fail_line_with_journalctl_hint(self) -> None:  # B-11
        self.systemctl(fail=["restart diarchy-display.service"])
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] systemctl --user restart failed", result.stderr)
        self.assertIn("journalctl --user -u diarchy-display.service", result.stderr)

    def test_failed_systemctl_disable_prints_fail_line(self) -> None:  # B-11
        self.systemctl(fail=["disable diarchy-display.service"])
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] systemctl --user disable failed", result.stderr)

    def test_hyprland_error_tells_the_user_to_log_in(self) -> None:  # B-22
        result = self.install(FAKE_NO_HYPR="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Hyprland IPC is not reachable", result.stderr)
        self.assertIn("Log in to Hyprland", result.stderr)

    def test_service_and_encoder_errors_name_the_journal_command(self) -> None:  # B-22
        self.systemctl(fail=["restart diarchy-sunshine.service"])
        self.assertIn("journalctl --user -u diarchy-", self.install().stderr)
        self.systemctl()
        result = self.install(FAKE_ENCODER_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("journalctl --user -u diarchy-sunshine.service", result.stderr)


class UserFileBackupTests(InstallerCase):
    def test_user_apps_json_is_backed_up_before_it_is_replaced(self) -> None:  # B-12
        self.config.mkdir(parents=True)
        (self.config / "apps.json").write_text('{"apps": [{"name": "Mine"}]}\n')
        self.ok_install()
        backups = self.backups(self.config, "apps.json")
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), '{"apps": [{"name": "Mine"}]}\n')
        self.assertEqual((self.config / "apps.json").read_text(), (HOST / "apps.json").read_text())

    def test_foreign_sunshine_launcher_is_backed_up_but_our_own_is_not(self) -> None:  # B-12
        self.bin.mkdir(parents=True)
        (self.bin / "sunshine").write_text("#!/bin/sh\necho mine\n")
        self.ok_install()
        self.assertEqual(len(self.backups(self.bin, "sunshine")), 1)
        self.ok_install()  # second run: the launcher is ours now
        self.assertEqual(len(self.backups(self.bin, "sunshine")), 1)
        self.assertEqual(self.backups(self.config, "apps.json"), [])

    def test_existing_sunshine_credentials_are_kept_and_warned_about(self) -> None:  # B-12
        self.config.mkdir(parents=True)
        (self.config / "sunshine_state.json").write_text('{"user": "mine"}')
        result = self.ok_install()
        self.assertEqual(self.calls().count("creds"), 0)
        self.assertEqual((self.config / "sunshine_state.json").read_text(), '{"user": "mine"}')
        self.assertFalse((self.config / ".diarchy-ui").exists())
        self.assertIn("[warn] Sunshine credentials already exist", result.stderr)

    def test_fresh_host_still_creates_credentials(self) -> None:  # B-12 guard
        self.ok_install()
        self.assertEqual(self.calls().count("creds"), 1)
        self.assertTrue((self.config / ".diarchy-ui").exists())


class ServiceOrderTests(InstallerCase):
    def test_active_kvm_is_stopped_before_the_streaming_restart_and_started_again(self) -> None:  # B-21
        self.systemctl(active=["diarchy-kvm.service"])
        self.ok_install()
        calls = self.calls()
        stop = calls.index("--user stop diarchy-kvm.service")
        restart = calls.index("--user restart diarchy-display.service diarchy-sunshine.service")
        back = calls.index("--user restart diarchy-kvm.service")
        self.assertLess(stop, restart)
        self.assertLess(restart, back)

    def test_inactive_kvm_is_left_alone(self) -> None:  # B-21
        self.ok_install()
        touched = [c for c in self.calls() if "diarchy-kvm.service" in c and "is-active" not in c]
        self.assertEqual(touched, [])

    def test_running_streaming_is_refused_before_anything_changes(self) -> None:  # B-21
        self.systemctl(active=["diarchy-sunshine.service"])
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("diarchy-sunshine.service is active", result.stderr)
        self.assertIn("diarchy-host stream stop", result.stderr)
        self.assertFalse(self.opt.exists())
        self.assertNotIn("curl", " ".join(self.calls()))

    def test_failed_install_still_starts_kvm_again(self) -> None:  # B-21
        self.systemctl(active=["diarchy-kvm.service"], fail=["restart diarchy-display.service"])
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("--user restart diarchy-kvm.service", self.calls())


class LidTests(InstallerCase):
    def test_no_lid_warning_on_a_host_without_a_lid_switch(self) -> None:  # B-20
        result = self.ok_install()
        self.assertNotIn("Lid close", result.stderr)

    def test_lid_warning_on_a_laptop_that_suspends_on_lid_close(self) -> None:  # B-20
        self.add_lid()
        self.assertIn("[warn] Lid close is not ignored", self.ok_install().stderr)

    def test_no_lid_warning_on_a_laptop_that_ignores_lid_close(self) -> None:  # B-20
        self.add_lid()
        self.assertNotIn("Lid close", self.ok_install(FAKE_LID_IGNORED="1").stderr)


class OldVersionPruneTests(InstallerCase):
    def test_other_sunshine_versions_are_removed_after_a_verified_install(self) -> None:  # B-32
        old = self.opt / "sunshine-2025.1.1"
        (old / "bin").mkdir(parents=True)
        keep_other = self.opt / "sunshine-custom"
        keep_other.mkdir()
        result = self.ok_install()
        self.assertFalse(old.exists())
        self.assertIn(str(old), result.stdout)
        self.assertTrue((self.opt / f"sunshine-{VERSION}/AppRun").exists())
        self.assertTrue(keep_other.exists(), "only numbered version folders are pruned")

    def test_nothing_is_removed_when_the_install_fails(self) -> None:  # B-32
        old = self.opt / "sunshine-2025.1.1"
        old.mkdir(parents=True)
        self.assertEqual(self.install(FAKE_ENCODER_FAIL="1").returncode, 1)
        self.assertTrue(old.exists())


class DoctorCase(InstallerCase):
    def setUp(self) -> None:
        super().setUp()
        self.script("tools/stat", 'echo 600\n')
        self.bin.mkdir(parents=True)
        shutil.copy2(self.payload / "sunshine", self.bin / "sunshine")
        shutil.copy2(self.payload / "diarchy-check-sunshine-service", self.bin / "diarchy-check-sunshine-service")
        (self.home / ".config/sunshine").mkdir(parents=True)
        (self.home / ".config/sunshine/sunshine.conf").write_text("")
        unit_dir = self.home / ".config/systemd/user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "diarchy-sunshine.service").write_text("")
        self.script(
            "tools/systemctl",
            'case " $* " in\n'
            '  *" is-active "*) [[ " ${FAKE_ACTIVE:-} " == *" ${@: -1} "* ]];;\n'
            '  *" is-failed "*) [[ " ${FAKE_FAILED:-} " == *" ${@: -1} "* ]];;\n'
            'esac\n',
        )

    def doctor(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.payload / "diarchy-host-doctor")],
            env=self.env | extra, text=True, capture_output=True, check=False, timeout=60,
        )


class DoctorTests(DoctorCase):
    def test_healthy_host_with_streaming_off_and_no_lid_exits_zero(self) -> None:  # B-13
        result = self.doctor()
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertIn("[info] Sunshine service is inactive (Streaming is off)", result.stdout)
        self.assertIn("[info] No lid switch", result.stdout)
        self.assertNotIn("[fail]", result.stderr)

    def test_laptop_that_suspends_on_lid_close_still_fails(self) -> None:  # B-13
        self.add_lid()
        result = self.doctor()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Lid close can suspend the host", result.stderr)

    def test_laptop_that_ignores_lid_close_exits_zero(self) -> None:  # B-13
        self.add_lid()
        self.assertEqual(self.doctor(FAKE_LID_IGNORED="1").returncode, 0)

    def test_failed_streaming_unit_is_still_a_failure(self) -> None:  # B-13
        result = self.doctor(FAKE_FAILED="diarchy-sunshine.service")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Sunshine service failed", result.stderr)
        self.assertIn("journalctl --user -u diarchy-sunshine.service", result.stderr)

    def test_active_streaming_with_a_bad_capture_check_fails(self) -> None:  # B-13
        result = self.doctor(
            FAKE_ACTIVE="diarchy-display.service diarchy-sunshine.service", FAKE_ENCODER_FAIL="1",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("capture or hardware encoding does not match", result.stderr)

    def test_kvm_only_host_without_streaming_exits_zero(self) -> None:  # B-13
        (self.bin / "sunshine").unlink()
        (self.home / ".config/systemd/user/diarchy-sunshine.service").unlink()
        result = self.doctor()
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertIn("Streaming is not installed", result.stdout)


class ShellSyntaxTests(unittest.TestCase):
    def test_touched_scripts_parse(self) -> None:
        for name in ("install.sh", "diarchy-host-doctor", "install-lan-mouse.sh", "install-host.sh"):
            result = subprocess.run(["bash", "-n", str(HOST / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, (name, result.stderr))


if __name__ == "__main__":
    unittest.main()
