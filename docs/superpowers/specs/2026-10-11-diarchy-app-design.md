# Diarchy App — Design Specification

**Date:** 2026-10-11
**Status:** Approved for implementation (user instruction 2026-10-11: proceed to plan and execute)
**Supersedes:** the AppleScript launchers `Diarchy.app` and `Diarchy KVM.app`. Builds on
`2026-07-22-diarchy-design.md`, `2026-07-22-lan-mouse-local-kvm-design.md`,
`2026-07-29-clipboard-file-transfer-design.md`, `2026-07-29-edge-file-portal-design.md`.

Glossary: `CONTEXT.md`. Decisions: `docs/adr/0001-*.md`, `docs/adr/0002-*.md`.

## 1. Goal

Turn Diarchy from a set of personal scripts into a product: **one native macOS menu-bar app** that lets
**any person with a Mac and a second computer running Omarchy** pair the two machines, choose on which side of
the Mac the other screen sits, and use the Mac's keyboard and mouse (with clipboard and file transfer) on the
other machine — or stream the other machine's desktop to the Mac — configured entirely through a GUI.

A later project extends host support to "almost any Linux". This project must leave a **platform adapter seam**
on the host so that extension adds an adapter instead of rewriting the agent.

Success means: a new user with a fresh Mac and a fresh Omarchy install, SSH enabled on Omarchy, can go from
installing the app to crossing the mouse onto Omarchy without opening a terminal except to run commands the app
shows them verbatim (sudo-level host changes and the one-time `ssh-copy-id`).

## 2. Users, platforms, and limits

| Role | Machine | Supported in this project |
|---|---|---|
| Controller | Mac | Apple Silicon (arm64), macOS 14 or later |
| Host | Linux PC | x86_64, Arch-family (`/etc/os-release` `ID=arch` or `ID_LIKE` contains `arch`), live Hyprland session, systemd user manager. Omarchy is detected and reported as the flavor. |

- Control direction: **Controller → Host only.** The host never captures its own input to drive the Mac.
- KVM engine: Lan Mouse **0.11.0** (pinned, existing installers). Host capture backend stays `layer-shell`;
  the Hyprland InputCapture portal is forbidden (crash history, see KVM design §Failure behavior).
- Screen placement: one of `left`, `right`, `top`, `bottom` relative to the Mac. No per-monitor choice, no offset.
- Streaming engine: Sunshine on the host (pinned AppImage, existing installer) + Moonlight Qt 6.1.0 on the Mac
  (pinned, existing installer). Video is never decoded or rendered by our app.
- KVM mode and Streaming mode are **mutually exclusive** (both inject input on the host).
- The app and every script **never run sudo** and never ask for a sudo password (ADR 0002). Privileged host
  changes are surfaced as *required actions* with an exact command for the user to run.

## 3. Architecture

```
Mac (Controller)                                         Host (Omarchy)
┌──────────────────────────────┐                         ┌───────────────────────────────┐
│ Diarchy.app (SwiftUI)│── ssh via diarchy-ssh ─▶│ diarchy-host (Python agent)    │
│   DiarchyCore (logic)        │                         │   platform adapter (Hyprland)  │
│   Diarchy (UI)         │                         │   kvm / stream / probe         │
│   Resources/payload/{client, │── tar over ssh ────────▶│ ~/.local/share/diarchy/ │
│      host, VERSION}          │                         │   payload/host/install-host.sh │
└──────────┬───────────────────┘                         └──────────────┬────────────────┘
           │ runs                                                      │ systemctl --user
           ▼                                                           ▼
 ~/.local/bin scripts (bash): diarchy-config.sh, diarchy-ssh,   diarchy-kvm.service (lan-mouse daemon)
 diarchy-kvm, diarchy-kvm-configure, diarchy-mac-fingerprint,   diarchy-file-portal.service
 diarchy-stream, doctors, clipboard/file portal                 diarchy-display.service
           │                                                    diarchy-sunshine.service
           ▼
 ~/.config/diarchy/diarchy.conf  (single source of truth, KEY=VALUE, 0600)
```

The app **orchestrates the existing, hardened scripts** instead of re-implementing them (ADR 0001). The app owns
the configuration file and the user interface; scripts own process lifecycle, routing, and installation.

## 4. Configuration contract — `diarchy.conf`

Path: `${DIARCHY_CONFIG:-$HOME/.config/diarchy/diarchy.conf}`. Directory mode 0700, file mode 0600.
Written only by the app (atomic write: temp file in same dir, fsync, rename). Read by the bash loader and the app.

### 4.1 Format

- UTF-8, LF line endings. First line exactly `# Managed by Diarchy`.
- Every other non-empty line that does not start with `#` is `KEY=VALUE`: no spaces around `=`, no quotes,
  no inline comments, VALUE may be empty. Keys not in the table below are an error. A duplicated key is an error.
- Readers never `source`/`eval` the file.

### 4.2 Keys

