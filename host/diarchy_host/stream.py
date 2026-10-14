"""Streaming commands for the Diarchy agent."""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

from . import common, gpu, outputs
from .common import AgentError

PIN_RE = re.compile(r"[0-9]{4}")
NAME_RE = re.compile(r"[A-Za-z0-9 ._-]{1,40}")
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _parse_pair(args: list[str]) -> tuple[str, str]:
    if len(args) != 5 or args[0] != "pair":
        raise AgentError("usage", "usage: stream pair --pin PIN --name NAME")
    seen: dict[str, str] = {}
    i = 1
    while i < len(args):
        flag = args[i]
        if flag not in ("--pin", "--name") or flag in seen or i + 1 >= len(args):
            raise AgentError("usage", "usage: stream pair --pin PIN --name NAME")
        value = args[i + 1]
        seen[flag] = value
        i += 2
    if set(seen) != {"--pin", "--name"}:
        raise AgentError("usage", "usage: stream pair --pin PIN --name NAME")
    pin, name = seen["--pin"], seen["--name"]
    if PIN_RE.fullmatch(pin) is None or NAME_RE.fullmatch(name) is None:
        raise AgentError("usage", "invalid PIN or name")
    return pin, name


def _credentials() -> tuple[str, str]:
    path = common.resolve_bin("DIARCHY_SUNSHINE_CREDENTIALS", str(common.home() / ".config/sunshine/.diarchy-ui"))
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise AgentError("not_installed", "Sunshine credentials are not installed") from error
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key in ("username", "password"):
            values[key] = value
    if not values.get("username") or "password" not in values or not values["password"]:
        raise AgentError("not_installed", "Sunshine credentials are not installed")
    return values["username"], values["password"]


