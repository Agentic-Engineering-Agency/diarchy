"""Detect a host that keeps hopping between access points of the same Wi-Fi network.

Every reassociation silences the radio for a few hundred milliseconds. For a stream that means a burst of lost
frames and a wait for the next IDR frame; on a host that sits still but sees two access points at similar strength
the supplicant can ping-pong between them every ~35 s. Only NetworkManager + wpa_supplicant hosts are understood:
the supplicant's journal is the only roaming history that exists after the fact, and `nmcli` the only way to name
the connection to pin.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from .common import AgentError, resolve_bin, run

JOURNAL_WINDOW = "-24h"
# A stationary host that changes access point this often over the window is ping-ponging, not being carried around.
MIN_ACCESS_POINT_CHANGES = 10

_AUTHENTICATE_RE = re.compile(
    r"^(?P<interface>\S+): SME: Trying to authenticate with (?P<bssid>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}) "
    r"\(SSID='(?P<ssid>.*)' freq=\d+"
)
_NMCLI_SCAN_SPLIT_RE = re.compile(r"(?<!\\):")


@dataclass(frozen=True)
class Roaming:
    interface: str
    ssid: str
    changes: int  # authentications against a different access point than the previous one


def _roaming_from_journal(text: str) -> Roaming | None:
    changes = 0
    previous: str | None = None
    last = None
    for line in text.splitlines():
        match = _AUTHENTICATE_RE.match(line)
        if match is None:
            continue
        bssid = match["bssid"].lower()
        if previous is not None and bssid != previous:
            changes += 1
        previous = bssid
        last = match
    if last is None or changes < MIN_ACCESS_POINT_CHANGES:
        return None
    return Roaming(last["interface"], last["ssid"], changes)


def _read_journal() -> str | None:
    try:
        result = run([resolve_bin("JOURNALCTL_BIN", "journalctl"), "-u", "wpa_supplicant",
                      "--since", JOURNAL_WINDOW, "--no-pager", "-o", "cat"])
    except AgentError:
        return None
    return result.stdout if result.returncode == 0 else None


def _nmcli(*args: str) -> str | None:
    try:
        result = run([resolve_bin("NMCLI_BIN", "nmcli"), *args])
    except AgentError:
        return None
    return result.stdout if result.returncode == 0 else None


def _connection_name(interface: str) -> str | None:
    output = _nmcli("-g", "GENERAL.CONNECTION", "device", "show", interface)
    name = output.strip() if output else ""
    return name or None


def _associated_bssid(interface: str, ssid: str) -> str | None:
    """The BSSID the host is connected to right now. The strongest same-SSID access point could be a rogue one."""
    output = _nmcli("-t", "-f", "ACTIVE,BSSID,SSID,SIGNAL", "device", "wifi", "list", "ifname", interface)
    for line in (output or "").splitlines():
        fields = _NMCLI_SCAN_SPLIT_RE.split(line)
        if len(fields) != 4 or fields[0] != "yes" or fields[2].replace("\\:", ":") != ssid:
            continue
        return fields[1].replace("\\:", ":").upper()
    return None


def pin_action() -> dict | None:
    """The `wifi-roaming` required action, or `None` when the host is not ping-ponging or cannot be told how to stop."""
    journal = _read_journal()
    roaming = _roaming_from_journal(journal) if journal else None
    if roaming is None:
        return None
    connection = _connection_name(roaming.interface)
    bssid = _associated_bssid(roaming.interface, roaming.ssid)
    if connection is None or bssid is None:
        return None
    name = shlex.quote(connection)
    return {
        "id": "wifi-roaming",
        "severity": "recommended",
        "title": "Stop the host from hopping between Wi-Fi access points",
        "reason": (
            f"The host changed access point {roaming.changes} times in the last day; every change cuts the stream "
            "for a moment. Prefer a wired connection; otherwise pin the connection to the access point it is connected to now "
            f"(undo with: nmcli connection modify {name} 802-11-wireless.bssid \"\")."
        ),
        "command": f"nmcli connection modify {name} 802-11-wireless.bssid {bssid} && nmcli connection up {name}",
    }
