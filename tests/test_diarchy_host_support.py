from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "host"
LAUNCHER = HOST / "diarchy-host"
FP_A = ":".join(["AA"] * 32)
FP_B = ":".join(["BB"] * 32)


_FAKE_SYSTEMCTL = """\
import os, sys
from pathlib import Path

state, fail, manager = Path({state!r}), {fail!r}, {manager!r}
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(" ".join(argv) + "\\n")
scope = "user" if "--user" in argv else "system"
quiet = "--quiet" in argv
words = [word for word in argv if not word.startswith("--")]
verb, unit = (words + ["", ""])[:2]
if verb == "show":
    unit = words[-1]
if verb == "is-system-running":
    print(manager)
    sys.exit(0 if manager == "running" else 1)
if f"{{verb}} {{unit}}" in fail:
    sys.exit(1)
key = state / f"{{scope}}:{{unit}}"
current = (key.read_text().strip() or "active") if key.exists() else "inactive"
if verb == "is-active":
    if not quiet:
        print(current)
    sys.exit(0 if current == "active" else 3)
if verb == "show" and "ActiveState" in words:
    print(current)
    sys.exit(0)
if verb in ("start", "restart"):
    key.write_text("active")
elif verb == "stop":
    key.unlink(missing_ok=True)
"""

_FAKE_SUDO = """\
import os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(" ".join(["sudo", *sys.argv[1:]]) + "\\n")
"""


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

    def fake_systemctl(self, active: Iterable[str] = (), fail: Iterable[str] = (), manager: str = "running",
                       activating: Iterable[str] = ()) -> Path:
        """Install a stateful fake `systemctl` as SYSTEMCTL_BIN (also placed on the sandbox PATH).

        active:  units that start out active; a bare name is a `--user` unit, `system:<unit>` a system unit.
        activating: units that start out in `activating` (`is-active` fails, `show -p ActiveState` prints it).
        fail:    `"<verb> <unit>"` pairs (e.g. `"start diarchy-kvm.service"`) that exit 1 without changing state.
        manager: what `is-system-running` prints; it exits 0 only for `running`.
        State lives in `self.root/"state"` as one file per active unit (`user:<unit>` / `system:<unit>`);
        `start`/`restart` create it, `stop` removes it, other verbs exit 0. Every call appends its argv
        (without the program name, e.g. `--user restart diarchy-kvm.service`) to FAKE_LOG.
        """
        state = self.root / "state"
        state.mkdir(exist_ok=True)
        for unit, value in [(unit, "") for unit in active] + [(unit, "activating") for unit in activating]:
            (state / (unit if unit.startswith("system:") else f"user:{unit}")).write_text(value)
        body = _FAKE_SYSTEMCTL.format(state=str(state), fail=sorted(fail), manager=manager)
        return self.tool("systemctl", body, "SYSTEMCTL_BIN")

    def fake_sudo(self) -> Path:
        """A real `sudo` on the sandbox PATH that only logs; the agent must never invoke it (ADR 0002)."""
        return self.tool("sudo", _FAKE_SUDO)

    def fake_hyprctl(self, instances: str | None = None, exit_code: int = 0) -> Path:
        """Install a fake `hyprctl` as HYPRCTL_BIN that answers `instances -j` (and logs nothing).

        `instances` is the raw stdout (default: one live session); `exit_code` its exit status.
        """
        if instances is None:
            instances = json.dumps([{"pid": 4242, "instance": "abc123", "wl_socket": "wayland-1", "time": 1}])
        body = f"import sys\nsys.stdout.write({instances!r})\nsys.exit({exit_code})\n"
        return self.tool("hyprctl", body, "HYPRCTL_BIN")

    def sudo_calls(self) -> list[str]:
        return [line for line in self.calls() if line.startswith("sudo")]
