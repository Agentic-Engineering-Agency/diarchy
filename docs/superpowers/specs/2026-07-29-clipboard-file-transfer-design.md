# Host Clipboard and File Transfer — Design Specification

**Date:** 2026-07-29
**Status:** Implemented and verified from the user-approved design
**Predecessors:** `2026-07-22-diarchy-design.md`, `2026-07-22-lan-mouse-local-kvm-design.md`
**Source of truth:** this document. Where it disagrees with earlier specs, this one wins for the clipboard and file-transfer surfaces; the earlier specs continue to govern Sunshine/Moonlight streaming and the Lan Mouse KVM.

## 1. Goal

Add two small user-visible capabilities on top of the existing Diarchy stack, without touching the KVM or streaming media paths:

1. **Text clipboard sharing** between macOS and the host Hyprland session. Bidirectional, text-only, opt-in, and live **only while the Diarchy KVM is enabled**. Transported over an existing Mac-initiated SSH connection using the standard macOS pasteboard commands (`pbcopy`, `pbpaste`) and `wl-clipboard` (`wl-copy`, `wl-paste`) on Hyprland.
2. **Manual file transfer** between the two machines using SSH plus `rsync`. Fixed drop folders on both sides, no overwrite by default, LAN/Tailscale route selection identical in intent to the Sunshine/Moonlight launcher, and a native macOS drag-and-drop sender app.

Both features are opt-in, rootless, deterministic, and add **no new listening port** to either machine. Lan Mouse, Sunshine, and Moonlight behavior, protocols, and configuration are unchanged and byte-identical after installation.

## 2. Non-goals

- Clipboard for images, files, HTML, RTF, or any non-text pasteboard type.
- Persistent clipboard history or any on-disk record of clipboard bytes.
- Auto-start of the clipboard bridge at login. The bridge lives and dies with the KVM toggle.
- New inbound listening sockets on either machine. Only outbound SSH from Mac to the host on the already-open TCP 22.
- Dropbox-style continuous two-way file synchronization, watched folders, or push notifications from the host to the Mac.
- Automatic overwrite or merge on file-name collisions.
- Any change to pre-existing Lan Mouse or systemd configuration, `capture_backend`, authorized fingerprints, `Diarchy KVM.app`, `Diarchy.app`, Sunshine, Moonlight, or the streaming path. The feature may add only its owned `diarchy-file-portal.service`.
- Any management of the user's `~/.ssh/config`, `~/.ssh/known_hosts`, SSH identities, or `authorized_keys`. The existing paired SSH setup is a precondition and is not touched by any installer or doctor in this feature.
- x86 Mac or non-Wayland host support.
- Content encryption beyond what SSH already provides.
- Relay fallback (DERP, cloud broker). LAN and direct Tailscale peer only.

## 3. Architecture

### 3.1 Topology

```
+-------------------------+                 +-----------------------------+
| Mac (192.0.2.20)     |                 | Host (192.0.2.10     |
|                         |                 |          / 100.64.0.10)  |
|  Diarchy KVM.app       |                 |                             |
|      |                  |                 |                             |
|      v                  |    outbound     |                             |
|  diarchy-kvm on ------> spawns bridge     |                             |
|      |                  |                 |                             |
|      v                  |                 |                             |
|  diarchy-clipboard-bridge --- SSH TCP 22 -> diarchy-clipboard-remote    |
|   (stdio pump, hash     |  (existing,     |   (stdio pump, hash filter, |
|    filter, cap 100 KiB) |   Mac-initiated)|    cap 100 KiB)             |
|      ^        v         |                 |      ^         v            |
|      |        v         |                 |      |         v            |
|   pbpaste   pbcopy      |                 |  wl-paste     wl-copy       |
|                         |                 |                             |
|  diarchy-file / Drop.app --- SSH + rsync -> ~/Drop/from-mac/            |
|  diarchy-file get       <-- SSH + rsync --- ~/Drop/to-mac/              |
+-------------------------+                 +-----------------------------+
```

- The KVM (Lan Mouse over UDP 4242) is unrelated to this transport. It stays on its own socket, fingerprints, and lifecycle.
- Every clipboard byte and every file byte travels over SSH TCP 22, initiated by the Mac.
- Host adds only transient processes: `diarchy-clipboard-remote`, its currently owned `wl-copy --foreground` child while serving a Mac-originated clipboard value, and `rsync` invocations during file transfer. All clipboard-side processes terminate with the KVM session.

### 3.2 Transport: reuse the existing SSH alias

The Mac's OpenSSH configuration already defines a working `diarchy` alias with a dedicated identity and host-key policy; `ssh -o BatchMode=yes diarchy true` already succeeds. The alias is the **only authentication surface** this feature depends on. The implementation never names or discovers the underlying identity file.

> Superseded by the app design (`2026-10-11-diarchy-app-design.md`). `diarchy` is now an internal label. The scripts set `HostName`, `User` and the port on every call. The user does not add an SSH config entry.

Consequences:

- No component directly opens, edits, moves, or backs up `~/.ssh/config`, `~/.ssh/known_hosts`, or any file under `~/.ssh/` on either side. The invoked `/usr/bin/ssh` process necessarily reads the user's own OpenSSH configuration.
- `IdentityFile`, `UserKnownHostsFile`, and `StrictHostKeyChecking` remain whatever the user's existing SSH configuration provides. The design does not name, print, copy, or relocate any private key path or key material.
- The host `authorized_keys` pairing is a precondition. Neither installer creates or mutates it. The doctor verifies the paired path and, on failure, prints a manual `ssh-copy-id diarchy` pairing hint without modifying remote state.