def _post_pin(url: str, user: str, password: str, pin: str, name: str, timeout: float) -> tuple[int, object]:
    auth = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    request = urllib.request.Request(
        url.rstrip("/") + "/api/pin",
        data=json.dumps({"pin": pin, "name": name}, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        method="POST",
    )
    context = None
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname in LOOPBACK:
        context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            try:
                payload = json.loads(response.read().decode())
            except (UnicodeDecodeError, ValueError) as error:
                raise AgentError("pair_failed", "Sunshine returned an invalid pairing response") from error
            if not isinstance(payload, dict):
                raise AgentError("pair_failed", "Sunshine returned an invalid pairing response")
            return response.status, payload
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            error.close()
            raise AgentError("pair_failed", "Sunshine rejected credentials") from error
        try:
            payload = json.loads(error.read().decode())
        except (ValueError, OSError):
            payload = None
        finally:
            error.close()
        return error.code, payload
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AgentError("sunshine_unreachable", "Sunshine is unreachable") from error


def pair(args: list[str]) -> dict:
    pin, name = _parse_pair(args)
    user, password = _credentials()
    url = os.environ.get("DIARCHY_SUNSHINE_URL", "https://localhost:47990")
    try:
        timeout = float(os.environ.get("DIARCHY_STREAM_PAIR_TIMEOUT", "20"))
        interval = float(os.environ.get("DIARCHY_STREAM_PAIR_INTERVAL", "1"))
    except ValueError as error:
        raise AgentError("usage", "invalid pair timing configuration") from error
    deadline = time.monotonic() + max(0.0, timeout)
    last_error = "pair_failed"
    while True:
        try:
            status, payload = _post_pin(url, user, password, pin, name, max(0.01, min(5.0, deadline - time.monotonic())))
            if status == 200 and isinstance(payload, dict):
                state = payload.get("status")
                if state is True or state == "true":
                    return {"paired": True}
                if state is False or state == "false":
                    last_error = "pair_failed"
                else:
                    raise AgentError("pair_failed", "Sunshine returned an invalid pairing status")
            else:
                raise AgentError("pair_failed", "Sunshine pairing failed")
        except AgentError as error:
            if error.code not in ("sunshine_unreachable",):
                raise
            last_error = error.code
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentError(last_error, "Sunshine pairing timed out")
        time.sleep(min(max(0.0, interval), remaining))



DEFAULT_PROBE_ADDR = "127.0.0.1:47984"
DEFAULT_READY_TIMEOUT = "8"


def _readiness_config() -> tuple[str, int, float]:
    address = os.environ.get("DIARCHY_SUNSHINE_PROBE_ADDR") or DEFAULT_PROBE_ADDR
    host, _, port = address.rpartition(":")
    try:
        timeout = float(os.environ.get("DIARCHY_SUNSHINE_READY_TIMEOUT") or DEFAULT_READY_TIMEOUT)
        if not host:
            raise ValueError(address)
        return host, int(port), max(0.0, timeout)
    except ValueError as error:
        raise AgentError("usage", "invalid Sunshine readiness configuration") from error


def _wait_listening(host: str, port: int, timeout: float) -> None:
    """`active` only means Sunshine's process forked; wait until it accepts TCP so the client's route probe succeeds."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        try:
            socket.create_connection((host, port), timeout=max(0.05, min(1.0, remaining))).close()
            return
        except OSError:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentError("service_failed", "Sunshine did not start listening")
        time.sleep(min(0.2, remaining))


def _service_failed(action: str, unit: str, result) -> AgentError:
    detail = (result.stderr or result.stdout or "").strip()
    return AgentError("service_failed", f"systemctl --user {action} {unit} failed" + (f": {detail}" if detail else ""))


def _stop_units(*units: str) -> None:
    """Stop every unit, then fail unless all of them are really down."""
    failure: AgentError | None = None
    for unit in units:
        result = common.systemctl_user("stop", unit)
        if result.returncode != 0 and failure is None:
            failure = _service_failed("stop", unit, result)
    if failure is not None:
        raise failure
    for unit in units:
        if common.unit_running(unit):
            raise AgentError("service_failed", f"{unit} is still active after stop")


def _selected_output() -> str | None:
    """The output the display service captures (`diarchy-display reconcile` prints it), or `None`."""
    display = common.resolve_bin("DIARCHY_DISPLAY_BIN", str(common.home() / ".local/bin/diarchy-display"))
    try:
        result = common.run([display, "reconcile"])
    except AgentError:
        return None
    lines = result.stdout.split()
    return lines[-1] if result.returncode == 0 and lines else None


def _pin_capture_gpu() -> gpu.CaptureGpu | None:
    """Sunshine reads its conf at start: encode on the GPU that renders the selected output (see `gpu`)."""
    conf = common.home() / ".config/sunshine/sunshine.conf"
    sysfs = Path(os.environ.get("DIARCHY_SYSFS_ROOT") or "/sys")
    found = gpu.resolve_capture_gpu(_selected_output(), sysfs)
    try:
        gpu.apply_to_conf(conf, found)
    except OSError as error:
        raise AgentError("config_write_failed", f"cannot write {conf}: {error.strerror or error}") from error
    return found


USAGE = "usage: stream {start|configure} [--output auto|NAME] | stop | status | pair"


def _parse_output(args: list[str]) -> str:
    """`[]` or `[--output VALUE]` after `start`/`configure`; the value is validated by `outputs.resolve_output`."""
    if not args:
        return "auto"
    if len(args) == 2 and args[0] == "--output":
        return args[1]
    raise AgentError("usage", USAGE)


def _display_env_path() -> Path:
    override = os.environ.get("DIARCHY_DISPLAY_ENV")
    return Path(override) if override else common.config_home() / "diarchy/display.env"


def _save_physical_output(requested: str) -> bool:
    """Validate `requested` and save it as it is (`auto` or a name) where every `diarchy-display` caller (the display
    service, Sunshine's ExecStartPre, the installer) reads it. `auto` is resolved again on every pass.
    Returns whether the saved value changed."""
    value = requested if requested == "auto" else outputs.resolve_output(requested, None)  # a name is validated
    path = _display_env_path()
    text = f"PHYSICAL_OUTPUT={value}\n"
    try:
        previous = path.read_text(encoding="utf-8")
    except OSError:
        previous = None
    if previous == text:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        raise AgentError("config_write_failed", f"cannot write {path}: {error.strerror or error}") from error
    return True


def _configure(requested: str) -> dict:
    """The pin alone, without starting anything: the installer runs it so the encoder it validates is the one
    `start` will use."""
    _save_physical_output(requested)
    found = _pin_capture_gpu()
    return {"adapter_name": found.render_node if found else None, "encoder": found.encoder if found else None}


def _start(requested: str) -> dict:
    sunshine = common.home() / ".local/bin/sunshine"
    if not sunshine.is_file() or not os.access(sunshine, os.X_OK):
        raise AgentError("not_installed", "Sunshine is not installed")
    host, port, timeout = _readiness_config()
    common.require_hyprland_session()
    outputs.resolve_output(requested, None)  # a bad name is a usage error before any unit is touched
    kvm_was_running = common.unit_running(common.UNIT_KVM)
    if kvm_was_running:
        _stop_units(common.UNIT_KVM)
    try:
        changed = _save_physical_output(requested)
        _pin_capture_gpu()
        if changed and common.unit_running(common.UNIT_STREAM_DISPLAY):  # `start` would not re-read the saved output
            _start_units("restart", common.UNIT_STREAM_DISPLAY, common.UNIT_STREAM_SUNSHINE)
        _start_units("start", common.UNIT_STREAM_DISPLAY, common.UNIT_STREAM_SUNSHINE)
        if not common.unit_active(common.UNIT_STREAM_DISPLAY) or not common.unit_active(common.UNIT_STREAM_SUNSHINE):
            raise AgentError("service_failed", "streaming service failed to start")
    except AgentError:
        _roll_back_start(kvm_was_running)
        raise
    _wait_listening(host, port, timeout)
    return {"service_active": True, "encoder": _encoder()}


def _start_units(action: str, *units: str) -> None:
    result = common.systemctl_user(action, *units)
    if result.returncode != 0:
        raise _service_failed(action, " ".join(units), result)


def _roll_back_start(kvm_was_running: bool) -> None:
    """Best effort after a failed start: stop half-started stream units, give the workspaces back to a physical
    monitor, and start KVM again if it ran before. The original error stays the reported one."""
    for unit in (common.UNIT_STREAM_SUNSHINE, common.UNIT_STREAM_DISPLAY):
        common.systemctl_user("stop", unit)
    try:
        outputs.restore_display()
    except AgentError:
        pass
    if kvm_was_running:
        common.systemctl_user("start", common.UNIT_KVM)


def _stop() -> dict:
    _stop_units(common.UNIT_STREAM_SUNSHINE, common.UNIT_STREAM_DISPLAY)
    if common.hyprland_session_present():
        outputs.restore_display()
    return {"service_active": False, "encoder": None}

def _encoder() -> str:
    try:
        result = common.run([common.resolve_bin("DIARCHY_SUNSHINE_CHECK_BIN", str(common.home() / ".local/bin/diarchy-check-sunshine-service")), "--encoder"])
    except AgentError:
        return "unknown"
    value = result.stdout.strip().lower()
    return value if result.returncode == 0 and value in {"nvenc", "vaapi"} else "unknown"
def _status() -> dict:
    active = common.unit_active(common.UNIT_STREAM_DISPLAY) and common.unit_active(common.UNIT_STREAM_SUNSHINE)
    return {"service_active": active, "encoder": _encoder() if active else None}


def handle(args: list[str]) -> dict:
    if not args:
        raise AgentError("usage", USAGE)
    if args[0] == "start":
        return _start(_parse_output(args[1:]))
    if args[0] == "stop" and len(args) == 1:
        return _stop()
    if args[0] == "status" and len(args) == 1:
        return _status()
    if args[0] == "configure":
        return _configure(_parse_output(args[1:]))
    if args[0] == "pair":
        return pair(args)
    raise AgentError("usage", USAGE)
