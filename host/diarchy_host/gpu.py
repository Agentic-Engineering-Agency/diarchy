"""Pick the GPU that renders the captured output, and pin Sunshine to it.

Sunshine's capture hands dmabufs to the encoder, which only works when both live on the same GPU: on a hybrid laptop
the NVIDIA encoder cannot import frames rendered by the Intel iGPU (a black stream). So encode on the render GPU.
Only sysfs is read (injectable root), never `lspci`.
"""
from __future__ import annotations

from collections.abc import Sequence
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

HEADER_PREFIX = "# Managed by diarchy"
MANAGED_KEYS = ("adapter_name", "encoder")
_CARD_RE = re.compile(r"card(\d+)")
_KEY_RE = re.compile(r"\s*([A-Za-z0-9_]+)\s*=")
_NVRM_RE = re.compile(r"Kernel Module\s+(\S+)")
ENCODERS = {"nvidia": "nvenc", "i915": "vaapi", "xe": "vaapi", "amdgpu": "vaapi"}
VENDORS = {"0x8086": "intel", "0x10de": "nvidia", "0x1002": "amd"}


def encoder_for(driver: str) -> str | None:
    """The hardware encoder Sunshine can use on `driver`, `None` when there is none (nouveau, radeon, BMC,
    simpledrm, virtual GPUs...)."""
    return ENCODERS.get(driver)


def unsupported_reason(found: "CaptureGpu | None") -> str | None:
    """Why there is no hardware encoder, or `None` when `found` has one."""
    if found is None:
        return "No GPU with a render node was found, so hardware encoding is unavailable."
    if found.encoder is not None:
        return None
    return (f"The {found.driver or 'unknown'} driver has no supported hardware encoder"
            " (supported: nvidia, i915, xe, amdgpu).")


@dataclass(frozen=True)
class CaptureGpu:
    render_node: str  # /dev/dri/renderDNNN
    driver: str  # kernel driver name, e.g. i915, nvidia, amdgpu
    encoder: str | None  # nvenc | vaapi | None when the driver has no supported hardware encoder


class _Card:
    def __init__(self, name: str, device: Path) -> None:
        self.name = name
        self.device = device

    def render_node(self) -> str | None:
        try:
            nodes = sorted(entry.name for entry in (self.device / "drm").iterdir() if entry.name.startswith("renderD"))
        except OSError:
            return None
        return f"/dev/dri/{nodes[0]}" if nodes else None

    def driver(self) -> str:
        try:
            return os.path.basename(os.readlink(self.device / "driver"))
        except OSError:
            return ""

    def boot_vga(self) -> bool:
        try:
            return (self.device / "boot_vga").read_text().strip() == "1"
        except OSError:
            return False

    def gpu(self) -> CaptureGpu | None:
        node = self.render_node()
        if node is None:
            return None
        driver = self.driver()
        return CaptureGpu(node, driver, encoder_for(driver))


def _cards(drm: Path) -> list[_Card]:
    try:
        names = [entry.name for entry in drm.iterdir() if _CARD_RE.fullmatch(entry.name)]
    except OSError:
        return []
    names.sort(key=lambda name: int(_CARD_RE.fullmatch(name).group(1)))
    return [_Card(name, drm / name / "device") for name in names]


def resolve_capture_gpu(output: str | None, sysfs: Path) -> CaptureGpu | None:
    """The GPU behind the `output` connector (e.g. `DP-1`); for a headless or unknown output, the boot_vga GPU,
    else the first one with a render node. `None` when no GPU has a render node."""
    drm = sysfs / "class/drm"
    cards = _cards(drm)
    if output:
        for card in cards:
            if (drm / f"{card.name}-{output}").exists():
                found = card.gpu()
                if found is not None:
                    return found
    for card in cards:
        if card.boot_vga():
            found = card.gpu()
            if found is not None:
                return found
    for card in cards:
        found = card.gpu()
        if found is not None:
            return found
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def snapshot(sysfs: Path) -> list[dict]:
    """One row per DRM card, in the shape of `tests/fixtures/sysfs/*.json` `cards`. Values are read as they are
    (`vendor`/`device` keep the `0x` prefix); a missing value is `None`, a missing `boot_vga` is `False`."""
    drm = sysfs / "class/drm"
    try:
        entries = sorted(entry.name for entry in drm.iterdir())
    except OSError:
        return []
    rows = []
    for card in _cards(drm):
        node = card.render_node()
        rows.append({
            "card": card.name,
            "pci": os.path.basename(os.path.realpath(card.device)),
            "vendor": _read(card.device / "vendor"),
            "device": _read(card.device / "device"),
            "driver": card.driver(),
            "render": node.rsplit("/", 1)[1] if node else None,
            "boot_vga": card.boot_vga(),
            "connectors": [name[len(card.name) + 1:] for name in entries if name.startswith(card.name + "-")],
        })
    return rows


