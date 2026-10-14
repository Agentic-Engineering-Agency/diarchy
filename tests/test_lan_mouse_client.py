from __future__ import annotations

import os
import json
import plistlib
import re
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


def regex_escape(text: str) -> str:
    """The pattern diarchy-kvm hands to pgrep and pkill (extended regular expression)."""
    return re.sub(r"([\][\\.*+?(){}|^$])", r"\\\1", text)


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client"
INSTALLER = CLIENT / "install-lan-mouse.sh"
TOGGLE = CLIENT / "diarchy-kvm"
DOCTOR = CLIENT / "diarchy-kvm-client-doctor"
ACCESSIBILITY_CHECK = CLIENT / "diarchy-kvm-accessibility-check"
VERSION = "0.11.0"
SHA256 = "5ff9965d05be7f125b1d75b9e7259d874ec98aa086ce40012522bc25406500a9"
HOST_FINGERPRINT = ":".join(["bb"] * 32)
BUNDLE_ID = "de.feschber.LanMouse"
TOGGLE_BUNDLE_ID = "dev.sebastiangmz.diarchy-kvm"
INSTALLED_COMMANDS = (
    "diarchy-kvm",
    "diarchy-kvm-client-doctor",
    "diarchy-kvm-accessibility-check",
    "diarchy-config.sh",
    "diarchy-route.sh",
    "diarchy-ssh",
    "diarchy-kvm-configure",
    "diarchy-mac-fingerprint",
    "diarchy-host-key",
)
NOT_CONFIGURED = "[info] Not configured yet; open Diarchy to create diarchy.conf"


def clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DIARCHY_", "LAN_MOUSE_")) and key != "XDG_CONFIG_HOME"
    }


def write_diarchy_conf(
    path: Path,
    *,
    side: str = "left",
    lan: str = "192.0.2.10",
    tailscale: str = "100.64.0.20",
    fingerprint: str = HOST_FINGERPRINT.upper(),
) -> None:
    lines = ["# Managed by Diarchy", "DIARCHY_SSH_USER=tester"]
    if lan:
        lines.append(f"DIARCHY_LAN_HOST={lan}")
    if tailscale:
        lines.append(f"DIARCHY_TAILSCALE_HOST={tailscale}")
    lines.append(f"DIARCHY_KVM_SIDE={side}")
    if fingerprint:
        lines.append(f"DIARCHY_HOST_FINGERPRINT={fingerprint}")
    path.write_text("\n".join(lines) + "\n")


