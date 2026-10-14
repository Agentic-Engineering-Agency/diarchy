# Diarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install and verify a native Sunshine→Moonlight remote desktop from the Mac to the maintainer's existing Hyprland session over LAN or Tailscale, including a closed-lid headless fallback.

**Architecture:** Sunshine runs inside the existing Hyprland user session with wlroots capture and hardware encoding. A sleeping event-driven Bash helper owns one Hyprland headless output only when the physical panel is unavailable. A macOS launcher performs a bounded network probe, chooses LAN/Tailscale and a quality profile, then launches native Moonlight without joining the media path.

**Tech Stack:** Bash 5, systemd user services, Hyprland IPC, Sunshine v2026.516.143833+, Moonlight Qt for macOS, Python 3 standard library tests, AppleScript application wrapper.

## Global Constraints

- Preserve the existing Hyprland process and desktop session; never start a nested compositor or second login session.
- Use `capture = wlr`, because Sunshine documents virtual Hyprland output capture through this backend.
- Use NVIDIA NVENC; both H.264 and HEVC must pass Sunshine's real startup probe, with no software fallback.
- Initial stream contract is video, keyboard, and mouse; `stream_audio = disabled` and controller input is disabled.
- Disable UPnP and public WAN forwarding; remote access uses Tailscale address `100.64.0.10`.
- Prefer LAN address `192.0.2.10`; select 1080p75/25 Mbps, 1080p60/15 Mbps, or 720p60/8 Mbps from measured path quality.
- Keep credentials, pairing PINs, private keys, certificates, serial numbers, and Tailscale material out of the repository and command output.
- Both installers must be idempotent and preserve unrelated user configuration.
- Pin the official Sunshine AppImage `v2026.516.143833` with SHA-256 `d0ee0a9cfb66f27869b559455f84622d21615047ccf3443c9a2f572ca971c7a2`.

---

## File map

- `host/diarchy-display`: owns headless-output reconciliation and Hyprland monitor-event watching.
- `host/diarchy-host-doctor`: reports session, capture, encoder, input, service, route, and packet-loss facts.
- `host/diarchy-check-sunshine-log`: validates exact Wayland output selection plus H.264/HEVC NVENC markers from one Sunshine invocation.
- `host/diarchy-check-sunshine-service`: binds selected output, current systemd `InvocationID`, journal query, and log validation into one testable probe.
- `host/sunshine.conf`: credential-free Sunshine runtime policy.
- `host/apps.json`: exposes only the existing Desktop session.
- `host/systemd/diarchy-display.service`: starts the output reconciler after the graphical session.
- `host/install.sh`: verifies hardware, extracts the pinned Sunshine AppImage rootlessly, deploys files, verifies lid policy, configures services, and runs probes.
- `client/diarchy-stream`: selects route/profile and launches Moonlight.
- `client/diarchy-client-doctor`: reports Moonlight, route, Tailscale, latency, and loss.
- `client/Diarchy.applescript`: zero-background native launcher wrapper.
- `client/install.sh`: installs Moonlight, scripts, and the native launcher idempotently.
- `tests/test_display.py`: tests output reconciliation against a fake `hyprctl`.
- `tests/test_client.py`: tests deterministic route/profile selection against a fake probe and launcher.

### Task 1: Host display reconciler

**Files:**
- Create: `host/diarchy-display`
- Create: `tests/test_display.py`

**Interfaces:**
- Consumes: `hyprctl -j monitors all`, `hyprctl -j workspaces`, and Hyprland `.socket2.sock` monitor events.
- Produces: CLI `diarchy-display reconcile|watch|status`; environment overrides `HYPRCTL_BIN`, `JQ_BIN`, `SOCAT_BIN`, `SYSTEMCTL_BIN`, `PHYSICAL_OUTPUT`, `HEADLESS_OUTPUT`, `HEADLESS_MODE`, and `HYPR_EVENT_SOCKET` for tests.

- [x] **Step 1: Write failing reconciliation tests**

```python
class DisplayTests(unittest.TestCase):
    def test_creates_headless_when_physical_is_absent(self):
        result = run_display("reconcile", monitors=[], workspaces=[(1, "eDP-1")])
        self.assertEqual(result.commands, [
            ["output", "create", "headless", "DIARCHY"],
            ["keyword", "monitor", "DIARCHY,1920x1080@75,0x0,1"],
            ["dispatch", "moveworkspacetomonitor", "1", "DIARCHY"],
            ["dispatch", "workspace", "1"],
        ])

    def test_returns_workspaces_and_removes_owned_headless(self):
        result = run_display("reconcile", monitors=["eDP-1", "DIARCHY"], workspaces=[(4, "DIARCHY")])
        self.assertIn(["dispatch", "moveworkspacetomonitor", "4", "eDP-1"], result.commands)
        self.assertEqual(result.commands[-1], ["output", "remove", "DIARCHY"])
```