def gpus(sysfs: Path) -> list[dict]:
    """The `gpus` rows of `diarchy-host report`."""
    rows = []
    for row in snapshot(sysfs):
        vendor, device = row["vendor"], row["device"]
        pci_id = f"{vendor[2:]}:{device[2:]}" if vendor and device else None
        rows.append({
            "card": row["card"], "vendor": VENDORS.get(vendor, "other"), "pci_id": pci_id,
            "driver": row["driver"] or None, "render_node": row["render"], "boot_vga": row["boot_vga"],
            "connectors": row["connectors"],
        })
    return rows


MODPROBE_DIRS = (Path("/etc/modprobe.d"), Path("/usr/lib/modprobe.d"))
_CMDLINE_MODESET_RE = re.compile(r"(?:^|\s)nvidia[_-]drm\.modeset=(\S+)")
_MODPROBE_MODESET_RE = re.compile(r"^\s*options\s+nvidia[_-]drm\b.*?\bmodeset=(\S+)", re.MULTILINE)


def _modeset_fallback(proc_root: Path, modprobe_dirs: Sequence[Path]) -> bool | None:
    """`nvidia_drm modeset` from the kernel command line, then the modprobe.d files, when the sysfs value is
    unreadable (it is root-only on some hosts). `None` when no source says."""
    match = _CMDLINE_MODESET_RE.search(_read(proc_root / "cmdline") or "")
    if match:
        return match.group(1) in ("1", "Y", "y")
    for directory in modprobe_dirs:
        try:
            files = sorted(directory.glob("*.conf"))
        except OSError:
            continue
        value = None
        for path in files:
            for match in _MODPROBE_MODESET_RE.finditer(_read(path) or ""):
                value = match.group(1)
        if value is not None:
            return value in ("1", "Y", "y")
    return None


def nvidia_info(sysfs: Path, proc_root: Path, modprobe_dirs: Sequence[Path] = MODPROBE_DIRS) -> dict | None:
    """`{"driver_version", "modeset"}` for the NVIDIA proprietary driver, `None` when it is not loaded.
    `modeset` is `None` (unknown) when neither sysfs, /proc/cmdline nor modprobe.d tell."""
    module = sysfs / "module/nvidia"
    proc_version = proc_root / "driver/nvidia/version"
    if not (module.exists() or proc_version.exists() or any(row["driver"] == "nvidia" for row in snapshot(sysfs))):
        return None
    version = _read(module / "version")
    if not version:
        match = _NVRM_RE.search(_read(proc_version) or "")
        version = match.group(1) if match else None
    modeset = _read(sysfs / "module/nvidia_drm/parameters/modeset")
    if modeset is None:
        return {"driver_version": version, "modeset": _modeset_fallback(proc_root, modprobe_dirs)}
    return {"driver_version": version, "modeset": modeset == "Y"}


def render_conf(text: str, found: CaptureGpu | None) -> str:
    """`text` with the managed `adapter_name`/`encoder` lines set for `found` (removed when `None`). Existing lines
    are replaced where they are, duplicates dropped, missing ones appended; everything else is kept as is."""
    wanted = {} if found is None else {"adapter_name": found.render_node}
    if found is not None and found.encoder is not None:
        wanted["encoder"] = found.encoder
    lines = text.splitlines()
    out: list[str] = []
    placed: set[str] = set()
    for line in lines:
        match = _KEY_RE.match(line)
        key = match.group(1) if match else None
        if key not in MANAGED_KEYS:
            out.append(line)
        elif key in wanted and key not in placed:
            out.append(f"{key} = {wanted[key]}")
            placed.add(key)
    for key in MANAGED_KEYS:
        if key in wanted and key not in placed:
            out.append(f"{key} = {wanted[key]}")
    return "\n".join(out) + "\n"


def apply_to_conf(conf: Path, found: CaptureGpu | None) -> bool:
    """Rewrite `conf` atomically (mode 600). Only a managed file is touched: a missing file, or one without the
    `# Managed by diarchy` header, is left alone. Returns whether the file changed; raises `OSError`."""
    try:
        text = conf.read_text()
    except FileNotFoundError:
        return False
    if not text.startswith(HEADER_PREFIX):
        return False
    rendered = render_conf(text, found)
    if rendered == text:
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=f".{conf.name}.", dir=conf.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, conf)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True