| Key | Required | Default | Validation (full-match regex or rule) |
|---|---|---|---|
| `DIARCHY_SSH_USER` | yes | — | `^[a-z_][a-z0-9_-]{0,31}$` |
| `DIARCHY_LAN_HOST` | one of LAN/Tailscale | empty | empty or HOST |
| `DIARCHY_TAILSCALE_HOST` | one of LAN/Tailscale | empty | empty or HOST |
| `DIARCHY_SSH_PORT` | no | `22` | integer 1–65535 |
| `DIARCHY_SSH_IDENTITY_FILE` | no | empty | empty, or absolute path `^/[^\n"]*$` (no newline, no double quote) without `..` path segments |
| `DIARCHY_HOST_KEY_ALIAS` | no | Tailscale host if set, else LAN host | empty or HOST |
| `DIARCHY_KVM_SIDE` | no | `left` | `left`, `right`, `top`, `bottom` |
| `DIARCHY_HOST_FINGERPRINT` | no | empty (unpaired) | empty or FP |
| `DIARCHY_STREAM_RESOLUTION` | no | `1920x1080` | `^[0-9]{3,4}x[0-9]{3,4}$` |
| `DIARCHY_STREAM_FPS` | no | `60` | integer 30–240 |
| `DIARCHY_STREAM_OVERLAY` | no | `0` | `0` or `1` |

- HOST = `^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$` (IPv4 literal or DNS/MagicDNS name).
- FP = `^([0-9A-F]{2}:){31}[0-9A-F]{2}$` (uppercase SHA-256, Lan Mouse/OpenSSL form). Writers uppercase input.
- At least one of `DIARCHY_LAN_HOST` / `DIARCHY_TAILSCALE_HOST` must be non-empty.
- Fingerprint case (ruling 2026-10-11): `diarchy.conf`, agent JSON, and all comparisons use uppercase FP; every Lan
  Mouse `config.toml` (Mac and Host) stores fingerprints **lowercase** (the physically verified Lan Mouse form).
  Comparisons are case-insensitive when reading a `config.toml`.

### 4.3 Bash loader — `client/diarchy-config.sh`

Sourced library. Function `diarchy_config_load` (no arguments):

1. Path = `${DIARCHY_CONFIG:-$HOME/.config/diarchy/diarchy.conf}`.
2. If the file is missing **and** `DIARCHY_SSH_USER` plus at least one host variable are already set in the
   environment, use the environment only (test and expert override). Otherwise a missing file fails with
   `diarchy-config: Diarchy is not configured (open the app to set it up)`.
3. Parse per §4.1/§4.2. For each key, a non-empty value already present in the environment wins over the file.
4. Apply defaults, validate every effective value, export all keys listed in §4.2.
5. On any error print exactly one line `diarchy-config: <reason>` to stderr and `return 1` (never `exit`).

### 4.4 Swift codec

`DiarchyCore.ConfigCodec` parses and renders the same format with the same validation and error reasons, so a
file written by the app always loads in bash and vice versa. Rendering writes every key in table order, including
defaults explicitly.

### 4.5 Canonical error reasons (ruling 2026-10-11, binding for bash and Swift)

Reference implementation: `client/diarchy-config.sh` at commit `b1cb5a8` (branch `feat/diarchy-app--mac-scripts`).
Both readers emit exactly these reasons (after the `diarchy-config: ` prefix in bash), checking in this order and
stopping at the first failure:

1. `Diarchy is not configured (open the app to set it up)` — file missing and no env fallback.
2. `cannot read configuration file: <path>`
3. `first line must be "# Managed by Diarchy"`
4. Per line, top to bottom: `line N: carriage returns are not allowed`, `line N: expected KEY=VALUE`,
   `line N: unknown key K`, `line N: duplicate key K`, `quoted values are not allowed`.
5. After merging env precedence and defaults, in key order: `DIARCHY_SSH_USER is required`, then
   `<KEY> is invalid` for `DIARCHY_SSH_USER`, `DIARCHY_LAN_HOST`, `DIARCHY_TAILSCALE_HOST`, `DIARCHY_SSH_PORT`,
   `DIARCHY_SSH_IDENTITY_FILE`, `DIARCHY_HOST_KEY_ALIAS`, `DIARCHY_KVM_SIDE`, `DIARCHY_HOST_FINGERPRINT`,
   `DIARCHY_STREAM_RESOLUTION`, `DIARCHY_STREAM_FPS`, `DIARCHY_STREAM_OVERLAY` (range violations of port and FPS use
   the same `is invalid` reason).
6. `at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required`

Integers are decimal digits only and are evaluated in base 10 (a leading zero never means octal).

## 5. Mac command contract (`~/.local/bin`)

All commands are bash, `set -euo pipefail`, call `diarchy_config_load` first (except installers and pure doctors
that do not need hosts), and print `<command>: <reason>` on failure.