class LanMouseClientInstallerTests(unittest.TestCase):
    def make_environment(
        self,
        *,
        checksum_ok: bool = True,
        existing_bundle_id: str | None = None,
        legacy_toggle_bundle_id: str | None = None,
        config: dict[str, str] | None = None,
    ) -> tuple[Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        home = root / "home"
        tools = root / "tools"
        home.mkdir()
        tools.mkdir()

        curl = tools / "curl"
        shasum = tools / "shasum"
        ditto = tools / "ditto"
        codesign = tools / "codesign"
        xattr = tools / "xattr"
        open_bin = tools / "open"

        curl.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "pathlib.Path(__file__).with_name('curl.argv').write_text(json.dumps(sys.argv[1:]))\n"
            "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'archive')\n"
        )
        shasum.write_text(
            "#!/usr/bin/env bash\n"
            + (
                f"echo '{SHA256}  '${{@: -1}}\n"
                if checksum_ok
                else "echo '0000  '${@: -1}\n"
            )
        )
        ditto.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, plistlib, shutil, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['-x', '-k']:\n"
            "    app = pathlib.Path(args[-1]) / 'Lan Mouse.app'\n"
            "    binary = app / 'Contents' / 'MacOS' / 'lan-mouse'\n"
            "    binary.parent.mkdir(parents=True)\n"
            "    binary.write_text('#!/usr/bin/env bash\\n'"
            "                      'if [[ ${1:-} == --version ]]; then echo lan-mouse 0.11.0; fi\\n')\n"
            "    binary.chmod(0o755)\n"
            "    with (app / 'Contents' / 'Info.plist').open('wb') as handle:\n"
            "        plistlib.dump({'CFBundleIdentifier': 'de.feschber.LanMouse', "
            "'CFBundleExecutable': 'lan-mouse'}, handle)\n"
            "else:\n"
            "    shutil.copytree(args[-2], args[-1])\n"
        )
        codesign.write_text("#!/usr/bin/env bash\nexit 0\n")
        xattr.write_text("#!/usr/bin/env bash\nexit 0\n")
        open_bin.write_text("#!/usr/bin/env bash\nexit 0\n")
        for executable in (curl, shasum, ditto, codesign, xattr, open_bin):
            executable.chmod(0o755)

        if existing_bundle_id:
            contents = home / "Applications" / "Lan Mouse.app" / "Contents"
            contents.mkdir(parents=True)
            with (contents / "Info.plist").open("wb") as handle:
                plistlib.dump({"CFBundleIdentifier": existing_bundle_id}, handle)
        if legacy_toggle_bundle_id:
            contents = home / "Applications" / "Diarchy KVM.app" / "Contents"
            contents.mkdir(parents=True)
            with (contents / "Info.plist").open("wb") as handle:
                plistlib.dump({"CFBundleIdentifier": legacy_toggle_bundle_id}, handle)

        conf = root / "diarchy.conf"
        if config is not None:
            write_diarchy_conf(conf, **config)

        return home, clean_environment() | {
            "HOME": str(home),
            "DIARCHY_CONFIG": str(conf),
            "CURL_BIN": str(curl),
            "SHA256_BIN": str(shasum),
            "DITTO_BIN": str(ditto),
            "CODESIGN_BIN": str(codesign),
            "XATTR_BIN": str(xattr),
            "OPEN_BIN": str(open_bin),
            "OSACOMPILE_BIN": str(root / "missing-osacompile"),
        }

    def run_installer(
        self,
        *args: str,
        checksum_ok: bool = True,
        existing_bundle_id: str | None = None,
        legacy_toggle_bundle_id: str | None = None,
        config: dict[str, str] | None = None,
    ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        self.assertTrue(INSTALLER.exists(), "client/install-lan-mouse.sh must exist")
        home, environment = self.make_environment(
            checksum_ok=checksum_ok,
            existing_bundle_id=existing_bundle_id,
            legacy_toggle_bundle_id=legacy_toggle_bundle_id,
            config=config,
        )
        result = subprocess.run(
            [str(INSTALLER), *args],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        return home, result

    def test_dry_run_is_pinned_rootless_and_non_mutating(self) -> None:
        self.assertTrue(INSTALLER.exists(), "client/install-lan-mouse.sh must exist")
        with tempfile.TemporaryDirectory() as temp_home:
            result = subprocess.run(
                [str(INSTALLER), "--dry-run"],
                env=clean_environment() | {"HOME": temp_home},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Lan Mouse {VERSION}", result.stdout)
            self.assertIn(SHA256, result.stdout)
            self.assertIn("Applications/Lan Mouse.app", result.stdout)
            self.assertIn("diarchy-kvm-configure", result.stdout)
            for command in INSTALLED_COMMANDS:
                self.assertIn(command, result.stdout)
            self.assertIn("legacy", result.stdout)
            self.assertNotIn("opt-in toggle", result.stdout)
            self.assertNotIn("sudo", result.stdout.lower())
            self.assertEqual(list(Path(temp_home).iterdir()), [])

    def test_sec32_download_is_limited_to_https_and_swaps_existing_app_by_rename(self) -> None:
        home, result = self.run_installer(existing_bundle_id=BUNDLE_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads((home.parent / "tools" / "curl.argv").read_text())
        self.assertEqual(argv[argv.index("--proto") + 1], "=https")
        self.assertEqual(argv[argv.index("--proto-redir") + 1], "=https")
        applications = home / "Applications"
        self.assertTrue((applications / "Lan Mouse.app" / "Contents" / "MacOS" / "lan-mouse").exists())
        self.assertEqual([path.name for path in applications.glob(".Lan Mouse.app.*")], [])

    def test_sec33_unset_tmpdir_uses_user_cache_not_tmp(self) -> None:
        home, environment = self.make_environment()
        environment.pop("TMPDIR", None)
        result = subprocess.run([str(INSTALLER)], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        cache = home / "Library" / "Caches"
        self.assertTrue(cache.is_dir())
        self.assertEqual(list(cache.glob("diarchy-kvm-client.*")), [])

    def test_authorize_fingerprint_option_is_removed(self) -> None:
        home, result = self.run_installer("--authorize-fingerprint", HOST_FINGERPRINT)
        self.assertEqual(result.returncode, 64)
        self.assertIn("usage:", result.stderr)
        self.assertNotIn("--authorize-fingerprint", result.stderr)
        self.assertFalse((home / "Applications").exists())

    def test_rejects_checksum_mismatch(self) -> None:
        home, result = self.run_installer(checksum_ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stderr.lower())
        self.assertFalse((home / "Applications" / "Lan Mouse.app").exists())

    def test_rejects_foreign_existing_bundle(self) -> None:
        home, result = self.run_installer(existing_bundle_id="example.foreign")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to replace", result.stderr.lower())
        info = home / "Applications" / "Lan Mouse.app" / "Contents" / "Info.plist"
        with info.open("rb") as handle:
            self.assertEqual(plistlib.load(handle)["CFBundleIdentifier"], "example.foreign")

    def test_installs_commands_without_config_and_succeeds(self) -> None:
        home, result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(NOT_CONFIGURED, result.stdout)

        app = home / "Applications" / "Lan Mouse.app"
        self.assertTrue((app / "Contents" / "MacOS" / "lan-mouse").is_file())
        self.assertFalse((home / "Applications" / "Diarchy KVM.app").exists())
        for command in INSTALLED_COMMANDS:
            installed = home / ".local" / "bin" / command
            self.assertTrue(installed.is_file(), command)
            self.assertEqual(installed.stat().st_mode & 0o777, 0o755, command)
        self.assertFalse((home / ".config" / "lan-mouse" / "config.toml").exists())

    def test_reports_unusable_diarchy_conf_and_skips_configuration(self) -> None:
        home, environment = self.make_environment()
        Path(environment["DIARCHY_CONFIG"]).write_text(
            "# Managed by Diarchy\nDIARCHY_SSH_USER=tester\n"
            "DIARCHY_LAN_HOST=192.0.2.10\nDIARCHY_KVM_SIDE=middle\n"
        )
        result = subprocess.run(
            [str(INSTALLER)], env=environment, text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "[info] diarchy.conf is not usable (DIARCHY_KVM_SIDE is invalid); "
            "skipping Lan Mouse configuration",
            result.stdout,
        )
        self.assertNotIn("Not configured yet", result.stdout)
        self.assertNotIn("diarchy-config:", result.stdout)
        self.assertFalse((home / ".config" / "lan-mouse" / "config.toml").exists())
        self.assertTrue((home / ".local" / "bin" / "diarchy-kvm-configure").is_file())

    def test_runs_diarchy_kvm_configure_when_config_loads_idempotently(self) -> None:
        home, environment = self.make_environment(
            config={"side": "right", "lan": "192.0.2.10", "tailscale": "100.64.0.20"}
        )
        first = subprocess.run(
            [str(INSTALLER)], env=environment, text=True, capture_output=True, check=False
        )
        second = subprocess.run(
            [str(INSTALLER)], env=environment, text=True, capture_output=True, check=False
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn(NOT_CONFIGURED, first.stdout)
        self.assertIn("Lan Mouse configuration written (side: right)", first.stdout)

        config = home / ".config" / "lan-mouse" / "config.toml"
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        source = config.read_text()
        self.assertIn(f'"{HOST_FINGERPRINT}" = "host"', source)
        self.assertIn('position = "right"', source)
        self.assertIn("activate_on_startup = true", source)
        self.assertIn('ips = ["192.0.2.10", "100.64.0.20"]', source)
        self.assertEqual(list(config.parent.glob("config.toml.backup.*")), [])

    def test_removes_owned_legacy_toggle_app(self) -> None:
        home, result = self.run_installer(legacy_toggle_bundle_id=TOGGLE_BUNDLE_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((home / "Applications" / "Diarchy KVM.app").exists())
        self.assertTrue((home / "Applications" / "Lan Mouse.app").is_dir())

    def test_leaves_foreign_diarchy_kvm_app_in_place_with_info(self) -> None:
        home, result = self.run_installer(legacy_toggle_bundle_id="example.foreign")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[info]", result.stdout)
        self.assertIn("Diarchy KVM.app", result.stdout)
        info = home / "Applications" / "Diarchy KVM.app" / "Contents" / "Info.plist"
        with info.open("rb") as handle:
            self.assertEqual(plistlib.load(handle)["CFBundleIdentifier"], "example.foreign")


class LanMouseToggleTests(unittest.TestCase):
    def make_environment(self, *, enabled: bool = False) -> tuple[Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        home = root / "home"
        tmp = root / "tmp"
        home.mkdir()
        tmp.mkdir()
        app = root / "Lan Mouse.app"
        executable = app / "Contents" / "MacOS" / "lan-mouse"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/usr/bin/env bash\nexit 0\n")
        executable.chmod(0o755)
        state = root / "running"
        if enabled:
            state.touch()

        pgrep = root / "pgrep"
        open_bin = root / "open"
        pkill = root / "pkill"
        sleep = root / "sleep"
        pgrep.write_text(
            "#!/usr/bin/env bash\n"
            "[[ $* == \"-f -x $FAKE_EXECUTABLE_PATTERN\" "
            "|| $* == \"-f -x $FAKE_EXECUTABLE_PATTERN daemon\" ]] || exit 2\n"
            "[[ -f $FAKE_KVM_STATE ]] || exit 1\n"
            "echo 4242\n"
        )
        open_bin.write_text("#!/usr/bin/env bash\ntouch \"$FAKE_KVM_STATE\"\n")
        pkill.write_text(
            "#!/usr/bin/env bash\n"
            "[[ $* == \"-TERM -f -x $FAKE_EXECUTABLE_PATTERN\" "
            "|| $* == \"-TERM -f -x $FAKE_EXECUTABLE_PATTERN daemon\" ]] || exit 2\n"
            "rm -f \"$FAKE_KVM_STATE\"\n"
        )
        sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
        bridge = home / ".local" / "bin" / "diarchy-clipboard-bridge"
        bridge.parent.mkdir(parents=True)
        bridge.write_text("#!/usr/bin/env bash\nexit 0\n")
        bridge.chmod(0o755)
        for tool in (pgrep, pkill, open_bin, sleep):
            tool.chmod(0o755)
        return state, os.environ | {
            "HOME": str(home),
            "TMPDIR": str(tmp),
            "LAN_MOUSE_APP": str(app),
            "PGREP_BIN": str(pgrep),
            "PKILL_BIN": str(pkill),
            "OPEN_BIN": str(open_bin),
            "SLEEP_BIN": str(sleep),
            "FAKE_KVM_STATE": str(state),
            "FAKE_EXECUTABLE": str(executable),
            "FAKE_EXECUTABLE_PATTERN": regex_escape(str(executable)),
        }

    def run_toggle(
        self, command: str, *, enabled: bool = False
    ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        self.assertTrue(TOGGLE.exists(), "client/diarchy-kvm must exist")
        state, environment = self.make_environment(enabled=enabled)
        result = subprocess.run(
            [str(TOGGLE), command],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        return state, result

    def test_status_is_read_only(self) -> None:
        state, disabled = self.run_toggle("status")
        self.assertEqual(disabled.returncode, 1)
        self.assertIn("disabled", disabled.stdout)
        self.assertFalse(state.exists())

        state, enabled = self.run_toggle("status", enabled=True)
        self.assertEqual(enabled.returncode, 1)
        self.assertIn("degraded", enabled.stdout)
        self.assertTrue(state.exists())

    def test_missing_portals_prevent_enable_but_disable_remains_deterministic(self) -> None:
        state, turned_on = self.run_toggle("on")
        self.assertEqual(turned_on.returncode, 1)
        self.assertIn("file portals are not installed", turned_on.stderr)
        self.assertFalse(state.exists())

        state, turned_off = self.run_toggle("off", enabled=True)
        self.assertEqual(turned_off.returncode, 0, turned_off.stderr)
        self.assertFalse(state.exists())

        state, toggled_on = self.run_toggle("toggle")
        self.assertEqual(toggled_on.returncode, 1)
        self.assertIn("file portals are not installed", toggled_on.stderr)
        self.assertFalse(state.exists())

        state, toggled_off = self.run_toggle("toggle", enabled=True)
        self.assertEqual(toggled_off.returncode, 0, toggled_off.stderr)
        self.assertFalse(state.exists())

    def test_toggle_applescript_is_gone(self) -> None:
        self.assertFalse((CLIENT / "Diarchy KVM.applescript").exists())



class LanMouseAccessibilityCheckTests(unittest.TestCase):
    def test_accepts_authorized_bundle_from_system_tcc_database(self) -> None:
        self.assertTrue(
            ACCESSIBILITY_CHECK.exists(),
            "client/diarchy-kvm-accessibility-check must exist",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "TCC.db"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "create table access (service text, client text, auth_value integer)"
                )
                connection.execute(
                    "insert into access values (?, ?, ?)",
                    ("kTCCServiceAccessibility", BUNDLE_ID, 2),
                )

            result = subprocess.run(
                [str(ACCESSIBILITY_CHECK)],
                env=os.environ
                | {
                    "LAN_MOUSE_BIN": str(Path(temp_dir) / "missing"),
                    "TCC_DB": str(database),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Accessibility permission", result.stdout)

    def test_rejects_environment_override_and_missing_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "TCC.db"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "create table access (service text, client text, auth_value integer)"
                )
                connection.execute(
                    "insert into access values (?, ?, ?)",
                    ("kTCCServiceAccessibility", "attacker.bundle", 2),
                )

            result = subprocess.run(
                [str(ACCESSIBILITY_CHECK)],
                env=os.environ
                | {
                    "TCC_DB": str(database),
                    "BUNDLE_ID": "' OR auth_value=2 --",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Accessibility permission is required for Lan Mouse",
                result.stderr,
            )

def render_lan_mouse_toml(side: str, ips: str, fingerprint: str = HOST_FINGERPRINT) -> str:
    return (
        "# Managed by diarchy\n"
        "port = 4242\n"
        "[authorized_fingerprints]\n"
        f'"{fingerprint}" = "host"\n'
        "[[clients]]\n"
        f'position = "{side}"\n'
        'hostname = "host"\n'
        "activate_on_startup = true\n"
        f"ips = [{ips}]\n"
        "port = 4242\n"
    )


class LanMouseClientDoctorTests(unittest.TestCase):
    LAN = "192.0.2.10"
    TAILSCALE = "100.64.0.20"

    def make_environment(
        self,
        *,
        side: str = "left",
        lan: str = LAN,
        tailscale: str = TAILSCALE,
        configured: bool = True,
        toml: str | None = None,
        peer: str | None = TAILSCALE,
    ) -> tuple[Path, dict[str, str]]:
        self.assertTrue(DOCTOR.exists(), "client/diarchy-kvm-client-doctor must exist")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        app = root / "Lan Mouse.app"
        binary = app / "Contents" / "MacOS" / "lan-mouse"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/usr/bin/env bash\necho 'lan-mouse 0.11.0'\n")
        binary.chmod(0o755)

        conf = root / "diarchy.conf"
        if configured:
            write_diarchy_conf(conf, side=side, lan=lan, tailscale=tailscale)
        if toml is None:
            ips = ", ".join(f'"{host}"' for host in (lan, tailscale) if host)
            toml = render_lan_mouse_toml(side, ips)
        config = root / "config.toml"
        config.write_text(toml)
        config.chmod(0o600)
        certificate = root / "lan-mouse.pem"
        certificate.write_text("certificate")

        contents = {
            "codesign": "exit 0",
            "accessibility": "exit 0",
            "lsof": (
                "if [[ ${LSOF_IDLE:-0} == 1 ]]; then\n"
                "  echo 'lan-mouse 4242 user 10u IPv4 0 0 UDP *:4242'\n"
                "  exit 0\n"
                "fi\n"
                "count=0\n"
                "[[ -f $LSOF_STATE ]] && count=$(cat \"$LSOF_STATE\")\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" >\"$LSOF_STATE\"\n"
                "(( count >= 3 )) || exit 1\n"
                "echo \"lan-mouse 4242 user 11u IPv4 0 0 UDP "
                "198.51.100.5:59064->$LSOF_PEER:4242\"\n"
            ),
            "pgrep": (
                "[[ $* == \"-f -x $FAKE_EXECUTABLE_PATTERN\" "
                "|| $* == \"-f -x $FAKE_EXECUTABLE_PATTERN daemon\" ]] || exit 2\n"
                "echo 4242"
            ),
            "openssl": f"echo 'sha256 Fingerprint={HOST_FINGERPRINT.upper()}'",
            "sleep": "exit 0",
            "stat": "echo 600",
        }
        tools: dict[str, Path] = {}
        for name, body in contents.items():
            tool = root / name
            tool.write_text(f"#!/usr/bin/env bash\n{body}\n")
            tool.chmod(0o755)
            tools[name] = tool

        return root, clean_environment() | {
            "HOME": str(root),
            "DIARCHY_CONFIG": str(conf),
            "LAN_MOUSE_APP": str(app),
            "LAN_MOUSE_CONFIG": str(config),
            "LAN_MOUSE_CERT": str(certificate),
            "CODESIGN_BIN": str(tools["codesign"]),
            "ACCESSIBILITY_CHECK_BIN": str(tools["accessibility"]),
            "LSOF_BIN": str(tools["lsof"]),
            "PGREP_BIN": str(tools["pgrep"]),
            "OPENSSL_BIN": str(tools["openssl"]),
            "STAT_BIN": str(tools["stat"]),
            "SLEEP_BIN": str(tools["sleep"]),
            "FAKE_EXECUTABLE": str(binary),
            "FAKE_EXECUTABLE_PATTERN": regex_escape(str(binary)),
            "LSOF_STATE": str(root / "lsof-state"),
            "LSOF_PEER": peer or "",
        }

    def run_doctor(
        self, environment: dict[str, str], **extra: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DOCTOR)],
            env=environment | extra,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_doctor_reports_connected_and_healthy_idle_runtime(self) -> None:
        self.assertTrue(
            ACCESSIBILITY_CHECK.exists(),
            "client/diarchy-kvm-accessibility-check must exist",
        )
        root, environment = self.make_environment()
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("active-route=tailscale", result.stdout)
        self.assertIn("Diarchy KVM client is healthy", result.stdout)
        self.assertEqual((root / "lsof-state").read_text().strip(), "3")

        idle = self.run_doctor(environment, LSOF_IDLE="1")
        self.assertEqual(idle.returncode, 0, idle.stderr)
        self.assertIn("active-route=idle", idle.stdout)
        self.assertIn("Diarchy KVM client is healthy", idle.stdout)

    def test_doctor_expects_the_configured_side_for_all_four_sides(self) -> None:
        for side in ("left", "right", "top", "bottom"):
            with self.subTest(side=side):
                _, environment = self.make_environment(side=side)
                result = self.run_doctor(environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"trusted {side}-edge client configuration", result.stdout)

    def test_doctor_rejects_a_config_toml_on_the_wrong_side(self) -> None:
        ips = f'"{self.LAN}", "{self.TAILSCALE}"'
        _, environment = self.make_environment(
            side="right", toml=render_lan_mouse_toml("left", ips)
        )
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] host is not positioned right", result.stderr)

    def test_doctor_compares_the_fingerprint_case_insensitively(self) -> None:
        ips = f'"{self.LAN}", "{self.TAILSCALE}"'
        _, environment = self.make_environment(
            toml=render_lan_mouse_toml("left", ips, HOST_FINGERPRINT.upper())
        )
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_doctor_requires_the_configured_fingerprint(self) -> None:
        ips = f'"{self.LAN}", "{self.TAILSCALE}"'
        _, environment = self.make_environment(
            toml=render_lan_mouse_toml("left", ips, ":".join(["cc"] * 32))
        )
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] trusted host fingerprint is missing", result.stderr)

    def test_doctor_lan_only_config_pins_and_routes_the_lan_host(self) -> None:
        _, environment = self.make_environment(tailscale="", peer=self.LAN)
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("active-route=lan", result.stdout)

        ips = f'"{self.LAN}", "{self.TAILSCALE}"'
        _, stale = self.make_environment(
            tailscale="", peer=self.LAN, toml=render_lan_mouse_toml("left", ips)
        )
        mismatch = self.run_doctor(stale)
        self.assertEqual(mismatch.returncode, 1)
        self.assertIn("[fail] host addresses are not pinned", mismatch.stderr)

    def test_doctor_tailscale_only_config_pins_and_routes_the_tailscale_host(self) -> None:
        _, environment = self.make_environment(lan="", peer=self.TAILSCALE)
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("active-route=tailscale", result.stdout)

        ips = f'"{self.LAN}", "{self.TAILSCALE}"'
        _, stale = self.make_environment(
            lan="", peer=self.TAILSCALE, toml=render_lan_mouse_toml("left", ips)
        )
        mismatch = self.run_doctor(stale)
        self.assertEqual(mismatch.returncode, 1)
        self.assertIn("[fail] host addresses are not pinned", mismatch.stderr)

    def test_doctor_prefers_lan_route_when_both_peers_are_connected(self) -> None:
        _, environment = self.make_environment(peer=self.LAN)
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("active-route=lan", result.stdout)

    def test_doctor_fails_with_loader_reason_when_config_is_missing(self) -> None:
        _, environment = self.make_environment(configured=False)
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "[fail] Diarchy is not configured (open the app to set it up)",
            result.stderr,
        )
        self.assertNotIn("diarchy-config:", result.stderr)

    def test_doctor_fails_with_loader_reason_when_config_is_invalid(self) -> None:
        _, environment = self.make_environment()
        Path(environment["DIARCHY_CONFIG"]).write_text(
            "# Managed by Diarchy\nDIARCHY_SSH_USER=tester\n"
            "DIARCHY_LAN_HOST=192.0.2.10\nDIARCHY_KVM_SIDE=middle\n"
        )
        result = self.run_doctor(environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] DIARCHY_KVM_SIDE is invalid", result.stderr)

    def test_doctor_accepts_config_rendered_by_the_real_configure_script(self) -> None:
        # Guards format drift: nothing in this test hand-renders config.toml.
        host_sets = (
            ("both", self.LAN, self.TAILSCALE, self.TAILSCALE, "active-route=tailscale"),
            ("lan-only", self.LAN, "", self.LAN, "active-route=lan"),
            ("tailscale-only", "", self.TAILSCALE, self.TAILSCALE, "active-route=tailscale"),
        )
        for name, lan, tailscale, peer, route in host_sets:
            with self.subTest(hosts=name):
                _, environment = self.make_environment(
                    side="right", lan=lan, tailscale=tailscale, peer=peer
                )
                config = Path(environment["LAN_MOUSE_CONFIG"])
                config.unlink()
                configure = subprocess.run(
                    [str(CLIENT / "diarchy-kvm-configure")],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(configure.returncode, 0, configure.stderr)
                self.assertIn(f'"{HOST_FINGERPRINT}" = "host"', config.read_text())
                result = self.run_doctor(environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("trusted right-edge client configuration", result.stdout)
                self.assertIn(route, result.stdout)

    def test_doctor_no_peer_hint_names_the_configured_side_edge(self) -> None:
        for side in ("left", "right", "top", "bottom"):
            with self.subTest(side=side):
                root, environment = self.make_environment(side=side)
                silent_lsof = root / "silent-lsof"
                silent_lsof.write_text("#!/usr/bin/env bash\nexit 1\n")
                silent_lsof.chmod(0o755)
                result = self.run_doctor(environment, LSOF_BIN=str(silent_lsof))
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"cross the {side} edge once, then retry", result.stderr)
                if side != "left":
                    self.assertNotIn("left edge", result.stderr)

    IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d)(/\d{1,2})?")
    DOCUMENTATION_VALUES = {"100.64.0.10", "100.64.0.20"}

    @classmethod
    def private_ip_literals(cls, text: str) -> list[str]:
        """Find RFC 1918 and 100.64.0.0/10 literals. CIDR constants and documentation values pass."""
        found = []
        for match in cls.IPV4.finditer(text):
            if match.group(5):
                continue
            octets = [int(match.group(index)) for index in range(1, 5)]
            if any(octet > 255 for octet in octets):
                continue
            literal = ".".join(str(octet) for octet in octets)
            first, second = octets[0], octets[1]
            private = (
                first == 10
                or (first == 172 and 16 <= second <= 31)
                or (first == 192 and second == 168)
                or (first == 100 and 64 <= second <= 127)
            )
            if private and literal not in cls.DOCUMENTATION_VALUES:
                found.append(literal)
        return found

    def test_client_product_files_have_no_private_ip_literals(self) -> None:
        for path in sorted(CLIENT.iterdir()):
            if path.is_file():
                with self.subTest(path=path.name):
                    self.assertEqual(self.private_ip_literals(path.read_text(errors="replace")), [])

    def test_private_ip_guard_fails_on_added_literal_and_allows_cidr_and_documentation_values(self) -> None:
        def address(*octets: int) -> str:
            return ".".join(str(octet) for octet in octets)

        added = address(10, 9, 8, 7)
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp) / DOCTOR.name
            copy.write_text(DOCTOR.read_text() + f'\nHOST_ADDRESS="{added}"\n')
            self.assertEqual(self.private_ip_literals(copy.read_text()), [added])
        allowed = " ".join(
            (
                "192.0.2.10",
                "100.64.0.10",
                address(100, 64, 0, 0) + "/10",
                address(10, 0, 0, 0) + "/8",
                address(192, 168, 0, 0) + "/16",
            )
        )
        self.assertEqual(self.private_ip_literals(allowed), [])
        for octets in ((172, 16, 5, 5), (172, 31, 0, 1), (192, 168, 1, 1), (100, 127, 1, 1)):
            literal = address(*octets)
            self.assertEqual(self.private_ip_literals(f"x={literal}"), [literal])


if __name__ == "__main__":
    unittest.main()