- [x] **Step 2: Run the tests and confirm RED**

Run: `python3 -m unittest tests/test_display.py -v`

Expected: FAIL because `host/diarchy-display` does not exist.

- [x] **Step 3: Implement idempotent reconcile/status/watch**

```bash
case "${1:-}" in
  reconcile) reconcile ;;
  status) select_output ;;
  watch)
    reconcile
    while IFS= read -r event; do
      case "$event" in monitoradded*|monitorremoved*) reconcile ;; esac
    done < <(socat -u "UNIX-CONNECT:${HYPR_EVENT_SOCKET:-$(event_socket)}" -)
    ;;
  *) printf 'usage: %s reconcile|watch|status\n' "$0" >&2; exit 64 ;;
esac
```

Implementation invariants: parse JSON with `jq -e`; treat disabled `eDP-1` as absent; create only `DIARCHY`; repair wrong headless mode/position/scale by recreation; migrate and focus physical workspaces during headless transition; move every workspace owned by the headless output before removal; no-op when state is already correct; propagate event-body failures to systemd; wait/reconnect only after a clean IPC socket closure.

- [x] **Step 4: Run focused tests and a live status smoke**

Run: `python3 -m unittest tests/test_display.py -v`

Expected: all display tests pass.

Run on the host: `host/diarchy-display status`

Expected with lid open: `eDP-1`.

- [x] **Step 5: Commit**

```bash
git add host/diarchy-display tests/test_display.py
git commit -m "feat: reconcile Hyprland mirror output"
```

### Task 2: Sunshine host policy and installer

**Files:**
- Create: `host/sunshine.conf`
- Create: `host/apps.json`
- Create: `host/systemd/diarchy-display.service`
- Create: `host/diarchy-host-doctor`
- Create: `host/diarchy-check-sunshine-log`
- Create: `host/diarchy-check-sunshine-service`
- Create: `host/install.sh`

**Interfaces:**
- Consumes: Task 1's `diarchy-display`, systemd user manager, `/dev/nvidia0`, `/dev/uinput`, `nvidia-smi`, and the pinned official Sunshine AppImage.
- Produces: fixed managed paths `~/.config/sunshine/{sunshine.conf,apps.json}` and `~/.config/systemd/user`, a rootless Sunshine runtime under `~/.local/opt`, managed wrappers under `~/.local/bin`, services enabled under `graphical-session.target`, and `diarchy-host-doctor`.

- [x] **Step 1: Write credential-free host templates**

```ini
sunshine_name = host
capture = wlr
encoder = nvenc
stream_audio = disabled
keyboard = enabled
mouse = enabled
controller = disabled
upnp = disabled
origin_web_ui_allowed = pc
lan_encryption_mode = 1
wan_encryption_mode = 2
max_bitrate = 30000
hevc_mode = 2
av1_mode = 1
min_log_level = info
file_apps = apps.json
```

```json
{"env":{},"apps":[{"name":"Desktop","image-path":"desktop.png"}]}
```

- [x] **Step 2: Implement the installer with hard preflights**

```bash
SUNSHINE_VERSION=2026.516.143833
SUNSHINE_SHA256=d0ee0a9cfb66f27869b559455f84622d21615047ccf3443c9a2f572ca971c7a2
require_session() { host/diarchy-display status >/dev/null; }
require_encoder() { test -c /dev/nvidia0 && nvidia-smi --query-gpu=name --format=csv,noheader; }
install_managed_file() { install -Dm"$1" "$2" "$3"; }
```

The installer and doctor call one shared service probe that obtains the selected output and current systemd `InvocationID`, reads only that invocation's logs, and feeds them to the log checker. Success requires an exact end-anchored `[wayland] Name`, a monitor-selection marker correlated to `eDP-1` or the observed empty headless description, the Wayland screencast marker, and both H.264 and HEVC NVENC markers; any frame-capture failure is fatal.

Installer steps: verify Linux/x86_64, the current Hyprland session, `/dev/uinput`, and NVIDIA availability; download the pinned official AppImage over TLS; verify SHA-256; extract it rootlessly; install a wrapper that discovers the live Hyprland/Wayland environment and executes `AppRun` from its real directory; preserve pre-existing unmanaged Sunshine config under a timestamped permission-restricted backup; install scripts/templates and re-enable both user services under `graphical-session.target`; verify the existing effective lid policy without escalating privileges; generate a random UI password without printing it, apply it through `sunshine --creds`, and only then publish credential state atomically; restart both services and require exact H.264 and HEVC NVENC markers from the current systemd invocation.

- [x] **Step 3: Implement the host doctor**

