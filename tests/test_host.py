from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "host"


class HostPolicyTests(unittest.TestCase):
    def require_file(self, relative: str) -> Path:
        path = HOST / relative
        self.assertTrue(path.exists(), f"host/{relative} must exist")
        return path

    HEADER = (
        "Info: Screencasting with Wayland's protocol\n"
        "Info: [wayland] Name: eDP-1\n"
        "Info: [wlgrab] Selected monitor [Laptop panel (eDP-1)] for streaming\n"
    )

    @staticmethod
    def encoders(h264: str, hevc: str | None = None) -> str:
        hevc = hevc or h264
        return (
            f"Info: Found H.264 encoder: h264_{h264} [{h264}]\n"
            f"Info: Found HEVC encoder: hevc_{hevc} [{hevc}]\n"
        )

    def run_capture_check(
        self, output: str, logs: str
    ) -> subprocess.CompletedProcess[str]:
        checker = self.require_file("diarchy-check-sunshine-log")
        return subprocess.run(
            [str(checker), output],
            input=logs,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_service_check(
        self,
        logs: str,
        *,
        output: str = "eDP-1",
        invocation_id: str = "invocation-123",
        args: tuple[str, ...] = (),
    ) -> tuple[subprocess.CompletedProcess[str], str, str]:
        checker = self.require_file("diarchy-check-sunshine-service")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_display = temp / "display"
            fake_systemctl = temp / "systemctl"
            fake_journalctl = temp / "journalctl"
            systemctl_log = temp / "systemctl.log"
            journalctl_log = temp / "journalctl.log"
            fake_display.write_text(
                "#!/usr/bin/env bash\n"
                '[[ "$*" == "status" ]] || exit 70\n'
                'printf "%s\\n" "$FAKE_OUTPUT"\n'
            )
            fake_systemctl.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >>"$FAKE_SYSTEMCTL_LOG"\n'
                'printf "%s\\n" "$FAKE_INVOCATION"\n'
            )
            fake_journalctl.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >>"$FAKE_JOURNALCTL_LOG"\n'
                '[[ " $* " == *" _SYSTEMD_INVOCATION_ID=$FAKE_INVOCATION "* ]] '
                "|| exit 71\n"
                'printf "%s\\n" "$FAKE_SUNSHINE_LOGS"\n'
            )
            for executable in (fake_display, fake_systemctl, fake_journalctl):
                executable.chmod(0o755)
            result = subprocess.run(
                [str(checker), *args],
                env=os.environ
                | {
                    "DISPLAY_BIN": str(fake_display),
                    "SYSTEMCTL_BIN": str(fake_systemctl),
                    "JOURNALCTL_BIN": str(fake_journalctl),
                    "LOG_CHECKER_BIN": str(
                        self.require_file("diarchy-check-sunshine-log")
                    ),
                    "FAKE_OUTPUT": output,
                    "FAKE_INVOCATION": invocation_id,
                    "FAKE_SUNSHINE_LOGS": logs,
                    "FAKE_SYSTEMCTL_LOG": str(systemctl_log),
                    "FAKE_JOURNALCTL_LOG": str(journalctl_log),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            return (
                result,
                systemctl_log.read_text() if systemctl_log.exists() else "",
                journalctl_log.read_text() if journalctl_log.exists() else "",
            )

    def test_sunshine_policy_pins_no_device_and_has_no_audio(self) -> None:
        config = self.require_file("sunshine.conf").read_text()

        required = {
            "capture": "wlr",
            "stream_audio": "disabled",
            "keyboard": "enabled",
            "mouse": "enabled",
            "controller": "disabled",
            "upnp": "disabled",
            "origin_web_ui_allowed": "pc",
            "wan_encryption_mode": "2",
            "max_bitrate": "30000",
        }
        parsed = {}
        for line in config.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                parsed[key.strip()] = value.strip()

        for key, value in required.items():
            self.assertEqual(parsed.get(key), value, key)
        # The GPU differs per host: the agent pins both at `stream start`.
        self.assertNotIn("encoder", parsed)
        self.assertNotIn("adapter_name", parsed)

    def test_apps_exposes_only_existing_desktop(self) -> None:
        apps = json.loads(self.require_file("apps.json").read_text())

        self.assertEqual(apps["apps"], [{"name": "Desktop", "image-path": "desktop.png"}])

    def test_services_run_rootless_inside_graphical_user_session(self) -> None:
        sunshine = self.require_file("systemd/diarchy-sunshine.service").read_text()
        display = self.require_file("systemd/diarchy-display.service").read_text()

        self.assertIn("ExecStart=%h/.local/bin/sunshine", sunshine)
        self.assertNotIn("ConditionEnvironment=WAYLAND_DISPLAY", sunshine + display)
        self.assertIn("ExecStart=%h/.local/bin/diarchy-display watch", display)
        self.assertIn("WantedBy=graphical-session.target", sunshine)
        self.assertIn("WantedBy=graphical-session.target", display)
        self.assertNotIn("sudo", sunshine + display)



    def test_output_transition_restarts_running_sunshine(self) -> None:
        display = self.require_file("diarchy-display").read_text()

        self.assertIn("if [[ $selected != \"$previous\" ]]", display)
        self.assertIn('"$SYSTEMCTL_BIN" --user try-restart', display)
        self.assertIn("diarchy-sunshine.service", display)

    def test_installer_discovers_hyprland_from_ssh_shell(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn('"$SCRIPT_DIR/diarchy-display" status', installer)
        self.assertNotIn('hyprctl -j monitors >/dev/null', installer)

    def test_health_checks_share_current_invocation_probe(self) -> None:
        doctor = self.require_file("diarchy-host-doctor").read_text()
        installer = self.require_file("install.sh").read_text()
        service_check = self.require_file(
            "diarchy-check-sunshine-service"
        ).read_text()

        for script in (doctor, installer):
            self.assertIn("diarchy-check-sunshine-service", script)
        self.assertIn("SERVICE_NAME=diarchy-sunshine.service", service_check)
        self.assertIn('"$SYSTEMCTL_BIN" --user show "$SERVICE_NAME"', service_check)
        self.assertIn("--property=InvocationID --value", service_check)
        self.assertIn(
            '"$JOURNALCTL_BIN" --user _SYSTEMD_INVOCATION_ID="$invocation_id"',
            service_check,
        )
        for helper in (
            "diarchy-check-sunshine-log",
            "diarchy-check-sunshine-service",
        ):
            self.assertIn(f'"$SCRIPT_DIR/{helper}"', installer)
            self.assertIn(f'"$BIN_DIR/{helper}"', installer)
        self.assertIn("for _ in {1..30}", installer)

    def test_capture_log_checker_accepts_each_hardware_family_and_reports_it(self) -> None:
        for family in ("nvenc", "vaapi"):
            with self.subTest(family=family):
                result = self.run_capture_check("eDP-1", self.HEADER + self.encoders(family))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), family)

    def test_capture_log_checker_accepts_encoder_line_trailing_whitespace(self) -> None:
        for family in ("nvenc", "vaapi"):
            with self.subTest(family=family):
                logs = (
                    self.HEADER
                    + f"Info: Found H.264 encoder: h264_{family} [{family}]  \r\n"
                    + f"Info: Found HEVC encoder: hevc_{family} [{family}]\r\n"
                )
                result = self.run_capture_check("eDP-1", logs)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), family)

    def test_capture_log_checker_rejects_software_and_inconsistent_encoders(self) -> None:
        cases = {
            "software fallback": self.HEADER
            + "Info: Found H.264 encoder: libx264 [software]\n",
            "mixed families": self.HEADER + self.encoders("nvenc", "vaapi"),
            "missing HEVC": self.HEADER
            + "Info: Found H.264 encoder: h264_vaapi [vaapi]\n",
            "missing H.264": self.HEADER
            + "Info: Found HEVC encoder: hevc_vaapi [vaapi]\n",
            "no encoder lines": self.HEADER,
        }
        for label, logs in cases.items():
            with self.subTest(label=label):
                result = self.run_capture_check("eDP-1", logs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[fail]", result.stderr)

    def test_service_checker_encoder_flag_prints_family_and_default_is_unchanged(self) -> None:
        logs = self.HEADER + self.encoders("vaapi")
        default, _, _ = self.run_service_check(logs)
        flagged, _, _ = self.run_service_check(logs, args=("--encoder",))
        bogus, _, _ = self.run_service_check(logs, args=("--nope",))
        self.assertEqual(default.stdout.strip(), "eDP-1")
        self.assertEqual(flagged.stdout.strip(), "vaapi")
        self.assertEqual(bogus.returncode, 64)

    def test_capture_log_checker_accepts_physical_and_headless_outputs(self) -> None:
        for output, selected_monitor in (
            ("eDP-1", "Laptop panel (eDP-1)"),
            ("DP-1", "Dell Inc. U2720Q (DP-1)"),
            ("HDMI-A-1", "Some TV (HDMI-A-1)"),
            ("DIARCHY", ""),
        ):
            with self.subTest(output=output):
                logs = (
                    "Info: Screencasting with Wayland's protocol\n"
                    f"Info: [wayland] Name: {output}\n"
                    f"Info: [wlgrab] Selected monitor [{selected_monitor}] for streaming\n"
                    "Info: Found H.264 encoder: h264_nvenc [nvenc]\n"
                    "Info: Found HEVC encoder: hevc_nvenc [nvenc]\n"
                )
                result = self.run_capture_check(output, logs)

                self.assertEqual(result.returncode, 0, result.stderr)

    def test_capture_log_checker_matches_the_selected_name_exactly(self) -> None:
        logs = (
            "Info: Screencasting with Wayland's protocol\n"
            "Info: [wayland] Name: DP-1\n"
            "Info: [wlgrab] Selected monitor [Dell (DP-1)] for streaming\n"
            "Info: Found H.264 encoder: h264_vaapi [vaapi]\n"
            "Info: Found HEVC encoder: hevc_vaapi [vaapi]\n"
        )
        for selected in ("DP-2", "DP-10", "DP.1", "DP-1;id", "bad name"):
            with self.subTest(selected=selected):
                result = self.run_capture_check(selected, logs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[fail]", result.stderr)

    def test_capture_log_checker_rejects_incomplete_or_failed_capture(self) -> None:
        valid = (
            "Info: Screencasting with Wayland's protocol\n"
            "Info: [wayland] Name: eDP-1\n"
            "Info: [wlgrab] Selected monitor [Laptop panel (eDP-1)] for streaming\n"
            "Info: Found H.264 encoder: h264_nvenc [nvenc]\n"
            "Info: Found HEVC encoder: hevc_nvenc [nvenc]\n"
        )
        invalid_logs = {
            "generic marker only": (
                "Info: wlr Wayland capture\n"
                "Info: Found H.264 encoder: h264_nvenc [nvenc]\n"
                "Info: Found HEVC encoder: hevc_nvenc [nvenc]\n"
            ),
            "wrong output": valid.replace("Name: eDP-1", "Name: DIARCHY"),
            "prefix collision": valid.replace("Name: eDP-1", "Name: eDP-10"),
            "unrelated selected monitor": valid.replace(
                "(eDP-1)", "(HDMI-A-1)"
            ),
            "capture failure": valid + "Error: Frame capture failed\n",
            "missing monitor selection": "\n".join(
                line
                for line in valid.splitlines()
                if "[wlgrab] Selected monitor" not in line
            ),
        }

        for label, logs in invalid_logs.items():
            with self.subTest(label=label):
                result = self.run_capture_check("eDP-1", logs)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[fail]", result.stderr)

    def test_capture_log_checker_rejects_a_client_session_on_another_monitor(self) -> None:
        """Field log with an index pin: the startup probe selected DIARCHY, the client session selected eDP-1."""
        startup = (
            "Info: Screencasting with Wayland's protocol\n"
            "Info: [wayland] Name: eDP-1\n"
            "Info: [wayland] Name: DIARCHY\n"
            "Info: [wlgrab] Selected monitor [] for streaming\n"
            "Info: Found H.264 encoder: h264_vaapi [vaapi]\n"
            "Info: Found HEVC encoder: hevc_vaapi [vaapi]\n"
        )
        session = (
            "Info: [wlgrab] Monitor 0 is eDP-1: Laptop panel (eDP-1)\n"
            "Info: [wlgrab] Monitor 1 is DIARCHY: \n"
            "Info: [wlgrab] Selected monitor [Laptop panel (eDP-1)] for streaming\n"
        )
        self.assertEqual(self.run_capture_check("DIARCHY", startup).returncode, 0)
        result = self.run_capture_check("DIARCHY", startup + session)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("client session", result.stderr)

    def test_capture_log_checker_accepts_client_sessions_on_the_selected_monitor(self) -> None:
        logs = (
            "Info: Screencasting with Wayland's protocol\n"
            "Info: [wayland] Name: eDP-1\n"
            "Info: [wlgrab] Selected monitor [Laptop panel (eDP-1)] for streaming\n"
            "Info: Found H.264 encoder: h264_vaapi [vaapi]\n"
            "Info: Found HEVC encoder: hevc_vaapi [vaapi]\n"
            "Info: [wlgrab] Monitor 0 is eDP-1: Laptop panel (eDP-1)\n"
            "Info: [wlgrab] Selected monitor [Laptop panel (eDP-1)] for streaming\n"
        )
        result = self.run_capture_check("eDP-1", logs)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_service_checker_queries_only_current_invocation(self) -> None:
        logs = (
            "Info: Screencasting with Wayland's protocol\n"
            "Info: [wayland] Name: eDP-1\n"
            "Info: [wlgrab] Selected monitor [Laptop panel (eDP-1)] for streaming\n"
            "Info: Found H.264 encoder: h264_nvenc [nvenc]\n"
            "Info: Found HEVC encoder: hevc_nvenc [nvenc]\n"
        )
        result, systemctl_calls, journalctl_calls = self.run_service_check(logs)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "eDP-1")
        self.assertEqual(
            systemctl_calls.strip(),
            "--user show diarchy-sunshine.service "
            "--property=InvocationID --value",
        )
        self.assertEqual(
            journalctl_calls.strip(),
            "--user _SYSTEMD_INVOCATION_ID=invocation-123 --no-pager",
        )

    def test_service_checker_rejects_each_runtime_contract_failure(self) -> None:
        valid = (
            "Info: Screencasting with Wayland's protocol\n"
            "Info: [wayland] Name: eDP-1\n"
            "Info: [wlgrab] Selected monitor [Laptop panel (eDP-1)] for streaming\n"
            "Info: Found H.264 encoder: h264_nvenc [nvenc]\n"
            "Info: Found HEVC encoder: hevc_nvenc [nvenc]\n"
        )
        failures = {
            "wrong output": valid.replace("Name: eDP-1", "Name: DIARCHY"),
            "capture failure": valid + "Error: Frame capture failed\n",
            "missing H.264": valid.replace(
                "Info: Found H.264 encoder: h264_nvenc [nvenc]\n", ""
            ),
            "missing HEVC": valid.replace(
                "Info: Found HEVC encoder: hevc_nvenc [nvenc]\n", ""
            ),
        }

        for label, logs in failures.items():
            with self.subTest(label=label):
                result, _, _ = self.run_service_check(logs)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[fail]", result.stderr)

    def test_sunshine_wrapper_selects_latest_complete_live_instance_pair(self) -> None:
        wrapper = self.require_file("sunshine")
        with tempfile.TemporaryDirectory() as temp_home:
            apprun = (
                Path(temp_home)
                / ".local"
                / "opt"
                / "sunshine-2026.516.143833"
                / "AppRun"
            )
            apprun.parent.mkdir(parents=True)
            apprun.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$0" "$HYPRLAND_INSTANCE_SIGNATURE" '
                '"$WAYLAND_DISPLAY" "$@"\n'
            )
            apprun.chmod(0o755)
            fake_hyprctl = Path(temp_home) / "hyprctl"
            fake_hyprctl.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps(["
                '{"instance":"stale-signature","wl_socket":"wayland-stale","pid":0,"time":99},'
                '{"instance":"old-signature","wl_socket":"wayland-old","pid":100,"time":10},'
                '{"instance":"new-signature","wl_socket":"wayland-new","pid":200,"time":20},'
                '{"instance":"incomplete-signature","pid":300,"time":30}'
                "]))\n"
            )
            fake_hyprctl.chmod(0o755)
            environment = os.environ | {
                "HOME": temp_home,
                "HYPRCTL_BIN": str(fake_hyprctl),
                "HYPRLAND_INSTANCE_SIGNATURE": "wrong-signature",
                "WAYLAND_DISPLAY": "wayland-new",
            }
            result = subprocess.run(
                [str(wrapper), "--version"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [str(apprun), "new-signature", "wayland-new", "--version"],
        )

    def test_sunshine_wrapper_without_a_live_instance_exits_with_a_message(self) -> None:
        """Bughunt B-24: `jq -e` failed under `set -e` and the wrapper ended with exit 1 and an empty stderr."""
        wrapper = self.require_file("sunshine")
        for label, body in (("empty", "[]"), ("pid zero", '[{"instance":"s","wl_socket":"w","pid":0}]')):
            with self.subTest(label), tempfile.TemporaryDirectory() as temp:
                apprun = Path(temp) / ".local/opt/sunshine-2026.516.143833/AppRun"
                apprun.parent.mkdir(parents=True)
                ran = Path(temp) / "ran"
                apprun.write_text(f"#!/usr/bin/env bash\ntouch {ran}\n")
                apprun.chmod(0o755)
                hyprctl = Path(temp) / "hyprctl"
                hyprctl.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' '{body}'\n")
                hyprctl.chmod(0o755)
                result = subprocess.run([str(wrapper)], env=os.environ | {"HOME": temp, "HYPRCTL_BIN": str(hyprctl)},
                                        text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn("no Hyprland session is running for this user", result.stderr)
                self.assertFalse(ran.exists())

    def run_wrapper_env(self, libva: bool = True, libva_drm: bool = True, drivers: bool = True,
                        extra: dict[str, str] | None = None) -> dict[str, str]:
        """Runs the real wrapper with fake libva files/dir (paths overridable by env) and returns the LD_PRELOAD and
        LIBVA_DRIVERS_PATH the AppRun saw (`<unset>` when absent)."""
        wrapper = self.require_file("sunshine")
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            apprun = home / ".local/opt/sunshine-2026.516.143833/AppRun"
            apprun.parent.mkdir(parents=True)
            apprun.write_text(
                "#!/usr/bin/env bash\n"
                'printf "LD_PRELOAD=%s\\nLIBVA_DRIVERS_PATH=%s\\nLIBVA_DRIVER_NAME=%s\\n" "${LD_PRELOAD-<unset>}" '
                '"${LIBVA_DRIVERS_PATH-<unset>}" "${LIBVA_DRIVER_NAME-<unset>}"\n'
            )
            apprun.chmod(0o755)
            hyprctl = Path(temp) / "hyprctl"
            hyprctl.write_text(
                "#!/usr/bin/env python3\nimport json\n"
                'print(json.dumps([{"instance":"sig","wl_socket":"wayland-1","pid":1,"time":1}]))\n'
            )
            hyprctl.chmod(0o755)
            lib = Path(temp) / "lib"
            lib.mkdir()
            paths = {"DIARCHY_LIBVA_PATH": lib / "libva.so.2", "DIARCHY_LIBVA_DRM_PATH": lib / "libva-drm.so.2",
                     "DIARCHY_LIBVA_DRIVERS_DIR": lib / "dri"}
            if libva:
                paths["DIARCHY_LIBVA_PATH"].write_text("")
            if libva_drm:
                paths["DIARCHY_LIBVA_DRM_PATH"].write_text("")
            if drivers:
                paths["DIARCHY_LIBVA_DRIVERS_DIR"].mkdir()
            environment = {key: value for key, value in os.environ.items()
                           if key not in ("LD_PRELOAD", "LIBVA_DRIVERS_PATH", "LIBVA_DRIVER_NAME",
                                          "DIARCHY_LIBVA_DRIVER_NAME")}
            environment |= {"HOME": str(home), "HYPRCTL_BIN": str(hyprctl)}
            environment |= {key: str(value) for key, value in paths.items()} | (extra or {})
            result = subprocess.run([str(wrapper)], env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            seen = dict(line.split("=", 1) for line in result.stdout.splitlines())
            seen["lib"] = str(lib)
            return seen

    def test_wrapper_preloads_the_host_libva_and_points_at_its_drivers_when_present(self) -> None:
        seen = self.run_wrapper_env()
        lib = seen["lib"]
        self.assertEqual(seen["LD_PRELOAD"], f"{lib}/libva.so.2:{lib}/libva-drm.so.2")
        self.assertEqual(seen["LIBVA_DRIVERS_PATH"], f"{lib}/dri")

    def test_wrapper_prepends_to_an_existing_ld_preload(self) -> None:
        seen = self.run_wrapper_env(extra={"LD_PRELOAD": "/opt/other.so"})
        lib = seen["lib"]
        self.assertEqual(seen["LD_PRELOAD"], f"{lib}/libva.so.2:{lib}/libva-drm.so.2:/opt/other.so")

    def test_wrapper_leaves_the_environment_alone_unless_both_libva_files_exist(self) -> None:
        for label, kwargs in (("no libva", {"libva": False}), ("no libva-drm", {"libva_drm": False}),
                              ("neither", {"libva": False, "libva_drm": False})):
            with self.subTest(label):
                seen = self.run_wrapper_env(extra={"LD_PRELOAD": "/opt/other.so"}, **kwargs)
                self.assertEqual(seen["LD_PRELOAD"], "/opt/other.so")
                self.assertEqual(seen["LIBVA_DRIVERS_PATH"], "<unset>")

    def test_wrapper_without_a_drivers_dir_preloads_but_sets_no_drivers_path(self) -> None:
        seen = self.run_wrapper_env(drivers=False)
        self.assertIn("libva.so.2", seen["LD_PRELOAD"])
        self.assertEqual(seen["LIBVA_DRIVERS_PATH"], "<unset>")

    def test_wrapper_keeps_a_drivers_path_the_caller_already_set(self) -> None:
        seen = self.run_wrapper_env(extra={"LIBVA_DRIVERS_PATH": "/custom/dri"})
        self.assertEqual(seen["LIBVA_DRIVERS_PATH"], "/custom/dri")

    def test_wrapper_drops_the_sessions_libva_driver_name(self) -> None:
        # Omarchy exports LIBVA_DRIVER_NAME=nvidia session-wide; on the Intel render node libva must pick its own.
        for label, kwargs in (("libva present", {}), ("libva absent", {"libva": False, "libva_drm": False})):
            with self.subTest(label):
                seen = self.run_wrapper_env(extra={"LIBVA_DRIVER_NAME": "nvidia"}, **kwargs)
                self.assertEqual(seen["LIBVA_DRIVER_NAME"], "<unset>")

    def test_wrapper_exports_an_explicit_libva_driver_name_override(self) -> None:
        seen = self.run_wrapper_env(extra={"LIBVA_DRIVER_NAME": "nvidia", "DIARCHY_LIBVA_DRIVER_NAME": "iHD"})
        self.assertEqual(seen["LIBVA_DRIVER_NAME"], "iHD")

    def test_wrapper_ignores_an_empty_libva_driver_name_override(self) -> None:
        seen = self.run_wrapper_env(extra={"LIBVA_DRIVER_NAME": "nvidia", "DIARCHY_LIBVA_DRIVER_NAME": ""})
        self.assertEqual(seen["LIBVA_DRIVER_NAME"], "<unset>")


    # --- output_name: the session matches it against the Wayland output names, so the wrapper pins the name ---------

    CONF_HEADER = "# Managed by diarchy. Credentials and pairing state live outside this file.\n"

    def run_wrapper_pin(self, selected: str, monitors: list[dict], conf_text: str | None = None,
                        managed: bool = True) -> tuple[subprocess.CompletedProcess, str, bool]:
        """Runs the real wrapper with a conf argument, a fake `diarchy-display status` and a fake `hyprctl`. Returns the
        result, the conf text afterwards and whether AppRun ran."""
        wrapper = self.require_file("sunshine")
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            apprun = home / ".local/opt/sunshine-2026.516.143833/AppRun"
            apprun.parent.mkdir(parents=True)
            ran = Path(temp) / "ran"
            apprun.write_text(f'#!/usr/bin/env bash\ntouch {ran}\n')
            apprun.chmod(0o755)
            hyprctl = Path(temp) / "hyprctl"
            hyprctl.write_text(
                "#!/usr/bin/env python3\nimport json, sys\n"
                "if sys.argv[1:] == ['-j', 'monitors', 'all']:\n"
                f"    print(json.dumps({monitors!r}))\n"
                "else:\n"
                '    print(json.dumps([{"instance":"sig","wl_socket":"wayland-1","pid":1,"time":1}]))\n'
            )
            hyprctl.chmod(0o755)
            display = Path(temp) / "diarchy-display"
            display.write_text(f"#!/usr/bin/env bash\n[[ $1 == status ]] || exit 64\nprintf '%s\\n' {selected}\n")
            display.chmod(0o755)
            conf = Path(temp) / "sunshine.conf"
            text = conf_text if conf_text is not None else f"{self.CONF_HEADER}locale = en\ncapture = wlr\n"
            conf.write_text(text if managed else text.replace(self.CONF_HEADER, ""))
            conf.chmod(0o600)
            environment = os.environ | {"HOME": str(home), "HYPRCTL_BIN": str(hyprctl),
                                        "DIARCHY_DISPLAY_BIN": str(display)}
            result = subprocess.run([str(wrapper), str(conf)], env=environment, text=True, capture_output=True,
                                    check=False)
            return result, conf.read_text(), ran.exists()

    def test_wrapper_pins_the_headless_output_on_a_lid_closed_laptop(self) -> None:
        result, conf, ran = self.run_wrapper_pin(
            "DIARCHY", [{"id": 1, "name": "DIARCHY"}, {"id": 0, "name": "eDP-1", "disabled": False}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(ran)
        self.assertEqual(conf, f"{self.CONF_HEADER}locale = en\ncapture = wlr\noutput_name = DIARCHY\n")

    def test_wrapper_pins_the_name_of_the_selected_output_on_a_desktop(self) -> None:
        result, conf, _ = self.run_wrapper_pin(
            "HDMI-A-1", [{"id": 1, "name": "HDMI-A-1"}, {"id": 0, "name": "DP-1"}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\noutput_name = HDMI-A-1\n", conf)
        self.assertNotRegex(conf, r"output_name = [0-9]")

    def test_wrapper_pins_the_internal_panel_by_name_next_to_a_headless_output(self) -> None:
        """The reported field case: eDP-1 and DIARCHY both enabled. An index pin (`1`) made the startup probe take
        DIARCHY and every session take eDP-1. The name is the same for both."""
        result, conf, _ = self.run_wrapper_pin(
            "eDP-1", [{"id": 0, "name": "eDP-1"}, {"id": 1, "name": "DIARCHY"}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\noutput_name = eDP-1\n", conf)

    def test_wrapper_writes_output_name_once_and_keeps_the_rest_of_the_conf(self) -> None:
        old = f"{self.CONF_HEADER}output_name = 7\nlocale = en\n  output_name=3\ncapture = wlr\n"
        result, conf, _ = self.run_wrapper_pin("DP-1", [{"id": 0, "name": "DP-1"}], conf_text=old)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(conf, f"{self.CONF_HEADER}locale = en\ncapture = wlr\noutput_name = DP-1\n")

    def test_wrapper_fails_before_sunshine_when_the_selected_output_is_not_enabled(self) -> None:
        for label, monitors in (("unknown", [{"id": 0, "name": "DP-1"}]),
                                ("disabled", [{"id": 0, "name": "DP-1"}, {"id": 1, "name": "DP-2", "disabled": True}])):
            with self.subTest(label):
                result, conf, ran = self.run_wrapper_pin("DP-2", monitors)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DP-2", result.stderr)
                self.assertFalse(ran)
                self.assertNotIn("output_name", conf)

    def test_wrapper_leaves_a_conf_without_the_managed_header_alone(self) -> None:
        result, conf, ran = self.run_wrapper_pin("DP-1", [{"id": 0, "name": "DP-1"}], managed=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(ran)
        self.assertNotIn("output_name", conf)

    def test_installer_copies_wrapper_instead_of_symlinking_apprun(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn('install -m 755 \"$SCRIPT_DIR/sunshine\" \"$BIN_DIR/sunshine\"', installer)
        self.assertNotIn('ln -sfn \"$TARGET_DIR/AppRun\"', installer)

    def test_installer_version_check_is_safe_under_pipefail(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn('version_output=$(\"$BIN_DIR/sunshine\" --version 2>&1)', installer)
        self.assertNotIn('--version 2>&1 | grep -q', installer)

    def test_installer_preflight_is_vendor_neutral(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn("/dev/dri/renderD", installer)
        for forbidden in ("nvidia-smi", "nvenc", "NVENC"):
            self.assertNotIn(forbidden, installer)
        self.assertNotIn("sudo", installer.replace("never run sudo", ""))

    def test_installer_leaves_services_stopped_and_disabled(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn(
            "systemctl --user disable diarchy-display.service "
            "diarchy-sunshine.service",
            installer,
        )
        self.assertIn(
            "systemctl --user stop diarchy-sunshine.service "
            "diarchy-display.service",
            installer,
        )
        self.assertNotIn("reenable", installer)
        self.assertIn(
            'if ! selected_family=$("$BIN_DIR/diarchy-check-sunshine-service" --encoder); then',
            installer,
        )

    def test_installer_restarts_services_after_updating_managed_files(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn(
            "systemctl --user restart "
            "diarchy-display.service diarchy-sunshine.service",
            installer,
        )

    def test_installer_pins_the_capture_gpu_before_validating_sunshine(self) -> None:
        installer = self.require_file("install.sh").read_text()

        conf_installed = installer.index('install -m 600 "$SCRIPT_DIR/sunshine.conf"')
        display_installed = installer.index('"$SCRIPT_DIR/diarchy-display" "$BIN_DIR/diarchy-display"')
        pinned = installer.index('"$agent" stream configure')
        restarted = installer.index("systemctl --user restart diarchy-display.service")
        validated = installer.index("for _ in {1..30}")
        self.assertLess(max(conf_installed, display_installed), pinned)
        self.assertLess(pinned, restarted)
        self.assertLess(restarted, validated)
        self.assertIn("|| fail", installer[pinned:installer.index("\n", pinned)])  # a failing pin aborts the install

    def test_installer_publishes_credentials_only_after_sunshine_accepts_them(self) -> None:
        installer = self.require_file("install.sh").read_text()

        credential_call = installer.index(
            '"$BIN_DIR/sunshine" --creds "$ui_username" "$ui_password"'
        )
        credential_publish = installer.index(
            'mv "$credential_stage" "$CREDENTIAL_FILE"'
        )
        self.assertLess(credential_call, credential_publish)
        self.assertIn('credential_stage=$(mktemp "$CONFIG_DIR/.diarchy-ui.XXXXXX")', installer)
        self.assertNotIn('>"$CREDENTIAL_FILE"', installer)

    def test_installer_uses_the_same_fixed_config_paths_as_services_and_doctor(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn("CONFIG_DIR=$HOME/.config/sunshine", installer)
        self.assertIn("UNIT_DIR=$HOME/.config/systemd/user", installer)
        self.assertNotIn("XDG_CONFIG_HOME", installer)

    def test_installer_dry_run_is_rootless_and_does_not_mutate_home(self) -> None:
        installer = self.require_file("install.sh")
        with tempfile.TemporaryDirectory() as temp_home:
            result = subprocess.run(
                [str(installer), "--dry-run"],
                env=os.environ | {"HOME": temp_home},
                text=True,
                capture_output=True,
                check=False,
            )
            remaining = list(Path(temp_home).iterdir())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(remaining, [])
        self.assertIn("Sunshine 2026.516.143833", result.stdout)
        self.assertIn("d0ee0a9cfb66f27869b559455f84622d21615047ccf3443c9a2f572ca971c7a2", result.stdout)
        self.assertIn("rootless", result.stdout.lower())
        self.assertNotIn("sudo", result.stdout.lower())
        self.assertNotIn("nvenc", result.stdout.lower())
        self.assertIn("hardware", result.stdout.lower())
        self.assertNotIn("enable them", result.stdout.lower())


class HostDoctorTests(unittest.TestCase):
    DOCTOR = HOST / "diarchy-host-doctor"

    def run_doctor(self, *args: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ping_log = temp / "ping.log"
            for name, body in {
                "ping": '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"$FAKE_PING_LOG"\n'
                'echo "10 packets transmitted, 10 packets received, 0.0% packet loss"\n'
                'echo "round-trip min/avg/max/stddev = 1.0/2.0/3.0/0.5 ms"\n',
                "systemctl": "#!/usr/bin/env bash\nexit 0\n",
                "systemd-analyze": "#!/usr/bin/env bash\nexit 0\n",
            }.items():
                (temp / name).write_text(body)
                (temp / name).chmod(0o755)
            result = subprocess.run(
                [str(self.DOCTOR), *args],
                env=os.environ
                | {
                    "HOME": str(temp),
                    "PATH": f"{temp}:{os.environ['PATH']}",
                    "PING_BIN": str(temp / "ping"),
                    "FAKE_PING_LOG": str(ping_log),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            calls = ping_log.read_text().splitlines() if ping_log.exists() else []
            return result, calls

    def test_probes_the_given_controller_address(self) -> None:
        result, calls = self.run_doctor("198.51.100.20")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("198.51.100.20"), calls)
        self.assertIn("Mac LAN path is healthy", result.stdout)

    def test_skips_the_loss_probe_without_an_address(self) -> None:
        result, calls = self.run_doctor()
        self.assertEqual(calls, [])
        self.assertIn("skipped", result.stdout.lower())

    def test_rejects_option_like_or_shell_looking_addresses(self) -> None:
        for bad in ("-c", "1.2.3.4;id", "a b", "-", ".", ""):
            with self.subTest(address=bad):
                result, calls = self.run_doctor(bad)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("usage:", result.stderr)
                self.assertEqual(calls, [])

    def test_rejects_more_than_one_argument(self) -> None:
        result, calls = self.run_doctor("198.51.100.20", "198.51.100.21")
        self.assertEqual(result.returncode, 64)
        self.assertEqual(calls, [])

    def test_rejects_newline_in_controller_address(self) -> None:
        for bad in ("abc\n", "198.51.100.20\n", "198.51.\n100.20", "198.51.100.\r20"):
            with self.subTest(address=bad):
                result, calls = self.run_doctor(bad)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("usage:", result.stderr)
                self.assertEqual(calls, [])

    def test_accepts_maximum_length_controller_address(self) -> None:
        result, calls = self.run_doctor("a" * 253)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(result.returncode, 64, result.stderr)

    def test_rejects_oversized_controller_address(self) -> None:
        result, calls = self.run_doctor("a" * 254)
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("usage:", result.stderr)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
