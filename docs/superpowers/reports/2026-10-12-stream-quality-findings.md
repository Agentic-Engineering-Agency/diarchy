# Streaming quality — findings

Branch `feat/stream-quality`. Evidence: Moonlight client logs (not tracked; 10 Mac sessions, 2026-10-11 23:32 →
2026-10-12 01:26), host journal (`journalctl`), `coredumpctl`, Sunshine/Moonlight sources (read, not guessed), and two
passive ping captures. No streaming session was started by this work (nothing took over the screen); no host setting
was changed; no `sudo` was used (one `sudo -n true` availability probe was issued by mistake while reading the host's
Wi-Fi setup: it needs a password, so it did nothing).

## Verdict in one paragraph

The periodic freeze is the **host's Wi-Fi radio ping-ponging between two access points of the same network every
~34 s** (82 access-point changes in 24 h). Every change silences the link for ~0.4 s: Moonlight logs 21–86 lost frames
and then waits for an IDR frame. 13 of 13 loss bursts (≥ 5 frames) fall within 3 s of such a change; sessions with no
change have 0.00 % network drops. Two further events are real but secondary: one Sunshine crash (explains one
`Connection terminated: -1`) and one 5-minute client-side presentation stall. Everything else on the candidate list
was either already fine, by design, or has no evidence — and was left alone.

## Hypotheses

