"""`diarchy-host report`: a redacted hardware and software summary for compatibility reports."""
from __future__ import annotations

import getpass
import importlib
import os
import re
import socket
from pathlib import Path
from typing import Any, Callable, TypeVar

from diarchy_host import platform, probe
from diarchy_host.common import AgentError, agent_version, home, resolve_bin, run

T = TypeVar("T")
REDACTED = "[redacted]"
NO_ENCODER_REASON = "No supported hardware video encoder was detected."
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b")
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
_HOME_PATH_RE = re.compile(r"(?:/home|/Users)/[^\s/:;,'\"]+(?:/[^\s:;,'\"]*)?")
_SERIAL_KEY_RE = re.compile(r"serial|^sn$", re.IGNORECASE)
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+[\w.+~-]*")
_MIN_TOKEN = 3


def _guard(call: Callable[[], T], fallback: T | None = None) -> T | None:
    """A failing source yields `fallback` (null), never an error."""
    try:
        return call()
    except Exception:  # noqa: BLE001 - the report must survive any single failing source
        return fallback


def _module(name: str) -> Any:
    try:
        return importlib.import_module(f"diarchy_host.{name}")
    except ImportError:
        return None


def _call(module: str, function: str, *args: Any) -> Any:
    """`diarchy_host.<module>.<function>(*args)`, or `None` if the module, function or call is unavailable."""
    def invoke() -> Any:
        return getattr(_module(module), function)(*args)

    return _guard(invoke)


def _sysfs() -> Path:
    return Path(os.environ.get("DIARCHY_SYSFS_ROOT") or "/sys")


def _proc() -> Path:
    return Path(os.environ.get("DIARCHY_PROC_ROOT") or "/proc")


def _text(path: Path) -> str | None:
    def read() -> str | None:
        return path.read_text(errors="replace").strip() or None

    return _guard(read)


def _version_in(text: str | None) -> str | None:
    match = _VERSION_RE.search(text or "")
    return match.group(0) if match else None


def _os() -> dict:
    release = _guard(platform._read_os_release)
    omarchy_path = Path(os.environ.get("OMARCHY_PATH") or home() / ".local/share/omarchy")

    def channel() -> str | None:
        result = run([resolve_bin("OMARCHY_VERSION_CHANNEL_BIN", "omarchy-version-channel")])
        value = result.stdout.strip() if result.returncode == 0 else ""
        return value if re.fullmatch(r"[a-z][\w-]{0,31}", value) else None

    return {
        "id": release.get("ID") if release else None,
        "kernel": _guard(lambda: os.uname().release),
        "omarchy_version": _version_in(_text(omarchy_path / "version")),
        "omarchy_channel": _guard(channel),
    }


def _hyprland_version() -> str | None:
    def read() -> str | None:
        result = run([resolve_bin("HYPRCTL_BIN", "hyprctl"), "version", "-j"])
        if result.returncode != 0:
            return None
        import json

        data = json.loads(result.stdout)
        return _version_in(str(data.get("version") or data.get("tag") or ""))

    return _guard(read)


def _monitor(raw: dict) -> dict:
    def number(key: str) -> int | None:
        value = raw.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    refresh = raw.get("refreshRate")
    name = raw.get("name") if isinstance(raw.get("name"), str) else None
    return {
        "name": name,
        "width": number("width"),
        "height": number("height"),
        "refresh": round(float(refresh), 1) if isinstance(refresh, (int, float)) else None,
        "internal": bool(_call("outputs", "is_internal", name)) if name else None,
        "enabled": not raw["disabled"] if isinstance(raw.get("disabled"), bool) else None,
    }


def _hyprland(monitors: list[dict] | None) -> dict:
    return {"version": _hyprland_version(),
            "monitors": None if monitors is None else [_monitor(m) for m in monitors]}


def _capture(monitors: list[dict] | None) -> dict:
    output = _call("outputs", "resolve_output", "auto", monitors)
    found = _call("gpu", "resolve_capture_gpu", output, _sysfs()) if output else None
    render_node = getattr(found, "render_node", None)
    encoder = getattr(found, "encoder", None) if found else None
    reason = _call("gpu", "unsupported_reason", found) if encoder is None else None
    if encoder is None and not (isinstance(reason, str) and reason.strip()):
        reason = NO_ENCODER_REASON
    return {
        "output": output,
        "render_node": os.path.basename(render_node) if render_node else None,
        "driver": getattr(found, "driver", None),
        "encoder": encoder,
        "unsupported_reason": reason,
    }


