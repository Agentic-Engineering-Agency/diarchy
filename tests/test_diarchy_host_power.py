from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from diarchy_host import power  # noqa: E402


class PowerCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / "proc"
        self.sysfs = self.root / "sys"
        self.logind = [self.root / "lib", self.root / "etc"]
        env = mock.patch.dict(os.environ, {
            "DIARCHY_PROC_ROOT": str(self.proc),
            "DIARCHY_SYSFS_ROOT": str(self.sysfs),
            "DIARCHY_LOGIND_DIRS": ":".join(str(path) for path in self.logind),
        })
        env.start()
        self.addCleanup(env.stop)

    def lid(self, state: str | None, name: str = "LID0") -> None:
        directory = self.proc / "acpi/button/lid" / name
        directory.mkdir(parents=True)
        if state is not None:
            (directory / "state").write_text(f"state:      {state}\n")

    def supply(self, name: str, kind: str) -> None:
        directory = self.sysfs / "class/power_supply" / name
        directory.mkdir(parents=True)
        (directory / "type").write_text(f"{kind}\n")

    def logind_conf(self, index: int, relative: str, text: str) -> None:
        path = self.logind[index] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class LidTests(PowerCase):
    def test_desktop_has_no_lid(self) -> None:
        self.assertIs(power.has_lid(), False)
        self.assertIsNone(power.lid_closed())

    def test_lid_entry_means_laptop(self) -> None:
        self.lid("open")
        self.assertIs(power.has_lid(), True)
        self.assertIs(power.lid_closed(), False)

    def test_closed_lid(self) -> None:
        self.lid("closed")
        self.assertIs(power.lid_closed(), True)

    def test_any_closed_lid_wins_and_unreadable_state_is_unknown(self) -> None:
        self.lid("open", "LID0")
        self.lid("closed", "LID1")
        self.assertIs(power.lid_closed(), True)

    def test_lid_without_state_file_is_unknown(self) -> None:
        self.lid(None)
        self.assertIs(power.has_lid(), True)
        self.assertIsNone(power.lid_closed())


class BatteryTests(PowerCase):
    def test_battery_is_found_by_type(self) -> None:
        self.supply("AC", "Mains")
        self.assertIs(power.has_battery(), False)
        self.supply("BAT0", "Battery")
        self.assertIs(power.has_battery(), True)

    def test_missing_sysfs_means_no_battery(self) -> None:
        self.assertIs(power.has_battery(), False)


class LidPolicyTests(PowerCase):
    def test_default_is_suspend(self) -> None:
        self.assertIs(power.lid_policy_ok(), False)

    def test_ignore_in_drop_in_is_ok(self) -> None:
        self.logind_conf(1, "logind.conf.d/x.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.assertIs(power.lid_policy_ok(), True)

    def test_later_directory_and_drop_in_override(self) -> None:
        self.logind_conf(0, "logind.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.logind_conf(1, "logind.conf.d/50.conf", "[Login]\nHandleLidSwitch=suspend\n")
        self.assertIs(power.lid_policy_ok(), False)

    def test_other_sections_comments_and_empty_value(self) -> None:
        self.logind_conf(0, "logind.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.logind_conf(1, "logind.conf", "[Other]\nHandleLidSwitch=suspend\n#HandleLidSwitch=suspend\n")
        self.assertIs(power.lid_policy_ok(), True)
        self.logind_conf(1, "logind.conf", "[Login]\nHandleLidSwitch=\n")
        self.assertIs(power.lid_policy_ok(), False)


if __name__ == "__main__":
    unittest.main()
