# Bidirectional Edge File Portal Design

## 1. Goal

Make file transfer feel like part of the existing Diarchy KVM session. While `diarchy-kvm` is enabled, each desktop exposes a small drop target on the screen edge facing the other computer:

- Mac: left-edge target sends dropped items to the host.
- Host: right-edge target sends dropped items to the Mac.

A drop copies files or directories to the receiver's fixed inbox. It does not delete the source and does not simulate a native drag session across operating systems.

## 2. User experience

`diarchy-kvm on` starts the Mac portal and a Mac-initiated SSH receiver session. The receiver session makes the host portal visible. `diarchy-kvm off` removes both portals and terminates their transport processes.

Both portals normally expose only a transparent 3-pixel activation strip on the screen edge facing the other computer: left on the Mac, right on the host. Ordinary cursor hover must touch that strip. While a file drag is active, the Mac additionally watches an invisible 120-pixel edge band so it can reveal the destination before Lan Mouse transfers the pointer away from macOS. Revealing the 52-pixel tab does not activate the application, steal keyboard focus, or reserve workspace area.

Each revealed tab remains visible while the cursor or drag is over it and hides 500 milliseconds after leaving, unless a drag or transfer is still active. A supported drag expands and highlights the tab to 72 pixels. Releasing one or more items starts a copy and immediately reports `Sending N items`. Completion reports success or the final actionable error. No file body or clipboard content is logged.

Fixed destinations remain the existing private folders:

- Mac to the host: `~/Drop/from-mac/`
- Host to Mac: `~/Drop/from-host/`

The portal accepts regular files and directories. Symlinks and special files are rejected. Existing destination names are never overwritten by the portal; the user must remove or rename the collision first. The existing explicit CLI `--overwrite` behavior remains unchanged.

## 3. Components

### 3.1 Mac edge portal

The portal uses an AppKit tracking area for ordinary hover. Because AppKit does not reliably deliver tracking-area entry while Finder owns an external drag, a global mouse-event monitor observes external left-drag and left-button-up events without an event tap. Before revealing, it checks the system drag pasteboard for local file URLs, so ordinary text and UI drags do not activate the destination. Entering the invisible 120-pixel activation band during a candidate file drag expands the existing panel to 72 pixels before the 3-pixel physical destination or Lan Mouse boundary is reached; AppKit's normal drag-destination callbacks then validate and receive the file URLs. Global mouse monitoring requires no Accessibility permission.

The application displays transfer state inside the panel and uses macOS notifications for completion. It never constructs a shell command. For each accepted drop, the AppKit process starts security-scoped access while it copies the selected items into a user-private `mkdtemp` directory. The Python child receives only those staged paths, avoiding macOS TCC denial for user-selected files under protected folders such as `~/Documents`. Security-scoped access ends after the copy, symlinks remain symlinks for the sender's no-follow validation, and the temporary tree is removed after transfer or staging failure.

Staged paths are passed as process arguments to the portal-specific transfer command. The command retains its stable-descriptor traversal, unsupported-type rejection, route selection, SSH identity, and collision behavior.

This path-based bridge is private from other Unix users but does not preserve TCC isolation from a malicious process already running as the same macOS user. A crash or forced kill can also leave a staged tree until macOS clears that user's temporary directory. Eliminating both residual risks requires a future descriptor- or framed-stream boundary in which AppKit reads the security-scoped source directly into the sender pipe; normal completion and handled staging errors remove the temporary tree immediately.

### 3.2 host edge portal

A Python/PyGObject GTK 3 application creates a right-edge layer-shell surface under Hyprland. `GtkLayerShell` anchors the window without reserving workspace area. The surface mirrors the Mac state machine: a transparent 3-pixel trigger expands to 52 pixels on pointer entry and 72 pixels for a supported `text/uri-list` drag, then collapses 500 milliseconds after pointer and drag exit unless a transfer remains active. It rejects additional drops while a transfer is active.

The portal is installed as a graphical-session user service but remains hidden unless the per-session receiver socket exists. Socket loss clears pointer, drag, transfer, style, and delayed-callback generations before hiding; stale asynchronous transfer completions cannot resurrect the tab after reconnect. Dropped paths are sent to that socket using a length-delimited local protocol. The portal never opens a network connection.

### 3.3 Mac receiver

A rootless Python process starts with the KVM on the Mac. It uses the shared LAN/Tailscale route contract to open one SSH subprocess running `diarchy-file-portal-remote` on the host. It reads ready records from the remote helper and pulls each staged basename with the existing `diarchy-file get` transport. Successful items land atomically under `~/Drop/from-host/` with mode `0600` for files and private permissions for directories.

The receiver returns an acknowledgement for every record so the host can report success or failure. A connection loss fails pending records; reconnecting the KVM creates a fresh session rather than replaying unknown work.

### 3.4 host remote helper