def _tool_version(binary: Path) -> str | None:
    def read() -> str | None:
        result = run([str(binary), "--version"])
        return _version_in(result.stdout) if result.returncode == 0 else None

    return _guard(read)


def _sunshine() -> dict:
    binary = home() / ".local" / "bin" / "sunshine"
    installed = bool(_guard(lambda: probe._is_executable(binary), False))
    return {"installed": installed, "version": _tool_version(binary) if installed else None}


def _lan_mouse() -> dict:
    binary = _guard(probe._lan_mouse_bin)
    installed = bool(binary and _guard(lambda: probe._is_executable(binary), False))
    return {"installed": installed, "version": probe._lan_mouse_version(binary) if installed else None}


def _network() -> dict:
    net = _sysfs() / "class" / "net"

    def interfaces() -> list[Path]:
        return sorted(net.iterdir())

    def virtual(path: Path) -> bool:
        return "/devices/virtual/" in str(path.resolve())

    def wifi() -> bool:
        return any((path / "wireless").exists() for path in interfaces())

    def ethernet_up() -> bool:
        return any(
            not (path / "wireless").exists() and not virtual(path) and _text(path / "operstate") == "up"
            for path in interfaces()
        )

    return {
        "wifi": _guard(wifi, False),
        "ethernet_up": _guard(ethernet_up, False),
        "tailscale": _guard(lambda: (net / "tailscale0").exists(), False),
    }


def _sensitive_tokens() -> list[str]:
    tokens: list[str] = []

    def add(value: str | None) -> None:
        if value and len(value) >= _MIN_TOKEN:
            tokens.append(value)

    hostname = _guard(socket.gethostname)
    add(hostname)
    add(hostname.split(".")[0] if hostname else None)
    add(_guard(getpass.getuser))
    add(_guard(lambda: str(home())))

    def ssids() -> list[str]:
        result = run([resolve_bin("NMCLI_BIN", "nmcli"), "-t", "-f", "ACTIVE,SSID", "device", "wifi"])
        if result.returncode != 0:
            return []
        return [line.split(":", 1)[1].replace("\\:", ":") for line in result.stdout.splitlines()
                if line.startswith("yes:")]

    for ssid in _guard(ssids, []) or []:
        add(ssid)
    return sorted(set(tokens), key=len, reverse=True)


def sanitize(value: Any, tokens: list[str] | None = None) -> Any:
    """Strip IP/MAC addresses, home paths, known host/user/SSID tokens and serial-keyed fields from `value`."""
    if tokens is None:
        tokens = _sensitive_tokens()
    if isinstance(value, dict):
        return {key: sanitize(item, tokens) for key, item in value.items()
                if not (isinstance(key, str) and _SERIAL_KEY_RE.search(key))}
    if isinstance(value, list):
        return [sanitize(item, tokens) for item in value]
    if isinstance(value, str):
        text = _HOME_PATH_RE.sub(REDACTED, value)
        for token in tokens:
            text = re.sub(rf"(?<![\w-]){re.escape(token)}(?![\w-])", REDACTED, text)
        for pattern in (_MAC_RE, _IPV4_RE, _IPV6_RE):
            text = pattern.sub(REDACTED, text)
        return text
    return value


def handle(args: list[str]) -> dict:
    if args:
        raise AgentError("usage", "report takes no arguments")
    sysfs, proc = _sysfs(), _proc()
    monitors = _call("outputs", "list_monitors")
    has_lid = _call("power", "has_lid")
    payload = {
        "diarchy_host_version": agent_version(),
        "os": _os(),
        "hyprland": _hyprland(monitors),
        "gpus": _call("gpu", "gpus", sysfs),
        "capture": _capture(monitors),
        "nvidia": _call("gpu", "nvidia_info", sysfs, proc),
        "sunshine": _sunshine(),
        "lan_mouse": _lan_mouse(),
        "power": {
            "has_lid": has_lid,
            "has_battery": _call("power", "has_battery"),
            "lid_policy_ok": _call("power", "lid_policy_ok") if has_lid is not False else None,
        },
        "network": _network(),
        "sysfs_snapshot": _call("gpu", "snapshot", sysfs),
    }
    return sanitize(payload)
