"""Hyprland output selection for streaming."""
from __future__ import annotations

import json
import re

from . import common
from .common import AgentError

HEADLESS_OUTPUT = "DIARCHY"
INTERNAL_PREFIXES = ("eDP-", "LVDS-", "DSI-")
OUTPUT_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def is_internal(name: str) -> bool:
    return name.startswith(INTERNAL_PREFIXES)


def list_monitors() -> list[dict] | None:
    """Parsed `hyprctl -j monitors all`, or `None` when it cannot be run or parsed."""
    try:
        result = common.run([common.resolve_bin("HYPRCTL_BIN", "hyprctl"), "-j", "monitors", "all"])
    except AgentError:
        return None
    if result.returncode != 0:
        return None
    try:
        monitors = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(monitors, list) or not all(isinstance(m, dict) for m in monitors):
        return None
    return monitors


def resolve_output(requested: str, monitors: list[dict] | None) -> str:
    """`auto`: the enabled internal panel, else the focused enabled monitor, else the first enabled one, else headless.

    The headless output is used only when no physical monitor is enabled. `DIARCHY` is never a candidate.
    A named value is validated and returned as it is."""
    if requested != "auto":
        if OUTPUT_RE.fullmatch(requested) is None:
            raise AgentError("usage", f"invalid output name: {requested!r}")
        return requested
    enabled = [m for m in monitors or [] if isinstance(m.get("name"), str) and m["name"] != HEADLESS_OUTPUT
               and not m.get("disabled")]
    internal = next((m["name"] for m in enabled if is_internal(m["name"])), None)
    if internal:
        return internal
    focused = next((m["name"] for m in enabled if m.get("focused")), None)
    return focused or (enabled[0]["name"] if enabled else HEADLESS_OUTPUT)


def restore_display() -> None:
    """Run `diarchy-display restore`: it moves the headless workspaces back to the physical monitor.

    Any failure is `service_failed` with the tool's stderr text."""
    display = common.resolve_bin("DIARCHY_DISPLAY_BIN", str(common.home() / ".local/bin/diarchy-display"))
    try:
        result = common.run([display, "restore"])
    except AgentError as error:
        raise AgentError("service_failed", f"diarchy-display restore failed: {error.message}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise AgentError("service_failed", "diarchy-display restore failed" + (f": {detail}" if detail else ""))