`diarchy-file-portal-remote` runs only as the child of the Mac-initiated SSH session. It creates `$XDG_RUNTIME_DIR/diarchy-file-portal.sock` with user-only permissions and removes it on exit.

The helper validates each local request, copies accepted items atomically into `~/Drop/to-mac/`, and emits one ready record containing an opaque request ID, basename, type, size, and modification time. It retains the staged item until the Mac acknowledges a successful pull. On failure it leaves the staged item available for manual recovery through `diarchy-file list/get` and reports the error locally.

Only one helper may own the socket. A second receiver fails closed instead of replacing a live session.

## 4. Protocols and safety

The local host portal-to-helper socket uses a 4-byte big-endian length followed by UTF-8 JSON. Requests contain an operation and an array of absolute local paths. The helper limits each record to 64 KiB, rejects malformed JSON, relative paths, missing paths, symlinks, special files, empty basenames, `.`/`..`, and items whose basename contains `/`.

The SSH helper-to-Mac stream uses the same length-prefixed JSON envelope. Messages are typed as `ready`, `ack`, or `error`; unknown types and malformed lengths terminate the session. File bytes never travel through JSON. They continue to use rsync over the separately authenticated SSH invocation created by `diarchy-file`.

All SSH connections retain strict host-key verification and the canonical `HostKeyAlias` route contract. Neither machine opens a new listening network port. The only new listener is the host Unix socket under the logged-in user's runtime directory.

## 5. Lifecycle

`diarchy-kvm on` succeeds only when Lan Mouse, the clipboard bridge, the Mac portal, and the receiver are all running with verified process identities. Partial startup rolls back every component started by that invocation.

`diarchy-kvm off` stops the Mac portal and receiver before stopping the clipboard bridge and Lan Mouse. SSH exit removes the host socket; the host portal observes its disappearance and hides. Stale PID files or sockets are removed only after their owning process is proven absent.

`diarchy-kvm status` reports portal state explicitly:

```text
Diarchy KVM: enabled (clipboard bridge: on, file portals: on)
```

A portal failure does not silently degrade to an enabled state.

## 6. Installation

The client installer compiles and ad-hoc signs `Diarchy File Portal.app`, installs the Mac receiver under `~/.local/bin`, and updates the reviewed lifecycle integration. It remains rootless and idempotent.

The host installer installs the GTK portal, remote helper, and a graphical-session systemd user unit. It verifies Python 3, PyGObject GTK 3, GtkLayerShell 0.1, `rsync`, and `notify-send` before changing managed files. After publishing a changed executable, it explicitly restarts an already-active portal service so the running process cannot remain stale; rollback restores the prior unit and process state. It remains rootless and idempotent.

The existing `Diarchy Drop.app` and manual `diarchy-file send/get/list` commands remain available as explicit fallbacks.

## 7. Error handling

- Unreachable host: Mac portal rejects the drop with the existing route error.
- Receiver absent: Host portal stays hidden; a race at drop time produces `Mac receiver unavailable`.
- Name collision: no overwrite; the source and existing destination remain unchanged.
- Partial copy: rsync staging remains hidden and never publishes a partial final item.
- SSH disconnect after host staging: staged data remains under `~/Drop/to-mac/` for manual recovery.
- Unsupported drag item: reject before any copy.
- Multi-item drop: process every item, report per-item status, and return an aggregate summary.

## 8. Verification

Automated tests cover:

1. Length framing, malformed records, maximum record size, and unknown message types.
2. Path validation including symlinks, special files, traversal, and unusual valid Unicode names.
3. Atomic file and directory staging, collision refusal, acknowledgement, and disconnect recovery.
4. Mac portal security-scoped staging for files and directories, symlink preservation for downstream rejection, temporary-tree cleanup, and argument-safe sender invocation.
5. GTK URI-list parsing and local socket requests.
6. KVM startup rollback, verified process identity, status, idempotent on/off, and stale-state cleanup.
7. Rootless, idempotent installers and read-only doctors.
8. Existing clipboard, manual transfer, route-selection, KVM, and streaming regressions.

Physical acceptance on the real machines requires:

1. Enable KVM and confirm both edges remain visually clear except for their transparent 3-pixel triggers.
2. Touch each trigger with the cursor and confirm its tab appears; leave it and confirm it hides after approximately 500 milliseconds.
3. Start a real file drag before entering each edge and confirm the corresponding tab expands without releasing the file. On the Mac, confirm activation occurs within the invisible 120-pixel drag band before Lan Mouse switches hosts; confirm ordinary cursor crossing remains unchanged outside a drag.
4. Drop multiple files and one directory on the Mac tab; verify matching hashes/tree on the host.
5. Drop multiple files and one directory on the host tab; verify matching hashes/tree on the Mac.
6. Repeat a colliding name and verify refusal without modification.
7. Disable KVM and confirm both tabs, socket, SSH child, clipboard bridge, and Lan Mouse processes are gone.