**Routing on top of the alias.** Every operation keeps the alias for user, identity, and host-key policy, but selects the endpoint per invocation with `-o HostName=<selected-host> -o Port=<port>`. It also uses `-o ControlMaster=no -o ControlPath=none` so an existing multiplexed connection for the other route cannot defeat the selected endpoint. No OpenSSH configuration file is changed.

**Configurable overrides** (all optional, unset by default):

| Environment variable | Purpose | Default |
| --- | --- | --- |
| `DIARCHY_SSH_ALIAS` | Override the SSH alias name if the user renames it. | `diarchy` |
| `DIARCHY_LAN_HOST` | LAN address used for the LAN route override. | `192.0.2.10` |
| `DIARCHY_TAILSCALE_HOST` | Tailscale address used for probe and the Tailscale route override. | `100.64.0.10` |
| `DIARCHY_SSH_PORT` | TCP port used for both reachability probes and SSH/rsync connections. | `22` |

Overrides align 1:1 with the pattern already in `client/diarchy-stream` (`DIARCHY_LAN_HOST`, `DIARCHY_TAILSCALE_HOST`) and keep the shipped defaults matching the values verified on this deployment. The overrides are read but never persisted.

### 3.3 Components

Repository shape adds one directory per side and one bundle. No existing file layout is removed.

**Host (`host/`)**
- `install-clipboard-file-transfer.sh` — rootless installer for the host side. Verifies `/usr/bin/wl-copy`, `/usr/bin/wl-paste`, `/usr/bin/rsync`, `python3` ≥ 3.11, and an active `sshd` are present (all confirmed on the current host). Creates the drop folders. Installs `diarchy-clipboard-remote` under `~/.local/bin`. Does not directly access `~/.ssh/`.
- `diarchy-clipboard-remote` — Python 3 stdio pump invoked by SSH. Polls `wl-paste --type text/plain --no-newline` on the same cadence as the Mac bridge, emits changes in the wire framing (§4), and applies each inbound frame through one owned `wl-copy --foreground --type text/plain` child. Before applying a replacement it terminates and reaps the previous owner; on shutdown it terminates and reaps the current owner.
- `diarchy-file-transfer-host-doctor` — read-only, follows the existing `ok/fail/warn` convention. Checks drop folders' existence and modes, `wl-clipboard`/`rsync`/`python3` presence, and that the `diarchy-kvm.service` user unit is active (unchanged from the KVM spec; verified here for cross-feature consistency).

**Mac (`client/`)**
- `install-clipboard-file-transfer.sh` — rootless installer for the Mac side. Verifies `/usr/bin/pbcopy`, `/usr/bin/pbpaste`, `/usr/bin/ssh`, `/usr/bin/rsync`, `/usr/bin/osacompile`, `/usr/bin/python3`. Verifies the existing `diarchy` SSH alias works via `ssh -o BatchMode=yes -o ConnectTimeout=5 "$DIARCHY_SSH_ALIAS" true`. Installs three commands under `~/.local/bin` and compiles the drag-and-drop droplet under `~/Applications`.
- `diarchy-clipboard-bridge` — Python 3 stdio pump. Polls `pbpaste -Prefer txt` on a 200 ms cadence, applies inbound frames via `pbcopy`, and multiplexes with an SSH subprocess to `diarchy-clipboard-remote`.
- `diarchy-file` — Bash launcher with three verbs: `send`, `get`, `list`. Selects the SSH route (§3.6) and invokes `rsync` over SSH.
- `diarchy-clipboard-file-transfer-client-doctor` — read-only, `ok/fail/warn` convention. Verifies commands, drop folder mode, LAN and Tailscale probes, and a `ssh -o BatchMode=yes` liveness check against the selected route; never directly accesses an OpenSSH configuration or key file.
- `Diarchy Drop.applescript` → compiled and ad-hoc signed as `Diarchy Drop.app` under `~/Applications` with bundle identifier `dev.sebastiangmz.diarchy-drop`. Accepts drag-and-drop; iterates each dropped item and invokes `~/.local/bin/diarchy-file send`.

**KVM lifecycle integration (`client/diarchy-kvm`)**

The clipboard installer replaces `~/.local/bin/diarchy-kvm` with the repository's updated version. The later edge-file-portal extension makes the clipboard bridge and both file-portal processes mandatory KVM components: `turn_on` preflights and starts the complete stack transactionally, while `turn_off` stops every owned component. The bridge owns a singleton runtime directory `${TMPDIR}/diarchy-clipboard-bridge-$UID/` containing `pid`; startup rejects a live owner and removes only a stale runtime directory. `turn_off` reads the PID, verifies the exact bridge process identity, signals it, waits up to 2 s, and removes stale state only after the process is gone. Both the Lan Mouse and clipboard/file-transfer installers copy this shared `diarchy-kvm`; the Lan Mouse installer explicitly directs the operator to complete the clipboard/file-transfer installation before enabling KVM.

### 3.4 Command contracts

`diarchy-file` (Mac):

```
diarchy-file send [--overwrite] [--dry-run] [--route lan|tailscale|auto] <path>...
diarchy-file get  [--overwrite] [--dry-run] [--route lan|tailscale|auto] <name>...
diarchy-file list [--route lan|tailscale|auto]
```

