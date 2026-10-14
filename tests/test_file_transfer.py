from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import tempfile
import textwrap
import urllib.parse
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client" / "diarchy-file"
LAN_HOST = "192.0.2.10"
TAILSCALE_HOST = "100.64.0.10"
SSH_PORT = "22"

_ACTIVE_CASE: "FileTransferTests | None" = None


def run_file(args: list[str], scenario: str) -> subprocess.CompletedProcess[str]:
    if _ACTIVE_CASE is None:
        raise RuntimeError("run_file requires an active FileTransferTests instance")
    return _ACTIVE_CASE.run_file(args, scenario)


class FileTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        global _ACTIVE_CASE
        _ACTIVE_CASE = self
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._clear_active)
        self.tmpdir = Path(self.temp.name)
        self.bin_dir = self.tmpdir / "bin"
        self.bin_dir.mkdir()
        self.execution_log = self.tmpdir / "executions.jsonl"
        self.source = str(self.tmpdir / "source.txt")
        Path(self.source).write_text("payload")
        self.space_source = str(self.tmpdir / "file with spaces.txt")
        Path(self.space_source).write_text("space payload")
        self.dash_source = str(self.tmpdir / "-leading-dash.txt")
        Path(self.dash_source).write_text("dash payload")
        self.apostrophe_source = str(self.tmpdir / "x';touch pwn;#.txt")
        Path(self.apostrophe_source).write_text("quoted payload")
        self.tab_newline_source = str(self.tmpdir / "line\tone\nline two.txt")
        Path(self.tab_newline_source).write_text("weird payload")
        self.raw_source = os.fsdecode(b"raw-\xff.bin")
        self.home = self.tmpdir / "home"
        self.download_dir = self.home / "Drop" / "from-host"
        self.download = self.download_dir / "missing.txt"
        self.diarchy_config = self.tmpdir / "diarchy.conf"
        self.diarchy_config.write_text(
            "# Managed by Diarchy\n"
            "DIARCHY_SSH_USER=labuser\n"
            f"DIARCHY_LAN_HOST={LAN_HOST}\n"
            f"DIARCHY_TAILSCALE_HOST={TAILSCALE_HOST}\n"
            f"DIARCHY_SSH_PORT={SSH_PORT}\n"
            "DIARCHY_HOST_KEY_ALIAS=diarchy-test-host\n"
        )
        self._write_stubs()
        self._write_xattr_stub()

    def _write_xattr_stub(self) -> None:
        self._write_executable(
            "xattr",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, sys
                from pathlib import Path
                with Path(os.environ["EXECUTION_LOG"]).open("a") as handle:
                    handle.write(json.dumps({"tool": "xattr", "argv": sys.argv[1:], "exists": os.path.lexists(sys.argv[-1])}) + "\\n")
                raise SystemExit(int(os.environ.get("FAKE_XATTR_EXIT", "0")))
                """
            ),
        )

    def _clear_active(self) -> None:
        global _ACTIVE_CASE
        if _ACTIVE_CASE is self:
            _ACTIVE_CASE = None

    def _write_executable(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
        return path

    def _write_stubs(self) -> None:
        self._write_executable("ping", "#!/bin/sh\nprintf '4 packets transmitted, 4 packets received, 0.0%% packet loss\\n64 bytes from x: icmp_seq=1 ttl=64 time=1.0 ms\\n'\n")
        self._write_executable("route", "#!/bin/sh\nprintf ' interface: en0\\n'\n")
        self._write_executable("nc", "#!/bin/sh\nexit 0\n")
        self._write_executable(
            "ssh",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import base64, json, os, subprocess, sys
                from pathlib import Path
                log = Path(os.environ["EXECUTION_LOG"])
                with log.open("a") as handle:
                    handle.write(json.dumps({"tool": "ssh", "argv": sys.argv[1:]}) + "\\n")
                scenario = os.environ["FAKE_SCENARIO"]
                if scenario == "visible-hidden-nested":
                    print("dmlzaWJsZS50eHQ\\t12\\t1700000000")
                    raise SystemExit(0)
                if scenario == "encoded-weird-list":
                    names = ["tab\\tname.txt", "line\\nname.txt", "forged\\nname=evil bytes=1 mtime=1970-01-01T00:00:00Z"]
                    for index, name in enumerate(names, 1):
                        encoded = base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")
                        print(f"{encoded}\\t{10 + index}\\t170000000{index}")
                    raise SystemExit(0)
                if scenario == "encoded-raw-list":
                    encoded = base64.urlsafe_b64encode(b"raw-\\xff.bin").decode().rstrip("=")
                    print(f"{encoded}\\t3\\t1700000004")
                    raise SystemExit(0)
                if scenario == "exec-local-list":
                    completed = subprocess.run(sys.argv[-1], shell=True, text=True)
                    raise SystemExit(completed.returncode)
                if scenario == "metadata-failure":
                    raise SystemExit(7)
                raise SystemExit(0)
                """
            ),
        )
        self._write_executable(
            "rsync",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, sys
                from pathlib import Path
                log = Path(os.environ["EXECUTION_LOG"])
                with log.open("a") as handle:
                    handle.write(json.dumps({"tool": "rsync", "argv": sys.argv[1:]}) + "\\n")
                scenario = os.environ["FAKE_SCENARIO"]
                if scenario in {"created", "spaces", "leading-dash", "apostrophe-metachar", "tab-newline"}:
                    print(">f+++++++++")
                    raise SystemExit(0)
                if scenario == "directory-created":
                    print("created directory /home/user/Drop/from-mac/source-dir")
                    print("cd+++++++++")
                    print(">f+++++++++")
                    raise SystemExit(0)
                if scenario == "legacy-created":
                    print("<f+++++++")
                    raise SystemExit(0)
                if scenario == "updated":
                    print(">f.st......")
                    raise SystemExit(0)
                if scenario == "no-change":
                    raise SystemExit(0)
                if scenario == "diagnostic-only":
                    print("receiving file list ... done")
                    raise SystemExit(0)
                if scenario == "metadata-only":
                    print(".f..t......")
                    raise SystemExit(0)
                if scenario == "delete-record":
                    print("*deleting  ")
                    raise SystemExit(0)
                if scenario == "malformed-itemized":
                    print(">f++++")
                    raise SystemExit(0)
                if scenario == "mixed-output":
                    print(">f+++++++++")
                    print("warning: not an itemized record")
                    raise SystemExit(0)
                if scenario == "nonzero-with-valid-output":
                    print(">f+++++++++")
                    raise SystemExit(23)
                if scenario == "multi-mixed":
                    if any("source.txt" in arg for arg in sys.argv[1:]):
                        print(">f+++++++++")
                        raise SystemExit(0)
                    raise SystemExit(23)
                if scenario == "download-ok":
                    dest_dir = Path(sys.argv[-1])
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    (dest_dir / os.environ["DOWNLOAD_NAME"]).write_text("downloaded")
                    print(">f+++++++++")
                    raise SystemExit(0)
                if scenario == "download-race":
                    dest_dir = Path(sys.argv[-1])
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    (dest_dir / os.environ["DOWNLOAD_NAME"]).write_text("downloaded")
                    final = Path(os.environ["HOME"]) / "Drop" / "from-host" / os.environ["DOWNLOAD_NAME"]
                    final.write_text("concurrent bytes")
                    print(">f+++++++++")
                    raise SystemExit(0)
                if scenario in {"download-directory", "download-directory-symlink"}:
                    dest_dir = Path(sys.argv[-1])
                    tree = dest_dir / os.environ["DOWNLOAD_NAME"]
                    nested = tree / "nested"
                    nested.mkdir(parents=True)
                    (nested / "data.txt").write_text("directory payload")
                    if scenario == "download-directory-symlink":
                        (tree / "link").symlink_to(nested / "data.txt")
                    print("cd+++++++++")
                    print(">f+++++++++")
                    raise SystemExit(0)
                if scenario in {"missing", "overwrite-failure", "network-failure"}:
                    dest_dir = Path(sys.argv[-1])
                    partial = dest_dir / ".rsync-partial" / os.environ.get("DOWNLOAD_NAME", "missing.txt")
                    partial.parent.mkdir(parents=True, exist_ok=True)
                    partial.write_text("partial")
                    if scenario == "missing":
                        print("rsync: link_stat failed: No such file or directory (2)", file=sys.stderr)
                    if scenario == "network-failure":
                        print(">f+++++++++")
                    raise SystemExit(23)
                raise SystemExit(99)
                """
            ),
        )

    def run_file(
        self,
        args: list[str],
        scenario: str,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("DIARCHY_")} | {
            "PATH": str(self.bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "TMPDIR": str(self.tmpdir),
            "EXECUTION_LOG": str(self.execution_log),
            "FAKE_SCENARIO": scenario,
            "DOWNLOAD_NAME": args[-1] if args and args[0] == "get" else "download.txt",
            "DIARCHY_FILE_ROUTE_SH": str(ROOT / "client" / "diarchy-route.sh"),
            "DIARCHY_CONFIG": str(self.diarchy_config),
            "DIARCHY_SSH_ALIAS": "diarchy-test",
            "PING_BIN": str(self.bin_dir / "ping"),
            "ROUTE_BIN": str(self.bin_dir / "route"),
            "CONNECT_BIN": str(self.bin_dir / "nc"),
            "SSH_BIN": str(self.bin_dir / "ssh"),
            "RSYNC_BIN": str(self.bin_dir / "rsync"),
            "XATTR_BIN": str(self.bin_dir / "xattr"),
        }
        env.update(extra_env or {})
        return subprocess.run([str(CLIENT), *args], env=env, text=True, capture_output=True, check=False)

    def log_entries(self) -> list[dict[str, object]]:
        if not self.execution_log.exists():
            return []
        return [json.loads(line) for line in self.execution_log.read_text().splitlines()]

    def rsync_entries(self) -> list[dict[str, object]]:
        return [entry for entry in self.log_entries() if entry["tool"] == "rsync"]

    def ssh_entries(self) -> list[dict[str, object]]:
        return [entry for entry in self.log_entries() if entry["tool"] == "ssh"]

    def rsync_argv(self, index: int = -1) -> list[str]:
        return self.rsync_entries()[index]["argv"]  # type: ignore[return-value]

    def decode_name_field(self, stdout: str) -> list[str]:
        names: list[str] = []
        for line in stdout.splitlines():
            for field in line.split():
                if field.startswith("name="):
                    names.append(urllib.parse.unquote(field.removeprefix("name=")))
        return names

    def decode_name_field_bytes(self, stdout: str) -> list[bytes]:
        names: list[bytes] = []
        for line in stdout.splitlines():
            for field in line.split():
                if field.startswith("name="):
                    names.append(urllib.parse.unquote_to_bytes(field.removeprefix("name=")))
        return names

    def test_send_uses_ignore_existing_and_itemized_output(self) -> None:
        result = run_file(["send", self.source], "created")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--ignore-existing", self.rsync_argv())
        self.assertIn("--itemize-changes", self.rsync_argv())
        self.assertIn("status=ok", result.stdout)

    def test_send_accepts_legacy_macos_rsync_itemized_output(self) -> None:
        result = run_file(["send", self.source], "legacy-created")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=ok", result.stdout)

    def test_overwrite_omits_ignore_existing(self) -> None:
        result = run_file(["send", "--overwrite", self.source], "updated")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--ignore-existing", self.rsync_argv())

    def test_no_itemized_change_reports_skipped_and_nonzero(self) -> None:
        result = run_file(["send", self.source], "no-change")
        self.assertEqual(result.returncode, 1)
        self.assertIn("status=skipped-exists", result.stdout)

    def test_get_missing_leaves_no_final_or_created_partial_file(self) -> None:
        result = run_file(["get", "missing.txt"], "missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status=failed reason=missing", result.stdout)
        self.assertFalse(self.download.exists())
        self.assertFalse((self.download_dir / ".rsync-partial" / "missing.txt").exists())

    def test_default_get_collision_preserves_existing_file_and_skips_rsync(self) -> None:
        self.download_dir.mkdir(parents=True)
        self.download.write_text("original")
        result = run_file(["get", "missing.txt"], "download-ok")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.download.read_text(), "original")
        self.assertIn("status=skipped-exists", result.stdout)
        self.assertEqual(self.rsync_entries(), [])

    def test_default_get_race_preserves_concurrent_file_and_reports_skipped(self) -> None:
        result = run_file(["get", "missing.txt"], "download-race")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.download.read_text(), "concurrent bytes")
        self.assertIn("status=skipped-exists", result.stdout)
        self.assertFalse(any(self.download_dir.glob(".diarchy-file-get.*")))

    def test_get_directory_publishes_private_tree(self) -> None:
        result = run_file(["get", "folder"], "download-directory")
        target = self.download_dir / "folder"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / "nested" / "data.txt").read_text(), "directory payload")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((target / "nested").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((target / "nested" / "data.txt").stat().st_mode), 0o600)
        self.assertIn("status=ok", result.stdout)
        self.assertFalse(any(self.download_dir.glob(".diarchy-file-get.*")))

    def test_get_directory_rejects_symlink_without_publishing(self) -> None:
        result = run_file(["get", "folder"], "download-directory-symlink")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.download_dir / "folder").exists())
        self.assertIn("status=failed", result.stdout)
        self.assertFalse(any(self.download_dir.glob(".diarchy-file-get.*")))

    def test_get_overwrite_publication_failure_preserves_existing_file(self) -> None:
        target = self.download_dir / "download.txt"
        target.parent.mkdir(parents=True)
        target.write_text("existing bytes")
        python_wrapper = self._write_executable(
            "python-publication-failure",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import subprocess, sys
                script = sys.stdin.read()
                script = script.replace(
                    "os.replace(staged, target)",
                    'raise OSError("injected publication failure")',
                )
                script = script.replace(
                    "exchange_rename(staged, target)",
                    'raise OSError("injected publication failure")',
                )
                completed = subprocess.run(
                    [sys.executable, *sys.argv[1:]],
                    input=script,
                    text=True,
                )
                raise SystemExit(completed.returncode)
                """
            ),
        )

        result = self.run_file(
            ["get", "--overwrite", "download.txt"],
            "download-ok",
            extra_env={"PYTHON_BIN": str(python_wrapper)},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "existing bytes")
        self.assertFalse(any(target.parent.glob(".diarchy-file-get.*")))

    def test_get_overwrite_completes_before_post_publish_observation(self) -> None:
        target = self.download_dir / "download.txt"
        target.parent.mkdir(parents=True)
        target.write_text("existing bytes")
        python_wrapper = self._write_executable(
            "python-post-publication-failure",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import subprocess, sys
                script = sys.stdin.read()
                script = script.replace(
                    "size = target.stat().st_size",
                    'target.unlink(); raise OSError("injected post-publication failure")',
                )
                completed = subprocess.run(
                    [sys.executable, *sys.argv[1:]],
                    input=script,
                    text=True,
                )
                raise SystemExit(completed.returncode)
                """
            ),
        )

        result = self.run_file(
            ["get", "--overwrite", "download.txt"],
            "download-ok",
            extra_env={"PYTHON_BIN": str(python_wrapper)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(), "downloaded")
        self.assertFalse(any(target.parent.glob(".diarchy-file-get.*")))

    def test_get_overwrite_failure_preserves_existing_file_and_removes_new_partial(self) -> None:
        target = self.download_dir / "download.txt"
        target.parent.mkdir(parents=True)
        target.write_text("existing bytes")
        result = run_file(["get", "--overwrite", "download.txt"], "overwrite-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "existing bytes")
        self.assertFalse(any(target.parent.glob(".diarchy-file-get.*")))

    def test_get_network_failure_preserves_existing_file_and_removes_new_partial(self) -> None:
        target = self.download_dir / "download.txt"
        target.parent.mkdir(parents=True)
        target.write_text("existing before network")
        result = run_file(["get", "--overwrite", "download.txt"], "network-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "existing before network")
        self.assertFalse(any(target.parent.glob(".diarchy-file-get.*")))

    def test_list_is_non_recursive_and_internal_staging_is_hidden(self) -> None:
        result = run_file(["list"], "visible-hidden-nested")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("name="), 1)
        self.assertIn("name=visible.txt", result.stdout)
        self.assertEqual(len(self.ssh_entries()), 1)
        remote_command = self.ssh_entries()[0]["argv"][-1]
        self.assertIn("python3 -c", remote_command)
        self.assertIn("Drop", remote_command)
        self.assertIn("to-mac", remote_command)
        self.assertNotIn('path.name.startswith(".")', remote_command)
        self.assertIn(".diarchy-portal-stage-", remote_command)
        self.assertIn(".rsync-partial", remote_command)

    def test_list_includes_user_dotfiles_but_excludes_portal_internals(self) -> None:
        outbox = self.home / "Drop" / "to-mac"
        outbox.mkdir(parents=True)
        (outbox / ".user-notes").write_text("notes")
        (outbox / ".diarchy-portal-stage-dead").write_text("partial")
        (outbox / ".portal-stage-dead").write_text("partial")
        (outbox / ".rsync-partial").mkdir()

        result = run_file(["list"], "exec-local-list")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decode_name_field(result.stdout), [".user-notes"])

    def test_list_escapes_tabs_newlines_and_prevents_forged_records(self) -> None:
        result = run_file(["list"], "encoded-weird-list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("name="), 3)
        self.assertEqual(
            self.decode_name_field(result.stdout),
            ["tab\tname.txt", "line\nname.txt", "forged\nname=evil bytes=1 mtime=1970-01-01T00:00:00Z"],
        )
        self.assertNotIn("name=evil", result.stdout)
        self.assertEqual(len(self.ssh_entries()), 1)

    def test_list_percent_encodes_non_utf8_bytes(self) -> None:
        result = run_file(["list"], "encoded-raw-list")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decode_name_field_bytes(result.stdout), [b"raw-\xff.bin"])
        self.assertIn("name=raw-%FF.bin", result.stdout)
        self.assertEqual(len(self.ssh_entries()), 1)

    def test_list_excludes_direct_symlinks_without_following_them(self) -> None:
        result = run_file(["list"], "visible-hidden-nested")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("path.is_symlink()", self.ssh_entries()[0]["argv"][-1])
        self.assertNotIn("linked.txt", result.stdout)

    def test_dry_run_executes_neither_ssh_nor_rsync(self) -> None:
        result = run_file(["send", "--dry-run", self.source], "created")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.execution_log.exists())
        self.assertIn("rsync", result.stdout)

    def test_get_dry_run_executes_neither_rsync_nor_local_staging(self) -> None:
        result = run_file(["get", "--dry-run", "download.txt"], "download-ok")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.execution_log.exists())
        self.assertFalse(self.download_dir.exists())
        self.assertIn("rsync", result.stdout)
        self.assertIn("route=lan", result.stdout)

    def test_get_rejects_path_components_but_allows_shell_metacharacter_basenames(self) -> None:
        for name in ("../secret", "nested/file.txt"):
            result = run_file(["get", name], "created")
            self.assertEqual(result.returncode, 64, name)
        result = run_file(["get", "x';touch pwn;#.txt"], "download-ok")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.tmpdir / "pwn").exists())
        self.assertIn("diarchy-test:~/Drop/to-mac/'x'\\'';touch pwn;#.txt'", self.rsync_argv())

    def test_send_quotes_apostrophes_and_metacharacters_without_side_effects(self) -> None:
        result = run_file(["send", self.apostrophe_source], "apostrophe-metachar")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.tmpdir / "pwn").exists())
        self.assertIn("status=ok", result.stdout)
        self.assertIn("name=x%27%3Btouch%20pwn%3B%23.txt", result.stdout)
        self.assertIn("diarchy-test:~/Drop/from-mac/'x'\\'';touch pwn;#.txt'", self.rsync_argv())

    def test_send_status_escapes_tabs_and_newlines_without_forgery(self) -> None:
        result = run_file(["send", self.tab_newline_source], "tab-newline")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("send "), 1)
        self.assertNotIn("\nline two", result.stdout)
        self.assertEqual(self.decode_name_field(result.stdout), ["line\tone\nline two.txt"])

    def test_send_status_percent_encodes_non_utf8_bytes(self) -> None:
        result = run_file(["send", self.raw_source], "created")
        self.assertRegex(result.stderr, r"route=lan host=192\.0\.2\.10 interface=en0 loss_pct=0\.0 median_ms=1\.000")
        self.assertIn("name=raw-%FF.bin", result.stdout)
        self.assertIn("status=failed", result.stdout)
        self.assertEqual(self.decode_name_field_bytes(result.stdout), [b"raw-\xff.bin"])

    def test_multiple_send_items_aggregate_status(self) -> None:
        result = run_file(["send", self.source, self.space_source], "multi-mixed")
        self.assertEqual(result.returncode, 1)
        self.assertIn("name=source.txt", result.stdout)
        self.assertIn("status=ok", result.stdout)
        self.assertIn("name=file%20with%20spaces.txt", result.stdout)
        self.assertIn("status=failed", result.stdout)
        self.assertEqual(len(self.rsync_entries()), 2)

    def test_send_directory_accepts_directory_itemization(self) -> None:
        source_dir = self.tmpdir / "source-dir"
        source_dir.mkdir()
        (source_dir / "nested.txt").write_text("nested")

        result = run_file(["send", str(source_dir)], "directory-created")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("name=source-dir", result.stdout)
        self.assertIn("status=ok", result.stdout)
        self.assertIn(f"{source_dir}/", self.rsync_argv())

    def xattr_entries(self) -> list[dict[str, object]]:
        return [entry for entry in self.log_entries() if entry["tool"] == "xattr"]

    def test_sec17_send_refuses_top_level_symlink(self) -> None:
        link_to_dir = self.tmpdir / "link-to-dir"
        link_to_dir.symlink_to(self.tmpdir)
        link_to_file = self.tmpdir / "link-to-file"
        link_to_file.symlink_to(self.source)
        dangling = self.tmpdir / "dangling"
        dangling.symlink_to(self.tmpdir / "missing")
        for link in (link_to_dir, link_to_file, dangling):
            for extra in ([], ["--dry-run"]):
                with self.subTest(link=link.name, extra=extra):
                    result = run_file(["send", *extra, str(link)], "created")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("status=failed", result.stdout)
        self.assertEqual(self.rsync_entries(), [])

    def test_sec06_get_limits_item_size(self) -> None:
        run_file(["get", "big.bin"], "download-ok")
        self.assertIn("--max-size=2147483648", self.rsync_argv())

    def test_sec18_get_passes_safe_links_to_rsync(self) -> None:
        run_file(["get", "folder"], "download-directory")
        self.assertIn("--safe-links", self.rsync_argv())

    def test_sec18_safe_links_keeps_hostile_link_out_of_stage(self) -> None:
        real_rsync = Path("/usr/bin/rsync")
        if not real_rsync.exists():
            self.skipTest("rsync is not installed")
        run_file(["get", "folder"], "download-directory")
        flags = self.rsync_argv()
        flags = flags[: flags.index("-e")]
        outside = self.tmpdir / "outside"
        outside.mkdir()
        remote_tree = self.tmpdir / "remote-tree"
        remote_tree.mkdir()
        (remote_tree / "link").symlink_to(outside)
        (remote_tree / "file.txt").write_text("data")
        stage = self.tmpdir / "stage"
        stage.mkdir()
        subprocess.run([str(real_rsync), *flags, "--", f"{remote_tree}/", f"{stage}/"], check=False, capture_output=True)
        self.assertFalse(os.path.lexists(stage / "link"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_sec06_get_sets_quarantine_on_published_file_and_directory(self) -> None:
        for name, scenario in (("downloaded.txt", "download-ok"), ("folder", "download-directory")):
            with self.subTest(name=name):
                self.execution_log.unlink(missing_ok=True)
                result = run_file(["get", name], scenario)
                self.assertEqual(result.returncode, 0, result.stderr)
                entries = self.xattr_entries()
                self.assertEqual(len(entries), 1)
                argv = entries[0]["argv"]
                self.assertIn("com.apple.quarantine", argv)
                value = argv[argv.index("com.apple.quarantine") + 1]
                self.assertRegex(value, r"^0081;[0-9a-f]+;Diarchy;$")
                self.assertEqual(argv[-1], str(self.download_dir / name))
                self.assertTrue(entries[0]["exists"], "quarantine must run after publish")

    def test_sec06_quarantine_failure_fails_get_and_removes_item(self) -> None:
        result = self.run_file(["get", "downloaded.txt"], "download-ok", extra_env={"FAKE_XATTR_EXIT": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status=failed", result.stdout)
        self.assertFalse((self.download_dir / "downloaded.txt").exists())

    @unittest.skipUnless(Path("/usr/bin/xattr").exists() and os.uname().sysname == "Darwin", "needs macOS xattr")
    def test_sec06_real_xattr_marks_received_file_quarantined(self) -> None:
        result = self.run_file(["get", "downloaded.txt"], "download-ok", extra_env={"XATTR_BIN": "/usr/bin/xattr"})
        self.assertEqual(result.returncode, 0, result.stderr)
        listing = subprocess.run(
            ["/usr/bin/xattr", str(self.download_dir / "downloaded.txt")], capture_output=True, text=True, check=True
        ).stdout
        self.assertIn("com.apple.quarantine", listing)

    def test_rsync_transport_preserves_route_alias_and_selected_ssh_options(self) -> None:
        run_file(["send", "--route", "lan", self.source], "created")
        argv = self.rsync_argv()
        self.assertIn("-e", argv)
        transport = argv[argv.index("-e") + 1]
        self.assertIn(str(self.bin_dir / "ssh"), transport)
        self.assertIn("ControlMaster=no", transport)
        self.assertIn("ControlPath=none", transport)
        self.assertIn("PermitLocalCommand=no", transport)
        self.assertIn("ClearAllForwardings=yes", transport)
        self.assertIn("StrictHostKeyChecking=yes", transport)
        self.assertIn("UserKnownHostsFile=", transport)
        self.assertIn(f"HostName={LAN_HOST}", transport)
        self.assertIn(f"Port={SSH_PORT}", transport)
        self.assertTrue(any(arg.startswith("diarchy-test:") for arg in argv), argv)

    def test_send_uses_exact_remote_drop_path_with_safe_remote_quoting(self) -> None:
        result = run_file(["send", self.space_source, self.dash_source], "created")
        self.assertEqual(result.returncode, 0, result.stderr)
        remote_args = [entry["argv"][-1] for entry in self.rsync_entries()]
        self.assertIn("diarchy-test:~/Drop/from-mac/'file with spaces.txt'", remote_args)
        self.assertIn("diarchy-test:~/Drop/from-mac/-leading-dash.txt", remote_args)
        for entry in self.rsync_entries():
            self.assertIn("--", entry["argv"])

    def test_get_uses_exact_remote_drop_path_chmods_download_to_0600(self) -> None:
        result = run_file(["get", "downloaded file.txt"], "download-ok")
        target = self.home / "Drop" / "from-host" / "downloaded file.txt"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(target.exists())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        argv = self.rsync_argv()
        self.assertIn("diarchy-test:~/Drop/to-mac/'downloaded file.txt'", argv)
        self.assertTrue(argv[-1].startswith(str(self.home / "Drop" / "from-host" / ".diarchy-file-get.")))

    def test_metadata_only_failures_are_failed_not_successful(self) -> None:
        result = run_file(["list"], "metadata-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("list failed", result.stderr)


    def test_rsync_itemized_parser_rejects_diagnostic_metadata_delete_malformed_mixed_and_nonzero(self) -> None:
        for scenario in (
            "diagnostic-only",
            "metadata-only",
            "delete-record",
            "malformed-itemized",
            "mixed-output",
            "nonzero-with-valid-output",
        ):
            result = run_file(["send", self.source], scenario)
            self.assertEqual(result.returncode, 1, scenario)
            self.assertIn("status=failed", result.stdout, scenario)

if __name__ == "__main__":
    unittest.main()
