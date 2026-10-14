from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.test_diarchy_host_support import AgentCase, HOST

sys.path.insert(0, str(HOST))
import diarchy_host  # noqa: E402
from diarchy_host import cli, common, report  # noqa: E402

CONTRACT_VERSION = "0.1.0-beta.1"
TOP_KEYS = {"schema", "ok", "command", "diarchy_host_version", "os", "hyprland", "gpus", "capture", "nvidia",
            "sunshine", "lan_mouse", "power", "network", "sysfs_snapshot"}
LEAKS = ["192.0.2.10", "100.64.0.10", "00:00:5e:00:53:01", "example-ssid", "host.example", "/home/user/x",
         "SN12345678"]

HYPRCTL = """\
import json, sys
if sys.argv[1:] == ["version", "-j"]:
    print(json.dumps({"version": "0.54.0", "branch": "main", "commit": "abc", "tag": "v0.54.0"}))
elif sys.argv[1:] == ["-j", "monitors", "all"]:
    print(json.dumps([
        {"name": "eDP-1", "width": 1920, "height": 1080, "refreshRate": 143.998, "disabled": False,
         "serial": "SN12345678", "description": "Example Corp SN12345678", "make": "Example"},
        {"name": "DP-1", "width": 2560, "height": 1440, "refreshRate": 59.951, "disabled": True},
    ]))
else:
    sys.exit(1)
"""


def fake_modules() -> dict[str, types.ModuleType]:
    monitors = [
        {"name": "eDP-1", "width": 1920, "height": 1080, "refreshRate": 143.998, "disabled": False},
        {"name": "DP-1", "width": 2560, "height": 1440, "refreshRate": 59.951, "disabled": True},
    ]
    outputs = types.SimpleNamespace(
        is_internal=lambda name: name.startswith(("eDP-", "LVDS-", "DSI-")),
        list_monitors=lambda: monitors,
        resolve_output=lambda requested, mons: "eDP-1",
    )
    power = types.SimpleNamespace(has_lid=lambda: True, has_battery=lambda: True, lid_policy_ok=lambda: True)
    found = types.SimpleNamespace(render_node="renderD129", driver="i915", encoder="vaapi")
    row = {"card": "card0", "vendor": "intel", "pci_id": "8086:7d55", "driver": "i915", "render_node": "renderD129",
           "boot_vga": True, "connectors": ["eDP-1"]}
    snap = {"card": "card0", "pci": "0000:00:02.0", "vendor": "0x8086", "device": "0x7d55", "driver": "i915",
            "render": "renderD129", "boot_vga": True, "connectors": ["eDP-1"]}
    gpu = types.SimpleNamespace(
        gpus=lambda sysfs: [dict(row)],
        snapshot=lambda sysfs: [dict(snap)],
        nvidia_info=lambda sysfs, proc_root: {"driver_version": "580.95", "modeset": True},
        resolve_capture_gpu=lambda output, sysfs: found,
        unsupported_reason=lambda f: None,
    )
    return {"diarchy_host.outputs": outputs, "diarchy_host.power": power, "diarchy_host.gpu": gpu}


class ReportCase(AgentCase):
    def setUp(self) -> None:
        super().setUp()
        self.sysfs = self.root / "sys"
        self.proc = self.root / "proc"
        self.sysfs.mkdir()
        self.proc.mkdir()
        (self.root / "os-release").write_text('NAME="Arch Linux"\nID=arch\n')
        self.omarchy = self.root / "omarchy"
        self.omarchy.mkdir()
        (self.omarchy / "version").write_text("3.1.0\n")
        self.tool("hyprctl", HYPRCTL, "HYPRCTL_BIN")
        self.tool("nmcli", "print('yes:example-ssid')", "NMCLI_BIN")
        bindir = self.home / ".local" / "bin"
        bindir.mkdir(parents=True)
        for name, text in (("sunshine", "Sunshine version: v2026.516.143833"), ("lan-mouse", "lan-mouse 0.11.0")):
            path = bindir / name
            path.write_text(f"#!{sys.executable}\nprint({text!r})\n")
            path.chmod(0o755)
        self.env.update({
            "DIARCHY_SYSFS_ROOT": str(self.sysfs), "DIARCHY_PROC_ROOT": str(self.proc),
            "DIARCHY_OS_RELEASE": str(self.root / "os-release"), "OMARCHY_PATH": str(self.omarchy),
            "USER": "user", "LOGNAME": "user",
        })

    def net(self, name: str, *, virtual: bool = False, wireless: bool = False, up: bool = False) -> None:
        base = self.sysfs / ("devices/virtual" if virtual else "devices/pci0") / "net" / name
        base.mkdir(parents=True)
        (base / "operstate").write_text("up\n" if up else "down\n")
        if wireless:
            (base / "wireless").mkdir()
        link = self.sysfs / "class" / "net" / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(base)

    def run_report(self, modules: dict | None = None) -> dict:
        from diarchy_host import report

        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.dict(sys.modules, fake_modules() if modules is None else modules), \
                mock.patch("socket.gethostname", return_value="host.example"):
            return report.handle([])