- `send` uploads each `<path>` to `host:~/Drop/from-mac/<basename>` using `rsync --archive --partial --partial-dir=.rsync-partial --itemize-changes --out-format=%i --ignore-existing` (dropping `--ignore-existing` when `--overwrite` is given; rsync's default temp-file rename provides atomic publication in both cases). Directories are supported. Under no-overwrite mode, exit 0 with no itemized change means the existing destination was skipped. The wrapper exits non-zero when any item fails or is skipped and prints one status line per item: `send name=<basename> bytes=<n> route=<lan|tailscale> status=<ok|skipped-exists|failed>`.
- `get` downloads each `<name>` from `host:~/Drop/to-mac/<name>` to `~/Drop/from-host/<name>` with the same itemized flags, collision classification, and aggregate semantics.
- `list` prints, one per line, `name=<basename> bytes=<n> mtime=<iso8601>` for every regular file directly under `host:~/Drop/to-mac/` (no recursion, no `.` entries). Implemented via a single SSH round-trip to `stat -c "%n\t%s\t%Y"` and formatted locally.
- `--route auto` (default) picks LAN first, Tailscale second (§3.6). Explicit `--route lan` or `--route tailscale` fails closed if the requested route is unreachable.
- `--dry-run` prints the fully expanded `rsync` command line and route without transferring, and exits 0.

`diarchy-clipboard-bridge` (Mac, internal):

```
diarchy-clipboard-bridge [--route lan|tailscale|auto] [--poll-ms N]
```

- Long-running foreground process. On `SIGTERM` or `SIGINT`, terminates the SSH child, waits up to 2 s for it to exit, and returns 0. Default poll cadence 200 ms; the flag exists to make the frame-drop tests deterministic (5 ms in tests).
- Stderr is the only diagnostic channel. It emits only metadata: frame direction, byte length, sha256 prefix (first 8 hex), decision (`sent`, `applied`, `dropped:echo`, `dropped:cap`, `dropped:empty`, `dropped:decode`). It **never** emits clipboard bytes.

`diarchy-clipboard-remote` (Host, invoked over SSH, internal):

```
diarchy-clipboard-remote [--poll-ms N]
```

- Mirrors the Mac framing, hash, cap, and signal guarantees. Its additional invariant is exactly zero or one owned `wl-copy --foreground` child. Applying a new inbound value replaces that child without allowing the transient empty selection to enter the polling path. Shutdown terminates and reaps the child before the remote exits.

`diarchy-kvm` (Mac, existing surface, extended contract):
- Verbs remain `on | off | toggle | status`; the AppleScript entry point is unchanged.
- `on` requires Lan Mouse, clipboard bridge, Mac file portal, and file-portal receiver readiness; missing or crashed components fail closed and roll back anything started by that invocation.
- `off` deterministically stops the receiver, Mac portal, clipboard bridge, and Lan Mouse. `status` reports `Diarchy KVM: enabled|disabled|degraded (clipboard bridge: on|off|absent|crashed, file portals: on|off|absent|crashed)` and exits non-zero for degraded state.

### 3.5 Fixed drop folders

Created by the respective installers with mode `0700`. Never auto-cleaned by any component of this project.

| Path | Owner | Direction | Mode |
| --- | --- | --- | --- |
| `~/Drop/from-mac/` (Host) | Host | inbound from Mac | 0700 |
| `~/Drop/to-mac/` (Host) | Host | outbound to Mac | 0700 |
| `~/Drop/from-host/` (Mac) | Mac | inbound from the host | 0700 |

Only the leaf `Drop/` directory and the named subfolder are created; the installer does not modify permissions on `$HOME` or any ancestor. If `~/Drop/` already exists with a stricter mode, the installer uses it as-is and only ensures the leaf subfolder mode is 0700.

### 3.6 LAN/Tailscale route selection

Both `diarchy-file` and `diarchy-clipboard-bridge` use one shared shell function library (`client/diarchy-route.sh`, sourced by both) that reproduces the probe policy already in `client/diarchy-stream`, retargeted to SSH:

1. Prefer `$DIARCHY_LAN_HOST` (default `192.0.2.10`) when `route -n get` returns a local, non-tunnel interface (`utun*`, `tun*`, `tap*`, `tailscale*`, `ipsec*`, and `ppp*` are rejected as LAN) and `nc -z -G 3 -w 3 <host> "$DIARCHY_SSH_PORT"` succeeds.
2. Otherwise probe `$DIARCHY_TAILSCALE_HOST` with the same route and TCP checks. The bounded ICMP probe supplies `loss_pct` and `median_ms` diagnostics only; an ICMP-silent host remains eligible when its SSH port is reachable.
3. For either selected route, invoke SSH with `ControlMaster=no`, `ControlPath=none`, `HostKeyAlias=$DIARCHY_TAILSCALE_HOST`, `HostName=<selected-host>`, and `Port=$DIARCHY_SSH_PORT`, preserving `$DIARCHY_SSH_ALIAS` for user/config lookup. This verifies LAN and Tailscale connections against one canonical host-key identity without disabling strict verification. Explicit routes fail closed when their requested endpoint is unavailable; `auto` fails with `Host is unreachable on LAN and Tailscale` when neither succeeds.

The probe is deterministic, bounded (< 6 s wall clock in the worst reachable case), writes no persistent state, and prints one line to stderr: `route=<lan|tailscale> host=<addr> interface=<name> loss_pct=<n> median_ms=<n>`.

## 4. Wire framing, deduplication, and size cap

Both bridge processes speak one binary-safe, line-header/raw-body framing over stdio:

```
CLIP <hex-sha256> <byte-length>\n
<byte-length raw UTF-8 bytes; NO trailing newline is added>
```

- `hex-sha256` is exactly 64 lowercase hex chars.
- `byte-length` is a decimal integer, `0 ≤ len ≤ 102400`.
- Reader: read one line ending in `\n`; validate `CLIP <64-hex> <int>`; if invalid, log `dropped:decode` and resync by discarding bytes until the next `CLIP ` header. Then read exactly `byte-length` bytes and verify `sha256(bytes) == hex-sha256`; on mismatch, log `dropped:decode` and drop only that frame.
- Empty frames (`len == 0`) are legal and applied as clearing events only when the local clipboard is currently a non-empty text value that differs from empty; otherwise dropped as `empty-no-change` to avoid clearing storms during startup.

**Deduplication (anti-echo) invariant.** Each side keeps two ephemeral in-memory hashes:

- `last_out_hash` — sha256 of the frame most recently transmitted from this side.
- `last_in_hash` — sha256 of the frame most recently applied from the other side.

Rules:
- On a **local** clipboard change with hash `h`:
  - if `h == last_in_hash` → this event is our own application of an incoming frame; drop, log `dropped:echo`.
  - else transmit; set `last_out_hash = h`.
- On an **incoming** frame with hash `h`:
  - if `h == last_out_hash` → the far side echoed our own transmission; drop, log `dropped:echo`.
  - else apply to local clipboard; set `last_in_hash = h`; do **not** touch `last_out_hash`.

Both hashes reset on bridge start. Neither hash nor content is persisted.

**Size cap.** `MAX_BYTES = 102400` (100 KiB). Payloads strictly greater than 102400 bytes are dropped **at the source**: not transmitted, not applied. Diagnostic: `dropped:cap direction=<local|remote> bytes=<n>`. The far side's clipboard is not touched.

**Text-only.** Mac reads via `pbpaste -Prefer txt`; non-text pasteboards return empty and are indistinguishable from an empty text clipboard (dropped as `empty-no-change`). Host reads via `wl-paste --type text/plain --no-newline`; `wl-paste` returns non-zero when the MIME type is unavailable, which the pump treats as "no text present" and does not transmit.

## 5. Data flows

### 5.1 Clipboard: KVM on

1. User clicks `Diarchy KVM.app` → AppleScript calls `~/.local/bin/diarchy-kvm toggle`.
2. `diarchy-kvm` calls `turn_on`: `open -gj "$LAN_MOUSE_APP"`, verifies liveness. Then, iff `~/.local/bin/diarchy-clipboard-bridge` is executable, forks it in the background with stderr silenced from the Terminal (a doctor-only debug mode routes it to a bounded ring buffer under `${TMPDIR}` if `DIARCHY_CLIPBOARD_DEBUG=1`).
3. The bridge probes routes, opens `ssh <selected-route-options> "$DIARCHY_SSH_ALIAS" diarchy-clipboard-remote`, and starts the poll loop.
4. On the first non-empty differing local `pbpaste`, the bridge emits a frame. The remote sets `last_in_hash`, replaces its owned foreground `wl-copy` child, and resumes polling only after the new owner is serving the value. A newly observed host value is emitted and applied locally with `pbcopy`. Anti-echo (§4) prevents ping-pong.

### 5.2 Clipboard: KVM off

1. `diarchy-kvm off` sends `SIGTERM` to Lan Mouse (existing behavior), then signals the verified bridge PID from its runtime directory.
2. The bridge closes the SSH child's stdin, signals the SSH child, waits ≤ 2 s, removes its runtime directory, and exits 0.
3. The remote terminates and reaps its owned `wl-copy --foreground` child before exiting; `sshd` reaps the remote. No clipboard-side process or socket remains. Consequently, a host clipboard value whose owner was this bridge is intentionally unavailable after KVM off; values owned by other host applications are unaffected.

### 5.3 File send

1. Create `${TMPDIR}/diarchy-transfer-e2e.txt`, then run `diarchy-file send "${TMPDIR}/diarchy-transfer-e2e.txt"` → probe route → e.g. `route=lan host=192.0.2.10`.
2. Runs `rsync --archive --partial --partial-dir=.rsync-partial --itemize-changes --out-format=%i --ignore-existing -e 'ssh <selected-route-options>' "${TMPDIR}/diarchy-transfer-e2e.txt" "$DIARCHY_SSH_ALIAS":Drop/from-mac/`.
3. `rsync`'s native temp-file rename provides atomic publication; a `.rsync-partial/` sibling is used for resumability and never surfaces a partial file at the final name.
4. On `--ignore-existing`, if the target already exists, rsync skips it; the wrapper checks the exit code plus rsync's `--itemize-changes` output to distinguish `status=skipped-exists` from `status=ok` deterministically.

### 5.4 File get

1. `diarchy-file get notes.txt` → probe route → identical `rsync` flags in the reverse direction: `rsync ... -e 'ssh ...' "$DIARCHY_SSH_ALIAS":Drop/to-mac/notes.txt ~/Drop/from-host/`.
2. Received files' modes are normalized to `0600` post-transfer via `chmod 600` on each successful destination path.

### 5.5 File list

1. `diarchy-file list` → probe route → `ssh <selected-route-options> "$DIARCHY_SSH_ALIAS" 'stat -c "%n\t%s\t%Y" -- Drop/to-mac/* 2>/dev/null || true'`.
2. Output parsed and printed as `name=<basename> bytes=<n> mtime=<iso8601>`. Zero files prints nothing and exits 0.

### 5.6 Drag-and-drop send

1. User drops N items on `Diarchy Drop.app`.
2. AppleScript `on open theItems` handler iterates each item POSIX path and calls `do shell script "~/.local/bin/diarchy-file send " & quoted form of each` in sequence.
3. Per-item success or failure surfaces via `display notification`. Failures also `display alert` with the last stderr line, truncated to 240 chars (never any file body).

## 6. Rootless installers

The two installers follow the exact patterns already used by `host/install-lan-mouse.sh` and `client/install-lan-mouse.sh`:

- `set -euo pipefail`, no `sudo` anywhere.
- `--dry-run` prints planned actions, mutates nothing.
- All destinations under `$HOME`: `~/.local/bin`, `~/Applications`, `~/Drop/`.
- Neither installer directly opens or writes anything under `~/.ssh/`. SSH is a precondition. The Mac installer invokes `/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=5 "$DIARCHY_SSH_ALIAS" true`; that child reads the user's own OpenSSH configuration. On failure, the installer prints a manual pairing hint and exits non-zero.
- Config files that this project owns start with the sentinel line `# Managed by diarchy` and are staged via `mktemp` + `mv` to be atomic; managed configuration is created with mode `0600`. This feature ships **no** managed configuration outside its own scripts and the compiled droplet, so this rule only applies to future extensions.
- Downloaded artifacts (none required — everything is scripts and one AppleScript droplet in-repo) would be pinned by SHA-256 as in `install-lan-mouse.sh`. This feature does not download anything.
- Idempotent: reruns leave one command, one droplet, one drop folder per side.
- Both installers verify presence of the exact prerequisites listed in §3.3 (all verified present on the current pair) and fail with a precise `[fail]` message naming the missing binary. The installer never invokes `pacman`, `brew`, or any privileged path.
- Both installers refuse to overwrite an existing droplet whose bundle identifier is not `dev.sebastiangmz.diarchy-drop` (mirrors the KVM installer's `assert_replaceable`).
- Neither installer touches Lan Mouse, Sunshine, or Moonlight configuration. The host installer owns exactly `~/.config/systemd/user/diarchy-file-portal.service`; every pre-existing `diarchy-kvm.service`, `diarchy-display.service`, and `diarchy-sunshine.service` artifact remains byte-for-byte unchanged. A test in `tests/test_clipboard_file_transfer_install.py` snapshots those pre-existing units plus `~/.config/lan-mouse/` and `~/.config/sunshine/`, while separate tests verify exact installation and rollback of the portal's own unit.

## 7. Security and privacy

- **Authentication.** The existing SSH alias `diarchy` is authoritative for identity, user, and host-key policy. The user's already-paired identity and host-key trust are inherited unchanged. No component generates, stores, prints, copies, relocates, or directly reads a private-key file. The Mac installer and doctors observe only the `/usr/bin/ssh` exit status and stderr.
- **No listener.** No new inbound listener is opened on either machine. `sshd` on TCP 22 is already open by prior operational choice and is unchanged.
- **No content persistence.** The bridge writes nothing to disk. Diagnostics contain only length and sha256 prefix. Files land only in the recipient's drop folder (which is the point of the file transfer). `rsync` runs without `--log-file`.
- **Config privacy.** All managed config files (if any are ever added) are mode 0600. Drop folders are mode 0700.
- **No secrets in repo.** No keys, passphrases, host keys, private-key paths, or credential-derived paths appear in tracked files. The LAN and Tailscale addresses are existing deployment defaults already published in predecessor specs.

## 8. Failure behavior

- **KVM off.** No bridge or portal process exists on Mac; no SSH session; no `diarchy-clipboard-remote` or file-portal socket on the host. Doctors report the stack disabled and return success.
- **KVM on, component or SSH failure.** Startup fails closed, rolls back every component started by that invocation, and `diarchy-kvm status` reports `degraded` with the failed/missing component. Lan Mouse is not left running alone.
- **Frame decode error.** Single frame dropped with `dropped:decode`; pump continues.
- **Size cap exceeded.** Source-side drop; recipient's clipboard unchanged; diagnostic emitted.
- **Non-text pasteboard.** No frame emitted; recipient's clipboard unchanged.
- **`diarchy-file` with unreachable host.** Exits non-zero with `Host is unreachable on LAN and Tailscale`; no partial local or remote file.
- **`diarchy-file send` collision without `--overwrite`.** `rsync --ignore-existing` skips atomically; wrapper prints `status=skipped-exists` and exits non-zero (aggregate exit code = 1 when any file was skipped or failed) so callers such as `Diarchy Drop.app` can notify the user; the remote file is byte-identical to the pre-existing one.
- **`diarchy-file get` of a missing name.** `rsync` returns non-zero; wrapper prints one `status=failed reason=missing` line and no partial file appears in `~/Drop/from-host/`.
- **Broken SSH pairing** (identity removed, `authorized_keys` entry deleted, host key changed). All operations fail closed with the SSH client's own message; the doctor identifies the failing route and prints the one-line pairing hint. No component attempts to modify `~/.ssh/`.
- **Rerun of either installer.** Idempotent, non-destructive, drops any orphan stage files via the same `mktemp` + `mv` pattern used by the KVM installer.
- **Concurrent KVM restart.** The bridge's SSH child receives `SIGTERM`; the bridge exits cleanly; the next `turn_on` spawns a fresh instance. Anti-echo state is intentionally reset (see §4), so a fresh transmission is expected on the first change post-restart.
- **`xclip` on the host.** Not supported. `wl-clipboard` on the compositor's Wayland socket is the only supported source and sink on the host.

## 9. Frozen acceptance criteria

Numbered, mechanically checkable. The implementation is complete only when every one is observed against the real Mac + host pair.

1. **Bridge lifecycle bound to KVM.** After `diarchy-kvm on`, `${TMPDIR}/diarchy-clipboard-bridge-$UID/pid` names one live Mac process whose command line contains `diarchy-clipboard-bridge`; a script-basename process query returns exactly one `diarchy-clipboard-remote` on the host and at most one child `wl-copy --foreground` within 2 s. After `diarchy-kvm off`, the runtime directory is absent and all three process counts are 0 within 2 s.
2. **No listener added.** `ss -H -ltn` (Host) and `lsof -nP -iTCP -sTCP:LISTEN` (Mac) produce sorted output before and after installing and after a KVM on/off cycle whose difference is empty, with the sole exception of the pre-existing `sshd` and `lan-mouse` sockets. No new port is bound.
3. **Text round-trip Mac → host.** With KVM on, `printf 'hello-world' | pbcopy` on Mac results in `wl-paste --type text/plain` returning exactly `hello-world` on the host within 500 ms measured wall clock. No newline is added.
4. **Text round-trip host → Mac.** With KVM on, `printf 'ola-mundo' | wl-copy --type text/plain` on the host results in `pbpaste` returning exactly `ola-mundo` on Mac within 500 ms.
5. **Anti-echo.** Copying the same string twice in a row on either side transmits exactly one frame across the wire (measured by the bridge's stderr `sent` count). No ping-pong is observed for 5 s after the second copy.
6. **Size cap.** Copying a 102401-byte string on either side produces one `dropped:cap` on the source, no frame transmitted, and the recipient's clipboard byte-identical to its prior value. Copying exactly 102400 bytes succeeds.
7. **Non-text drop.** Copying an image (via `screencapture -c` on Mac or `wl-copy --type image/png` on the host) produces no `sent` frames and does not modify the recipient's clipboard.
8. **File send atomic-and-safe.** Sending `${TMPDIR}/diarchy-transfer-e2e.txt` writes `host:~/Drop/from-mac/diarchy-transfer-e2e.txt` with the same sha256. A second send with different local content and no `--overwrite` prints `status=skipped-exists`, exits non-zero, and leaves the remote sha256 unchanged. Sending with `--overwrite` succeeds and updates the remote sha256; no `.rsync-partial` residue remains.
9. **File get atomic-and-safe.** `diarchy-file get notes.txt` places `~/Drop/from-host/notes.txt` with sha256 matching the remote file, mode 0600. A second `get` without `--overwrite` refuses and does not modify the local file. `diarchy-file get missing.txt` refuses cleanly and leaves no local file.
10. **File list correctness.** `diarchy-file list` prints one line per file directly under `~/Drop/to-mac/` on the host, with correct byte length and mtime; recursion is not applied; hidden files are skipped.
11. **Drop.app.** Dropping three files onto `Diarchy Drop.app` invokes `diarchy-file send` exactly three times, produces three notifications, and results in all three files present under `host:~/Drop/from-mac/` with matching sha256.
12. **Route policy.** `diarchy-file list` prints `route=lan` when `$DIARCHY_LAN_HOST:$DIARCHY_SSH_PORT` is reachable and prints `route=tailscale` when only `$DIARCHY_TAILSCALE_HOST:$DIARCHY_SSH_PORT` is reachable. With both blocked, it prints `Host is unreachable on LAN and Tailscale` on stderr and exits non-zero.
13. **Rootless installers.** Both installers complete with exit 0, without `sudo`, and with `--dry-run` mutating nothing. Idempotent reruns produce byte-identical output on the second run. Neither installer directly opens or writes anything under `~/.ssh/`; only the invoked OpenSSH client may read its own configuration.
14. **KVM/streaming byte-preserved.** Recursive snapshots of `~/.config/lan-mouse/`, `~/.config/sunshine/`, the pre-existing `diarchy-kvm.service`, `diarchy-display.service`, and `diarchy-sunshine.service` units, `~/Applications/Lan Mouse.app/Contents/Info.plist`, and `~/Applications/Diarchy KVM.app/Contents/Info.plist` are identical before and after installing this feature. The new `diarchy-file-portal.service` is the sole owned unit addition. Existing KVM doctors continue to pass after the complete client/host installation.
15. **Doctors are read-only and complete.** Both new doctor scripts exit non-zero on any missing binary, wrong mode, or unreachable SSH probe; they never mutate managed state or directly open/write OpenSSH configuration and key files.
16. **No content persisted.** After a 60 s session that transmits at least 20 clipboard changes and 5 files, `find ~/Drop ~/Library/Logs ~/.local ~/.config -newer <t0> -type f` on Mac and the equivalent on the host list only the recipients' drop-folder files. No log, cache, or history file contains any clipboard byte.
17. **Alias/overrides honored.** With `DIARCHY_SSH_ALIAS=diarchy-alt DIARCHY_LAN_HOST=198.51.100.7 DIARCHY_TAILSCALE_HOST=100.64.0.20 DIARCHY_SSH_PORT=2222`, the bridge and CLI preserve the alias for authentication while using the selected overridden host and port; unset, they use the shipped endpoint defaults.

## 10. Predeclared evidence plan

Evidence is layered. The order is fixed; a lower layer failing halts the higher layers.

### 10.1 Targeted deterministic tests (`python3 -m unittest`)

New Python `unittest` modules following the existing convention (`tests/test_lan_mouse_host.py` style — temporary `HOME`, injected `PATH` stubs for `ssh`, `rsync`, `pbcopy`, `pbpaste`, `wl-copy`, `wl-paste`, `open`, `pgrep`, `pkill`, `stat`, `nc`, `route`, `ping`):

- `tests/test_clipboard_bridge.py` — anti-echo state machine, size cap boundary at 102399/102400/102401, frame parse/emit round-trip, non-text drop, malformed frame resync, managed `wl-copy --foreground` replacement/teardown, singleton PID ownership, and `SIGTERM` teardown ≤ 2 s using fake child processes.
- `tests/test_file_transfer.py` — `diarchy-file` argument parsing, `send`/`get`/`list` command shape, itemized `--ignore-existing` collision classification vs `--overwrite`, `--dry-run` prints and mutates nothing, aggregate exit codes with mixed per-file results, route override precedence, and all four environment overrides.
- `tests/test_route_selection.py` — probe policy against a stub `ping`/`route`/`nc` that emulates LAN reachable, LAN loss, LAN interface = `utun0` (Tailscale masquerading as LAN, must reject), Tailscale reachable, both blocked.
- `tests/test_clipboard_file_transfer_install.py` — both installers, dry-run inventory, idempotency, refusal to overwrite a foreign droplet, and byte-preservation of Lan Mouse/Sunshine fixtures. A poisoned `~/.ssh/` fixture catches any **direct project-code** access, while the injected `ssh` stub is explicitly exempt as the sole OpenSSH-config reader. The SSH liveness assertion checks BatchMode/timeout use and pairing-hint failure without mutating state.
- `tests/test_diarchy_kvm_hook.py` — updated `diarchy-kvm` script: `turn_on` spawns bridge iff present; `turn_off` reaps bridge; hook is no-op when bridge is absent; `status` output includes the bridge-state suffix; no diff in KVM-only behavior when the bridge binary is not installed.

All new tests plus the pre-existing suites must pass under `python3 -m unittest discover -s tests -v`.

### 10.2 Deterministic artifact gate

artifact-gate is initialized **before implementation** and remains a required branch gate. The installed artifact-gate harness currently supports deterministic `file_exists` assertions, so it owns artifact-contract enforcement while `unittest` owns executable behavior. Its smoke suite declares every required runtime script and targeted test module from §10.1. The pre-implementation run must fail because those artifacts do not yet exist; the post-implementation run must pass. Sign-off records the branch-specific report path, covered critical targets, `critical regressions=0`, and the report's sanitized privacy status.

Cases under `evals/cases/` cover these artifact groups:

- clipboard bridge and remote pump, plus `tests/test_clipboard_bridge.py`;
- file-transfer CLI, plus `tests/test_file_transfer.py`;
- shared route library, plus `tests/test_route_selection.py`;
- both installers and doctors, plus `tests/test_clipboard_file_transfer_install.py`;
- KVM lifecycle hook coverage in `tests/test_diarchy_kvm_hook.py`;
- the native droplet source `client/Diarchy Drop.applescript`.

artifact-gate is a harness concern: it is not installed into `~/.local/bin`, does not weaken or replace the behavioral tests, and is not referenced by runtime installers.

### 10.3 Real branch end-to-end matrix (Mac + host)

Run on the paired physical hardware after §10.1 and §10.2 are green. Each row records: preconditions, exact command(s), observation, and pass/fail.

**Positive**

| # | Scenario | Command / action | Observation |
| --- | --- | --- | --- |
| P1 | KVM on, Mac → host text | `printf 'p1-%s' "$(date +%s)" | pbcopy` on Mac | `wl-paste --type text/plain` on the host equals payload within 500 ms |
| P2 | KVM on, host → Mac text | `printf 'p2-%s' "$(date +%s)" | wl-copy --type text/plain` on the host | `pbpaste` on Mac equals payload within 500 ms |
| P3 | KVM off leaves clipboards independent | `diarchy-kvm off`; change both clipboards | Neither propagates; `pgrep` counts match §9.1 |
| P4 | File send | Create `${TMPDIR}/diarchy-transfer-e2e.txt`; run `diarchy-file send` on it | `host:~/Drop/from-mac/diarchy-transfer-e2e.txt` sha256 matches; one status line printed |
| P5 | Drop 3 files on `Diarchy Drop.app` | Drag three temporary files onto the app | 3 files present remotely with matching sha256; 3 macOS notifications |
| P6 | `diarchy-file list` | `diarchy-file list` | One line per remote file with correct bytes/mtime |
| P7 | `diarchy-file get notes.txt` | Prepare on the host; run on Mac | Local sha256 matches remote; mode 0600 |
| P8 | Route selection LAN preferred | When the verified LAN endpoint is reachable, run any `diarchy-file` command | Diagnostic `route=lan host=192.0.2.10`; otherwise record this row as INCONCLUSIVE rather than modifying system routes |
| P9 | Route falls back to Tailscale | Set `DIARCHY_LAN_HOST=127.0.0.2`; run any `diarchy-file` command | Diagnostic `route=tailscale host=100.64.0.10`; no route table is changed |
| P10 | Doctors green on healthy state | Run both doctor scripts | Both exit 0; every line prefixed `[ok]` |
| P11 | Alias override honored | `DIARCHY_SSH_ALIAS=diarchy DIARCHY_TAILSCALE_HOST=100.64.0.10 diarchy-file list --route tailscale` | Behaves identically to default; unchanged output shape |

**Negative**

| # | Scenario | Command / action | Observation |
| --- | --- | --- | --- |
| N1 | Size cap | `python3 -c "import sys; sys.stdout.buffer.write(b'x'*102401)" | pbcopy` | `dropped:cap` on bridge stderr; host clipboard unchanged |
| N2 | Non-text | `screencapture -c` (image) on Mac | Host clipboard unchanged; no `sent` frames |
| N3 | Echo storm suppression | Copy `same` on Mac 20 times in a tight loop | Bridge stderr shows exactly the number of distinct wire `sent` frames warranted by the rate of `pbpaste` polling (one per detected change); host `wl-paste` value equals `same` and never oscillates |
| N4 | File collision without `--overwrite` | Send `${TMPDIR}/diarchy-transfer-e2e.txt` twice with edited local content | Second call exits non-zero with `status=skipped-exists`; remote sha256 unchanged; `--overwrite` succeeds and remote sha256 flips |
| N5 | `get` of missing file | `diarchy-file get zzz.txt` (no such remote) | Exits non-zero with `status=failed reason=missing`; no local file created |
| N6 | Host unreachable | Set both endpoint overrides to `127.0.0.2` and the port to a verified-closed high port | `diarchy-file` exits non-zero with `Host is unreachable on LAN and Tailscale`; no network interface or route is changed |
| N7 | KVM off mid-transmission | Start `pbcopy` of 90 KiB on Mac; immediately `diarchy-kvm off` | Bridge exits ≤ 2 s; no `diarchy-clipboard-remote` remains; no crash log |
| N8 | Idempotent reinstall preserves KVM state | Rerun both new installers | Second run prints identical [ok] lines; KVM doctors still pass; sha256 snapshot per §9.14 unchanged |
| N9 | Route override refusal | Set `DIARCHY_TAILSCALE_HOST=127.0.0.2 DIARCHY_SSH_PORT=<verified-closed-port>` and force `--route tailscale` | Exits non-zero with `forced Tailscale route is unreachable`; no transfer |

Removing `authorized_keys`, rotating host keys, disabling interfaces, and mutating route tables are forbidden in branch E2E. Broken-pairing and host-key-change behavior is covered deterministically with a stubbed `/usr/bin/ssh` in §10.1.

### 10.4 Validator and reviewer

- **Validator** (the validator agent definition) runs `python3 -m unittest discover -s tests -v`, executes both new doctor scripts against a locally staged `$HOME`, and confirms exit codes. Returns binary PASS/FAIL.
- **Reviewer** (the reviewer agent definition) performs the post-implementation review across correctness (§9), security (§7), goal compliance (§1), and repository shape (§3.3). Reviewer walks every branch E2E row (§10.3) and refuses to sign off on missing evidence unless a row is explicitly reported INCONCLUSIVE with its environmental blocker.

## 11. Artifact-gate planning

```yaml
evalApplicability: applicable
evalTargets:
  - diarchy-clipboard-bridge (Mac stdio pump)
  - diarchy-clipboard-remote (Host stdio pump)
  - diarchy-file (send/get/list argument surface)
  - diarchy-kvm transactional full-stack turn_on/turn_off integration
  - route selection library (client/diarchy-route.sh)
riskTier: medium
failureModes:
  - Bridge loops on its own applied frame, causing clipboard oscillation.
  - Bridge transmits >100 KiB and truncates the recipient's clipboard.
  - Bridge transmits non-text (image) bytes and writes them into the recipient's clipboard as a garbled string.
  - Bridge outlives KVM off and continues to hold an SSH session.
  - File send silently overwrites a differing pre-existing remote file.
  - File get places a partial file at the final destination path.
  - Route selection prefers Tailscale over an available LAN, wasting the DERP path.
  - Route selection treats LAN as reachable when the resolved interface is a utun tunnel.
  - Installer edits Lan Mouse or Sunshine configuration.
  - Installer directly opens or writes anything under ~/.ssh/, mutates authorized_keys, or exposes a private key path.
  - Doctor reports OK against a broken state.
  - Bridge writes clipboard bytes to any log or cache.
  - Overrides DIARCHY_SSH_ALIAS/DIARCHY_LAN_HOST/DIARCHY_TAILSCALE_HOST/DIARCHY_SSH_PORT are ignored, silently truncated, or persisted anywhere.
```

## 12. Repository shape after this change

This section inventories feature-owned artifacts. The same branch also carries repository-bootstrap prerequisites (`.gitignore`, agent-harness files, and artifact-gate `.gitkeep` placeholders); those harness files are not runtime deliverables of clipboard/file transfer.

Added:

- `host/install-clipboard-file-transfer.sh`
- `host/diarchy-clipboard-remote`
- `host/diarchy-file-transfer-host-doctor`
- `client/install-clipboard-file-transfer.sh`
- `client/diarchy-clipboard-bridge`
- `client/diarchy-file`
- `client/diarchy-route.sh`
- `client/diarchy-clipboard-file-transfer-client-doctor`
- `client/Diarchy Drop.applescript`
- `client/Diarchy File Portal.swift`
- `client/diarchy-file-portal-send`
- `client/diarchy-file-portal-receiver`
- `host/diarchy-file-portal`
- `host/diarchy-file-portal-remote`
- `host/diarchy-file-portal-ingest`
- `host/systemd/diarchy-file-portal.service`
- `tests/test_clipboard_bridge.py`
- `tests/test_file_transfer.py`
- `tests/test_route_selection.py`
- `tests/test_clipboard_file_transfer_install.py`
- `tests/test_diarchy_kvm_hook.py`
- `tests/test_mac_file_portal.py`
- `tests/test_file_portal_send.py`
- `tests/test_file_portal_receiver.py`
- `tests/test_file_portal_protocol.py`
- `tests/test_host_file_portal.py`
- `evals/config.json`
- `evals/cases/clipboard-artifacts.json`
- `evals/cases/file-transfer-artifacts.json`
- `evals/cases/integration-artifacts.json`
- `evals/cases/edge-file-portal-artifacts.json`
- `evals/e2e/physical-mac-diarchy-20260730.md`
- `evals/runs/run-smoke-*.json` and matching `evals/reports/run-smoke-*.md` branch evidence
- `docs/superpowers/plans/2026-07-29-clipboard-file-transfer.md` (created by the implementer during planning; not by this spec task).

Modified:

- `client/diarchy-kvm` — mandatory transactional lifecycle integration for the clipboard bridge and both file portals (§3.3).

Neither the KVM installer nor the Sunshine installer is edited. Existing `.service`, `sunshine.conf`, `apps.json`, and Lan Mouse configuration artifacts are not modified; the feature adds only its owned `host/systemd/diarchy-file-portal.service`. Project code never directly opens, writes, names, or relocates files under `~/.ssh/`; only the invoked `/usr/bin/ssh` process may read OpenSSH-managed configuration and keys.

## 13. Verification summary

The feature is delivered when, in order:

1. §10.1 tests pass under `python3 -m unittest discover -s tests -v`.
2. §10.2 artifact-gate cases pass with a current branch report path, all declared critical targets covered, `critical regressions=0`, and sanitized privacy status.
3. Every §10.3 positive and negative row is observed on the paired physical Mac + host, or explicitly reported INCONCLUSIVE with its environmental blocker under §10.4.
4. §10.4 validator returns PASS; reviewer signs off.
5. §9.14 byte-identity snapshot of KVM and streaming state holds before/after installation and after one full KVM on/off cycle.
