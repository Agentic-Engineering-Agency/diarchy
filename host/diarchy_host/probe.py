from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Callable, TypeVar

from diarchy_host import gpu, kvmconfig, power, wifi
from diarchy_host.common import (
    UNIT_KVM, UNIT_STREAM_SUNSHINE, AgentError, agent_version, home, resolve_bin, run, unit_active,
    unit_active_system,
)
from diarchy_host.platform import PlatformAdapter, PlatformInfo, select_adapter

KVM_PORT = 4242
UNIT_FILE_PORTAL = "diarchy-file-portal.service"
USAGE = "usage: diarchy-host probe [--controller-ip IPv4] [--streaming]"
DEFAULT_SYS_NET = "/sys/class/net"
DEFAULT_UINPUT = "/dev/uinput"
_VERSION_RE = re.compile(r"lan-mouse\s+v?(\d[\w.+-]*)", re.IGNORECASE)

# Fixed command templates (spec §9.2). The `\n` below is a literal backslash-n inside the shell's single quotes.
UINPUT_COMMAND = (
    "printf 'KERNEL==\"uinput\", SUBSYSTEM==\"misc\", OPTIONS+=\"static_node=uinput\", TAG+=\"uaccess\"\\n'"
    " | sudo tee /etc/udev/rules.d/60-diarchy-uinput.rules"
    " && sudo udevadm control --reload && sudo udevadm trigger --name-match=uinput"
)
LID_COMMAND = (
    "sudo mkdir -p /etc/systemd/logind.conf.d"
    " && printf '[Login]\\nHandleLidSwitch=ignore\\nHandleLidSwitchExternalPower=ignore\\n'"
    " | sudo tee /etc/systemd/logind.conf.d/60-diarchy.conf"
)
# SEC-24: this rule opens UDP 4242 to EVERY peer on the tailnet, not only the Mac. The action text says so and
# shows how to scope it (the IP below is an example, never a real one).
FIREWALL_TAILSCALE_COMMAND = "sudo ufw allow in on tailscale0 to any port 4242 proto udp"
FIREWALL_TAILSCALE_SCOPE_HINT = (
    " The rule opens UDP 4242 to all tailnet peers; to allow only the Mac, use"
    " `sudo ufw allow in on tailscale0 from 100.64.0.20 to any port 4242 proto udp` (replace the example IP"
    " with the Mac's tailnet IP).")
NVIDIA_MODESET_COMMAND = (
    "echo 'options nvidia_drm modeset=1' | sudo tee /etc/modprobe.d/diarchy-nvidia.conf"
    " # then reboot for it to take effect")

T = TypeVar("T")


def _guard(call: Callable[[], T], fallback: T) -> T:
    """R5: an external failure yields the conservative value instead of crashing the probe."""
    try:
        return call()
    except (AgentError, OSError, ValueError):
        return fallback


def _parse_args(args: list[str]) -> tuple[str | None, bool]:
    controller_ip: str | None = None
    streaming = False
    seen: set[str] = set()
    index = 0
    while index < len(args):
        flag = args[index]
        if flag not in ("--controller-ip", "--streaming"):
            raise AgentError("usage", f"unknown argument: {flag}\n{USAGE}")
        if flag in seen:
            raise AgentError("usage", f"{flag} may be given at most once\n{USAGE}")
        seen.add(flag)
        index += 1
        if flag == "--streaming":
            streaming = True
            continue
        if index >= len(args):
            raise AgentError("usage", f"--controller-ip requires a value\n{USAGE}")
        controller_ip = _validated_ipv4(args[index])
        index += 1
    return controller_ip, streaming


def _validated_ipv4(value: str) -> str:
    try:
        canonical = str(ipaddress.IPv4Address(value))
    except ValueError:
        canonical = None
    if canonical != value:
        raise AgentError("usage", f"--controller-ip must be a canonical IPv4 address\n{USAGE}")
    return value


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _local_bin(name: str) -> Path:
    return home() / ".local" / "bin" / name


def _lan_mouse_bin() -> Path:
    return Path(resolve_bin("LAN_MOUSE_BIN", str(_local_bin("lan-mouse"))))


def _lan_mouse_version(binary: Path) -> str | None:
    def read() -> str | None:
        result = run([str(binary), "--version"])
        if result.returncode != 0:
            return None
        match = _VERSION_RE.search(result.stdout)
        return match.group(1) if match else None

    return _guard(read, None)


def _listening_udp() -> bool:
    def read() -> bool:
        result = run([resolve_bin("SS_BIN", "ss"), "-H", "-lun"])
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 4 and fields[3].endswith(f":{KVM_PORT}"):
                return True
        return False

    return _guard(read, False)


def _lan_mouse_component() -> dict:
    binary = _lan_mouse_bin()
    installed = _guard(lambda: _is_executable(binary), False)
    return {
        "installed": installed,
        "version": _lan_mouse_version(binary) if installed else None,
        "service_active": unit_active(UNIT_KVM),
        "listening_udp": _listening_udp(),
    }


def _components() -> dict:
    def executable(name: str) -> bool:
        return _guard(lambda: _is_executable(_local_bin(name)), False)

    portal_unit = home() / ".config" / "systemd" / "user" / UNIT_FILE_PORTAL
    return {
        "lan_mouse": _lan_mouse_component(),
        "clipboard": {"installed": executable("diarchy-clipboard-remote")},
        "file_portal": {
            "installed": executable("diarchy-file-portal") and _guard(portal_unit.exists, False),
            "service_active": unit_active(UNIT_FILE_PORTAL),
        },
        "sunshine": {
            "installed": executable("sunshine"),
            "service_active": unit_active(UNIT_STREAM_SUNSHINE),
        },
    }


