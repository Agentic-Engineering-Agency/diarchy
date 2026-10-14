# Diarchy — Domain Glossary

**Controller** — The Mac whose keyboard and mouse drive the other machine. The app runs here. Never receives input
from the Host.

**Host** — The Linux computer (Omarchy today) that receives input or whose desktop is streamed. Also called "the
host" in user-facing text.

**Pairing** — The one-time mutual trust between one Controller and one Host: each side records the other's
certificate fingerprint. A Controller is paired with at most one Host.

**Fingerprint** — The SHA-256 identity of a machine's Lan Mouse certificate. A public value, not a secret.

**Route** — The network path the Controller uses to reach the Host: `LAN` (same local network) or `Tailscale`.
LAN is preferred when usable.

**Side** — Where the Host's screen sits relative to the Controller's screen: left, right, top, or bottom. Crossing
that edge moves input to the Host.

**Mode** — What the pairing is doing right now: `Off`, `KVM`, or `Streaming`. Exactly one at a time.

**KVM** — Mode in which the Controller's keyboard and mouse operate the Host's own physical screen, with clipboard
and file transfer. No video travels.

**Streaming** — Mode in which the Host's desktop is shown inside a window on the Controller and operated from there.

**Setup checklist** — The ordered list of conditions a Controller–Host pair must meet before any Mode can start.

**Required action** — A privileged change on the Host that only the user may perform; the app shows the exact
command and never runs it. `Required` actions block Modes; `recommended` ones do not.

**Agent** — The program on the Host that answers the Controller's questions and starts or stops Host services.

**Platform adapter** — The Host-side description of one family of Linux systems (today: Arch with Hyprland) that the
Agent uses to detect support, missing packages, and how to install them.

**Payload** — The Controller and Host scripts shipped inside the app, installed on each machine by the app.
