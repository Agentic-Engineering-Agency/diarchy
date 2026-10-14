# Host Local KVM Design

**Date:** 2026-07-22
**Status:** Approved

## Goal

Use the Mac's keyboard and mouse to operate the host's existing physical Hyprland display as if it were a monitor positioned to the left of the Mac. Applications continue to execute and render on the host. Crossing the Mac's left screen edge transfers input; crossing back returns input to macOS.

This replaces local Sunshine/Moonlight streaming with a software KVM. SSH remains the remote administration path. No video or audio is transported for the local workflow.

## Selected approach

Use Lan Mouse 0.11.0 on both machines:

- Mac: input source and controller.
- Host: input receiver on Hyprland/wlroots.
- Layout: Host is the `left` client of the Mac.
- Transport: direct LAN and Tailscale addresses are both pinned. Lan Mouse 0.11.0 races configured candidates, so the first authenticated connection wins rather than respecting list priority. The verified runtime currently uses a direct Tailscale peer because UFW blocks the LAN source on UDP 4242.
- Security: DTLS plus explicit certificate-fingerprint authorization. No unauthenticated peer is accepted.
- Emergency release chord: `A+S+D+F` using Lan Mouse's `release_bind`.

Lan Mouse is selected over Deskflow because Lan Mouse explicitly supports macOS input capture and wlroots/Hyprland input emulation. Deskflow's documented Wayland client path depends on a RemoteDesktop portal that Hyprland does not currently provide.

## Installation

### Host

Install the upstream Linux x86_64 release rootlessly at `~/.local/opt/lan-mouse-0.11.0/lan-mouse`, with a stable launcher at `~/.local/bin/lan-mouse`. The release is pinned to SHA-256 `c43d211ec1af3eaea0cd585ac5c415776000355c3fcb9742fa2513f32dab55ba`.

Install a systemd user unit bound to `graphical-session.target`. The unit starts `lan-mouse daemon`, restarts only on failure, and receives the live Hyprland/Wayland environment through the existing user manager.

Manage `~/.config/lan-mouse/config.toml` as mode 0600. The host configuration contains the Mac's authorized DTLS fingerprint and no outgoing client, so it only receives normal input. Its capture backend is explicitly `layer-shell`: Lan Mouse uses a local edge surface to detect the return transition without opening Hyprland's InputCapture portal.

### Mac

Install the upstream Apple Silicon bundle at `~/Applications/Lan Mouse.app`, pinned to SHA-256 `5ff9965d05be7f125b1d75b9e7259d874ec98aa086ce40012522bc25406500a9`. Verify the bundle identifier `de.feschber.LanMouse`, code signature, and executable version before replacing an owned installation. Remove only the release archive's quarantine attribute, as required by upstream.

Manage `~/.config/lan-mouse/config.toml` as mode 0600. It contains one active client at `left`, with direct-LAN and Tailscale addresses. Lan Mouse races both candidates and retains the first authenticated connection. It authorizes only the host fingerprint for return capture.

Install a `Diarchy KVM.app` toggle and `~/.local/bin/diarchy-kvm` command. The later clipboard/file-transfer installation extends this shared command into a complete readiness gate; `install-lan-mouse.sh` therefore directs the operator to run `install-clipboard-file-transfer.sh` before enabling the KVM. The final toggle has explicit `on`, `off`, `toggle`, and `status` states:

- `on`: requires and launches Lan Mouse, the clipboard bridge, the Mac file portal, and the file-portal receiver; verifies every owned process before reporting enabled.
- `off`: stops all owned portal/clipboard processes, asks Lan Mouse to quit, and confirms no owned process remains.
- `toggle`: derives the complete readiness state and performs the opposite action; a partial or missing component set fails closed.
- `status`: reports enabled/disabled/degraded plus clipboard and file-portal component state without mutation.

The app does not install a LaunchAgent. The KVM remains opt-in after login and consumes no resources while off. A Lan-Mouse-only installation is intentionally incomplete until the clipboard/file-transfer client installer supplies the mandatory portal components.

## Pairing and trust

Each installation exposes its own DTLS fingerprint through Lan Mouse. Pairing records the opposite machine's fingerprint in the managed configuration. Fingerprints are values, not secrets, but the configuration is still private because it defines trusted endpoints.

When supplied, the installer accepts a fingerprint only in Lan Mouse's 32-byte colon-separated SHA-256 form and rejects malformed or placeholder values before modifying configuration. Omitting the fingerprint is allowed only for the documented unpaired host bootstrap; doctors remain failing until pairing is completed.

## Network policy

Lan Mouse listens on UDP 4242. The desired firewall rule on the host is source-scoped to the Mac's LAN address `192.0.2.20`. The installer and doctor never weaken the firewall automatically. Privileged rule installation is currently unavailable and UFW blocks the direct-LAN candidate, so the observed connection uses the configured Tailscale address over a direct peer-to-peer path rather than DERP. The client doctor derives the active route from Lan Mouse's connected UDP socket; it does not treat a successful connectionless UDP probe as proof.

## Existing streaming stack

After the KVM path passes end-to-end validation, stop and disable the two Diarchy Sunshine user services. Preserve their files and Moonlight bundle for rollback; do not delete working state during the cutover.

## Failure behavior

- An unreachable host releases Mac input automatically through Lan Mouse liveness tracking.
- `A+S+D+F` forces release locally.
- `diarchy-kvm off` disables capture immediately.
- A malformed configuration, missing accessibility permission, inactive host daemon, untrusted fingerprint, or absence of a connected LAN/Tailscale peer fails closed and is reported by the doctor.
- The host uses Lan Mouse's `layer-shell` capture backend for the return edge. Automatic portal capture is prohibited: repeated portal-session creation crashed `xdg-desktop-portal-hyprland` during physical round-trip testing.
- The Mac reports the actual connected peer address and never infers an active route from UDP probe exit status.

## Verification

The implementation is complete only when all of the following are directly observed:

1. Pinned artifacts install idempotently and checksum verification rejects tampering.
2. Host daemon is active in the Hyprland session and listens on UDP 4242.
3. The client doctor identifies the actual connected LAN or Tailscale peer from Lan Mouse's UDP socket; the current fallback must also be proven direct peer-to-peer rather than DERP.
4. Moving left from the Mac transfers the pointer to the host physical display; moving right returns it.
5. Text, Shift, Control, Option, Command/Super-equivalent, arrows, click, double-click, right-click, and scroll reach a real host application.
6. Turning the toggle off prevents transfer and leaves no Lan Mouse process on the Mac.
7. Idle and active CPU, RSS, and observed input latency are measured on both machines.
8. Sunshine user services are disabled only after checks 1–7 pass.

## Non-goals

- Clipboard sharing: Lan Mouse 0.11.0 does not provide it.
- File transfer, audio, or video transport.
- Internet exposure of UDP 4242.
- Replacing SSH or deleting the previous Sunshine/Moonlight rollback path.
