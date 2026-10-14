from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from tests.test_diarchy_host_support import AgentCase, HOST

sys.path.insert(0, str(HOST))
from diarchy_host import common, platform as host_platform  # noqa: E402

FAKE_SYSTEMCTL = """\
import os, sys
args = sys.argv[1:]
assert args[0] == "--user", args
if args[1:2] == ["is-system-running"]:
    print(os.environ.get("FAKE_MANAGER_STATE", "running"))
    sys.exit(0 if os.environ.get("FAKE_MANAGER_STATE", "running") == "running" else 1)
if args[1:4] == ["is-active", "--quiet", "graphical-session.target"]:
    sys.exit(0 if os.environ.get("FAKE_GRAPHICAL", "1") == "1" else 3)
sys.exit(2)
"""
HYPRCTL_ONE = (
    'import sys\nassert sys.argv[1:] == ["instances", "-j"], sys.argv\n'
    'print(\'[{"instance":"abc","wl_socket":"wayland-1","pid":42,"time":1}]\')\n'
)
HYPRCTL_NONE = 'print("[]")\n'
HYPRCTL_FAILS = "import sys\nsys.exit(1)\n"
HYPRCTL_GARBAGE = 'print("not json")\n'
NOOP = "pass\n"
PYTHON_FAILS = "import sys\nsys.exit(1)\n"
PACKAGE_TOOLS = ("wl-copy", "wl-paste", "rsync", "notify-send", "openssl", "jq", "curl")


