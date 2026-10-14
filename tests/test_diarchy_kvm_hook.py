from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KVM = ROOT / "client" / "diarchy-kvm"


def ere_escape(text: str) -> str:
    """The escape that client/diarchy-kvm applies before it gives a path to pgrep/pkill as a pattern."""
    return "".join("\\" + ch if ch in "[]\\.*+?(){}|^$" else ch for ch in text)


BRIDGE_IDENTITY = "diarchy-clipboard-bridge-runtime-v1"

FILE_PORTAL_RECEIVER_IDENTITY = "diarchy-file-portal-receiver"
FILE_PORTAL_APP_IDENTITY = "diarchy-file-portal"

@dataclass
class KvmFixture:
    root: Path
    env: dict[str, str]
    runtime_dir: Path
    runtime_lock: Path
    bridge_pid: int
    event_log: Path
    event_counter: Path
    lan_log: Path
    bridge_log: Path
    kill_log: Path
    sleep_log: Path

    portal_runtime_dir: Path
    portal_receiver_pid: int
    portal_app_pid: int
    portal_log: Path

def run_kvm(command: str, state: KvmFixture) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(KVM), command],
        env=state.env,
        text=True,
        capture_output=True,
        check=False,
    )


class DiarchyKvmHookTests(unittest.TestCase):
    def make_fixture(
        self,
        *,
        lan_enabled: bool = False,
        bridge_installed: bool = True,
        bridge_running: bool = False,
        bridge_pid: int = 42420,
        ps_command: str | None = None,
        start_mode: str = "success",
        kill_mode: str = "term_exits",
        lan_kill_mode: str = "term_exits",
        portals_installed: bool = True,
        portal_receiver_running: bool = False,
        portal_app_running: bool = False,
        portal_receiver_pid: int = 52520,
        portal_app_pid: int = 52521,
        portal_receiver_ps_command: str | None = None,
        portal_app_ps_command: str | None = None,
        portal_receiver_start_mode: str = "success",
        portal_app_start_mode: str = "success",
    ) -> KvmFixture:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        app = root / "Lan Mouse.app"
        executable = app / "Contents" / "MacOS" / "lan-mouse"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/usr/bin/env bash\nexit 0\n")
        executable.chmod(0o755)

        bin_dir = root / "bin"
        bin_dir.mkdir()
        tmp = root / "tmp"
        tmp.mkdir()
        bridge = bin_dir / "diarchy-clipboard-bridge"
        if bridge_installed:
            bridge.write_text("#!/usr/bin/env bash\nexit 0\n")
            bridge.chmod(0o755)


        portal_app = root / "Diarchy File Portal.app"
        portal_executable = portal_app / "Contents" / "MacOS" / "diarchy-file-portal"
        portal_receiver = bin_dir / "diarchy-file-portal-receiver"
        portal_sender = bin_dir / "diarchy-file-portal-send"
        if portals_installed:
            portal_executable.parent.mkdir(parents=True)
            portal_executable.write_text("#!/usr/bin/env bash\nexit 0\n")
            portal_executable.chmod(0o755)
            portal_receiver.write_text("#!/usr/bin/env bash\nexit 0\n")
            portal_receiver.chmod(0o755)
            portal_sender.write_text("#!/usr/bin/env bash\nexit 0\n")
            portal_sender.chmod(0o755)

        portal_runtime_dir = tmp / f"diarchy-file-portal-{os.getuid()}"
        portal_receiver_pid_file = portal_runtime_dir / "receiver.pid"
        portal_app_pid_file = portal_runtime_dir / "app.pid"
        portal_state = root / "portal-running"
        if portal_receiver_running or portal_app_running:
            portal_runtime_dir.mkdir()
        if portal_receiver_running:
            portal_receiver_pid_file.write_text(f"{portal_receiver_pid}\n")
            (portal_runtime_dir / "receiver.ready").write_text("ready\n")
        if portal_app_running:
            portal_app_pid_file.write_text(f"{portal_app_pid}\n")
            portal_state.touch()
        runtime_dir = tmp / f"diarchy-clipboard-bridge-{os.getuid()}"
        runtime_lock = tmp / f"diarchy-clipboard-bridge-{os.getuid()}.lock"
        pid_file = runtime_dir / "pid"
        if bridge_running:
            runtime_dir.mkdir()
            pid_file.write_text(f"{bridge_pid}\n{BRIDGE_IDENTITY}\n")
            runtime_lock.write_text("persistent sibling lock\n")

        lan_state = root / "lan-running"
        if lan_enabled:
            lan_state.touch()
        event_log = root / "events.log"
        event_counter = root / "events.seq"
        lan_log = root / "lan.log"
        bridge_log = root / "bridge.log"
        portal_log = root / "portal.log"
        kill_log = root / "kill.log"
        sleep_log = root / "sleep.log"

        pgrep = root / "pgrep"
        open_bin = root / "open"
        pkill = root / "pkill"
        ps = root / "ps"
        kill = root / "kill"
        sleep = root / "sleep"
        nohup = root / "nohup"
        event_prefix = (
            "event() { "
            "local n=0; "
            "[[ -f \"$FAKE_EVENT_COUNTER\" ]] && n=$(cat \"$FAKE_EVENT_COUNTER\"); "
            "n=$((n + 1)); "
            "printf '%s' \"$n\" >\"$FAKE_EVENT_COUNTER\"; "
            "printf '%04d:%s\\n' \"$n\" \"$1\" >>\"$FAKE_EVENT_LOG\"; "
            "}\n"
        )

        pgrep.write_text(
            "#!/usr/bin/env bash\n"
            + event_prefix
            + "printf 'pgrep:%s\\n' \"$*\" >>\"$FAKE_LAN_LOG\"\n"
            "case \"$*\" in\n"
            "  \"-f -x $FAKE_EXECUTABLE_PATTERN\"|\"-f -x $FAKE_EXECUTABLE_PATTERN daemon\")\n"
            "    event \"lan-pgrep:$*\"\n"
            "    [[ -f $FAKE_LAN_STATE ]] || exit 1\n"
            "    echo 10101\n"
            "    ;;\n"
            "  \"-f -x $FAKE_FILE_PORTAL_EXECUTABLE_PATTERN\")\n"
            "    event \"portal-pgrep:$*\"\n"
            "    [[ -f $FAKE_PORTAL_STATE ]] || exit 1\n"
            "    echo \"$FAKE_FILE_PORTAL_APP_PID\"\n"
            "    ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n"
        )
        open_bin.write_text(
            "#!/usr/bin/env bash\n"
            + event_prefix
            + "printf 'open:%s\\n' \"$*\" >>\"$FAKE_LAN_LOG\"\n"
            "case \"$*\" in\n"
            "  \"-gj $FAKE_LAN_MOUSE_APP\") event \"lan-open:$*\"; touch \"$FAKE_LAN_STATE\" ;;\n"
            "  \"-g $FAKE_FILE_PORTAL_APP\") event \"portal-open:$*\"; [[ \"$FAKE_FILE_PORTAL_APP_START_MODE\" == fail ]] || touch \"$FAKE_PORTAL_STATE\" ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n"
        )
        pkill.write_text(
            "#!/usr/bin/env bash\n"
            + event_prefix
            + "printf 'pkill:%s\\n' \"$*\" >>\"$FAKE_LAN_LOG\"\n"
            "event \"lan-pkill:$*\"\n"
            "[[ $* == \"-TERM -f -x $FAKE_EXECUTABLE_PATTERN\" || $* == \"-TERM -f -x $FAKE_EXECUTABLE_PATTERN daemon\" || $* == \"-KILL -f -x $FAKE_EXECUTABLE_PATTERN\" || $* == \"-KILL -f -x $FAKE_EXECUTABLE_PATTERN daemon\" ]] || exit 2\n"
            "if [[ $FAKE_LAN_KILL_MODE == term_exits || $1 == -KILL ]]; then rm -f \"$FAKE_LAN_STATE\"; fi\n"
        )
        ps.write_text(
            "#!/usr/bin/env bash\n"
            + event_prefix
            + "if [[ $1 != '-p' || $3 != '-o' || $4 != 'command=' ]]; then exit 1; fi\n"
            "if [[ $2 == \"$FAKE_BRIDGE_PID\" ]]; then\n"
            "  event \"bridge-ps:$*\"\n"
            "  [[ -f \"$FAKE_RUNTIME_DIR/pid\" ]] || exit 1\n"
            "  [[ -n ${FAKE_PS_COMMAND:-} ]] || exit 1\n"
            "  printf '%s\\n' \"$FAKE_PS_COMMAND\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ -f \"$FAKE_FILE_PORTAL_RUNTIME_DIR/receiver.pid\" && $(cat \"$FAKE_FILE_PORTAL_RUNTIME_DIR/receiver.pid\") == \"$2\" ]]; then\n"
            "  event \"portal-receiver-ps:$*\"\n"
            "  [[ -n ${FAKE_FILE_PORTAL_RECEIVER_PS_COMMAND:-} ]] || exit 1\n"
            "  printf '%s\\n' \"$FAKE_FILE_PORTAL_RECEIVER_PS_COMMAND\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ -f \"$FAKE_FILE_PORTAL_RUNTIME_DIR/app.pid\" && $(cat \"$FAKE_FILE_PORTAL_RUNTIME_DIR/app.pid\") == \"$2\" ]]; then\n"
            "  event \"portal-app-ps:$*\"\n"
            "  [[ -n ${FAKE_FILE_PORTAL_APP_PS_COMMAND:-} ]] || exit 1\n"
            "  printf '%s\\n' \"$FAKE_FILE_PORTAL_APP_PS_COMMAND\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n"
        )
        kill.write_text(
            "#!/usr/bin/env bash\n"
            + event_prefix
            + "printf '%s %s\\n' \"$1\" \"$2\" >>\"$FAKE_KILL_LOG\"\n"
            "if [[ $2 == \"$FAKE_BRIDGE_PID\" ]]; then\n"
            "  printf 'bridge-kill:%s %s\\n' \"$1\" \"$2\" >>\"$FAKE_LAN_LOG\"\n"
            "  event \"bridge-kill:$1 $2\"\n"
            "  case \"$FAKE_KILL_MODE:$1\" in\n"
            "    term_exits:-TERM) rm -rf \"$FAKE_RUNTIME_DIR\" ;;\n"
            "    needs_kill:-KILL) rm -rf \"$FAKE_RUNTIME_DIR\" ;;\n"
            "  esac\n"
            "elif [[ -f \"$FAKE_FILE_PORTAL_RUNTIME_DIR/receiver.pid\" && $(cat \"$FAKE_FILE_PORTAL_RUNTIME_DIR/receiver.pid\") == \"$2\" ]]; then\n"
            "  event \"portal-receiver-kill:$1 $2\"\n"
            "  rm -f \"$FAKE_FILE_PORTAL_RUNTIME_DIR/receiver.pid\"\n"
            "elif [[ -f \"$FAKE_FILE_PORTAL_RUNTIME_DIR/app.pid\" && $(cat \"$FAKE_FILE_PORTAL_RUNTIME_DIR/app.pid\") == \"$2\" ]]; then\n"
            "  event \"portal-app-kill:$1 $2\"\n"
            "  rm -f \"$FAKE_FILE_PORTAL_RUNTIME_DIR/app.pid\" \"$FAKE_PORTAL_STATE\"\n"
            "fi\n"
        )
        sleep.write_text(
            "#!/usr/bin/env bash\n"
            + event_prefix
            + "printf 'sleep:%s\\n' \"$1\" >>\"$FAKE_SLEEP_LOG\"\n"
            "event \"sleep:$1\"\n"
            "if [[ \"$FAKE_START_MODE\" == delayed && $(wc -l <\"$FAKE_SLEEP_LOG\") -eq 25 ]]; then mkdir -p \"$FAKE_RUNTIME_DIR\"; printf '%s\\n%s\\n' \"$FAKE_BRIDGE_PID\" \"$FAKE_BRIDGE_IDENTITY\" >\"$FAKE_RUNTIME_DIR/pid\"; touch \"$FAKE_RUNTIME_LOCK\"; fi\n"
            "if [[ \"$FAKE_FILE_PORTAL_RECEIVER_START_MODE\" == delayed && -f \"$FAKE_FILE_PORTAL_RUNTIME_DIR/receiver.pid\" && $(wc -l <\"$FAKE_SLEEP_LOG\") -ge 5 ]]; then touch \"$FAKE_FILE_PORTAL_RUNTIME_DIR/receiver.ready\"; fi\n"
            "if [[ ${FAKE_REAL_SLEEP:-0} == 1 ]]; then /bin/sleep \"$1\"; fi\n"
        )
        nohup.write_text(
            "#!/usr/bin/env bash\n"
            + event_prefix
            + "if [[ $1 == \"$FAKE_BRIDGE_BIN\" ]]; then\n"
            "  printf 'start:%s\\n' \"$*\" >>\"$FAKE_BRIDGE_LOG\"\n"
            "  [[ -z ${DIARCHY_CLIPBOARD_DEBUG_LOG:-} ]] || printf 'debug:%s\\n' \"$DIARCHY_CLIPBOARD_DEBUG_LOG\" >>\"$FAKE_BRIDGE_LOG\"\n"
            "  event \"bridge-start:$*\"\n"
            "  case \"$FAKE_START_MODE\" in\n"
            "    success) mkdir -p \"$FAKE_RUNTIME_DIR\"; printf '%s\\n%s\\n' \"$FAKE_BRIDGE_PID\" \"$FAKE_BRIDGE_IDENTITY\" >\"$FAKE_RUNTIME_DIR/pid\"; touch \"$FAKE_RUNTIME_LOCK\" ;;\n"
            "    crash) mkdir -p \"$FAKE_RUNTIME_DIR\"; printf '%s\\n%s\\n' \"$FAKE_BRIDGE_PID\" \"$FAKE_BRIDGE_IDENTITY\" >\"$FAKE_RUNTIME_DIR/pid\"; touch \"$FAKE_RUNTIME_LOCK\"; exit 0 ;;\n"
            "    fail) exit 1 ;;\n"
            "  esac\n"
            "elif [[ $1 == \"$FAKE_FILE_PORTAL_RECEIVER_BIN\" ]]; then\n"
            "  printf 'receiver-start:%s\\n' \"$*\" >>\"$FAKE_PORTAL_LOG\"\n"
            "  event \"portal-receiver-start:$*\"\n"
            "  [[ \"$FAKE_FILE_PORTAL_RECEIVER_START_MODE\" == fail ]] && exit 1\n"
            "  [[ \"$FAKE_FILE_PORTAL_RECEIVER_START_MODE\" =~ ^(slow|delayed)$ ]] || touch \"$DIARCHY_FILE_PORTAL_READY_FILE\"\n"
            "fi\n"
        )
        for tool in (pgrep, open_bin, pkill, ps, kill, sleep, nohup):
            tool.chmod(0o755)

        env = os.environ | {
            "LAN_MOUSE_APP": str(app),
            "FILE_PORTAL_APP": str(portal_app),
            "FILE_PORTAL_RECEIVER_BIN": str(portal_receiver),
            "FILE_PORTAL_SENDER_BIN": str(portal_sender),
            "FILE_PORTAL_RUNTIME_DIR": str(portal_runtime_dir),
            "PGREP_BIN": str(pgrep),
            "PKILL_BIN": str(pkill),
            "OPEN_BIN": str(open_bin),
            "PS_BIN": str(ps),
            "KILL_BIN": str(kill),
            "SLEEP_BIN": str(sleep),
            "NOHUP_BIN": str(nohup),
            "CLIPBOARD_BRIDGE_BIN": str(bridge),
            "TMPDIR": str(tmp),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_LAN_STATE": str(lan_state),
            "FAKE_EXECUTABLE": str(executable),
            "FAKE_EXECUTABLE_PATTERN": ere_escape(str(executable)),
            "FAKE_LAN_MOUSE_APP": str(app),
            "FAKE_BRIDGE_BIN": str(bridge),
            "FAKE_FILE_PORTAL_APP": str(portal_app),
            "FAKE_FILE_PORTAL_EXECUTABLE": str(portal_executable),
            "FAKE_FILE_PORTAL_EXECUTABLE_PATTERN": ere_escape(str(portal_executable)),
            "FAKE_FILE_PORTAL_RECEIVER_BIN": str(portal_receiver),
            "FAKE_FILE_PORTAL_RUNTIME_DIR": str(portal_runtime_dir),
            "FAKE_FILE_PORTAL_APP_PID": str(portal_app_pid),
            "FAKE_FILE_PORTAL_RECEIVER_PID": str(portal_receiver_pid),
            "FAKE_PORTAL_STATE": str(portal_state),
            "FAKE_LAN_LOG": str(lan_log),
            "FAKE_BRIDGE_LOG": str(bridge_log),
            "FAKE_PORTAL_LOG": str(portal_log),
            "FAKE_KILL_LOG": str(kill_log),
            "FAKE_SLEEP_LOG": str(sleep_log),
            "FAKE_EVENT_LOG": str(event_log),
            "FAKE_EVENT_COUNTER": str(event_counter),
            "FAKE_BRIDGE_PID": str(bridge_pid),
            "FAKE_BRIDGE_IDENTITY": BRIDGE_IDENTITY,
            "FAKE_RUNTIME_DIR": str(runtime_dir),
            "FAKE_RUNTIME_LOCK": str(runtime_lock),
            "FAKE_START_MODE": start_mode,
            "FAKE_KILL_MODE": kill_mode,
            "FAKE_LAN_KILL_MODE": lan_kill_mode,
            "FAKE_PS_COMMAND": ps_command
            if ps_command is not None
            else f"python3 -c payload --{BRIDGE_IDENTITY} --routed 250 ssh host diarchy-clipboard-remote",
            "FAKE_FILE_PORTAL_RECEIVER_PS_COMMAND": portal_receiver_ps_command
            if portal_receiver_ps_command is not None
            else str(portal_receiver),
            "FAKE_FILE_PORTAL_APP_PS_COMMAND": portal_app_ps_command
            if portal_app_ps_command is not None
            else str(portal_executable),
            "FAKE_FILE_PORTAL_RECEIVER_START_MODE": portal_receiver_start_mode,
            "FAKE_FILE_PORTAL_APP_START_MODE": portal_app_start_mode,
        }
        return KvmFixture(
            root,
            env,
            runtime_dir,
            runtime_lock,
            bridge_pid,
            event_log,
            event_counter,
            lan_log,
            bridge_log,
            kill_log,
            sleep_log,
            portal_runtime_dir,
            portal_receiver_pid,
            portal_app_pid,
            portal_log,
        )

    def read_log(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return path.read_text().splitlines()

    def event_messages(self, state: KvmFixture) -> list[str]:
        return [line.split(":", 1)[1] for line in self.read_log(state.event_log)]

    def assert_event_order(self, state: KvmFixture, *needles: str) -> None:
        events = self.event_messages(state)
        cursor = -1
        for needle in needles:
            for index, event in enumerate(events[cursor + 1 :], cursor + 1):
                if needle in event:
                    cursor = index
                    break
            else:
                self.fail(f"{needle!r} not found after index {cursor} in event log: {events}")

    def test_on_starts_exactly_one_bridge_after_lan_mouse(self) -> None:
        state = self.make_fixture(lan_enabled=False, bridge_installed=True)
        result = run_kvm("on", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_log(state.bridge_log), [f"start:{state.env['CLIPBOARD_BRIDGE_BIN']}"])
        self.assert_event_order(
            state,
            "lan-open:-gj " + state.env["LAN_MOUSE_APP"],
            "lan-pgrep:-f -x " + state.env["FAKE_EXECUTABLE_PATTERN"],
            "bridge-start:" + state.env["CLIPBOARD_BRIDGE_BIN"],
        )
        self.assertTrue(state.runtime_dir.exists())
        self.assertTrue(state.runtime_lock.exists())

    def test_debug_mode_passes_bounded_log_path_to_bridge(self) -> None:
        state = self.make_fixture(lan_enabled=False, bridge_installed=True)
        state.env["DIARCHY_CLIPBOARD_DEBUG"] = "1"
        result = run_kvm("on", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.read_log(state.bridge_log),
            [
                f"start:{state.env['CLIPBOARD_BRIDGE_BIN']}",
                f"debug:{state.env['TMPDIR']}/diarchy-clipboard-bridge-debug-{os.getuid()}.log",
            ],
        )

    def test_sec33_unset_tmpdir_uses_user_cache_not_tmp(self) -> None:
        state = self.make_fixture(lan_enabled=False, bridge_installed=True)
        home = Path(state.env["TMPDIR"]) / "home"
        home.mkdir()
        del state.env["TMPDIR"]
        state.env["HOME"] = str(home)
        cache = home / "Library" / "Caches"
        state.env["FAKE_RUNTIME_DIR"] = str(cache / f"diarchy-clipboard-bridge-{os.getuid()}")
        state.env["FAKE_RUNTIME_LOCK"] = str(cache / f"diarchy-clipboard-bridge-{os.getuid()}.lock")
        state.env["DIARCHY_CLIPBOARD_DEBUG"] = "1"
        result = run_kvm("on", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"debug:{home}/Library/Caches/diarchy-clipboard-bridge-debug-{os.getuid()}.log",
            self.read_log(state.bridge_log),
        )
        self.assertTrue((home / "Library" / "Caches").is_dir())

    def test_sec33_symlinked_runtime_directory_is_refused(self) -> None:
        state = self.make_fixture(lan_enabled=False, bridge_installed=True, portals_installed=True)
        elsewhere = Path(state.env["TMPDIR"]) / "elsewhere"
        elsewhere.mkdir()
        if state.portal_runtime_dir.exists():
            for child in state.portal_runtime_dir.iterdir():
                child.unlink()
            state.portal_runtime_dir.rmdir()
        state.portal_runtime_dir.symlink_to(elsewhere)
        result = run_kvm("on", state)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_on_allows_route_probe_to_finish_before_marking_bridge_crashed(self) -> None:
        state = self.make_fixture(lan_enabled=False, bridge_installed=True, start_mode="delayed")
        result = run_kvm("on", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = run_kvm("status", state)
        self.assertEqual(status.stdout, "Diarchy KVM: enabled (clipboard bridge: on, file portals: on)\n")

    def test_status_names_clipboard_bridge_contract(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            bridge_installed=True,
            portal_receiver_running=True,
            portal_app_running=True,
        )

        status = run_kvm("status", state)

        self.assertEqual(
            status.stdout,
            "Diarchy KVM: enabled (clipboard bridge: on, file portals: on)\n",
        )

    def test_on_is_idempotent_when_bridge_already_running(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_running=True, bridge_installed=True)
        result = run_kvm("on", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: already enabled\n")
        self.assertEqual(self.read_log(state.bridge_log), [])

    def test_on_requires_clipboard_bridge(self) -> None:
        state = self.make_fixture(lan_enabled=False, bridge_installed=False)
        result = run_kvm("on", state)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clipboard bridge is not installed", result.stderr)
        self.assertFalse((state.root / "lan-running").exists())

    def test_immediate_bridge_start_failure_rolls_back_kvm(self) -> None:
        state = self.make_fixture(lan_enabled=False, bridge_installed=True, start_mode="fail")
        result = run_kvm("on", state)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clipboard bridge did not remain running", result.stderr)
        self.assertFalse((state.root / "lan-running").exists())
        status = run_kvm("status", state)
        self.assertEqual(status.returncode, 1)
        self.assertEqual(status.stdout, "Diarchy KVM: disabled (clipboard bridge: off, file portals: off)\n")

    def test_start_failure_escalates_stubborn_lan_mouse_rollback(self) -> None:
        state = self.make_fixture(
            lan_enabled=False,
            bridge_installed=True,
            start_mode="fail",
            lan_kill_mode="needs_kill",
        )

        result = run_kvm("on", state)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((state.root / "lan-running").exists())
        self.assertIn(
            "pkill:-KILL -f -x " + state.env["FAKE_EXECUTABLE_PATTERN"],
            self.read_log(state.lan_log),
        )


    def test_post_pid_bridge_crash_rolls_back_kvm(self) -> None:
        state = self.make_fixture(
            lan_enabled=False,
            bridge_installed=True,
            start_mode="crash",
            ps_command="",
        )
        result = run_kvm("on", state)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clipboard bridge did not remain running", result.stderr)
        self.assertFalse((state.root / "lan-running").exists())
        status = run_kvm("status", state)
        self.assertEqual(status.returncode, 1)
        self.assertEqual(status.stdout, "Diarchy KVM: disabled (clipboard bridge: off, file portals: off)\n")

    def test_off_verifies_pid_then_reaps_bridge_before_lan_mouse(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_running=True, bridge_installed=True)
        result = run_kvm("off", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assertEqual(self.read_log(state.kill_log), [f"-TERM {state.bridge_pid}"])
        self.assertFalse(state.runtime_dir.exists())
        self.assertTrue(state.runtime_lock.exists())
        self.assert_event_order(
            state,
            f"bridge-ps:-p {state.bridge_pid} -o command=",
            f"bridge-kill:-TERM {state.bridge_pid}",
            "lan-pkill:-TERM -f -x " + state.env["FAKE_EXECUTABLE_PATTERN"],
        )

    def test_stale_pid_is_cleaned_without_signalling_unrelated_process(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            bridge_installed=True,
            ps_command="/usr/bin/python3 unrelated-service",
        )
        result = run_kvm("off", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assertEqual(self.read_log(state.kill_log), [])
        self.assertFalse(state.runtime_dir.exists())
        self.assertTrue(state.runtime_lock.exists())

    def test_malformed_present_pid_reports_crashed_and_off_cleans_without_signal(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_installed=True)
        state.runtime_dir.mkdir()
        (state.runtime_dir / "pid").write_text("not-a-pid\n")
        state.runtime_lock.write_text("persistent sibling lock\n")
        status = run_kvm("status", state)
        self.assertEqual(status.returncode, 1)
        self.assertEqual(status.stdout, "Diarchy KVM: degraded (clipboard bridge: crashed, file portals: off)\n")
        result = run_kvm("off", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assertEqual(self.read_log(state.kill_log), [])
        self.assertFalse((state.runtime_dir / "pid").exists())
        self.assertTrue(state.runtime_lock.exists())

    def test_unreadable_present_pid_reports_crashed_and_off_cleans_without_signal(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_installed=True)
        state.runtime_dir.mkdir()
        pid_file = state.runtime_dir / "pid"
        pid_file.write_text(f"{state.bridge_pid}\n")
        pid_file.chmod(0)
        state.runtime_lock.write_text("persistent sibling lock\n")
        self.addCleanup(lambda: pid_file.chmod(0o600) if pid_file.exists() else None)
        status = run_kvm("status", state)
        self.assertEqual(status.returncode, 1)
        self.assertEqual(status.stdout, "Diarchy KVM: degraded (clipboard bridge: crashed, file portals: off)\n")
        result = run_kvm("off", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assertEqual(self.read_log(state.kill_log), [])
        self.assertFalse(pid_file.exists())
        self.assertTrue(state.runtime_lock.exists())

    def test_missing_pid_status_is_off_and_stable_lock_is_preserved(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_installed=True)
        state.runtime_dir.mkdir()
        state.runtime_lock.write_text("persistent sibling lock\n")
        status = run_kvm("status", state)
        self.assertEqual(status.returncode, 1)
        self.assertEqual(status.stdout, "Diarchy KVM: degraded (clipboard bridge: off, file portals: off)\n")
        result = run_kvm("off", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_log(state.kill_log), [])
        self.assertTrue(state.runtime_lock.exists())

    def test_exact_identity_does_not_match_substring(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            bridge_installed=True,
            ps_command=f"python3 --x{BRIDGE_IDENTITY}x",
        )
        result = run_kvm("off", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assertEqual(self.read_log(state.kill_log), [])
        self.assertFalse(state.runtime_dir.exists())

    def test_stop_escalates_from_term_to_kill_with_bounded_waits(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_running=True, bridge_installed=True, kill_mode="needs_kill")
        result = run_kvm("off", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assertEqual(self.read_log(state.kill_log), [f"-TERM {state.bridge_pid}", f"-KILL {state.bridge_pid}"])
        self.assertLessEqual(len(self.read_log(state.sleep_log)), 35)
        self.assertFalse(state.runtime_dir.exists())
        self.assertTrue(state.runtime_lock.exists())

    def test_failed_term_and_kill_retains_recoverable_metadata_and_reports_crashed(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_running=True, bridge_installed=True, kill_mode="never_exits")
        result = run_kvm("off", state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assertEqual(self.read_log(state.kill_log), [f"-TERM {state.bridge_pid}", f"-KILL {state.bridge_pid}"])
        self.assertTrue((state.runtime_dir / "pid").exists())
        self.assertEqual((state.runtime_dir / "status").read_text(), "crashed\n")
        self.assertTrue(state.runtime_lock.exists())
        status = run_kvm("status", state)
        self.assertEqual(status.returncode, 1)
        self.assertEqual(status.stdout, "Diarchy KVM: degraded (clipboard bridge: crashed, file portals: off)\n")


    def test_on_rejects_crashed_bridge_with_verified_live_pid(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_running=True, bridge_installed=True, kill_mode="never_exits")
        first_off = run_kvm("off", state)
        self.assertEqual(first_off.returncode, 0, first_off.stderr)
        self.assertEqual(self.read_log(state.kill_log), [f"-TERM {state.bridge_pid}", f"-KILL {state.bridge_pid}"])
        self.assertEqual((state.runtime_dir / "status").read_text(), "crashed\n")
        state.event_log.write_text("")
        state.event_counter.write_text("0")

        result = run_kvm("on", state)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clipboard bridge did not remain running", result.stderr)
        self.assertEqual(self.read_log(state.bridge_log), [])
        self.assertTrue((state.runtime_dir / "pid").exists())
        self.assertEqual((state.runtime_dir / "pid").read_text(), f"{state.bridge_pid}\n{BRIDGE_IDENTITY}\n")
        self.assertEqual((state.runtime_dir / "status").read_text(), "crashed\n")
        self.assertNotIn("bridge-start:" + state.env["CLIPBOARD_BRIDGE_BIN"], self.event_messages(state))
        self.assert_event_order(
            state,
            f"bridge-ps:-p {state.bridge_pid} -o command=",
        )
    def test_second_off_after_stubborn_live_failure_retries_and_recovers(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_running=True, bridge_installed=True, kill_mode="never_exits")
        first_off = run_kvm("off", state)
        self.assertEqual(first_off.returncode, 0, first_off.stderr)
        self.assertEqual(self.read_log(state.kill_log), [f"-TERM {state.bridge_pid}", f"-KILL {state.bridge_pid}"])
        self.assertEqual((state.runtime_dir / "status").read_text(), "crashed\n")
        state.env["FAKE_KILL_MODE"] = "needs_kill"
        state.event_log.write_text("")
        state.event_counter.write_text("0")

        second_off = run_kvm("off", state)

        self.assertEqual(second_off.returncode, 0, second_off.stderr)
        self.assertEqual(second_off.stdout, "Diarchy KVM: already disabled\n")
        self.assertEqual(
            self.read_log(state.kill_log),
            [f"-TERM {state.bridge_pid}", f"-KILL {state.bridge_pid}", f"-TERM {state.bridge_pid}", f"-KILL {state.bridge_pid}"],
        )
        self.assertFalse(state.runtime_dir.exists())
        self.assertTrue(state.runtime_lock.exists())
        self.assert_event_order(
            state,
            f"bridge-ps:-p {state.bridge_pid} -o command=",
            f"bridge-kill:-TERM {state.bridge_pid}",
            "sleep:0.1",
            f"bridge-kill:-KILL {state.bridge_pid}",
            "lan-pgrep:-f -x " + state.env["FAKE_EXECUTABLE_PATTERN"],
        )

    def test_second_off_after_repeated_stubborn_live_failure_retains_recoverable_metadata(self) -> None:
        state = self.make_fixture(lan_enabled=True, bridge_running=True, bridge_installed=True, kill_mode="never_exits")
        first_off = run_kvm("off", state)
        self.assertEqual(first_off.returncode, 0, first_off.stderr)
        self.assertEqual((state.runtime_dir / "status").read_text(), "crashed\n")
        state.event_log.write_text("")
        state.event_counter.write_text("0")

        second_off = run_kvm("off", state)

        self.assertEqual(second_off.returncode, 0, second_off.stderr)
        self.assertEqual(second_off.stdout, "Diarchy KVM: already disabled\n")
        self.assertEqual(
            self.read_log(state.kill_log),
            [f"-TERM {state.bridge_pid}", f"-KILL {state.bridge_pid}", f"-TERM {state.bridge_pid}", f"-KILL {state.bridge_pid}"],
        )
        self.assertEqual((state.runtime_dir / "pid").read_text(), f"{state.bridge_pid}\n{BRIDGE_IDENTITY}\n")
        self.assertEqual((state.runtime_dir / "status").read_text(), "crashed\n")
        self.assertTrue(state.runtime_lock.exists())
        self.assertGreaterEqual(self.event_messages(state).count(f"bridge-ps:-p {state.bridge_pid} -o command="), 32)
        self.assert_event_order(
            state,
            f"bridge-ps:-p {state.bridge_pid} -o command=",
            f"bridge-kill:-TERM {state.bridge_pid}",
            "sleep:0.1",
            f"bridge-kill:-KILL {state.bridge_pid}",
        )
    def test_on_starts_verified_file_portals_after_lan_mouse_and_clipboard(self) -> None:
        state = self.make_fixture(lan_enabled=False, bridge_installed=True, portals_installed=True)

        result = run_kvm("on", state)
        status = run_kvm("status", state)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: enabled\n")
        self.assertEqual(self.read_log(state.portal_log), [f"receiver-start:{state.env['FILE_PORTAL_RECEIVER_BIN']}"])
        self.assertEqual(status.returncode, 0)
        self.assertEqual(
            status.stdout,
            "Diarchy KVM: enabled (clipboard bridge: on, file portals: on)\n",
        )
        self.assertTrue((state.portal_runtime_dir / "receiver.pid").exists())
        self.assertTrue((state.portal_runtime_dir / "app.pid").exists())
        self.assert_event_order(
            state,
            "lan-open:-gj " + state.env["LAN_MOUSE_APP"],
            "bridge-start:" + state.env["CLIPBOARD_BRIDGE_BIN"],
            "portal-receiver-start:" + state.env["FILE_PORTAL_RECEIVER_BIN"],
            "portal-open:-g " + state.env["FILE_PORTAL_APP"],
        )

    def test_on_is_idempotent_when_file_portals_are_already_running(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            bridge_installed=True,
            portals_installed=True,
            portal_receiver_running=True,
            portal_app_running=True,
        )

        result = run_kvm("on", state)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: already enabled\n")
        self.assertEqual(self.read_log(state.bridge_log), [])
        self.assertEqual(self.read_log(state.portal_log), [])
        self.assertNotIn("portal-open:-g " + state.env["FILE_PORTAL_APP"], self.event_messages(state))

    def test_off_stops_file_portals_before_clipboard_and_lan_mouse(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            bridge_installed=True,
            portals_installed=True,
            portal_receiver_running=True,
            portal_app_running=True,
        )

        result = run_kvm("off", state)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assert_event_order(
            state,
            "portal-receiver-kill:-TERM",
            "portal-app-kill:-TERM",
            f"bridge-kill:-TERM {state.bridge_pid}",
            "lan-pkill:-TERM -f -x " + state.env["FAKE_EXECUTABLE_PATTERN"],
        )
        self.assertFalse((state.portal_runtime_dir / "receiver.pid").exists())
        self.assertFalse((state.portal_runtime_dir / "app.pid").exists())

    def test_file_portal_stale_pid_metadata_is_cleaned_without_signal(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_installed=True,
            portals_installed=True,
            portal_receiver_running=True,
            portal_app_running=True,
            portal_receiver_ps_command="/usr/bin/python3 unrelated-receiver",
            portal_app_ps_command="/usr/bin/python3 unrelated-app",
        )

        result = run_kvm("off", state)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Diarchy KVM: disabled\n")
        self.assertEqual(self.read_log(state.kill_log), [])
        self.assertFalse((state.portal_runtime_dir / "receiver.pid").exists())
        self.assertFalse((state.portal_runtime_dir / "app.pid").exists())


    def test_off_stops_verified_portals_after_install_artifacts_disappear(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            portal_receiver_running=True,
            portal_app_running=True,
        )
        Path(state.env["FILE_PORTAL_SENDER_BIN"]).unlink()
        Path(state.env["FAKE_FILE_PORTAL_EXECUTABLE"]).unlink()

        result = run_kvm("off", state)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_event_order(
            state,
            "portal-receiver-kill:-TERM",
            "portal-app-kill:-TERM",
            f"bridge-kill:-TERM {state.bridge_pid}",
        )
        self.assertFalse((state.portal_runtime_dir / "receiver.pid").exists())
        self.assertFalse((state.portal_runtime_dir / "app.pid").exists())

    def test_on_terminates_verified_receiver_that_never_became_ready(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            portal_receiver_running=True,
            portal_app_running=True,
        )
        (state.portal_runtime_dir / "receiver.ready").unlink()

        result = run_kvm("on", state)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_event_order(
            state,
            "portal-receiver-kill:-TERM",
            "portal-receiver-start:" + state.env["FILE_PORTAL_RECEIVER_BIN"],
        )

    def test_concurrent_on_calls_serialize_receiver_startup(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            portal_receiver_start_mode="delayed",
        )
        state.env["FAKE_REAL_SLEEP"] = "1"
        first = subprocess.Popen(
            [str(KVM), "on"],
            env=state.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 3
        while not (state.portal_runtime_dir / "receiver.pid").exists():
            self.assertLess(time.monotonic(), deadline, "first KVM start never reached receiver")
            time.sleep(0.01)
        second = subprocess.Popen(
            [str(KVM), "on"],
            env=state.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)

        self.assertEqual(first.returncode, 0, first_stdout + first_stderr)
        self.assertEqual(second.returncode, 0, second_stdout + second_stderr)
        starts = [
            line
            for line in self.read_log(state.portal_log)
            if line.startswith("receiver-start:")
        ]
        self.assertEqual(len(starts), 1)

    def test_signal_defers_until_locked_action_is_consistent(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            portal_receiver_start_mode="delayed",
        )
        state.env["FAKE_REAL_SLEEP"] = "1"
        first = subprocess.Popen(
            [str(KVM), "on"],
            env=state.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 3
        while not (state.portal_runtime_dir / "receiver.pid").exists():
            self.assertLess(time.monotonic(), deadline, "KVM start never reached receiver")
            time.sleep(0.01)

        first.send_signal(signal.SIGTERM)
        second = subprocess.Popen(
            [str(KVM), "on"],
            env=state.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)

        self.assertEqual(first.returncode, 143, first_stdout + first_stderr)
        self.assertEqual(second.returncode, 0, second_stdout + second_stderr)
        starts = [
            line
            for line in self.read_log(state.portal_log)
            if line.startswith("receiver-start:")
        ]
        self.assertEqual(len(starts), 1)
        self.assertFalse((Path(state.env["TMPDIR"]) / f"diarchy-kvm-action-{os.getuid()}.lock").exists())

    def test_receiver_must_publish_readiness_before_kvm_enables(self) -> None:
        state = self.make_fixture(
            lan_enabled=False,
            bridge_installed=True,
            portal_receiver_start_mode="slow",
        )

        result = run_kvm("on", state)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("file portal receiver did not become ready", result.stderr)
        self.assertFalse((state.root / "lan-running").exists())
        self.assertFalse(state.runtime_dir.exists())
        self.assertFalse((state.portal_runtime_dir / "receiver.pid").exists())
        self.assertFalse((state.portal_runtime_dir / "receiver.ready").exists())
        self.assertFalse((state.portal_runtime_dir / "app.pid").exists())

    def test_receiver_pid_identity_rejects_embedded_expected_path(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            bridge_installed=True,
            portal_receiver_running=True,
            portal_app_running=True,
        )
        state.env["FAKE_FILE_PORTAL_RECEIVER_PS_COMMAND"] = (
            f"/usr/bin/python3 unrelated --note {state.env['FILE_PORTAL_RECEIVER_BIN']}"
        )

        status = run_kvm("status", state)
        result = run_kvm("off", state)

        self.assertEqual(status.returncode, 1)
        self.assertEqual(
            status.stdout,
            "Diarchy KVM: degraded (clipboard bridge: on, file portals: crashed)\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(state.portal_receiver_pid), "\n".join(self.read_log(state.kill_log)))

    def test_receiver_pid_identity_accepts_macos_python_interpreter(self) -> None:
        state = self.make_fixture(
            lan_enabled=True,
            bridge_running=True,
            bridge_installed=True,
            portal_receiver_running=True,
            portal_app_running=True,
        )
        state.env["FAKE_FILE_PORTAL_RECEIVER_PS_COMMAND"] = (
            "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/"
            "Python.framework/Versions/3.14/Resources/Python.app/"
            "Contents/MacOS/Python "
            f"{state.env['FILE_PORTAL_RECEIVER_BIN']}"
        )

        result = run_kvm("status", state)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            "Diarchy KVM: enabled (clipboard bridge: on, file portals: on)\n",
        )

    def test_file_portal_start_failure_rolls_back_started_components(self) -> None:
        state = self.make_fixture(
            lan_enabled=False,
            bridge_installed=True,
            portals_installed=True,
            portal_app_start_mode="fail",
        )

        result = run_kvm("on", state)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("file portal app did not remain running", result.stderr)
        self.assertFalse((state.root / "lan-running").exists())
        self.assertFalse(state.runtime_dir.exists())
        self.assertFalse((state.portal_runtime_dir / "receiver.pid").exists())
        self.assertFalse((state.portal_runtime_dir / "app.pid").exists())
        self.assert_event_order(
            state,
            "lan-open:-gj " + state.env["LAN_MOUSE_APP"],
            "bridge-start:" + state.env["CLIPBOARD_BRIDGE_BIN"],
            "portal-receiver-start:" + state.env["FILE_PORTAL_RECEIVER_BIN"],
            "portal-open:-g " + state.env["FILE_PORTAL_APP"],
            "portal-receiver-kill:-TERM",
            "lan-pkill:-TERM -f -x " + state.env["FAKE_EXECUTABLE_PATTERN"],
        )

    def test_status_requires_every_kvm_component(self) -> None:
        cases = [
            (
                "all-absent",
                dict(lan_enabled=False, bridge_installed=False, portals_installed=False),
                "Diarchy KVM: disabled (clipboard bridge: absent, file portals: absent)\n",
            ),
            (
                "all-off",
                dict(lan_enabled=False, bridge_installed=True),
                "Diarchy KVM: disabled (clipboard bridge: off, file portals: off)\n",
            ),
            (
                "lan-only",
                dict(lan_enabled=True, bridge_installed=True),
                "Diarchy KVM: degraded (clipboard bridge: off, file portals: off)\n",
            ),
            (
                "bridge-only",
                dict(lan_enabled=False, bridge_installed=True, bridge_running=True),
                "Diarchy KVM: degraded (clipboard bridge: on, file portals: off)\n",
            ),
            (
                "portals-only",
                dict(
                    lan_enabled=True,
                    bridge_installed=True,
                    portal_receiver_running=True,
                    portal_app_running=True,
                ),
                "Diarchy KVM: degraded (clipboard bridge: off, file portals: on)\n",
            ),
            (
                "all-on",
                dict(
                    lan_enabled=True,
                    bridge_installed=True,
                    bridge_running=True,
                    portal_receiver_running=True,
                    portal_app_running=True,
                ),
                "Diarchy KVM: enabled (clipboard bridge: on, file portals: on)\n",
            ),
            (
                "portal-partial",
                dict(lan_enabled=True, bridge_installed=True, portal_receiver_running=True),
                "Diarchy KVM: degraded (clipboard bridge: off, file portals: crashed)\n",
            ),
            (
                "bridge-crashed",
                dict(lan_enabled=False, bridge_installed=True, bridge_running=True, ps_command=""),
                "Diarchy KVM: degraded (clipboard bridge: crashed, file portals: off)\n",
            ),
        ]
        for name, fixture_kwargs, expected_stdout in cases:
            with self.subTest(name=name):
                state = self.make_fixture(**fixture_kwargs)
                result = run_kvm("status", state)
                self.assertEqual(result.returncode, 0 if name == "all-on" else 1)
                self.assertEqual(result.stdout, expected_stdout)

    def test_toggle_preserves_exit_codes_and_lan_mouse_call_order(self) -> None:
        on_state = self.make_fixture(lan_enabled=False, bridge_installed=True)
        toggled_on = run_kvm("toggle", on_state)
        self.assertEqual(toggled_on.returncode, 0, toggled_on.stderr)
        self.assertEqual(toggled_on.stdout, "Diarchy KVM: enabled\n")
        self.assertEqual(self.read_log(on_state.bridge_log), [f"start:{on_state.env['CLIPBOARD_BRIDGE_BIN']}"])
        self.assert_event_order(
            on_state,
            "lan-open:-gj " + on_state.env["LAN_MOUSE_APP"],
            "lan-pgrep:-f -x " + on_state.env["FAKE_EXECUTABLE_PATTERN"],
            "bridge-start:" + on_state.env["CLIPBOARD_BRIDGE_BIN"],
        )

        off_state = self.make_fixture(lan_enabled=True, bridge_running=True, bridge_installed=True)
        toggled_off = run_kvm("toggle", off_state)
        self.assertEqual(toggled_off.returncode, 0, toggled_off.stderr)
        self.assertEqual(toggled_off.stdout, "Diarchy KVM: disabled\n")
        self.assert_event_order(
            off_state,
            f"bridge-ps:-p {off_state.bridge_pid} -o command=",
            f"bridge-kill:-TERM {off_state.bridge_pid}",
            "lan-pkill:-TERM -f -x " + off_state.env["FAKE_EXECUTABLE_PATTERN"],
        )


if __name__ == "__main__":
    unittest.main()