```bash
check "Hyprland session" diarchy-display status
check "Sunshine service" systemctl --user is-active diarchy-sunshine.service
check "Mirror output service" systemctl --user is-active diarchy-display.service
check "Current H.264 and HEVC NVENC" current_invocation_logs
check "Input device" test -w /dev/uinput
probe_path 192.0.2.20
```

The doctor exits non-zero for missing capture/encoder/input/service requirements but reports current packet loss as a warning with the exact Ethernet recommendation.

The checker is fixture-tested for both `eDP-1` and `DIARCHY`, including Sunshine's valid empty headless monitor description, and rejects generic Wayland text, output mismatch, missing selection, or capture failure.
Behavioral tests stub display, systemd, and journal commands to prove the exact invocation filter is used and each output/capture/encoder failure propagates. Event-loop fixtures separately prove nonzero socket closure does not reconnect, clean closure does reconnect, and post-event monitor-query failures escape to systemd.

- [x] **Step 4: Install on the host and inspect service logs**

Run: `ssh diarchy 'cd ~/diarchy && ./host/install.sh'`

Expected: AppImage checksum passes, both services are active, and the current Sunshine invocation logs the selected wlroots display plus exact `h264_nvenc` and `hevc_nvenc` encoders.

Run: `ssh diarchy '~/.local/bin/diarchy-host-doctor'`

Expected: all functional checks pass; network quality may warn about measured Wi-Fi loss.

- [x] **Step 5: Commit**

```bash
git add host tests/test_display.py tests/test_host.py
git commit -m "feat: provision Sunshine host"
```

### Task 3: macOS route/profile launcher

**Files:**
- Create: `client/diarchy-stream`
- Create: `tests/test_client.py`

**Interfaces:**
- Consumes: `ping`, `route`, `nc`, native Moonlight executable, and environment overrides `DIARCHY_LAN_HOST`, `DIARCHY_TAILSCALE_HOST`, `MOONLIGHT_BIN`, `PING_BIN`, `ROUTE_BIN`, `CONNECT_BIN`, `SUNSHINE_CONTROL_PORT`, `DIARCHY_DRY_RUN`.
- Produces: CLI `diarchy-stream [--diagnose|--dry-run|--force-lan|--force-remote]`, one selected host/profile, and one Moonlight launch.

- [ ] **Step 1: Write failing deterministic selection tests**

```python
class ClientTests(unittest.TestCase):
    def test_healthy_local_path_uses_lan_1080p75(self):
        result = run_client(lan={"reachable": True, "loss": 0, "median_ms": 4})
        self.assertEqual(result.profile, "lan")
        self.assertEqual(result.host, "192.0.2.10")

    def test_lossy_local_path_uses_degraded_profile(self):
        result = run_client(lan={"reachable": True, "loss": 10, "median_ms": 50})
        self.assertEqual(result.profile, "degraded")

    def test_unreachable_lan_falls_back_to_tailscale(self):
        result = run_client(lan={"reachable": False}, tailnet={"reachable": True, "loss": 0, "median_ms": 20})
        self.assertEqual(result.host, "100.64.0.10")
        self.assertEqual(result.profile, "remote")

    def test_blocked_lan_service_falls_back_to_tailscale(self):
        result = run_client(lan={"reachable": True}, lan_service=False, tailnet={"reachable": True})
        self.assertEqual(result.host, "100.64.0.10")
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `python3 -m unittest tests/test_client.py -v`

Expected: FAIL because `client/diarchy-stream` does not exist.

- [ ] **Step 3: Implement bounded route, latency, and Sunshine-service measurement with one-shot launch**

```bash
if lan_reachable; then
  host=$DIARCHY_LAN_HOST
  if (( loss_pct > 2 )) || (( median_ms > 25 )); then profile=degraded; else profile=lan; fi
elif tailnet_reachable; then
  host=$DIARCHY_TAILSCALE_HOST
  profile=remote
else
  fail "Host is unreachable on LAN and Tailscale"
