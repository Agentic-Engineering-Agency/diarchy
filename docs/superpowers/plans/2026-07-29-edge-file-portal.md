# Bidirectional Edge File Portal Implementation Plan

> **Superseded UI geometry:** The hidden-edge behavior in `2026-07-29-edge-file-portal-hardening.md` replaces this plan's original always-visible 52 px tabs. Keep this document as the implementation history for the transfer pipeline; use the hardening plan for current portal geometry and activation behavior.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add KVM-scoped edge drop targets that copy files and directories from Mac to the host and from the host to Mac using the existing authenticated SSH/rsync transport.

**Architecture:** A native AppKit drop tab sends Mac drops through `diarchy-file send`. A GTK layer-shell tab on the host forwards local drop paths to a session-scoped Unix socket owned by a remote helper. A Mac receiver owns the SSH helper session, receives staged-item records, pulls them through `diarchy-file get`, and acknowledges each result.

**Tech Stack:** Bash, Python 3.11+, Swift/AppKit, GTK 3/PyGObject, GtkLayerShell 0.1, systemd user services, OpenSSH, rsync, unittest.

## Global Constraints

- All network connections originate on the Mac; do not enable a listener on the Mac or add a network port.
- Portals exist only while `diarchy-kvm` is enabled.
- Mac portal is on the left edge; host portal is on the right edge.
- Receive folders remain `~/Drop/from-mac/` and `~/Drop/from-host/`.
- Accept regular files and directories; reject symlinks and special files.
- Never overwrite a destination through a portal.
- Preserve strict SSH host-key verification and the shared route contract.
- No shell interpolation of paths, no persisted file bodies, and no content-bearing logs.
- Installers remain rootless and idempotent.

---

### Task 1: Shared framed protocol and the host staging helper

**Files:**
- Create: `host/diarchy-file-portal-remote`
- Create: `tests/test_file_portal_protocol.py`

**Interfaces:**
- Consumes: `$XDG_RUNTIME_DIR`, `~/Drop/to-mac/`, local absolute paths from the host portal.
- Produces: `encode_record(record: dict) -> bytes`, `decode_record(stream: BinaryIO) -> dict | None`, `validate_drop_path(path: pathlib.Path) -> tuple[str, str]`, socket `$XDG_RUNTIME_DIR/diarchy-file-portal.sock`, SSH records `{type:"ready", id, name, kind, size, mtime}`, and acknowledgement records `{type:"ack", id, status, error?}`.

- [ ] **Step 1: Add failing protocol and validation tests**

Cover round trips, split reads, 64 KiB rejection, malformed JSON, unknown message types, absolute-path enforcement, `.`/`..`, symlinks, FIFOs, Unicode basenames, regular files, and directories. Import the script through `runpy.run_path` so tests execute real functions.

- [ ] **Step 2: Run the protocol tests and confirm RED**

Run: `python3 -m unittest tests.test_file_portal_protocol`
Expected: import/file-not-found failures for `host/diarchy-file-portal-remote`.

- [ ] **Step 3: Implement framing and path validation**

Use a 4-byte network-order length and compact UTF-8 JSON. Set `MAX_RECORD_BYTES = 65536`. `decode_record` must read exactly the declared body, reject zero/oversized lengths, require a JSON object, and validate `type` against the caller's allowed set.

- [ ] **Step 4: Add failing atomic staging and acknowledgement tests**

Use temporary HOME/runtime directories and a socket client. Assert no-overwrite publication, hidden staging cleanup, directory-tree preservation, 0600/0700 destination modes, successful ACK deletion of the staged outbox copy, failed ACK retention, duplicate socket refusal, and socket cleanup on exit.

- [ ] **Step 5: Implement the remote helper loop**

Bind the user-only Unix socket, accept length-framed `{type:"stage", paths:[...]}` requests, copy each item to a hidden same-filesystem stage with `shutil.copy2`/`copytree`, normalize private modes, atomically rename, emit `ready` on stdout, and consume `ack` from stdin. Do not remove the original dropped path.