def _host_fingerprint() -> str | None:
    def read() -> str | None:
        cert = os.environ.get("LAN_MOUSE_CERT")
        path = Path(cert) if cert else kvmconfig.config_dir() / "lan-mouse.pem"
        if not path.is_file():
            return None
        result = run([resolve_bin("OPENSSL_BIN", "openssl"), "x509", "-in", str(path),
                      "-noout", "-fingerprint", "-sha256"])
        if result.returncode != 0:
            return None
        _, separator, value = result.stdout.strip().partition("=")
        candidate = value.strip().upper()
        return candidate if separator and kvmconfig.FP_RE.fullmatch(candidate) else None

    return _guard(read, None)


def _kvm() -> dict:
    _, authorized = kvmconfig.read_authorized()
    return {"host_fingerprint": _host_fingerprint(), "authorized_fingerprints": authorized, "port": KVM_PORT}


def _action(action_id: str, severity: str, title: str, reason: str, command: str) -> dict:
    return {"id": action_id, "severity": severity, "title": title, "reason": reason, "command": command}


def _required_actions(adapter: PlatformAdapter, controller_ip: str | None, streaming: bool,
                      components: dict) -> list[dict]:
    actions: list[dict] = []
    missing = _guard(adapter.missing_packages, [])
    if missing:
        actions.append(_action(
            "install-packages", "required", "Install required packages",
            "These packages are missing: " + ", ".join(missing) + ".",
            adapter.install_command(missing),
        ))

    if unit_active_system("ufw.service"):
        if controller_ip is not None:
            actions.append(_action(
                "firewall-lan", "recommended", "Allow Lan Mouse from the controller",
                f"ufw is active and UDP port {KVM_PORT} is not opened for the controller.",
                f"sudo ufw allow proto udp from {controller_ip} to any port {KVM_PORT}",
            ))
            if streaming:
                # Sunshine's web UI (47990) is deliberately left closed.
                actions.append(_action(
                    "firewall-streaming-lan", "recommended", "Allow streaming from the controller",
                    "ufw is active and would block Moonlight from the local network.",
                    f"sudo ufw allow proto tcp from {controller_ip} to any port 47984,47989,48010"
                    f" && sudo ufw allow proto udp from {controller_ip} to any port 47998:48000,48010",
                ))
        sys_net = Path(os.environ.get("DIARCHY_SYS_NET") or DEFAULT_SYS_NET)
        if _guard(lambda: (sys_net / "tailscale0").exists(), False):
            actions.append(_action(
                "firewall-tailscale", "recommended", "Allow Lan Mouse over Tailscale",
                f"ufw is active and the tailscale0 interface exists; UDP port {KVM_PORT} is not opened on it."
                + FIREWALL_TAILSCALE_SCOPE_HINT,
                FIREWALL_TAILSCALE_COMMAND,
            ))

    if streaming or components["sunshine"]["installed"]:
        uinput = Path(os.environ.get("DIARCHY_UINPUT_PATH") or DEFAULT_UINPUT)
        if not _guard(lambda: os.access(uinput, os.W_OK), False):
            actions.append(_action(
                "uinput-access", "required", "Grant access to /dev/uinput",
                "Streaming input needs a writable uinput device, which the current user cannot open.",
                UINPUT_COMMAND,
            ))
        if power.has_lid() and not power.lid_policy_ok():
            actions.append(_action(
                "lid-policy", "recommended", "Keep the host awake when the lid closes",
                "logind suspends the host on lid close, which stops streaming.",
                LID_COMMAND,
            ))
        actions += _gpu_actions()
        if streaming and (roaming := wifi.pin_action()) is not None:
            actions.append(roaming)
    return actions


def _gpu_actions() -> list[dict]:
    sysfs = power.sysfs_root()
    actions: list[dict] = []
    found = _guard(lambda: gpu.resolve_capture_gpu(None, sysfs), None)
    if found is None or found.encoder is None:
        actions.append(_action(
            "gpu-unsupported", "recommended", "No supported hardware encoder",
            gpu.unsupported_reason(found) or "", "",
        ))
    nvidia = _guard(lambda: gpu.nvidia_info(sysfs, power.proc_root()), None)
    if nvidia is not None and nvidia["modeset"] is False:
        actions.append(_action(
            "nvidia-modeset", "recommended", "Enable NVIDIA DRM modeset",
            "The NVIDIA driver is loaded without nvidia_drm modeset=1, so Sunshine cannot import frames.",
            NVIDIA_MODESET_COMMAND,
        ))
    return actions


def _graphical_session_active(adapter: PlatformAdapter, info: PlatformInfo) -> bool:
    if info.supported:
        return _guard(adapter.graphical_session_active, False)
    return unit_active("graphical-session.target")  # the unsupported adapter hard-codes False


def handle(args: list[str]) -> dict:
    controller_ip, streaming = _parse_args(args)
    adapter = select_adapter()
    info = adapter.detect()
    components = _components()
    return {
        "agent_version": agent_version(),
        "platform": info.to_dict(),
        "session": {"graphical_session_active": _graphical_session_active(adapter, info)},
        "components": components,
        "kvm": _kvm(),
        "required_actions": _required_actions(adapter, controller_ip, streaming, components) if info.supported else [],
    }
