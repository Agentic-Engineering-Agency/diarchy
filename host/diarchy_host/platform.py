from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from diarchy_host.common import (
    AgentError, home, hyprland_session_present, resolve_bin, run, systemctl_user, unit_active,
)

ADAPTER_HYPRLAND_ARCH = "hyprland-arch"
ADAPTER_UNSUPPORTED = "unsupported"
DEFAULT_OS_RELEASE = "/etc/os-release"
GI_CHECK = (
    "import sys\n"
    "try:\n"
    "    import gi\n"
    "except ImportError:\n"
    "    sys.exit(1)\n"
    "for package, namespace, version in (('gtk3', 'Gtk', '3.0'), ('gtk-layer-shell', 'GtkLayerShell', '0.1')):\n"
    "    try:\n"
    "        gi.require_version(namespace, version)\n"
    "    except (ValueError, ImportError):\n"
    "        print(package)\n"
)
GI_PACKAGES = ["python-gobject", "gtk3", "gtk-layer-shell"]
ACTIVE_MANAGER_STATES = {"running", "degraded"}


@dataclass(frozen=True)
class PlatformInfo:
    supported: bool
    adapter: str
    os_id: str | None
    flavor: str | None
    compositor: str | None
    machine: str | None
    reasons: list[str]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


class PlatformAdapter(Protocol):
    def detect(self) -> PlatformInfo: ...

    def missing_packages(self) -> list[str]: ...

    def install_command(self, packages: list[str]) -> str: ...

    def kvm_capture_backend(self) -> str: ...

    def graphical_session_active(self) -> bool: ...


def _read_os_release() -> dict[str, str] | None:
    path = Path(os.environ.get("DIARCHY_OS_RELEASE") or DEFAULT_OS_RELEASE)
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _user_manager_active() -> bool:
    try:
        result = systemctl_user("is-system-running")
    except AgentError:
        return False
    return result.stdout.strip() in ACTIVE_MANAGER_STATES


def _missing_gi_packages() -> list[str]:
    """Packages behind the GI bundle that are actually absent (the whole bundle if `gi` itself is unusable)."""
    try:
        result = run([resolve_bin("PYTHON_BIN", "python3"), "-c", GI_CHECK])
    except AgentError:
        return list(GI_PACKAGES)
    if result.returncode != 0:
        return list(GI_PACKAGES)
    return [package for package in result.stdout.split() if package in GI_PACKAGES]


class HyprlandArchAdapter:
    name = ADAPTER_HYPRLAND_ARCH

    def detect(self) -> PlatformInfo:
        reasons: list[str] = []
        release = _read_os_release()
        os_id = release.get("ID") if release else None
        family = {os_id.lower()} if os_id else set()
        if release:
            family.update(release.get("ID_LIKE", "").lower().split())
        if "arch" not in family:
            found = os_id or "unreadable os-release"
            reasons.append(f"An Arch Linux family distribution is required (found: {found}).")

        machine = os.environ.get("DIARCHY_MACHINE") or os.uname().machine
        if machine != "x86_64":
            reasons.append(f"An x86_64 machine is required (found: {machine}).")

        # The same rule as `kvm start` and `stream start` (pid, instance and wl_socket), so a probe that says
        # "supported" never meets `no_session` there.
        compositor = "hyprland" if hyprland_session_present() else None
        if compositor is None:
            reasons.append("No running Hyprland session was found for this user (hyprctl instances).")

        if not _user_manager_active():
            reasons.append("The systemd user manager is not running.")

        flavor = "omarchy" if (home() / ".local/share/omarchy").exists() else None
        supported = not reasons
        return PlatformInfo(
            supported=supported,
            adapter=self.name if supported else ADAPTER_UNSUPPORTED,
            os_id=os_id, flavor=flavor, compositor=compositor, machine=machine, reasons=reasons,
        )

    def missing_packages(self) -> list[str]:
        def present(binary: str) -> bool:
            return shutil.which(binary) is not None

        missing: list[str] = []
        if not (present("wl-copy") and present("wl-paste")):
            missing.append("wl-clipboard")
        if not present("rsync"):
            missing.append("rsync")
        if not present("notify-send"):
            missing.append("libnotify")
        if not present(resolve_bin("OPENSSL_BIN", "openssl")):
            missing.append("openssl")
        if not present("jq"):
            missing.append("jq")
        if not present("curl"):
            missing.append("curl")
        missing.extend(_missing_gi_packages())
        return missing

    def install_command(self, packages: list[str]) -> str:
        return "sudo pacman -S --needed " + " ".join(packages)

    def kvm_capture_backend(self) -> str:
        return "layer-shell"

    def graphical_session_active(self) -> bool:
        return unit_active("graphical-session.target")


class UnsupportedAdapter:
    name = ADAPTER_UNSUPPORTED

    def __init__(self, info: PlatformInfo) -> None:
        self._info = info

    def detect(self) -> PlatformInfo:
        return self._info

    def missing_packages(self) -> list[str]:
        return []

    def install_command(self, packages: list[str]) -> str:
        raise AgentError("unsupported_platform", "Package installation is not supported on this platform.")

    def kvm_capture_backend(self) -> str:
        raise AgentError("unsupported_platform", "No KVM capture backend exists for this platform.")

    def graphical_session_active(self) -> bool:
        return False


def select_adapter() -> PlatformAdapter:
    candidates: list[PlatformAdapter] = [HyprlandArchAdapter()]
    first_failure: PlatformInfo | None = None
    for candidate in candidates:
        info = candidate.detect()
        if info.supported:
            return candidate
        first_failure = first_failure or info
    assert first_failure is not None
    return UnsupportedAdapter(replace(first_failure, adapter=ADAPTER_UNSUPPORTED))
