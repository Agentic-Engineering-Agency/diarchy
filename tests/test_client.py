from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client"
LAUNCHER = CLIENT / "diarchy-stream"
LAN_HOST = "198.51.100.10"
TAILSCALE_HOST = "100.64.0.10"
INSTALLER = CLIENT / "install.sh"
DOCTOR = CLIENT / "diarchy-client-doctor"
MOONLIGHT_BUNDLE_ID = "com.moonlight-stream.Moonlight"
LAUNCHER_BUNDLE_ID = "dev.sebastiangmz.diarchy"


class ClientLauncherTests(unittest.TestCase):
    def run_client(
        self,
        *,
        lan: tuple[int, tuple[float, ...]] | None,
        remote: tuple[int, tuple[float, ...]] | None,
        args: tuple[str, ...] = (),
        route_failure: str | None = None,
        service_failure: str | None = None,
        lan_interface: str = "en0",
        lan_host: str = LAN_HOST,
        remote_host: str = TAILSCALE_HOST,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]], list[list[str]], list[list[str]]]:
        self.assertTrue(LAUNCHER.exists(), "client/diarchy-stream must exist")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ping = temp / "ping"
            route = temp / "route"
            connect = temp / "nc"
            moonlight = temp / "moonlight"
            ping_log = temp / "ping.jsonl"
            route_log = temp / "route.jsonl"
            moonlight_log = temp / "moonlight.jsonl"

            ping.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "host = sys.argv[-1]\n"
                "with open(os.environ['FAKE_PING_LOG'], 'a') as f:\n"
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "data = json.loads(os.environ['FAKE_PROBES']).get(host)\n"
                "if data is None:\n"
                "    print(f'4 packets transmitted, 0 packets received, 100.0% packet loss')\n"
                "    raise SystemExit(2)\n"
                "loss, samples = data\n"
                "received = len(samples)\n"
                "for index, value in enumerate(samples, 1):\n"
                "    print(f'64 bytes from {host}: icmp_seq={index} ttl=64 time={value} ms')\n"
                "print(f'4 packets transmitted, {received} packets received, {loss}% packet loss')\n"
            )
            route.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "host = sys.argv[-1]\n"
                "with open(os.environ['FAKE_ROUTE_LOG'], 'a') as f:\n"
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if host == os.environ.get('FAKE_ROUTE_FAILURE'):\n"
                "    raise SystemExit(1)\n"
                "interface = os.environ['FAKE_LAN_INTERFACE'] if host == os.environ['DIARCHY_LAN_HOST'] else 'utun4'\n"
                "print(f'   route to: {host}')\n"
                "print(f'  interface: {interface}')\n"
            )
            connect.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "host = sys.argv[-2]\n"
                "raise SystemExit(1 if host == os.environ.get('FAKE_SERVICE_FAILURE') else 0)\n"
            )
            moonlight.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['FAKE_MOONLIGHT_LOG'], 'a') as f:\n"
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            )
            for executable in (ping, route, connect, moonlight):
                executable.chmod(0o755)

            probes = {
                host: [loss, list(samples)]
                for host, probe in ((lan_host, lan), (remote_host, remote))
                if host and probe is not None
                for loss, samples in (probe,)
            }
            result = subprocess.run(
                [str(LAUNCHER), *args],
                env=os.environ
                | {
                    "DIARCHY_CONFIG": str(temp / "missing.conf"),
                    "DIARCHY_SSH_USER": "tester",
                    "DIARCHY_LAN_HOST": lan_host,
                    "DIARCHY_TAILSCALE_HOST": remote_host,
                    "MOONLIGHT_BIN": str(moonlight),
                    "PING_BIN": str(ping),
                    "ROUTE_BIN": str(route),
                    "CONNECT_BIN": str(connect),
                    "FAKE_PROBES": json.dumps(probes),
                    "FAKE_ROUTE_FAILURE": route_failure or "",
                    "FAKE_SERVICE_FAILURE": service_failure or "",
                    "FAKE_LAN_INTERFACE": lan_interface,
                    "FAKE_PING_LOG": str(ping_log),
                    "FAKE_ROUTE_LOG": str(route_log),
                    "FAKE_MOONLIGHT_LOG": str(moonlight_log),
                }
                | (extra_env or {}),
                text=True,
                capture_output=True,
                check=False,
            )

            def read_log(path: Path) -> list[list[str]]:
                return (
                    [json.loads(line) for line in path.read_text().splitlines()]
                    if path.exists()
                    else []
                )

            return result, read_log(ping_log), read_log(route_log), read_log(moonlight_log)

    def test_healthy_local_path_uses_config_resolution_and_fps(self) -> None:
        result, ping_calls, route_calls, moonlight_calls = self.run_client(
            lan=(0, (3.0, 4.0, 5.0, 6.0)), remote=(0, (18.0, 20.0, 22.0, 24.0))
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("route=lan", result.stdout)
        self.assertIn("profile=lan", result.stdout)
        self.assertEqual(len(ping_calls), 1)
        self.assertEqual(route_calls, [["-n", "get", LAN_HOST]])
        self.assertEqual(len(moonlight_calls), 1)
        command = moonlight_calls[0]
        self.assertEqual(command[:3], ["stream", LAN_HOST, "Desktop"])
        self.assertEqual(command[command.index("--resolution") + 1], "1920x1080")
        self.assertEqual(command[command.index("--fps") + 1], "60")
        self.assertEqual(command[command.index("--bitrate") + 1], "25000")
        self.assertEqual(command[command.index("--video-codec") + 1], "HEVC")
        self.assertEqual(command[command.index("--video-decoder") + 1], "hardware")
        self.assertNotIn("--performance-overlay", command)
        self.assertIn("--no-audio-on-host", command)
        self.assertIn("--absolute-mouse", command)

    def test_overlay_flag_only_when_enabled(self) -> None:
        on = self.run_client(lan=(0, (3.0, 3.0, 3.0, 3.0)), remote=None,
                             extra_env={"DIARCHY_STREAM_OVERLAY": "1"})
        off = self.run_client(lan=(0, (3.0, 3.0, 3.0, 3.0)), remote=None,
                              extra_env={"DIARCHY_STREAM_OVERLAY": "0"})
        self.assertIn("--performance-overlay", on[3][0])
        self.assertNotIn("--performance-overlay", off[3][0])

    def test_config_resolution_and_fps_apply_to_lan_and_remote_but_degraded_clamps(self) -> None:
        extra = {"DIARCHY_STREAM_RESOLUTION": "2560x1440", "DIARCHY_STREAM_FPS": "144"}
        lan = self.run_client(lan=(0, (3.0,) * 4), remote=None, extra_env=extra)[3][0]
        remote = self.run_client(lan=None, remote=(0, (30.0,) * 4), extra_env=extra)[3][0]
        degraded = self.run_client(lan=(10, (40.0,) * 4), remote=None, extra_env=extra)[3][0]
        for argv, res, fps in ((lan, "2560x1440", "144"), (remote, "2560x1440", "144"),
                               (degraded, "1280x720", "60")):
            self.assertEqual(argv[argv.index("--resolution") + 1], res)
            self.assertEqual(argv[argv.index("--fps") + 1], fps)
        self.assertEqual(degraded[degraded.index("--video-codec") + 1], "H.264")
        self.assertEqual(degraded[degraded.index("--bitrate") + 1], "8000")

    def test_degraded_keeps_lower_configured_fps(self) -> None:
        argv = self.run_client(lan=(10, (40.0,) * 4), remote=None,
                               extra_env={"DIARCHY_STREAM_FPS": "30"})[3][0]
        self.assertEqual(argv[argv.index("--fps") + 1], "30")

    def test_empty_lan_host_is_never_probed(self) -> None:
        result, pings, routes, launches = self.run_client(
            lan=None, remote=(0, (30.0, 30.0, 30.0, 30.0)), lan_host=""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[-1] for call in pings], [TAILSCALE_HOST])
        self.assertEqual(len(launches), 1)

    def test_diagnose_prints_unset_for_missing_host(self) -> None:
        result, *_ = self.run_client(lan=None, remote=(0, (30.0, 30.0, 30.0, 30.0)),
                                     lan_host="", args=("--diagnose",))
        self.assertIn("candidate=lan host=unset reachable=false", result.stdout)

    def test_unconfigured_launcher_fails_with_loader_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            result = subprocess.run(
                [str(LAUNCHER)],
                env={"PATH": os.environ["PATH"], "HOME": str(temp),
                     "DIARCHY_CONFIG": str(temp / "missing.conf")},
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("diarchy-stream: configuration could not be loaded", result.stderr)

    def test_lossy_local_path_uses_degraded_profile(self) -> None:
        result, _, _, moonlight_calls = self.run_client(
            lan=(25, (45.0, 50.0, 55.0)), remote=(0, (18.0, 20.0, 22.0, 24.0))
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("route=lan", result.stdout)
        self.assertIn("profile=degraded", result.stdout)
        command = moonlight_calls[0]
        self.assertIn("1280x720", command)
        self.assertIn("60", command)
        self.assertIn("8000", command)
        self.assertIn("H.264", command)

    def test_high_latency_local_path_uses_degraded_profile(self) -> None:
        result, _, _, _ = self.run_client(
            lan=(0, (20.0, 30.0, 40.0, 50.0)), remote=None, args=("--dry-run",)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("profile=degraded", result.stdout)
        self.assertIn("median_ms=35.000", result.stdout)

    def test_unreachable_lan_falls_back_to_tailscale(self) -> None:
        result, ping_calls, route_calls, moonlight_calls = self.run_client(
            lan=None, remote=(0, (18.0, 20.0, 22.0, 24.0))
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("route=remote", result.stdout)
        self.assertIn("profile=remote", result.stdout)
        self.assertEqual([call[-1] for call in ping_calls], [LAN_HOST, TAILSCALE_HOST])
        self.assertEqual(route_calls, [["-n", "get", TAILSCALE_HOST]])
        self.assertEqual(moonlight_calls[0][:3], ["stream", TAILSCALE_HOST, "Desktop"])

    def test_blocked_lan_service_falls_back_to_tailscale(self) -> None:
        result, ping_calls, route_calls, moonlight_calls = self.run_client(
            lan=(0, (3.0, 4.0, 5.0, 6.0)),
            remote=(0, (18.0, 20.0, 22.0, 24.0)),
            service_failure=LAN_HOST,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("route=remote", result.stdout)
        self.assertEqual([call[-1] for call in ping_calls], [LAN_HOST, TAILSCALE_HOST])
        self.assertEqual(
            route_calls,
            [["-n", "get", LAN_HOST], ["-n", "get", TAILSCALE_HOST]],
        )
        self.assertEqual(moonlight_calls[0][1], TAILSCALE_HOST)

    def test_lan_ip_routed_through_tunnel_falls_back_to_explicit_tailnet_host(self) -> None:
        result, _, _, moonlight_calls = self.run_client(
            lan=(0, (3.0, 4.0, 5.0, 6.0)),
            remote=(0, (18.0, 20.0, 22.0, 24.0)),
            lan_interface="utun4",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("route=remote", result.stdout)
        self.assertEqual(moonlight_calls[0][1], TAILSCALE_HOST)

    def test_route_failure_makes_candidate_unavailable(self) -> None:
        result, _, _, moonlight_calls = self.run_client(
            lan=(0, (3.0, 4.0, 5.0, 6.0)),
            remote=None,
            route_failure=LAN_HOST,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unreachable on LAN and Tailscale", result.stderr)
        self.assertEqual(moonlight_calls, [])

    def test_force_remote_skips_lan_probe(self) -> None:
        result, ping_calls, _, moonlight_calls = self.run_client(
            lan=(0, (3.0, 4.0, 5.0, 6.0)),
            remote=(0, (18.0, 20.0, 22.0, 24.0)),
            args=("--force-remote",),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[-1] for call in ping_calls], [TAILSCALE_HOST])
        self.assertIn("route=remote", result.stdout)
        self.assertEqual(moonlight_calls[0][1], TAILSCALE_HOST)

    def test_dry_run_never_launches_moonlight(self) -> None:
        result, ping_calls, _, moonlight_calls = self.run_client(
            lan=(0, (3.0, 4.0, 5.0, 6.0)), remote=None, args=("--dry-run",)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("profile=lan", result.stdout)
        self.assertEqual(moonlight_calls, [])
        self.assertTrue(all("-c" in call and "-W" in call for call in ping_calls))

    def test_diagnose_measures_both_routes_without_launching(self) -> None:
        result, ping_calls, _, moonlight_calls = self.run_client(
            lan=(0, (3.0, 4.0, 5.0, 6.0)),
            remote=(0, (18.0, 20.0, 22.0, 24.0)),
            args=("--diagnose",),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[-1] for call in ping_calls], [LAN_HOST, TAILSCALE_HOST])
        self.assertIn(f"candidate=lan host={LAN_HOST} reachable=true", result.stdout)
        self.assertIn(f"candidate=remote host={TAILSCALE_HOST} reachable=true", result.stdout)
        self.assertEqual(moonlight_calls, [])

    def test_both_routes_unreachable_fails_without_launch(self) -> None:
        result, _, _, moonlight_calls = self.run_client(lan=None, remote=None)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unreachable on LAN and Tailscale", result.stderr)
        self.assertEqual(moonlight_calls, [])

    def test_conflicting_force_flags_are_rejected_before_probing(self) -> None:
        result, ping_calls, _, moonlight_calls = self.run_client(
            lan=(0, (3.0, 4.0, 5.0, 6.0)),
            remote=(0, (18.0, 20.0, 22.0, 24.0)),
            args=("--force-lan", "--force-remote"),
        )

        self.assertEqual(result.returncode, 64)
        self.assertIn("mutually exclusive", result.stderr)
        self.assertEqual(ping_calls, [])
        self.assertEqual(moonlight_calls, [])


class ClientInstallerTests(unittest.TestCase):
    def make_install_environment(
        self, *, existing_bundle_id: str | None = None
    ) -> tuple[Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        home = root / "home"
        tools = root / "tools"
        home.mkdir()
        tools.mkdir()

        fake_curl = tools / "curl"
        fake_sha256 = tools / "shasum"
        fake_hdiutil = tools / "hdiutil"
        fake_codesign = tools / "codesign"
        fake_spctl = tools / "spctl"
        fake_curl.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "output = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
            "output.write_bytes(b'fake pinned dmg')\n"
        )
        fake_sha256.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s  %s\\n' "
            "'d494740eead8ad4e620cdc8feedb56083bc29cabbbeef34cb82585fd87725fa2' "
            "\"${@: -1}\"\n"
        )
        fake_hdiutil.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, plistlib, sys\n"
            "if sys.argv[1] == 'detach':\n"
            "    raise SystemExit(0)\n"
            "mount = pathlib.Path(sys.argv[sys.argv.index('-mountpoint') + 1])\n"
            "app = mount / 'Moonlight.app'\n"
            "binary = app / 'Contents' / 'MacOS' / 'Moonlight'\n"
            "binary.parent.mkdir(parents=True)\n"
            "binary.write_text('#!/usr/bin/env bash\\nexit 0\\n')\n"
            "binary.chmod(0o755)\n"
            "with (app / 'Contents' / 'Info.plist').open('wb') as handle:\n"
            "    plistlib.dump({\n"
            "        'CFBundleIdentifier': 'com.moonlight-stream.Moonlight',\n"
            "        'CFBundleExecutable': 'Moonlight',\n"
            "        'CFBundleName': 'Moonlight',\n"
            "    }, handle)\n"
        )
        fake_codesign.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake_spctl.write_text("#!/usr/bin/env bash\nexit 0\n")
        for executable in (
            fake_curl,
            fake_sha256,
            fake_hdiutil,
            fake_codesign,
            fake_spctl,
        ):
            executable.chmod(0o755)

        if existing_bundle_id:
            existing = home / "Applications" / "Moonlight.app" / "Contents"
            existing.mkdir(parents=True)
            import plistlib

            with (existing / "Info.plist").open("wb") as handle:
                plistlib.dump({"CFBundleIdentifier": existing_bundle_id}, handle)

        return home, os.environ | {
            "HOME": str(home),
            "CURL_BIN": str(fake_curl),
            "SHA256_BIN": str(fake_sha256),
            "HDIUTIL_BIN": str(fake_hdiutil),
            "CODESIGN_BIN": str(fake_codesign),
            "SPCTL_BIN": str(fake_spctl),
        }

    def test_installer_dry_run_is_rootless_and_pinned(self) -> None:
        self.assertTrue(INSTALLER.exists(), "client/install.sh must exist")
        home, environment = self.make_install_environment()

        result = subprocess.run(
            [str(INSTALLER), "--dry-run"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Moonlight 6.1.0", result.stdout)
        self.assertIn(
            "d494740eead8ad4e620cdc8feedb56083bc29cabbbeef34cb82585fd87725fa2",
            result.stdout,
        )
        self.assertIn(str(home / "Applications" / "Moonlight.app"), result.stdout)
        self.assertNotIn("Launcher destination", result.stdout)
        self.assertNotIn("osacompile", result.stdout)
        self.assertNotIn("sudo", result.stdout.lower())
        self.assertFalse((home / "Applications").exists())
        self.assertFalse((home / ".local").exists())

    def test_installer_rejects_foreign_existing_moonlight_bundle(self) -> None:
        self.assertTrue(INSTALLER.exists(), "client/install.sh must exist")
        home, environment = self.make_install_environment(
            existing_bundle_id="example.foreign.app"
        )

        result = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to replace", result.stderr)
        info = home / "Applications" / "Moonlight.app" / "Contents" / "Info.plist"
        self.assertTrue(info.exists())

    def test_installer_is_idempotent_and_installs_owned_bundles(self) -> None:
        self.assertTrue(INSTALLER.exists(), "client/install.sh must exist")
        home, environment = self.make_install_environment()

        first = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        second = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        moonlight = home / "Applications" / "Moonlight.app"
        launcher = home / "Applications" / "Diarchy.app"
        self.assertTrue((moonlight / "Contents" / "MacOS" / "Moonlight").exists())
        self.assertFalse(launcher.exists())
        self.assertTrue((home / ".local" / "bin" / "diarchy-stream").exists())
        self.assertTrue(
            (home / ".local" / "bin" / "diarchy-client-doctor").exists()
        )
        self.assertTrue((home / ".local" / "bin" / "diarchy-config.sh").exists())
        self.assertEqual(
            (home / ".local" / "bin" / "diarchy-config.sh").read_bytes(),
            (CLIENT / "diarchy-config.sh").read_bytes(),
        )
        self.assertEqual(
            (home / ".local" / "bin" / "diarchy-config.sh").stat().st_mode & 0o777,
            0o755,
        )

    def test_installer_never_touches_an_existing_launcher_bundle(self) -> None:
        home, environment = self.make_install_environment()
        marker = home / "Applications" / "Diarchy.app" / "Contents" / "marker"
        marker.parent.mkdir(parents=True)
        marker.write_text("keep")
        result = subprocess.run([str(INSTALLER)], env=environment, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), "keep")

    def make_doctor_environment(
        self, bundle_id: str
    ) -> tuple[Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        home = root / "home"
        tools = root / "tools"
        home.mkdir()
        tools.mkdir()
        app = home / "Applications" / "Diarchy.app" / "Contents"
        app.mkdir(parents=True)
        (app / "Info.plist").write_text(bundle_id)
        moonlight = home / "Applications" / "Moonlight.app" / "Contents"
        (moonlight / "MacOS").mkdir(parents=True)
        (moonlight / "MacOS" / "Moonlight").write_text("#!/usr/bin/env bash\nexit 0\n")
        (moonlight / "MacOS" / "Moonlight").chmod(0o755)
        (moonlight / "Info.plist").write_text(MOONLIGHT_BUNDLE_ID)
        launcher = home / ".local" / "bin" / "diarchy-stream"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/usr/bin/env bash\nexit 0\n")
        launcher.chmod(0o755)
        for name in ("codesign", "spctl"):
            tool = tools / name
            tool.write_text("#!/usr/bin/env bash\nexit 0\n")
            tool.chmod(0o755)
        plistbuddy = tools / "PlistBuddy"
        plistbuddy.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "print(pathlib.Path(sys.argv[-1]).read_text())\n"
        )
        plistbuddy.chmod(0o755)
        return home, os.environ | {
            "HOME": str(home),
            "CODESIGN_BIN": str(tools / "codesign"),
            "SPCTL_BIN": str(tools / "spctl"),
            "PLIST_BUDDY_BIN": str(plistbuddy),
            "DIARCHY_CONFIG_SH": str(CLIENT / "diarchy-config.sh"),
            "DIARCHY_CONFIG": str(root / "missing.conf"),
            "DIARCHY_SSH_USER": "tester",
            "DIARCHY_LAN_HOST": LAN_HOST,
            "DIARCHY_TAILSCALE_HOST": TAILSCALE_HOST,
        }

    def test_doctor_accepts_owned_launcher_bundle(self) -> None:
        home, environment = self.make_doctor_environment(LAUNCHER_BUNDLE_ID)
        result = subprocess.run([str(DOCTOR)], env=environment, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[ok] Diarchy launcher", result.stdout)

    def test_doctor_rejects_foreign_launcher_bundle(self) -> None:
        home, environment = self.make_doctor_environment("example.foreign.app")
        result = subprocess.run([str(DOCTOR)], env=environment, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] Diarchy launcher", result.stderr)
    def test_doctor_reports_configuration_failure(self) -> None:
        home, environment = self.make_doctor_environment(LAUNCHER_BUNDLE_ID)
        environment["DIARCHY_CONFIG_SH"] = str(home / "missing-loader.sh")
        result = subprocess.run([str(DOCTOR)], env=environment, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] Diarchy configuration loads", result.stderr)

if __name__ == "__main__":
    unittest.main()
