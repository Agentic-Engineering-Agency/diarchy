from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from diarchy_host import outputs  # noqa: E402
from diarchy_host.common import AgentError  # noqa: E402


def mon(name: str, *, disabled: bool = False, focused: bool = False) -> dict:
    return {"name": name, "disabled": disabled, "focused": focused}


class ResolveOutputTests(unittest.TestCase):
    def test_laptop_prefers_the_internal_panel_even_when_another_monitor_is_focused(self) -> None:
        monitors = [mon("DP-2", focused=True), mon("eDP-1")]
        self.assertEqual(outputs.resolve_output("auto", monitors), "eDP-1")

    def test_other_internal_prefixes_count_as_internal(self) -> None:
        for name in ("LVDS-1", "DSI-1", "eDP-2"):
            with self.subTest(name=name):
                self.assertEqual(outputs.resolve_output("auto", [mon("HDMI-A-1"), mon(name)]), name)

    def test_desktop_uses_the_focused_monitor(self) -> None:
        monitors = [mon("DP-1"), mon("HDMI-A-1", focused=True)]
        self.assertEqual(outputs.resolve_output("auto", monitors), "HDMI-A-1")

    def test_desktop_without_focus_uses_the_first_enabled_monitor(self) -> None:
        monitors = [mon("DP-1", disabled=True), mon("HDMI-A-1"), mon("DP-2")]
        self.assertEqual(outputs.resolve_output("auto", monitors), "HDMI-A-1")

    def test_a_disabled_focused_monitor_is_skipped(self) -> None:
        monitors = [mon("DP-1", disabled=True, focused=True), mon("DP-2")]
        self.assertEqual(outputs.resolve_output("auto", monitors), "DP-2")

    def test_an_enabled_internal_panel_wins_over_the_focused_external_monitor(self) -> None:
        monitors = [mon("eDP-1"), mon("DP-1", focused=True)]
        self.assertEqual(outputs.resolve_output("auto", monitors), "eDP-1")

    def test_a_disabled_internal_panel_is_skipped_for_the_focused_monitor(self) -> None:
        monitors = [mon("eDP-1", disabled=True), mon("DP-1", focused=True), mon("DP-2")]
        self.assertEqual(outputs.resolve_output("auto", monitors), "DP-1")

    def test_a_disabled_internal_panel_with_no_other_enabled_monitor_is_headless(self) -> None:
        monitors = [mon("eDP-1", disabled=True), mon("DP-1", disabled=True)]
        self.assertEqual(outputs.resolve_output("auto", monitors), outputs.HEADLESS_OUTPUT)

    def test_no_enabled_monitor_or_no_data_falls_back_to_headless(self) -> None:
        self.assertEqual(outputs.resolve_output("auto", [mon("DP-1", disabled=True)]), outputs.HEADLESS_OUTPUT)
        self.assertEqual(outputs.resolve_output("auto", []), outputs.HEADLESS_OUTPUT)
        self.assertEqual(outputs.resolve_output("auto", None), outputs.HEADLESS_OUTPUT)

    def test_the_headless_output_is_never_picked_by_auto(self) -> None:
        self.assertEqual(outputs.resolve_output("auto", [mon("DIARCHY", focused=True)]), outputs.HEADLESS_OUTPUT)
        self.assertEqual(outputs.resolve_output("auto", [mon("DIARCHY", focused=True), mon("DP-1")]), "DP-1")

    def test_an_explicit_name_is_returned_without_consulting_monitors(self) -> None:
        self.assertEqual(outputs.resolve_output("DP-1", None), "DP-1")
        self.assertEqual(outputs.resolve_output("HDMI-A-1", [mon("eDP-1")]), "HDMI-A-1")

    def test_invalid_names_are_usage_errors(self) -> None:
        for bad in ("", "DP 1", "a;b", "x" * 65, "DP-1\n", "$(id)"):
            with self.subTest(bad=bad):
                with self.assertRaises(AgentError) as ctx:
                    outputs.resolve_output(bad, [])
                self.assertEqual(ctx.exception.code, "usage")

    def test_is_internal(self) -> None:
        self.assertTrue(all(outputs.is_internal(n) for n in ("eDP-1", "LVDS-2", "DSI-1")))
        self.assertFalse(any(outputs.is_internal(n) for n in ("DP-1", "HDMI-A-1", "DIARCHY", "eDP")))


class ListMonitorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def fake(self, body: str) -> None:
        path = Path(self.temp.name) / "hyprctl"
        path.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        patcher = mock.patch.dict(os.environ, {"HYPRCTL_BIN": str(path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parses_the_monitor_array_and_asks_for_all_monitors(self) -> None:
        self.fake(f"assert sys.argv[1:] == ['-j', 'monitors', 'all']\nprint({json.dumps(json.dumps([mon('DP-1')]))})")
        self.assertEqual(outputs.list_monitors(), [mon("DP-1")])

    def test_any_failure_is_none(self) -> None:
        for body in ("sys.exit(1)", "print('not json')", "print('{}')", "print('[1]')"):
            with self.subTest(body=body):
                self.fake(body)
                self.assertIsNone(outputs.list_monitors())

    def test_missing_binary_is_none(self) -> None:
        with mock.patch.dict(os.environ, {"HYPRCTL_BIN": "/nonexistent/hyprctl"}):
            self.assertIsNone(outputs.list_monitors())


if __name__ == "__main__":
    unittest.main()
