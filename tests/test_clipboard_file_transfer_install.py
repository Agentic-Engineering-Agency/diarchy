from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import sys
import textwrap
import time
import unittest
from pathlib import Path

HOST_PYTHON_SKIP_REASON = "host installer needs Python 3.11 (Omarchy host)"
needs_host_python = unittest.skipIf(sys.version_info < (3, 11), HOST_PYTHON_SKIP_REASON)

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client"
HOST = ROOT / "host"
CLIENT_INSTALLER = CLIENT / "install-clipboard-file-transfer.sh"
HOST_INSTALLER = HOST / "install-clipboard-file-transfer.sh"
CLIENT_DOCTOR = CLIENT / "diarchy-clipboard-file-transfer-client-doctor"
HOST_DOCTOR = HOST / "diarchy-file-transfer-host-doctor"
APPLESCRIPT = CLIENT / "Diarchy Drop.applescript"
DROP_BUNDLE_ID = "dev.sebastiangmz.diarchy-drop"
FILE_PORTAL_BUNDLE_ID = "dev.sebastiangmz.diarchy-file-portal"
FILE_PORTAL_SWIFT = CLIENT / "Diarchy File Portal.swift"
FILE_PORTAL_STAGING_SWIFT = CLIENT / "File Portal Staging.swift"
FILE_PORTAL_RECEIVER = CLIENT / "diarchy-file-portal-receiver"
FILE_PORTAL_SENDER = CLIENT / "diarchy-file-portal-send"
HOST_FILE_PORTAL = HOST / "diarchy-file-portal"
HOST_FILE_PORTAL_REMOTE = HOST / "diarchy-file-portal-remote"
HOST_FILE_PORTAL_INGEST = HOST / "diarchy-file-portal-ingest"
HOST_FILE_PORTAL_UNIT = HOST / "systemd" / "diarchy-file-portal.service"

_ACTIVE_CASE: "ClipboardFileTransferInstallTests | None" = None


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "missing"
    for node in sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix()):
        rel = node.relative_to(path).as_posix().encode()
        st = node.lstat()
        digest.update(rel + b"\0")
        digest.update(oct(stat.S_IMODE(st.st_mode)).encode() + b"\0")
        if node.is_symlink():
            digest.update(b"L" + os.readlink(node).encode())
        elif node.is_file():
            digest.update(b"F" + node.read_bytes())
        elif node.is_dir():
            digest.update(b"D")
    return digest.hexdigest()


def run_installer(side: str, args: list[str], home: Path) -> subprocess.CompletedProcess[str]:
    if _ACTIVE_CASE is None:
        raise RuntimeError("run_installer requires an active ClipboardFileTransferInstallTests")
    return _ACTIVE_CASE.run_installer(side, args, home)


def run_doctor(side: str, home: Path) -> subprocess.CompletedProcess[str]:
    if _ACTIVE_CASE is None:
        raise RuntimeError("run_doctor requires an active ClipboardFileTransferInstallTests")
    return _ACTIVE_CASE.run_doctor(side, home)


class ClipboardFileTransferInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        global _ACTIVE_CASE
        _ACTIVE_CASE = self
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._clear_active)
        self.tmpdir = Path(self.temp.name)
        self.bin_dir = self.tmpdir / "tools"
        self.bin_dir.mkdir()
        self.log = self.tmpdir / "tool-argv.jsonl"
        self.home = self.tmpdir / "home"
        self.home.mkdir()
        self.protected_tree = self.home / ".config"
        (self.protected_tree / "sunshine").mkdir(parents=True)
        (self.protected_tree / "sunshine" / "sunshine.conf").write_text("sunshine-user-bytes\n")
        (self.protected_tree / "sunshine" / "apps.json").write_text("sunshine-apps-user-bytes\n")
        (self.protected_tree / "lan-mouse").mkdir()
        (self.protected_tree / "lan-mouse" / "config.toml").write_text("lan-mouse-user-bytes\n")
        protected_units = self.protected_tree / "systemd" / "user"
        protected_units.mkdir(parents=True)
        for unit_name in (
            "diarchy-kvm.service",
            "diarchy-display.service",
            "diarchy-sunshine.service",
        ):
            (protected_units / unit_name).write_text(f"{unit_name}-user-bytes\n")
        self.home_with_foreign_bundle = self.tmpdir / "foreign-home"
        self.home_with_foreign_bundle.mkdir()
        self._make_bundle(self.home_with_foreign_bundle / "Applications" / "Diarchy Drop.app", "com.example.foreign")
        self._write_tool_stubs()
        self.diarchy_config = self.tmpdir / "diarchy.conf"
        self.diarchy_config.write_text(
            "# Managed by Diarchy\n"
            "DIARCHY_SSH_USER=labuser\n"
            "DIARCHY_LAN_HOST=192.0.2.10\n"
            "DIARCHY_TAILSCALE_HOST=100.64.0.10\n"
            "DIARCHY_HOST_KEY_ALIAS=diarchy-test-host\n"
        )
        pinned_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        (self.tmpdir / "known_hosts").write_text(
            "".join(f"{alias} {pinned_key}" for alias in ("diarchy-test-host", "100.64.0.10", "192.0.2.10"))
        )
        self._write_python_gi_stub()

    def _clear_active(self) -> None:
        global _ACTIVE_CASE
        if _ACTIVE_CASE is self:
            _ACTIVE_CASE = None

    def _write_executable(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
        return path

    def _write_tool_stubs(self) -> None:
        self.uname = self._write_executable(
            "uname",
            "#!/usr/bin/env bash\n[[ ${1:-} == -s ]] && { printf 'Darwin\\n'; exit 0; }\n[[ ${1:-} == -m ]] && { printf 'arm64\\n'; exit 0; }\nprintf 'Darwin\\n'\n",
        )
        self.osacompile = self._write_executable(
            "osacompile",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, shutil, sys
                from pathlib import Path
                Path(os.environ["TOOL_LOG"]).open("a").write(json.dumps({"tool":"osacompile","argv":sys.argv[1:]}) + "\\n")
                if os.environ.get("FAIL_TOOL") == "osacompile":
                    raise SystemExit(42)
                out = Path(sys.argv[sys.argv.index("-o") + 1])
                source = Path(sys.argv[-1])
                if out.exists():
                    shutil.rmtree(out)
                (out / "Contents").mkdir(parents=True)
                (out / "Contents" / "Info.plist").write_text("CFBundleIdentifier=compiled.placeholder\\n")
                resources = out / "Contents" / "Resources"
                resources.mkdir()
                resources.joinpath("main.scpt").write_text(source.read_text())
                marker = os.environ.get("DIARCHY_STAGE_MARKER")
                if marker:
                    resources.joinpath("stage.txt").write_text(marker + "\\n")
                """
            ),
        )
        self.swiftc = self._write_executable(
            "swiftc",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, sys
                from pathlib import Path
                Path(os.environ["TOOL_LOG"]).open("a").write(json.dumps({"tool":"swiftc","argv":sys.argv[1:]}) + "\\n")
                if os.environ.get("FAIL_TOOL") == "swiftc":
                    raise SystemExit(47)
                out = Path(sys.argv[sys.argv.index("-o") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("#!/usr/bin/env bash\\nexit 0\\n")
                out.chmod(0o755)
                """
            ),
        )
        self.codesign = self._write_executable(
            "codesign",
            "#!/usr/bin/env python3\nimport json, os, sys\nopen(os.environ['TOOL_LOG'], 'a').write(json.dumps({'tool':'codesign','argv':sys.argv[1:]}) + '\\n')\nraise SystemExit(43 if os.environ.get('FAIL_TOOL') == 'codesign' else 0)\n",
        )
        self.plistbuddy = self._write_executable(
            "PlistBuddy",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import os, re, sys
                from pathlib import Path
                command = sys.argv[sys.argv.index("-c") + 1]
                plist = Path(sys.argv[-1])
                if os.environ.get("FAIL_TOOL") == "plistbuddy" and command.startswith(("Set ", "Add ")):
                    raise SystemExit(44)
                data = plist.read_text() if plist.exists() else ""
                def current(key):
                    for line in data.splitlines():
                        if line.startswith(key + "="):
                            return line.split("=", 1)[1]
                    key_match = re.search(r"<key>" + re.escape(key) + r"</key>\\s*(?:<string>(.*?)</string>|<(true|false)/>)", data, re.S)
                    if key_match:
                        return key_match.group(1) if key_match.group(1) is not None else key_match.group(2)
                    raise SystemExit(1)
                if command.startswith("Print :"):
                    print(current(command.split(":", 1)[1]))
                elif command.startswith("Set :CFBundleIdentifier ") or command.startswith("Add :CFBundleIdentifier string "):
                    value = command.rsplit(" ", 1)[1]
                    lines = [line for line in data.splitlines() if not line.startswith("CFBundleIdentifier=")]
                    lines.append("CFBundleIdentifier=" + value)
                    plist.write_text("\\n".join(lines) + "\\n")
                else:
                    raise SystemExit(64)
                """
            ),
        )
        self.ping = self._write_executable("ping", "#!/usr/bin/env bash\nprintf '4 packets transmitted, 4 packets received, 0.0%% packet loss\\n64 bytes from x: icmp_seq=1 ttl=64 time=1.0 ms\\n'\n")
        self.route = self._write_executable("route", "#!/usr/bin/env bash\nprintf ' interface: en0\\n'\n")
        self.nc = self._write_executable("nc", "#!/usr/bin/env bash\n[[ ${FAKE_ROUTE_DOWN:-0} == 1 ]] && exit 1\nexit 0\n")
        self.ssh = self._write_executable(
            "ssh",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, sys
                from pathlib import Path
                argv = sys.argv[1:]
                open(os.environ['TOOL_LOG'], 'a').write(json.dumps({'tool':'ssh','argv':argv}) + '\\n')
                required = {
                    'BatchMode=yes',
                    'ConnectTimeout=5',
                    'PermitLocalCommand=no',
                    'ClearAllForwardings=yes',
                    'ControlMaster=no',
                    'ControlPath=none',
                    'StrictHostKeyChecking=yes',
                }
                forbidden = {'StrictHostKeyChecking=accept-new', 'UpdateHostKeys=no'}
                options = {argv[index + 1] for index, value in enumerate(argv[:-1]) if value == '-o'}
                if not required.issubset(options) or forbidden.intersection(options):
                    Path(os.environ['HOME'], 'ssh-side-effect').write_text('missing hardening\\n')
                    raise SystemExit(66)
                raise SystemExit(1 if os.environ.get('FAKE_SSH_DOWN') == '1' else 0)
                """
            ),
        )
        self.wl_paste = self._write_executable("wl-paste", "#!/usr/bin/env bash\nprintf 'host clipboard'\n")
        self.wl_copy = self._write_executable("wl-copy", "#!/usr/bin/env bash\ncat >/dev/null\n")
        self.rsync = self._write_executable("rsync", "#!/usr/bin/env bash\nexit 0\n")
        self.pbcopy = self._write_executable("pbcopy", "#!/usr/bin/env bash\ncat >/dev/null\n")
        self.pbpaste = self._write_executable("pbpaste", "#!/usr/bin/env bash\nprintf 'mac clipboard'\n")
        self.notify_send = self._write_executable("notify-send", "#!/usr/bin/env bash\nexit 0\n")
        self.python_no_gi = self._write_executable(
            "python-no-gi",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import sys
                code = sys.argv[sys.argv.index("-c") + 1]
                if "sys.version_info" in code:
                    raise SystemExit(0)
                if "import gi" in code:
                    raise SystemExit(1)
                raise SystemExit(0)
                """
            ),
        )
        self.systemctl = self._write_executable(
            "systemctl",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, sys
                from pathlib import Path
                argv = sys.argv[1:]
                Path(os.environ["TOOL_LOG"]).open("a").write(json.dumps({"tool":"systemctl","argv":argv}) + "\\n")
                joined = " ".join(argv)
                if "sshd.service" in argv and os.environ.get("FAKE_SSHD_DOWN", "0") == "1":
                    raise SystemExit(1)
                if "diarchy-kvm.service" in argv and os.environ.get("FAKE_KVM_SERVICE_DOWN", "0") == "1":
                    raise SystemExit(1)
                if argv == ["--user", "restart", "diarchy-file-portal.service"] and os.environ.get("FAKE_FAIL_PORTAL_RESTART", "0") == "1":
                    raise SystemExit(47)
                if "diarchy-file-portal.service" in argv and os.environ.get("FAKE_FILE_PORTAL_SERVICE_DOWN", "0") == "1":
                    raise SystemExit(1)
                if "--user" in argv and "is-enabled" in argv and os.environ.get("FAKE_FILE_PORTAL_SERVICE_DISABLED", "0") == "1":
                    raise SystemExit(1)
                if argv == ["--user", "is-active", "--quiet", "diarchy-file-portal.service"] and os.environ.get("FAKE_FILE_PORTAL_SERVICE_INACTIVE", "0") == "1":
                    raise SystemExit(1)
                raise SystemExit(0)
                """
            ),
        )
        self.mv = self._write_executable(
            "mv",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, shutil, sys
                from pathlib import Path
                Path(os.environ["TOOL_LOG"]).open("a").write(json.dumps({"tool":"mv","argv":sys.argv[1:]}) + "\\n")
                if os.environ.get("FAIL_TOOL") == "final-move":
                    raise SystemExit(45)
                shutil.move(sys.argv[1], sys.argv[2])
                """
            ),
        )
        self.mkdir = self._write_executable(
            "mkdir",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import os, subprocess, sys, time
                from pathlib import Path
                argv = sys.argv[1:]
                lock_args = [
                    Path(value)
                    for value in argv
                    if value.endswith((".Diarchy Drop.install.lock", ".diarchy-file-transfer.install.lock"))
                ]
                if lock_args and os.environ.get("DIARCHY_INSTALL_PROCESS") == "B":
                    Path(os.environ["B_LOCK_WAITING"]).write_text("waiting\\n")
                    allow = Path(os.environ["ALLOW_B_LOCK"])
                    deadline = time.monotonic() + 30
                    while not allow.exists():
                        if time.monotonic() > deadline:
                            raise SystemExit("timed out waiting to acquire B lock")
                        time.sleep(0.01)
                raise SystemExit(subprocess.run(["/bin/mkdir", *argv]).returncode)
                """
            ),
        )
        self.install = self._write_executable(
            "install",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, subprocess, sys
                from pathlib import Path
                argv = sys.argv[1:]
                Path(os.environ["TOOL_LOG"]).open("a").write(json.dumps({"tool":"install","argv":argv}) + "\\n")
                fail_after = os.environ.get("FAIL_COMMIT_AFTER")
                if os.environ.get("DIARCHY_INSTALL_PHASE") == "commit" and fail_after:
                    counter = Path(os.environ["TMPDIR"]) / ("install-commit-count-" + os.environ.get("HOME", "").replace("/", "_"))
                    count_text = counter.read_text() if counter.exists() else "0"
                    count = int(count_text or "0") + 1
                    counter.write_text(str(count))
                    if count == int(fail_after):
                        for marker in os.environ.get("ROLLBACK_SIBLING_MARKERS", "").split(os.pathsep):
                            if marker:
                                Path(marker).parent.mkdir(parents=True, exist_ok=True)
                                Path(marker).write_text("sibling-after-snapshot\\n")
                        raise SystemExit(46)
                raise SystemExit(subprocess.run(["/usr/bin/install", *argv]).returncode)
                """
            ),
        )
        self.atomic_app_replace = self._write_executable(
            "atomic-app-replace",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import ctypes, json, os, sys, threading, time
                from pathlib import Path
                old_path, live_path = map(Path, sys.argv[1:])
                log = Path(os.environ["TOOL_LOG"])
                entry = {
                    "tool": "atomic-app-replace",
                    "argv": sys.argv[1:],
                    "live_exists_before": live_path.exists(),
                    "live_info_before": (live_path / "Contents" / "Info.plist").read_text() if (live_path / "Contents" / "Info.plist").exists() else None,
                    "stage_info_before": (old_path / "Contents" / "Info.plist").read_text() if (old_path / "Contents" / "Info.plist").exists() else None,
                }
                log.open("a").write(json.dumps(entry) + "\\n")
                if os.environ.get("FAIL_TOOL") == "final-move":
                    raise SystemExit(45)

                observations_path = os.environ.get("PUBLICATION_OBSERVATIONS")
                if live_path.name != "Diarchy Drop.app":
                    observations_path = None
                stop = False
                def observe():
                    observations = []
                    while not stop:
                        info = live_path / "Contents" / "Info.plist"
                        marker = live_path / "Contents" / "Resources" / "stage.txt"
                        observations.append({
                            "exists": live_path.exists(),
                            "is_dir": live_path.is_dir(),
                            "info": info.read_text() if info.exists() else None,
                            "marker": marker.read_text() if marker.exists() else None,
                        })
                        time.sleep(0.002)
                    observations_path and Path(observations_path).write_text(json.dumps(observations))

                thread = None
                if observations_path:
                    thread = threading.Thread(target=observe)
                    thread.start()

                ready = os.environ.get("PUBLICATION_SWAP_READY")
                go = os.environ.get("PUBLICATION_SWAP_GO")
                if ready:
                    Path(ready).write_text("ready\\n")
                if go:
                    deadline = time.monotonic() + 10
                    while not Path(go).exists():
                        if time.monotonic() > deadline:
                            raise SystemExit("timed out waiting to swap")
                        time.sleep(0.01)
                elif observations_path:
                    time.sleep(0.05)

                libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
                result = libc.renameatx_np(-2, os.fsencode(old_path), -2, os.fsencode(live_path), 0x00000002)
                stop = True
                if thread:
                    thread.join()
                if result != 0:
                    error = ctypes.get_errno()
                    raise OSError(error, os.strerror(error), str(live_path))
                """
            ),
        )

    def _write_python_gi_stub(self) -> None:
        package = self.tmpdir / "python-stubs" / "gi"
        repository = package / "repository"
        repository.mkdir(parents=True)
        (package / "__init__.py").write_text(
            "def require_version(name, version):\n"
            "    allowed = {('Gtk', '3.0'), ('Gdk', '3.0'), ('GtkLayerShell', '0.1')}\n"
            "    if (name, version) not in allowed:\n"
            "        raise ValueError((name, version))\n"
        )
        (repository / "__init__.py").write_text(
            "class Gdk: pass\n"
            "class Gtk: pass\n"
            "class GtkLayerShell: pass\n"
        )
        self.pythonpath = str(package.parent)

    def _make_bundle(self, path: Path, bundle_id: str, *, marker: str | None = None) -> None:
        (path / "Contents").mkdir(parents=True)
        (path / "Contents" / "Info.plist").write_text(f"CFBundleIdentifier={bundle_id}\n")
        if marker is not None:
            resources = path / "Contents" / "Resources"
            resources.mkdir()
            (resources / "stage.txt").write_text(marker + "\n")

    def _env(self, home: Path, *, ssh_down: bool = False, fail_tool: str | None = None, rsync_bin: str | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("DIARCHY_")} | {
            "HOME": str(home),
            "TMPDIR": str(self.tmpdir),
            # Apple's /usr/bin/python3 writes its bytecode cache under $HOME/Library/Caches, which would change the
            # fake home that the symlink-refusal tests compare before and after the installer runs.
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": str(self.bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "TOOL_LOG": str(self.log),
            "APPLICATIONS_DIR": str(home / "Applications"),
            "BIN_DIR": str(home / ".local" / "bin"),
            "UNAME_BIN": str(self.uname),
            "OSACOMPILE_BIN": str(self.osacompile),
            "CODESIGN_BIN": str(self.codesign),
            "PLIST_BUDDY_BIN": str(self.plistbuddy),
            "MV_BIN": str(self.mv),
            "SWIFTC_BIN": str(self.swiftc),
            "INSTALL_BIN": str(self.install),
            "ATOMIC_APP_REPLACE_BIN": str(self.atomic_app_replace),
            "PING_BIN": str(self.ping),
            "ROUTE_BIN": str(self.route),
            "CONNECT_BIN": str(self.nc),
            "SSH_BIN": str(self.ssh),
            "WL_PASTE_BIN": str(self.wl_paste),
            "WL_COPY_BIN": str(self.wl_copy),
            "PBCOPY_BIN": str(self.pbcopy),
            "PBPASTE_BIN": str(self.pbpaste),
            "PYTHON_BIN": sys.executable,
            "SYSTEMCTL_BIN": str(self.systemctl),
            "NOTIFY_SEND_BIN": str(self.notify_send),
            "DIARCHY_CONFIG": str(self.diarchy_config),
            "DIARCHY_CONFIG_SH": str(CLIENT / "diarchy-config.sh"),
            "DIARCHY_SSH_ALIAS": "diarchy-test",
            "DIARCHY_SSH_PORT": "22",
            "FAKE_SSH_DOWN": "1" if ssh_down else "0",
            "FAKE_ROUTE_DOWN": "0",
            "PYTHONPATH": self.pythonpath + os.pathsep + os.environ.get("PYTHONPATH", ""),
        }
        if fail_tool is not None:
            env["FAIL_TOOL"] = fail_tool
        if rsync_bin is not None:
            env["RSYNC_BIN"] = rsync_bin
        else:
            env["RSYNC_BIN"] = str(self.rsync)
        return env

    def run_installer(self, side: str, args: list[str], home: Path, *, fail_tool: str | None = None, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        installer = CLIENT_INSTALLER if side == "client" else HOST_INSTALLER
        env = self._env(home, fail_tool=fail_tool)
        if extra_env:
            env |= extra_env
        return subprocess.run([str(installer), *args], env=env, text=True, capture_output=True, check=False)

    def run_doctor(self, side: str, home: Path, *, rsync_bin: str | None = None) -> subprocess.CompletedProcess[str]:
        doctor = CLIENT_DOCTOR if side == "client" else HOST_DOCTOR
        return subprocess.run([str(doctor)], env=self._env(home, ssh_down=(home.name == "ssh-down"), rsync_bin=rsync_bin), text=True, capture_output=True, check=False)

    def home_for(self, scenario: str) -> Path:
        home = self.tmpdir / scenario
        home.mkdir(exist_ok=True)
        if scenario not in {"missing-binary", "host-missing"}:
            run_installer("client", [], home)
            run_installer("host", [], home)
        if scenario == "wrong-mode":
            (home / ".local" / "bin" / "diarchy-file").chmod(0o644)
        return home

    def tool_entries(self, tool: str | None = None) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [entry for entry in entries if tool is None or entry["tool"] == tool]

    def client_artifact_tree(self, home: Path) -> Path:
        snapshot_root = self.tmpdir / f"{home.name}-snapshot"
        if snapshot_root.exists():
            shutil.rmtree(snapshot_root)
        for path in (
            home / ".local",
            home / "Drop",
            home / "Applications",
        ):
            if path.exists() or path.is_symlink():
                destination = snapshot_root / path.relative_to(home)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(path, destination, symlinks=True) if path.is_dir() and not path.is_symlink() else shutil.copy2(path, destination, follow_symlinks=False)
        return snapshot_root

    def assert_client_install_failure_rolls_back(
        self,
        fail_tool: str | None = None,
        *,
        fail_commit_after: int | None = None,
        extra_env: dict[str, str] | None = None,
        custom_bin: bool = False,
    ) -> None:
        scenario = (fail_tool or f"commit-{fail_commit_after}") + ("-custom-bin" if custom_bin else "")
        home = self.tmpdir / f"rollback-{scenario}"
        home.mkdir()
        bin_dir = self.tmpdir / f"{home.name}-custom-bin" if custom_bin else home / ".local" / "bin"
        self._make_bundle(home / "Applications" / "Diarchy Drop.app", DROP_BUNDLE_ID)
        existing_command = bin_dir / "diarchy-file"
        existing_command.parent.mkdir(parents=True)
        existing_command.write_text("old command\\n")
        existing_command.chmod(0o744)
        (home / "Drop" / "from-host").mkdir(parents=True)
        (home / "Drop" / "from-host" / "kept.txt").write_text("kept\\n")
        (home / "Drop" / "to-host").mkdir()
        sibling_markers = [
            home / "Drop" / "unrelated-after-snapshot.txt",
            home / "Applications" / "unrelated-after-snapshot.txt",
            bin_dir / "unrelated-after-snapshot.txt",
        ]
        exact_destinations = [
            bin_dir / "diarchy-clipboard-bridge",
            bin_dir / "diarchy-kvm",
            bin_dir / "diarchy-file",
            bin_dir / "diarchy-route.sh",
            bin_dir / "diarchy-config.sh",
            bin_dir / "diarchy-ssh",
            bin_dir / "diarchy-host-key",
            bin_dir / "diarchy-clipboard-file-transfer-client-doctor",
            bin_dir / "diarchy-file-portal-receiver",
            bin_dir / "diarchy-file-portal-send",
            home / "Drop" / "from-host",
            home / "Drop" / "to-host",
            home / "Applications" / "Diarchy Drop.app",
            home / "Applications" / "Diarchy File Portal.app",
        ]
        before = {path: (path.exists() or path.is_symlink(), tree_hash(path), stat.S_IMODE(path.lstat().st_mode) if path.exists() or path.is_symlink() else None) for path in exact_destinations}
        env = {"BIN_DIR": str(bin_dir), "ROLLBACK_SIBLING_MARKERS": os.pathsep.join(str(path) for path in sibling_markers)}
        if fail_commit_after is not None:
            env["FAIL_COMMIT_AFTER"] = str(fail_commit_after)
        if extra_env:
            env |= extra_env

        counter = self.tmpdir / "install-commit-count"
        if counter.exists():
            counter.unlink()
        result = self.run_installer("client", [], home, fail_tool=fail_tool, extra_env=env)

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        for path, (existed, digest, mode) in before.items():
            with self.subTest(path=path):
                if existed:
                    self.assertTrue(path.exists() or path.is_symlink(), path)
                    self.assertEqual(tree_hash(path), digest, result.stderr + result.stdout)
                    self.assertEqual(stat.S_IMODE(path.lstat().st_mode), mode)
                else:
                    self.assertFalse(path.exists() or path.is_symlink(), path)
        self.assertEqual(existing_command.read_text(), "old command\\n")
        self.assertEqual(stat.S_IMODE(existing_command.stat().st_mode), 0o744)
        for sibling in sibling_markers:
            if fail_commit_after is not None:
                self.assertEqual(sibling.read_text(), "sibling-after-snapshot\n")
        self.assertFalse(list((home / "Applications").glob(".Diarchy Drop.stage*")))
        self.assertFalse(list(self.tmpdir.glob("diarchy-drop-client.*")))

    def assert_host_install_failure_rolls_back(
        self,
        *,
        fail_commit_after: int | None = None,
        fail_restart: bool = False,
        service_initially_enabled: bool = True,
        service_initially_active: bool = True,
    ) -> None:
        scenario = f"host-commit-{fail_commit_after}" if fail_commit_after else "host-restart"
        home = self.tmpdir / f"rollback-{scenario}"
        home.mkdir()
        bin_dir = home / ".local" / "bin"
        from_mac = home / "Drop" / "from-mac"
        to_mac = home / "Drop" / "to-mac"
        unit_dir = home / ".config" / "systemd" / "user"
        for directory, mode in (
            (bin_dir, 0o750),
            (from_mac, 0o751),
            (to_mac, 0o710),
            (unit_dir, 0o755),
        ):
            directory.mkdir(parents=True)
            directory.chmod(mode)
        (from_mac / "kept.txt").write_text("from-mac kept\n")
        (to_mac / "kept.txt").write_text("to-mac kept\n")
        exact_files = [
            bin_dir / "diarchy-clipboard-remote",
            bin_dir / "diarchy-file-transfer-host-doctor",
            bin_dir / "diarchy-file-portal",
            bin_dir / "diarchy-file-portal-remote",
            bin_dir / "diarchy-file-portal-ingest",
            unit_dir / "diarchy-file-portal.service",
        ]
        for index, path in enumerate(exact_files):
            path.write_text(f"old host artifact {index}\n")
            path.chmod(0o740 + index)
        before_files = {
            path: (tree_hash(path), stat.S_IMODE(path.stat().st_mode))
            for path in exact_files
        }
        before_directories = {
            path: (tree_hash(path), stat.S_IMODE(path.stat().st_mode))
            for path in (from_mac, to_mac)
        }
        before_parent_modes = {
            path: stat.S_IMODE(path.stat().st_mode)
            for path in (bin_dir, unit_dir)
        }
        env = {
            "FAIL_COMMIT_AFTER": str(fail_commit_after or ""),
            "FAKE_FAIL_PORTAL_RESTART": "1" if fail_restart else "0",
            "FAKE_FILE_PORTAL_SERVICE_DISABLED": "0" if service_initially_enabled else "1",
            "FAKE_FILE_PORTAL_SERVICE_INACTIVE": "0" if service_initially_active else "1",
        }

        log_start = len(self.tool_entries("systemctl"))
        result = self.run_installer("host", [], home, extra_env=env)

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        for path, (digest, mode) in before_files.items():
            with self.subTest(path=path):
                self.assertEqual(tree_hash(path), digest)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        for path, (digest, mode) in before_directories.items():
            with self.subTest(path=path):
                self.assertEqual(tree_hash(path), digest)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        for path, mode in before_parent_modes.items():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        rollback_calls = [
            entry["argv"]
            for entry in self.tool_entries("systemctl")[log_start:]
        ]
        self.assertIn(["--user", "daemon-reload"], rollback_calls)
        expected_enabled = "enable" if service_initially_enabled else "disable"
        expected_active = "restart" if service_initially_active else "stop"
        self.assertIn(["--user", expected_enabled, "diarchy-file-portal.service"], rollback_calls)
        self.assertIn(["--user", expected_active, "diarchy-file-portal.service"], rollback_calls)

    def test_both_dry_runs_are_rootless_and_non_mutating(self) -> None:
        before = tree_hash(self.home)
        for side in ("client", "host"):
            result = run_installer(side, ["--dry-run"], self.home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("sudo", result.stdout.lower() + result.stderr.lower())
            self.assertNotIn("systemctl", result.stdout.lower() + result.stderr.lower())
            self.assertNotIn("launchagent", result.stdout.lower() + result.stderr.lower())
        self.assertEqual(tree_hash(self.home), before)

    def test_installers_are_idempotent_and_preserve_kvm_streaming_fixtures(self) -> None:
        protected = {
            path: tree_hash(path)
            for path in (
                self.protected_tree / "sunshine",
                self.protected_tree / "lan-mouse",
                self.protected_tree / "systemd/user/diarchy-kvm.service",
                self.protected_tree / "systemd/user/diarchy-display.service",
                self.protected_tree / "systemd/user/diarchy-sunshine.service",
            )
        }
        for side in ("client", "host"):
            with self.subTest(side=side):
                if side == "host" and sys.version_info < (3, 11):
                    self.skipTest(HOST_PYTHON_SKIP_REASON)
                first = run_installer(side, [], self.home)
                after_first = tree_hash(self.home)
                second = run_installer(side, [], self.home)
                self.assertEqual((first.returncode, second.returncode), (0, 0), first.stderr + second.stderr)
                self.assertEqual(tree_hash(self.home), after_first)
        for path, digest in protected.items():
            self.assertEqual(tree_hash(path), digest)

    @needs_host_python
    def test_installed_scripts_have_exact_reviewed_bytes_and_modes(self) -> None:
        result_client = run_installer("client", [], self.home)
        result_host = run_installer("host", [], self.home)
        self.assertEqual((result_client.returncode, result_host.returncode), (0, 0), result_client.stderr + result_host.stderr)
        expected = {
            self.home / ".local/bin/diarchy-clipboard-bridge": CLIENT / "diarchy-clipboard-bridge",
            self.home / ".local/bin/diarchy-kvm": CLIENT / "diarchy-kvm",
            self.home / ".local/bin/diarchy-file": CLIENT / "diarchy-file",
            self.home / ".local/bin/diarchy-route.sh": CLIENT / "diarchy-route.sh",
            self.home / ".local/bin/diarchy-config.sh": CLIENT / "diarchy-config.sh",
            self.home / ".local/bin/diarchy-ssh": CLIENT / "diarchy-ssh",
            self.home / ".local/bin/diarchy-host-key": CLIENT / "diarchy-host-key",
            self.home / ".local/bin/diarchy-clipboard-file-transfer-client-doctor": CLIENT_DOCTOR,
            self.home / ".local/bin/diarchy-file-portal-receiver": FILE_PORTAL_RECEIVER,
            self.home / ".local/bin/diarchy-file-portal-send": FILE_PORTAL_SENDER,
            self.home / ".local/bin/diarchy-clipboard-remote": HOST / "diarchy-clipboard-remote",
            self.home / ".local/bin/diarchy-file-transfer-host-doctor": HOST_DOCTOR,
            self.home / ".local/bin/diarchy-file-portal": HOST_FILE_PORTAL,
            self.home / ".local/bin/diarchy-file-portal-remote": HOST_FILE_PORTAL_REMOTE,
            self.home / ".local/bin/diarchy-file-portal-ingest": HOST_FILE_PORTAL_INGEST,
        }
        for installed, source in expected.items():
            with self.subTest(installed=installed.name):
                self.assertEqual(installed.read_bytes(), source.read_bytes())
                self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o755)
        unit = self.home / ".config/systemd/user/diarchy-file-portal.service"
        self.assertEqual(unit.read_bytes(), HOST_FILE_PORTAL_UNIT.read_bytes())
        self.assertEqual(stat.S_IMODE(unit.stat().st_mode), 0o644)
        self.assertIn("ExecStart=%h/.local/bin/diarchy-file-portal", unit.read_text())
        self.assertIn("WantedBy=graphical-session.target", unit.read_text())
        systemctl_argvs = [entry["argv"] for entry in self.tool_entries("systemctl")]
        self.assertIn(["--user", "daemon-reload"], systemctl_argvs)
        self.assertIn(["--user", "enable", "diarchy-file-portal.service"], systemctl_argvs)
        self.assertIn(["--user", "restart", "diarchy-file-portal.service"], systemctl_argvs)
        self.assertNotIn(["--user", "enable", "--now", "diarchy-file-portal.service"], systemctl_argvs)

    @needs_host_python
    def test_both_required_drop_paths_are_private(self) -> None:
        run_installer("client", [], self.home)
        run_installer("host", [], self.home)
        for drop in (self.home / "Drop" / "from-host", self.home / "Drop" / "from-mac", self.home / "Drop" / "to-mac"):
            with self.subTest(drop=drop):
                self.assertTrue(drop.is_dir())
                self.assertEqual(stat.S_IMODE(drop.stat().st_mode), 0o700)

    def test_project_code_never_directly_opens_poisoned_dot_ssh(self) -> None:
        poison = self.home / ".ssh"
        poison.mkdir()
        run_installer("client", [], self.home)
        run_doctor("client", self.home)
        self.assertFalse((poison / "project-accessed").exists())
        for artifact in (CLIENT_INSTALLER, HOST_INSTALLER, CLIENT_DOCTOR, HOST_DOCTOR):
            self.assertNotIn(".ssh", artifact.read_text())

    def test_client_liveness_uses_batchmode_ssh_via_route_contract(self) -> None:
        run_installer("client", [], self.home)
        result = run_doctor("client", self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(line.startswith("[ok]") for line in result.stdout.splitlines() if line))
        ssh_entries = self.tool_entries("ssh")
        self.assertTrue(ssh_entries)
        argv = ssh_entries[-1]["argv"]
        self.assertIn("-o", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("diarchy-test", argv)
        self.assertIn("true", argv)
        required_options = {
            "BatchMode=yes",
            "PermitLocalCommand=no",
            "ClearAllForwardings=yes",
            "ControlMaster=no",
            "ControlPath=none",
        }
        forbidden_options = {"StrictHostKeyChecking=accept-new", "UpdateHostKeys=no"}
        passed_options = {argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-o"}
        self.assertTrue(required_options.issubset(passed_options))
        self.assertTrue(forbidden_options.isdisjoint(passed_options))
        self.assertFalse((self.home / "ssh-side-effect").exists())

    @needs_host_python
    def test_host_doctor_has_no_outbound_liveness(self) -> None:
        run_installer("host", [], self.home)
        result = run_doctor("host", self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(line.startswith("[ok]") for line in result.stdout.splitlines() if line))
        self.assertEqual(self.tool_entries("ssh"), [])

    def test_client_refuses_foreign_drop_bundle(self) -> None:
        result = run_installer("client", [], self.home_with_foreign_bundle)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("foreign bundle", result.stderr.lower())

    def test_client_refuses_live_and_dangling_destination_symlinks(self) -> None:
        for scenario, target in (
            ("live-app-symlink", Path("Applications/Diarchy Drop.app")),
            ("dangling-app-symlink", Path("Applications/Diarchy Drop.app")),
            ("live-portal-app-symlink", Path("Applications/Diarchy File Portal.app")),
            ("dangling-portal-app-symlink", Path("Applications/Diarchy File Portal.app")),
            ("command-symlink", Path(".local/bin/diarchy-file")),
            ("receiver-symlink", Path(".local/bin/diarchy-file-portal-receiver")),
            ("drop-symlink", Path("Drop/from-host")),
        ):
            with self.subTest(scenario=scenario):
                home = self.tmpdir / scenario
                home.mkdir()
                symlink = home / target
                symlink.parent.mkdir(parents=True, exist_ok=True)
                if scenario.startswith("live"):
                    real = home / "real-target"
                    real.mkdir()
                    symlink.symlink_to(real)
                else:
                    symlink.symlink_to(home / "missing-target")
                before = tree_hash(home)
                result = run_installer("client", [], home)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlink", result.stderr.lower())
                self.assertEqual(tree_hash(home), before)

    @needs_host_python
    def test_host_refuses_live_and_dangling_destination_symlinks(self) -> None:
        for scenario, target in (
            ("live-host-portal-symlink", Path(".local/bin/diarchy-file-portal")),
            ("dangling-host-portal-remote-symlink", Path(".local/bin/diarchy-file-portal-remote")),
            ("dangling-host-service-symlink", Path(".config/systemd/user/diarchy-file-portal.service")),
        ):
            with self.subTest(scenario=scenario):
                home = self.tmpdir / scenario
                home.mkdir()
                symlink = home / target
                symlink.parent.mkdir(parents=True, exist_ok=True)
                if scenario.startswith("live"):
                    real = home / "real-target"
                    real.write_text("owned elsewhere\n")
                    symlink.symlink_to(real)
                else:
                    symlink.symlink_to(home / "missing-target")
                before = tree_hash(home)
                result = run_installer("host", [], home)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlink", result.stderr.lower())
                self.assertEqual(tree_hash(home), before)

    def test_client_install_rolls_back_after_each_injected_failure(self) -> None:
        for fail_tool in ("osacompile", "swiftc", "plistbuddy", "codesign", "final-move"):
            with self.subTest(fail_tool=fail_tool):
                self.assert_client_install_failure_rolls_back(fail_tool)
        for commit_operation in range(1, 12):
            with self.subTest(commit_operation=commit_operation):
                self.assert_client_install_failure_rolls_back(fail_commit_after=commit_operation)
        self.assert_client_install_failure_rolls_back(fail_commit_after=4, custom_bin=True)

    @needs_host_python
    def test_host_install_rolls_back_after_each_injected_failure(self) -> None:
        for commit_operation in range(1, 7):
            with self.subTest(commit_operation=commit_operation):
                self.assert_host_install_failure_rolls_back(
                    fail_commit_after=commit_operation,
                )
        self.assert_host_install_failure_rolls_back(
            fail_restart=True,
            service_initially_enabled=False,
            service_initially_active=False,
        )

    def test_client_recovers_install_lock_owned_by_dead_process(self) -> None:
        home = self.tmpdir / "client-stale-lock"
        lock_dir = home / "Applications" / ".Diarchy Drop.install.lock"
        lock_dir.mkdir(parents=True)
        (lock_dir / "owner").write_text("999999999\n")

        result = self.run_installer("client", [], home)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(lock_dir.exists())

    @needs_host_python
    def test_host_pretransaction_failure_releases_install_lock(self) -> None:
        home = self.tmpdir / "host-pretransaction-failure"
        home.mkdir()
        failing_mktemp = self._write_executable(
            "failing-host-mktemp",
            "#!/usr/bin/env bash\nexit 70\n",
        )

        failed = self.run_installer(
            "host",
            [],
            home,
            extra_env={"MKTEMP_BIN": str(failing_mktemp)},
        )

        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse((home / ".diarchy-file-transfer.install.lock").exists())
        retry = self.run_installer("host", [], home)
        self.assertEqual(retry.returncode, 0, retry.stderr)

    def test_waiting_failed_installer_rolls_back_to_state_snapshotted_after_lock(self) -> None:
        home = self.tmpdir / "concurrent-rollback"
        home.mkdir()
        self._make_bundle(home / "Applications" / "Diarchy Drop.app", DROP_BUNDLE_ID, marker="old")
        unrelated = home / "Applications" / "unrelated.txt"
        unrelated.write_text("unrelated-before\n")

        b_waiting = self.tmpdir / "b-waiting-for-lock"
        allow_b = self.tmpdir / "allow-b-lock"
        env_b = self._env(home) | {
            "DIARCHY_INSTALL_PROCESS": "B",
            "B_LOCK_WAITING": str(b_waiting),
            "ALLOW_B_LOCK": str(allow_b),
            "FAIL_COMMIT_AFTER": "2",
            "DIARCHY_STAGE_MARKER": "b-failed",
        }
        blocked = subprocess.Popen([str(CLIENT_INSTALLER)], env=env_b, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 10
        while not b_waiting.exists():
            if blocked.poll() is not None:
                out, err = blocked.communicate()
                self.fail(err + out)
            if time.monotonic() > deadline:
                blocked.kill()
                self.fail("timed out waiting for blocked installer to reach publication lock")
            time.sleep(0.01)

        committed = self.run_installer("client", [], home, extra_env={"DIARCHY_STAGE_MARKER": "a-committed"})
        self.assertEqual(committed.returncode, 0, committed.stderr)
        self.assertEqual((home / "Applications" / "Diarchy Drop.app" / "Contents" / "Resources" / "stage.txt").read_text(), "a-committed\n")

        allow_b.write_text("go\n")
        b_out, b_err = blocked.communicate(timeout=10)
        self.assertNotEqual(blocked.returncode, 0, b_err + b_out)
        app = home / "Applications" / "Diarchy Drop.app"
        self.assertEqual((app / "Contents" / "Resources" / "stage.txt").read_text(), "a-committed\n")
        self.assertEqual((app / "Contents" / "Info.plist").read_text(), f"CFBundleIdentifier={DROP_BUNDLE_ID}\n")
        self.assertEqual(unrelated.read_text(), "unrelated-before\n")
        self.assertFalse(list((home / "Applications").glob(".Diarchy Drop.stage*")))

    @needs_host_python
    def test_host_waiting_failed_installer_preserves_committed_install(self) -> None:
        home = self.tmpdir / "host-concurrent-rollback"
        home.mkdir()
        b_waiting = self.tmpdir / "host-b-waiting-for-lock"
        allow_b = self.tmpdir / "allow-host-b-lock"
        env_b = self._env(home) | {
            "DIARCHY_INSTALL_PROCESS": "B",
            "B_LOCK_WAITING": str(b_waiting),
            "ALLOW_B_LOCK": str(allow_b),
            "FAIL_COMMIT_AFTER": "2",
        }
        blocked = subprocess.Popen(
            [str(HOST_INSTALLER)],
            env=env_b,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 10
        while not b_waiting.exists():
            if blocked.poll() is not None:
                out, err = blocked.communicate()
                self.fail(err + out)
            if time.monotonic() > deadline:
                blocked.kill()
                self.fail("timed out waiting for host installer publication lock")
            time.sleep(0.01)

        committed = self.run_installer("host", [], home)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        installed = home / ".local" / "bin" / "diarchy-file-portal"
        committed_digest = tree_hash(installed)

        allow_b.write_text("go\n")
        b_out, b_err = blocked.communicate(timeout=10)
        self.assertNotEqual(blocked.returncode, 0, b_err + b_out)
        self.assertEqual(tree_hash(installed), committed_digest)
        self.assertFalse(list((home / ".local" / "bin").glob(".diarchy-file-transfer.install.*")))

    def test_client_doctor_refuses_tampered_route_without_execution(self) -> None:
        run_installer("client", [], self.home)
        marker = self.home / "route-executed"
        route = self.home / ".local" / "bin" / "diarchy-route.sh"
        route.write_text(f"#!/usr/bin/env bash\nprintf tampered > {marker!s}\nselect_diarchy_route() {{ return 0; }}\n")
        route.chmod(0o755)

        result = run_doctor("client", self.home)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reviewed digest", result.stderr.lower())
        self.assertFalse(marker.exists())

    def test_client_doctor_resolves_rsync_with_path_lookup(self) -> None:
        run_installer("client", [], self.home)
        available = self.run_doctor("client", self.home, rsync_bin="rsync")
        self.assertEqual(available.returncode, 0, available.stderr)
        self.assertIn("[ok] rsync is available", available.stdout)

        missing = self.run_doctor("client", self.home, rsync_bin="diarchy-rsync-missing")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("[fail] rsync was not found", missing.stderr)

    def test_installers_fail_before_mutation_when_prerequisite_is_missing(self) -> None:
        cases = (
            ("client", "PBCOPY_BIN", self.tmpdir / "missing-pbcopy", str(self.tmpdir / "missing-pbcopy")),
            ("client", "SWIFTC_BIN", self.tmpdir / "missing-swiftc", str(self.tmpdir / "missing-swiftc")),
            ("host", "WL_COPY_BIN", self.tmpdir / "missing-wl-copy", str(self.tmpdir / "missing-wl-copy")),
            ("host", "RSYNC_BIN", self.tmpdir / "missing-rsync", str(self.tmpdir / "missing-rsync")),
            ("host", "NOTIFY_SEND_BIN", self.tmpdir / "missing-notify-send", str(self.tmpdir / "missing-notify-send")),
            ("host", "PYTHON_BIN", self.python_no_gi, "PyGObject GTK 3"),
        )
        for side, variable, missing, expected_message in cases:
            with self.subTest(side=side, variable=variable):
                if variable == "PYTHON_BIN" and sys.version_info < (3, 11):
                    self.skipTest(HOST_PYTHON_SKIP_REASON)
                home = self.tmpdir / f"missing-{side}-{variable.lower()}"
                home.mkdir()
                before = tree_hash(home)

                result = self.run_installer(
                    side,
                    [],
                    home,
                    extra_env={variable: str(missing)},
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[fail]", result.stderr)
                self.assertIn(expected_message, result.stderr)
                self.assertEqual(tree_hash(home), before)

    def _client_config(self, name: str, body: str) -> Path:
        path = self.tmpdir / name
        path.write_text("# Managed by Diarchy\n" + body)
        return path

    def test_client_installer_without_config_skips_ssh_preflight_and_installs_support_files(self) -> None:
        home = self.tmpdir / "installer-no-config"
        home.mkdir()

        result = self.run_installer(
            "client",
            [],
            home,
            extra_env={
                "DIARCHY_CONFIG": str(self.tmpdir / "absent.conf"),
                "FAKE_SSH_DOWN": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("[info] Not configured yet; skipping SSH preflight", result.stdout)
        self.assertEqual(self.tool_entries("ssh"), [])
        for name in ("diarchy-config.sh", "diarchy-route.sh", "diarchy-ssh", "diarchy-host-key"):
            with self.subTest(name=name):
                installed = home / ".local" / "bin" / name
                self.assertEqual(installed.read_bytes(), (CLIENT / name).read_bytes())
                self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o755)

    def test_client_installer_runs_diarchy_ssh_style_preflight_when_config_loads(self) -> None:
        home = self.tmpdir / "installer-ssh-up"
        home.mkdir()

        result = self.run_installer("client", [], home)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("Not configured yet", result.stdout)
        ssh_entries = self.tool_entries("ssh")
        self.assertEqual(len(ssh_entries), 1)
        argv = ssh_entries[0]["argv"]
        options = {argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-o"}
        self.assertTrue(
            {
                "BatchMode=yes",
                "ConnectTimeout=5",
                "StrictHostKeyChecking=yes",
                "PermitLocalCommand=no",
                "ClearAllForwardings=yes",
                "ControlMaster=no",
                "ControlPath=none",
                "User=labuser",
                "HostName=192.0.2.10",
                "Port=22",
                "HostKeyAlias=diarchy-test-host",
            }.issubset(options),
            argv,
        )
        self.assertEqual(argv[-3:], ["diarchy-test", "--", "true"])
        self.assertNotIn("host", argv[:-3])

    def test_client_installer_skips_ssh_preflight_when_the_app_already_checks_it(self) -> None:
        home = self.tmpdir / "installer-ssh-skipped"
        home.mkdir()

        result = self.run_installer(
            "client",
            [],
            home,
            extra_env={"FAKE_SSH_DOWN": "1", "DIARCHY_SKIP_SSH_PREFLIGHT": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("[info] SSH preflight skipped (checked by the app)", result.stdout)
        self.assertEqual(self.tool_entries("ssh"), [])
        self.assertTrue((home / ".local" / "bin" / "diarchy-clipboard-bridge").exists())

    def test_client_installer_only_skips_ssh_preflight_for_the_exact_flag_value(self) -> None:
        home = self.tmpdir / "installer-ssh-not-skipped"
        home.mkdir()

        result = self.run_installer(
            "client",
            [],
            home,
            extra_env={"FAKE_SSH_DOWN": "1", "DIARCHY_SKIP_SSH_PREFLIGHT": "0"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSH pairing", result.stderr)

    def test_client_installer_requires_batch_ssh_pairing(self) -> None:
        home = self.tmpdir / "installer-ssh-down"
        home.mkdir()

        result = self.run_installer(
            "client",
            [],
            home,
            extra_env={"FAKE_SSH_DOWN": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSH pairing for labuser@192.0.2.10 (lan route) failed", result.stderr)
        self.assertIn("ssh-copy-id -p 22 labuser@192.0.2.10", result.stderr)
        self.assertNotIn("ssh-copy-id diarchy-test", result.stderr)
        self.assertFalse((home / ".local").exists())

    def test_client_installer_ssh_failure_names_tailscale_route_host_and_port(self) -> None:
        home = self.tmpdir / "installer-ssh-down-tailscale"
        home.mkdir()
        config = self._client_config(
            "tailscale-only.conf",
            "DIARCHY_SSH_USER=labuser\nDIARCHY_TAILSCALE_HOST=100.64.0.10\n",
        )

        result = self.run_installer(
            "client",
            [],
            home,
            extra_env={"DIARCHY_CONFIG": str(config), "DIARCHY_SSH_PORT": "2222", "FAKE_SSH_DOWN": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSH pairing for labuser@100.64.0.10 (tailscale route) failed", result.stderr)
        self.assertIn("ssh-copy-id -p 2222 labuser@100.64.0.10", result.stderr)
        self.assertFalse((home / ".local").exists())

    def test_client_installer_ssh_copy_id_hint_includes_identity_public_key_when_set(self) -> None:
        cases = (
            (
                "with-identity",
                "DIARCHY_SSH_USER=labuser\nDIARCHY_LAN_HOST=192.0.2.10\nDIARCHY_SSH_IDENTITY_FILE=/keys/id_lab\n",
                "ssh-copy-id -p 22 -i /keys/id_lab.pub labuser@192.0.2.10",
            ),
            (
                "without-identity",
                "DIARCHY_SSH_USER=labuser\nDIARCHY_LAN_HOST=192.0.2.10\n",
                "ssh-copy-id -p 22 labuser@192.0.2.10",
            ),
        )
        for name, body, expected in cases:
            with self.subTest(name=name):
                home = self.tmpdir / f"installer-hint-{name}"
                home.mkdir()
                config = self._client_config(f"installer-hint-{name}.conf", body)
                result = self.run_installer(
                    "client",
                    [],
                    home,
                    extra_env={"DIARCHY_CONFIG": str(config), "FAKE_SSH_DOWN": "1"},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                if name == "without-identity":
                    self.assertNotIn(" -i ", result.stderr)

    def test_client_installer_reports_unusable_config_and_skips_ssh_preflight(self) -> None:
        home = self.tmpdir / "installer-invalid-config"
        home.mkdir()
        config = self._client_config(
            "installer-invalid.conf",
            "DIARCHY_SSH_USER=labuser\nDIARCHY_LAN_HOST=-192.0.2.10\n",
        )

        result = self.run_installer(
            "client",
            [],
            home,
            extra_env={"DIARCHY_CONFIG": str(config), "FAKE_SSH_DOWN": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(
            "[info] diarchy.conf is not usable (DIARCHY_LAN_HOST is invalid); skipping SSH preflight",
            result.stdout,
        )
        self.assertNotIn("Not configured yet", result.stdout)
        self.assertNotIn("diarchy-config:", result.stdout)
        self.assertEqual(self.tool_entries("ssh"), [])
        self.assertTrue((home / ".local" / "bin" / "diarchy-ssh").is_file())

    def test_client_installer_fails_when_configured_routes_are_unreachable(self) -> None:
        home = self.tmpdir / "installer-route-down"
        home.mkdir()

        result = self.run_installer("client", [], home, extra_env={"FAKE_ROUTE_DOWN": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[fail] install-clipboard-file-transfer.sh:", result.stderr)
        self.assertIn("Host is unreachable on LAN and Tailscale", result.stderr)
        self.assertEqual(self.tool_entries("ssh"), [])
        self.assertFalse((home / ".local").exists())

    def test_client_installer_has_no_personal_ssh_alias_default(self) -> None:
        text = CLIENT_INSTALLER.read_text()
        self.assertNotIn("DIARCHY_SSH_ALIAS=", text)
        self.assertNotIn("ssh-copy-id host", text)

    @needs_host_python
    def test_host_installer_requires_active_sshd(self) -> None:
        home = self.tmpdir / "installer-sshd-down"
        home.mkdir()

        result = self.run_installer(
            "host",
            [],
            home,
            extra_env={"FAKE_SSHD_DOWN": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sshd", result.stderr.lower())
        self.assertFalse((home / ".local").exists())

    def test_doctors_fail_on_missing_binary_wrong_mode_and_unreachable_ssh(self) -> None:
        for scenario in ("missing-binary", "wrong-mode", "ssh-down"):
            with self.subTest(scenario=scenario):
                result = run_doctor("client", self.home_for(scenario))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[fail]", result.stderr + result.stdout)
                if scenario == "ssh-down":
                    self.assertIn("ssh-copy-id -p 22 labuser@192.0.2.10", result.stderr + result.stdout)
                    self.assertNotIn("ssh-copy-id diarchy-test", result.stderr + result.stdout)

    def test_client_doctor_fails_when_both_routes_are_unreachable(self) -> None:
        home = self.home_for("route-down")
        result = subprocess.run(
            [str(CLIENT_DOCTOR)],
            env=self._env(home) | {"FAKE_ROUTE_DOWN": "1"},
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot select a reachable route", result.stderr)

    def test_client_doctor_reports_exact_config_loader_reason(self) -> None:
        home = self.home_for("doctor-config-reason")
        ssh_calls_before = len(self.tool_entries("ssh"))
        missing_config = self.tmpdir / "doctor-absent.conf"
        invalid_config = self._client_config(
            "doctor-invalid.conf",
            "DIARCHY_SSH_USER=labuser\nDIARCHY_LAN_HOST=-192.0.2.10\n",
        )
        cases = (
            (missing_config, "[fail] Diarchy is not configured (open the app to set it up)"),
            (invalid_config, "[fail] DIARCHY_LAN_HOST is invalid"),
        )
        for config, expected in cases:
            with self.subTest(config=config.name):
                result = subprocess.run(
                    [str(CLIENT_DOCTOR)],
                    env=self._env(home) | {"DIARCHY_CONFIG": str(config)},
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("diarchy-config:", result.stderr)
                self.assertNotIn("cannot select a reachable route", result.stderr)
                self.assertEqual(len(self.tool_entries("ssh")), ssh_calls_before)

    def test_client_doctor_ssh_copy_id_hint_includes_identity_public_key_when_set(self) -> None:
        home = self.home_for("doctor-hint-identity")
        cases = (
            (
                "with-identity",
                "DIARCHY_SSH_USER=labuser\nDIARCHY_LAN_HOST=192.0.2.10\nDIARCHY_SSH_IDENTITY_FILE=/keys/id_lab\n",
                "ssh-copy-id -p 22 -i /keys/id_lab.pub labuser@192.0.2.10",
            ),
            (
                "without-identity",
                "DIARCHY_SSH_USER=labuser\nDIARCHY_LAN_HOST=192.0.2.10\n",
                "ssh-copy-id -p 22 labuser@192.0.2.10",
            ),
        )
        for name, body, expected in cases:
            with self.subTest(name=name):
                config = self._client_config(f"doctor-hint-{name}.conf", body)
                result = subprocess.run(
                    [str(CLIENT_DOCTOR)],
                    env=self._env(home, ssh_down=True) | {"DIARCHY_CONFIG": str(config)},
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                if name == "without-identity":
                    self.assertNotIn(" -i ", result.stderr)

    def test_client_doctor_requires_config_loader_and_diarchy_ssh_installed(self) -> None:
        for name, label in (("diarchy-config.sh", "Config loader"), ("diarchy-ssh", "SSH wrapper")):
            with self.subTest(name=name):
                home = self.home_for(f"doctor-missing-{name}")
                (home / ".local" / "bin" / name).chmod(0o644)
                result = run_doctor("client", home)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"[fail] {label} is missing, not executable, or wrong mode", result.stderr)

    @needs_host_python
    def test_host_doctor_fails_on_missing_or_wrong_host_state(self) -> None:
        missing = run_doctor("host", self.home_for("host-missing"))
        self.assertNotEqual(missing.returncode, 0)
        installed = self.home_for("host-wrong-mode")
        (installed / ".local/bin/diarchy-clipboard-remote").chmod(0o644)
        wrong_mode = run_doctor("host", installed)
        self.assertNotEqual(wrong_mode.returncode, 0)

    def test_doctors_fail_when_runtime_dependency_or_service_is_missing(self) -> None:
        run_installer("client", [], self.home)
        run_installer("host", [], self.home)
        cases = (
            (
                "client-pbcopy",
                "client",
                {"PBCOPY_BIN": str(self.tmpdir / "missing-pbcopy")},
                "pbcopy",
            ),
            (
                "host-wl-copy",
                "host",
                {"WL_COPY_BIN": str(self.tmpdir / "missing-wl-copy")},
                "wl-copy",
            ),
            (
                "host-notify-send",
                "host",
                {"NOTIFY_SEND_BIN": str(self.tmpdir / "missing-notify-send")},
                "notify-send",
            ),
            (
                "host-gi",
                "host",
                {"PYTHON_BIN": str(self.python_no_gi)},
                "PyGObject GTK 3",
            ),
            (
                "host-kvm-service",
                "host",
                {"FAKE_KVM_SERVICE_DOWN": "1"},
                "diarchy-kvm.service",
            ),
            (
                "host-file-portal-service",
                "host",
                {"FAKE_FILE_PORTAL_SERVICE_DOWN": "1"},
                "diarchy-file-portal.service",
            ),
        )
        for name, side, extra_env, expected in cases:
            with self.subTest(name=name):
                doctor = CLIENT_DOCTOR if side == "client" else HOST_DOCTOR
                result = subprocess.run(
                    [str(doctor)],
                    env=self._env(self.home) | extra_env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    @needs_host_python
    def test_doctors_are_read_only(self) -> None:
        run_installer("client", [], self.home)
        run_installer("host", [], self.home)
        before = tree_hash(self.home)
        self.assertEqual(run_doctor("client", self.home).returncode, 0)
        self.assertEqual(run_doctor("host", self.home).returncode, 0)
        self.assertEqual(tree_hash(self.home), before)

    def test_osacompile_swiftc_codesign_bundle_metadata_and_atomic_app_install(self) -> None:
        result = run_installer("client", [], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.home / "Applications" / "Diarchy Drop.app"
        portal_app = self.home / "Applications" / "Diarchy File Portal.app"
        self.assertTrue(app.is_dir())
        self.assertTrue(portal_app.is_dir())
        self.assertEqual((app / "Contents" / "Info.plist").read_text().strip(), f"CFBundleIdentifier={DROP_BUNDLE_ID}")
        portal_plist = (portal_app / "Contents" / "Info.plist").read_text()
        self.assertIn(f"<string>{FILE_PORTAL_BUNDLE_ID}</string>", portal_plist)
        self.assertIn("<key>LSUIElement</key>", portal_plist)
        self.assertIn("<true/>", portal_plist)
        self.assertIn("<key>CFBundleExecutable</key>", portal_plist)
        self.assertIn("<string>diarchy-file-portal</string>", portal_plist)
        self.assertEqual(stat.S_IMODE((portal_app / "Contents/MacOS/diarchy-file-portal").stat().st_mode), 0o755)
        self.assertFalse(list((self.home / "Applications").glob(".Diarchy Drop.stage*")))
        self.assertFalse(list(self.tmpdir.glob("diarchy-drop-client.*")))
        osacompile_argv = self.tool_entries("osacompile")[-1]["argv"]
        self.assertEqual(osacompile_argv[-1], str(APPLESCRIPT))
        self.assertIn("-o", osacompile_argv)
        stage_path = Path(osacompile_argv[osacompile_argv.index("-o") + 1])
        self.assertEqual(stage_path.name, "Diarchy Drop.app")
        self.assertTrue(stage_path.parent.name.startswith(".Diarchy Drop.stage."))
        self.assertEqual(stage_path.parent.parent, self.home / "Applications")
        swiftc_argv = self.tool_entries("swiftc")[-1]["argv"]
        self.assertIn(str(FILE_PORTAL_SWIFT), swiftc_argv)
        self.assertIn(str(FILE_PORTAL_STAGING_SWIFT), swiftc_argv)
        self.assertIn("-framework", swiftc_argv)
        self.assertIn("AppKit", swiftc_argv)
        self.assertIn("-o", swiftc_argv)
        swift_stage = Path(swiftc_argv[swiftc_argv.index("-o") + 1])
        self.assertEqual(swift_stage.name, "diarchy-file-portal")
        self.assertTrue(swift_stage.parent.name.startswith("diarchy-drop-client."))
        self.assertEqual(swift_stage.parent.parent, self.tmpdir)
        codesign_entries = self.tool_entries("codesign")
        self.assertTrue(any(Path(entry["argv"][4]) == stage_path for entry in codesign_entries))
        self.assertTrue(any(Path(entry["argv"][4]).name == "Diarchy File Portal.app" for entry in codesign_entries))

        replacement_home = self.tmpdir / "replacement-home"
        replacement_home.mkdir()
        self._make_bundle(replacement_home / "Applications" / "Diarchy Drop.app", DROP_BUNDLE_ID, marker="old")
        observations = self.tmpdir / "publication-observations.json"
        ready = self.tmpdir / "publication-ready"
        go = self.tmpdir / "publication-go"
        env = self._env(replacement_home) | {
            "DIARCHY_STAGE_MARKER": "new",
            "PUBLICATION_OBSERVATIONS": str(observations),
            "PUBLICATION_SWAP_READY": str(ready),
            "PUBLICATION_SWAP_GO": str(go),
        }
        replacement_process = subprocess.Popen([str(CLIENT_INSTALLER)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 10
        while not ready.exists():
            if replacement_process.poll() is not None:
                out, err = replacement_process.communicate()
                self.fail(err + out)
            if time.monotonic() > deadline:
                replacement_process.kill()
                self.fail("timed out waiting for atomic replacement barrier")
            time.sleep(0.01)
        go.write_text("go\n")
        replacement_out, replacement_err = replacement_process.communicate(timeout=10)
        self.assertEqual(replacement_process.returncode, 0, replacement_err + replacement_out)
        atomic_entries = self.tool_entries("atomic-app-replace")
        self.assertTrue(atomic_entries)
        entry = atomic_entries[-1]
        self.assertTrue(entry["live_exists_before"])
        self.assertEqual(entry["live_info_before"], f"CFBundleIdentifier={DROP_BUNDLE_ID}\n")
        old_bundle_after_swap = Path(entry["argv"][0])
        self.assertFalse(old_bundle_after_swap.exists(), "old bundle stage must be cleaned only after commit")
        observed = json.loads(observations.read_text())
        self.assertGreater(len(observed), 1)
        self.assertTrue(all(item["exists"] and item["is_dir"] for item in observed))
        self.assertEqual({item["info"] for item in observed}, {f"CFBundleIdentifier={DROP_BUNDLE_ID}\n"})
        self.assertLessEqual({item["marker"] for item in observed}, {"old\n", "new\n"})

    def test_concurrent_app_publication_uses_unique_same_filesystem_stages(self) -> None:
        home = self.tmpdir / "concurrent-publish"
        home.mkdir()
        self._make_bundle(home / "Applications" / "Diarchy Drop.app", DROP_BUNDLE_ID, marker="base")
        env1 = self._env(home) | {"DIARCHY_STAGE_MARKER": "first", "PUBLICATION_OBSERVATIONS": str(self.tmpdir / "observer-1.json")}
        env2 = self._env(home) | {"DIARCHY_STAGE_MARKER": "second", "PUBLICATION_OBSERVATIONS": str(self.tmpdir / "observer-2.json")}
        first = subprocess.Popen([str(CLIENT_INSTALLER)], env=env1, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen([str(CLIENT_INSTALLER)], env=env2, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        first_out, first_err = first.communicate(timeout=10)
        second_out, second_err = second.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, first_err + first_out)
        self.assertEqual(second.returncode, 0, second_err + second_out)
        osacompile_entries = self.tool_entries("osacompile")[-2:]
        stages = [Path(entry["argv"][entry["argv"].index("-o") + 1]).parent for entry in osacompile_entries]
        self.assertEqual(len(set(stages)), 2)
        self.assertTrue(all(stage.parent == home / "Applications" for stage in stages))
        for observation_file in (self.tmpdir / "observer-1.json", self.tmpdir / "observer-2.json"):
            observed = json.loads(observation_file.read_text())
            self.assertTrue(all(item["exists"] and item["is_dir"] for item in observed))
            self.assertEqual({item["info"] for item in observed}, {f"CFBundleIdentifier={DROP_BUNDLE_ID}\n"})
            self.assertLessEqual({item["marker"] for item in observed}, {"base\n", "first\n", "second\n"})
        self.assertEqual((home / "Applications" / "Diarchy Drop.app" / "Contents" / "Info.plist").read_text(), f"CFBundleIdentifier={DROP_BUNDLE_ID}\n")
        self.assertIn((home / "Applications" / "Diarchy Drop.app" / "Contents" / "Resources" / "stage.txt").read_text(), {"first\n", "second\n"})
        self.assertFalse(list((home / "Applications").glob(".Diarchy Drop.stage*")))

    def test_drop_applescript_sends_every_opened_item_with_quoted_paths_and_aggregate_errors(self) -> None:
        applescript_log = self.tmpdir / "applescript-calls.jsonl"
        fake_diarchy_file = self._write_executable(
            "fake-diarchy-file",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import json, sys
                from pathlib import Path
                log = Path({str(applescript_log)!r})
                log.open("a").write(json.dumps({{"argv": sys.argv[1:]}}) + "\\n")
                if len(sys.argv) > 2 and "middle" in sys.argv[2]:
                    print("remote failed for middle", file=sys.stderr)
                    raise SystemExit(17)
                """
            ),
        )
        paths = [
            self.tmpdir / "plain.txt",
            self.tmpdir / "middle ; 'quoted' $file.txt",
            self.tmpdir / "after & still-attempted.txt",
        ]
        for path in paths:
            path.write_text(path.name)
        notification_log = self.tmpdir / "notification.log"
        alert_log = self.tmpdir / "alert.log"
        env = os.environ | {
            "DIARCHY_FILE_BIN": str(fake_diarchy_file),
            "DIARCHY_DROP_NOTIFICATION_LOG": str(notification_log),
            "DIARCHY_DROP_ALERT_LOG": str(alert_log),
        }
        result = subprocess.run(["/usr/bin/osascript", str(APPLESCRIPT), *[str(path) for path in paths]], env=env, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line)["argv"] for line in applescript_log.read_text().splitlines()]
        self.assertEqual(calls, [["send", str(path)] for path in paths])
        self.assertIn("1 of 3 item(s) failed", result.stdout)
        notifications = notification_log.read_text().splitlines()
        self.assertEqual(len(notifications), 3)
        self.assertIn("sent", notifications[0].lower())
        self.assertIn("failed", notifications[1].lower())
        self.assertIn("sent", notifications[2].lower())
        self.assertEqual(alert_log.read_text(), "remote failed for middle\n")
        source = APPLESCRIPT.read_text()
        self.assertIn("on run argv", source)
        self.assertNotIn("items as string", source.lower())

    @needs_host_python
    def test_no_service_listener_or_privilege_artifacts_are_created_or_referenced(self) -> None:
        run_installer("client", [], self.home)
        run_installer("host", [], self.home)
        forbidden_paths = [
            self.home / "Library" / "LaunchAgents",
        ]
        for path in forbidden_paths:
            self.assertFalse(path.exists(), path)
        self.assertTrue((self.home / ".config" / "systemd" / "user" / "diarchy-file-portal.service").is_file())
        for artifact in (CLIENT_INSTALLER, HOST_INSTALLER, CLIENT_DOCTOR, HOST_DOCTOR, APPLESCRIPT):
            text = artifact.read_text().lower()
            self.assertNotIn("sudo", text)
            self.assertNotIn("launchagent", text)
            self.assertNotIn("listen", text)


if __name__ == "__main__":
    unittest.main()
