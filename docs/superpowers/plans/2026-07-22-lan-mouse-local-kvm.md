# Lan Mouse Local KVM Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace local desktop streaming with an opt-in Lan Mouse software KVM that lets the Mac's keyboard and mouse cross onto the host's physical Hyprland display.

**Architecture:** Install pinned Lan Mouse 0.11.0 artifacts rootlessly on both machines. Run the host receiver as a graphical-session systemd user service, run the Mac sender only through an explicit toggle, and authorize both endpoints by DTLS certificate fingerprint. Preserve Sunshine/Moonlight as disabled rollback state.

**Tech Stack:** Bash, Python `unittest`, systemd user services, AppleScript application bundle, Lan Mouse 0.11.0, DTLS, Hyprland/wlroots, macOS Accessibility.

---

### Task 1: Lock the host installation contract

**Files:**
- Create: `tests/test_lan_mouse_host.py`
- Create: `host/install-lan-mouse.sh`
- Create: `host/systemd/diarchy-kvm.service`
- Create: `host/diarchy-kvm-host-doctor`

**Step 1: Write the failing tests**

Cover these observable contracts:

- `--dry-run` reports Lan Mouse 0.11.0, the Linux SHA-256, rootless destinations, UDP 4242, and no `sudo` mutation.
- A tampered release fails checksum verification before installation.
- Re-running the installer is idempotent.
- A foreign existing version directory is never overwritten.
- The generated systemd unit binds to `graphical-session.target`, runs `lan-mouse daemon`, and restarts only on failure.
- The generated config has mode 0600, validates the Mac fingerprint, contains no outgoing client, and selects the portal-free `layer-shell` capture backend.
- The doctor requires the exact version, current graphical-session service, UDP listener, certificate, and trusted Mac fingerprint.

**Step 2: Prove RED**

Run: `python3 -m unittest tests.test_lan_mouse_host -v`
Expected: FAIL because the host artifacts do not exist.

**Step 3: Implement the minimum host artifacts**

The installer must:

- Download the official `lan-mouse-linux-x86_64` release over HTTPS.
- Verify SHA-256 `c43d211ec1af3eaea0cd585ac5c415776000355c3fcb9742fa2513f32dab55ba`.
- Stage and atomically install under `~/.local/opt/lan-mouse-0.11.0`.
- Install the systemd user unit and doctor.
- Accept `--authorize-fingerprint <fingerprint>` and reject malformed values before mutation.
- Set `capture_backend = "layer-shell"` so the receiver detects the return edge without opening Hyprland's InputCapture portal.
- Preserve any non-managed config by timestamped backup.
- Reload, enable, and restart the service in the graphical user manager.
- Verify the binary version and live service before success.

**Step 4: Prove GREEN**

Run: `python3 -m unittest tests.test_lan_mouse_host -v`
Expected: PASS.

**Step 5: Commit**

Commit the test and implementation together as `feat: install Lan Mouse host receiver`.

### Task 2: Lock the Mac installation and toggle contract

**Files:**
- Create: `tests/test_lan_mouse_client.py`
- Create: `client/install-lan-mouse.sh`
- Create: `client/diarchy-kvm`
- Create: `client/diarchy-kvm-client-doctor`
- Create: `client/Diarchy KVM.applescript`

**Step 1: Write the failing tests**

Cover these observable contracts:

- `--dry-run` reports Lan Mouse 0.11.0, the Apple Silicon SHA-256, owned destinations, and no mutation.
- The installer rejects a foreign bundle and a malformed host fingerprint.
- A valid pinned bundle installs idempotently after signature, bundle-identifier, version, and checksum checks.
- The managed config places host on `left`, activates it at startup, pins both LAN and Tailscale candidates, and authorizes the host fingerprint.
- `status` is read-only.
- `on` launches only the owned bundle executable and verifies it remains alive.
- `off` quits only the exact bundle and verifies termination.
- `toggle` deterministically switches states.
- The toggle app invokes `~/.local/bin/diarchy-kvm toggle` and installs no LaunchAgent.
- The doctor checks version, signature, accessibility permission, configuration, fingerprint trust, process state, and the actual connected LAN/Tailscale UDP peer.

**Step 2: Prove RED**

Run: `python3 -m unittest tests.test_lan_mouse_client -v`
Expected: FAIL because the client artifacts do not exist.

**Step 3: Implement the minimum client artifacts**

The installer must:

