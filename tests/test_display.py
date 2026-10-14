from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "host" / "diarchy-display"


def lua_move(workspace: int, output: str) -> list[str]:
    return [
        "dispatch",
        f'hl.dsp.workspace.move({{ workspace = "{workspace}", monitor = "{output}" }})',
    ]


def lua_focus(workspace: int) -> list[str]:
    return ["dispatch", f'hl.dsp.focus({{ workspace = "{workspace}" }})']


LUA_RULE = [
    "eval",
    'hl.monitor({ output = "DIARCHY", mode = "1920x1080@75", position = "0x0", scale = 1 })',
]


class DisplayCommandTests(unittest.TestCase):
    def run_display(
        self,
        action: str,
        *,
        monitors: list[dict[str, object]],
        workspaces: list[dict[str, object]] | None = None,
        active_workspace: int = 1,
        instances: list[dict[str, object]] | None = None,
        raw_instances: str | None = None,
        inherited_instance: tuple[str, str] | None = None,
        required_instance: tuple[str, str] | None = None,
        fail_monitor_query: bool = False,
        watch_event: str | None = None,
        socat_exit: int = 0,
        socat_sequence: list[dict[str, object]] | None = None,
        systemctl_exit: int = 0,
        physical: str | None = "eDP-1",
        lid: str | None = None,
        env_file: str | None = None,
        mode: str = "lua",
        remove_reply: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        self.assertTrue(SCRIPT.exists(), "host/diarchy-display must exist")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            state_path = temp / "state.json"
            log_path = temp / "commands.jsonl"
            fake_hyprctl = temp / "hyprctl"
            fake_socat = temp / "socat"
            fake_systemctl = temp / "systemctl"
            state_path.write_text(
                json.dumps(
                    {
                        "monitors": monitors,
                        "workspaces": workspaces or [],
                        "active_workspace": active_workspace,
                        "fail_monitors": fail_monitor_query,
                    }
                )
            )
            fake_hyprctl.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import os
                    import sys
                    from pathlib import Path

                    args = sys.argv[1:]
                    state = json.loads(Path(os.environ["FAKE_HYPR_STATE"]).read_text())
                    if args != ["instances", "-j"] and "FAKE_REQUIRED_SIGNATURE" in os.environ:
                        if (
                            os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
                            != os.environ["FAKE_REQUIRED_SIGNATURE"]
                            or os.environ.get("WAYLAND_DISPLAY")
                            != os.environ["FAKE_REQUIRED_WAYLAND"]
                        ):
                            print("Hyprland instance pair is inconsistent!", file=sys.stderr)
                            raise SystemExit(55)
                    if args == ["-j", "monitors", "all"]:
                        if state.get("fail_monitors", False):
                            raise SystemExit(42)
                        print(json.dumps(state["monitors"]))
                    elif args == ["-j", "workspaces"]:
                        print(json.dumps(state["workspaces"]))
                    elif args == ["-j", "activeworkspace"]:
                        print(json.dumps({"id": state["active_workspace"]}))
                    elif args == ["instances", "-j"]:
                        print(os.environ["FAKE_HYPR_INSTANCES"])
                    else:
                        mode = os.environ.get("FAKE_HYPR_MODE", "lua")
                        with Path(os.environ["FAKE_HYPR_LOG"]).open("a") as log:
                            log.write(json.dumps(args) + "\\n")
                        verb = args[0]
                        is_lua = len(args) > 1 and args[1].startswith("hl.dsp.")
                        if verb == "output":
                            if args[1] == "remove" and "FAKE_REMOVE_REPLY" in os.environ:
                                print(os.environ["FAKE_REMOVE_REPLY"])
                            else:
                                print("ok")
                        elif mode == "broken":
                            print("error: boom")
                        elif mode == "lua":
                            if verb == "eval" or (verb == "dispatch" and is_lua):
                                print("ok")
                            elif verb == "keyword":
                                print("keyword can't work with non-legacy parsers. Use eval.")
                            else:
                                print("error: ... ')' expected near '1'")
                        else:
                            if verb == "eval":
                                print("eval is only supported with the lua config manager")
                            elif verb == "dispatch" and is_lua:
                                print("Invalid dispatcher")
                            else:
                                print("ok")
                    """
                )
            )
            fake_hyprctl.chmod(0o755)
            fake_socat.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import os
                    from pathlib import Path

                    attempt_path = Path(os.environ["FAKE_SOCAT_ATTEMPT"])
                    attempt = int(attempt_path.read_text()) if attempt_path.exists() else 0
                    sequence = json.loads(os.environ["FAKE_SOCAT_SEQUENCE"])
                    entry = sequence[min(attempt, len(sequence) - 1)]
                    attempt_path.write_text(str(attempt + 1))
                    with Path(os.environ["FAKE_HYPR_LOG"]).open("a") as log:
                        log.write(json.dumps(["socat"]) + "\\n")
                    event = entry.get("event")
                    if event:
                        state_path = Path(os.environ["FAKE_HYPR_STATE"])
                        state = json.loads(state_path.read_text())
                        if event.startswith("monitorremoved"):
                            for monitor in state["monitors"]:
                                if monitor["name"] == "eDP-1":
                                    monitor["disabled"] = True
                        if entry.get("fail_monitor_after_event"):
                            state["fail_monitors"] = True
                        state_path.write_text(json.dumps(state))
                        print(event, flush=True)
                    raise SystemExit(int(entry["exit"]))
                    """
                )
            )
            fake_socat.chmod(0o755)
            fake_systemctl.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >>"$FAKE_SYSTEMCTL_LOG"\n'
                'exit "$FAKE_SYSTEMCTL_EXIT"\n'
            )
            fake_systemctl.chmod(0o755)

            default_instances = [
                {
                    "instance": "fake-signature",
                    "wl_socket": "wayland-test",
                    "pid": 1,
                    "time": 1,
                }
            ]
            env = os.environ | {
                "HYPRCTL_BIN": str(fake_hyprctl),
                "SOCAT_BIN": str(fake_socat),
                "SYSTEMCTL_BIN": str(fake_systemctl),
                "FAKE_HYPR_STATE": str(state_path),
                "FAKE_HYPR_LOG": str(log_path),
                "FAKE_HYPR_INSTANCES": raw_instances
                if raw_instances is not None
                else json.dumps(instances or default_instances),
                "FAKE_SOCAT_SEQUENCE": json.dumps(
                    socat_sequence
                    or [{"event": watch_event or "", "exit": socat_exit}]
                ),
                "FAKE_SOCAT_ATTEMPT": str(temp / "socat-attempt"),
                "FAKE_SYSTEMCTL_EXIT": str(systemctl_exit),
                "FAKE_SYSTEMCTL_LOG": str(temp / "systemctl.log"),
                "FAKE_HYPR_MODE": mode,
                "DIARCHY_DISPLAY_ENV": str(temp / "display.env"),
                "HEADLESS_OUTPUT": "DIARCHY",
                "HEADLESS_MODE": "1920x1080@75",
            }
            if remove_reply is not None:
                env["FAKE_REMOVE_REPLY"] = remove_reply
            if physical is None:
                env.pop("PHYSICAL_OUTPUT", None)
            else:
                env["PHYSICAL_OUTPUT"] = physical
            if lid is not None:
                lid_dir = temp / "proc/acpi/button/lid/LID0"
                lid_dir.mkdir(parents=True)
                (lid_dir / "state").write_text(f"state:      {lid}\n")
            if env_file is not None:
                (temp / "display.env").write_text(env_file)
            if inherited_instance is None:
                env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
                env.pop("WAYLAND_DISPLAY", None)
            else:
                env["HYPRLAND_INSTANCE_SIGNATURE"] = inherited_instance[0]
                env["WAYLAND_DISPLAY"] = inherited_instance[1]
            if required_instance is not None:
                env["FAKE_REQUIRED_SIGNATURE"] = required_instance[0]
                env["FAKE_REQUIRED_WAYLAND"] = required_instance[1]
            result = subprocess.run(
                [str(SCRIPT), action],
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=3,
            )
            commands = []
            if log_path.exists():
                commands = [json.loads(line) for line in log_path.read_text().splitlines()]
            return result, commands

    def test_creates_headless_output_when_physical_output_is_absent(self) -> None:
        result, commands = self.run_display(
            "reconcile",
            monitors=[],
            workspaces=[{"id": 1, "monitor": "eDP-1"}],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            commands,
            [
                ["output", "create", "headless", "DIARCHY"],
                LUA_RULE,
                lua_move(1, "DIARCHY"),
                lua_focus(1),
            ],
        )
        self.assertEqual(result.stdout.strip(), "DIARCHY")

    def test_existing_headless_output_is_idempotent(self) -> None:
        result, commands = self.run_display(
            "reconcile",
            monitors=[
                {
                    "name": "DIARCHY",
                    "disabled": False,
                    "width": 1920,
                    "height": 1080,
                    "refreshRate": 75.0,
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                }
            ],
            workspaces=[{"id": 1, "monitor": "DIARCHY"}],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, [])
        self.assertEqual(result.stdout.strip(), "DIARCHY")

    def test_repairs_existing_headless_output_with_wrong_mode_without_recreating_it(self) -> None:
        result, commands = self.run_display(
            "reconcile",
            monitors=[
                {
                    "name": "DIARCHY",
                    "disabled": False,
                    "width": 1280,
                    "height": 720,
                    "refreshRate": 60.0,
                    "x": 20,
                    "y": 10,
                    "scale": 1.5,
                }
            ],
            workspaces=[{"id": 1, "monitor": "DIARCHY"}],
            physical=None,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, [LUA_RULE])
        self.assertEqual(result.stdout.strip(), "DIARCHY")

    def test_disabled_physical_output_is_treated_as_absent(self) -> None:
        result, commands = self.run_display(
            "reconcile",
            monitors=[{"name": "eDP-1", "disabled": True}],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands[0], ["output", "create", "headless", "DIARCHY"])

    def test_returns_workspaces_before_removing_owned_headless_output(self) -> None:
        result, commands = self.run_display(
            "reconcile",
            monitors=[
                {"name": "eDP-1", "disabled": False},
                {"name": "DIARCHY", "disabled": False},
            ],
            workspaces=[
                {"id": 2, "monitor": "DIARCHY"},
                {"id": 5, "monitor": "eDP-1"},
                {"id": 7, "monitor": "DIARCHY"},
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            commands,
            [lua_move(2, "eDP-1"), lua_move(7, "eDP-1"), ["output", "remove", "DIARCHY"]],
        )
        self.assertEqual(result.stdout.strip(), "eDP-1")

    def test_status_reports_selected_output_without_mutation(self) -> None:
        result, commands = self.run_display(
            "status",
            monitors=[{"name": "eDP-1", "disabled": False}],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, [])
        self.assertEqual(result.stdout.strip(), "eDP-1")

    def test_status_with_the_lid_closed_selects_the_enabled_internal_panel(self) -> None:
        result, commands = self.run_display(
            "status", monitors=[{"name": "eDP-1", "disabled": False}], lid="closed", physical=None
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, [])
        self.assertEqual(result.stdout.strip(), "eDP-1")

    def test_status_without_a_physical_monitor_reports_when_the_headless_output_is_pending(self) -> None:
        result, commands = self.run_display(
            "status", monitors=[{"name": "eDP-1", "disabled": True}], physical=None
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, [])
        self.assertEqual(result.stdout.splitlines()[0], "DIARCHY")
        self.assertIn("created at stream start", result.stdout)

    def test_status_with_an_existing_headless_output_does_not_say_it_is_pending(self) -> None:
        result, _ = self.run_display(
            "status",
            monitors=[{"name": "eDP-1", "disabled": True}, {"name": "DIARCHY", "disabled": False}],
            physical=None,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "DIARCHY")

    def test_status_fails_when_hyprctl_fails_or_returns_no_array(self) -> None:
        result, _ = self.run_display(
            "status", monitors=[{"name": "eDP-1", "disabled": False}], fail_monitor_query=True
        )
        self.assertNotEqual(result.returncode, 0)
        result, _ = self.run_display("status", monitors={"error": "x"}, lid="closed")  # type: ignore[arg-type]
        self.assertNotEqual(result.returncode, 0)

    def test_discovers_running_instance_from_non_graphical_shell(self) -> None:
        result, commands = self.run_display(
            "status",
            monitors=[{"name": "eDP-1", "disabled": False}],
            required_instance=("fake-signature", "wayland-test"),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "eDP-1")
        self.assertEqual(commands, [])

    def test_physical_external_output_is_captured_and_not_hardcoded(self) -> None:
        for name in ("DP-1", "HDMI-A-1"):
            with self.subTest(name=name):
                result, commands = self.run_display(
                    "reconcile",
                    monitors=[{"name": name, "disabled": False}],
                    physical=name,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), name)
                self.assertEqual(commands, [])

    def test_physical_output_is_read_from_the_env_file_and_the_variable_wins(self) -> None:
        monitors = [{"name": "DP-1", "disabled": False}, {"name": "HDMI-A-1", "disabled": False}]
        result, _ = self.run_display(
            "status", monitors=monitors, physical=None, env_file="PHYSICAL_OUTPUT=HDMI-A-1\n"
        )
        self.assertEqual(result.stdout.strip(), "HDMI-A-1")
        result, _ = self.run_display(
            "status", monitors=monitors, physical="DP-1", env_file="PHYSICAL_OUTPUT=HDMI-A-1\n"
        )
        self.assertEqual(result.stdout.strip(), "DP-1")

    def test_env_file_with_headless_name_falls_back_to_the_auto_rule(self) -> None:
        result, commands = self.run_display(
            "reconcile",
            monitors=[{"name": "DP-1", "disabled": False}],
            physical=None,
            env_file="PHYSICAL_OUTPUT=DIARCHY\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "DP-1")
        self.assertEqual(commands, [])

    def test_without_configuration_the_auto_rule_applies(self) -> None:
        cases = {
            "laptop": ([("eDP-1", False, False), ("DP-2", False, True)], "eDP-1"),
            "desktop focus": ([("DP-1", False, False), ("HDMI-A-1", False, True)], "HDMI-A-1"),
            "desktop first": ([("DP-1", True, False), ("HDMI-A-1", False, False)], "HDMI-A-1"),
            "lvds": ([("HDMI-A-1", False, True), ("LVDS-1", False, False)], "LVDS-1"),
            "dsi": ([("DSI-1", False, False)], "DSI-1"),
        }
        for label, (rows, expected) in cases.items():
            with self.subTest(label=label):
                result, _ = self.run_display(
                    "status",
                    monitors=[{"name": n, "disabled": d, "focused": f} for n, d, f in rows],
                    physical=None,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_without_configuration_and_no_enabled_monitor_the_headless_output_is_used(self) -> None:
        result, commands = self.run_display(
            "reconcile", monitors=[{"name": "DP-1", "disabled": True}], physical=None
        )
        self.assertEqual(result.stdout.strip(), "DIARCHY")
        self.assertEqual(commands[0], ["output", "create", "headless", "DIARCHY"])

    def test_closed_lid_with_the_internal_panel_enabled_selects_the_panel(self) -> None:
        result, commands = self.run_display(
            "reconcile",
            monitors=[{"name": "eDP-1", "disabled": False}],
            workspaces=[{"id": 1, "monitor": "eDP-1"}],
            lid="closed",
            physical=None,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "eDP-1")
        self.assertEqual(commands, [])

    def test_restore_moves_stale_headless_workspaces_then_removes_the_output(self) -> None:
        for mode in ("lua", "legacy"):
            with self.subTest(mode=mode):
                result, commands = self.run_display(
                    "restore",
                    monitors=[
                        {"name": "eDP-1", "disabled": False},
                        {"name": "DIARCHY", "disabled": False},
                    ],
                    workspaces=[
                        {"id": 2, "monitor": "DIARCHY"},
                        {"id": 5, "monitor": "eDP-1"},
                        {"id": 7, "monitor": "DIARCHY"},
                    ],
                    mode=mode,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "eDP-1")
                self.assertEqual(commands[-1], ["output", "remove", "DIARCHY"])
                self.assertNotIn(["output", "create", "headless", "DIARCHY"], commands)
                if mode == "lua":
                    self.assertEqual(
                        commands,
                        [lua_move(2, "eDP-1"), lua_move(7, "eDP-1"), ["output", "remove", "DIARCHY"]],
                    )
                else:
                    self.assertEqual(
                        commands,
                        [
                            lua_move(2, "eDP-1"),
                            ["dispatch", "moveworkspacetomonitor", "2", "eDP-1"],
                            ["dispatch", "moveworkspacetomonitor", "7", "eDP-1"],
                            ["output", "remove", "DIARCHY"],
                        ],
                    )

    def test_restore_keeps_the_headless_output_when_no_physical_monitor_is_enabled(self) -> None:
        result, commands = self.run_display(
            "restore",
            monitors=[{"name": "eDP-1", "disabled": True}, {"name": "DIARCHY", "disabled": False}],
            workspaces=[{"id": 2, "monitor": "DIARCHY"}],
            physical=None,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "DIARCHY")
        self.assertEqual(commands, [])

    def test_restore_never_creates_the_headless_output(self) -> None:
        for monitors, expected in (
            ([{"name": "eDP-1", "disabled": True}], "DIARCHY"),
            ([{"name": "eDP-1", "disabled": False}], "eDP-1"),
        ):
            with self.subTest(expected=expected):
                result, commands = self.run_display("restore", monitors=monitors, physical=None)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
                self.assertEqual(commands, [])

    def test_both_syntaxes_pass_reconcile_and_detection_sends_the_lua_form_once(self) -> None:
        for mode in ("lua", "legacy"):
            with self.subTest(mode=mode):
                result, commands = self.run_display(
                    "reconcile",
                    monitors=[],
                    workspaces=[{"id": 1, "monitor": "eDP-1"}, {"id": 3, "monitor": "eDP-1"}],
                    mode=mode,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip().splitlines()[-1], "DIARCHY")
                lua_dispatches = [
                    c for c in commands if c[0] == "dispatch" and c[1].startswith("hl.dsp.")
                ]
                if mode == "lua":
                    self.assertEqual(
                        commands,
                        [
                            ["output", "create", "headless", "DIARCHY"],
                            LUA_RULE,
                            lua_move(1, "DIARCHY"),
                            lua_move(3, "DIARCHY"),
                            lua_focus(1),
                        ],
                    )
                else:
                    self.assertEqual(
                        commands,
                        [
                            ["output", "create", "headless", "DIARCHY"],
                            LUA_RULE,
                            ["keyword", "monitor", "DIARCHY,1920x1080@75,0x0,1"],
                            ["dispatch", "moveworkspacetomonitor", "1", "DIARCHY"],
                            ["dispatch", "moveworkspacetomonitor", "3", "DIARCHY"],
                            ["dispatch", "workspace", "1"],
                        ],
                    )
                    self.assertEqual(len(lua_dispatches), 0)  # the eval already detected legacy

    def test_a_dispatch_that_fails_in_both_forms_exits_with_an_error(self) -> None:
        result, _ = self.run_display(
            "reconcile",
            monitors=[],
            workspaces=[{"id": 1, "monitor": "eDP-1"}],
            mode="broken",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("diarchy-display: hyprctl", result.stderr)
        self.assertIn("lua: error: boom legacy: error: boom", result.stderr)

    def test_output_remove_reply_that_is_not_ok_exits_with_an_error(self) -> None:
        result, _ = self.run_display(
            "restore",
            monitors=[
                {"name": "eDP-1", "disabled": False},
                {"name": "DIARCHY", "disabled": False},
            ],
            remove_reply="error: no such output",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("diarchy-display: hyprctl", result.stderr)
        self.assertIn("no such output", result.stderr)

    def test_auto_in_the_env_file_uses_the_auto_rule(self) -> None:
        result, _ = self.run_display(
            "status",
            monitors=[{"name": "DP-1", "disabled": False}, {"name": "eDP-1", "disabled": False}],
            physical=None,
            env_file="PHYSICAL_OUTPUT=auto\n",
        )
        self.assertEqual(result.stdout.strip(), "eDP-1")

    def test_a_disabled_named_output_falls_back_to_the_auto_rule(self) -> None:
        result, _ = self.run_display(
            "status",
            monitors=[{"name": "DP-9", "disabled": True}, {"name": "eDP-1", "disabled": False}],
            physical="DP-9",
        )
        self.assertEqual(result.stdout.strip(), "eDP-1")

    def test_closed_lid_does_not_affect_an_external_output(self) -> None:
        result, commands = self.run_display(
            "reconcile",
            monitors=[{"name": "DP-1", "disabled": False}],
            physical="DP-1",
            lid="closed",
        )
        self.assertEqual(result.stdout.strip(), "DP-1")
        self.assertEqual(commands, [])

    def test_invalid_action_returns_usage_error(self) -> None:
        result, commands = self.run_display("invalid", monitors=[])

        self.assertEqual(result.returncode, 64)
        self.assertIn("reconcile|restore|watch|status", result.stderr)
        self.assertEqual(commands, [])

    def test_watch_propagates_initial_monitor_query_failure(self) -> None:
        result, _ = self.run_display(
            "watch",
            monitors=[],
            fail_monitor_query=True,
        )

        self.assertEqual(result.returncode, 42, result.stderr)

    def test_watch_does_not_reconnect_after_event_socket_failure(self) -> None:
        result, commands = self.run_display(
            "watch",
            monitors=[{"name": "eDP-1", "disabled": False}],
            socat_exit=23,
        )

        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(commands.count(["socat"]), 1)
        self.assertNotIn(["socat"], commands[1:])

    def test_watch_reconnects_only_after_clean_event_socket_closure(self) -> None:
        result, commands = self.run_display(
            "watch",
            monitors=[{"name": "eDP-1", "disabled": False}],
            socat_sequence=[
                {"event": "", "exit": 0},
                {"event": "", "exit": 23},
            ],
        )

        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(commands.count(["socat"]), 2)

    def test_watch_propagates_monitor_query_failure_after_event(self) -> None:
        result, commands = self.run_display(
            "watch",
            monitors=[{"name": "eDP-1", "disabled": False}],
            socat_sequence=[
                {
                    "event": "monitoradded>>HDMI-A-1",
                    "exit": 0,
                    "fail_monitor_after_event": True,
                }
            ],
        )

        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(commands.count(["socat"]), 1)

    def test_watch_propagates_sunshine_restart_failure(self) -> None:
        result, _ = self.run_display(
            "watch",
            monitors=[{"name": "eDP-1", "disabled": False}],
            workspaces=[{"id": 1, "monitor": "eDP-1"}],
            watch_event="monitorremoved>>eDP-1",
            systemctl_exit=33,
        )

        self.assertEqual(result.returncode, 33, result.stderr)

    def test_selects_latest_complete_live_instance_as_one_pair(self) -> None:
        result, commands = self.run_display(
            "status",
            monitors=[{"name": "eDP-1", "disabled": False}],
            instances=[
                {
                    "instance": "stale-signature",
                    "wl_socket": "wayland-stale",
                    "pid": 0,
                    "time": 99,
                },
                {
                    "instance": "old-signature",
                    "wl_socket": "wayland-old",
                    "pid": 100,
                    "time": 10,
                },
                {
                    "instance": "new-signature",
                    "wl_socket": "wayland-new",
                    "pid": 200,
                    "time": 20,
                },
                {
                    "instance": "incomplete-signature",
                    "pid": 300,
                    "time": 30,
                },
            ],
            inherited_instance=("wrong-signature", "wayland-new"),
            required_instance=("new-signature", "wayland-new"),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "eDP-1")
        self.assertEqual(commands, [])

    def test_without_a_usable_hyprland_instance_every_action_exits_with_one_clear_line(self) -> None:
        # After an Omarchy logout only SDDM's greeter Hyprland is left; hyprctl as the user lists nothing usable.
        unusable = {
            "empty list": "[]",
            "not JSON": "not json at all",
            "only a dead pid": '[{"instance": "a", "wl_socket": "wayland-1", "pid": 0}]',
            "no wl_socket": '[{"instance": "a", "pid": 5}]',
        }
        for action in ("reconcile", "status", "watch"):
            for name, raw in unusable.items():
                with self.subTest(action=action, instances=name):
                    result, commands = self.run_display(
                        action, monitors=[{"name": "eDP-1", "disabled": False}], raw_instances=raw
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(
                        result.stderr.strip(),
                        "diarchy-display: no Hyprland session is running for this user",
                    )
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