fi
launch_moonlight "$host" "$profile"
```

Profile command contract after inspecting the installed Moonlight `--help`: LAN = 1920×1080/75/25000 Kbps HEVC; remote = 1920×1080/60/15000 Kbps HEVC; degraded = 1280×720/60/8000 Kbps H.264. If Moonlight exposes settings only through its persisted configuration, install three named configuration files and atomically activate one before launching; never spawn a wrapper process that remains between Moonlight and Sunshine.

- [ ] **Step 4: Run tests and real dry-run selection**

Run: `python3 -m unittest tests/test_client.py -v`

Expected: all client selection tests pass.

Run: `client/diarchy-stream --dry-run`

Expected on the measured network: the first route whose route, ICMP, and Sunshine control-port probes all succeed, with the profile and measured loss/latency reason. A pingable LAN address whose GameStream ports are blocked must fall back to Tailscale.

- [ ] **Step 5: Commit**

```bash
git add client/diarchy-stream tests/test_client.py
git commit -m "feat: select Moonlight route and quality"
```

### Task 4: Native Moonlight client installation

**Files:**
- Create: `client/Diarchy.applescript`
- Create: `client/diarchy-client-doctor`
- Create: `client/install.sh`

**Interfaces:**
- Consumes: Task 3 launcher, the pinned official Moonlight 6.1.0 universal DMG, macOS `hdiutil`, `codesign`, `spctl`, `PlistBuddy`, `ditto`, and `osacompile`.
- Produces: `~/Applications/Moonlight.app`, `~/.local/bin/diarchy-stream`, `~/.local/bin/diarchy-client-doctor`, and `~/Applications/Diarchy.app`.

- [ ] **Step 1: Implement signed Moonlight installation and native wrapper**

```applescript
on run
  do shell script quoted form of (POSIX path of (path to home folder) & ".local/bin/diarchy-stream") & " >/dev/null 2>&1 &"
end run
```

Installer invariants: download the official Moonlight 6.1.0 universal DMG; verify the pinned SHA-256 published independently by Homebrew Cask; mount read-only; require valid Gatekeeper assessment, code signature, and exact `com.moonlight-stream.Moonlight` bundle identity; replace only an existing bundle with that same identity; compile and ad-hoc sign the owned `dev.sebastiangmz.diarchy` launcher; install everything rootlessly with scripts at mode 0755; do not request Accessibility or Screen Recording permissions because Moonlight receives decoded frames and sends protocol input rather than capturing the Mac.

- [ ] **Step 2: Implement client doctor and verify install**

```bash
check "Moonlight signature" codesign --verify --deep --strict "$HOME/Applications/Moonlight.app"
check "Moonlight Gatekeeper" spctl --assess --type execute "$HOME/Applications/Moonlight.app"
check "LAN route" ping_probe 192.0.2.10
check "Tailscale route" ping_probe 100.64.0.10
"$HOME/.local/bin/diarchy-stream" --dry-run
```

Run: `./client/install.sh && ~/.local/bin/diarchy-client-doctor`

Expected: signatures pass, at least one route works, and launcher dry-run names one route/profile.

- [ ] **Step 3: Commit**

```bash
git add client/Diarchy.applescript client/diarchy-client-doctor client/install.sh
git commit -m "feat: install native Moonlight client"
```

### Task 5: Pairing, input, physical/headless, and route verification

**Files:**
- Modify only if a measured failure requires a source fix; no new permanent test scaffolding.

**Interfaces:**
- Consumes: installed host/client, Sunshine local web UI through SSH forwarding, Moonlight GUI, Hyprland IPC, systemd logs, and Moonlight statistics overlay.
- Produces: one paired Mac client and an evidence record in the final response.

- [ ] **Step 1: Pair without exposing credentials or PINs**

Open an SSH tunnel from Mac localhost to Sunshine's HTTPS UI, retrieve the generated host UI credential from the host directly into a private process channel, add `192.0.2.10` in Moonlight, enter the displayed pairing PIN in Sunshine, and confirm the host becomes paired. Never write the password or PIN to repository/log output.

- [ ] **Step 2: Verify the physical display stream**

Launch `Desktop` through `diarchy-stream`. Observe 1920×1080 video, invoke Moonlight's statistics overlay, and record codec, host processing latency, network latency, decode latency, FPS, and dropped frames. Confirm Sunshine logs report `wlr` plus NVENC.

- [ ] **Step 3: Verify keyboard and mouse in the same session**

From Moonlight, focus a disposable text field and type `diarchy-input-ok`; move and click the pointer. On the host, observe those events in the current Hyprland session and remove the disposable text afterwards.

- [ ] **Step 4: Verify simulated headless behavior**

Temporarily disable `eDP-1` through Hyprland IPC, run `diarchy-display reconcile`, confirm `DIARCHY` is 1920×1080@75 and Sunshine can stream it, then restore `eDP-1`, reconcile, and confirm the owned headless output is removed. A trap must restore the physical output even when a command fails.

- [ ] **Step 5: Verify LAN and Tailscale addressing**

Start one session by LAN address and one by Tailscale address. Confirm both reach the paired Sunshine host. Verify UPnP is disabled and no public port mapping was created.

- [ ] **Step 6: Run complete verification**

Run locally: `python3 -m unittest discover -s tests -v`

Run on the host: `~/.local/bin/diarchy-host-doctor`

Run on Mac: `~/.local/bin/diarchy-client-doctor`

Expected: behavior tests pass; all functional checks pass; current packet loss may remain a named network warning rather than a hidden success.

- [ ] **Step 7: Commit measured fixes only**

```bash
git add host client tests
git diff --cached --check
git commit -m "fix: harden verified mirror setup"
```

Skip this commit when verification required no source changes.