- [ ] **Step 6: Run Task 1 tests and confirm GREEN**

Run: `python3 -m unittest tests.test_file_portal_protocol`
Expected: all tests pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add host/diarchy-file-portal-remote tests/test_file_portal_protocol.py
git commit -m "feat: add host file portal transport"
```

### Task 2: Host GTK edge portal

**Files:**
- Create: `host/diarchy-file-portal`
- Create: `host/systemd/diarchy-file-portal.service`
- Create: `tests/test_host_file_portal.py`

**Interfaces:**
- Consumes: `text/uri-list` GTK drops and `$XDG_RUNTIME_DIR/diarchy-file-portal.sock`.
- Produces: `parse_uri_list(raw: bytes) -> list[pathlib.Path]`, `send_stage_request(paths: list[pathlib.Path], socket_path: pathlib.Path) -> dict`, and a right-edge GTK layer-shell tab visible only while the receiver socket exists.

- [ ] **Step 1: Add failing URI and socket tests**

Cover percent-decoded `file://` URIs, CRLF/comment handling, non-file URI rejection, multiple paths, socket unavailable errors, exact stage request shape, and helper error propagation.

- [ ] **Step 2: Run host portal tests and confirm RED**

Run: `python3 -m unittest tests.test_host_file_portal`
Expected: import/file-not-found failures.

- [ ] **Step 3: Implement pure parsing and socket client functions**

Keep imports of `gi`, `Gtk`, and `GtkLayerShell` inside `run_gui()` so pure tests run headlessly. Reuse the framing functions by loading the installed remote helper path or a small duplicated framing codec with byte-identical tests; do not import across client/host executable scripts through `sys.path` mutation.

- [ ] **Step 4: Implement the GTK layer-shell tab**

Create a 52 px by 180 px top-layer window anchored right and vertically centered with exclusive zone 0 and keyboard interactivity disabled. Register `text/uri-list`, highlight on `drag-motion`, restore on `drag-leave`, and submit paths on `drag-data-received`. Hide/unmap when the socket is absent and show when present. Display concise status text and use `notify-send` for completion/failure.

- [ ] **Step 5: Add and validate the graphical-session unit**

Use `After=graphical-session.target`, `PartOf=graphical-session.target`, `ExecStart=%h/.local/bin/diarchy-file-portal`, and `Restart=on-failure`. The process may run throughout the graphical session but the surface must be absent without the receiver socket.

- [ ] **Step 6: Run Task 2 tests and syntax checks**

Run: `python3 -m unittest tests.test_host_file_portal && python3 -m py_compile host/diarchy-file-portal host/diarchy-file-portal-remote`
Expected: all pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add host/diarchy-file-portal host/systemd/diarchy-file-portal.service tests/test_host_file_portal.py
git commit -m "feat: add host edge drop portal"
```

### Task 3: Mac receiver and directory-safe pull

**Files:**
- Create: `client/diarchy-file-portal-receiver`
- Create: `tests/test_file_portal_receiver.py`
- Modify: `client/diarchy-file`
- Modify: `tests/test_file_transfer.py`

**Interfaces:**
- Consumes: shared `diarchy_ssh_options`, remote `ready` records, and `diarchy-file get --portal-request-id <id> <basename>` internally.
- Produces: `receiver_session(ssh_argv, get_command, stdin, stdout) -> int`, acknowledgements for every ready record, files/directories atomically published under `~/Drop/from-host/`, and no-overwrite status.

- [ ] **Step 1: Add failing receiver record tests**

Drive a fake remote stream and fake get executable. Assert one invocation per ready record, argument-safe Unicode names, OK/failed ACKs, unknown record termination, duplicate request-ID rejection, and failure of all pending records when the stream closes.

- [ ] **Step 2: Run receiver tests and confirm RED**

Run: `python3 -m unittest tests.test_file_portal_receiver`
Expected: import/file-not-found failures.

- [ ] **Step 3: Implement the receiver process**

Source `diarchy-route.sh` in the executable wrapper, select `auto`, and exec embedded Python with the exact SSH argv plus `diarchy-file`. Python starts `ssh ... diarchy-file-portal-remote`, decodes records, invokes `diarchy-file get <basename>`, writes ACK records, forwards only concise diagnostics to stderr, and terminates the SSH child cleanly on SIGTERM.

- [ ] **Step 4: Add failing directory-get tests**

Extend the rsync test fixture with a remote directory. Assert directory trees publish atomically, destination mode is 0700, nested regular files are 0600, symlinks are rejected, collisions remain unchanged, and file behavior is unchanged.

- [ ] **Step 5: Extend `diarchy-file get` for safe directories**

Accept one basename, rsync into the existing private stage directory, inspect the staged leaf with `lstat`, reject symlinks/special files, normalize recursively without following symlinks, and publish via same-filesystem rename/link without overwrite. Preserve current output fields and aggregate exit behavior.

- [ ] **Step 6: Run Task 3 tests and confirm GREEN**

Run: `python3 -m unittest tests.test_file_portal_receiver tests.test_file_transfer`
Expected: all pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add client/diarchy-file-portal-receiver client/diarchy-file tests/test_file_portal_receiver.py tests/test_file_transfer.py
git commit -m "feat: receive host edge drops on Mac"
```

