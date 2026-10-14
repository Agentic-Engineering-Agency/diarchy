# Edge File Portal Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both edge file portals secure, atomic, lifecycle-correct, actionable, and ready for physical drag testing, with symmetric hidden edge triggers that reveal reliably even when a file drag starts before reaching the edge.

**Architecture:** Mac-to-host portal drops use a dedicated Python sender and receiver over a Mac-initiated SSH process. The Mac AppKit panel supplements tracking-area events with a global external-drag monitor, verifies the system drag pasteboard contains local file URLs, and uses an invisible 120-pixel activation band so it expands before Lan Mouse can switch hosts. The visible idle destination remains 3 pixels. The host GTK layer-shell portal mirrors the same 3/52/72-pixel state machine on the right edge, rejects overlapping drops, and resets stale interaction/transfer generations when its receiver socket disappears. Host installation explicitly restarts the portal service after publishing the executable so the live process always matches installed bytes.

**Tech Stack:** Bash 3.2, Python 3.11+, Swift/AppKit, GTK 3/PyGObject, Unix sockets, OpenSSH, systemd user services, `unittest`.

## Global Constraints

- All network connections originate on the Mac over the reviewed SSH route contract; no new TCP listener is allowed.
- Portal destinations are fixed at `~/Drop/from-mac/` and `~/Drop/from-host/`.
- Regular files and directories are accepted; symlinks and special files are rejected during the actual copy, not only before it.
- Existing destination basenames are never overwritten or merged by a portal transfer.
- Files publish atomically with mode `0600`; directories and descendants publish with private `0700`/`0600` modes.
- Portal processes exist only while `diarchy-kvm` is fully enabled.
- Both portals have a transparent 3-pixel trigger, reveal to 52 pixels, expand to 72 pixels during drag, and hide 500 milliseconds after cursor/drag exit.
- During a Mac file drag only, an invisible 120-pixel activation band reveals the 72-pixel destination before the pointer reaches Lan Mouse's edge-switch boundary.
- Existing explicit `diarchy-file send/get` behavior remains compatible.

---

### Task 1: Atomic Mac-to-host portal transport

**Files:**
- Create: `client/diarchy-file-portal-send`
- Create: `host/diarchy-file-portal-ingest`
- Modify: `client/install-clipboard-file-transfer.sh`
- Modify: `client/diarchy-clipboard-file-transfer-client-doctor`
- Modify: `host/install-clipboard-file-transfer.sh`
- Modify: `host/diarchy-file-transfer-host-doctor`
- Test: `tests/test_file_portal_send.py`
- Test: `tests/test_clipboard_file_transfer_install.py`

**Interfaces:**
- `diarchy-file-portal-send -- PATH...` resolves `diarchy-route.sh`, opens one SSH ingest process per top-level item, and emits one bounded status line per item plus one aggregate summary.
- The framed stream uses 4-byte big-endian JSON control records. `start` carries basename/type; `entry` carries a slash-separated relative path, kind, size, mode-independent private target type, and `mtime_ns`; regular-file bytes immediately follow their `entry` record; `finish` terminates the item.
- `diarchy-file-portal-ingest` reads one item from stdin, creates `~/Drop/from-mac/.diarchy-portal-stage-<uuid>`, and publishes with `renameat2(RENAME_NOREPLACE)` only after a complete validated stream.

- [ ] **Step 1: Add failing sender/ingest tests**

Cover a regular file, nested directory, Unicode/spaces, top-level collision, nested symlink, FIFO, source replacement during copy, truncated stream, and preserved mtime. Assert collision and every failure leave the existing destination hash/tree unchanged and remove the hidden stage.

- [ ] **Step 2: Run the transport tests red**

Run: `python3 -m unittest tests.test_file_portal_send`
Expected: failure because the sender and ingest commands do not exist.

- [ ] **Step 3: Implement fd-safe source traversal**

Use `os.open(..., O_NOFOLLOW)` plus `os.fstat`; traverse directories with directory FDs and `os.scandir(fd)`. Re-open every child with `dir_fd`, compare `(st_dev, st_ino)` from `lstat` and `fstat`, reject every non-regular/non-directory inode, stream exactly the declared size, and reject a file whose size or mtime changes while read.

- [ ] **Step 4: Implement private remote staging and no-replace publication**

