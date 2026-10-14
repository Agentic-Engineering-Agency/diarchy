# Diarchy — Design Specification

**Date:** 2026-07-22
**Status:** Approved for implementation

## 1. Goal

Provide a native-feeling, low-latency remote desktop from the maintainer's Mac to the existing Hyprland session on the always-on host laptop. The stream must carry video, keyboard, and mouse; work over the local network and through Tailscale; survive the host lid being open or closed; and add no custom process to the media path.

## 2. Measured environment

### Host

- MSI Cyborg 15, Arch Linux, Hyprland 0.56.0 on Wayland.
- Intel Core Ultra 7 155H, Intel Meteor Lake Arc iGPU, NVIDIA RTX 4060 Mobile 8 GiB.
- Physical display `eDP-1`, 1920×1080 at 144 Hz, scale 1.5.
- PipeWire 1.6.8 and WirePlumber 0.5.15.
- Intel VA-API H.264/HEVC/AV1 encode is available on `/dev/dri/renderD129`.
- NVIDIA NVENC H.264 smoke test encoded 1080p144 at 213 fps.
- LAN address `192.0.2.10`; Tailscale address `100.64.0.10`.

### Client

- MacBook Air (Apple Silicon), native VideoToolbox decode.
- Primary external display 1920×1080 at 75 Hz.
- LAN address `192.0.2.20`; Tailscale installed.

### Network constraint

Measured Wi-Fi throughput is approximately 112 Mbps in both directions, but the direct LAN path currently shows 10% ICMP loss and approximately 50 ms mean latency. This is enough bandwidth for 1080p streaming but not a reliable low-latency medium at the target quality. The implementation will detect and report this condition; a wired host uplink remains the highest-impact physical improvement.

## 3. Architecture

### Host media plane

Use the official pinned **Sunshine AppImage**, extracted rootlessly under `~/.local/opt` and invoked through a stable wrapper in `~/.local/bin`. The wrapper always selects the newest live Hyprland instance with a complete instance-signature/Wayland-socket pair and overwrites both environment variables together, preventing stale or mixed IPC state. The host has no passwordless package-management path, so this preserves deterministic installation without weakening privilege boundaries. Sunshine captures the existing Hyprland session, hardware-encodes it, accepts authenticated Moonlight clients, and injects keyboard and mouse through Linux input devices.

Use `capture = wlr` as the default capture backend. Sunshine documents that this backend captures wlroots compositors and virtual Hyprland outputs. It is therefore the only single backend that satisfies both the physical-display and closed-lid/headless requirements without an interactive portal prompt or a second compositor session.

Use NVIDIA NVENC as the encoder. The official AppImage's VA-API probe could not load the host's Arch `iHD` driver path, while the same Sunshine build proved H.264 and HEVC NVENC operational against wlroots capture. Pinning the measured working encoder avoids Sunshine's fallback search and keeps software encoding forbidden.

A shared service probe reads the reconciler's selected output, resolves Sunshine's exact current systemd `InvocationID`, and validates only that invocation's journal through a strict log checker. The checker requires exact end-anchored Wayland output and wlroots monitor-selection markers correlated to `eDP-1` or `DIARCHY`, plus H.264 and HEVC NVENC markers, and rejects reported frame-capture failures; generic backend text, prefix collisions, stale invocations, and unrelated monitor selections are not evidence of a working capture path.

Disable streamed audio in the initial profile because the requested contract is video, keyboard, and mouse. This avoids an unnecessary capture/mix path and host audio side effects.

### Client media plane

Use the signed native **Moonlight Qt 6.1.0** macOS application, pinned to the official universal DMG and an independently published SHA-256. Install Moonlight and the lightweight **Diarchy** launcher rootlessly under `~/Applications`, with commands under `~/.local/bin`; this avoids weakening privilege boundaries or requiring Homebrew. Moonlight provides VideoToolbox decode, direct mouse control, keyboard forwarding, and the GameStream protocol expected by Sunshine. The custom project supplies routing, launch, diagnostics, and the native launcher only; it does not wrap or proxy video frames.

### Control plane

A small host-side user service maintains a capturable output:

1. If `eDP-1` is present, it is the streamed output.
2. If no physical output is present, create one Hyprland headless output named `DIARCHY` at 1920×1080 and 75 Hz.
3. Keep the same Hyprland process, migrate every workspace left on the unavailable physical output, and focus the first workspace containing windows; never start a nested compositor or a second desktop session.
4. When `eDP-1` returns, migrate back and remove only the headless output owned by this project.
5. Reconcile state idempotently on service start and relevant Hyprland monitor events; recreate an owned headless output whose mode, position, or scale drifted from the contract.
6. Restart the running Sunshine user service only when the selected output changes, so capture follows the physical/headless transition.

The helper is control-only and sleeps on Hyprland's event socket; it does not poll continuously or touch the media stream.

## 4. Lifecycle

