from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import stat
import sys
import tempfile
import textwrap
import time
import types
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client" / "diarchy-clipboard-bridge"
REMOTE = ROOT / "host" / "diarchy-clipboard-remote"


def load_script(path: Path) -> types.ModuleType:
    source = path.read_text()
    marker = "# PYTHON_MODULE_START\n"
    if marker in source:
        source = source.split(marker, 1)[1]
    module = types.ModuleType(path.name.replace("-", "_"))
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def run_pump(script: Path, env: dict[str, str], stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(script), "--poll-ms", "1"],
        input=stdin,
        env=os.environ | env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class FakeProcess:
    def __init__(
        self,
        pid: int = 1234,
        args: tuple[str, ...] = ("wl-copy", "--foreground", "--type", "text/plain"),
        events: list[str] | None = None,
        name: str = "child",
    ) -> None:
        self.pid = pid
        self.args = args
        self.terminated = False
        self.waited = False
        self.killed = False
        self.stdin = io.BytesIO()
        self.events = events
        self.name = name

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True
        if self.events is not None:
            self.events.append(f"terminate:{self.name}")

    def kill(self) -> None:
        self.killed = True
        if self.events is not None:
            self.events.append(f"kill:{self.name}")

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        if self.events is not None:
            self.events.append(f"wait:{self.name}:{timeout}")
        return 0


class ClipboardBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = load_script(CLIENT)
        self.remote = load_script(REMOTE)
        self.client.local_clipboard_is_sensitive = lambda env=None, timeout=2.0: False
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmpdir = Path(self.temp.name)
        self.runtime_dir = self.tmpdir / f"diarchy-clipboard-bridge-{os.getuid()}"
        self.bin_dir = self.tmpdir / "bin"
        self.bin_dir.mkdir()
        self.diarchy_config = self.tmpdir / "diarchy.conf"
        self.diarchy_config.write_text(
            "# Managed by Diarchy\n"
            "DIARCHY_SSH_USER=labuser\n"
            "DIARCHY_LAN_HOST=192.0.2.10\n"
            "DIARCHY_TAILSCALE_HOST=100.64.0.10\n"
            "DIARCHY_HOST_KEY_ALIAS=diarchy-test-host\n"
        )
        self.runtime_env = {
            "TMPDIR": str(self.tmpdir),
            "PATH": str(self.bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "DIARCHY_CLIPBOARD_RUNTIME_ONCE": "1",
            "DIARCHY_CONFIG": str(self.diarchy_config),
        }

    def write_executable(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
        return path

    def fake_route_env(self, ssh: Path) -> dict[str, str]:
        return {
            "DIARCHY_CLIPBOARD_ROUTE_SH": str(CLIENT.parent / "diarchy-route.sh"),
            "SSH_BIN": str(ssh),
            "PING_BIN": str(self.write_executable("ping", "#!/bin/sh\nprintf '4 packets transmitted, 4 packets received, 0.0%% packet loss\\n64 bytes from x: icmp_seq=1 ttl=64 time=1.0 ms\\n'\n")),
            "ROUTE_BIN": str(self.write_executable("route", "#!/bin/sh\nprintf ' interface: en0\\n'\n")),
            "CONNECT_BIN": str(self.write_executable("nc", "#!/bin/sh\nexit 0\n")),
        }

    def test_frame_boundary_and_hash_round_trip(self) -> None:
        payload = b"line one\nline two"
        frame = self.client.encode_frame(payload)
        self.assertEqual(self.client.decode_frame(io.BytesIO(frame)), payload)

    def test_exact_102400_byte_utf8_boundary_is_accepted(self) -> None:
        payload = "x".encode() * 102400
        self.assertEqual(self.client.frame_for_local_change(payload, None), self.client.encode_frame(payload))

    def test_102401_bytes_is_dropped_without_emission(self) -> None:
        self.assertIsNone(self.client.frame_for_local_change(b"x" * 102401, None))


    def test_empty_clipboard_is_not_emitted_as_a_wire_change(self) -> None:
        self.assertIsNone(self.client.frame_for_local_change(b"", None))
        self.assertEqual(self.client.local_change_frame_and_digest(b"", None), (None, None))
        self.assertIsNone(self.remote.frame_for_local_change(b"", None))
    def test_malformed_and_hash_mismatch_frames_fail_safely(self) -> None:
        self.assertIsNone(self.client.decode_frame(io.BytesIO(b"NOPE 0\n")))
        digest = hashlib.sha256(b"actual").hexdigest().encode("ascii")
        self.assertIsNone(self.client.decode_frame(io.BytesIO(b"CLIP " + digest + b" 5\nwrong")))
        self.assertIsNone(self.client.decode_frame(io.BytesIO(b"CLIP " + b"0" * 64 + b" 102401\n" + b"x" * 102401)))

    def test_decoder_resynchronizes_after_invalid_header(self) -> None:
        payload = b"after-invalid-header"
        stream = io.BytesIO(b"NOPE 0\n" + self.client.encode_frame(payload))
        diagnostics: list[str] = []

        self.assertEqual(
            self.client.decode_frame(stream, diagnostics.append, "remote"),
            payload,
        )
        self.assertEqual(len(diagnostics), 1)
        self.assertTrue(diagnostics[0].startswith("dropped:decode direction=remote"))

    def test_decoder_drops_only_hash_mismatch_frame(self) -> None:
        bad_digest = hashlib.sha256(b"actual").hexdigest().encode("ascii")
        bad_frame = b"CLIP " + bad_digest + b" 5\nwrong"
        payload = b"after-hash-mismatch"
        diagnostics: list[str] = []

        self.assertEqual(
            self.remote.decode_frame(
                io.BytesIO(bad_frame + self.remote.encode_frame(payload)),
                diagnostics.append,
                "local",
            ),
            payload,
        )
        self.assertEqual(len(diagnostics), 1)
        self.assertTrue(diagnostics[0].startswith("dropped:decode direction=local"))

    def test_empty_incoming_frame_clears_only_nonempty_text(self) -> None:
        for module in (self.client, self.remote):
            with self.subTest(module=module.__file__):
                state = module.ClipboardState()
                applied: list[bytes] = []
                diagnostics: list[str] = []

                changed = module.apply_incoming_payload(
                    b"",
                    state,
                    lambda: b"nonempty",
                    lambda payload: applied.append(payload) or True,
                    diagnostics.append,
                    "remote",
                )
                unchanged = module.apply_incoming_payload(
                    b"",
                    state,
                    lambda: b"",
                    lambda payload: applied.append(payload) or True,
                    diagnostics.append,
                    "remote",
                )

                self.assertTrue(changed)
                self.assertFalse(unchanged)
                self.assertEqual(applied, [b""])
                self.assertEqual(
                    state.last_in_hash,
                    hashlib.sha256(b"").hexdigest(),
                )
                self.assertTrue(
                    diagnostics[-1].startswith(
                        "empty-no-change direction=remote bytes=0"
                    )
                )

    def test_incoming_echo_matches_last_out_hash_and_is_not_applied(self) -> None:
        for module in (self.client, self.remote):
            with self.subTest(module=module.__file__):
                state = module.ClipboardState()
                state.last_out_hash = hashlib.sha256(b"echo").hexdigest()
                applied: list[bytes] = []
                diagnostics: list[str] = []

                changed = module.apply_incoming_payload(
                    b"echo",
                    state,
                    lambda: b"different",
                    lambda payload: applied.append(payload) or True,
                    diagnostics.append,
                    "remote",
                )

                self.assertFalse(changed)
                self.assertEqual(applied, [])
                self.assertIsNone(state.last_in_hash)
                self.assertTrue(
                    diagnostics[0].startswith(
                        "dropped:echo direction=remote bytes=4"
                    )
                )

    def test_invalid_utf8_is_rejected_at_encode_decode_and_local_boundaries(self) -> None:
        invalid = b"\xff"
        with self.assertRaises(ValueError):
            self.client.encode_frame(invalid)
        digest = hashlib.sha256(invalid).hexdigest().encode("ascii")
        self.assertIsNone(self.client.decode_frame(io.BytesIO(b"CLIP " + digest + b" 1\n" + invalid)))
        self.assertIsNone(self.client.frame_for_local_change(invalid, None))
        self.assertIsNone(self.remote.decode_frame(io.BytesIO(b"CLIP " + digest + b" 1\n" + invalid)))

    def test_fragmented_frame_decodes_after_multiple_body_reads(self) -> None:
        payload = b"fragmented"
        frame = self.client.encode_frame(payload)

        class Fragmented(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                return super().read(3 if size > 3 else size)

        self.assertEqual(self.client.decode_frame(Fragmented(frame)), payload)
        self.assertEqual(self.remote.decode_frame(Fragmented(frame)), payload)

    def test_remote_boundary_and_utf8_rules_match_client(self) -> None:
        payload = "ü".encode() * 51200
        self.assertEqual(len(payload), 102400)
        self.assertEqual(self.remote.decode_frame(io.BytesIO(self.remote.encode_frame(payload))), payload)
        self.assertIsNone(self.remote.frame_for_local_change(b"x" * 102401, None))

    def test_production_copy_loops_handle_duplex_anti_echo_and_failed_write_recovery(self) -> None:
        class RemotePipe:
            def __init__(self, inbound: bytes) -> None:
                self.stdout = io.BytesIO(inbound)
                self.stdin = io.BytesIO()
                self.returncode = None

            def poll(self) -> int | None:
                return self.returncode

        original_read = self.client.read_local_clipboard_raw
        original_write = self.client.write_local_clipboard
        stop = threading.Event()
        diagnostics: list[str] = []
        try:
            writes: list[bytes] = []
            write_results = iter([False, True])

            def write_clipboard(payload: bytes) -> bool:
                writes.append(payload)
                return next(write_results)

            self.client.write_local_clipboard = write_clipboard
            remote = RemotePipe(self.client.encode_frame(b"remote-one") + self.client.encode_frame(b"remote-two"))
            state = self.client.ClipboardState()

            self.client._copy_remote_to_local(remote, stop, state, diagnostics.append)
            self.assertEqual(writes, [b"remote-one", b"remote-two"])
            self.assertEqual(state.last_in_hash, hashlib.sha256(b"remote-two").hexdigest())

            local_reads = iter([b"remote-one", b"local-one", b"local-one", b"remote-two", b"local-two"])

            def read_clipboard() -> bytes | None:
                payload = next(local_reads, None)
                if payload is None:
                    remote.returncode = 0
                return payload

            self.client.read_local_clipboard_raw = read_clipboard
            self.client._copy_local_to_remote(remote, stop, 1, state, diagnostics.append)
            outbound = io.BytesIO(remote.stdin.getvalue())
            self.assertEqual(self.client.decode_frame(outbound), b"remote-one")
            self.assertEqual(self.client.decode_frame(outbound), b"local-one")
            self.assertEqual(self.client.decode_frame(outbound), b"local-two")
            self.assertIsNone(self.client.decode_frame(outbound))
            self.assertEqual(
                [message.split()[0] for message in diagnostics],
                ["applied", "sent", "sent", "dropped:echo", "sent"],
            )
        finally:
            self.client.read_local_clipboard_raw = original_read
            self.client.write_local_clipboard = original_write

    def test_incoming_digest_is_visible_before_clipboard_write_can_be_observed(self) -> None:
        payload = b"remote-race"
        state = self.client.ClipboardState()
        original_write = self.client.write_local_clipboard

        class RemotePipe:
            stdout = io.BytesIO(self.client.encode_frame(payload))

        def write_clipboard(value: bytes) -> bool:
            self.assertEqual(value, payload)
            self.assertEqual(state.last_in_hash, hashlib.sha256(payload).hexdigest())
            return True

        try:
            self.client.write_local_clipboard = write_clipboard
            self.client._copy_remote_to_local(RemotePipe(), threading.Event(), state)
        finally:
            self.client.write_local_clipboard = original_write

    def test_local_loop_reports_each_distinct_cap_drop_and_sent_frame_once(self) -> None:
        class RemotePipe:
            def __init__(self) -> None:
                self.stdin = io.BytesIO()
                self.returncode = None

            def poll(self) -> int | None:
                return self.returncode

        remote = RemotePipe()
        reads = iter([b"x" * 102401, b"x" * 102401, b"same", b"same", None])
        diagnostics: list[str] = []
        original_read = self.client.read_local_clipboard_raw

        def read_clipboard() -> bytes | None:
            payload = next(reads)
            if payload is None:
                remote.returncode = 0
            return payload

        try:
            self.client.read_local_clipboard_raw = read_clipboard
            self.client._copy_local_to_remote(remote, threading.Event(), 1, self.client.ClipboardState(), diagnostics.append)
        finally:
            self.client.read_local_clipboard_raw = original_read

        self.assertEqual([message.split()[0] for message in diagnostics], ["dropped:cap", "sent"])
        self.assertEqual(self.client.decode_frame(io.BytesIO(remote.stdin.getvalue())), b"same")

    def write_pasteboard_types(self, types: list[str], exit_code: int = 0) -> dict[str, str]:
        listing = "\\n".join(types)
        self.write_executable(
            "osascript",
            f"#!/bin/sh\nprintf '{listing}\\n'\nexit {exit_code}\n",
        )
        return os.environ | {"PATH": str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")}

    def test_sec05_concealed_transient_and_auto_generated_types_are_sensitive(self) -> None:
        real = load_script(CLIENT)
        for sensitive in (
            "org.nspasteboard.ConcealedType",
            "org.nspasteboard.TransientType",
            "org.nspasteboard.AutoGeneratedType",
        ):
            with self.subTest(sensitive=sensitive):
                env = self.write_pasteboard_types(["public.utf8-plain-text", sensitive])
                self.assertTrue(real.local_clipboard_is_sensitive(env=env))
        env = self.write_pasteboard_types(["public.utf8-plain-text", "public.html"])
        self.assertFalse(real.local_clipboard_is_sensitive(env=env))

    def test_sec05_unreadable_pasteboard_types_fail_closed(self) -> None:
        real = load_script(CLIENT)
        env = self.write_pasteboard_types(["public.utf8-plain-text"], exit_code=1)
        self.assertTrue(real.local_clipboard_is_sensitive(env=env))

    def test_sec05_concealed_item_produces_no_frame(self) -> None:
        real = load_script(CLIENT)
        env = self.write_pasteboard_types(["public.utf8-plain-text", "org.nspasteboard.ConcealedType"])
        checker = real.local_clipboard_is_sensitive
        real.local_clipboard_is_sensitive = lambda: checker(env=env)

        class RemotePipe:
            def __init__(self) -> None:
                self.stdin = io.BytesIO()
                self.returncode = None

            def poll(self) -> int | None:
                return self.returncode

        remote = RemotePipe()
        reads = iter([b"hunter2-password", None])
        diagnostics: list[str] = []

        def read_clipboard() -> bytes | None:
            payload = next(reads)
            if payload is None:
                remote.returncode = 0
            return payload

        real.read_local_clipboard_raw = read_clipboard
        real._copy_local_to_remote(remote, threading.Event(), 1, real.ClipboardState(), diagnostics.append)
        self.assertEqual(remote.stdin.getvalue(), b"")
        self.assertEqual([message.split()[0] for message in diagnostics], ["dropped:sensitive"])
        self.assertNotIn("hunter2", " ".join(diagnostics))

    def test_sec27_local_clipboard_read_is_capped_at_max_bytes_plus_one(self) -> None:
        real = load_script(CLIENT)
        self.write_executable(
            "pbpaste",
            "#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b'x' * 10_000_000)\n",
        )
        env = os.environ | {"PATH": str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")}
        payload = real.read_local_clipboard_raw(env=env)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload), real.MAX_BYTES + 1)
        self.assertIsNone(real.read_local_clipboard(env=env))

    def test_sec27_diagnostics_carry_no_payload_digest(self) -> None:
        payload = b"short-secret"
        digest = hashlib.sha256(payload).hexdigest()
        for decision in ("sent", "applied", "dropped:echo", "dropped:cap"):
            message = self.client.diagnostic_message(decision, "local", payload)
            self.assertNotIn("sha256", message)
            self.assertNotIn(digest[:8], message)
        self.assertEqual(self.client.diagnostic_message("sent", "local", payload), "sent direction=local bytes=12")

    def test_sec33_runtime_paths_fall_back_to_user_cache_not_tmp(self) -> None:
        home = self.tmpdir / "home"
        env = {"HOME": str(home)}
        cache = home / "Library" / "Caches"
        self.assertEqual(self.client.runtime_dir(env), cache / f"diarchy-clipboard-bridge-{os.getuid()}")
        self.assertEqual(self.client.runtime_lock_path(env), cache / f"diarchy-clipboard-bridge-{os.getuid()}.lock")
        runtime = self.client.acquire_runtime(env)
        try:
            self.assertTrue((cache / f"diarchy-clipboard-bridge-{os.getuid()}").is_dir())
        finally:
            self.client.release_runtime(runtime)

    def test_sec33_runtime_dir_owned_by_another_user_or_symlink_is_rejected(self) -> None:
        env = {"TMPDIR": str(self.tmpdir)}
        self.runtime_dir.mkdir(mode=0o700)
        original = self.client._current_uid
        try:
            self.client._current_uid = lambda: os.getuid() + 1
            with self.assertRaises(RuntimeError):
                self.client.acquire_runtime(env)
        finally:
            self.client._current_uid = original
        self.runtime_dir.rmdir()
        target = self.tmpdir / "elsewhere"
        target.mkdir()
        self.runtime_dir.symlink_to(target)
        with self.assertRaises(RuntimeError):
            self.client.acquire_runtime(env)

    def test_incoming_hash_is_not_echoed_back(self) -> None:
        digest = hashlib.sha256(b"same").hexdigest()
        self.assertFalse(self.client.should_emit(digest, digest))

    def test_local_change_frame_records_digest_for_anti_echo(self) -> None:
        frame, digest = self.client.local_change_frame_and_digest(b"new", None)
        self.assertEqual(frame, self.client.encode_frame(b"new"))
        self.assertEqual(digest, hashlib.sha256(b"new").hexdigest())
        self.assertEqual(self.client.local_change_frame_and_digest(b"new", digest), (None, digest))

    def test_binary_pipe_clipboard_commands_preserve_newlines(self) -> None:
        pbpaste = self.write_executable(
            "pbpaste",
            "#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b'a\\nb')\n",
        )
        captured = self.bin_dir / "pbcopy.out"
        self.write_executable(
            "pbcopy",
            "#!/usr/bin/env python3\nimport os, sys\nopen(os.environ['PBCOPY_CAPTURE'], 'wb').write(sys.stdin.buffer.read())\n",
        )
        env = os.environ | {
            "PATH": str(self.bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "PBCOPY_CAPTURE": str(captured),
        }
        self.assertEqual(self.client.read_local_clipboard(env=env), b"a\nb")
        self.client.write_local_clipboard(b"c\nd", env=env)
        self.assertEqual(captured.read_bytes(), b"c\nd")
        self.assertEqual(self.client.local_clipboard_command(env), [str(pbpaste), "-Prefer", "txt"])

    def test_clipboard_command_timeouts_are_metadata_only_and_recoverable(self) -> None:
        self.write_executable("pbpaste", "#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n")
        self.write_executable("pbcopy", "#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n")
        self.write_executable("wl-paste", "#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n")
        env = os.environ | {"PATH": str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")}
        self.assertIsNone(self.client.read_local_clipboard(env=env, timeout=0.01))
        self.assertFalse(self.client.write_local_clipboard(b"private", env=env, timeout=0.01))
        self.assertIsNone(self.remote.read_host_clipboard(env=env, timeout=0.01))

    def test_remote_discovers_active_wayland_socket_when_ssh_omits_display(self) -> None:
        instances = json.dumps(
            [
                {"pid": 12, "instance": "old", "wl_socket": "wayland-0", "time": 1},
                {"pid": 34, "instance": "current", "wl_socket": "wayland-1", "time": 2},
            ]
        ).encode()
        calls: list[list[str]] = []

        def check_output(argv: list[str], **kwargs: object) -> bytes:
            calls.append(argv)
            return instances

        env = self.remote.discover_wayland_environment(
            {"PATH": os.environ.get("PATH", ""), "XDG_RUNTIME_DIR": "/run/user/1000"},
            check_output=check_output,
        )

        self.assertEqual(calls, [["hyprctl", "instances", "-j"]])
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-1")

    def test_debug_diagnostics_are_bounded_and_private(self) -> None:
        debug_log = Path(self.temp.name) / "clipboard-debug.log"
        original_value = os.environ.get("DIARCHY_CLIPBOARD_DEBUG_LOG")
        original_stderr = sys.stderr
        os.environ["DIARCHY_CLIPBOARD_DEBUG_LOG"] = str(debug_log)
        sys.stderr = io.StringIO()
        try:
            for index in range(2500):
                self.client.emit_diagnostic(
                    f"sent direction=local bytes={index}"
                )
        finally:
            sys.stderr = original_stderr
            if original_value is None:
                os.environ.pop("DIARCHY_CLIPBOARD_DEBUG_LOG", None)
            else:
                os.environ["DIARCHY_CLIPBOARD_DEBUG_LOG"] = original_value
        self.assertLessEqual(debug_log.stat().st_size, 65536)
        self.assertEqual(stat.S_IMODE(debug_log.stat().st_mode), 0o600)
        contents = debug_log.read_text()
        self.assertIn("bytes=2499", contents)
        self.assertNotIn("clipboard payload", contents)

    def test_bridge_uses_route_selector_and_ssh_alias_without_leaking_payload_metadata(self) -> None:
        client_dir = CLIENT.parent
        ssh_log = Path(self.temp.name) / "ssh.json"
        self.write_executable(
            "ssh",
            "#!/usr/bin/env python3\nimport json, os, sys\nopen(os.environ['SSH_LOG'], 'w').write(json.dumps(sys.argv[1:]))\n",
        )
        route = client_dir / "diarchy-route.sh"
        result = subprocess.run(
            [str(CLIENT), "--route", "lan", "--poll-ms", "1"],
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ
            | self.runtime_env
            | {
                "DIARCHY_CLIPBOARD_RUNTIME_ONCE": "1",
                "DIARCHY_CLIPBOARD_ROUTE_SH": str(route),
                "DIARCHY_SSH_ALIAS": "user-private-alias",
                "SSH_BIN": str(self.bin_dir / "ssh"),
                "PING_BIN": str(self.write_executable("ping", "#!/bin/sh\nprintf '4 packets transmitted, 4 packets received, 0.0%% packet loss\\n64 bytes from x: icmp_seq=1 ttl=64 time=1.0 ms\\n'\n")),
                "ROUTE_BIN": str(self.write_executable("route", "#!/bin/sh\nprintf ' interface: en0\\n'\n")),
                "CONNECT_BIN": str(self.write_executable("nc", "#!/bin/sh\nexit 0\n")),
                "SSH_LOG": str(ssh_log),
            },
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        argv = json.loads(ssh_log.read_text())
        self.assertIn("ControlMaster=no", argv)
        self.assertIn("ControlPath=none", argv)
        self.assertIn("PermitLocalCommand=no", argv)
        self.assertIn("ClearAllForwardings=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertTrue(any(item.startswith("UserKnownHostsFile=") for item in argv), argv)
        self.assertIn("user-private-alias", argv)
        self.assertIn("HostName=192.0.2.10", argv)
        self.assertIn("User=labuser", argv)
        self.assertEqual(argv[-1], "$HOME/.local/bin/diarchy-clipboard-remote --poll-ms 1")
        stderr = result.stderr.decode()
        self.assertNotIn("user-private-alias", stderr)
        self.assertNotIn("sha256", stderr.lower())
        self.assertNotIn("102400", stderr)

    def test_ssh_refusal_emits_one_metadata_only_diagnostic(self) -> None:
        ssh = self.write_executable(
            "ssh-refused",
            "#!/bin/sh\nprintf 'private ssh detail\\n' >&2\nexit 42\n",
        )
        result = subprocess.run(
            [str(CLIENT), "--route", "lan", "--poll-ms", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ
            | self.runtime_env
            | {
                "DIARCHY_CLIPBOARD_RUNTIME_ONCE": "1",
                "DIARCHY_CLIPBOARD_ROUTE_SH": str(CLIENT.parent / "diarchy-route.sh"),
                "SSH_BIN": str(ssh),
                "PING_BIN": str(self.write_executable("ping-refused", "#!/bin/sh\nprintf '4 packets transmitted, 4 packets received, 0.0%% packet loss\\n64 bytes from x: icmp_seq=1 ttl=64 time=1.0 ms\\n'\n")),
                "ROUTE_BIN": str(self.write_executable("route-refused", "#!/bin/sh\nprintf ' interface: en0\\n'\n")),
                "CONNECT_BIN": str(self.write_executable("nc-refused", "#!/bin/sh\nexit 0\n")),
            },
            check=False,
        )
        stderr = result.stderr.decode()
        self.assertEqual(result.returncode, 42, stderr)
        self.assertEqual(stderr.count("dropped:ssh-refused"), 1)
        self.assertRegex(
            stderr,
            r"dropped:ssh-refused direction=remote bytes=0\n",
        )
        self.assertNotIn("private ssh detail", stderr)

    def test_live_owner_is_rejected_and_stale_metadata_is_replaced_identity_safely(self) -> None:
        directory = self.runtime_dir
        directory.mkdir()
        pid_file = directory / "pid"
        pid_file.write_text("777\ndiarchy-clipboard-bridge-runtime-v1\n")

        with self.assertRaisesRegex(RuntimeError, "already running"):
            self.client.acquire_runtime(
                env=self.runtime_env,
                command_identity=lambda pid: "diarchy-clipboard-bridge-runtime-v1",
            )
        self.assertEqual(pid_file.read_text(), "777\ndiarchy-clipboard-bridge-runtime-v1\n")

        pid_file.write_text("888\ndiarchy-clipboard-bridge-runtime-v1\n")
        acquired = self.client.acquire_runtime(
            env=self.runtime_env,
            command_identity=lambda pid: "ssh host",
        )
        self.assertEqual(Path(acquired), directory)
        self.assertEqual(
            pid_file.read_text(),
            f"{os.getpid()}\ndiarchy-clipboard-bridge-runtime-v1\n",
        )
        self.client.release_runtime(acquired)

    def test_runtime_lock_blocks_contender_during_pid_write_window(self) -> None:
        entered_write_window = threading.Event()
        release_write_window = threading.Event()
        results: list[str] = []

        def first_owner() -> None:
            runtime = self.client.acquire_runtime(
                env=self.runtime_env,
                before_pid_write=lambda: (entered_write_window.set(), release_write_window.wait(5)),
            )
            results.append("first-acquired")
            self.client.release_runtime(runtime)

        thread = threading.Thread(target=first_owner)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.assertTrue(entered_write_window.wait(5))
        with self.assertRaises(RuntimeError):
            self.client.acquire_runtime(env=self.runtime_env)
        release_write_window.set()
        thread.join(5)
        self.assertEqual(results, ["first-acquired"])

    def test_runtime_release_keeps_contender_on_same_stable_lock_inode(self) -> None:
        original_close = self.client.os.close
        contender_runtime = None
        second_runtime = None
        runtime_existed_when_contender_started = None

        owner_runtime = self.client.acquire_runtime(env=self.runtime_env)
        lock_path = getattr(owner_runtime, "lock_path", self.runtime_dir / "lock")
        owner_inode = lock_path.stat().st_ino
        owner_fd = owner_runtime.lock_fd

        def close_and_acquire_contender(fd: int) -> None:
            nonlocal contender_runtime, runtime_existed_when_contender_started
            original_close(fd)
            if fd == owner_fd:
                runtime_existed_when_contender_started = self.runtime_dir.exists()
                contender_runtime = self.client.acquire_runtime(env=self.runtime_env)

        self.client.os.close = close_and_acquire_contender
        try:
            self.client.release_runtime(owner_runtime)
            self.assertFalse(runtime_existed_when_contender_started)
            self.assertIsNotNone(contender_runtime)
            self.assertEqual(lock_path.stat().st_ino, owner_inode)
            with self.assertRaises(RuntimeError):
                second_runtime = self.client.acquire_runtime(env=self.runtime_env)
        finally:
            self.client.os.close = original_close
            if second_runtime is not None:
                self.client.release_runtime(second_runtime)
            if contender_runtime is not None:
                self.client.release_runtime(contender_runtime)

    def test_remote_replaces_and_reaps_foreground_wl_copy(self) -> None:
        first, second = FakeProcess(), FakeProcess()
        owner = self.remote.replace_owner(first, second)
        self.assertTrue(first.terminated and first.waited)
        self.assertIs(owner, second)

    def test_remote_does_not_reap_unowned_or_wrong_command_child(self) -> None:
        wrong = FakeProcess(args=("sleep", "100"))
        self.assertIs(self.remote.replace_owner(wrong, None), None)
        self.assertFalse(wrong.terminated)
        self.assertFalse(wrong.waited)
    def test_remote_run_remote_reaps_in_flight_spawn_on_sigterm(self) -> None:
        events: list[str] = []
        child = FakeProcess(pid=12, events=events, name="current")
        original_stdin, original_stdout = sys.stdin, sys.stdout
        original_start = self.remote.start_wl_copy
        try:
            sys.stdin = types.SimpleNamespace(buffer=io.BytesIO(self.remote.encode_frame(b"payload")))
            sys.stdout = types.SimpleNamespace(buffer=io.BytesIO())

            def delayed_start(payload: bytes, popen=subprocess.Popen) -> FakeProcess:
                events.append("spawn:start")
                time.sleep(0.2)
                events.append("spawn:return")
                return child

            def terminate_soon() -> None:
                deadline = time.time() + 5
                while time.time() < deadline and "spawn:start" not in events:
                    time.sleep(0.01)
                os.kill(os.getpid(), signal.SIGTERM)

            self.remote.start_wl_copy = delayed_start
            killer = threading.Thread(target=terminate_soon)
            killer.start()
            with self.assertRaises(SystemExit):
                self.remote.run_remote(1)
            killer.join(5)
            self.assertEqual(events, ["spawn:start", "spawn:return", "terminate:current", "wait:current:2.0"])
        finally:
            self.remote.start_wl_copy = original_start
            sys.stdin, sys.stdout = original_stdin, original_stdout

    def test_bridge_singleton_pid_directory_is_cleaned_on_sigterm(self) -> None:
        sleep = self.write_executable("ssh", "#!/bin/sh\nsleep 30\n")
        proc = subprocess.Popen(
            [str(CLIENT), "--poll-ms", "1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ
            | self.runtime_env
            | {
                "DIARCHY_CLIPBOARD_ROUTE_SH": str(CLIENT.parent / "diarchy-route.sh"),
                "SSH_BIN": str(sleep),
                "PING_BIN": str(self.write_executable("ping", "#!/bin/sh\nprintf '4 packets transmitted, 4 packets received, 0.0%% packet loss\\n64 bytes from x: icmp_seq=1 ttl=64 time=1.0 ms\\n'\n")),
                "ROUTE_BIN": str(self.write_executable("route", "#!/bin/sh\nprintf ' interface: en0\\n'\n")),
                "CONNECT_BIN": str(self.write_executable("nc", "#!/bin/sh\nexit 0\n")),
            },
        )
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.time() + 5
        pid_file = self.runtime_dir / "pid"
        while time.time() < deadline and not pid_file.exists():
            time.sleep(0.01)
        self.assertTrue(pid_file.exists())
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 0, stderr.decode())
        self.assertFalse(pid_file.parent.exists())


if __name__ == "__main__":
    unittest.main()