class VersionTests(unittest.TestCase):
    def test_all_versions_agree(self) -> None:
        self.assertEqual(diarchy_host.__version__, CONTRACT_VERSION)
        self.assertEqual((HOST / "VERSION").read_text().strip(), CONTRACT_VERSION)
        self.assertEqual(common.agent_version(), diarchy_host.__version__)


class ReportShapeTests(ReportCase):
    def test_every_contract_key_is_present_with_contract_values(self) -> None:
        self.net("wlan0", wireless=True)
        self.net("eth0", up=True)
        self.net("tailscale0", virtual=True, up=True)
        body = self.run_report()
        self.assertEqual(set(body), TOP_KEYS - {"schema", "ok", "command"})
        self.assertEqual(body["os"], {"id": "arch", "kernel": os.uname().release, "omarchy_version": "3.1.0",
                                      "omarchy_channel": None})
        self.assertEqual(body["hyprland"], {"version": "0.54.0", "monitors": [
            {"name": "eDP-1", "width": 1920, "height": 1080, "refresh": 144.0, "internal": True, "enabled": True},
            {"name": "DP-1", "width": 2560, "height": 1440, "refresh": 60.0, "internal": False, "enabled": False},
        ]})
        self.assertEqual(body["gpus"][0]["driver"], "i915")
        self.assertEqual(body["capture"], {"output": "eDP-1", "render_node": "renderD129", "driver": "i915",
                                           "encoder": "vaapi", "unsupported_reason": None})
        self.assertEqual(body["nvidia"], {"driver_version": "580.95", "modeset": True})
        self.assertEqual(body["sunshine"], {"installed": True, "version": "2026.516.143833"})
        self.assertEqual(body["lan_mouse"], {"installed": True, "version": "0.11.0"})
        self.assertEqual(body["power"], {"has_lid": True, "has_battery": True, "lid_policy_ok": True})
        self.assertEqual(body["network"], {"wifi": True, "ethernet_up": True, "tailscale": True})
        self.assertEqual(body["sysfs_snapshot"][0]["pci"], "0000:00:02.0")

    def test_network_ignores_virtual_and_down_interfaces(self) -> None:
        self.net("eth0", up=False)
        self.net("docker0", virtual=True, up=True)
        self.assertEqual(self.run_report()["network"], {"wifi": False, "ethernet_up": False, "tailscale": False})

    def test_unsupported_encoder_carries_the_reason(self) -> None:
        modules = fake_modules()
        gpu = modules["diarchy_host.gpu"]
        gpu.resolve_capture_gpu = lambda output, sysfs: types.SimpleNamespace(
            render_node="renderD128", driver="nouveau", encoder=None)
        gpu.unsupported_reason = lambda found: "The nouveau driver has no hardware encoder."
        capture = self.run_report(modules)["capture"]
        self.assertIsNone(capture["encoder"])
        self.assertEqual(capture["unsupported_reason"], "The nouveau driver has no hardware encoder.")

    def test_null_encoder_always_has_a_non_empty_reason(self) -> None:
        for found, reason in ((None, "No GPU with a render node was found."), (None, None), (None, "  ")):
            modules = fake_modules()
            gpu = modules["diarchy_host.gpu"]
            gpu.resolve_capture_gpu = lambda output, sysfs, found=found: found
            gpu.unsupported_reason = lambda f, reason=reason: reason
            capture = self.run_report(modules)["capture"]
            self.assertIsNone(capture["encoder"])
            self.assertIsInstance(capture["unsupported_reason"], str)
            self.assertTrue(capture["unsupported_reason"].strip())
        modules = fake_modules()
        modules["diarchy_host.gpu"].resolve_capture_gpu = lambda output, sysfs: None
        modules["diarchy_host.gpu"].unsupported_reason = lambda f: "No GPU with a render node was found."
        self.assertEqual(self.run_report(modules)["capture"]["unsupported_reason"],
                         "No GPU with a render node was found.")

    def test_desktop_has_null_lid_policy(self) -> None:
        modules = fake_modules()
        modules["diarchy_host.power"].has_lid = lambda: False
        modules["diarchy_host.power"].lid_policy_ok = lambda: True
        self.assertEqual(self.run_report(modules)["power"],
                         {"has_lid": False, "has_battery": True, "lid_policy_ok": None})