| Command | Contract |
|---|---|
| `diarchy-route.sh` (library) | Existing `select_diarchy_route auto|lan|tailscale`. Now loads config via the loader; skips a candidate whose host is empty; SSH options add `-o User=$DIARCHY_SSH_USER`, `-o HostKeyAlias=$DIARCHY_HOST_KEY_ALIAS`, and, when set, `-o IdentityFile=$DIARCHY_SSH_IDENTITY_FILE -o IdentitiesOnly=yes`. Destination token `DIARCHY_SSH_ALIAS` defaults to `diarchy` (HostName is always overridden). No hardcoded addresses remain. |
| `diarchy-ssh [--route auto|lan|tailscale] -- <remote words...>` | Selects a route and `exec`s `ssh` with route options plus `-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new`, passing stdin through. Remote words are passed to ssh unchanged after `<alias> --` (OpenSSH drops a `--` that follows the destination; verified with a local sshd). Usage errors exit 64. Exit 255 for ssh/route failure (route failure prints the route reason). |
| `diarchy-kvm-configure` | Renders `~/.config/lan-mouse/config.toml` (0600, atomic) from config: header `# Managed by diarchy`, `release_bind = ["KeyA", "KeyS", "KeyD", "KeyF"]`, `port = 4242`, `[authorized_fingerprints]` with `"<DIARCHY_HOST_FINGERPRINT>" = "host"` when set, one `[[clients]]` with `position = "<DIARCHY_KVM_SIDE>"`, `hostname = "host"`, `activate_on_startup = true`, `ips = [<LAN>, <Tailscale>]` (non-empty ones, LAN first), `port = 4242`. A pre-existing unmanaged file is backed up to `config.toml.backup.<UTC timestamp>` first. Prints `[ok] Lan Mouse configuration written (side: <side>)`. |
| `diarchy-mac-fingerprint` | Prints the Mac's Lan Mouse certificate SHA-256 fingerprint in FP form from `~/.config/lan-mouse/lan-mouse.pem` via `/usr/bin/openssl x509 -noout -fingerprint -sha256`. Exit 1 with `diarchy-mac-fingerprint: Lan Mouse certificate not found; start Lan Mouse once` when absent. |
| `diarchy-kvm on|off|toggle|status` | Existing. Unchanged output contract: first line `Diarchy KVM: enabled|already enabled|disabled|already disabled` for on/off; status prints `Diarchy KVM: <enabled|degraded|disabled> (clipboard bridge: <s>, file portals: <s>)`, exit 0 only when enabled. |
| `diarchy-stream [--diagnose|--dry-run] [--force-lan|--force-remote]` | Existing launcher, generalized (§9). Overlay only when `DIARCHY_STREAM_OVERLAY=1`. |
| Doctors | `diarchy-kvm-client-doctor`, `diarchy-clipboard-file-transfer-client-doctor`, `diarchy-client-doctor`: same `[ok]`/`[fail]` line format; read hosts/side from config; no hardcoded addresses. |

`install-lan-mouse.sh` (client) no longer writes `config.toml` itself (it calls `diarchy-kvm-configure` only when
a config exists) and no longer builds `Diarchy KVM.app`; if an owned `Diarchy KVM.app`
(`dev.sebastiangmz.diarchy-kvm`) exists it is removed. It installs the new commands above.

## 6. Host agent contract — `diarchy-host`

Python ≥ 3.11, standard library only. Source: package `host/diarchy_host/` plus launcher `host/diarchy-host`
(`#!/usr/bin/env python3` that runs the package). Installed to `~/.local/share/diarchy/agent/` with launcher
`~/.local/bin/diarchy-host`. Invoked from the Mac as `diarchy-ssh -- '~/.local/bin/diarchy-host' <args>`.

### 6.1 Envelope

Every invocation prints exactly one JSON object on stdout followed by `\n`; nothing else on stdout.

- Success: `{"schema": 1, "ok": true, "command": "<cmd>", ...payload}` — exit 0.
- Handled failure: `{"schema": 1, "ok": false, "command": "<cmd>", "error": {"code": "<code>", "message": "<human text>"}}` — exit 1.
- Usage error: same failure envelope with `code` `usage` — exit 64.

Error codes: `usage`, `unsupported_platform`, `not_installed`, `no_session`, `service_failed`, `invalid_fingerprint`,
`config_write_failed`, `sunshine_unreachable`, `pair_failed`, `internal`.

### 6.2 Commands

| Command | Payload / behavior |
|---|---|
| `version` | `{"agent_version": "<VERSION file contents>"}` |
| `probe [--controller-ip IPv4]` | Read-only. Payload in §6.3. |
| `kvm authorize --fingerprint FP` | Rewrites the host Lan Mouse config (managed header, `capture_backend = "layer-shell"`, `release_bind`, `port = 4242`, `[authorized_fingerprints]` with exactly `"FP" = "mac"`), 0600, atomic, backing up an unmanaged file. Restarts `diarchy-kvm.service` if active. `{"authorized": "FP"}` |
| `kvm configure` | Writes the same managed host Lan Mouse config, **preserving** the currently authorized fingerprint if the existing file is managed and has one, else an empty `[authorized_fingerprints]` table. Same file rules and restart rule as `kvm authorize`. `{"authorized": "FP"|null}` |
| `kvm start` | Stops `diarchy-sunshine.service` and `diarchy-display.service` if active, then `systemctl --user start diarchy-kvm.service`; verifies active. `{"service_active": true}` |
| `kvm stop` / `kvm status` | `{"service_active": bool}` |
| `stream start` | Stops `diarchy-kvm.service` if running (checked), pins Sunshine's adapter and encoder to the GPU that renders the selected output (§9.2; `config_write_failed` when `sunshine.conf` cannot be rewritten), then starts `diarchy-display.service` and `diarchy-sunshine.service`; verifies both active, then waits until Sunshine accepts TCP on `127.0.0.1:47984` (≤ 8 s, `DIARCHY_SUNSHINE_READY_TIMEOUT`), else `service_failed` `Sunshine did not start listening`. `{"service_active": true, "encoder": "nvenc"|"vaapi"|"unknown"}` |
| `stream stop` / `stream status` | `{"service_active": bool, "encoder": "nvenc"|"vaapi"|"unknown"|null}` |
| `stream configure` | The capture-GPU pin of `stream start` alone, starting nothing (`host/install.sh` runs it before restarting the services, so the encoder it validates is the one streaming will use). `{"adapter_name": "/dev/dri/renderDNNN"|null, "encoder": "nvenc"|"vaapi"|null}` |
| `stream pair --pin NNNN --name NAME` | Submits the Moonlight PIN to the local Sunshine API (§9.3). `{"paired": true}` |

