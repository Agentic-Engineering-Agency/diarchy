from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_diarchy_host_probe import FIREWALL_TAILSCALE_COMMAND, HYPRCTL_ONE, NOOP, PACKAGE_TOOLS, UFW
from tests.test_diarchy_host_support import HOST, AgentCase

# The only system utilities install-host.sh (and the probe, via `#!/usr/bin/env python3`) may rely on.
SYSTEM_UTILITIES = ("bash", "env", "install", "mkdir", "mv", "rm", "cp", "cat", "dirname", "find", "python3")
LAN_MOUSE = "install-lan-mouse.sh"
CLIPBOARD = "install-clipboard-file-transfer.sh"
STREAMING = "install.sh"
STUBS = (LAN_MOUSE, CLIPBOARD, STREAMING)
FAILING_CLI = "def main(argv):\n    print('definitely not json')\n    return 0\n"


class InstallHostCase(AgentCase):
    """Runs the real install-host.sh and the real agent from a temp payload tree on a sandboxed PATH."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_sudo()
        self.payload = self.root / "payload"
        self.host = self.payload / "host"
        self.host.mkdir(parents=True)
        shutil.copy2(HOST / "install-host.sh", self.host / "install-host.sh")
        shutil.copy2(HOST / "diarchy-host", self.host / "diarchy-host")
        shutil.copytree(HOST / "diarchy_host", self.host / "diarchy_host", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(HOST / "VERSION", self.host / "VERSION")
        (self.payload / "VERSION").write_text("1.0.0\n")
        for stub in STUBS:
            self.stub(stub)

        sysbin = self.root / "sysbin"
        sysbin.mkdir()
        for name in SYSTEM_UTILITIES:
            target = Path(sys.executable) if name == "python3" else shutil.which(name)
            self.assertIsNotNone(target, f"system utility {name} not found")
            (sysbin / name).symlink_to(target)
        self.env["PATH"] = f"{self.tools}:{sysbin}"
        self.bash = str(sysbin / "bash")

        self.os_release = self.root / "os-release"
        self.os_release.write_text("NAME=Arch Linux\nID=arch\n")
        self.sys_net = self.root / "sys-net"
        self.sys_net.mkdir()
        self.uinput = self.root / "dev-uinput"
        self.logind = self.root / "logind"
        self.proc = self.root / "proc"
        lid = self.proc / "acpi/button/lid/LID0"
        lid.mkdir(parents=True)
        (lid / "state").write_text("state:      open\n")
        self.sysfs = self.root / "sysfs"
        device = self.sysfs / "devices/pci0000:00/0000:00:02.0"
        (device / "drm/renderD128").mkdir(parents=True)
        (device / "drm/card0").mkdir()
        (device / "boot_vga").write_text("1\n")
        (device / "driver").symlink_to("../../../bus/pci/drivers/i915")
        drm = self.sysfs / "class/drm"
        drm.mkdir(parents=True)
        (drm / "card0").mkdir()
        (drm / "card0/device").symlink_to(device)
        self.env.update({
            "DIARCHY_OS_RELEASE": str(self.os_release),
            "DIARCHY_MACHINE": "x86_64",
            "DIARCHY_SYS_NET": str(self.sys_net),
            "DIARCHY_UINPUT_PATH": str(self.uinput),
            "DIARCHY_LOGIND_DIRS": str(self.logind),
            "DIARCHY_PROC_ROOT": str(self.proc),
            "DIARCHY_SYSFS_ROOT": str(self.sysfs),
        })
        self.tool("hyprctl", HYPRCTL_ONE, "HYPRCTL_BIN")
        self.fake_systemctl()
        for name in PACKAGE_TOOLS:
            self.tool(name, NOOP)
        self.tool("openssl", NOOP, "OPENSSL_BIN")
        # `python3` itself must stay the real interpreter; the agent's PyGObject check uses this stub.
        self.tool("gi-python", NOOP, "PYTHON_BIN")
        (self.home / ".local/share/omarchy").mkdir(parents=True)

    def tearDown(self) -> None:
        self.assertEqual(self.sudo_calls(), [], "the installer must never invoke sudo")
        super().tearDown()

    def stub(self, name: str, code: int = 0) -> None:
        path = self.host / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            'test -x "$HOME/.local/bin/diarchy-host" || exit 9\n'
            f"printf '%s\\n' {name} >> \"$FAKE_LOG\"\n"
            f"exit {code}\n"
        )
        path.chmod(0o755)

    def run_install(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.bash, str(self.host / "install-host.sh"), *args],
            env=self.env | (env or {}), text=True, capture_output=True, check=False,
        )

    def stub_runs(self) -> list[str]:
        return [line for line in self.calls() if line in STUBS]

    def allow_streaming(self) -> None:
        self.uinput.write_text("")
        (self.logind / "logind.conf.d").mkdir(parents=True)
        (self.logind / "logind.conf.d/60-lid.conf").write_text("[Login]\nHandleLidSwitch=ignore\n")

    @property
    def agent_dir(self) -> Path:
        return self.home / ".local/share/diarchy/agent"


class InstallFlowTests(InstallHostCase):
    def test_supported_host_installs_agent_and_runs_sub_installers_in_order(self) -> None:
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stub_runs(), [LAN_MOUSE, CLIPBOARD])
        self.assertEqual(result.stdout.splitlines()[-1], "[ok] Diarchy host installed (1.0.0)")
        self.assertEqual(result.stderr, "")
        launcher = self.home / ".local/bin/diarchy-host"
        self.assertTrue(launcher.is_file() and launcher.stat().st_mode & 0o111)
        self.assertTrue((self.agent_dir / "VERSION").is_file())
        self.assertTrue((self.agent_dir / "diarchy_host/cli.py").is_file())
        self.assertEqual(list(self.agent_dir.rglob("__pycache__")), [])

    def test_pycache_in_payload_is_not_copied(self) -> None:
        cache = self.host / "diarchy_host/__pycache__"
        cache.mkdir()
        (cache / "cli.cpython-311.pyc").write_bytes(b"\0")
        self.assertEqual(self.run_install().returncode, 0)
        self.assertEqual(list(self.agent_dir.rglob("__pycache__")), [])
        self.assertEqual(list(self.agent_dir.rglob("*.pyc")), [])

    def test_appledouble_files_in_payload_are_not_installed(self) -> None:
        (self.host / "diarchy_host/._cli.py").write_bytes(b"\0\x05\x16\x07")
        (self.host / "diarchy_host/.___init__.py").write_bytes(b"\0\x05\x16\x07")
        (self.host / "._diarchy-host").write_bytes(b"\0\x05\x16\x07")
        (self.host / "._VERSION").write_bytes(b"\0\x05\x16\x07")
        self.assertEqual(self.run_install().returncode, 0)
        self.assertTrue((self.agent_dir / "diarchy_host/cli.py").is_file())
        self.assertEqual([p for p in self.agent_dir.rglob("._*")], [])
        self.assertEqual([p for p in (self.home / ".local/bin").glob("._*")], [])

    def test_with_streaming_probes_for_streaming_and_runs_install_sh_last(self) -> None:
        result = self.run_install("--with-streaming")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("  - uinput-access:", result.stderr)
        self.assertEqual(self.stub_runs(), [])
        self.allow_streaming()
        result = self.run_install("--with-streaming")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stub_runs(), [LAN_MOUSE, CLIPBOARD, STREAMING])
        self.assertEqual(result.stdout.splitlines()[-1], "[ok] Diarchy host installed (1.0.0)")

    def test_without_streaming_flag_streaming_actions_are_not_required(self) -> None:
        self.assertEqual(self.run_install().returncode, 0)
        self.assertNotIn(STREAMING, self.stub_runs())

    def test_agent_dir_override(self) -> None:
        target = self.root / "custom-agent"
        result = self.run_install(env={"DIARCHY_AGENT_DIR": str(target)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / "diarchy_host/cli.py").is_file())
        self.assertFalse(self.agent_dir.exists())


class GateTests(InstallHostCase):
    def test_unsupported_platform_fails_before_any_sub_installer(self) -> None:
        self.os_release.write_text("NAME=Ubuntu\nID=ubuntu\nID_LIKE=debian\n")
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("[fail] install-host.sh: unsupported platform:"), result.stderr)
        self.assertIn("An Arch Linux family distribution is required (found: ubuntu).", result.stderr)
        self.assertEqual(self.stub_runs(), [])
        self.assertTrue((self.agent_dir / "diarchy_host/cli.py").is_file())
        self.assertTrue((self.home / ".local/bin/diarchy-host").is_file())

    def test_unsupported_reasons_are_joined_with_semicolons(self) -> None:
        self.os_release.write_text("NAME=Ubuntu\nID=ubuntu\n")
        result = self.run_install(env={"DIARCHY_MACHINE": "aarch64"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("(found: ubuntu).; An x86_64 machine is required (found: aarch64).", result.stderr)

    def test_missing_packages_list_the_exact_command_and_run_nothing(self) -> None:
        (self.tools / "jq").unlink()
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        lines = result.stderr.splitlines()
        self.assertEqual(lines[0], "[fail] install-host.sh: required host actions are pending")
        self.assertTrue(lines[1].startswith("  - install-packages: "), lines[1])
        self.assertEqual(lines[2], "    sudo pacman -S --needed jq")
        self.assertEqual(len(lines), 3)
        self.assertEqual(self.stub_runs(), [])

    def test_missing_gi_packages_use_the_full_package_command(self) -> None:
        self.tool("gi-python", "import sys\nsys.exit(1)\n", "PYTHON_BIN")
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("    sudo pacman -S --needed python-gobject gtk3 gtk-layer-shell", result.stderr)
        self.assertEqual(self.stub_runs(), [])

    def test_every_required_action_is_listed_and_recommended_ones_are_not(self) -> None:
        (self.tools / "jq").unlink()
        result = self.run_install("--with-streaming")
        self.assertEqual(result.returncode, 2)
        for action_id in ("install-packages", "uinput-access"):
            self.assertIn(f"  - {action_id}: ", result.stderr)
        self.assertIn("sudo udevadm control --reload", result.stderr)
        self.assertNotIn("lid-policy", result.stderr)
        self.assertEqual(self.stub_runs(), [])

    def test_lid_policy_is_only_a_warning(self) -> None:
        self.uinput.write_text("")
        result = self.run_install("--with-streaming")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[warn] lid-policy: sudo mkdir -p /etc/systemd/logind.conf.d", result.stdout)
        self.assertEqual(self.stub_runs(), [LAN_MOUSE, CLIPBOARD, STREAMING])

    def test_desktop_without_a_lid_has_no_lid_policy_line(self) -> None:
        shutil.rmtree(self.proc / "acpi/button/lid")
        self.uinput.write_text("")
        result = self.run_install("--with-streaming")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("lid-policy", result.stdout + result.stderr)
        self.assertEqual(self.stub_runs(), [LAN_MOUSE, CLIPBOARD, STREAMING])

    def test_recommended_actions_warn_and_do_not_block(self) -> None:
        self.fake_systemctl(active=[UFW])
        (self.sys_net / "tailscale0").mkdir()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"[warn] firewall-tailscale: {FIREWALL_TAILSCALE_COMMAND}\n", result.stdout)
        self.assertEqual(self.stub_runs(), [LAN_MOUSE, CLIPBOARD])
        self.assertEqual(result.stdout.splitlines()[-1], "[ok] Diarchy host installed (1.0.0)")

    def test_broken_probe_is_reported(self) -> None:
        (self.host / "diarchy_host/cli.py").write_text(FAILING_CLI)
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("[fail] install-host.sh: diarchy-host probe failed: ", result.stderr)
        self.assertEqual(self.stub_runs(), [])

    def test_probe_error_envelope_message_is_surfaced(self) -> None:
        cli = (self.host / "diarchy_host/cli.py")
        cli.write_text(
            "import json\n"
            "def main(argv):\n"
            "    print(json.dumps({'schema': 1, 'ok': False, 'command': 'probe',"
            " 'error': {'code': 'internal', 'message': 'boom'}}))\n"
            "    return 1\n"
        )
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("diarchy-host probe failed: boom", result.stderr)
        self.assertEqual(self.stub_runs(), [])

    def test_probe_ok_without_platform_is_reported_as_failed_probe(self) -> None:
        (self.host / "diarchy_host/cli.py").write_text(
            "def main(argv):\n    print('{\"schema\":1,\"ok\":true,\"command\":\"probe\"}')\n    return 0\n"
        )
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("diarchy-host probe failed: ", result.stderr)
        self.assertEqual(self.stub_runs(), [])


class OutcomeTests(InstallHostCase):
    def test_failing_sub_installer_aborts_the_run(self) -> None:
        self.stub(LAN_MOUSE, code=3)
        result = self.run_install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] install-host.sh: install-lan-mouse.sh failed", result.stderr)
        self.assertEqual(self.stub_runs(), [LAN_MOUSE])
        self.assertNotIn("[ok]", result.stdout)

    def test_later_failure_names_the_failing_installer(self) -> None:
        self.allow_streaming()
        self.stub(STREAMING, code=5)
        result = self.run_install("--with-streaming")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] install-host.sh: install.sh failed", result.stderr)
        self.assertEqual(self.stub_runs(), [LAN_MOUSE, CLIPBOARD, STREAMING])

    def test_rerun_is_idempotent_and_replaces_agent_files(self) -> None:
        self.assertEqual(self.run_install().returncode, 0)
        stale = self.agent_dir / "stale.txt"
        stale.write_text("old")
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale.exists())
        self.assertTrue((self.agent_dir / "diarchy_host/cli.py").is_file())
        leftovers = [path.name for path in self.agent_dir.parent.iterdir() if path.name != "agent"]
        self.assertEqual(leftovers, [])

    def test_installed_agent_is_replaced_with_the_payload_copy(self) -> None:
        self.assertEqual(self.run_install().returncode, 0)
        (self.host / "diarchy_host/common.py").write_text((self.host / "diarchy_host/common.py").read_text() + "\n# v2\n")
        self.assertEqual(self.run_install().returncode, 0)
        self.assertIn("# v2", (self.agent_dir / "diarchy_host/common.py").read_text())

    def test_directories_created_by_the_installer_are_private(self) -> None:
        self.assertEqual(self.run_install().returncode, 0)
        self.assertEqual(self.agent_dir.stat().st_mode & 0o777, 0o700)

    def test_bad_arguments_print_usage_and_exit_64(self) -> None:
        for args in (("--bogus",), ("extra",), ("--with-streaming", "--bogus")):
            result = self.run_install(*args)
            self.assertEqual(result.returncode, 64, args)
            self.assertTrue(result.stderr.startswith("usage: "), result.stderr)
            self.assertEqual(result.stdout, "")
        self.assertEqual(self.stub_runs(), [])
        self.assertFalse(self.agent_dir.exists())

    def test_payload_version_wins_over_host_version(self) -> None:
        (self.payload / "VERSION").write_text("2.3.4\n")
        result = self.run_install()
        self.assertEqual(result.stdout.splitlines()[-1], "[ok] Diarchy host installed (2.3.4)")

    def test_host_version_is_the_fallback(self) -> None:
        (self.payload / "VERSION").unlink()
        (self.host / "VERSION").write_text("1.0.0\n")
        result = self.run_install()
        self.assertEqual(result.stdout.splitlines()[-1], "[ok] Diarchy host installed (1.0.0)")

    def test_missing_payload_pieces_fail_cleanly(self) -> None:
        for name in ("diarchy_host", "diarchy-host", "VERSION"):
            path = self.host / name
            backup = self.root / f"backup-{name}"
            path.rename(backup)
            result = self.run_install()
            self.assertEqual(result.returncode, 2, name)
            self.assertTrue(result.stderr.startswith("[fail] install-host.sh: "), result.stderr)
            self.assertFalse(self.agent_dir.exists())
            backup.rename(path)

    def test_missing_python3_fails_cleanly(self) -> None:
        (self.root / "sysbin/python3").unlink()
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "[fail] install-host.sh: python3 is required\n")
        self.assertEqual(self.stub_runs(), [])

    def test_signal_between_the_two_moves_restores_the_previous_agent(self) -> None:
        self.assertEqual(self.run_install().returncode, 0)
        (self.agent_dir / "marker").write_text("previous agent")
        real_mv = shutil.which("mv")
        # Swallows the `staged -> AGENT_DIR` move and SIGTERMs the installer instead (agent dir is then missing).
        self.tool("mv", (
            "import os, signal, sys\n"
            "if os.path.basename(sys.argv[1]).startswith('agent.new.'):\n"
            "    os.kill(os.getppid(), signal.SIGTERM)\n"
            "    sys.exit(0)\n"
            f"os.execv({real_mv!r}, ['mv', *sys.argv[1:]])\n"))
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.agent_dir / "marker").read_text(), "previous agent")
        self.assertEqual(sorted(path.name for path in self.agent_dir.parent.iterdir()), ["agent"])


class StaticTests(unittest.TestCase):
    SCRIPT = HOST / "install-host.sh"

    def test_script_is_executable_and_syntactically_valid(self) -> None:
        self.assertTrue(self.SCRIPT.stat().st_mode & 0o111)
        subprocess.run(["bash", "-n", str(self.SCRIPT)], check=True)

    def test_script_never_executes_sudo(self) -> None:
        code = [line for line in self.SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")]
        self.assertFalse([line for line in code if "sudo" in line])


if __name__ == "__main__":
    unittest.main()