### Task 4: Native Mac edge portal

**Files:**
- Create: `client/Diarchy File Portal.swift`
- Create: `tests/test_mac_file_portal.py`

**Interfaces:**
- Consumes: AppKit file-URL drags and `~/.local/bin/diarchy-file`.
- Produces: accessory app `~/Applications/Diarchy File Portal.app`, left-edge non-activating panel, and one argument-safe `diarchy-file send -- <paths...>` child per drop.

- [ ] **Step 1: Add failing source-contract and harness tests**

Assert the Swift source registers `.fileURL`, reads URLs with `readObjects(forClasses:options:)`, uses `Process.executableURL` plus `arguments`, includes `--`, rejects symlinks/special files before launch, places the panel on `NSScreen.main.visibleFrame.minX`, and never invokes `/bin/sh`.

- [ ] **Step 2: Run Mac portal tests and confirm RED**

Run: `python3 -m unittest tests.test_mac_file_portal`
Expected: missing Swift source failures.

- [ ] **Step 3: Implement the AppKit portal**

Use `NSApplication` accessory activation policy, a borderless non-activating `NSPanel`, a custom `NSView` implementing `NSDraggingDestination`, and a vertical label. Keep a 52 px by 180 px tab centered on the left edge. Expand/highlight during a valid drag, invoke `diarchy-file` off the main thread, collect bounded stderr, and update the UI/notification on the main thread.

- [ ] **Step 4: Compile and smoke-launch a staged app**

Run `swiftc -O -framework AppKit -o <stage>/Contents/MacOS/diarchy-file-portal 'client/Diarchy File Portal.swift'`, create the minimal Info.plist with `LSUIElement=true` and bundle ID `dev.sebastiangmz.diarchy-file-portal`, ad-hoc sign, launch, verify its panel exists, then terminate it.

- [ ] **Step 5: Run Task 4 tests and Swift typecheck**

Run: `python3 -m unittest tests.test_mac_file_portal && xcrun swiftc -typecheck -framework AppKit 'client/Diarchy File Portal.swift'`
Expected: all pass without warnings promoted to errors.

- [ ] **Step 6: Commit Task 4**

```bash
git add 'client/Diarchy File Portal.swift' tests/test_mac_file_portal.py
git commit -m "feat: add native Mac edge drop portal"
```

### Task 5: Installers, doctors, and KVM lifecycle

