from __future__ import annotations

import io
import json
import os
import runpy
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = ROOT / "host" / "diarchy-clipboard-remote"


class FakeStdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()


class WaylandRediscoveryTests(unittest.TestCase):
    """B-06: the pump finds the Wayland socket again after a Hyprland restart and tells the Mac when it is blind."""

    def setUp(self) -> None:
        loaded = runpy.run_path(str(REMOTE))
        # run_path returns a copy of the module namespace: patch the namespace the functions really read.
        self.remote = loaded["_wl_paste_to_stdout"].__globals__
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))

    def fake_tool(self, name: str, body: str) -> None:
        path = self.temp / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        if str(self.temp) not in os.environ["PATH"].split(os.pathsep):
            os.environ["PATH"] = f"{self.temp}{os.pathsep}{os.environ['PATH']}"

    def fake_hyprctl(self, socket: str) -> None:
        instances = json.dumps([{"instance": "sig", "pid": 4242, "wl_socket": socket, "time": 1}])
        self.fake_tool("hyprctl", f"print({instances!r})\n")

    def test_discovery_can_ignore_a_stale_wayland_display(self) -> None:
        discover = self.remote["discover_wayland_environment"]
        instances = json.dumps([{"instance": "sig", "pid": 4242, "wl_socket": "wayland-2", "time": 1}]).encode()
        fake = lambda *args, **kwargs: instances  # noqa: E731
        self.assertEqual(discover({"WAYLAND_DISPLAY": "wayland-1"}, fake)["WAYLAND_DISPLAY"], "wayland-1")
        self.assertEqual(discover({"WAYLAND_DISPLAY": "wayland-1"}, fake, refresh=True)["WAYLAND_DISPLAY"], "wayland-2")

    def test_failed_rediscovery_keeps_the_known_socket(self) -> None:
        def broken(*args, **kwargs):
            raise FileNotFoundError("hyprctl")

        discovered = self.remote["discover_wayland_environment"]({"WAYLAND_DISPLAY": "wayland-1"}, broken, refresh=True)
        self.assertEqual(discovered["WAYLAND_DISPLAY"], "wayland-1")

    def run_paste_loop(self, failures: int, alive: bool = False) -> tuple[bytes, int]:
        remote = self.remote
        stop = threading.Event()
        calls = {"read": 0, "refresh": 0}

        def read() -> bytes | None:
            calls["read"] += 1
            if calls["read"] > failures:
                stop.set()
                return None
            return None

        def refresh() -> bool:
            calls["refresh"] += 1
            return False

        remote["read_host_clipboard_raw"] = read
        remote["refresh_wayland_environment"] = refresh
        remote["wayland_socket_alive"] = lambda: alive
        fake_out = FakeStdout()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = fake_out, io.StringIO()
        try:
            remote["_wl_paste_to_stdout"](stop, 1, remote["ClipboardState"](), threading.Lock())
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        return fake_out.buffer.getvalue(), calls["refresh"]

    def test_repeated_read_failures_trigger_rediscovery_and_one_visible_line(self) -> None:
        threshold = self.remote["WAYLAND_RETRY_AFTER"]
        out, refreshes = self.run_paste_loop(failures=threshold - 2)
        self.assertEqual((out, refreshes), (b"", 0))
        out, refreshes = self.run_paste_loop(failures=threshold * 2 + 1)
        self.assertEqual(refreshes, 2)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1, out)
        self.assertFalse(lines[0].startswith(b"CLIP "))
        self.assertNotIn(b"CLIP ", lines[0])
        self.assertIn(b"clipboard", lines[0])

    def test_a_live_socket_with_an_empty_clipboard_is_not_a_failure(self) -> None:
        threshold = self.remote["WAYLAND_RETRY_AFTER"]
        out, refreshes = self.run_paste_loop(failures=threshold * 3, alive=True)
        self.assertEqual((out, refreshes), (b"", 0))

    def test_a_new_failure_streak_after_a_good_read_is_reported_again(self) -> None:
        threshold = self.remote["WAYLAND_RETRY_AFTER"]
        remote = self.remote
        sequence = [None] * threshold + [b"ok"] + [None] * threshold
        stop = threading.Event()

        def read() -> bytes | None:
            value = sequence.pop(0)
            if not sequence:
                stop.set()
            return value

        remote["read_host_clipboard_raw"] = read
        remote["refresh_wayland_environment"] = lambda: False
        remote["wayland_socket_alive"] = lambda: False
        fake_out = FakeStdout()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = fake_out, io.StringIO()
        try:
            remote["_wl_paste_to_stdout"](stop, 1, remote["ClipboardState"](), threading.Lock())
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        data = fake_out.buffer.getvalue()
        self.assertEqual(data.count(b"unavailable"), 2, data)
        self.assertEqual(data.count(b"CLIP "), 1, data)

    def test_incoming_text_is_copied_with_the_socket_of_the_new_session(self) -> None:
        self.fake_hyprctl("wayland-2")
        log = self.temp / "wl-copy.log"
        self.fake_tool(
            "wl-copy",
            "import os, sys\n"
            f"open({str(log)!r}, 'a').write(os.environ.get('WAYLAND_DISPLAY', '') + '\\n')\n"
            "sys.stdin.buffer.read()\n",
        )
        self.fake_tool("wl-paste", "import sys\nsys.exit(1)\n")
        os.environ["WAYLAND_DISPLAY"] = "wayland-1"

        remote = self.remote
        payload = b"hello"
        stdin = io.BytesIO(remote["encode_frame"](payload))
        stop = threading.Event()
        owner = remote["WlCopyOwner"]()
        real_in, real_err = sys.stdin, sys.stderr
        sys.stdin, sys.stderr = type("In", (), {"buffer": stdin})(), io.StringIO()
        try:
            remote["_stdin_to_wl_copy"](stop, owner, remote["ClipboardState"](), threading.Lock())
            deadline = time.time() + 5
            while not log.exists() and time.time() < deadline:
                time.sleep(0.02)
        finally:
            sys.stdin, sys.stderr = real_in, real_err
            owner.shutdown()
        self.assertEqual(log.read_text().split(), ["wayland-2"])


if __name__ == "__main__":
    unittest.main()