**No desktop session.** `kvm start` and `stream start` need a live Hyprland user session (the Sunshine wrapper and
`diarchy-display` discover it through `hyprctl instances -j`: an entry with a positive `pid` and a non-empty
`instance` and `wl_socket`). When none exists (after an Omarchy "Logout" only SDDM's greeter Hyprland is left, which
`hyprctl` cannot see as the user) both commands fail with `no_session` `no Hyprland session is running for this user`
after their `not_installed` checks and before starting or stopping any unit, so nothing is left restart-looping.
`diarchy-display` likewise exits non-zero with the one-line stderr message
`diarchy-display: no Hyprland session is running for this user`. The `HYPRCTL_BIN` environment variable
overrides the binary, as elsewhere.

The host Lan Mouse config writer lives **only** in the agent (one internal function used by `kvm configure` and
`kvm authorize`). `host/install-lan-mouse.sh` installs the binary and unit, then calls `diarchy-host kvm configure`,
and additionally `diarchy-host kvm authorize --fingerprint FP` when given `--authorize-fingerprint FP`. The agent
must therefore be installed before `host/install-lan-mouse.sh` runs (`install-host.sh` guarantees this order).

### 6.3 `probe` payload

```json
{
  "schema": 1, "ok": true, "command": "probe",
  "agent_version": "1.0.0",
  "platform": {
    "supported": true,
    "adapter": "hyprland-arch",
    "os_id": "arch",
    "flavor": "omarchy",
    "compositor": "hyprland",
    "machine": "x86_64",
    "reasons": []
  },
  "session": {"graphical_session_active": true},
  "components": {
    "lan_mouse": {"installed": true, "version": "0.11.0", "service_active": true, "listening_udp": true},
    "clipboard": {"installed": true},
    "file_portal": {"installed": true, "service_active": true},
    "sunshine": {"installed": false, "service_active": false}
  },
  "kvm": {"host_fingerprint": "AA:...", "authorized_fingerprints": ["BB:..."], "port": 4242},
  "required_actions": [
    {"id": "install-packages", "severity": "required", "title": "...", "reason": "...", "command": "sudo pacman -S --needed ..."}
  ]
}
```

- `flavor` is `"omarchy"` when `~/.local/share/omarchy` exists, else `null`.
- `supported` is true only for an Arch-family x86_64 host with a live Hyprland instance (`hyprctl instances -j`
  returns at least one instance) and an active systemd user manager. Otherwise `false` with one human reason per
  failed condition, `adapter` = `"unsupported"`.
- `host_fingerprint` is computed from `~/.config/lan-mouse/lan-mouse.pem` (`openssl x509 -noout -fingerprint -sha256`),
  `null` when absent.
- `required_actions` ids and rules (never executed by the agent):
  - `install-packages` (required): any of `wl-copy`, `wl-paste`, `rsync`, `notify-send`, `openssl`, `jq`, `curl`
    missing, or PyGObject GTK 3 + GtkLayerShell 0.1 not importable. Command:
    `sudo pacman -S --needed <exact missing package names>` using the mapping `wl-clipboard`, `rsync`, `libnotify`,
    `openssl`, `jq`, `curl`, `python-gobject gtk3 gtk-layer-shell`.
  - `firewall-lan` (recommended): `ufw.service` active and `--controller-ip` given. Command:
    `sudo ufw allow proto udp from <controller-ip> to any port 4242`.
  - `firewall-tailscale` (recommended): `ufw.service` active and interface `tailscale0` exists. Command:
    `sudo ufw allow in on tailscale0 to any port 4242 proto udp`.
  - `firewall-streaming-lan` (recommended, streaming only): `--streaming`, `ufw.service` active and
    `--controller-ip` given. Command: `sudo ufw allow proto tcp from <ip> to any port 47984,47989,48010 && sudo ufw
    allow proto udp from <ip> to any port 47998:48000,48010`. Sunshine's web UI port 47990 is never opened.
  - `uinput-access` (required for streaming only, emitted when Sunshine is installed or requested): `/dev/uinput`
    not writable by the user. Command installs the udev rule (§9.2).
  - `lid-policy` (recommended for streaming only): logind `HandleLidSwitch` resolves to `suspend`. Command in §9.2.
  - `wifi-roaming` (recommended, `--streaming` only): the `wpa_supplicant` journal of the last 24 h
    (`journalctl -u wpa_supplicant --since -24h -o cat`) holds at least 10 `SME: Trying to authenticate with <bssid>`
    lines that name a different access point than the previous one, i.e. the host ping-pongs between access points of
    its Wi-Fi network (every reassociation silences the radio for a few hundred ms, which the stream shows as a burst of
    lost frames and a wait for the next IDR frame). Only emitted when `nmcli` also names the active connection of that
    interface and the strongest cached access point of its SSID; otherwise nothing is reported (the agent never offers
    a command it could not complete). NetworkManager + wpa_supplicant hosts only. Command in §9.2.

