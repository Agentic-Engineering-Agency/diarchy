# Adapting Diarchy to another Mac and host

Target: a macOS client plus an Omarchy (Hyprland on Arch) host. The only configuration mechanism is
`~/.config/diarchy/diarchy.conf`, written by the app. Do not hand-edit it, and do not set env files for the
scripts.

## Steps

1. Install the app and open it. Menu bar → `Settings…`.
2. **Connection**: SSH user, LAN host, Tailscale host, SSH port, optional SSH key file. `Save and check` writes
   `diarchy.conf` and runs the setup checklist; follow the commands it shows.
3. **Displays**: pick the side of the Mac where the host sits. This sets `DIARCHY_KVM_SIDE` and reconfigures Lan Mouse.
4. **Streaming** (optional): resolution, FPS, overlay, `Install streaming`, `Pair Moonlight`.
5. **Diagnostics**: `Run diagnostics` runs the Mac doctors and `diarchy-host probe`.

## `diarchy.conf` keys

| Key | Required | Default |
|---|---|---|
| `DIARCHY_SSH_USER` | yes | — |
| `DIARCHY_LAN_HOST` | one of LAN/Tailscale | empty |
| `DIARCHY_TAILSCALE_HOST` | one of LAN/Tailscale | empty |
| `DIARCHY_SSH_PORT` | no | `22` |
| `DIARCHY_SSH_IDENTITY_FILE` | no | empty |
| `DIARCHY_HOST_KEY_ALIAS` | no | Tailscale host, else LAN host |
| `DIARCHY_KVM_SIDE` | no | `left` (`left`/`right`/`top`/`bottom`) |
| `DIARCHY_HOST_FINGERPRINT` | no | empty (unpaired) |
| `DIARCHY_STREAM_RESOLUTION` | no | `1920x1080` |
| `DIARCHY_STREAM_FPS` | no | `60` |
| `DIARCHY_STREAM_OVERLAY` | no | `0` |

Format, validation rules, loader behavior and canonical error reasons: spec
`docs/superpowers/specs/2026-10-11-diarchy-app-design.md` §4 (keys §4.2, errors §4.5). Rationale:
`docs/adr/0001-app-orchestrates-existing-scripts.md`.

`DIARCHY_CONFIG` (environment) overrides the file path; scripts never `source` the file.
