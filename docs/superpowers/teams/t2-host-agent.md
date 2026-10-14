# Team T2 — host-agent

Team id: `t2`. Branch: `feat/diarchy-app--host-agent`.

## Mission

Build the Host agent `diarchy-host` (spec §6) with the platform adapter seam (§6.4), the host installer
orchestrator `install-host.sh` (§7), and make host KVM installation use the agent. Spec sections: §2, §6 (all), §7,
§10, §11, §12.

## Ownership (edit only these)

- `host/diarchy_host/` (new package) EXCEPT `host/diarchy_host/stream.py` (team t4)
- `host/diarchy-host` (new launcher), `host/install-host.sh` (new), `host/VERSION` (new, content `1.0.0`)
- `host/install-lan-mouse.sh`, `host/diarchy-kvm-host-doctor`, `host/systemd/diarchy-kvm.service`
- Tests: `tests/test_lan_mouse_host.py`, new `tests/test_diarchy_host_*.py`, new `tests/test_install_host.py`
- `docs/superpowers/plans/2026-10-11-diarchy-app-t2-host-agent.md`,
  `docs/superpowers/reports/2026-10-11-t2-host-agent.md`

NOT yours: `host/install.sh`, `host/sunshine*`, `host/diarchy-*`, `host/systemd/diarchy-*`,
`host/diarchy_host/stream.py` (team t4); `host/install-clipboard-file-transfer.sh` and file-portal/clipboard host
scripts (ask Main if a change is needed); anything in `client/` or `app/`.

## Interfaces you must provide to t4 (stage 1)

- `host/diarchy_host/common.py`:
  - `class AgentError(Exception)` with `__init__(self, code: str, message: str)` and attributes `code`, `message`.
  - `def run(argv: list[str], timeout: float = 20.0, input: str | None = None) -> subprocess.CompletedProcess[str]`
    (text mode, captured output, never raises on non-zero exit; raises `AgentError("internal", ...)` on timeout or
    missing executable).
  - `def systemctl_user(*args: str) -> subprocess.CompletedProcess[str]` (honors env `SYSTEMCTL_BIN`, default
    `systemctl`).
  - `def unit_active(unit: str) -> bool`.
  - Constants `UNIT_KVM = "diarchy-kvm.service"`, `UNIT_STREAM_DISPLAY = "diarchy-display.service"`,
    `UNIT_STREAM_SUNSHINE = "diarchy-sunshine.service"`.
- `host/diarchy_host/cli.py`: `main(argv: list[str]) -> int` builds the §6.1 envelope. For `stream` it calls
  `diarchy_host.stream.handle(args: list[str]) -> dict` (payload without envelope keys; raises `AgentError`).
  Until t4 merges, `stream` returns `AgentError("not_installed", "streaming support is not installed")` when the
  module is absent (guarded import).
- `diarchy_host.platform` adapters per §6.4. All external binaries are resolved through env overrides
  (`HYPRCTL_BIN`, `OPENSSL_BIN`, `SYSTEMCTL_BIN`, `SS_BIN`, ...) so tests use fakes; `~` resolves from `HOME`;
  `/etc/os-release` path overridable via `DIARCHY_OS_RELEASE`.

After stage 1 commits, message `[t2→Main] READY AGENT-SKELETON <hash>`.

## Stages

1. Package skeleton + `common.py` + `cli.py` envelope + `version` + tests (envelope shapes, exit codes 0/1/64,
   stdout is exactly one JSON line even on internal exceptions). READY message.
2. Platform adapters + `probe` (every field and every required-action rule in §6.3, including `uinput-access` and
   `lid-policy` rules and the package-name mapping; `--controller-ip` validation).
3. `kvm configure | authorize | start | stop | status`, including mutual exclusion with stream units, managed
   config writer (0600, atomic, unmanaged backup, preserve-on-configure), fingerprint validation.
4. `install-host.sh` gating per §7 and `host/install-lan-mouse.sh` delegating config writing to the agent; update
   `diarchy-kvm-host-doctor` and `tests/test_lan_mouse_host.py` accordingly.
5. Verification + report.

## Acceptance

- `python3 -m unittest` over all owned tests passes; full suite run once at the end.
- `host/diarchy-host probe` against fakes for: supported Omarchy, non-Arch, no Hyprland instance, missing packages,
  UFW active with and without controller IP, Tailscale interface present.
- No `sudo` string is ever executed (only appears inside `command` strings of required actions).