### 6.4 Platform adapter seam

`diarchy_host/platform.py` defines `class PlatformAdapter(Protocol)` with: `detect() -> PlatformInfo`,
`missing_packages() -> list[str]`, `install_command(packages: list[str]) -> str`,
`kvm_capture_backend() -> str`, and `graphical_session_active() -> bool`. This project ships `HyprlandArchAdapter`
and `UnsupportedAdapter`; `select_adapter()` returns the first adapter whose `detect()` reports supported.

## 7. Installation and payload

- The app bundle contains `Contents/Resources/payload/{client/, host/, VERSION}` copied from the repository at
  build time. `VERSION` is the app version (`1.0.0` for this project).
- **Install on this Mac** (button): runs, in order, `payload/client/install-lan-mouse.sh`,
  `payload/client/install-clipboard-file-transfer.sh`, and (Streaming tab only) `payload/client/install.sh`.
  Output is streamed into the UI log. Each installer stays idempotent and checksum-pinned.
- **Install on the host** (button): `tar -C payload -czf - host VERSION | diarchy-ssh -- '<remote script>'` where the
  remote script extracts into `~/.local/share/diarchy/payload.new`, then atomically replaces
  `~/.local/share/diarchy/payload`, then runs
  `bash ~/.local/share/diarchy/payload/host/install-host.sh [--with-streaming]`.
