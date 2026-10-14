from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KVM = ROOT / "client" / "diarchy-kvm"


def kvm_env(temp: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DIARCHY_", "KVM_"))}
    env.update(HOME=str(temp), TMPDIR=str(temp / "tmp"), **extra)
    (temp / "tmp").mkdir(exist_ok=True)
    return env


class StaleLockTests(unittest.TestCase):
    def run_off(self, temp: Path, lock: Path) -> tuple[subprocess.CompletedProcess[str], float]:
        pgrep = temp / "pgrep"
        pgrep.write_text("#!/bin/sh\nexit 1\n")
        pgrep.chmod(0o755)
        env = kvm_env(
            temp,
            PGREP_BIN=str(pgrep),
            LAN_MOUSE_APP=str(temp / "Lan Mouse.app"),
            KVM_ACTION_LOCK_DIR=str(lock),
        )
        started = time.monotonic()
        result = subprocess.run(
            [str(KVM), "off"], env=env, text=True, capture_output=True, check=False, timeout=30
        )
        return result, time.monotonic() - started

    def test_old_lock_without_pid_file_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            lock = temp / "lock.d"
            lock.mkdir(mode=0o700)
            old = time.time() - 120
            os.utime(lock, (old, old))
            result, elapsed = self.run_off(temp, lock)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already disabled", result.stdout)
            self.assertLess(elapsed, 5)

    def test_fresh_lock_without_pid_file_is_kept_while_its_owner_may_still_write_the_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            lock = temp / "lock.d"
            lock.mkdir(mode=0o700)
            pgrep = temp / "pgrep"
            pgrep.write_text("#!/bin/sh\nexit 1\n")
            pgrep.chmod(0o755)
            env = kvm_env(
                temp,
                PGREP_BIN=str(pgrep),
                LAN_MOUSE_APP=str(temp / "Lan Mouse.app"),
                KVM_ACTION_LOCK_DIR=str(lock),
                SLEEP_BIN="/usr/bin/true",
            )
            result = subprocess.run(
                [str(KVM), "off"], env=env, text=True, capture_output=True, check=False, timeout=30
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("another KVM action is in progress", result.stderr)
            self.assertTrue(lock.exists())


class SpecialHomeTests(unittest.TestCase):
    def start_fake_lan_mouse(self, executable: str) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            ["/bin/bash", "-c", 'exec -a "$0" /bin/cat', executable],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: (process.poll() is None and process.kill(), process.wait()))
        time.sleep(0.5)
        return process

    def test_status_and_off_find_lan_mouse_when_the_path_has_regex_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            app = temp / "a+b (1) [x]" / "Lan Mouse.app"
            executable = str(app / "Contents" / "MacOS" / "lan-mouse")
            process = self.start_fake_lan_mouse(executable)
            env = kvm_env(temp, LAN_MOUSE_APP=str(app))
            status = subprocess.run(
                [str(KVM), "status"], env=env, text=True, capture_output=True, check=False, timeout=30
            )
            self.assertIn("Diarchy KVM: degraded", status.stdout)
            off = subprocess.run(
                [str(KVM), "off"], env=env, text=True, capture_output=True, check=False, timeout=30
            )
            self.assertEqual(off.returncode, 0, off.stderr)
            self.assertIn("Diarchy KVM: disabled", off.stdout)
            self.assertIsNotNone(process.wait(timeout=5))


if __name__ == "__main__":
    unittest.main()
