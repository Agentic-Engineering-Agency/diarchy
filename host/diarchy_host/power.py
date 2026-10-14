"""Lid and battery facts, read from procfs/sysfs (injectable roots) and logind drop-ins. Never runs a command."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PROC = "/proc"
DEFAULT_SYSFS = "/sys"
DEFAULT_LOGIND_DIRS = "/usr/lib/systemd:/run/systemd:/etc/systemd"
LID_SWITCH_DEFAULT = "suspend"


def proc_root() -> Path:
    return Path(os.environ.get("DIARCHY_PROC_ROOT") or DEFAULT_PROC)


def sysfs_root() -> Path:
    return Path(os.environ.get("DIARCHY_SYSFS_ROOT") or DEFAULT_SYSFS)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _lid_dirs() -> list[Path]:
    try:
        return sorted(path for path in (proc_root() / "acpi/button/lid").iterdir() if path.is_dir())
    except OSError:
        return []


def has_lid() -> bool:
    """Any ACPI lid switch entry exists (a laptop); desktops have none."""
    return bool(_lid_dirs())


def lid_closed() -> bool | None:
    """`True` when any lid reports `closed`, `False` when all report `open`, `None` when unknown."""
    states = []
    for directory in _lid_dirs():
        _, _, value = _read(directory / "state").partition(":")
        states.append(value.strip().lower())
    if "closed" in states:
        return True
    return False if states and all(state == "open" for state in states) else None


def has_battery() -> bool:
    try:
        entries = list((sysfs_root() / "class/power_supply").iterdir())
    except OSError:
        return False
    return any(_read(entry / "type").strip() == "Battery" for entry in entries)


def _lid_switch_policy() -> str:
    dirs = [item for item in (os.environ.get("DIARCHY_LOGIND_DIRS") or DEFAULT_LOGIND_DIRS).split(":") if item]
    value = LID_SWITCH_DEFAULT
    for directory in dirs:
        base = Path(directory)
        files = [base / "logind.conf"]
        try:
            files += sorted((base / "logind.conf.d").glob("*.conf"), key=lambda path: path.name)
        except OSError:
            pass
        for file in files:
            in_login = False
            for raw in _read(file).splitlines():
                line = raw.strip()
                if not line or line[0] in "#;":
                    continue
                if line.startswith("["):
                    in_login = line == "[Login]"
                    continue
                key, separator, setting = line.partition("=")
                if in_login and separator and key.strip() == "HandleLidSwitch":
                    value = setting.strip() or LID_SWITCH_DEFAULT
    return value


def lid_policy_ok() -> bool:
    """`True` when logind does not suspend the host on lid close (the last `HandleLidSwitch` wins)."""
    return _lid_switch_policy() != "suspend"
