# Third-party notices

Diarchy is released under the [MIT license](LICENSE). It uses the programs below. Diarchy does not copy code or
assets from them. It runs them as separate programs.

The Diarchy installers download Lan Mouse and Sunshine from the official upstream releases. Each download is pinned
by version and checked against a SHA-256 value before use. The Mac installer also downloads Moonlight from the
official release with the same pin. Diarchy does not host or redistribute these files.

Diarchy does not bundle Moonlight or Tailscale. The user installs Tailscale. Diarchy does not install it.

## Lan Mouse

- Use: shares keyboard and mouse between the Mac and the Omarchy host (KVM mode).
- Upstream: https://github.com/feschber/lan-mouse
- License: GPL-3.0
- Pinned version: 0.11.0 (Mac and host installers).

## Sunshine

- Use: game-stream host that captures the Omarchy screen (Streaming mode).
- Upstream: https://github.com/LizardByte/Sunshine
- License: GPL-3.0
- Pinned version: 2026.516.143833 (host installer, AppImage).

## Moonlight

- Use: client on the Mac that shows the stream from Sunshine (Streaming mode).
- Upstream: https://github.com/moonlight-stream/moonlight-qt
- License: GPL-3.0
- Pinned version: 6.1.0 (Mac installer). Diarchy does not bundle it.

## Tailscale

- Use: optional route between the Mac and the host outside the local network.
- Upstream: https://github.com/tailscale/tailscale
- License: BSD-3-Clause (open-source client).
- Diarchy does not bundle or install Tailscale. The user installs and signs in to it.

## MenuBarExtraAccess

- Use: Swift package linked into the Mac app. It gives access to the menu-bar item window.
- Upstream: https://github.com/orchetect/MenuBarExtraAccess
- License: MIT
- Version: 1.3.1 (pinned in `app/Package.resolved`).

## Not affiliated

Diarchy is not affiliated with Omarchy, Hyprland, Sunshine, Moonlight, Lan Mouse or Tailscale. These names belong
to their owners. Diarchy uses them only to say which programs it works with.