class ReportDegradationTests(ReportCase):
    def test_missing_tools_and_modules_give_null_not_errors(self) -> None:
        for name in ("hyprctl", "nmcli"):
            (self.tools / name).unlink()
        (self.omarchy / "version").unlink()
        (self.root / "os-release").unlink()
        for tool in (self.home / ".local" / "bin").iterdir():
            tool.unlink()
        body = self.run_report(modules={"diarchy_host.outputs": None, "diarchy_host.power": None,
                                        "diarchy_host.gpu": None})
        self.assertEqual(set(body), TOP_KEYS - {"schema", "ok", "command"})
        self.assertEqual(body["os"]["id"], None)
        self.assertIsNone(body["os"]["omarchy_version"])
        self.assertEqual(body["hyprland"], {"version": None, "monitors": None})
        self.assertIsNone(body["gpus"])
        self.assertEqual(body["capture"], {"output": None, "render_node": None, "driver": None, "encoder": None,
                                           "unsupported_reason": report.NO_ENCODER_REASON})
        self.assertIsNone(body["nvidia"])
        self.assertEqual(body["sunshine"], {"installed": False, "version": None})
        self.assertEqual(body["power"], {"has_lid": None, "has_battery": None, "lid_policy_ok": None})
        self.assertIsNone(body["sysfs_snapshot"])

    def test_a_raising_source_only_nulls_its_own_field(self) -> None:
        modules = fake_modules()

        def boom(*_args):
            raise OSError("broken")

        modules["diarchy_host.gpu"].gpus = boom
        body = self.run_report(modules)
        self.assertIsNone(body["gpus"])
        self.assertEqual(body["power"]["has_lid"], True)


class RedactionTests(ReportCase):
    def leaky_modules(self) -> dict:
        modules = fake_modules()
        leak = ("failed at /home/user/x on host.example 192.0.2.10 100.64.0.10 00:00:5e:00:53:01 "
                "example-ssid for user")
        gpu = modules["diarchy_host.gpu"]
        gpu.resolve_capture_gpu = lambda output, sysfs: types.SimpleNamespace(
            render_node="renderD128", driver="nouveau", encoder=None)
        gpu.unsupported_reason = lambda found: leak
        gpu.gpus = lambda sysfs: [{"card": "card0", "vendor": "intel", "pci_id": "8086:7d55", "driver": "i915",
                                   "render_node": "renderD129", "boot_vga": True, "connectors": ["eDP-1"],
                                   "serial": "SN12345678", "note": leak}]
        gpu.snapshot = lambda sysfs: [{"card": "card0", "serial": "SN12345678", "note": leak}]
        return modules

    def test_report_contains_no_personal_values(self) -> None:
        self.net("wlan0", wireless=True, up=True)
        serialized = json.dumps(self.run_report(self.leaky_modules()))
        for leak in [*LEAKS, str(self.home)]:
            self.assertNotIn(leak, serialized)

    def test_redaction_test_has_teeth(self) -> None:
        from diarchy_host import report

        with mock.patch.object(report, "sanitize", lambda value, *_a, **_k: value):
            serialized = json.dumps(self.run_report(self.leaky_modules()))
        self.assertIn("192.0.2.10", serialized)

    def test_sanitize_scrubs_strings_and_serial_keys(self) -> None:
        from diarchy_host import report

        ip, mac = ".".join(("10", "1", "2", "3")), ":".join(["aa"] * 6)
        cleaned = report.sanitize({"a": [f"{ip} {mac}"], "serial": "X", "ok": 3, "fine": "i915"},
                                  tokens=["secret-name"])
        self.assertEqual(cleaned, {"a": ["[redacted] [redacted]"], "ok": 3, "fine": "i915"})
        self.assertEqual(report.sanitize("hi secret-name", tokens=["secret-name"]), "hi [redacted]")
        self.assertEqual(report.sanitize("/Users/user/doc and /home/user/x", tokens=[]), "[redacted] and [redacted]")


class CliTests(ReportCase):
    def test_report_envelope_and_usage(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.dict(sys.modules, fake_modules()):
            line, code = cli._execute(["report"])
            _, extra_code = cli._execute(["report", "--bogus"])
        body = json.loads(line)
        self.assertEqual(code, 0)
        self.assertEqual(set(body), TOP_KEYS)
        self.assertEqual((body["schema"], body["ok"], body["command"]), (1, True, "report"))
        self.assertEqual(body["diarchy_host_version"], CONTRACT_VERSION)
        self.assertEqual(extra_code, 64)
        self.assertIn("report", cli.USAGE)

    def test_launcher_prints_one_line_with_all_keys_when_tools_are_missing(self) -> None:
        result, body = self.agent("report")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(set(body), TOP_KEYS)
        self.assertTrue(re.fullmatch(r"[\w.-]+", body["diarchy_host_version"]))


if __name__ == "__main__":
    unittest.main()