Create files with `O_CREAT|O_EXCL|O_NOFOLLOW` mode `0600`, directories mode `0700`, validate relative components before every `openat`, apply original mtimes with `os.utime(..., follow_symlinks=False)`, and use `renameat2(..., RENAME_NOREPLACE)`. A `finally` block removes only the unique hidden stage on EOF, malformed input, collision, or signal.

- [ ] **Step 5: Wire argument-safe SSH and installers**

Resolve the route through the existing Bash contract without shell-joining user paths. Invoke `~/.local/bin/diarchy-file-portal-ingest` as the fixed remote command, capture bounded stdout/stderr, install both commands mode `0755`, and add doctor checks.

- [ ] **Step 6: Run focused transport and installer tests green**

Run: `python3 -m unittest tests.test_file_portal_send tests.test_clipboard_file_transfer_install`
Expected: all tests pass.

### Task 2: Explicit receiver readiness and strict KVM lifecycle

**Files:**
- Modify: `host/diarchy-file-portal-remote`
- Modify: `client/diarchy-file-portal-receiver`
- Modify: `client/diarchy-kvm`
- Test: `tests/test_file_portal_protocol.py`
- Test: `tests/test_file_portal_receiver.py`
- Test: `tests/test_diarchy_kvm_hook.py`

**Interfaces:**
- The SSH helper emits `{"type":"session","status":"ready"}` only after binding and chmodding the Unix socket.
- The receiver writes the private path in `DIARCHY_FILE_PORTAL_READY_FILE` only after validating that frame.
- KVM considers the receiver `on` only when its PID identity is exact and its ready marker exists.

- [ ] **Step 1: Add red tests for missing, crashed, slow, and spoofed components**

Assert `diarchy-kvm on` preflights the clipboard bridge, portal sender, receiver, and app before starting Lan Mouse; rolls back on every startup failure; rejects a PID command that merely contains the expected path; waits for the ready marker; and returns status 0 only when all four components are verified on.

- [ ] **Step 2: Add the session-ready frame and receiver marker**

Emit the frame after `bind`, `chmod(0600)`, and `listen`. In the receiver, require it before entering the ready/ack loop, create the marker with mode `0600`, and unlink it in `finally`.

- [ ] **Step 3: Make lifecycle preflights and failures mandatory**

Make `clipboard_start` return nonzero on absent/crashed startup. Make `turn_on` fail before mutation if any required component is absent, propagate every start failure, and roll back every component started by that invocation. Match only the exact executable command or the exact Python-interpreter-plus-script command.

- [ ] **Step 4: Run lifecycle tests green**

Run: `python3 -m unittest tests.test_file_portal_protocol tests.test_file_portal_receiver tests.test_diarchy_kvm_hook`
Expected: all tests pass.

### Task 3: Bounded local protocol and honest recovery

**Files:**
- Modify: `host/diarchy-file-portal`
- Modify: `host/diarchy-file-portal-remote`
- Modify: `client/diarchy-file`
- Test: `tests/test_host_file_portal.py`
- Test: `tests/test_file_portal_protocol.py`
- Test: `tests/test_file_transfer.py`

**Interfaces:**
- Local stage requests accept at most 32 items; each error message is at most 512 characters and every response is guaranteed below 65,536 encoded bytes.
- The 10-second timeout applies only to Unix-socket connect; the connected request waits for the helper's final staging response.
- Retained hidden user files are listable; only `.diarchy-portal-stage-*` and `.rsync-partial` internal entries are hidden.

- [ ] **Step 1: Add delayed-staging, maximum-item, oversized-error, dotfile, and disconnect tests**

Delay helper staging beyond the connect timeout and assert no false failure. Submit 33 items and assert rejection before side effects. Force long errors and assert a decodable bounded response. Assert `.report` is listed/gettable while internal staging names are not. Assert clean EOF with pending items triggers a bounded local notification.

- [ ] **Step 2: Separate connect timeout from response wait**

Connect with timeout 10 seconds, then call `connection.settimeout(None)` before writing/reading protocol frames.

- [ ] **Step 3: Bound requests and responses before staging**

Validate item count and path element types before any copy. Return basenames rather than full paths, truncate errors to 512 characters, and catch `ValueError` from response encoding to send a bounded terminal error without crashing the helper.

