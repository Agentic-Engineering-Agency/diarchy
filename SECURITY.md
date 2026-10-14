# Security policy

Diarchy is in public beta (`0.1.0-beta.1`). Only the latest release gets security fixes.

## Report a vulnerability

Use GitHub private reporting: <https://github.com/Sebastiangmz/diarchy/security/advisories/new>

Do not open a public issue for a vulnerability. Do not post secrets, IP addresses, host names or SSH keys in a report.

This is a small volunteer project. We aim to answer within 7 days. We cannot promise a fix date.

## In scope

- The Diarchy menu-bar app.
- The host agent (`diarchy-host`) and its systemd user units.
- The install scripts in `client/` and `host/`.
- The clipboard bridge and the file portal.

## Out of scope

- Bugs in Sunshine, Moonlight, Lan Mouse, Tailscale, Omarchy or Hyprland. Report them to those projects.
- Attacks that need root on the Mac or on the Omarchy host.
- The Gatekeeper warning for the beta app. The app is ad-hoc signed and not notarized. This is known and documented in [docs/install.md](docs/install.md).

## Safe harbor

If you test only your own machines, report in good faith, and do not read or change other people's data, we will not take legal action against you.
