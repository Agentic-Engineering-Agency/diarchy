from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.test_diarchy_host_support import AgentCase, HOST, LAUNCHER

sys.path.insert(0, str(HOST))
from diarchy_host import cli, common  # noqa: E402


class EnvelopeTests(AgentCase):
    def test_version_success_envelope(self) -> None:
        result, body = self.agent("version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body, {"schema": 1, "ok": True, "command": "version", "agent_version": "0.1.0-beta.1"})
        self.assertTrue(result.stdout.endswith("\n"))

    def test_launcher_runs_directly_through_its_shebang(self) -> None:
        env = self.env | {"PATH": os.pathsep.join([str(self.tools), os.path.dirname(sys.executable)])}
        result = subprocess.run([str(LAUNCHER), "version"], env=env, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"agent_version":"0.1.0-beta.1"', result.stdout)

    def test_missing_command_is_usage_error_exit_64(self) -> None:
        result, body = self.agent()
        self.assertEqual(result.returncode, 64)
        self.assertEqual(body["ok"], False)
        self.assertEqual(body["schema"], 1)
        self.assertEqual(body["error"]["code"], "usage")
        self.assertIsInstance(body["error"]["message"], str)

    def test_unknown_command_and_extra_args_are_usage_errors(self) -> None:
        for argv in (("bogus",), ("version", "extra"), ("--help",), ("-h",)):
            with self.subTest(argv=argv):
                result, body = self.agent(*argv)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(body["error"]["code"], "usage")
                self.assertIn("usage: diarchy-host {version|probe|report|kvm|stream} ...", body["error"]["message"])

    def test_command_field_follows_first_non_option_token(self) -> None:
        _, body = self.agent("bogus")
        self.assertEqual(body["command"], "bogus")
        _, body = self.agent()
        self.assertEqual(body["command"], "")

    def test_corrupt_agent_dir_yields_internal_envelope_not_traceback(self) -> None:
        corrupt = self.root / "corrupt"
        (corrupt / "diarchy_host").mkdir(parents=True)
        (corrupt / "diarchy_host" / "__init__.py").write_text("")
        result, body = self.agent("kvm", "status", env={"DIARCHY_AGENT_DIR": str(corrupt)})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body["command"], "kvm status")
        self.assertEqual(body["error"]["code"], "internal")
        self.assertTrue(body["error"]["message"].startswith("cannot load diarchy-host: "), body)
        self.assertNotIn("Traceback", result.stderr)

    def test_launcher_without_package_is_not_installed(self) -> None:
        lonely = self.root / "lonely"
        lonely.mkdir()
        launcher = lonely / "diarchy-host"
        shutil.copy(LAUNCHER, launcher)
        result = subprocess.run([sys.executable, str(launcher), "stream", "status"], env=self.env, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body, {"schema": 1, "ok": False, "command": "stream status",
                                "error": {"code": "not_installed", "message": "diarchy-host package not found"}})

    def test_launcher_rejects_python_older_than_3_11(self) -> None:
        script = ("import runpy, sys\nsys.version_info = (3, 10, 0)\nsys.argv = ['diarchy-host', 'version']\n"
                  f"runpy.run_path({str(LAUNCHER)!r}, run_name='__main__')\n")
        result = subprocess.run([sys.executable, "-c", script], env=self.env, text=True, capture_output=True,
                                check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["command"], "version")
        self.assertEqual(body["error"]["code"], "unsupported_platform")

    def test_launcher_command_rule_matches_cli(self) -> None:
        namespace = runpy.run_path(str(LAUNCHER), run_name="launcher_under_test")
        self.assertEqual(namespace["_SUBCOMMANDED"], cli.SUBCOMMANDED)
        for argv in ([], ["bogus"], ["--x", "kvm", "start"], ["kvm"], ["kvm", "bogus"], ["kvm", "--x"],
                     ["kvm", "start"], ["stream", "pair", "x"], ["stream", "-v"], ["version", "extra"]):
            with self.subTest(argv=argv):
                self.assertEqual(namespace["_command_name"](argv), cli._command_name(argv))

    def agent_copy(self, stream_source: str | None = None) -> Path:
        """Temp copy of the package WITHOUT team t4's stream.py (optionally with a stand-in)."""
        copy = self.root / "agent-copy"
        shutil.copytree(HOST / "diarchy_host", copy / "diarchy_host",
                        ignore=shutil.ignore_patterns("__pycache__", "stream.py"))
        shutil.copy(HOST / "VERSION", copy / "VERSION")
        if stream_source is not None:
            (copy / "diarchy_host" / "stream.py").write_text(stream_source)
        return copy

    def test_stream_reports_not_installed_while_module_absent(self) -> None:
        copy = self.agent_copy()
        result, body = self.agent("stream", "status", env={"DIARCHY_AGENT_DIR": str(copy)})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body["command"], "stream status")
        self.assertEqual(body["error"], {"code": "not_installed", "message": "streaming support is not installed"})

    def test_stream_dispatches_tokens_after_stream_and_wraps_payload(self) -> None:
        calls: list[list[str]] = []

        def handle(args: list[str]) -> dict:
            calls.append(args)
            return {"paired": True}

        fake = types.ModuleType("diarchy_host.stream")
        fake.handle = handle  # type: ignore[attr-defined]
        out = io.StringIO()
        with mock.patch.dict(sys.modules, {"diarchy_host.stream": fake}), contextlib.redirect_stdout(out):
            code = cli.main(["stream", "pair", "--pin", "1234"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [["pair", "--pin", "1234"]])
        self.assertEqual(json.loads(out.getvalue()),
                         {"schema": 1, "ok": True, "command": "stream pair", "paired": True})

    def test_unrelated_import_error_inside_stream_is_internal_not_not_installed(self) -> None:
        copy = self.agent_copy("import diarchy_host_missing_dependency\n")
        result, body = self.agent("stream", "status", env={"DIARCHY_AGENT_DIR": str(copy)})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body["command"], "stream status")
        self.assertEqual(body["error"]["code"], "internal")
        self.assertIn("ModuleNotFoundError", body["error"]["message"])

    def test_non_serializable_payload_becomes_internal_failure_envelope(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(cli.DISPATCH, {"version": lambda args: {"p": Path("x")}}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["version"])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue().count("\n"), 1)
        body = json.loads(out.getvalue())
        self.assertEqual((body["ok"], body["command"], body["error"]["code"]), (False, "version", "internal"))
        self.assertIn("TypeError", body["error"]["message"])
        self.assertIn("Traceback", err.getvalue())

    def test_payload_cannot_override_envelope_keys(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(cli.DISPATCH, {"version": lambda args: {"ok": False, "schema": 9}}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["version"])
        self.assertEqual(code, 1)
        body = json.loads(out.getvalue())
        self.assertEqual((body["schema"], body["ok"], body["error"]["code"]), (1, False, "internal"))
        self.assertIn("ok, schema", body["error"]["message"])

    def test_internal_exception_still_prints_one_json_line(self) -> None:
        # A missing VERSION file is an unexpected condition inside the `version` handler.
        result, body = self.agent("version", env={"DIARCHY_AGENT_DIR": str(self.root / "nowhere")})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body["error"]["code"], "internal")
        self.assertTrue(body["error"]["message"].startswith("cannot read agent VERSION: "), body)

    def test_agent_version_is_read_from_the_agent_root(self) -> None:
        self.assertEqual(common.agent_version(), (HOST / "VERSION").read_text().strip())
        with mock.patch.dict(os.environ, {"DIARCHY_AGENT_DIR": str(self.root / "nowhere")}):
            with self.assertRaises(common.AgentError) as caught:
                common.agent_version()
        self.assertEqual(caught.exception.code, "internal")
        self.assertTrue(caught.exception.message.startswith("cannot read agent VERSION: "))

    def test_unexpected_handler_exception_maps_to_internal(self) -> None:
        def boom(args: list[str]) -> dict:
            raise ValueError("kaput")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(cli.DISPATCH, {"version": boom}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["version"])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue().count("\n"), 1)
        self.assertIn('"message":"ValueError: kaput"', out.getvalue())
        self.assertIn("Traceback", err.getvalue())

    def test_stray_prints_never_reach_stdout(self) -> None:
        # Guard (R6): handlers run with stdout redirected to stderr.
        def noisy(args: list[str]) -> dict:
            print("noise")
            return {"agent_version": "x"}

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(cli.DISPATCH, {"version": noisy}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().count("\n"), 1)
        self.assertNotIn("noise", out.getvalue())
        self.assertIn("noise", err.getvalue())

    def test_non_interactive_ssh_env_gets_runtime_dir_default(self) -> None:
        with mock.patch.dict(os.environ), contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            cli.main(["version"])
            self.assertEqual(os.environ["XDG_RUNTIME_DIR"], f"/run/user/{os.getuid()}")