- `host/install-host.sh`: installs the agent (package + launcher + VERSION), runs `diarchy-host probe`; aborts with
  `[fail] install-host.sh: <reason>` and exit 2 when the platform is unsupported or any `required` action is
  pending (printing each action's command); otherwise runs `host/install-lan-mouse.sh`,
  `host/install-clipboard-file-transfer.sh`, and with `--with-streaming` `host/install.sh`. Final line
  `[ok] Diarchy host installed (<VERSION>)`.

## 8. App behavior

### 8.1 Packaging

- SwiftPM package in `app/`: library `DiarchyCore`, executable `Diarchy`, tests `DiarchyCoreTests`
  (Swift Testing). One third-party dependency: `orchetect/MenuBarExtraAccess` (MIT, 1.3.x) to close the popover.
- `app/scripts/build-app.sh [--install]` builds release, assembles `app/build/Diarchy.app`
  (Info.plist: `CFBundleIdentifier` `dev.sebastiangmz.diarchy`, `CFBundleName` `Diarchy`,
  `LSUIElement` true, `LSMinimumSystemVersion` `14.0`, `CFBundleShortVersionString` = VERSION), copies the payload,
  ad-hoc signs (`codesign --force --deep --sign -`). `--install` replaces `~/Applications/Diarchy.app` only
  when absent or its bundle id is `dev.sebastiangmz.diarchy`, then registers it with `lsregister -f`.
- The old `client/Diarchy.applescript` and `client/Diarchy KVM.applescript` are deleted;
  `client/install.sh` no longer compiles a launcher.

### 8.2 Menu bar

`MenuBarExtra` (`.window` style) with SF Symbol `display.2`. Contents, top to bottom:
- Status header: an SF Symbol in a semantic color (`StatusLine.Tone`) and the status line: `Not configured` /
  `Desconectado` / `KVM activo` / `KVM degradado` / `Streaming activo` / `Error: <message>`.
- Mode picker: `Off`, `KVM`, `Streaming` (disabled with tooltip while setup is incomplete).
- `Settings…` (opens the Settings window and closes the popover), `Diagnostics…` (Settings on the Diagnostics tab),
  `Quit`; rows highlight under the pointer like native menu items.

The popover is presented through MenuBarExtraAccess so the app can close it (`MenuBarExtra` has no API for that).

**Quitting.** There is one termination path: `Quit` only calls `NSApp.terminate`, and `applicationShouldTerminate`
(app delegate) handles every route (menu, Cmd-Q, AppleScript `quit`, logout, shutdown). A running stream, a pairing or
a mode change in flight first runs the quit cleanup (wait for the pairing, stop the stream: Moonlight terminated, then
`diarchy-host stream stop`) while AppKit waits (`terminateLater`); Off and KVM terminate at once and are left as they
are. The wait is bounded to 15 s, after which the app terminates anyway so a logout is never held up (`Termination` in
`DiarchyCore`).

### 8.3 Settings window — tabs

1. **Connection**: form fields — SSH user, LAN host, Tailscale host, SSH port, SSH key file (optional, file picker);
   `Save and check` saves the config and runs the setup checklist (§8.4). A `General` section holds
   `Open at login` (`SMAppService.mainApp`, `LoginItem` in `DiarchyCore`; when macOS reports
   `requiresApproval` the switch stays on, an English hint points to System Settings › General › Login Items &
   Extensions, and a button opens it). The checklist is shown below with one row per step: icon, title, message, and
   when present a monospaced command with a `Copy` button and an action button.
2. **Displays**: a diagram with the Mac in the center and four drop zones; selecting a zone sets
   `DIARCHY_KVM_SIDE`, saves, runs `diarchy-kvm-configure`, and if KVM is enabled runs `diarchy-kvm off` then
   `diarchy-kvm on`.
3. **Streaming**: resolution, FPS, overlay toggle, `Install streaming` (Mac + host with `--with-streaming`),
   `Pair Moonlight` (§9.3), required actions from `probe` that concern streaming.
4. **Diagnostics**: `Run diagnostics` runs the three Mac doctors and `diarchy-host probe`, shows combined
   output in a monospaced, selectable text view, plus `Copy all`.

**Visual design.** First-party look from native SwiftUI only: the Settings window keeps toolbar-style `TabView`
tabs (HIG, Settings: panes switched from a toolbar; a sidebar is for many panes); Connection, Displays and Streaming
are `Form`s with `.formStyle(.grouped)` and `Section`s (Diagnostics is a toolbar over an output panel with an empty
state); status is an SF Symbol with a semantic color (`StatusLine.Tone` for the menu,
the checklist state for the rows) plus text, never color alone; one primary button per group
(`.borderedProminent`), the rest `.bordered`; commands and logs are monospaced, selectable and copyable; light/dark
mode and accent color come from the system. On macOS 26 and later the primary/secondary buttons use `.glassProminent`
/ `.glass` and the selected mode segment or screen zone is tinted `glassEffect`; macOS 14 and 15 get the plain styles
(`Design.swift`, every use behind `#available(macOS 26, *)`).

All user-facing text is English.

### 8.4 Setup checklist

Ordered steps; each has state `pending | ok | actionRequired(command?) | failed(message)`. A step runs only when
all earlier steps are `ok`.

| # | Step | Check | When not ok |
|---|---|---|---|
| 1 | Mac components | `~/.local/bin/diarchy-kvm`, `diarchy-ssh`, `diarchy-kvm-configure`, `diarchy-mac-fingerprint` executable and `~/Applications/Lan Mouse.app` present | action button `Install on this Mac` |
| 2 | Configuration | config loads and validates | failed with loader reason |
| 3 | SSH | `diarchy-ssh -- true` exit 0 | actionRequired with command `ssh-copy-id -p <port> [-i <identity>.pub] <user>@<first non-empty host>` |
| 4 | Host agent | `diarchy-ssh -- '~/.local/bin/diarchy-host' version` ok and version == bundled VERSION | action button `Install on the host` |
| 5 | Plataforma | `probe.platform.supported` | failed listing `reasons` |
| 6 | Required actions | no `required` action in `probe.required_actions` | actionRequired listing each command; `recommended` actions are listed but do not block |
| 7 | Pairing | Mac FP ∈ `probe.kvm.authorized_fingerprints` and config `DIARCHY_HOST_FINGERPRINT` == `probe.kvm.host_fingerprint` | action button `Pair` (§8.5) |

`--controller-ip` passed to `probe` is the Mac IPv4 on the interface that routes to the LAN host
(`route -n get <lan host>` → interface → `ipconfig getifaddr <interface>`); omitted when there is no LAN host or no
address.

### 8.5 Pairing

1. If `diarchy-mac-fingerprint` fails, launch Lan Mouse once (`open -gj ~/Applications/Lan Mouse.app`), wait up to
   10 s for the certificate, retry once.
2. `diarchy-ssh -- '~/.local/bin/diarchy-host' kvm authorize --fingerprint <macFP>`.
3. Re-probe; write `DIARCHY_HOST_FINGERPRINT=<probe.kvm.host_fingerprint>` to the config.
4. Run `diarchy-kvm-configure`.
5. Re-run the checklist.

### 8.6 Modes

State machine with states `off`, `kvm`, `streaming`, `transitioning`. Transitions (each step must succeed; any
failure stops, leaves the safest reachable state, and shows `Error: <stderr first line>`):

- `off → kvm`: `diarchy-host kvm start` → `diarchy-kvm on` → `diarchy-kvm status` exit 0.
- `kvm → off`: `diarchy-kvm off`.
- `* → streaming`: `diarchy-kvm off` (if kvm) → `diarchy-host stream start` → launch `diarchy-stream` as a child
  process; when that process exits, run `diarchy-host stream stop` and go to `off`. Once per streaming start (never
  for KVM) the app brings the Moonlight window to the front: the child pid is `diarchy-stream`, which runs `stream
  start` over ssh (about 10 s) before it `exec`s Moonlight, so the app polls every 250 ms until that pid registers as
  a running application (at most 60 s, ending early when the process is gone), activates it
  (`NSApp.yieldActivation(to:)` then `activate()`), and when it is
  still not active runs `/usr/bin/open -b com.moonlight-stream.Moonlight`. The session dropping Moonlight to the back
  when it connects and goes fullscreen is handled by a watch: for 20 s (every 500 ms) the app re-opens the bundle
  whenever Moonlight is not active, at most 4 times, stopping early when the process exits; leaving streaming or a new
  streaming start cancels it. No AppleScript (it would raise an Automation permission prompt).
- `streaming → off`: terminate the `diarchy-stream`/Moonlight child (SIGTERM, 5 s, SIGKILL), then
  `diarchy-host stream stop`.
- `kvm → streaming` and `streaming → kvm` pass through `off`.

Current state is derived on menu open from `diarchy-kvm status` and whether the app's Moonlight child is alive.
The app runs **no timers and no background polling**; it refreshes on menu/window appearance and after actions.
Every external command runs off the main thread with a timeout: 15 s for ssh commands, 30 s for `stream start`
(covers the Sunshine readiness wait), 40 s for `stream pair`, 20 s for local commands, 900 s for installers.

## 9. Streaming

### 9.1 Mac

- `client/diarchy-stream` reads hosts, resolution, FPS and overlay from config; profiles keep their codec and
  bitrate policy (LAN: HEVC 25 Mbps; remote: HEVC 15 Mbps; degraded: H.264 8 Mbps at 1280x720 and min(FPS, 60))
  with resolution/FPS from config for LAN and remote. `--performance-overlay` only when overlay is `1`.
  `--video-decoder hardware` stays mandatory.
- `client/install.sh` installs Moonlight and the `diarchy-stream` commands only.

### 9.2 Host

- `host/install.sh` (Sunshine) no longer pins NVENC: it accepts NVENC or VA-API evidence from the current invocation
  log (the log checker accepts either family, still rejects software encoding and capture failures).
- **Encode on the GPU that renders the captured output.** Sunshine's capture hands dmabufs to the encoder, which
  cannot import frames rendered by another GPU (hybrid laptop: an NVIDIA encoder on Intel-rendered frames logs
  `GL_INVALID_OPERATION` and streams black). `diarchy-host stream start` resolves the capture GPU from sysfs
  (`DIARCHY_SYSFS_ROOT`, default `/sys`; no `lspci`) and renders `adapter_name = /dev/dri/renderDNNN` and
  `encoder = nvenc|vaapi` into the managed `~/.config/sunshine/sunshine.conf` (atomic, mode 600, header and all other
  lines kept, idempotent) before starting the units. The output is the one `diarchy-display reconcile`
  selects: for a physical connector (`eDP-1`), the card behind `/sys/class/drm/card*-<connector>`; for the headless
  `DIARCHY` or an unknown connector, the card whose PCI device has `boot_vga == 1`, else the first card with a
  render node. Driver `nvidia` → `nvenc`; any other (i915, xe, amdgpu, …) → `vaapi`. With no resolvable GPU the two
  lines are removed and Sunshine selects by itself. The static `host/sunshine.conf` pins no device; `stream start`
  always reconciles the lines (the installer leaves them to it).
- **Host libva.** The Sunshine AppImage bundles libva 1.x, which cannot load the host's current VA-API drivers. The
  `~/.local/bin/sunshine` wrapper, when `/usr/lib/libva.so.2` and `/usr/lib/libva-drm.so.2` both exist, exports
  `LD_PRELOAD` with them (prepended to any existing value) and, when `/usr/lib/dri` exists and the caller set none,
  `LIBVA_DRIVERS_PATH=/usr/lib/dri` (paths overridable with `DIARCHY_LIBVA_PATH`, `DIARCHY_LIBVA_DRM_PATH`,
  `DIARCHY_LIBVA_DRIVERS_DIR`). The wrapper also unsets the session's `LIBVA_DRIVER_NAME` (Omarchy exports `nvidia`
  session-wide, which libva would try on the Intel render node) so libva picks the adapter's own driver;
  `DIARCHY_LIBVA_DRIVER_NAME`, when non-empty, is exported as `LIBVA_DRIVER_NAME` instead.
