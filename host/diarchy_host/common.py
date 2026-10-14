from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

UNIT_KVM = "diarchy-kvm.service"
UNIT_STREAM_DISPLAY = "diarchy-display.service"
UNIT_STREAM_SUNSHINE = "diarchy-sunshine.service"
ERROR_CODES = (
    "usage", "unsupported_platform", "not_installed", "no_session", "service_failed", "invalid_fingerprint",
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


def hyprland_session_present() -> bool:
    """A live Hyprland user session: `hyprctl instances -j` lists an entry with a pid, an instance and a wl_socket.

    The same selection rule as the Sunshine wrapper and `diarchy-display`. After an Omarchy logout only SDDM's
    greeter Hyprland is left, which belongs to another user and is invisible here.
    """
    try:
        result = run([resolve_bin("HYPRCTL_BIN", "hyprctl"), "instances", "-j"])
    except AgentError:
        return False
    if result.returncode != 0:
        return False
    try:
        instances = json.loads(result.stdout)
    except ValueError:
        return False
    if not isinstance(instances, list):
        return False
    return any(
        isinstance(entry, dict)
        and isinstance(entry.get("pid"), int) and entry["pid"] > 0
        and entry.get("instance") and entry.get("wl_socket")
        for entry in instances
    )


def require_hyprland_session() -> None:
    """Raise `no_session` before any unit is started or stopped when nobody is logged in to Hyprland."""
    if not hyprland_session_present():
        raise AgentError("no_session", "no Hyprland session is running for this user")


def home() -> Path:
    return Path(os.environ["HOME"])


def config_home() -> Path:
    """`${XDG_CONFIG_HOME:-~/.config}`."""
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base else home() / ".config"


def agent_root() -> Path:
    override = os.environ.get("DIARCHY_AGENT_DIR")
    return Path(override) if override else Path(__file__).resolve().parents[1]


def agent_version() -> str:
    try:
        return (agent_root() / "VERSION").read_text().strip()
    except OSError as error:
        raise AgentError("internal", f"cannot read agent VERSION: {error.strerror or error}") from error


def systemctl_user(*args: str) -> subprocess.CompletedProcess[str]:
    return run([resolve_bin("SYSTEMCTL_BIN", "systemctl"), "--user", *args])


def _active(argv_prefix: list[str], unit: str) -> bool:
    try:
        return run([*argv_prefix, "is-active", "--quiet", unit]).returncode == 0
    except AgentError:
        return False


def unit_active(unit: str) -> bool:
    return _active([resolve_bin("SYSTEMCTL_BIN", "systemctl"), "--user"], unit)


_RUNNING_STATES = frozenset({"active", "activating", "reloading", "deactivating"})


def unit_running(unit: str) -> bool:
    """True while the user unit is alive or transitioning (incl. `activating (auto-restart)`); False on any failure."""
    try:
        result = systemctl_user("show", "-p", "ActiveState", "--value", unit)
    except AgentError:
        return False
    return result.returncode == 0 and result.stdout.strip() in _RUNNING_STATES


def unit_active_system(unit: str) -> bool:
    return _active([resolve_bin("SYSTEMCTL_BIN", "systemctl")], unit)