- [ ] **Step 4: Complete recovery behavior**

On EOF/protocol failure, notify once with the bounded count and basenames of retained pending entries. Update `diarchy-file list` to exclude only known internal names.

- [ ] **Step 5: Run protocol and recovery tests green**

Run: `python3 -m unittest tests.test_host_file_portal tests.test_file_portal_protocol tests.test_file_transfer`
Expected: all tests pass.

### Task 4: Reliable symmetric edge activation

**Files:**
- Modify: `client/Diarchy File Portal.swift`
- Modify: `host/diarchy-file-portal`
- Test: `tests/test_mac_file_portal.py`
- Test: `tests/test_host_file_portal.py`

**Interfaces:**
- Both portals use 3-pixel idle, 52-pixel hover, and 72-pixel drag widths with a 500-millisecond delayed collapse.
- `DropPortalView` installs one `NSEvent` global monitor for external `.leftMouseDragged` and `.leftMouseUp` events; a file-URL-only check against `NSPasteboard(name: .drag)` gates the 120-pixel drag-only screen band before it calls `reveal(expanded: true)`.
- `PortalWindow` tracks pointer, drag, sending, and transfer generations; it rejects drops while sending or unavailable, and socket loss resets state before hiding so reconnect can map only the 3-pixel trigger.

- [ ] **Step 1: Add failing source-contract tests**

Add these assertions to `tests/test_mac_file_portal.py`:

```python
self.assertIn("NSEvent.addGlobalMonitorForEvents", source)
self.assertIn("edgeDragActivationWidth: CGFloat = 120", source)
self.assertIn("NSEvent.removeMonitor", source)
self.assertIn("NSPasteboard(name: .drag)", source)
```

Add assertions to `tests/test_host_file_portal.py` for the 3/52/72/500 constants and callbacks, plus behavioral tests for the pure `portal_visual_mode` and `portal_accepts_drop` helpers: unavailable state must override stale pointer/drag/sending flags, and an active transfer must reject another drop.

- [ ] **Step 2: Run focused tests red**

Run: `python3 -m unittest tests.test_mac_file_portal tests.test_host_file_portal`
Expected: failures for the missing global drag detector, invisible activation band, and the host collapsed state.

- [ ] **Step 3: Implement Mac external-drag activation**

Install one global mouse-event monitor when the view enters a window for `.leftMouseDragged` and `.leftMouseUp`. Require the system drag pasteboard to advertise local file URLs before a candidate drag inside the invisible 120-pixel edge band can reveal the 72-pixel panel. Remove the monitor when the view leaves its window and in `deinit`. Keep the visible idle destination at 3 pixels; AppKit drag-destination callbacks remain authoritative for item-count validation and transfer.

- [ ] **Step 4: Implement the host mirror state machine**

Start at `3 × 180` with transparent background, no border, and a hidden label. Pointer entry reveals `52 × 180`; supported drag motion expands to `72 × 200`; pointer/drag exit schedules one generation-guarded `GLib.timeout_add(500, ...)`. Reject drops while a transfer is active or the socket is unavailable. Socket disappearance clears pointer/drag/sending state and invalidates pending transfer/report callbacks before hiding; socket appearance maps only the collapsed 3-pixel trigger.

- [ ] **Step 5: Typecheck and run focused tests green**

Run: `python3 -m unittest tests.test_mac_file_portal tests.test_host_file_portal`
Expected: all tests pass and `xcrun swiftc -typecheck` succeeds.

### Task 5: Actionable host per-item results

**Files:**
- Modify: `host/diarchy-file-portal`
- Modify: `host/diarchy-file-portal-remote`
- Test: `tests/test_host_file_portal.py`
- Test: `tests/test_file_portal_protocol.py`

**Interfaces:**
- Helper errors are `{path, error}` with bounded basename and detail.
- GTK notifications show the first failed basename/reason plus `(+N more)` and the queued/rejected aggregate.

- [ ] **Step 1: Add red mixed-result tests**

Submit one valid item, one collision, and one unsupported item. Assert the response and formatted notification identify each failure without exposing full paths or file contents.

- [ ] **Step 2: Implement bounded result formatting**

Preserve per-item results through the helper response and format a concise first-error detail in the GTK client while keeping the aggregate count.

