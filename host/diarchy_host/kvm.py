from __future__ import annotations

from . import kvmconfig, outputs
from .common import (
    UNIT_KVM, UNIT_STREAM_DISPLAY, UNIT_STREAM_SUNSHINE, AgentError, config_home, require_hyprland_session,
    systemctl_user, unit_active, unit_running,
)

SUBCOMMANDS = ("configure", "authorize", "start", "stop", "status")
USAGE = "usage: diarchy-host kvm {configure|authorize --fingerprint FP|start|stop|status}"


def _service_failed(action: str, unit: str, result) -> AgentError:
    detail = (result.stderr or result.stdout or "").strip()
    return AgentError("service_failed", f"systemctl --user {action} {unit} failed" + (f": {detail}" if detail else ""))


def _systemctl(action: str, unit: str) -> None:
    result = systemctl_user(action, unit)
    if result.returncode != 0:
        raise _service_failed(action, unit, result)


def _restart_if_active() -> None:
    if unit_running(UNIT_KVM):
        _systemctl("restart", UNIT_KVM)


def _no_arguments(sub: str, args: list[str]) -> None:
    if args:
        raise AgentError("usage", f"kvm {sub} takes no arguments\n{USAGE}")


def _configure(args: list[str]) -> dict:
    _no_arguments("configure", args)
    fingerprints = kvmconfig.preserved_fingerprints()
    kvmconfig.write(fingerprints)
    _restart_if_active()
    return {"authorized": fingerprints[0] if fingerprints else None}


def _authorize(args: list[str]) -> dict:
    if len(args) != 2 or args[0] != "--fingerprint":
        raise AgentError("usage", f"kvm authorize requires exactly --fingerprint FP\n{USAGE}")
    fingerprint = kvmconfig.normalize_fingerprint(args[1])
    kvmconfig.write([*kvmconfig.preserved_fingerprints(), fingerprint])
    _restart_if_active()
    return {"authorized": fingerprint}


def _start(args: list[str]) -> dict:
    _no_arguments("start", args)
    if not (config_home() / "systemd" / "user" / UNIT_KVM).is_file():
        raise AgentError("not_installed", f"{UNIT_KVM} is not installed")
    require_hyprland_session()
    for unit in (UNIT_STREAM_SUNSHINE, UNIT_STREAM_DISPLAY):
        if unit_running(unit):
            _systemctl("stop", unit)
    outputs.restore_display()
    _systemctl("start", UNIT_KVM)
    if not unit_active(UNIT_KVM):
        raise AgentError("service_failed", f"{UNIT_KVM} did not become active")
    return {"service_active": True}


def _stop(args: list[str]) -> dict:
    _no_arguments("stop", args)
    if not unit_running(UNIT_KVM):
        return {"service_active": False}
    _systemctl("stop", UNIT_KVM)
    if unit_running(UNIT_KVM):
        raise AgentError("service_failed", f"{UNIT_KVM} is still active after stop")
    return {"service_active": False}


def _status(args: list[str]) -> dict:
    _no_arguments("status", args)
    return {"service_active": unit_active(UNIT_KVM)}


_HANDLERS = {
    "configure": _configure,
    "authorize": _authorize,
    "start": _start,
    "stop": _stop,
    "status": _status,
}


def handle(args: list[str]) -> dict:
    if not args or args[0] not in _HANDLERS:
        raise AgentError("usage", USAGE)
    return _HANDLERS[args[0]](args[1:])