- `uinput-access` command:
  `printf 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"\n' | sudo tee /etc/udev/rules.d/60-diarchy-uinput.rules && sudo udevadm control --reload && sudo udevadm trigger --name-match=uinput`
- `lid-policy` command:
  `sudo mkdir -p /etc/systemd/logind.conf.d && printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\n' | sudo tee /etc/systemd/logind.conf.d/60-diarchy.conf`
- `wifi-roaming` command (no `sudo`; `<name>` is the shell-quoted connection name, `<bssid>` the strongest access
  point of the SSID): `nmcli connection modify <name> 802-11-wireless.bssid <bssid> && nmcli connection up <name>`.
  The reason text names the undo (`nmcli connection modify <name> 802-11-wireless.bssid ""`) and prefers a wired
  connection. Evidence: `docs/superpowers/reports/2026-10-12-stream-quality-findings.md`.
- The headless-output reconciler and service units stay as designed in the 2026-07-22 mirror design.
- `host/diarchy-host-doctor` reads the controller address from its first argument instead of a hardcoded IP
  and skips the loss probe when none is given.

### 9.3 Moonlight pairing

1. App generates a random 4-digit PIN.
1b. Pre-check, run once step 2 has chosen the host and before anything is started: `Moonlight list <that host>` (15 s).
   Exit 0 with at least one non-empty stdout line that is not a log line (`Redirecting log output…`) means this Mac
   is already paired (Moonlight stores the host and then never sends `/pair`, so the agent could only time out): steps
   2–4 are skipped, the app reports "Moonlight ya estaba emparejado con el host." and stops the host stream as
   usual. Any other outcome runs steps 2–4.