class PlatformCase(AgentCase):
    """A fully supported Omarchy host by default; tests degrade one condition at a time."""

    def setUp(self) -> None:
        super().setUp()
        self.os_release = self.root / "os-release"
        self.os_release.write_text("NAME=Arch Linux\nID=arch\n")
        self.env["DIARCHY_OS_RELEASE"] = str(self.os_release)
        self.env["DIARCHY_MACHINE"] = "x86_64"
        self.tool("hyprctl", HYPRCTL_ONE, "HYPRCTL_BIN")
        self.tool("systemctl", FAKE_SYSTEMCTL, "SYSTEMCTL_BIN")
        for name in PACKAGE_TOOLS:
            self.tool(name, NOOP)
        self.tool("python3", NOOP)
        (self.home / ".local/share/omarchy").mkdir(parents=True)

    def live(self, **overrides: str) -> None:
        patcher = mock.patch.dict(os.environ, self.env | overrides, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def detect(self, **overrides: str) -> host_platform.PlatformInfo:
        self.live(**overrides)
        return host_platform.HyprlandArchAdapter().detect()

    def set_os_release(self, text: str) -> None:
        self.os_release.write_text(text)


class DetectTests(PlatformCase):
    def test_supported_omarchy_host(self) -> None:
        info = self.detect()
        self.assertEqual(info.to_dict(), {
            "supported": True, "adapter": "hyprland-arch", "os_id": "arch", "flavor": "omarchy",
            "compositor": "hyprland", "machine": "x86_64", "reasons": [],
        })
        self.assertIs(info.supported, True)

    def test_missing_omarchy_directory_means_no_flavor_but_still_supported(self) -> None:
        (self.home / ".local/share/omarchy").rmdir()
        info = self.detect()
        self.assertIsNone(info.flavor)
        self.assertTrue(info.supported)
        self.assertEqual(info.reasons, [])

    def test_arch_family_via_id_like_is_supported(self) -> None:
        for text, os_id in (
            ("ID=endeavouros\nID_LIKE=arch\n", "endeavouros"),
            ('ID=manjaro-custom\nID_LIKE="manjaro arch"\n', "manjaro-custom"),
            ('ID="arch"\n', "arch"),
        ):
            with self.subTest(text=text):
                self.set_os_release(text)
                info = self.detect()
                self.assertTrue(info.supported, info.reasons)
                self.assertEqual(info.os_id, os_id)

    def test_ubuntu_is_unsupported_and_selects_the_unsupported_adapter(self) -> None:
        self.set_os_release("ID=ubuntu\nID_LIKE=debian\n")
        info = self.detect()
        self.assertFalse(info.supported)
        self.assertEqual(info.os_id, "ubuntu")
        self.assertEqual(len(info.reasons), 1)
        self.assertIn("Arch", info.reasons[0])
        selected = host_platform.select_adapter().detect()
        self.assertEqual(selected.adapter, "unsupported")
        self.assertFalse(selected.supported)
        self.assertEqual(selected.reasons, info.reasons)

    def test_non_x86_64_machine_is_unsupported(self) -> None:
        info = self.detect(DIARCHY_MACHINE="aarch64")
        self.assertFalse(info.supported)
        self.assertEqual(info.machine, "aarch64")
        self.assertEqual(len(info.reasons), 1)
        self.assertIn("x86_64", info.reasons[0])

    def test_machine_falls_back_to_the_running_kernel(self) -> None:
        self.env.pop("DIARCHY_MACHINE")
        with mock.patch.object(os, "uname", return_value=mock.Mock(machine="x86_64")):
            self.assertEqual(self.detect().machine, "x86_64")

    def test_no_hyprland_instance_is_unsupported(self) -> None:
        cases = {
            "empty list": HYPRCTL_NONE,
            "exit 1": HYPRCTL_FAILS,
            "bad json": HYPRCTL_GARBAGE,
            "not a list": 'print(\'{"instance":"abc"}\')\n',
            # `kvm start` and `stream start` need a live session: the probe must use the same rule (bughunt B-25).
            "pid zero": 'print(\'[{"pid":0,"instance":"abc","wl_socket":"wayland-1"}]\')\n',
            "no wl_socket": 'print(\'[{"pid":42,"instance":"abc"}]\')\n',
            "no instance": 'print(\'[{"pid":42,"wl_socket":"wayland-1"}]\')\n',
        }
        for label, body in cases.items():
            with self.subTest(label):
                self.tool("hyprctl", body)
                info = self.detect()
                self.assertFalse(info.supported)
                self.assertIsNone(info.compositor)
                self.assertEqual(len(info.reasons), 1)
                self.assertIn("Hyprland", info.reasons[0])

    def test_missing_hyprctl_binary_is_unsupported_without_raising(self) -> None:
        info = self.detect(HYPRCTL_BIN=str(self.root / "absent-hyprctl"))
        self.assertFalse(info.supported)
        self.assertIsNone(info.compositor)
        self.assertIn("Hyprland", info.reasons[0])

    def test_hanging_hyprctl_is_unsupported_without_raising(self) -> None:
        self.live()
        with mock.patch.object(common, "run", side_effect=common.AgentError("internal", "timed out")):
            info = host_platform.HyprlandArchAdapter().detect()
        self.assertFalse(info.supported)
        self.assertIsNone(info.compositor)
        self.assertTrue(info.reasons)

    def test_offline_manager_is_unsupported_and_degraded_counts_as_active(self) -> None:
        info = self.detect(FAKE_MANAGER_STATE="offline")
        self.assertFalse(info.supported)
        self.assertEqual(len(info.reasons), 1)
        self.assertIn("systemd user manager", info.reasons[0])
        self.assertTrue(self.detect(FAKE_MANAGER_STATE="degraded").supported)
        self.assertTrue(self.detect(FAKE_MANAGER_STATE="running").supported)

    def test_missing_systemctl_degrades_to_a_reason(self) -> None:
        info = self.detect(SYSTEMCTL_BIN=str(self.root / "absent-systemctl"))
        self.assertFalse(info.supported)
        self.assertIn("systemd user manager", info.reasons[0])

    def test_one_reason_per_failed_condition(self) -> None:
        self.set_os_release("ID=ubuntu\nID_LIKE=debian\n")
        self.tool("hyprctl", HYPRCTL_NONE)
        info = self.detect()
        self.assertEqual(len(info.reasons), 2)
        self.assertIn("Arch", info.reasons[0])
        self.assertIn("Hyprland", info.reasons[1])
        everything = self.detect(DIARCHY_MACHINE="aarch64", FAKE_MANAGER_STATE="offline")
        self.assertEqual(len(everything.reasons), 4)

    def test_missing_os_release_is_unsupported_without_raising(self) -> None:
        self.os_release.unlink()
        info = self.detect()
        self.assertFalse(info.supported)
        self.assertIsNone(info.os_id)
        self.assertEqual(len(info.reasons), 1)
        self.assertIn("Arch", info.reasons[0])


class PackageTests(PlatformCase):
    def adapter(self, **overrides: str) -> host_platform.HyprlandArchAdapter:
        self.live(**overrides)
        return host_platform.HyprlandArchAdapter()

    def test_nothing_missing_when_every_tool_and_the_gi_import_are_present(self) -> None:
        self.assertEqual(self.adapter().missing_packages(), [])

    def test_missing_single_tools_map_to_their_package(self) -> None:
        for binary, package in (("wl-paste", "wl-clipboard"), ("wl-copy", "wl-clipboard"),
                                ("notify-send", "libnotify"), ("rsync", "rsync"),
                                ("openssl", "openssl")):
            with self.subTest(binary):
                (self.tools / binary).unlink()
                try:
                    self.assertEqual(self.adapter().missing_packages(), [package])
                finally:
                    self.tool(binary, NOOP)

    def test_both_clipboard_tools_missing_report_one_package(self) -> None:
        (self.tools / "wl-copy").unlink()
        (self.tools / "wl-paste").unlink()
        self.assertEqual(self.adapter().missing_packages(), ["wl-clipboard"])

    def test_packages_follow_the_documented_order(self) -> None:
        (self.tools / "jq").unlink()
        (self.tools / "curl").unlink()
        self.assertEqual(self.adapter().missing_packages(), ["jq", "curl"])
        for binary in ("notify-send", "wl-copy", "rsync", "openssl"):
            (self.tools / binary).unlink()
        self.assertEqual(self.adapter().missing_packages(),
                         ["wl-clipboard", "rsync", "libnotify", "openssl", "jq", "curl"])

    def test_openssl_presence_follows_openssl_bin(self) -> None:
        self.assertEqual(self.adapter(OPENSSL_BIN=str(self.root / "absent-openssl")).missing_packages(),
                         ["openssl"])
        self.tool("custom-openssl", NOOP, "OPENSSL_BIN")
        (self.tools / "openssl").unlink()
        self.assertEqual(self.adapter().missing_packages(), [])

    def test_failing_gi_import_adds_the_gtk_packages_last(self) -> None:
        self.tool("python3", PYTHON_FAILS)
        self.assertEqual(self.adapter().missing_packages(), ["python-gobject", "gtk3", "gtk-layer-shell"])
        (self.tools / "jq").unlink()
        self.assertEqual(self.adapter().missing_packages(),
                         ["jq", "python-gobject", "gtk3", "gtk-layer-shell"])

    def gi_stub(self, **overrides: str) -> host_platform.HyprlandArchAdapter:
        """A real interpreter with a stub `gi` whose typelibs are absent when named in FAKE_GI_MISSING."""
        stub = self.root / "stub-gi"
        (stub / "gi").mkdir(parents=True, exist_ok=True)
        (stub / "gi/__init__.py").write_text(
            "import os\n"
            "def require_version(namespace, version):\n"
            "    if namespace in os.environ.get('FAKE_GI_MISSING', '').split():\n"
            "        raise ValueError('Namespace %s not available' % namespace)\n")
        return self.adapter(PYTHON_BIN=sys.executable, PYTHONPATH=str(stub), **overrides)

    def test_gi_present_with_every_typelib_reports_nothing(self) -> None:
        self.assertEqual(self.gi_stub().missing_packages(), [])

    def test_only_the_missing_gi_typelib_package_is_listed(self) -> None:
        self.assertEqual(self.gi_stub(FAKE_GI_MISSING="GtkLayerShell").missing_packages(), ["gtk-layer-shell"])
        self.assertEqual(self.gi_stub(FAKE_GI_MISSING="Gtk").missing_packages(), ["gtk3"])
        self.assertEqual(self.gi_stub(FAKE_GI_MISSING="Gtk GtkLayerShell").missing_packages(),
                         ["gtk3", "gtk-layer-shell"])

    def test_absent_gi_module_lists_the_whole_bundle(self) -> None:
        self.assertEqual(self.adapter(PYTHON_BIN=sys.executable).missing_packages(),
                         ["python-gobject", "gtk3", "gtk-layer-shell"])

    def test_gi_import_uses_python_bin_and_requires_both_typelibs(self) -> None:
        self.tool("recording-python", (
            "import os, sys\n"
            "open(os.environ['FAKE_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(1)\n"), "PYTHON_BIN")
        self.assertEqual(self.adapter().missing_packages(), ["python-gobject", "gtk3", "gtk-layer-shell"])
        call = "\n".join(self.calls())
        self.assertTrue(call.startswith("-c "), call)
        for fragment in ("import gi", "require_version", "'Gtk'", "'3.0'", "'GtkLayerShell'", "'0.1'"):
            self.assertIn(fragment, call)

    def test_missing_python_binary_counts_as_failing_import(self) -> None:
        self.assertEqual(self.adapter(PYTHON_BIN=str(self.root / "absent-python")).missing_packages(),
                         ["python-gobject", "gtk3", "gtk-layer-shell"])


class AdapterSurfaceTests(PlatformCase):
    def test_install_command_and_capture_backend(self) -> None:
        self.live()
        adapter = host_platform.HyprlandArchAdapter()
        self.assertEqual(adapter.install_command(["jq", "curl"]), "sudo pacman -S --needed jq curl")
        self.assertEqual(adapter.kvm_capture_backend(), "layer-shell")

    def test_graphical_session_active_follows_systemctl(self) -> None:
        self.live(FAKE_GRAPHICAL="1")
        self.assertTrue(host_platform.HyprlandArchAdapter().graphical_session_active())
        self.live(FAKE_GRAPHICAL="0")
        self.assertFalse(host_platform.HyprlandArchAdapter().graphical_session_active())

    def test_graphical_session_inactive_when_systemctl_is_missing(self) -> None:
        self.live(SYSTEMCTL_BIN=str(self.root / "absent-systemctl"))
        self.assertFalse(host_platform.HyprlandArchAdapter().graphical_session_active())

    def test_unsupported_adapter_install_command_raises(self) -> None:
        info = host_platform.PlatformInfo(False, "unsupported", "ubuntu", None, None, "x86_64", ["why"])
        adapter = host_platform.UnsupportedAdapter(info)
        with self.assertRaises(common.AgentError) as caught:
            adapter.install_command(["jq"])
        self.assertEqual(caught.exception.code, "unsupported_platform")
        self.assertEqual(adapter.missing_packages(), [])
        self.assertEqual(adapter.detect(), info)

    def test_select_adapter_returns_hyprland_when_supported(self) -> None:
        self.live()
        adapter = host_platform.select_adapter()
        self.assertIsInstance(adapter, host_platform.HyprlandArchAdapter)
        self.assertEqual(adapter.detect().adapter, "hyprland-arch")

    def test_platform_info_is_frozen_and_serializes_a_fresh_reasons_list(self) -> None:
        info = host_platform.PlatformInfo(False, "unsupported", None, None, None, None, ["x"])
        with self.assertRaises(Exception):
            info.supported = True  # type: ignore[misc]
        payload = info.to_dict()
        payload["reasons"].append("y")
        self.assertEqual(info.reasons, ["x"])

    def test_package_does_not_shadow_or_import_the_stdlib_platform_module(self) -> None:
        source = (HOST / "diarchy_host" / "platform.py").read_text()
        self.assertNotIn("import platform", source)
        self.assertNotIn("from platform", source)


if __name__ == "__main__":
    unittest.main()