class CommonTests(AgentCase):
    def test_run_returns_non_zero_without_raising(self) -> None:
        result = common.run([sys.executable, "-c", "import sys; print('x'); sys.exit(3)"])
        self.assertEqual((result.returncode, result.stdout.strip()), (3, "x"))

    def test_run_missing_executable_raises_internal(self) -> None:
        with self.assertRaises(common.AgentError) as caught:
            common.run(["/nonexistent/definitely-not-here"])
        self.assertEqual(caught.exception.code, "internal")

    def test_run_timeout_raises_internal(self) -> None:
        with self.assertRaises(common.AgentError) as caught:
            common.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
        self.assertEqual(caught.exception.code, "internal")

    def test_run_passes_stdin(self) -> None:
        result = common.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], input="abc")
        self.assertEqual(result.stdout.strip(), "ABC")

    def test_agent_error_exposes_code_and_message(self) -> None:
        error = common.AgentError("usage", "bad")
        self.assertEqual((error.code, error.message, str(error)), ("usage", "bad", "bad"))

    def test_resolve_bin_prefers_environment(self) -> None:
        with mock.patch.dict(os.environ, {"X_BIN": "/opt/x"}):
            self.assertEqual(common.resolve_bin("X_BIN", "x"), "/opt/x")
        with mock.patch.dict(os.environ):
            os.environ.pop("X_BIN", None)
            self.assertEqual(common.resolve_bin("X_BIN", "x"), "x")

    def test_home_and_agent_root(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            os.environ.pop("DIARCHY_AGENT_DIR", None)
            self.assertEqual(common.home(), self.home)
            self.assertEqual(common.agent_root(), HOST)
            os.environ["DIARCHY_AGENT_DIR"] = str(self.root)
            self.assertEqual(common.agent_root(), self.root)

    def test_systemctl_user_honors_env_and_unit_active_maps_exit_code(self) -> None:
        self.tool("fake-systemctl", (
            "import os, sys\n"
            "open(os.environ['FAKE_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(0 if sys.argv[-1] == 'up.service' else 3)\n"
        ), env_var="SYSTEMCTL_BIN")
        with mock.patch.dict(os.environ, self.env):
            self.assertTrue(common.unit_active("up.service"))
            self.assertFalse(common.unit_active("down.service"))
            self.assertTrue(common.unit_active_system("up.service"))
            self.assertEqual(common.systemctl_user("status", "x").returncode, 3)
        self.assertEqual(self.calls(), [
            "--user is-active --quiet up.service",
            "--user is-active --quiet down.service",
            "is-active --quiet up.service",
            "--user status x",
        ])

    def test_unit_active_is_false_when_systemctl_is_missing(self) -> None:
        with mock.patch.dict(os.environ, {"SYSTEMCTL_BIN": str(self.root / "absent")}):
            self.assertFalse(common.unit_active("up.service"))
            self.assertFalse(common.unit_active_system("up.service"))

    def test_error_codes_are_the_spec_set(self) -> None:
        self.assertEqual(set(common.ERROR_CODES), {
            "usage", "unsupported_platform", "not_installed", "no_session", "service_failed", "invalid_fingerprint",
            "config_write_failed", "sunshine_unreachable", "pair_failed", "internal"})
        self.assertEqual(len(common.ERROR_CODES), 10)


if __name__ == "__main__":
    unittest.main()
