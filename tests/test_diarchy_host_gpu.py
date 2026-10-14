from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from diarchy_host import gpu  # noqa: E402

HEADER = "# Managed by diarchy. Credentials and pairing state live outside this file."


class FakeSysfs:
    """A `/sys` tree with only what the resolver reads: class/drm/<card>[-<connector>], the card's PCI device with
    `drm/renderD*`, `driver` and `boot_vga`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "class/drm").mkdir(parents=True)

    def gpu(self, card: str, driver: str, render: str | None, boot_vga: bool | None = None,
            connectors: tuple[str, ...] = (), pci: str | None = None, vendor: str | None = None,
            device_id: str | None = None) -> None:
        device = self.root / "devices/pci0000:00" / (pci or f"0000:00:{card}")
        (device / "drm" / card).mkdir(parents=True)
        if render:
            (device / "drm" / render).mkdir()
        drivers = self.root / "bus/pci/drivers" / driver
        drivers.mkdir(parents=True, exist_ok=True)
        os.symlink(drivers, device / "driver")
        if vendor is not None:
            (device / "vendor").write_text(vendor + "\n")
        if device_id is not None:
            (device / "device").write_text(device_id + "\n")
        if boot_vga is not None:
            (device / "boot_vga").write_text("1\n" if boot_vga else "0\n")
        card_dir = self.root / "class/drm" / card
        card_dir.mkdir()
        os.symlink(device, card_dir / "device")
        for connector in connectors:
            (self.root / "class/drm" / f"{card}-{connector}").mkdir()


class ResolveCaptureGpuTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.sysfs = FakeSysfs(Path(temporary.name))

    def resolve(self, output: str | None) -> gpu.CaptureGpu | None:
        return gpu.resolve_capture_gpu(output, self.sysfs.root)

    def hybrid(self, nvidia_boot_vga: bool) -> None:
        self.sysfs.gpu("card0", "i915", "renderD129", boot_vga=not nvidia_boot_vga, connectors=("eDP-1",))
        self.sysfs.gpu("card1", "nvidia", "renderD128", boot_vga=nvidia_boot_vga, connectors=("HDMI-A-1", "DP-1"))

    def test_hybrid_laptop_encodes_on_the_gpu_that_drives_the_physical_output(self) -> None:
        self.hybrid(nvidia_boot_vga=True)  # boot_vga must not override the connector
        self.assertEqual(self.resolve("eDP-1"), gpu.CaptureGpu("/dev/dri/renderD129", "i915", "vaapi"))

    def test_an_external_output_on_the_nvidia_card_uses_nvenc(self) -> None:
        self.hybrid(nvidia_boot_vga=False)
        self.assertEqual(self.resolve("HDMI-A-1"), gpu.CaptureGpu("/dev/dri/renderD128", "nvidia", "nvenc"))

    def test_connector_names_match_exactly(self) -> None:
        self.hybrid(nvidia_boot_vga=True)  # `DP-1` must not match `card0-eDP-1`
        self.assertEqual(self.resolve("DP-1"), gpu.CaptureGpu("/dev/dri/renderD128", "nvidia", "nvenc"))

    def test_nvidia_only_host_uses_nvenc(self) -> None:
        self.sysfs.gpu("card0", "nvidia", "renderD128", boot_vga=True, connectors=("eDP-1",))
        self.assertEqual(self.resolve("eDP-1"), gpu.CaptureGpu("/dev/dri/renderD128", "nvidia", "nvenc"))

    def test_amd_host_uses_vaapi(self) -> None:
        self.sysfs.gpu("card0", "amdgpu", "renderD128", boot_vga=True, connectors=("eDP-1",))
        self.assertEqual(self.resolve("eDP-1"), gpu.CaptureGpu("/dev/dri/renderD128", "amdgpu", "vaapi"))

    def test_intel_xe_host_uses_vaapi(self) -> None:
        self.sysfs.gpu("card0", "xe", "renderD128", boot_vga=True, connectors=("eDP-1",))
        self.assertEqual(self.resolve("eDP-1").encoder, "vaapi")

    def test_headless_output_uses_the_boot_vga_card(self) -> None:
        self.hybrid(nvidia_boot_vga=True)
        self.assertEqual(self.resolve("DIARCHY"), gpu.CaptureGpu("/dev/dri/renderD128", "nvidia", "nvenc"))
        self.assertEqual(self.resolve(None), gpu.CaptureGpu("/dev/dri/renderD128", "nvidia", "nvenc"))

    def test_headless_output_on_a_hybrid_laptop_booting_on_intel(self) -> None:
        self.hybrid(nvidia_boot_vga=False)
        self.assertEqual(self.resolve("DIARCHY"), gpu.CaptureGpu("/dev/dri/renderD129", "i915", "vaapi"))

    def test_without_boot_vga_the_first_card_with_a_render_node_wins(self) -> None:
        self.sysfs.gpu("card0", "simpledrm", None)
        self.sysfs.gpu("card1", "amdgpu", "renderD128")
        self.sysfs.gpu("card2", "nvidia", "renderD129")
        self.assertEqual(self.resolve("DIARCHY"), gpu.CaptureGpu("/dev/dri/renderD128", "amdgpu", "vaapi"))

    def test_a_boot_vga_card_without_a_render_node_is_skipped(self) -> None:
        self.sysfs.gpu("card0", "simpledrm", None, boot_vga=True)
        self.sysfs.gpu("card1", "i915", "renderD128", boot_vga=False)
        self.assertEqual(self.resolve(None), gpu.CaptureGpu("/dev/dri/renderD128", "i915", "vaapi"))

    def test_a_connector_on_a_card_without_a_render_node_falls_back(self) -> None:
        self.sysfs.gpu("card0", "simpledrm", None, boot_vga=False, connectors=("eDP-1",))
        self.sysfs.gpu("card1", "i915", "renderD128", boot_vga=True)
        self.assertEqual(self.resolve("eDP-1"), gpu.CaptureGpu("/dev/dri/renderD128", "i915", "vaapi"))

    def test_unknown_connector_falls_back_to_the_boot_vga_card(self) -> None:
        self.hybrid(nvidia_boot_vga=False)
        self.assertEqual(self.resolve("DP-9"), gpu.CaptureGpu("/dev/dri/renderD129", "i915", "vaapi"))

    def test_no_gpu_resolves_to_none(self) -> None:
        self.assertIsNone(self.resolve("eDP-1"))
        self.assertIsNone(gpu.resolve_capture_gpu("eDP-1", self.sysfs.root / "missing"))


FIXTURES = sorted((ROOT / "tests/fixtures/sysfs").glob("*.json"))


def build_fixture(root: Path, cards: list[dict]) -> FakeSysfs:
    sysfs = FakeSysfs(root)
    for entry in cards:
        sysfs.gpu(entry["card"], entry["driver"], entry["render"], boot_vga=entry["boot_vga"],
                  connectors=tuple(entry["connectors"]), pci=entry["pci"], vendor=entry["vendor"],
                  device_id=entry["device"])
    return sysfs


class FixtureCorpusTests(unittest.TestCase):
    def test_corpus_covers_every_layout(self) -> None:
        self.assertGreaterEqual(len(FIXTURES), 22)

    def test_every_fixture_resolves_as_expected(self) -> None:
        for path in FIXTURES:
            data = json.loads(path.read_text())
            with self.subTest(fixture=path.stem), tempfile.TemporaryDirectory() as temporary:
                sysfs = build_fixture(Path(temporary), data["cards"])
                self.assertIn("source", data)
                self.assertIn("DIARCHY", data["expect"])
                self.assertIn("null", data["expect"])
                for output, expected in data["expect"].items():
                    found = gpu.resolve_capture_gpu(None if output == "null" else output, sysfs.root)
                    self.assertEqual(None if found is None else [found.render_node, found.driver, found.encoder],
                                     expected, output)
                    if found is None or found.encoder is None:
                        self.assertTrue(gpu.unsupported_reason(found), output)
                    else:
                        self.assertIsNone(gpu.unsupported_reason(found), output)

    def test_snapshot_round_trips_the_fixture_cards(self) -> None:
        for path in FIXTURES:
            data = json.loads(path.read_text())
            with self.subTest(fixture=path.stem), tempfile.TemporaryDirectory() as temporary:
                sysfs = build_fixture(Path(temporary), data["cards"])
                self.assertEqual(gpu.snapshot(sysfs.root), data["cards"])

    def test_fixtures_hold_no_personal_values(self) -> None:
        for path in FIXTURES:
            text = path.read_text()
            self.assertNotRegex(text, r"\b\d{1,3}(\.\d{1,3}){3}\b", path.stem)
            self.assertNotRegex(text, r"(?i)([0-9a-f]{2}:){5}[0-9a-f]{2}\b(?!\.)", path.stem)
            self.assertNotIn("/home/", text)


class EncoderTests(unittest.TestCase):
    def test_supported_drivers(self) -> None:
        self.assertEqual([gpu.encoder_for(d) for d in ("nvidia", "i915", "xe", "amdgpu")],
                         ["nvenc", "vaapi", "vaapi", "vaapi"])

    def test_every_other_driver_has_no_encoder(self) -> None:
        for driver in ("nouveau", "radeon", "ast", "mgag200", "simpledrm", "virtio_gpu", "vmwgfx", "evdi", "udl", ""):
            self.assertIsNone(gpu.encoder_for(driver), driver)

    def test_unsupported_reason_names_the_driver(self) -> None:
        reason = gpu.unsupported_reason(gpu.CaptureGpu("/dev/dri/renderD128", "nouveau", None))
        self.assertIn("nouveau", reason)

    def test_no_gpu_has_an_english_reason(self) -> None:
        reason = gpu.unsupported_reason(None)
        self.assertIn("No GPU", reason)
        self.assertTrue(reason.endswith("."))

    def test_supported_gpu_has_no_reason(self) -> None:
        self.assertIsNone(gpu.unsupported_reason(gpu.CaptureGpu("/dev/dri/renderD128", "i915", "vaapi")))

    def test_conf_pins_the_adapter_but_never_an_encoder_for_an_unsupported_driver(self) -> None:
        text = f"{HEADER}\nencoder = vaapi\nlocale = en\n"
        rendered = gpu.render_conf(text, gpu.CaptureGpu("/dev/dri/renderD128", "nouveau", None))
        self.assertEqual(rendered, f"{HEADER}\nlocale = en\nadapter_name = /dev/dri/renderD128\n")


class ReportRowsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / "proc"

    def hybrid(self) -> FakeSysfs:
        sysfs = FakeSysfs(self.root / "sys")
        sysfs.gpu("card0", "i915", "renderD129", boot_vga=True, connectors=("eDP-1",), pci="0000:00:02.0",
                  vendor="0x8086", device_id="0x7d55")
        sysfs.gpu("card1", "nvidia", "renderD128", boot_vga=False, pci="0000:01:00.0",
                  vendor="0x10de", device_id="0x28a0")
        return sysfs

    def test_gpus_rows_follow_the_report_contract(self) -> None:
        self.assertEqual(gpu.gpus(self.hybrid().root), [
            {"card": "card0", "vendor": "intel", "pci_id": "8086:7d55", "driver": "i915",
             "render_node": "renderD129", "boot_vga": True, "connectors": ["eDP-1"]},
            {"card": "card1", "vendor": "nvidia", "pci_id": "10de:28a0", "driver": "nvidia",
             "render_node": "renderD128", "boot_vga": False, "connectors": []},
        ])

    def test_no_drm_root_gives_no_rows(self) -> None:
        self.assertEqual(gpu.gpus(self.root / "missing"), [])
        self.assertEqual(gpu.snapshot(self.root / "missing"), [])

    def test_nvidia_info_is_none_without_the_driver(self) -> None:
        sysfs = FakeSysfs(self.root / "sys")
        sysfs.gpu("card0", "i915", "renderD128")
        self.assertIsNone(gpu.nvidia_info(sysfs.root, self.proc))

    def test_nvidia_info_reads_sysfs_version_and_modeset(self) -> None:
        sysfs = self.hybrid()
        (sysfs.root / "module/nvidia").mkdir(parents=True)
        (sysfs.root / "module/nvidia/version").write_text("580.95.05\n")
        (sysfs.root / "module/nvidia_drm/parameters").mkdir(parents=True)
        (sysfs.root / "module/nvidia_drm/parameters/modeset").write_text("Y\n")
        self.assertEqual(gpu.nvidia_info(sysfs.root, self.proc), {"driver_version": "580.95.05", "modeset": True})

    def test_nvidia_info_falls_back_to_proc_and_reports_modeset_off(self) -> None:
        sysfs = self.hybrid()
        (sysfs.root / "module/nvidia_drm/parameters").mkdir(parents=True)
        (sysfs.root / "module/nvidia_drm/parameters/modeset").write_text("N\n")
        (self.proc / "driver/nvidia").mkdir(parents=True)
        (self.proc / "driver/nvidia/version").write_text(
            "NVRM version: NVIDIA UNIX x86_64 Kernel Module  580.95.05  Thu Sep  4 2025\n")
        self.assertEqual(gpu.nvidia_info(sysfs.root, self.proc), {"driver_version": "580.95.05", "modeset": False})

    def modeset(self, sysfs: FakeSysfs, *, unreadable: str | None = None, cmdline: str | None = None,
                modprobe: str | None = None) -> object:
        """`modeset` as `nvidia_info` reports it, with the sysfs value `unreadable` (root-only), a /proc/cmdline
        and one modprobe.d file."""
        parameter = sysfs.root / "module/nvidia_drm/parameters/modeset"
        if unreadable is not None:
            parameter.parent.mkdir(parents=True)
            parameter.write_text(unreadable)
            parameter.chmod(0)
            self.addCleanup(parameter.chmod, 0o644)
            if os.access(parameter, os.R_OK):
                self.skipTest("file permissions are not enforced for this user")
        self.proc.mkdir(parents=True, exist_ok=True)
        if cmdline is not None:
            (self.proc / "cmdline").write_text(cmdline + "\n")
        modprobe_dir = self.root / "etc-modprobe.d"
        modprobe_dir.mkdir(exist_ok=True)
        if modprobe is not None:
            (modprobe_dir / "nvidia.conf").write_text(modprobe)
        return gpu.nvidia_info(sysfs.root, self.proc, [modprobe_dir, self.root / "missing"])["modeset"]

    def test_nothing_readable_leaves_modeset_unknown(self) -> None:
        self.assertIsNone(self.modeset(self.hybrid()))
        self.setUp()
        self.assertIsNone(self.modeset(self.hybrid(), cmdline="quiet splash",
                                       modprobe="# options nvidia_drm modeset=1\noptions snd_hda_intel power_save=1\n"))

    def test_unreadable_sysfs_uses_the_kernel_command_line(self) -> None:
        for flag in ("nvidia_drm.modeset=1", "nvidia-drm.modeset=1"):
            with self.subTest(flag=flag):
                self.setUp()
                self.assertIs(self.modeset(self.hybrid(), unreadable="Y\n", cmdline=f"quiet {flag} splash"), True)

    def test_unreadable_sysfs_uses_a_modprobe_file(self) -> None:
        for line in ("options nvidia_drm modeset=1 fbdev=1", "options nvidia-drm modeset=1"):
            with self.subTest(line=line):
                self.setUp()
                self.assertIs(self.modeset(self.hybrid(), unreadable="Y\n", modprobe=f"# nvidia\n{line}\n"), True)

    def test_command_line_wins_over_modprobe(self) -> None:
        self.assertIs(self.modeset(self.hybrid(), cmdline="nvidia_drm.modeset=0",
                                   modprobe="options nvidia_drm modeset=1\n"), False)

    def test_a_readable_sysfs_value_wins_over_the_fallbacks(self) -> None:
        sysfs = self.hybrid()
        (sysfs.root / "module/nvidia_drm/parameters").mkdir(parents=True)
        (sysfs.root / "module/nvidia_drm/parameters/modeset").write_text("N\n")
        self.assertIs(self.modeset(sysfs, cmdline="nvidia_drm.modeset=1"), False)


class RenderConfTests(unittest.TestCase):
    TEXT = f"{HEADER}\nlocale = en\ncapture = wlr\nfile_apps = apps.json\n"
    INTEL = gpu.CaptureGpu("/dev/dri/renderD129", "i915", "vaapi")

    def test_appends_the_managed_lines_and_keeps_everything_else(self) -> None:
        rendered = gpu.render_conf(self.TEXT, self.INTEL)
        self.assertEqual(rendered, self.TEXT + "adapter_name = /dev/dri/renderD129\nencoder = vaapi\n")

    def test_is_idempotent(self) -> None:
        once = gpu.render_conf(self.TEXT, self.INTEL)
        self.assertEqual(gpu.render_conf(once, self.INTEL), once)

    def test_replaces_existing_values_in_place_and_drops_duplicates(self) -> None:
        text = f"{HEADER}\nadapter_name = /dev/dri/renderD128\nlocale = en\nencoder = nvenc\nencoder=software\n"
        self.assertEqual(
            gpu.render_conf(text, self.INTEL),
            f"{HEADER}\nadapter_name = /dev/dri/renderD129\nlocale = en\nencoder = vaapi\n")

    def test_no_gpu_removes_stale_managed_lines(self) -> None:
        text = self.TEXT + "adapter_name = /dev/dri/renderD129\nencoder = vaapi\n"
        self.assertEqual(gpu.render_conf(text, None), self.TEXT)

    def test_a_missing_final_newline_is_handled(self) -> None:
        self.assertEqual(
            gpu.render_conf(f"{HEADER}\nlocale = en", self.INTEL),
            f"{HEADER}\nlocale = en\nadapter_name = /dev/dri/renderD129\nencoder = vaapi\n")

    def test_other_keys_that_merely_start_with_the_names_are_untouched(self) -> None:
        text = f"{HEADER}\nencoder_extra = 1\nadapter_names = x\n"
        self.assertEqual(
            gpu.render_conf(text, self.INTEL), text + "adapter_name = /dev/dri/renderD129\nencoder = vaapi\n")


class ApplyConfTests(unittest.TestCase):
    INTEL = RenderConfTests.INTEL

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.conf = Path(temporary.name) / "sunshine.conf"
        self.text = f"{HEADER}\nlocale = en\nfile_apps = apps.json\n"

    def mode(self) -> int:
        return stat.S_IMODE(self.conf.stat().st_mode)

    def test_rewrites_atomically_with_mode_600(self) -> None:
        self.conf.write_text(self.text)
        self.conf.chmod(0o644)
        self.assertTrue(gpu.apply_to_conf(self.conf, self.INTEL))
        self.assertEqual(self.conf.read_text(), self.text + "adapter_name = /dev/dri/renderD129\nencoder = vaapi\n")
        self.assertEqual(self.mode(), 0o600)
        self.assertEqual([path.name for path in self.conf.parent.iterdir()], ["sunshine.conf"])

    def test_a_second_run_changes_nothing(self) -> None:
        self.conf.write_text(self.text)
        gpu.apply_to_conf(self.conf, self.INTEL)
        before = self.conf.stat()
        self.assertFalse(gpu.apply_to_conf(self.conf, self.INTEL))
        self.assertEqual(self.conf.stat().st_mtime_ns, before.st_mtime_ns)

    def test_a_file_without_the_managed_header_is_left_alone(self) -> None:
        self.conf.write_text("locale = en\n")
        self.assertFalse(gpu.apply_to_conf(self.conf, self.INTEL))
        self.assertEqual(self.conf.read_text(), "locale = en\n")

    def test_a_missing_file_is_not_created(self) -> None:
        self.assertFalse(gpu.apply_to_conf(self.conf, self.INTEL))
        self.assertFalse(self.conf.exists())

    def test_an_unwritable_directory_raises_oserror(self) -> None:
        self.conf.write_text(self.text)
        self.conf.parent.chmod(0o500)
        self.addCleanup(self.conf.parent.chmod, 0o700)
        if os.access(self.conf.parent, os.W_OK):
            self.skipTest("directory permissions are not enforced for this user")
        with self.assertRaises(OSError):
            gpu.apply_to_conf(self.conf, self.INTEL)


if __name__ == "__main__":
    unittest.main()