- [ ] **Step 3: Run host GUI/protocol tests green**

Run: `python3 -m unittest tests.test_host_file_portal tests.test_file_portal_protocol`
Expected: all tests pass.

### Task 6: Transactional host installer and live-process refresh

**Files:**
- Modify: `host/install-clipboard-file-transfer.sh`
- Test: `tests/test_clipboard_file_transfer_install.py`

**Interfaces:**
- A private `mkdir` lock serializes installation.
- Staged files publish with same-filesystem `mv`; backups and the prior service enabled/active state are restored on any failure.
- Successful installation runs `systemctl --user enable diarchy-file-portal.service` followed by `systemctl --user restart diarchy-file-portal.service`, ensuring an already-active process loads the new executable.
- Rollback restarts the restored executable when the service was previously active; it stops the service when previously inactive.

- [ ] **Step 1: Add red service-refresh tests**

Update the fake `systemctl` to fail an injected portal `restart`. Assert a successful host install logs `enable` and `restart`, not `enable --now`. For restart failure, assert every managed file and mode is restored, the old executable is restarted when initially active, and the service is stopped when initially inactive.

- [ ] **Step 2: Run the installer test red**

Run: `python3 -m unittest tests.test_clipboard_file_transfer_install.ClipboardFileTransferInstallTests.test_installed_scripts_have_exact_reviewed_bytes_and_modes`
Expected: failure because the installer still calls `enable --now` and never restarts.

- [ ] **Step 3: Implement restart-aware publication and rollback**

After `daemon-reload`, call:

```bash
"$SYSTEMCTL_BIN" --user enable diarchy-file-portal.service
"$SYSTEMCTL_BIN" --user restart diarchy-file-portal.service
```

During rollback, restore the old files and unit first, run `daemon-reload`, restore enabled/disabled state, then call `restart` when `service_was_active == 1` or `stop` otherwise.

- [ ] **Step 4: Run installer tests green**

Run: `python3 -m unittest tests.test_clipboard_file_transfer_install`
Expected: all tests pass, including publication and restart failure injection.

### Task 7: Full automated and physical verification

**Files:**
- Modify only if a verification failure identifies a source defect.

- [ ] **Step 1: Run the full automated suite**

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`
Expected: all tests pass.

Run: `python3 -m py_compile client/diarchy-file-portal-receiver client/diarchy-file-portal-send host/diarchy-file-portal host/diarchy-file-portal-remote host/diarchy-file-portal-ingest`
Expected: exit 0.

Run: `bash -n client/diarchy-file client/diarchy-kvm client/install-clipboard-file-transfer.sh host/install-clipboard-file-transfer.sh`
Expected: exit 0.

- [ ] **Step 2: Reinstall both machines and run doctors**

Install the staged host files over SSH, install the Mac client locally, and require both doctors to report zero failures/warnings.

- [ ] **Step 3: Verify both on-demand reveals visually**

With KVM on, capture both desktops and confirm neither shows a labeled tab at rest. Move the cursor into the Mac's leftmost 3 pixels and the host's rightmost 3 pixels; confirm each 52-pixel panel appears and hides after at least 500 milliseconds away. Start a real file drag on the Mac and confirm its panel expands within the invisible 120-pixel drag band before Lan Mouse switches hosts; confirm a the host drag expands its 72-pixel destination before release.

- [ ] **Step 4: Smoke both transfer directions and collisions**

Send a unique file and nested directory each way, compare SHA-256 hashes and tree shape, then repeat a colliding basename and assert the existing destination is unchanged. Remove only these uniquely named smoke artifacts.

- [ ] **Step 5: Verify disabled-at-rest cleanup**

Run `diarchy-kvm off`; assert status reports disabled, the Mac app/receiver/clipboard/Lan Mouse processes are absent, and the host Unix socket is absent while its hidden graphical service remains active.

### Task 8: Final review and commits

**Files:**
- Review all changed portal, lifecycle, installer, and test files.

- [ ] **Step 1: Run final engineering review**

Require no open correctness, security, lifecycle, installer rollback, or physical-readiness findings.

- [ ] **Step 2: Commit atomic implementation slices**

Commit secure transport, lifecycle/protocol hardening, on-demand UI, installer transaction, and verification fixes separately. Do not push without explicit approval.
