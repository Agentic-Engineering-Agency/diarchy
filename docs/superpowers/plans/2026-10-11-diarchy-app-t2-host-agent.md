# Diarchy — T2 Host Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Host agent `diarchy-host` (spec §6) with the platform adapter seam, the host installer orchestrator `install-host.sh` (§7), and make host KVM installation go through the agent.

**Architecture:** A stdlib-only Python ≥ 3.11 package `host/diarchy_host/` behind a thin launcher `host/diarchy-host`. `cli.py` owns the JSON envelope; `kvm`, `probe`, (t4's) `stream` expose `handle(args) -> dict` and raise `AgentError`. A `PlatformAdapter` protocol isolates distro/compositor knowledge. Every external binary is resolved through an env override so tests use fakes on a sandboxed `PATH` and a temp `HOME`. `install-host.sh` installs the agent, probes, gates on required actions, then runs the existing sub-installers; `install-lan-mouse.sh` no longer writes the Lan Mouse config itself.

**Tech Stack:** Python 3.11+ stdlib, bash, systemd user units, `unittest` (fake executables, temp HOME).

**Spec:** `docs/superpowers/specs/2026-10-11-diarchy-app-design.md` (§2, §6, §7, §10, §11, §12). Team brief: `docs/superpowers/teams/t2-host-agent.md`. Shared rules: `docs/superpowers/teams/00-shared-rules.md`. ADRs: `docs/adr/0001-*`, `0002-*`.

## Global Constraints

- Python stdlib only; Python ≥ 3.11 on the host; bash scripts `set -euo pipefail`. No new third-party dependencies.
- Envelope (§6.1): exactly one JSON object + `\n` on stdout; success `{"schema": 1, "ok": true, "command": "<cmd>", ...payload}` exit 0; handled failure `{"schema": 1, "ok": false, "command": "<cmd>", "error": {"code": "<code>", "message": "<text>"}}` exit 1; usage error same shape with `code` `usage` exit 64.
- Error codes: `usage`, `unsupported_platform`, `not_installed`, `service_failed`, `invalid_fingerprint`, `config_write_failed`, `sunshine_unreachable`, `pair_failed`, `internal`.
- FP = `^([0-9A-F]{2}:){31}[0-9A-F]{2}$` (uppercase SHA-256); never `sudo` executed (ADR 0002): `sudo` appears only inside `command` strings of required actions.
- Host Lan Mouse config: header `# Managed by diarchy`, `release_bind = ["KeyA", "KeyS", "KeyD", "KeyF"]`, `capture_backend = "layer-shell"`, `port = 4242`, `[authorized_fingerprints]`; mode 0600, atomic write, unmanaged file backed up to `config.toml.backup.<UTC %Y%m%dT%H%M%SZ>`. Config dir mode 0700.
- Units: `UNIT_KVM = "diarchy-kvm.service"`, `UNIT_STREAM_DISPLAY = "diarchy-display.service"`, `UNIT_STREAM_SUNSHINE = "diarchy-sunshine.service"`.
- Env overrides (tests): `HYPRCTL_BIN`, `OPENSSL_BIN`, `SYSTEMCTL_BIN`, `SS_BIN`, `PYTHON_BIN`, `LAN_MOUSE_BIN`, `LAN_MOUSE_CONFIG`, `LAN_MOUSE_CERT`, `DIARCHY_OS_RELEASE`, `DIARCHY_MACHINE`, `DIARCHY_UINPUT_PATH`, `DIARCHY_SYS_NET`, `DIARCHY_LOGIND_DIRS`, `DIARCHY_AGENT_DIR`, `DIARCHY_HOST_BIN`. `~` resolves from `$HOME` (never `pwd`).
- Product code has no personal values (`192.0.2.10`, `100.64.0.10`, `192.0.2.20`, `user`, key paths). Test fixtures: RFC 5737 (`192.0.2.x`, `198.51.100.x`) and `100.64.0.x`.
- Code/comments/logs/commit messages are English. Commit on branch `feat/diarchy-app--host-agent` with explicit paths. Never push/merge/rebase; never `sudo`; never contact the real Host.
- Run only owned tests: `python3 -m unittest tests.test_x -v` (full suite once at the end).

## Rulings (recorded up-front; spec is silent or ambiguous)

- **R1 FP casing:** the host `config.toml` is written with the fingerprint **lowercase** (the form physically verified by the pre-existing installers); input is accepted in either case; every value returned to the app (`authorized`, `probe.kvm.host_fingerprint`, `probe.kvm.authorized_fingerprints`) is uppercase FP. Cost if wrong: none for the host (matches verified behaviour); the Mac-side toml (t1) should lowercase too — flagged to Main.
- **R2 `command` field:** `"version"`, `"probe"`, `"kvm start"`, `"stream pair"`; for unknown/missing tokens the first non-option token or `""`.
- **R3 Non-interactive SSH:** `cli.main` does `os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")` so `systemctl --user`/`hyprctl` work over a non-login ssh. Not provable without a real Host.
- **R4 `probe --streaming`:** additive optional flag. `uinput-access`/`lid-policy` are emitted when Sunshine is installed **or** `--streaming` is given (§6.3 says "or requested" without a flag). `install-host.sh --with-streaming` passes it. Flagged to Main/t3.
- **R5 Probe degrades, never crashes:** a missing/failing external tool yields the conservative value (false/null) plus, for platform checks, a human reason; only `internal` bugs fail the envelope.
- **R6 stdout guard:** `cli.main` redirects `sys.stdout` to `sys.stderr` while a handler runs, so stray prints can never corrupt the single-JSON-line contract.
- **R7 Unsupported platform:** `required_actions` is `[]` (the app blocks on the platform step first); components/kvm are still reported best-effort.
- **R8 Agent strings are English** (`title`, `reason`, error `message`); the app localizes by `id`/`code`.
- **R9 `install-host.sh` final line** prints `payload/VERSION` (the app version, one dir above the script) when present, else `host/VERSION`.

## Review Focus

1. Mixed-case / malformed fingerprints (`aa:…`, 31 groups, `ZZ`): rejected with `invalid_fingerprint` **before any file is touched**; mixed case accepted and normalized (Task 3, 4).
2. Pre-existing **unmanaged** `config.toml` (user's own `[[clients]]`, other fingerprints): must be backed up byte-for-byte, never lost, on both `configure` and `authorize` (Task 3).
3. Non-interactive SSH environment: `XDG_RUNTIME_DIR` unset, tiny `PATH`; stdout still exactly one JSON line even on unexpected exceptions (Task 1).
4. Missing/broken tools during `probe` (no `hyprctl`, no `systemctl`, no `ss`, no `openssl`, hanging tool → timeout): probe still returns a valid envelope with conservative values (Task 5).
5. `--controller-ip` abuse (IPv6, hostname, `1.2.3`, `192.0.2.1; rm -rf /`, leading zeros): usage error exit 64; no required-action command ever contains unvalidated text (Task 5).

---

## File Structure

| File | Responsibility |
|---|---|
| `host/VERSION` | `1.0.0` |
| `host/diarchy-host` | launcher: python ≥ 3.11 guard, locate package, run `cli.main` |
| `host/diarchy_host/__init__.py` | empty marker |
| `host/diarchy_host/common.py` | `AgentError`, `run`, `systemctl_user`, `unit_active`, `unit_active_system`, unit constants, `resolve_bin`, `home`, `agent_root` |
| `host/diarchy_host/cli.py` | envelope, dispatch, exit codes, stdout guard |
| `host/diarchy_host/platform.py` | `PlatformInfo`, `PlatformAdapter`, `HyprlandArchAdapter`, `UnsupportedAdapter`, `select_adapter` |
| `host/diarchy_host/kvmconfig.py` | FP validation/normalization, render/parse/write host Lan Mouse config |
| `host/diarchy_host/kvm.py` | `handle(args)` for `kvm configure|authorize|start|stop|status` |
| `host/diarchy_host/probe.py` | `handle(args)` for `probe`, components, required actions |
| `host/install-host.sh` | host installer orchestrator (§7) |
| `host/install-lan-mouse.sh`, `host/diarchy-kvm-host-doctor` | modified (Task 6) |
| `tests/test_diarchy_host_support.py` | shared harness `AgentCase` (temp HOME, sole-`PATH` tools dir, fake systemctl) |
| `tests/test_diarchy_host_cli.py`, `_platform.py`, `_kvmconfig.py`, `_kvm.py`, `_probe.py`, `tests/test_install_host.py`, `tests/test_lan_mouse_host.py` | tests per task |

`host/diarchy_host/stream.py` belongs to team t4 — never create or edit it.

Task order is sequential (each builds on the previous); commit at the end of every task.

---

### Task 1: Package skeleton, envelope, `version`, launcher, test harness

**Files:**
- Create: `host/VERSION`, `host/diarchy-host` (0755), `host/diarchy_host/__init__.py`, `host/diarchy_host/common.py`, `host/diarchy_host/cli.py`
- Create: `tests/test_diarchy_host_support.py`, `tests/test_diarchy_host_cli.py`

**Interfaces:**
- Produces (t4 consumes, exact):
  - `common.AgentError(code: str, message: str)` with attributes `.code`, `.message`; `str(e) == message`.
  - `common.run(argv: list[str], timeout: float = 20.0, input: str | None = None) -> subprocess.CompletedProcess[str]` — text mode, captured stdout/stderr, never raises on non-zero exit; raises `AgentError("internal", ...)` on timeout or missing executable.
  - `common.systemctl_user(*args: str) -> CompletedProcess[str]` — runs `[$SYSTEMCTL_BIN or "systemctl", "--user", *args]`.
  - `common.unit_active(unit: str) -> bool` — `systemctl --user is-active --quiet <unit>` returns 0; any failure to run → `False`.
  - `common.unit_active_system(unit: str) -> bool` — same without `--user`.
  - `common.resolve_bin(env_var: str, default: str) -> str` — `os.environ.get(env_var) or default`.
  - `common.home() -> pathlib.Path` — from `$HOME`; `common.agent_root() -> pathlib.Path` — `$DIARCHY_AGENT_DIR` or the directory containing the `diarchy_host` package.
  - Constants `UNIT_KVM`, `UNIT_STREAM_DISPLAY`, `UNIT_STREAM_SUNSHINE`, `ERROR_CODES` (tuple of the 9 codes).
  - `cli.main(argv: list[str]) -> int`. `stream` dispatches to `diarchy_host.stream.handle(args: list[str]) -> dict` (payload only; `args` = tokens after `stream`); absent module → `AgentError("not_installed", "streaming support is not installed")`.
  - Test harness `tests.test_diarchy_host_support.AgentCase` (below) used by every later task.
- Consumes: nothing.

- [ ] **Step 1: Write the harness and the failing CLI tests**

`tests/test_diarchy_host_support.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "host"
LAUNCHER = HOST / "diarchy-host"
FP_A = ":".join(["AA"] * 32)
FP_B = ":".join(["BB"] * 32)


class AgentCase(unittest.TestCase):
    """Temp HOME, a tools dir that is the ONLY entry of PATH, and a launcher runner."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.tools = self.root / "tools"
        self.home.mkdir()
        self.tools.mkdir()
        self.log = self.root / "calls.log"
        self.env: dict[str, str] = {
            "HOME": str(self.home),
            "PATH": str(self.tools),
            "XDG_RUNTIME_DIR": str(self.root / "run"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "FAKE_LOG": str(self.log),
        }

    def tool(self, name: str, body: str, env_var: str | None = None) -> Path:
        path = self.tools / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)
        if env_var:
            self.env[env_var] = str(path)
        return path

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def agent(self, *args: str, env: dict[str, str] | None = None
              ) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            [sys.executable, str(LAUNCHER), *args],
            env=self.env | (env or {}), text=True, capture_output=True, check=False,
        )
        lines = result.stdout.splitlines()
        assert len(lines) == 1, f"stdout must be exactly one line, got: {result.stdout!r}"
        return result, json.loads(lines[0])
```

`tests/test_diarchy_host_cli.py`:

```python
from __future__ import annotations

import sys
import unittest

from tests.test_diarchy_host_support import AgentCase, HOST

sys.path.insert(0, str(HOST))
from diarchy_host import common  # noqa: E402


class EnvelopeTests(AgentCase):
    def test_version_success_envelope(self) -> None:
        result, body = self.agent("version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body, {"schema": 1, "ok": True, "command": "version", "agent_version": "1.0.0"})
        self.assertTrue(result.stdout.endswith("\n"))

    def test_missing_command_is_usage_error_exit_64(self) -> None:
        result, body = self.agent()
        self.assertEqual(result.returncode, 64)
        self.assertEqual(body["ok"], False)
        self.assertEqual(body["schema"], 1)
        self.assertEqual(body["error"]["code"], "usage")
        self.assertIsInstance(body["error"]["message"], str)

    def test_unknown_command_and_extra_args_are_usage_errors(self) -> None:
        for argv in (("bogus",), ("version", "extra"), ("--help",)):
            with self.subTest(argv=argv):
                result, body = self.agent(*argv)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(body["error"]["code"], "usage")

    def test_stream_reports_not_installed_while_module_absent(self) -> None:
        result, body = self.agent("stream", "status")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body["command"], "stream status")
        self.assertEqual(body["error"], {"code": "not_installed", "message": "streaming support is not installed"})

    def test_internal_exception_still_prints_one_json_line(self) -> None:
        # A missing VERSION file is an unexpected condition inside the `version` handler.
        result, body = self.agent("version", env={"DIARCHY_AGENT_DIR": str(self.root / "nowhere")})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body["error"]["code"], "internal")

    def test_stray_prints_never_reach_stdout(self) -> None:
        # Guard (R6): handlers run with stdout redirected to stderr.
        import io, contextlib
        from diarchy_host import cli
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            original = cli.DISPATCH["version"]
            cli.DISPATCH["version"] = lambda args: (print("noise"), {"agent_version": "x"})[1]
            try:
                code = cli.main(["version"])
            finally:
                cli.DISPATCH["version"] = original
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue().count("\n"), 1)
        self.assertNotIn("noise", buffer.getvalue())

    def test_non_interactive_ssh_env_gets_runtime_dir_default(self) -> None:
        import os
        env = dict(os.environ)
        env.pop("XDG_RUNTIME_DIR", None)
        saved = os.environ.copy()
        try:
            os.environ.pop("XDG_RUNTIME_DIR", None)
            from diarchy_host import cli
            cli.main(["version"])
            self.assertEqual(os.environ["XDG_RUNTIME_DIR"], f"/run/user/{os.getuid()}")
        finally:
            os.environ.clear()
            os.environ.update(saved)


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

    def test_systemctl_user_honors_env_and_unit_active_maps_exit_code(self) -> None:
        self.tool("fake-systemctl", (
            "import os, sys\n"
            "open(os.environ['FAKE_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(0 if sys.argv[-1] == 'up.service' else 3)\n"
        ), env_var="SYSTEMCTL_BIN")
        import os
        saved = os.environ.copy()
        try:
            os.environ.update(self.env)
            self.assertTrue(common.unit_active("up.service"))
            self.assertFalse(common.unit_active("down.service"))
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(self.calls(), ["--user is-active --quiet up.service", "--user is-active --quiet down.service"])

    def test_error_codes_are_the_spec_set(self) -> None:
        self.assertEqual(set(common.ERROR_CODES), {
            "usage", "unsupported_platform", "not_installed", "service_failed", "invalid_fingerprint",
            "config_write_failed", "sunshine_unreachable", "pair_failed", "internal"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to confirm RED**

Run: `python3 -m unittest tests.test_diarchy_host_cli -v`
Expected: FAIL/ERROR (`diarchy_host` and launcher do not exist).

- [ ] **Step 3: Implement**

`host/VERSION` contains `1.0.0\n`. `host/diarchy_host/__init__.py` is empty.

`host/diarchy_host/common.py`:

```python
from __future__ import annotations

import os
import subprocess
from pathlib import Path

UNIT_KVM = "diarchy-kvm.service"
UNIT_STREAM_DISPLAY = "diarchy-display.service"
UNIT_STREAM_SUNSHINE = "diarchy-sunshine.service"
ERROR_CODES = (
    "usage", "unsupported_platform", "not_installed", "service_failed", "invalid_fingerprint",
    "config_write_failed", "sunshine_unreachable", "pair_failed", "internal",
)


class AgentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def run(argv: list[str], timeout: float = 20.0, input: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input, check=False)
    except subprocess.TimeoutExpired as error:
        raise AgentError("internal", f"{argv[0]} timed out after {timeout:g}s") from error
    except OSError as error:
        raise AgentError("internal", f"cannot execute {argv[0]}: {error.strerror or error}") from error


def resolve_bin(env_var: str, default: str) -> str:
    return os.environ.get(env_var) or default


def home() -> Path:
    return Path(os.environ["HOME"])


def agent_root() -> Path:
    override = os.environ.get("DIARCHY_AGENT_DIR")
    return Path(override) if override else Path(__file__).resolve().parents[1]


def systemctl_user(*args: str) -> subprocess.CompletedProcess[str]:
    return run([resolve_bin("SYSTEMCTL_BIN", "systemctl"), "--user", *args])


def _active(argv_prefix: list[str], unit: str) -> bool:
    try:
        return run([*argv_prefix, "is-active", "--quiet", unit]).returncode == 0
    except AgentError:
        return False


def unit_active(unit: str) -> bool:
    return _active([resolve_bin("SYSTEMCTL_BIN", "systemctl"), "--user"], unit)


def unit_active_system(unit: str) -> bool:
    return _active([resolve_bin("SYSTEMCTL_BIN", "systemctl")], unit)
```

`host/diarchy_host/cli.py` — dispatch table `DISPATCH: dict[str, Callable[[list[str]], dict]]` (`version` only for now), `stream` handled by a guarded import, parse top-level token(s), compute `command` per R2, and:

```python
def main(argv: list[str]) -> int:
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")      # R3
    command, envelope, code = _execute(argv)
    sys.stdout.write(json.dumps(envelope, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return code
```

`_execute` redirects `sys.stdout` to `sys.stderr` around the handler (R6, restore in `finally`), maps `AgentError("usage", …)` → 64, other `AgentError` → 1, any other `Exception` → `internal` with message `f"{type(e).__name__}: {e}"` (traceback to stderr via `traceback.print_exc()`), exit 1. `version` reads `agent_root()/"VERSION"` (strip). Usage errors: no args, unknown command, extra args for `version`, any token starting with `-` as command (`--help`/`-h` produce a `usage` envelope whose message contains the usage text `usage: diarchy-host {version|probe|kvm|stream} ...`).

`host/diarchy-host` (0755): `#!/usr/bin/env python3`; syntax compatible with Python 3.6+ (no `from __future__ import annotations`, no walrus); if `sys.version_info < (3, 11)` prints the failure envelope `unsupported_platform` ("diarchy-host requires Python 3.11 or newer") on one line and exits 1; otherwise inserts the first existing candidate dir containing `diarchy_host/__init__.py` in this order into `sys.path` — `$DIARCHY_AGENT_DIR`, the directory of the resolved script (`os.path.realpath(__file__)`), `$HOME/.local/share/diarchy/agent` — and `sys.exit(cli.main(sys.argv[1:]))`; none found → `not_installed` envelope, exit 1.

- [ ] **Step 4: Run to confirm GREEN**

Run: `python3 -m unittest tests.test_diarchy_host_cli -v`
Expected: all PASS, no stderr noise.

- [ ] **Step 5: Commit**

```bash
git add host/VERSION host/diarchy-host host/diarchy_host/__init__.py host/diarchy_host/common.py host/diarchy_host/cli.py tests/test_diarchy_host_support.py tests/test_diarchy_host_cli.py
git commit -m "feat(host-agent): add diarchy-host skeleton, envelope and version command"
```

Controller then sends `READY AGENT-SKELETON <hash>` to Main.

---

### Task 2: Platform adapter seam (§6.4)

**Files:**
- Create: `host/diarchy_host/platform.py`, `tests/test_diarchy_host_platform.py`

**Interfaces:**
- Consumes: `common.run`, `common.resolve_bin`, `common.unit_active`, `common.systemctl_user`, `common.AgentError`.
- Produces:
  - `@dataclass(frozen=True) class PlatformInfo: supported: bool; adapter: str; os_id: str | None; flavor: str | None; compositor: str | None; machine: str | None; reasons: list[str]` with `to_dict() -> dict` (exact §6.3 `platform` keys).
  - `class PlatformAdapter(Protocol)`: `detect() -> PlatformInfo`, `missing_packages() -> list[str]`, `install_command(packages: list[str]) -> str`, `kvm_capture_backend() -> str`, `graphical_session_active() -> bool`.
  - `class HyprlandArchAdapter` (adapter name `"hyprland-arch"`), `class UnsupportedAdapter(info: PlatformInfo)` (name `"unsupported"`, `missing_packages() -> []`, `install_command` raises `AgentError("unsupported_platform", …)`), `select_adapter() -> PlatformAdapter`.
  - Package mapping, in this order: `wl-clipboard` (`wl-copy`/`wl-paste`), `rsync`, `libnotify` (`notify-send`), `openssl` (`$OPENSSL_BIN` or `openssl`), `jq`, `curl`, then `python-gobject`, `gtk3`, `gtk-layer-shell` when `$PYTHON_BIN` (default `python3`) fails `import gi; gi.require_version('Gtk','3.0'); gi.require_version('GtkLayerShell','0.1')`. Presence = `shutil.which` against the live `PATH`.
  - `install_command(pkgs)` = `"sudo pacman -S --needed " + " ".join(pkgs)`.

- [ ] **Step 1: Write the failing tests** (`tests/test_diarchy_host_platform.py`, in-process via `sys.path.insert(0, str(HOST))`, using `AgentCase` and `os.environ` patching with `unittest.mock.patch.dict`)

Fixtures: `DIARCHY_OS_RELEASE` file contents (`ID=arch`, `ID=endeavouros\nID_LIKE=arch`, `ID=ubuntu\nID_LIKE=debian`, quoted values `ID="arch"`), `DIARCHY_MACHINE` (`x86_64`/`aarch64`), fake `hyprctl` (`HYPRCTL_BIN`: prints `[{"instance":"abc"}]` / `[]` / exits 1), fake `systemctl` (`SYSTEMCTL_BIN`: `is-system-running` prints `running`|`degraded`|`offline` per `FAKE_MANAGER_STATE`; `is-active --quiet graphical-session.target` per `FAKE_GRAPHICAL`), `~/.local/share/omarchy` dir for flavor. Cases (each its own `test_*`):

1. supported Omarchy: `supported True, adapter "hyprland-arch", os_id "arch", flavor "omarchy", compositor "hyprland", machine "x86_64", reasons []`.
2. no `~/.local/share/omarchy` → `flavor None`, still supported.
3. `ID=endeavouros ID_LIKE=arch` and `ID_LIKE="manjaro arch"` supported (`os_id` is the `ID`).
4. ubuntu → unsupported, one reason mentioning Arch, `adapter "unsupported"` from `select_adapter().detect()`.
5. `aarch64` → unsupported, reason mentions `x86_64`.
6. `hyprctl instances -j` returns `[]` / exits 1 / binary missing → unsupported, reason mentions Hyprland, `compositor None`.
7. manager `offline` → unsupported, reason mentions systemd user manager; `degraded` counts as active.
8. multiple failures → one reason **per** failed condition (arch + hyprland → exactly 2 reasons).
9. `os-release` missing → unsupported (reason), no exception.
10. `missing_packages()` with every tool present and python import ok → `[]`; with `wl-paste` missing only → `["wl-clipboard"]`; `wl-copy` and `wl-paste` both missing → single `wl-clipboard`; `jq`,`curl` missing → `["jq","curl"]`; fake `python3` exit 1 → ends with `["python-gobject","gtk3","gtk-layer-shell"]`; `notify-send` → `libnotify`.
11. `install_command(["jq","curl"]) == "sudo pacman -S --needed jq curl"`; `kvm_capture_backend() == "layer-shell"`; `graphical_session_active()` true/false per fake systemctl; `UnsupportedAdapter.install_command([...])` raises `AgentError` code `unsupported_platform`.

- [ ] **Step 2: Run to confirm RED** — `python3 -m unittest tests.test_diarchy_host_platform -v` → ERROR (module missing).
- [ ] **Step 3: Implement** `platform.py` (parse `os-release` with a small `KEY=VALUE` reader that strips matching quotes; `ID_LIKE.split()`; `machine = os.environ.get("DIARCHY_MACHINE") or os.uname().machine`; `hyprctl` via `resolve_bin("HYPRCTL_BIN","hyprctl")` wrapped so `AgentError`/bad JSON means "no instance"; manager active ⇔ `systemctl --user is-system-running` stdout in `{"running","degraded"}`; `select_adapter()` returns the first candidate whose `detect().supported`, else `UnsupportedAdapter` built from the first candidate's failing info with `adapter` replaced by `"unsupported"`). Do NOT import stdlib `platform` anywhere in the package (name clash risk).
- [ ] **Step 4: Run to confirm GREEN** — same command; all PASS.
- [ ] **Step 5: Commit**

```bash
git add host/diarchy_host/platform.py tests/test_diarchy_host_platform.py
git commit -m "feat(host-agent): add platform adapter seam with Hyprland/Arch detection"
```

---

### Task 3: Host Lan Mouse config writer and fingerprint handling

**Files:**
- Create: `host/diarchy_host/kvmconfig.py`, `tests/test_diarchy_host_kvmconfig.py`

**Interfaces:**
- Consumes: `common.AgentError`, `common.home`.
- Produces:
  - `FP_RE = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")`; `normalize_fingerprint(value: str) -> str` returns uppercase FP or raises `AgentError("invalid_fingerprint", "fingerprint must be 32 colon-separated hex bytes")` (no mutation, no I/O).
  - `config_path() -> Path` = `$LAN_MOUSE_CONFIG` or `${XDG_CONFIG_HOME:-$HOME/.config}/lan-mouse/config.toml`.
  - `MANAGED_HEADER = "# Managed by diarchy"`.
  - `render(fingerprint: str | None) -> str` — exact output (blank line after `port`):

    ```
    # Managed by diarchy
    release_bind = ["KeyA", "KeyS", "KeyD", "KeyF"]
    capture_backend = "layer-shell"
    port = 4242

    [authorized_fingerprints]
    "<lowercase fp>" = "mac"        # only when fingerprint is not None
    ```

  - `read_authorized(path: Path | None = None) -> tuple[bool, list[str]]` → `(is_managed, [uppercase FP, ...])`, parsing only `"<fp>" = "<name>"` lines inside `[authorized_fingerprints]`; missing/unreadable file → `(False, [])`.
  - `write(fingerprint: str | None) -> None` — creates the config dir 0700 if absent; if the file exists and is not managed (first line != header) copies it first to `config.toml.backup.<UTC %Y%m%dT%H%M%SZ>` (mode 0600, byte-identical); atomic write (`tempfile.mkstemp` in the same dir → `chmod 0600` → write → `fsync` → `os.replace`); any `OSError` → `AgentError("config_write_failed", …)` leaving the original file intact.
  - `preserved_fingerprint() -> str | None` — first authorized FP when the existing file is managed, else `None`.

- [ ] **Step 1: Write the failing tests** — all in-process with `HOME`/`LAN_MOUSE_CONFIG` patched to the temp dir:
  1. `normalize_fingerprint`: lowercase and mixed case → uppercase; `""`, 31 groups, 33 groups, `ZZ` byte, no colons, trailing newline/space, `AA-BB-…` → each raises `invalid_fingerprint`.
  2. `render(None)` equals the exact text above ending `[authorized_fingerprints]\n`; `render(FP_A)` ends with `"aa:…:aa" = "mac"\n` (lowercase, R1).
  3. `write` creates dir 0700, file 0600, content equals `render`; calling twice leaves **no** `.backup.` files (managed file is not backed up) and no stray `config.toml.*` temp files.
  4. Unmanaged file (contains `[[clients]]` and another fingerprint) → exactly one `config.toml.backup.<ts>` byte-identical, mode 0600; new file managed.
  5. `read_authorized`: managed with one FP → `(True, [FP uppercase])`; unmanaged with FP → `(False, [FP])`; ignores FP-looking lines outside `[authorized_fingerprints]` and malformed names; missing file → `(False, [])`. Case-insensitive (Main ruling, spec e812eee): a toml containing the same FP in lowercase, uppercase or mixed case is read back as the identical uppercase FP, so `preserved_fingerprint()` and probe comparisons never depend on the file's casing.
  6. `preserved_fingerprint`: managed+FP → that FP; managed+empty → `None`; unmanaged → `None`; missing → `None`.
  7. Failure path: make the config dir read-only (`chmod 0o500`) → `AgentError` code `config_write_failed`, original file unchanged (skip the test if running as root).
  8. `write(fingerprint)` rejects an invalid FP through `normalize_fingerprint` **before** creating dir or backup (assert dir absent).

- [ ] **Step 2: RED** — `python3 -m unittest tests.test_diarchy_host_kvmconfig -v` → ERROR.
- [ ] **Step 3: Implement** `kvmconfig.py` as specified.
- [ ] **Step 4: GREEN** — same command; PASS.
- [ ] **Step 5: Commit**

```bash
git add host/diarchy_host/kvmconfig.py tests/test_diarchy_host_kvmconfig.py
git commit -m "feat(host-agent): add managed Lan Mouse host config writer"
```

---

### Task 4: `kvm` commands, mutual exclusion

**Files:**
- Create: `host/diarchy_host/kvm.py`, `tests/test_diarchy_host_kvm.py`
- Modify: `host/diarchy_host/cli.py` (register `kvm` in `DISPATCH`; `command` becomes `"kvm <sub>"`), `tests/test_diarchy_host_support.py` (add `fake_systemctl`)

**Interfaces:**
- Consumes: Task 1 `common.*`, Task 3 `kvmconfig.*`.
- Produces: `kvm.handle(args: list[str]) -> dict`:
  - `["configure"]` → `kvmconfig.write(kvmconfig.preserved_fingerprint())`; restart `UNIT_KVM` when active (non-zero restart → `service_failed`); returns `{"authorized": <uppercase FP or None>}`.
  - `["authorize", "--fingerprint", FP]` → `normalize_fingerprint` first (invalid → `invalid_fingerprint`, nothing touched), then `kvmconfig.write(fp)`, restart-if-active, returns `{"authorized": FP_UPPER}`. Missing/duplicate/extra arguments → `usage`.
  - `["start"]` → if `~/.config/systemd/user/diarchy-kvm.service` missing → `not_installed`; stop `UNIT_STREAM_SUNSHINE` then `UNIT_STREAM_DISPLAY` if each is active (a failing stop → `service_failed`, KVM not started); `systemctl --user start diarchy-kvm.service`; verify `unit_active` else `service_failed`; returns `{"service_active": True}`.
  - `["stop"]` → `systemctl --user stop diarchy-kvm.service`, returns `{"service_active": unit_active(...)}` (non-zero stop or still active → `service_failed`).
  - `["status"]` → `{"service_active": bool}`.
  - Unknown/missing subcommand → `usage`.
- Harness addition `AgentCase.fake_systemctl(active: Iterable[str] = (), fail: Iterable[str] = (), manager: str = "running")`: a Python fake installed as `SYSTEMCTL_BIN` that keeps state in files under `self.root/"state"`, key `user:<unit>` / `system:<unit>` (scope = `--user` present or not), logs every argv line to `FAKE_LOG`; `is-active [--quiet] <unit>` → exit 0 iff state file exists (prints `active`/`inactive` without `--quiet`); `start`/`restart` create state, `stop` removes; any verb whose `<verb> <unit>` is in `fail` exits 1; `is-system-running` prints `manager` and exits 0 iff `running`; other verbs exit 0.

- [ ] **Step 1: Write failing tests** (CLI-level through `self.agent(...)`; a fake `sudo` tool on PATH that logs to `FAKE_LOG` — every test asserts no `sudo` call was logged):
  1. `kvm configure` with no existing config → exit 0, `authorized` is `null`, file managed 0600, systemctl log has **no** restart (unit inactive).
  2. `kvm authorize --fingerprint <lowercase FP_A>` → body `{"authorized": FP_A_UPPER}`; file contains lowercase `"aa:…" = "mac"` exactly once; mixed-case input accepted.
  3. Active `UNIT_KVM` → `authorize`/`configure` log `--user restart diarchy-kvm.service`; inactive → none.
  4. `authorize` twice with different FPs → file has **only** the second (exactly one `"…" = "mac"` line).
  5. `configure` after `authorize FP_A` → preserves FP_A (`authorized` = FP_A upper); `configure` over an unmanaged file → backup created, `authorized: null`.
  6. Malformed FP → exit 1, `error.code == "invalid_fingerprint"`, config dir not created, no systemctl call. Missing `--fingerprint` value, extra arg, `authorize` with no args → exit 64 `usage`.
  7. `start` with unit file present: sunshine and display active → log shows `stop diarchy-sunshine.service`, `stop diarchy-display.service` **before** `start diarchy-kvm.service`; returns `{"service_active": true}`. Inactive stream units → no stop calls.
  8. `start` without the unit file → `not_installed`; `start` when systemctl start fails (`fail={"start diarchy-kvm.service"}` or unit does not become active) → `service_failed`; stop of sunshine failing → `service_failed` and KVM start never invoked.
  9. `stop`/`status` return `service_active` correctly; `kvm` / `kvm bogus` → usage exit 64 and `command` `"kvm"`.
  10. Config write failure (read-only dir) → `config_write_failed`, exit 1.

- [ ] **Step 2: RED** — `python3 -m unittest tests.test_diarchy_host_kvm -v` → FAIL.
- [ ] **Step 3: Implement** `kvm.py`, register in `cli.DISPATCH`, add the harness helper.
- [ ] **Step 4: GREEN** — `python3 -m unittest tests.test_diarchy_host_kvm tests.test_diarchy_host_cli -v` → PASS.
- [ ] **Step 5: Commit**

```bash
git add host/diarchy_host/kvm.py host/diarchy_host/cli.py tests/test_diarchy_host_kvm.py tests/test_diarchy_host_support.py
git commit -m "feat(host-agent): add kvm configure/authorize/start/stop/status"
```

---

### Task 5: `probe` and required actions (§6.3)

**Files:**
- Create: `host/diarchy_host/probe.py`, `tests/test_diarchy_host_probe.py`
- Modify: `host/diarchy_host/cli.py` (register `probe`)

**Interfaces:**
- Consumes: Tasks 1-4 (`platform.select_adapter`, `kvmconfig.read_authorized`, `common.*`).
- Produces: `probe.handle(args: list[str]) -> dict` accepting `[--controller-ip IPv4] [--streaming]` in any order, each at most once; payload keys exactly: `agent_version`, `platform` (`PlatformInfo.to_dict()`), `session` (`{"graphical_session_active": bool}`), `components`, `kvm`, `required_actions`.
  - `components.lan_mouse = {installed, version, service_active, listening_udp}`: installed = `$LAN_MOUSE_BIN` or `~/.local/bin/lan-mouse` is an executable file; `version` parsed from `lan-mouse --version` (`lan-mouse X.Y.Z`) else `None`; `service_active = unit_active(UNIT_KVM)`; `listening_udp` = `$SS_BIN -H -lun` has a local address ending `:4242`.
  - `components.clipboard = {installed}` ⇔ `~/.local/bin/diarchy-clipboard-remote` executable; `components.file_portal = {installed, service_active}` ⇔ `~/.local/bin/diarchy-file-portal` executable **and** `~/.config/systemd/user/diarchy-file-portal.service` exists / `unit_active("diarchy-file-portal.service")`; `components.sunshine = {installed, service_active}` ⇔ `~/.local/bin/sunshine` executable / `unit_active(UNIT_STREAM_SUNSHINE)`.
  - `kvm = {"host_fingerprint": FP_UPPER | None, "authorized_fingerprints": [FP_UPPER...], "port": 4242}`; fingerprint from `$OPENSSL_BIN x509 -in <$LAN_MOUSE_CERT or ~/.config/lan-mouse/lan-mouse.pem> -noout -fingerprint -sha256` (text after `=`, uppercased, must match `FP_RE`, else `None`).
  - `required_actions`: list of `{"id","severity","title","reason","command"}` in this order and with these exact rules (R7: `[]` when platform unsupported):
    - `install-packages` / `required`: `adapter.missing_packages()` non-empty; `command = adapter.install_command(missing)`.
    - `firewall-lan` / `recommended`: `ufw.service` active (system scope, `unit_active_system("ufw.service")`) **and** `--controller-ip` given; `command = f"sudo ufw allow proto udp from {ip} to any port 4242"`.
    - `firewall-tailscale` / `recommended`: `ufw.service` active **and** `$DIARCHY_SYS_NET` (default `/sys/class/net`)`/tailscale0` exists; `command = "sudo ufw allow in on tailscale0 to any port 4242 proto udp"`.
    - `uinput-access` / `required`: (sunshine installed **or** `--streaming`) **and** `$DIARCHY_UINPUT_PATH` (default `/dev/uinput`) not writable by the user (`os.access(path, os.W_OK)` false or path missing); `command` is exactly the §9.2 string: `printf 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"\n' | sudo tee /etc/udev/rules.d/60-diarchy-uinput.rules && sudo udevadm control --reload && sudo udevadm trigger --name-match=uinput` (with a literal backslash-n in the shell string, as the spec prints it).
    - `lid-policy` / `recommended`: (sunshine installed or `--streaming`) and logind `HandleLidSwitch` resolves to `suspend`; command exactly the §9.2 string: `sudo mkdir -p /etc/systemd/logind.conf.d && printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\n' | sudo tee /etc/systemd/logind.conf.d/60-diarchy.conf`. Resolution: read `logind.conf` then `logind.conf.d/*.conf` from the dirs in `$DIARCHY_LOGIND_DIRS` (colon-separated, default `/usr/lib/systemd:/run/systemd:/etc/systemd`; later dirs override earlier; within a dir `logind.conf` first then drop-ins sorted by filename; across dirs a drop-in with the same filename in a later dir replaces the earlier one — simplified: process dirs in order, each dir's `logind.conf` then its sorted `logind.conf.d/*.conf`), last `HandleLidSwitch=` in `[Login]` wins; unset → `suspend` (systemd default).
  - `--controller-ip` must satisfy `ipaddress.IPv4Address` **and** the canonical form equals the input (rejects leading zeros, IPv6, hostnames, whitespace, shell metacharacters) else `AgentError("usage", ...)`. Unknown flag, repeated flag, missing value → `usage`.
  - Every external call is guarded (R5): `AgentError` from `run` → conservative value.

- [ ] **Step 1: Write failing tests** (CLI-level via `self.agent("probe", ...)`; shared fixture `supported_host()` builds: `os-release` ID=arch, machine x86_64, fake hyprctl one instance, fake systemctl (`fake_systemctl()` from Task 4), tools `wl-copy wl-paste rsync notify-send openssl jq curl` as stubs, `PYTHON_BIN` stub exit 0, `~/.local/share/omarchy`; a fake `sudo` that logs to `FAKE_LOG` and must never be called):
  1. supported Omarchy: `schema 1`, `ok`, `command "probe"`, `platform.supported True`, `adapter "hyprland-arch"`, `flavor "omarchy"`, `agent_version "1.0.0"`, `required_actions == []`, `kvm.port == 4242`.
  2. non-Arch os-release → `supported False`, `adapter "unsupported"`, reasons non-empty, `required_actions == []`, exit 0.
  3. hyprctl returns `[]` → unsupported with Hyprland reason; no instance but everything else fine.
  4. missing packages (`jq`, `wl-paste`, `notify-send` absent, gi import fails): one `install-packages` action, severity `required`, command exactly `sudo pacman -S --needed wl-clipboard libnotify jq python-gobject gtk3 gtk-layer-shell`.
  5. UFW active (system scope) **with** `--controller-ip 192.0.2.10` → `firewall-lan` with exact command; UFW active without ip → no `firewall-lan`; UFW inactive → none; UFW active + `tailscale0` dir in `DIARCHY_SYS_NET` → `firewall-tailscale` exact command; no tailscale0 → none. Both severities `recommended`.
  6. KVM-only host (no sunshine, no `--streaming`) with unwritable `/dev/uinput` → **no** `uinput-access`/`lid-policy`; with `--streaming` → `uinput-access` (required) and, when `HandleLidSwitch` unset or `suspend`, `lid-policy` (recommended); `HandleLidSwitch=ignore` in `etc/systemd/logind.conf.d/x.conf` → no `lid-policy`; later-dir override beats earlier; writable uinput (temp file) → no `uinput-access`; sunshine binary present (without flag) also triggers both checks.
  7. Components/kvm: lan-mouse installed (fake binary printing `lan-mouse 0.11.0`) with unit active and fake `ss` listing `UNCONN 0 0 0.0.0.0:4242 0.0.0.0:*` → `{installed: True, version: "0.11.0", service_active: True, listening_udp: True}`; absent → `{False, None, False, False}`; pem present + fake openssl printing `sha256 Fingerprint=aa:bb:...` → uppercase `host_fingerprint`; garbage output → `None`; config with two authorized FPs (one unmanaged-written) → both uppercase in order; clipboard/file_portal/sunshine flags follow their paths and units; Tailscale interface present does not alter components.
  8. `session.graphical_session_active` mirrors fake systemctl.
  9. Degradation (R5): no `hyprctl`, no `systemctl`, no `ss`, no `openssl` binaries at all → exit 0 valid envelope with `supported False` and false/null components; a hanging fake `ss` is not exercised (timeout covered by Task 1), but a fake `ss` that exits 1 is.
  10. `--controller-ip` invalid set: `2001:db8::1`, `host.example`, `1.2.3`, `192.0.2.1;ls`, `192.000.002.001`, `" 192.0.2.1"`, `192.0.2.256`, empty string → exit 64, `usage`; unknown flag, repeated `--streaming`, `--controller-ip` without value → 64. Valid: `192.0.2.10`.
  11. The fake `sudo` log is empty across all scenarios above (no `sudo` executed) — assert in `tearDown`.

- [ ] **Step 2: RED** — `python3 -m unittest tests.test_diarchy_host_probe -v` → FAIL.
- [ ] **Step 3: Implement** `probe.py`; register in `cli.DISPATCH`.
- [ ] **Step 4: GREEN** — `python3 -m unittest tests.test_diarchy_host_probe tests.test_diarchy_host_platform tests.test_diarchy_host_kvm tests.test_diarchy_host_kvmconfig tests.test_diarchy_host_cli -v` → PASS.
- [ ] **Step 5: Commit**

```bash
git add host/diarchy_host/probe.py host/diarchy_host/cli.py tests/test_diarchy_host_probe.py
git commit -m "feat(host-agent): add probe with platform, components and required actions"
```

---

### Task 6: `install-lan-mouse.sh` delegates config to the agent; doctor checks the agent

**Files:**
- Modify: `host/install-lan-mouse.sh`, `host/diarchy-kvm-host-doctor`, `tests/test_lan_mouse_host.py`

**Interfaces:**
- Consumes: Task 4 `diarchy-host kvm configure` / `kvm authorize --fingerprint FP`.
- Produces: `install-lan-mouse.sh` (dry-run, argument parsing, checksum/version checks unchanged) which, after installing binary, doctor and unit file, calls `"$DIARCHY_HOST_BIN" kvm configure` (default `DIARCHY_HOST_BIN=$HOME/.local/bin/diarchy-host`) and, when `--authorize-fingerprint FP` was given, `"$DIARCHY_HOST_BIN" kvm authorize --fingerprint FP`, **before** `daemon-reload`/`reenable`/`restart`. Missing/non-executable agent → `install-lan-mouse.sh: diarchy-host agent is not installed (run install-host.sh)` exit 1 **before any mutation** (check right after argument validation and the dry-run branch). Agent non-zero exit → script fails with `install-lan-mouse.sh: diarchy-host kvm <sub> failed: <agent stdout first line>`. The script no longer writes `config.toml`, no longer creates the backup, and no longer creates `$CONFIG_DIR` itself. `diarchy-kvm-host-doctor` gains, after the version check, `DIARCHY_HOST_BIN` (default `$HOME/.local/bin/diarchy-host`) must be executable and `"$DIARCHY_HOST_BIN" version` stdout must contain `"ok":true` → `[ok] diarchy-host agent present` else `[fail] diarchy-host agent is missing or unhealthy: <path>` (exit 1). All remaining doctor checks stay.

- [ ] **Step 1: Update tests first** in `tests/test_lan_mouse_host.py`:
  - `make_environment` gets `DIARCHY_HOST_BIN` = the repo launcher `host/diarchy-host` and `PATH` keeps system python (so the launcher's shebang works); a new `make_environment(agent="real"|"missing"|"failing")` option, where `failing` is a script exiting 1 printing a JSON error line.
  - `test_install_is_idempotent_and_writes_trusted_receiver_config` (real agent): still asserts binary/unit/doctor installed, config 0600, `"{MAC_FINGERPRINT}" = "mac"` (lowercase form), `port = 4242`, `capture_backend = "layer-shell"`, no `[[clients]]`, plus header `# Managed by diarchy`; second run leaves exactly one config file and **no backup** files; systemctl calls order: `daemon-reload`, `reenable`, `restart` all present.
  - New: without `--authorize-fingerprint` the config has an empty `[authorized_fingerprints]` and the `[next]` hint is printed; with an existing authorized FP and no flag, the FP is preserved (configure semantics).
  - New: missing agent → exit 1, message names `install-host.sh`, nothing created under `home` (`.local`, `.config` absent) and systemctl log empty.
  - New: failing agent → non-zero exit, stderr contains `kvm configure failed`.
  - New: mixed-case fingerprint flag accepted (script validates case-insensitively); malformed still exit 64 before mutation (existing test stays).
  - Unmanaged pre-existing `config.toml` is backed up by the agent path (`config.toml.backup.*` exists, byte-identical).
  - Doctor tests: add agent presence to the passing environment; new failing case without the agent → `diarchy-host agent` in stderr.
- [ ] **Step 2: RED** — `python3 -m unittest tests.test_lan_mouse_host -v` → FAIL (new/changed cases).
- [ ] **Step 3: Implement** the script and doctor changes (remove lines that render/backup `config.toml`; keep `install -d` for `OPT_ROOT BIN_DIR CACHE_DIR UNIT_DIR`; update the `--dry-run` plan text: replace "Preserve unrelated configuration" with "Write the Lan Mouse configuration through the diarchy-host agent").
- [ ] **Step 4: GREEN** — `python3 -m unittest tests.test_lan_mouse_host -v` → PASS.
- [ ] **Step 5: Commit**

```bash
git add host/install-lan-mouse.sh host/diarchy-kvm-host-doctor tests/test_lan_mouse_host.py
git commit -m "feat(host): write Lan Mouse host config through the diarchy-host agent"
```

---

### Task 7: `install-host.sh` orchestrator (§7)

**Files:**
- Create: `host/install-host.sh` (0755), `tests/test_install_host.py`

**Interfaces:**
- Consumes: the whole agent (`probe --streaming`), sub-installers `host/install-lan-mouse.sh`, `host/install-clipboard-file-transfer.sh`, `host/install.sh` (t4; stubbed in tests).
- Produces: `install-host.sh [--with-streaming]`; any other argument → `usage` text on stderr, exit 64. Behavior, in order:
  1. Locate `SCRIPT_DIR`; require `python3` and `diarchy_host/` + `diarchy-host` + `VERSION` next to the script, else `[fail] install-host.sh: <reason>` exit 2.
  2. Install the agent: stage `diarchy_host/` (excluding `__pycache__`) and `VERSION` into `${DIARCHY_AGENT_DIR:-$HOME/.local/share/diarchy/agent}.new.$$`, swap atomically (`mv` old→`.old.$$`, new→target, remove old), `install -m 755 diarchy-host "$HOME/.local/bin/diarchy-host"` (dirs 0700 where created; re-runs idempotent).
  3. Run `"$HOME/.local/bin/diarchy-host" probe [--streaming]` (`--streaming` iff `--with-streaming`; no `--controller-ip`); parse JSON with `python3`. Non-JSON/`ok:false` → `[fail] install-host.sh: diarchy-host probe failed: <message>` exit 2. `platform.supported == false` → `[fail] install-host.sh: unsupported platform: <reasons joined with "; ">` exit 2 and **no sub-installer runs**. Any `required_actions` with severity `required` → `[fail] install-host.sh: required host actions are pending` followed by, per required action, `  - <id>: <title>` and `    <command>` lines (all on stderr), exit 2, **no sub-installer runs**. `recommended` actions are printed as `[warn] <id>: <command>` on stdout and do not block.
  4. Run, with `bash`, in order: `install-lan-mouse.sh`, `install-clipboard-file-transfer.sh`, and with `--with-streaming` `install.sh`; a failing sub-installer aborts with `[fail] install-host.sh: <name> failed` exit 1 (its own output is already shown).
  5. Final stdout line `[ok] Diarchy host installed (<VERSION>)` (R9).
  No `sudo` anywhere in the script.

- [ ] **Step 1: Write failing tests** (`tests/test_install_host.py`) — harness builds a temp payload tree `payload/{VERSION,host/}` by copying the real `install-host.sh`, `diarchy-host`, `diarchy_host/` (ignore `__pycache__`) and `VERSION`, plus stub `install-lan-mouse.sh`, `install-clipboard-file-transfer.sh`, `install.sh` that append `<name>` to `$FAKE_LOG` and assert the agent launcher already exists (`test -x "$HOME/.local/bin/diarchy-host"`). The environment reuses the `AgentCase` fixtures (os-release, machine, hyprctl, systemctl, package stubs, fake `sudo`) and drives the real agent end-to-end. `PATH` is the tools dir plus a harness-created `sysbin/` dir of symlinks to only the system utilities the script needs (`bash env install mkdir mv rm cp cat dirname python3`), so missing-package scenarios stay faithful. Cases:
  1. Supported host, all packages: exit 0; stub order `install-lan-mouse.sh` then `install-clipboard-file-transfer.sh` (no streaming installer); last stdout line `[ok] Diarchy host installed (1.0.0)`; `~/.local/bin/diarchy-host` is executable and `~/.local/share/diarchy/agent/{VERSION,diarchy_host/cli.py}` exist, no `__pycache__` copied.
  2. `--with-streaming` also runs `install.sh` last and passes `--streaming` to probe (a fake wrapper is unnecessary: with `DIARCHY_UINPUT_PATH` unwritable the run fails with `uinput-access`, with a writable temp file it succeeds).
  3. Unsupported platform (non-Arch os-release): exit 2, stderr starts `[fail] install-host.sh: unsupported platform:` with the reason, **no stub ran**, agent still installed.
  4. Missing packages: exit 2, stderr lists `install-packages` and the exact `sudo pacman -S --needed …` command, no stub ran.
  5. Recommended actions only (UFW active + `tailscale0`): exit 0 and `[warn] firewall-tailscale: sudo ufw allow …` on stdout.
  6. Probe broken (agent launcher replaced by a script printing garbage) → exit 2 with `diarchy-host probe failed`. Implement by pointing the payload's `diarchy_host/cli.py` copy at a failing stub in the temp tree.
  7. Sub-installer failure (stub exits 3) → exit 1, later stubs not run, `[fail] install-host.sh: install-lan-mouse.sh failed`.
  8. Re-run is idempotent (second run exits 0, agent files replaced, no `.new`/`.old` leftovers); bad argument → exit 64.
  9. Version printing: with `payload/VERSION` = `2.3.4` and `host/VERSION` = `1.0.0` the final line shows `2.3.4`; without `payload/VERSION` it shows `1.0.0`.
  10. The fake `sudo` on PATH is never invoked.

- [ ] **Step 2: RED** — `python3 -m unittest tests.test_install_host -v` → FAIL.
- [ ] **Step 3: Implement** `install-host.sh` as specified.
- [ ] **Step 4: GREEN** — `python3 -m unittest tests.test_install_host -v` → PASS.
- [ ] **Step 5: Commit**

```bash
git add host/install-host.sh tests/test_install_host.py
git commit -m "feat(host): add install-host.sh orchestrator gated on agent probe"
```

---

## Self-Review

- **Spec coverage:** §6.1 envelope/exit codes → T1; §6.2 version/probe/kvm → T1,T4,T5 (`stream` is t4's module, guarded dispatch in T1); config writer single source → T3,T6; §6.3 every field and action rule incl. `uinput-access`, `lid-policy`, package mapping, `--controller-ip` → T2,T5; §6.4 seam → T2; §7 `install-host.sh` ordering/gating/final line → T7; §10 no sudo/0600/fixed strings → T3,T5,T7 tests; §11 unsupported/pending-action behavior → T5,T7; §12 Python `unittest` with fakes → all tasks; brief acceptance scenarios (supported Omarchy, non-Arch, no Hyprland, missing packages, UFW with/without ip, Tailscale) → T5 cases 1-5.
- **Placeholder scan:** none; every spec value is copied verbatim from the spec.
- **Type consistency:** `normalize_fingerprint`, `render`, `write`, `read_authorized`, `preserved_fingerprint` names are used identically in Tasks 3-6; `handle(args) -> dict` is the single handler signature; `DISPATCH` is the registry in `cli.py`.
- **Review Focus** items map to: 1 → T3/T4 tests 1-2/6; 2 → T3 test 4, T6; 3 → T1; 4 → T5 test 9; 5 → T5 test 10.