**Files:**
- Modify: `client/install-clipboard-file-transfer.sh`
- Modify: `host/install-clipboard-file-transfer.sh`
- Modify: `client/diarchy-clipboard-file-transfer-client-doctor`
- Modify: `host/diarchy-file-transfer-host-doctor`
- Modify: `client/diarchy-kvm`
- Modify: `tests/test_clipboard_file_transfer_install.py`
- Modify: `tests/test_diarchy_kvm_hook.py`

**Interfaces:**
- Consumes: components from Tasks 1–4.
- Produces: rootless installations, reviewed route digest, verified portal process identities, transactional `diarchy-kvm on/off`, and status text `file portals: on|off`.

- [ ] **Step 1: Add failing installer tests**

Assert exact executable modes, app bundle ID and `LSUIElement`, systemd unit installation, dependency failures before mutation, idempotent second-run tree hashes, no sudo, and no writes to SSH configuration.

- [ ] **Step 2: Add failing KVM lifecycle tests**

Cover successful startup of Lan Mouse, clipboard bridge, Mac portal, and receiver; rollback at each failure point; verified PID ownership; idempotent on/off; stale PID cleanup; off ordering; and status requiring every component.

- [ ] **Step 3: Run lifecycle/install tests and confirm RED**

Run: `python3 -m unittest tests.test_clipboard_file_transfer_install tests.test_diarchy_kvm_hook`
Expected: failures for missing installation/lifecycle behavior.

- [ ] **Step 4: Update both installers and doctors**

Compile/sign the Mac app transactionally, install all scripts with mode 0755, install/enable the host user unit, verify the real GTK/LayerShell imports, update reviewed digests, and preserve rollback/idempotence patterns already used by the scripts.

- [ ] **Step 5: Integrate portal processes into `diarchy-kvm`**

Add runtime directories and PID files for Mac portal and receiver. Start them after clipboard bridge health succeeds; verify command identities before declaring enabled. Stop receiver then Mac portal during off. Ensure any startup failure stops all components started in the current transaction.

- [ ] **Step 6: Run Task 5 tests and installed doctors**

Run: `python3 -m unittest tests.test_clipboard_file_transfer_install tests.test_diarchy_kvm_hook`
Expected: all pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add client/install-clipboard-file-transfer.sh host/install-clipboard-file-transfer.sh client/diarchy-clipboard-file-transfer-client-doctor host/diarchy-file-transfer-host-doctor client/diarchy-kvm tests/test_clipboard_file_transfer_install.py tests/test_diarchy_kvm_hook.py
git commit -m "feat: integrate file portals with Diarchy KVM"
```

### Task 6: End-to-end installation and physical acceptance

**Files:**
- Modify only when a verified defect is found in Tasks 1–5.

**Interfaces:**
- Consumes: completed implementation and real Mac/host pair.
- Produces: installed, disabled-at-rest portals ready for the user's physical drag tests.

- [ ] **Step 1: Run the complete automated suite**

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`
Expected: all tests pass.

- [ ] **Step 2: Install host components**

Run the host installer over the existing authenticated SSH path, reload the user manager, and run `diarchy-file-transfer-host-doctor`. Expected: all checks `[ok]` and portal hidden while KVM is off.

- [ ] **Step 3: Install Mac components**

Run the client installer and `diarchy-clipboard-file-transfer-client-doctor`. Expected: all checks `[ok]`, correctly signed app bundle, and no portal process while KVM is off.

- [ ] **Step 4: Smoke-test KVM lifecycle without transferring user files**

Enable KVM, verify both portal processes, host socket, and visible edge tabs; verify direct LAN route; disable KVM and prove all portal/bridge/Lan Mouse processes and socket are absent.

- [ ] **Step 5: Review final patch**

Request code review focused on path safety, lifecycle rollback, protocol framing, no-overwrite behavior, and macOS/Wayland GUI correctness. Fix every critical or important finding and rerun affected tests.

- [ ] **Step 6: Leave the system ready for user testing**

Install the reviewed build, leave `diarchy-kvm` off, keep PR draft, and report exact commands plus the physical drag sequence. Do not claim bidirectional drag acceptance until the user performs it on both desktops.