- Download `lan-mouse-macos-arm64.zip` from the v0.11.0 release.
- Verify SHA-256 `5ff9965d05be7f125b1d75b9e7259d874ec98aa086ce40012522bc25406500a9`.
- Extract, validate `de.feschber.LanMouse`, verify code signature and version, remove quarantine, and atomically install to `~/Applications/Lan Mouse.app`.
- Accept `--authorize-fingerprint <fingerprint>` and use fixed host LAN/Tailscale addresses.
- Compile and ad-hoc sign `Diarchy KVM.app` with bundle identifier `dev.sebastiangmz.diarchy-kvm`.
- Install the toggle and doctor with mode 0755.
- Launch Lan Mouse once so macOS can request Accessibility permission and create its certificate.

**Step 4: Prove GREEN**

Run: `python3 -m unittest tests.test_lan_mouse_client -v`
Expected: PASS.

**Step 5: Commit**

Commit the test and implementation together as `feat: install Mac Lan Mouse toggle`.

### Task 3: Pair and install both endpoints

**Files:**
- Modify only generated user configuration outside the repository through the two installers.

**Step 1: Install the unpaired host receiver**

Copy the worktree's host artifacts to the host and run `host/install-lan-mouse.sh` without an authorization value to generate the host certificate and start the service.

**Step 2: Read and verify the host fingerprint**

Derive it from `~/.config/lan-mouse/lan-mouse.pem` using OpenSSL SHA-256. Do not parse logs or trust a copied value.

**Step 3: Install the Mac bundle with host trust**

Run `client/install-lan-mouse.sh --authorize-fingerprint <host-fingerprint>`. Use macOS System Settings only for the required Accessibility grant.

**Step 4: Read and verify the Mac fingerprint**

Derive it from `~/.config/lan-mouse/lan-mouse.pem` using OpenSSL SHA-256.

**Step 5: Reconcile host trust**

Re-run `host/install-lan-mouse.sh --authorize-fingerprint <mac-fingerprint>`, then verify the final configurations from both machines.

**Step 6: Verify network policy**

Confirm direct UDP 4242 reachability from Mac to `192.0.2.10`. If UFW blocks it, add only a source-scoped rule for `192.0.2.20` through an authorized privileged path. Never disable UFW or expose the port generally. If privilege remains unavailable, retain the Tailscale fallback and record the direct-LAN blocker.

### Task 4: Validate the real KVM path

**Files:**
- No repository edits.

**Step 1: Run both doctors**

Run the host doctor over SSH and the client doctor locally. Both must pass the installed binary, identity, trust, service/process, and network checks.

**Step 2: Validate pointer boundary transitions**

Start the KVM, move the pointer through the Mac's left edge, observe the host cursor move on the physical Hyprland display, then move through the host's right edge and verify return to macOS. Repeat at least ten round trips and exercise the emergency release chord.

**Step 3: Validate input semantics in a real app**

Open a host terminal and verify printable text, Shift, Control, Option/Alt, Command/Super-equivalent, arrow keys, click, double-click, right-click, vertical scroll, and horizontal scroll where supported. Confirm the resulting text or state over SSH.

**Step 4: Validate the off state**

Run `diarchy-kvm off`; verify the Mac process is gone and edge crossing no longer transfers input. Run `toggle` twice and verify deterministic off/on state.

**Step 5: Measure overhead and latency**

Capture idle and active CPU/RSS for Lan Mouse on both machines, network byte rate during input, and at least 30 input-transition latency samples. Report median and p95 without claiming display/video latency because no video is transported.

**Step 6: Disable the old local streaming services**

Only after all preceding KVM checks pass, disable and stop `diarchy-display.service` and `diarchy-sunshine.service`. Preserve their installed files and Moonlight for rollback.

### Task 5: Full verification and delivery

**Files:**
- Modify only if review finds a real defect.

**Step 1: Run all repository tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests pass.

**Step 2: Run shell syntax checks**

Run `bash -n` against every new shell artifact.
Expected: exit 0.

**Step 3: Review security and implementation quality**

Run the required multi-agent implementation review. Resolve every correctness, security, and goal-compliance failure; rerun affected tests.

**Step 4: Commit any bounded review fixes**

Use an atomic `fix:` commit with its tests.

**Step 5: Push and open a PR**

Use the repository push and GitHub mutation guards in dry-run mode first, then execute with approval. Include exact test, runtime, input, resource, latency, and rollback evidence.

**Step 6: Merge and verify canonical main**

After CI/review passes, merge the PR. Verify `origin/main` contains the merge and rerun both installed doctors plus one left-edge/right-edge transition on the final installed state.