- Both systemd user units are enabled under `graphical-session.target`; Sunshine discovers the live Wayland environment through its wrapper instead of relying on manager-startup environment.
- The output reconciler restarts on an unmasked reconciliation, IPC, or Sunshine-restart failure.
- Moonlight is manually opened from a native macOS application named **Diarchy** or the `diarchy-stream` command.
- Closing Moonlight ends the stream but does not terminate or replace the host desktop.
- The host remains usable locally while the lid is open.
- Lid-close policy must not suspend the machine. The rootless installer verifies the effective systemd-logind policy and warns when the privileged host prerequisite is absent; it never writes system configuration through an implicit privilege escalation.

## 5. Routing and profiles

The macOS launcher selects a route before starting Moonlight:

1. Prefer `192.0.2.10` when the address has a local route, answers the bounded quality probe, and accepts a TCP connection on Sunshine's control port.
2. Otherwise use `100.64.0.10` through Tailscale only when the same service-aware checks pass.
3. Refuse to launch with a precise diagnostic if neither route can reach Sunshine; ICMP alone is insufficient because a host firewall may allow ping while blocking GameStream.

Profiles:

| Profile | Resolution | FPS | Initial bitrate | Codec policy | Intended use |
|---|---:|---:|---:|---|---|
| LAN healthy | 1920×1080 | 75 | 25 Mbps | HEVC preferred, H.264 fallback | Same 5 GHz LAN or wired host |
| Remote | 1920×1080 | 60 | 15 Mbps | HEVC preferred, H.264 fallback | Tailscale with stable path |
| Degraded | 1280×720 | 60 | 8 Mbps | H.264 | Packet loss or high jitter |

The launcher measures route availability, Sunshine control-port reachability, packet loss, and median latency with bounded probes, writes no persistent daemon or probe state, and reports the chosen route/profile to standard output. Moonlight's own adaptive transport remains authoritative during a session.

## 6. Security

- Pin Sunshine to release `v2026.516.143833` or a newer stable release containing its security fixes.
- Disable UPnP. Do not create router port forwards.
- Accept stream connections only from explicitly paired Moonlight clients.
- Bind the HTTPS configuration UI to local-host access policy; remote configuration is done through SSH port forwarding when needed.
- Require encryption for non-LAN/Tailscale paths while retaining the low-overhead LAN setting for the trusted subnet.
- Generate Sunshine UI credentials in memory, apply them through `sunshine --creds`, then atomically publish the permission-restricted credential state under `~/.config/sunshine`; never commit credentials, certificates, PINs, or Tailscale material.
- The project repository contains only templates and deterministic installers.

## 7. Failure handling

- Missing hardware encoder: fail installation unless the current Sunshine systemd `InvocationID` logs exact H.264 and HEVC NVENC selections; stale logs and alternate/software encoders never satisfy the probe.
- Missing `uinput` access: fail the input probe and identify the exact device/ACL rule.
- Missing Hyprland socket: systemd waits for the graphical target and the wrappers discover the live instance; a genuine discovery failure exits so systemd can retry.
- Headless creation or reconciliation failure: propagate the failed `hyprctl` call so the user service restarts; a later pass repairs a missing or incorrectly configured owned output.
- Network degradation: choose the degraded profile and print the measured reason; do not hide loss by increasing bitrate or buffering.
- LAN unavailable: transparently use the Tailscale address.
- Tailscale unavailable: retain LAN operation and report that remote access is unavailable.

## 8. Repository shape

The repository is intentionally small:

- `host/`: deterministic installer, Sunshine template, Hyprland output reconciler, systemd user units, and host doctor.
- `client/`: Moonlight installer/configurator, route/profile launcher, native `.app` wrapper generator, and client doctor.
- `tests/`: behavior tests for route/profile selection and idempotent output reconciliation.
- `docs/superpowers/`: this design and its implementation plan.

No framework, package manager runtime, database, Docker image, background client service, or custom media protocol is introduced.

## 9. Verification contract

The implementation is complete only when all of the following are observed:

1. Sunshine runs under the host user session after login; a shared service probe binds the reconciler's selected output to Sunshine's exact current systemd invocation, which reports the matching Wayland output and wlroots monitor selection plus both H.264 and HEVC NVENC encoders without capture failures.
2. Moonlight pairs with the host and opens the existing desktop at 1920×1080.
3. A typed test string and a pointer action issued from the Mac appear in the same Hyprland session.
4. A LAN session and a Tailscale-address session both establish without public WAN exposure.
5. The physical-display path and a simulated no-physical-display/headless path both produce a capturable output.
6. Moonlight's statistics overlay confirms hardware decode and records host processing, network, and decode latency.
7. Route selection chooses LAN, remote, or degraded profile from deterministic measured inputs.
8. Re-running both installers is safe and leaves one service, one launcher, and one managed configuration.
9. No credentials or device-private identifiers exist in the repository.
10. The doctor commands explain the current Wi-Fi packet-loss limitation and the Ethernet recommendation.

## 10. Non-goals

- Audio streaming, gamepad support, HDR, 4K, multi-monitor composition, file transfer, clipboard synchronization, and public internet port forwarding.
- Replacing Hyprland, creating a second login session, or building a new remote-desktop codec/protocol.
- Claiming the current Wi-Fi path meets low-latency goals when measurement shows otherwise.
