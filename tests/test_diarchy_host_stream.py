from __future__ import annotations

import base64
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from tests.test_diarchy_host_support import AgentCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from diarchy_host import stream  # noqa: E402


class _Sunshine(BaseHTTPRequestHandler):
    replies: list[dict] = []
    requests: list[tuple[str, dict, bytes]] = []
    status_code: int = 200
    raw_body: bytes | None = None
    sleep_seconds: float = 0.0

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.requests.append((self.path, dict(self.headers), body))
        reply = self.__class__.replies.pop(0) if self.__class__.replies else {"status": "false"}
        if self.__class__.sleep_seconds:
            time.sleep(self.__class__.sleep_seconds)
        self.send_response(self.__class__.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.__class__.raw_body if self.__class__.raw_body is not None else json.dumps(reply).encode())

    def log_message(self, *_args: object) -> None:
        pass

    def handle_error(self, request: object, client_address: object) -> None:
        pass


class StreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Sunshine)
        _Sunshine.status_code = 200
        _Sunshine.requests, _Sunshine.replies = [], [{"status": "true"}]
        _Sunshine.raw_body = None
        _Sunshine.sleep_seconds = 0.0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        credentials = self.home / ".config/sunshine"
        credentials.mkdir(parents=True)
        (credentials / ".diarchy-ui").write_text("username=alice\npassword=secret\n")
        hyprctl = self.home / "fake-bin" / "hyprctl"  # a live Hyprland session, as `stream start` requires one
        hyprctl.parent.mkdir()
        hyprctl.write_text("#!/bin/sh\necho '[{\"pid\": 4242, \"instance\": \"abc\", \"wl_socket\": \"wayland-1\"}]'\n")
        hyprctl.chmod(0o755)
        self.env = {"HOME": str(self.home), "HYPRCTL_BIN": str(hyprctl), "DIARCHY_SUNSHINE_URL": f"http://127.0.0.1:{self.server.server_port}", "DIARCHY_STREAM_PAIR_TIMEOUT": "0.2", "DIARCHY_STREAM_PAIR_INTERVAL": "0.01", "DIARCHY_SUNSHINE_PROBE_ADDR": f"127.0.0.1:{self.server.server_port}", "DIARCHY_SUNSHINE_READY_TIMEOUT": "1", "DIARCHY_DISPLAY_ENV": str(self.home / "display.env")}
        self.old = dict(__import__("os").environ)
        __import__("os").environ.update(self.env)

    def tearDown(self) -> None:
        __import__("os").environ.clear(); __import__("os").environ.update(self.old)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.temp.cleanup()

    def test_pair_posts_pin_and_name_with_basic_auth_and_json_content_type(self) -> None:
        self.assertEqual(stream.handle(["pair", "--pin", "1234", "--name", "Diarchy"]), {"paired": True})
        path, headers, body = _Sunshine.requests[0]
        self.assertEqual(path, "/api/pin")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Authorization"], "Basic " + base64.b64encode(b"alice:secret").decode())
        self.assertEqual(body, b'{"pin":"1234","name":"Diarchy"}')

    def test_pair_retries_until_pending(self) -> None:
        _Sunshine.replies = [{"status": "false"}, {"status": "false"}, {"status": "true"}]
        self.assertEqual(stream.handle(["pair", "--pin", "1234", "--name", "-n"]), {"paired": True})
        self.assertEqual(len(_Sunshine.requests), 3)

    def test_pair_rejects_bad_arguments_without_request(self) -> None:
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["pair", "--pin", "123", "--name", "ok"])
        self.assertEqual(ctx.exception.code, "usage")
        self.assertEqual(_Sunshine.requests, [])

    def test_pair_false_timeout(self) -> None:
        _Sunshine.replies = []
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["pair", "--pin", "1234", "--name", "ok"])
        self.assertEqual(ctx.exception.code, "pair_failed")

    def test_pair_401_is_immediate(self) -> None:
        _Sunshine.status_code = 401
        started = time.monotonic()
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["pair", "--pin", "1234", "--name", "ok"])
        self.assertEqual(ctx.exception.code, "pair_failed")
        self.assertEqual(len(_Sunshine.requests), 1)
        self.assertLess(time.monotonic() - started, 0.15)

    def test_pair_connection_refused(self) -> None:
        self.server.shutdown()
        os.environ["DIARCHY_SUNSHINE_URL"] = "http://127.0.0.1:1"
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["pair", "--pin", "1234", "--name", "ok"])
        self.assertEqual(ctx.exception.code, "sunshine_unreachable")

    def test_bad_argument_matrix(self) -> None:
        bad = [("123", "ok"), ("12345", "ok"), ("12a4", "ok"), ("", "ok"), ("1234\n", "ok"),
               ("1234", ""), ("1234", "a" * 41), ("1234", "x;rm"), ("1234", "é")]
        for pin, name in bad:
            with self.subTest(pin=pin, name=name):
                _Sunshine.requests = []
                with self.assertRaises(stream.AgentError) as ctx:
                    stream.handle(["pair", "--pin", pin, "--name", name])
                self.assertEqual(ctx.exception.code, "usage")
                self.assertEqual(_Sunshine.requests, [])
        for args in (["pair", "--pin", "1234"], ["pair", "--pin", "1234", "--pin", "1234", "--name", "ok"],
                     ["pair", "--bogus", "x", "--name", "ok"], ["pair", "--pin", "1234", "--name", "ok", "extra"]):
            _Sunshine.requests = []
            with self.assertRaises(stream.AgentError) as ctx:
                stream.handle(args)
            self.assertEqual(ctx.exception.code, "usage")
            self.assertEqual(_Sunshine.requests, [])
    def test_pair_rejects_malformed_success_payload(self) -> None:
        for body in (b"not json", b"[]"):
            _Sunshine.raw_body = body
            with self.assertRaises(stream.AgentError) as ctx:
                stream.handle(["pair", "--pin", "1234", "--name", "ok"])
            self.assertEqual(ctx.exception.code, "pair_failed")

    def test_missing_credentials_is_not_installed(self) -> None:
        path = self.home / ".config/sunshine/.diarchy-ui"
        for content in (None, "", "username=alice\n"):
            if content is None:
                path.unlink()
            else:
                path.write_text(content)
            with self.assertRaises(stream.AgentError) as ctx:
                stream.handle(["pair", "--pin", "1234", "--name", "ok"])
            self.assertEqual(ctx.exception.code, "not_installed")
            path.write_text("username=alice\npassword=secret\n")

    def test_start_stop_status_and_installation(self) -> None:
        sunshine = self.home / ".local/bin/sunshine"
        sunshine.parent.mkdir(parents=True)
        sunshine.write_text("#!/bin/sh\n")
        sunshine.chmod(0o755)
        self._fake_display_restore()
        calls: list[tuple[str, ...]] = []
        active = {stream.common.UNIT_KVM}
        def ctl(*args: str):
            calls.append(args)
            if args[0] == "stop": active.discard(args[1])
            if args[0] == "start": active.update(args[1:])
            return mock.Mock(returncode=0, stdout="")
        with mock.patch.object(stream.common, "systemctl_user", side_effect=ctl), \
             mock.patch.object(stream.common, "unit_active", side_effect=lambda unit: unit in active), \
             mock.patch.object(stream.common, "unit_running", side_effect=lambda unit: unit in active), \
             mock.patch.object(stream, "_encoder", return_value="vaapi"):
            self.assertEqual(stream.handle(["start"])["encoder"], "vaapi")
            self.assertEqual(stream.handle(["stop"]), {"service_active": False, "encoder": None})
        self.assertEqual(calls[0], ("stop", stream.common.UNIT_KVM))
        self.assertEqual(calls[1], ("start", stream.common.UNIT_STREAM_DISPLAY, stream.common.UNIT_STREAM_SUNSHINE))
        self.assertEqual(calls[2:], [("stop", stream.common.UNIT_STREAM_SUNSHINE), ("stop", stream.common.UNIT_STREAM_DISPLAY)])
    def test_start_failures_and_status_encoder_states(self) -> None:
        sunshine = self.home / ".local/bin/sunshine"
        sunshine.parent.mkdir(parents=True)
        sunshine.write_text("#!/bin/sh\n")
        sunshine.chmod(0o755)
        with mock.patch.object(stream.common, "systemctl_user"), mock.patch.object(stream.common, "unit_active", return_value=False):
            with self.assertRaises(stream.AgentError) as ctx:
                stream.handle(["start"])
            self.assertEqual(ctx.exception.code, "service_failed")
        sunshine.chmod(0o644)
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["start"])
        self.assertEqual(ctx.exception.code, "not_installed")
        for output in ("nvenc\n", "vaapi\n", "junk\n"):
            with self.subTest(output=output), mock.patch.object(stream.common, "unit_active", return_value=True), mock.patch.object(stream.common, "run", return_value=mock.Mock(returncode=0, stdout=output)):
                self.assertEqual(stream.handle(["status"])["encoder"], output.strip() if output.strip() in ("nvenc", "vaapi") else "unknown")
        with mock.patch.object(stream.common, "unit_active", return_value=False):
            self.assertEqual(stream.handle(["status"]), {"service_active": False, "encoder": None})
    def test_active_status_and_start_report_unknown_for_checker_failures(self) -> None:
        sunshine = self.home / ".local/bin/sunshine"
        sunshine.parent.mkdir(parents=True)
        sunshine.write_text("#!/bin/sh\n")
        sunshine.chmod(0o755)
        systemctl = self.home / "systemctl"
        systemctl.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(0)\n")
        systemctl.chmod(0o755)
        os.environ["SYSTEMCTL_BIN"] = str(systemctl)
        for output, code in (("junk\n", 0), ("", 0), ("nvenc\n", 1)):
            checker = self.home / "checker"
            checker.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({output!r})\nsys.exit({code})\n")
            checker.chmod(0o755)
            os.environ["DIARCHY_SUNSHINE_CHECK_BIN"] = str(checker)
            with self.subTest(output=output, code=code):
                self.assertEqual(stream.handle(["status"]), {"service_active": True, "encoder": "unknown"})
                self.assertEqual(stream.handle(["start"]), {"service_active": True, "encoder": "unknown"})

    def _free_port(self) -> int:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    def _install_sunshine(self) -> None:
        sunshine = self.home / ".local/bin/sunshine"
        sunshine.parent.mkdir(parents=True)
        sunshine.write_text("#!/bin/sh\n")
        sunshine.chmod(0o755)

    def _fake_units(self, active: set[str], failing_stop: frozenset[str] = frozenset(),
                    stuck: frozenset[str] = frozenset()) -> list[tuple[str, ...]]:
        """Stateful systemctl fake. failing_stop: `stop` exits 1; stuck: `stop` exits 0 but the unit stays running."""
        calls: list[tuple[str, ...]] = []

        def ctl(*args: str):
            calls.append(args)
            if args[0] == "stop":
                if args[1] in failing_stop:
                    return mock.Mock(returncode=1, stdout="", stderr="Access denied")
                if args[1] not in stuck:
                    active.discard(args[1])
            if args[0] == "start":
                active.update(args[1:])
            return mock.Mock(returncode=0, stdout="", stderr="")

        for target, side_effect in (("systemctl_user", ctl),
                                    ("unit_active", lambda unit: unit in active),
                                    ("unit_running", lambda unit: unit in active)):
            patcher = mock.patch.object(stream.common, target, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(stream, "_encoder", return_value="vaapi")
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_start_waits_for_a_listener_that_appears_late(self) -> None:
        self._install_sunshine()
        port = self._free_port()
        os.environ["DIARCHY_SUNSHINE_PROBE_ADDR"] = f"127.0.0.1:{port}"
        os.environ["DIARCHY_SUNSHINE_READY_TIMEOUT"] = "5"
        self._fake_units(set())
        listeners: list[socket.socket] = []

        def listen_late() -> None:
            time.sleep(0.5)
            late = socket.socket()
            late.bind(("127.0.0.1", port))
            late.listen()
            listeners.append(late)

        thread = threading.Thread(target=listen_late)
        thread.start()
        self.addCleanup(lambda: [item.close() for item in listeners])
        self.addCleanup(thread.join)
        started = time.monotonic()
        self.assertEqual(stream.handle(["start"]), {"service_active": True, "encoder": "vaapi"})
        self.assertGreaterEqual(time.monotonic() - started, 0.4)

    def test_start_fails_when_sunshine_never_listens(self) -> None:
        self._install_sunshine()
        os.environ["DIARCHY_SUNSHINE_PROBE_ADDR"] = f"127.0.0.1:{self._free_port()}"
        os.environ["DIARCHY_SUNSHINE_READY_TIMEOUT"] = "0.5"
        self._fake_units(set())
        started = time.monotonic()
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["start"])
        self.assertEqual(ctx.exception.code, "service_failed")
        self.assertEqual(ctx.exception.message, "Sunshine did not start listening")
        self.assertLess(time.monotonic() - started, 3.0)

    def test_start_rejects_a_malformed_ready_timeout(self) -> None:
        self._install_sunshine()
        os.environ["DIARCHY_SUNSHINE_READY_TIMEOUT"] = "soon"
        self._fake_units(set())
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["start"])
        self.assertEqual(ctx.exception.code, "usage")

    def test_start_does_not_start_streaming_when_kvm_stop_fails(self) -> None:
        self._install_sunshine()
        calls = self._fake_units({stream.common.UNIT_KVM}, failing_stop=frozenset({stream.common.UNIT_KVM}))
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["start"])
        self.assertEqual(ctx.exception.code, "service_failed")
        self.assertFalse([call for call in calls if call[0] == "start"])

    def test_start_does_not_start_streaming_when_kvm_stays_running_after_stop(self) -> None:
        self._install_sunshine()
        calls = self._fake_units({stream.common.UNIT_KVM}, stuck=frozenset({stream.common.UNIT_KVM}))
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["start"])
        self.assertEqual(ctx.exception.code, "service_failed")
        self.assertIn("still active", ctx.exception.message)
        self.assertFalse([call for call in calls if call[0] == "start"])

    # --- bughunt B-23: systemctl start results, half-started units and the previous mode ------------------------

    def _failing_start(self, active: set[str], unit_left_up: str | None) -> tuple[list[tuple[str, ...]], list[str]]:
        """`systemctl start` of the stream units exits 1 with stderr. `unit_left_up` stays active (partial start).
        Returns the systemctl calls and the `diarchy-display` arguments seen."""
        self._install_sunshine()
        calls = self._fake_units(active)
        original = stream.common.systemctl_user

        def ctl(*args: str):
            if args[0] == "start" and stream.common.UNIT_STREAM_SUNSHINE in args:
                calls.append(args)
                if unit_left_up:
                    active.add(unit_left_up)
                return mock.Mock(returncode=1, stdout="", stderr="Job for diarchy-sunshine.service failed.")
            return original(*args)

        patcher = mock.patch.object(stream.common, "systemctl_user", side_effect=ctl)
        patcher.start()
        self.addCleanup(patcher.stop)
        seen = self.home / "display-args"
        display = self.home / "display"
        display.write_text(f"#!{sys.executable}\nimport sys\nopen({str(seen)!r}, 'a').write(sys.argv[1] + '\\n')\n"
                           "print('eDP-1')\n")
        display.chmod(0o755)
        os.environ["DIARCHY_DISPLAY_BIN"] = str(display)
        session = mock.patch.object(stream.common, "hyprland_session_present", return_value=True)
        session.start()
        self.addCleanup(session.stop)
        require = mock.patch.object(stream.common, "require_hyprland_session", return_value=None)
        require.start()
        self.addCleanup(require.stop)
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["start"])
        self.assertEqual(ctx.exception.code, "service_failed")
        self.assertIn("Job for diarchy-sunshine.service failed.", ctx.exception.message)
        self._active = active
        return calls, seen.read_text().split() if seen.exists() else []

    def test_start_failure_reports_systemctl_stderr_and_stops_half_started_units(self) -> None:
        calls, display_args = self._failing_start(set(), stream.common.UNIT_STREAM_DISPLAY)
        self.assertNotIn(stream.common.UNIT_STREAM_DISPLAY, self._active)
        self.assertNotIn(stream.common.UNIT_STREAM_SUNSHINE, self._active)
        self.assertEqual(display_args[-1], "restore")
        self.assertNotIn(("start", stream.common.UNIT_KVM), calls)

    def test_start_failure_starts_kvm_again_when_kvm_was_running(self) -> None:
        calls, _ = self._failing_start({stream.common.UNIT_KVM}, None)
        kvm_start = calls.index(("start", stream.common.UNIT_KVM))
        self.assertGreater(kvm_start, calls.index(("stop", stream.common.UNIT_KVM)))
        self.assertIn(stream.common.UNIT_KVM, self._active)

    # --- capture GPU: Sunshine encodes on the GPU that renders the captured output --------------------------------

    HEADER = "# Managed by diarchy. Credentials and pairing state live outside this file."

    def _gpu_setup(self, display_output: str | None, *, conf: str | None = None) -> tuple[Path, list[str]]:
        """Hybrid host: Intel (card0, renderD129, eDP-1) + NVIDIA (card1, renderD128, boot_vga). Returns the conf path
        and the list of conf texts seen at each `systemctl start` (the moment Sunshine would read it)."""
        from tests.test_diarchy_host_gpu import FakeSysfs
        sysfs = FakeSysfs(self.home / "sys")
        sysfs.gpu("card0", "i915", "renderD129", boot_vga=False, connectors=("eDP-1",))
        sysfs.gpu("card1", "nvidia", "renderD128", boot_vga=True)
        os.environ["DIARCHY_SYSFS_ROOT"] = str(sysfs.root)
        display = self.home / "display"
        body = f"print({display_output!r})\n" if display_output else "import sys\nsys.exit(1)\n"
        display.write_text(f"#!{sys.executable}\nimport sys\nassert sys.argv[1:] == ['reconcile']\n{body}")
        display.chmod(0o755)
        os.environ["DIARCHY_DISPLAY_BIN"] = str(display)
        conf_path = self.home / ".config/sunshine/sunshine.conf"
        conf_path.write_text(conf if conf is not None else f"{self.HEADER}\nlocale = en\n")
        self._install_sunshine()
        seen: list[str] = []
        started = self._fake_units(set())
        original = stream.common.systemctl_user

        def ctl(*args: str):
            if args[0] == "start":
                seen.append(conf_path.read_text() if conf_path.exists() else "")
            return original(*args)

        patcher = mock.patch.object(stream.common, "systemctl_user", side_effect=ctl)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._started = started
        return conf_path, seen

    def test_start_pins_adapter_and_encoder_to_the_gpu_of_the_selected_output_before_starting_units(self) -> None:
        conf, seen = self._gpu_setup("eDP-1")
        self.assertEqual(stream.handle(["start"])["service_active"], True)
        expected = f"{self.HEADER}\nlocale = en\nadapter_name = /dev/dri/renderD129\nencoder = vaapi\n"
        self.assertEqual(seen, [expected])
        self.assertEqual(conf.read_text(), expected)

    def test_start_uses_the_boot_vga_gpu_when_the_headless_output_is_selected(self) -> None:
        conf, seen = self._gpu_setup("DIARCHY")
        stream.handle(["start"])
        self.assertIn("adapter_name = /dev/dri/renderD128\nencoder = nvenc\n", seen[0])

    def _fake_hyprctl_monitors(self, monitors: list[dict]) -> None:
        hyprctl = self.home / "fake-bin" / "hyprctl"
        hyprctl.write_text(
            f"#!{sys.executable}\nimport sys\n"
            "if sys.argv[1:] == ['-j', 'monitors', 'all']:\n"
            f"    print({json.dumps(monitors)!r})\n"
            "else:\n"
            "    print('[{\"pid\": 4242, \"instance\": \"abc\", \"wl_socket\": \"wayland-1\"}]')\n"
        )

    def test_start_saves_the_requested_physical_output_for_the_display_script(self) -> None:
        self._gpu_setup("DP-1")
        for requested in ("DP-1", "HDMI-A-1"):
            with self.subTest(requested=requested):
                stream.handle(["start", "--output", requested])
                self.assertEqual((self.home / "display.env").read_text(), f"PHYSICAL_OUTPUT={requested}\n")

    def test_start_saves_auto_as_auto_without_resolving_the_monitors(self) -> None:
        self._gpu_setup("DP-2")
        self._fake_hyprctl_monitors([{"name": "DP-1", "disabled": False, "focused": False},
                                     {"name": "DP-2", "disabled": False, "focused": True}])
        stream.handle(["start"])
        self.assertEqual((self.home / "display.env").read_text(), "PHYSICAL_OUTPUT=auto\n")
        self._fake_hyprctl_monitors([{"name": "DP-2", "focused": True}, {"name": "eDP-1"}])
        stream.handle(["start", "--output", "auto"])
        self.assertEqual((self.home / "display.env").read_text(), "PHYSICAL_OUTPUT=auto\n")
        stream.handle(["start", "--output", "DP-1"])
        self.assertEqual((self.home / "display.env").read_text(), "PHYSICAL_OUTPUT=DP-1\n")

    def test_start_rejects_bad_output_before_touching_any_unit(self) -> None:
        self._gpu_setup("DP-1")
        for args in (["start", "--output", "DP 1"], ["start", "--output"], ["start", "DP-1"], ["start", "--output", "a", "b"]):
            with self.subTest(args=args):
                with self.assertRaises(stream.AgentError) as ctx:
                    stream.handle(args)
                self.assertEqual(ctx.exception.code, "usage")
        self.assertEqual(self._started, [])
        self.assertFalse((self.home / "display.env").exists())

    def test_changing_the_output_restarts_running_display_and_sunshine(self) -> None:
        self._install_sunshine()
        active = {stream.common.UNIT_STREAM_DISPLAY, stream.common.UNIT_STREAM_SUNSHINE}
        calls = self._fake_units(active)
        stream.handle(["start", "--output", "DP-1"])
        stream.handle(["start", "--output", "DP-1"])
        stream.handle(["start", "--output", "HDMI-A-1"])
        restarts = [c for c in calls if c[0] == "restart"]
        self.assertEqual(restarts, [("restart", stream.common.UNIT_STREAM_DISPLAY, stream.common.UNIT_STREAM_SUNSHINE)] * 2)

    def test_configure_saves_the_output_without_starting_anything(self) -> None:
        self._gpu_setup("DP-1")
        stream.handle(["configure", "--output", "DP-1"])
        self.assertEqual((self.home / "display.env").read_text(), "PHYSICAL_OUTPUT=DP-1\n")
        self.assertEqual(self._started, [])

    def test_start_still_reconciles_the_conf_when_the_display_script_fails(self) -> None:
        conf, seen = self._gpu_setup(None)
        stream.handle(["start"])
        self.assertIn("adapter_name = /dev/dri/renderD128\nencoder = nvenc\n", seen[0])

    def test_start_replaces_a_stale_pin_when_the_output_moves_to_another_gpu(self) -> None:
        stale = f"{self.HEADER}\nadapter_name = /dev/dri/renderD128\nlocale = en\nencoder = nvenc\n"
        conf, seen = self._gpu_setup("eDP-1", conf=stale)
        stream.handle(["start"])
        self.assertEqual(
            conf.read_text(), f"{self.HEADER}\nadapter_name = /dev/dri/renderD129\nlocale = en\nencoder = vaapi\n")

    def test_start_without_a_sunshine_conf_still_starts(self) -> None:
        conf, seen = self._gpu_setup("eDP-1")
        conf.unlink()
        self.assertEqual(stream.handle(["start"])["service_active"], True)
        self.assertEqual(seen, [""])
        self.assertFalse(conf.exists())

    def test_start_fails_with_config_write_failed_and_starts_nothing_when_the_conf_cannot_be_rewritten(self) -> None:
        conf, seen = self._gpu_setup("eDP-1")
        conf.unlink()
        conf.mkdir()  # reading it as a file fails
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["start"])
        self.assertEqual(ctx.exception.code, "config_write_failed")
        self.assertEqual(seen, [])
        self.assertFalse([call for call in self._started if call[0] == "start"])

    # `stream configure`: the pin alone, for the installer, so the encoder it validates is the one `start` will use.

    def test_configure_pins_the_conf_without_touching_any_unit(self) -> None:
        conf, seen = self._gpu_setup("eDP-1")
        self.assertEqual(stream.handle(["configure"]), {"adapter_name": "/dev/dri/renderD129", "encoder": "vaapi"})
        self.assertEqual(conf.read_text(),
                         f"{self.HEADER}\nlocale = en\nadapter_name = /dev/dri/renderD129\nencoder = vaapi\n")
        self.assertEqual(self._started, [])  # no systemctl call at all

    def test_configure_reports_nothing_pinned_when_no_gpu_resolves(self) -> None:
        conf, seen = self._gpu_setup("eDP-1", conf=f"{self.HEADER}\nencoder = nvenc\n")
        os.environ["DIARCHY_SYSFS_ROOT"] = str(self.home / "no-sysfs")
        self.assertEqual(stream.handle(["configure"]), {"adapter_name": None, "encoder": None})
        self.assertEqual(conf.read_text(), f"{self.HEADER}\n")

    def test_configure_fails_with_config_write_failed_when_the_conf_cannot_be_rewritten(self) -> None:
        conf, seen = self._gpu_setup("eDP-1")
        conf.unlink()
        conf.mkdir()
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["configure"])
        self.assertEqual(ctx.exception.code, "config_write_failed")

    def test_configure_takes_no_arguments(self) -> None:
        self._gpu_setup("eDP-1")
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["configure", "extra"])
        self.assertEqual(ctx.exception.code, "usage")

    def test_stop_reports_a_failed_stop_after_attempting_both_units(self) -> None:
        sunshine_unit, display_unit = stream.common.UNIT_STREAM_SUNSHINE, stream.common.UNIT_STREAM_DISPLAY
        calls = self._fake_units({sunshine_unit, display_unit}, failing_stop=frozenset({sunshine_unit}))
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["stop"])
        self.assertEqual(ctx.exception.code, "service_failed")
        self.assertEqual([call for call in calls if call[0] == "stop"],
                         [("stop", sunshine_unit), ("stop", display_unit)])

    def test_stop_reports_a_unit_that_stays_running(self) -> None:
        sunshine_unit, display_unit = stream.common.UNIT_STREAM_SUNSHINE, stream.common.UNIT_STREAM_DISPLAY
        self._fake_units({sunshine_unit, display_unit}, stuck=frozenset({display_unit}))
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["stop"])
        self.assertEqual(ctx.exception.code, "service_failed")
        self.assertIn(display_unit, ctx.exception.message)

    def _fake_display_restore(self, *, exit_code: int = 0, stderr: str = "") -> Path:
        log = self.home / "display-calls.log"
        display = self.home / "display-restore"
        display.write_text(f"#!{sys.executable}\nimport sys\n"
                           f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                           f"sys.stderr.write({stderr!r})\nsys.exit({exit_code})\n")
        display.chmod(0o755)
        os.environ["DIARCHY_DISPLAY_BIN"] = str(display)
        return log

    def test_stop_restores_the_display_once_after_both_units_are_down(self) -> None:
        sunshine_unit, display_unit = stream.common.UNIT_STREAM_SUNSHINE, stream.common.UNIT_STREAM_DISPLAY
        active = {sunshine_unit, display_unit}
        log = self._fake_display_restore()
        self._fake_units(active)
        seen: list[set[str]] = []
        real_run = stream.common.run

        def run(argv, *args, **kwargs):
            if argv[0] == os.environ["DIARCHY_DISPLAY_BIN"]:
                seen.append(set(active))
            return real_run(argv, *args, **kwargs)

        with mock.patch.object(stream.common, "run", side_effect=run):
            self.assertEqual(stream.handle(["stop"]), {"service_active": False, "encoder": None})
        self.assertEqual(log.read_text().splitlines(), ["restore"])
        self.assertEqual(seen, [set()])

    def test_stop_with_a_failing_restore_is_service_failed_with_the_stderr_text(self) -> None:
        self._fake_display_restore(exit_code=1, stderr="diarchy-display: hyprctl failed")
        self._fake_units({stream.common.UNIT_STREAM_SUNSHINE, stream.common.UNIT_STREAM_DISPLAY})
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["stop"])
        self.assertEqual(ctx.exception.code, "service_failed")
        self.assertIn("diarchy-display: hyprctl failed", ctx.exception.message)

    def test_stop_without_a_hyprland_session_does_not_restore(self) -> None:
        log = self._fake_display_restore()
        self._fake_units({stream.common.UNIT_STREAM_SUNSHINE, stream.common.UNIT_STREAM_DISPLAY})
        Path(os.environ["HYPRCTL_BIN"]).write_text("#!/bin/sh\necho '[]'\n")
        self.assertEqual(stream.handle(["stop"]), {"service_active": False, "encoder": None})
        self.assertFalse(log.exists())

    def test_pair_hanging_server_is_bounded_by_deadline(self) -> None:
        _Sunshine.sleep_seconds = 1.0
        started = time.monotonic()
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["pair", "--pin", "1234", "--name", "ok"])
        self.assertEqual(ctx.exception.code, "sunshine_unreachable")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_unknown_subcommand_is_usage_error(self) -> None:
        with self.assertRaises(stream.AgentError) as ctx:
            stream.handle(["wat"])
        self.assertEqual(ctx.exception.code, "usage")