| # | Hypothesis | Measurement | Verdict |
|---|------------|-------------|---------|
| 1 | Host Wi-Fi roaming causes the loss bursts | `journalctl -u wpa_supplicant`: 64 `SME: Trying to authenticate` in 22:18–00:03, alternating `00:00:5e:00:53:02` (ch100/5500 MHz, DFS) ↔ `00:00:5e:00:53:01` (ch149/5745 MHz), pairs ~34 s apart; `wlo1` at −70 dBm. Burst times in the Moonlight logs (file epoch + elapsed) vs. those events: 13/13 bursts ≥ 5 frames within 3 s of one (offsets 0–2 s). 13 changes happened inside sessions → 13 bursts. The sessions with zero changes have 0.00–0.07 % network drops (`…1791784160`, `…5768`, `…8685`, `…9129`, `…783046`) and, for the 752 s one (`…9216`), no `Network dropped` event at all. | **Root cause of the periodic stutter.** Fix is host-side Wi-Fi: wired link, or pin the BSSID. Surfaced as the `wifi-roaming` action (below). Efficacy of the pin itself is **unverified** (changing the host's Wi-Fi was out of scope for a read-only run). |
| 2 | Burst length → pixelation: no reference-frame invalidation on `hevc_vaapi` | Sunshine `video.cpp`: only the `nvenc` encoder carries `REF_FRAMES_INVALIDATION`; `vaapi` has `LIMITED_GOP_SIZE \| PARALLEL_ENCODING \| NO_RC_BUF_LIMIT`, `idr_interval = INT_MAX`. `rtsp.cpp` therefore does not advertise `refPicInvalidation`. Moonlight log: every burst is followed by `IDR frame request sent` and 5–48 × `Waiting for IDR frame`. | **Confirmed.** Each loss event costs a full IDR, no cheaper path exists on vaapi. The encoder is pinned to vaapi because capture happens on the Intel iGPU (`gpu.py`); nvenc cannot import those frames. Nothing to change; it makes #1 hurt more. |
| 3 | `fec_percentage` too low | Bursts remove everything for ~0.4 s (24–86 consecutive frames); FEC cannot help. Only one non-burst loss in 10 sessions (`17+5=22 received < 23 needed`, frame 4177: 6 of 28 packets lost). Default is 20 %. | **No change.** Raising it costs bandwidth for one event in 10 sessions. |
| 4 | Bitrate: 15000 → "Streaming bitrate is 10988000" | Sunshine `rtsp.cpp`: `15000 / (100/(100−20)) = 12000` (FEC budget), `− 2 ch × 256 = 512` (high-quality audio estimate, subtracted even with `stream_audio = disabled`), `− 500` = **10988** ✔ exactly. On the wire: 10.99 Mbps video + 20 % FEC ≈ 13.2 Mbps + control. | **By design.** The requested 15/25 Mbps is the on-wire budget, not an encode bitrate. Measured link (292 Mbps PHY, ICMP p50 5 ms) is far above it. No change. |
| 5 | VBR / rate control quality (blocky for seconds after an IDR) | Sunshine `vaapi.cpp`: Intel is whitelisted to VBR with a **single-frame VBV** (log: `Using VBR with single frame VBV size`). An IDR therefore cannot exceed ~one frame of budget (≈ 23 KB at 11 Mbps), so the picture after every IDR is coarse and refines over following frames — which matches "pixelado durante segundos" after each burst. | **Plausible, not measured** (needs a screen-visible session, and the fix is upstream's whitelist choice: `vaapi_strict_rc_buffer`/`vaapi_rc` override). With #1 fixed there are no IDRs to be coarse after. Left as is. |
| 6 | Transport: LAN vs Tailscale, MTU | `tailscale ping`: direct path via the Wi-Fi's IPv6 (`6 ms`), `tailscale0` MTU 1280; Moonlight (`session.cpp`: `RI_VPN` → `STREAM_CFG_REMOTE`, `packetSize = 1024`; the log shows `utun5` matched) uses 1024-byte packets, below the 1280 MTU less overhead: no fragmentation. One session (`…1791785768`) averaged **141 ms** latency (DERP relay) with 0 % loss: latency, not stutter. Passive 3 min ping Mac→host, 900 packets each: LAN p50 4.8 ms / Tailscale p50 5.4 ms, **0 % loss** on both. Source: on a private-range address Moonlight tags traffic with DSCP (`SdpGenerator.c`: `STREAM_CFG_LOCAL` → QoS) and uses 1392-byte packets; neither applies over Tailscale. | **WireGuard overhead is not the stutter source** (LAN and Tailscale behave identically; roam outage is Wi-Fi-level). LAN is still better (QoS tag, bigger packets, no relay fallback) and needs the existing `firewall-streaming-lan` action; already surfaced by the app. No change. |
| 7 | Mac Wi-Fi: AWDL/AirDrop/Continuity scans | `awdl0` is UP. 3-minute 200 ms ping capture: p50 4.8 ms, p99 ≈ 78 ms from one 3-s episode at +156 s that appears **at the same instant on LAN and Tailscale** (so link-level, once, non-periodic). Mac `airportd` log at that instant: SNR 36–37, cca 8 %, no scan, roam, channel or AWDL event. No periodic spikes at AWDL's cadence. | **Not implicated** (this is exactly what a Mac-side check would have shown). No detection code added. The 3-s episode points at the host side (−70 dBm, SNR lower) but is unattributed. |
| 8 | Host: Wi-Fi power save | `iw dev wlo1 get power_save`: **off**; NetworkManager `wifi.powersave = 2` drop-in present. | Already fine. |
| 9 | Host: `CAP_SYS_NICE` warning (EGL context priority) | Host processing latency avg 15–19 ms (min 14–15 ms) in every session; the 792/818 ms maxima are exactly in the two sessions that ended with `Connection terminated: -1` (#11), not under load. Fixing it needs `setcap` (root). | **No evidence it matters.** Not applied. |
| 10 | Moonlight flags (`--video-decoder hardware`, vsync, pacing) | Log: Metal renderer, hardware VideoToolbox decode 1.6–2.4 ms, render time ≈ 1 ms, rendering FPS 59.1–59.6 of 59.9 in clean sessions. V-sync **enabled**; "Frame pacing disabled: target 75 Hz with 60 FPS stream" is Moonlight's default (pacing off). The Mac display runs 1920×1080 @ 75 Hz against a 60 fps stream, which would give a slight 60-on-75 cadence, but render FPS ≈ incoming FPS and nothing in the logs measures judder. | **No change** (cannot be shown from logs; would need an on-screen A/B). Candidate if the user still notices judder with #1 fixed: set the Mac display to 60 Hz or the stream to 75 fps. |
| 11 | `Connection terminated: -1` (2 sessions) | Session `…1791784239`, 13:35 in: Sunshine journal 00:04:05 `Couldn't import RGB Image: 0000300C` → `Could not convert image` → `[wayland] Frame capture failed` ×3 → **SIGSEGV in `session::video`** (`coredumpctl info 657231`); the unit restarted in 4 s (`Restart=on-failure`, `RestartSec=2`) but the client session is gone. The other (`…1791785138`, 1:32 in) has no host-side error and is unexplained. | **Real, upstream.** Sunshine 2026.516.143833 crashes when the wlr dmabuf import fails; nothing in our control except bumping Sunshine (Main installs builds). One capture failure also occurred at 00:05:53 without a crash. |
| 12 | 5-minute stall: 3147 × `Presentation wait timed out after 100 ms` | Only in `…1791784239`, 00:03:27 → 00:13:24 (~10/s), rendering 32 FPS of 57 and 16.7 % "network jitter" drops (client-side drops, not network). `pmset` shows no display sleep / lock in that window; the other 9 sessions have 0–1. | **Unexplained client-side**, not reproducible. Almost certainly the Moonlight window not being presented (occlusion/Space) — nothing the launcher can control. No code. |

## Changes made

1. `host/diarchy_host/wifi.py` + `probe --streaming`: new `wifi-roaming` required action (recommended severity) with
   an exact, no-`sudo` command that pins the connection to the strongest AP of the SSID. Threshold: ≥ 10 AP changes in
   24 h (this host: 82; a host that is only carried between rooms stays below it). Emitted only when `nmcli`
   resolves the connection and the BSSID. Real-host smoke (via ssh wrappers, agent code run locally):
   `82 changes → nmcli connection modify 'example-connection' 802-11-wireless.bssid 00:00:5e:00:53:01 && nmcli connection up 'example-connection'`.
2. App: English title/reason for `wifi-roaming`, listed as a streaming-only id in `SetupChecklist` (can never block
   setup).
3. Spec §6.3 / §9.2 document the action.

## What the user must do

- **Best:** connect the host with an Ethernet cable (the host's `enp2s0` is DOWN today). Also removes the −70 dBm link.
- **Or** run on the host (no sudo): `nmcli connection modify 'example-connection' 802-11-wireless.bssid 00:00:5e:00:53:01 && nmcli connection up 'example-connection'`
  (undo: `nmcli connection modify 'example-connection' 802-11-wireless.bssid ""`). Not applied by this work; unverified.
- Optional: open the streaming ports for the Mac in UFW (the app's `firewall-streaming-lan` action) to use the LAN
  route instead of Tailscale.

## Not done / needs a GO

A before/after streaming session (after the BSSID pin or cable) would confirm that bursts disappear; it takes over
the screen, so it waits for Main's GO.
