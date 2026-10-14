from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .common import AgentError, config_home

FP_RE = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
MANAGED_HEADER = "# Managed by diarchy"
SECTION = "[authorized_fingerprints]"
_BODY = (
    'release_bind = ["KeyA", "KeyS", "KeyD", "KeyF"]\n'
    'capture_backend = "layer-shell"\n'
    "port = 4242\n"
)
_ENTRY_RE = re.compile(r'^\s*"([0-9A-Fa-f:]+)"\s*=\s*"[^"]*"\s*(?:#.*)?$')


def normalize_fingerprint(value: str) -> str:
    candidate = value.upper() if isinstance(value, str) else ""
    if not FP_RE.fullmatch(candidate):
        raise AgentError("invalid_fingerprint", "fingerprint must be 32 colon-separated hex bytes")
    return candidate


def config_dir() -> Path:
    return config_home() / "lan-mouse"


def config_path() -> Path:
    explicit = os.environ.get("LAN_MOUSE_CONFIG")
    return Path(explicit) if explicit else config_dir() / "config.toml"


def _as_list(fingerprints: str | Sequence[str] | None) -> list[str]:
    if fingerprints is None:
        return []
    items = [fingerprints] if isinstance(fingerprints, str) else list(fingerprints)
    unique: list[str] = []
    for item in items:
        normalized = normalize_fingerprint(item)
        if normalized not in unique:
            unique.append(normalized)
    return unique


def render(fingerprints: str | Sequence[str] | None) -> str:
    text = f"{MANAGED_HEADER}\n{_BODY}\n{SECTION}\n"
    for fingerprint in _as_list(fingerprints):
        text += f'"{fingerprint.lower()}" = "mac"\n'
    return text


def _is_managed(data: bytes) -> bool:
    return data.split(b"\n", 1)[0].rstrip(b"\r") == MANAGED_HEADER.encode()


def _authorized(text: str) -> list[str]:
    found: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == SECTION
            continue
        if not in_section:
            continue
        match = _ENTRY_RE.match(line)
        if match and FP_RE.fullmatch(match.group(1).upper()):
            found.append(match.group(1).upper())
    return found


def read_authorized(path: Path | None = None) -> tuple[bool, list[str]]:
    try:
        data = (path or config_path()).read_bytes()
        text = data.decode("utf-8")
    except (OSError, ValueError):
        return False, []
    return _is_managed(data), _authorized(text)


def preserved_fingerprints() -> list[str]:
    managed, fingerprints = read_authorized()
    return fingerprints if managed else []


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # the replace already succeeded; directory durability is best-effort
    finally:
        os.close(fd)


def _backup(path: Path, original: bytes) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.backup.{stamp}")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        backup.unlink(missing_ok=True)
        raise


def _replace_atomically(path: Path, text: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.chmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def write(fingerprints: str | Sequence[str] | None) -> None:
    text = render(fingerprints)  # validates before any filesystem side effect
    path = config_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)  # mkdir applies the mode only on creation; the dir holds the host private key
        try:
            original = path.read_bytes()
        except FileNotFoundError:
            original = None
        if original is not None and not _is_managed(original):
            _backup(path, original)
        _replace_atomically(path, text)
    except OSError as error:
        raise AgentError("config_write_failed", f"cannot write {path}: {error.strerror or error}") from error