class StreamCliTests(AgentCase):
    def setUp(self) -> None:
        super().setUp()
        self.fake_hyprctl()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Sunshine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close_server)
        credentials = self.home / ".config/sunshine"
        credentials.mkdir(parents=True)
        (credentials / ".diarchy-ui").write_text("username=alice\npassword=pw-S3cr3t-!x\n")
        _Sunshine.status_code = 200
        _Sunshine.raw_body = None
        _Sunshine.sleep_seconds = 0.0
        _Sunshine.requests = []
        _Sunshine.replies = [{"status": "true"}]
        self.env.update({
            "DIARCHY_SUNSHINE_URL": f"http://127.0.0.1:{self.server.server_port}",
            "DIARCHY_STREAM_PAIR_TIMEOUT": "0.2",
            "DIARCHY_STREAM_PAIR_INTERVAL": "0.01",
            "DIARCHY_SUNSHINE_PROBE_ADDR": f"127.0.0.1:{self.server.server_port}",
            "DIARCHY_SUNSHINE_READY_TIMEOUT": "2",
        })

    def _close_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def test_password_is_absent_from_real_launcher_tools_and_failure_envelopes(self) -> None:
        password = "pw-S3cr3t-!x"
        sunshine = self.home / ".local/bin/sunshine"
        sunshine.parent.mkdir(parents=True)
        sunshine.write_text("#!/bin/sh\n")
        sunshine.chmod(0o755)
        logger = (
            "import json, os, sys\n"
            "entry = {'argv': sys.argv, 'stdin': sys.stdin.read()}\n"
            "with open(os.environ['FAKE_LOG'], 'a') as f: f.write(json.dumps(entry) + '\\n')\n"
        )
        self.tool("systemctl", logger + "sys.exit(0)\n", "SYSTEMCTL_BIN")
        self.tool("checker", logger + "print('nvenc')\n", "DIARCHY_SUNSHINE_CHECK_BIN")
        outputs: list[str] = []
        result, envelope = self.agent("stream", "pair", "--pin", "1234", "--name", "Diarchy")
        outputs.append(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(envelope["ok"])
        result, envelope = self.agent("stream", "start")
        outputs.append(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(envelope["ok"])
        result, envelope = self.agent("stream", "status")
        outputs.append(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(envelope["ok"])
        logged = self.log.read_text()
        self.assertNotIn(password, logged)
        self.assertNotIn(password, "".join(outputs))

        for status_code in (401, 403):
            _Sunshine.status_code = status_code
            _Sunshine.requests = []
            _Sunshine.replies = [{"status": "false"}]
            result, envelope = self.agent("stream", "pair", "--pin", "1234", "--name", "Diarchy")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(envelope["ok"])
            self.assertNotIn(password, result.stdout + result.stderr + repr(envelope))





class StreamKvmExclusivityTests(AgentCase):
    def setUp(self) -> None:
        super().setUp()
        self.fake_hyprctl()

    def test_start_stops_kvm_that_is_activating_or_auto_restarting(self) -> None:
        sunshine = self.home / ".local/bin/sunshine"
        sunshine.parent.mkdir(parents=True)
        sunshine.write_text("#!/bin/sh\n")
        sunshine.chmod(0o755)
        state = self.root / "state.json"
        state.write_text(json.dumps({"diarchy-kvm.service": "activating"}))
        self.tool("systemctl", (
            "import json, os, sys\n"
            f"state_path = {str(state)!r}\n"
            "state = json.load(open(state_path))\n"
            "args = sys.argv[2:]\n"
            "with open(os.environ['FAKE_LOG'], 'a') as f: f.write(' '.join(args) + '\\n')\n"
            "if args[0] == 'show':\n"
            "    print(state.get(args[-1], 'inactive')); sys.exit(0)\n"
            "if args[0] == 'is-active':\n"
            "    sys.exit(0 if state.get(args[-1]) == 'active' else 3)\n"
            "if args[0] == 'stop':\n"
            "    for unit in args[1:]: state[unit] = 'inactive'\n"
            "if args[0] == 'start':\n"
            "    for unit in args[1:]: state[unit] = 'active'\n"
            "json.dump(state, open(state_path, 'w'))\n"
        ), "SYSTEMCTL_BIN")
        self.tool("checker", "print('vaapi')\n", "DIARCHY_SUNSHINE_CHECK_BIN")
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result, envelope = self.agent(
            "stream", "start", env={"DIARCHY_SUNSHINE_PROBE_ADDR": f"127.0.0.1:{listener.getsockname()[1]}"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        stop = calls.index("stop diarchy-kvm.service")
        start = calls.index("start diarchy-display.service diarchy-sunshine.service")
        self.assertLess(stop, start)


class StreamNoSessionTests(AgentCase):
    def test_start_without_a_hyprland_session_is_no_session_and_starts_or_stops_nothing(self) -> None:
        sunshine = self.home / ".local/bin/sunshine"
        sunshine.parent.mkdir(parents=True)
        sunshine.write_text("#!/bin/sh\n")
        sunshine.chmod(0o755)
        self.fake_systemctl(active=["diarchy-kvm.service"])
        self.fake_hyprctl("[]")
        result, envelope = self.agent("stream", "start")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(envelope["command"], "stream start")
        self.assertEqual(envelope["error"], {"code": "no_session",
                                             "message": "no Hyprland session is running for this user"})
        self.assertEqual(self.calls(), [], "no unit may be started, stopped or probed")

    def test_start_without_sunshine_is_still_not_installed_when_no_session_is_running(self) -> None:
        self.fake_systemctl()
        self.fake_hyprctl("[]")
        result, envelope = self.agent("stream", "start")
        self.assertEqual(envelope["error"]["code"], "not_installed")



if __name__ == "__main__":
    unittest.main()