2. After `stream start` succeeds, the app runs `diarchy-stream --dry-run`, takes the host chosen by it (the `host=`
   value of its `route=` line), and starts `Moonlight pair <that host> --pin <PIN>` as a child (Moonlight Qt CLI).
3. Concurrently runs `diarchy-host stream pair --pin <PIN> --name "Diarchy"`, which POSTs
   `{"pin": "<PIN>", "name": "<name>"}` to `https://localhost:47990/api/pin` with the Sunshine UI credentials stored
   by the Sunshine installer (`~/.config/sunshine/` state), TLS verification disabled for localhost only, retrying
   for up to 20 s while Moonlight's request is not yet pending.
4. Success is decided **only** by the agent returning `{"paired": true}`. Moonlight 6.1.0's `pair` is GUI-bound
   (CliPair.qml) and exits 0 on both success and failure (verified by t4 docs-research agent), so its exit code is ignored.
   After the agent answers (success or failure), the app terminates the Moonlight child if still running
   (SIGTERM, 5 s, SIGKILL). Sunshine `/api/pin` POST `{pin, name}` with basic auth is verified.
5. Pairing is transient and leaves the app in the mode it found. From KVM, the app switches KVM off before step 2 and,
   whatever the pairing's outcome (success, already paired, failure), brings KVM back through the normal mode path
   (`kvm start` → `diarchy-kvm on` → `status`) once the host stream is stopped; a pairing error keeps priority over a
   failed return to KVM, and nothing runs after a KVM that would not switch off. From Streaming the stream keeps
   running and the pairing does not stop it; from Off the app stays off. Quitting during a pairing waits for it.

## 10. Security

- No sudo anywhere; required actions are displayed, never executed (ADR 0002).
- Config and Lan Mouse configs are 0600; config dir 0700.
- Remote commands sent over ssh are fixed strings composed only of validated values (FP, PIN digits, name
  constant); no user free text reaches a remote shell.
- `StrictHostKeyChecking=accept-new` with a stable `HostKeyAlias`, so LAN and Tailscale routes share one known host
  key and a changed key fails closed.
- Fingerprints are values, not secrets; Sunshine credentials never leave the host.
- No network listener is added on the Mac. No UPnP, no port forwarding.

## 11. Failure behavior

| Condition | Behavior |
|---|---|
| Config missing | Menu shows `Not configured`; modes disabled; Settings opens on Connection. |
| Host unreachable on all routes | Step 3 fails with the route reason (`Host is unreachable on LAN and Tailscale`). |
| Host agent older/newer than app | Step 4 actionRequired: reinstall on host. |
| Unsupported host | Step 5 failed with reasons; install button hidden. |
| Pending required action | Step 6 lists exact commands; modes disabled. |
| KVM start fails after host service started | App runs `diarchy-kvm off`; state `off`; error shown. |
| No Hyprland session on the host (`no_session`) | Nothing is started; state `off`; error shown: `No Omarchy session is open on the host (did it log out?). Log in on the host, or restart the login screen over SSH with: sudo systemctl restart sddm`. A `service_failed` agent error (start or stop) reads `A host service failed: <agent message>`. |
| Moonlight exits | `stream stop` on host; state `off`. |
| Command timeout | Process killed; step/state shows `Error: <command> did not respond in time`. |

## 12. Testing

- Python `unittest` (existing style: fake executables on `PATH`, temp HOME) for all bash and Python changes:
  config loader, route, diarchy-ssh argv, kvm-configure rendering per side, mac-fingerprint, host agent commands
  and probe rules, install-host.sh gating, generalized log checker.
- Swift Testing for `DiarchyCore`: config codec round-trip and every validation reason, checklist step logic with
  a fake `CommandRunner`, probe JSON decoding, mode state machine transitions and failure paths, controller-IP
  resolution parsing, ssh-copy-id command composition.
- Full suite: `python3 -m unittest discover -s tests` and `swift test --package-path app` both pass.
- Smoke on this Mac: build and install the app, launch it, verify the menu and every Settings tab render, save a
  config, run the checklist against the real host when reachable.
- Physical acceptance (requires a reachable host): pair, cross the mouse for each side, clipboard and file portal
  round trip, streaming start/stop, KVM/streaming exclusivity. Recorded with evidence when executed.

## 13. Non-goals

- Host → Mac control; per-monitor placement or edge offsets; non-Arch or non-Hyprland hosts (seam only).
- Intel Macs; embedded video rendering; audio streaming.
- Automatic execution of privileged commands; storing sudo passwords.
- Mac App Store distribution, notarization, auto-update.
