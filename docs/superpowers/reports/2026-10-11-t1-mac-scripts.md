# T1 mac-scripts — Final report

Branch `feat/diarchy-app--mac-scripts`, base `bbe4c90`, final code commit `0806fb4` (report commit follows).
Plan: `docs/superpowers/plans/2026-10-11-diarchy-app-t1-mac-scripts.md`. Process: subagent-driven-development, one fresh
implementer per task, task review + scoped re-review per fix round, one whole-branch review (all on `anthropic/claude-sonnet-5-5`,
high thinking; see R8).

## Deliverables vs. brief

| Brief item | Status | Evidence |
|---|---|---|
| `client/diarchy-config.sh` loader (§4.3), canonical reasons (§4.5) | done | `tests/test_config_loader.py` |
| `client/diarchy-route.sh` generalized (§5) | done | `tests/test_route_selection.py` |
| `client/diarchy-ssh` (§5) | done | `tests/test_diarchy_ssh.py` |
| `client/diarchy-kvm-configure` (all four sides, LAN-only, Tailscale-only, both, with/without fingerprint, unmanaged backup) | done | `tests/test_kvm_configure.py` |
| `client/diarchy-mac-fingerprint` | done | `tests/test_mac_fingerprint.py` |
| `install-lan-mouse.sh`: no config writing, no `Diarchy KVM.app` build, owned one removed, installs new commands, calls `diarchy-kvm-configure` only when config loads | done | `tests/test_lan_mouse_client.py` |
| `Diarchy KVM.applescript` deleted + test refs | done | `git ls-files client` |
| `diarchy-kvm-client-doctor` generalized (side/hosts from config; label stays `"host"`) | done | `tests/test_lan_mouse_client.py` (all four sides, LAN-only, Tailscale-only, e2e vs real `diarchy-kvm-configure`) |
| `install-clipboard-file-transfer.sh` installs `diarchy-config.sh` + `diarchy-ssh`; doctor route digest updated | done | `tests/test_clipboard_file_transfer_install.py` |
| Existing route-driven tests migrated to temp `DIARCHY_CONFIG` fixtures | done | `test_file_transfer`, `test_clipboard_bridge`, `test_file_portal_receiver`, `test_file_portal_send`, `test_route_selection`, `test_clipboard_file_transfer_install` |
| Acceptance: `grep` for personal addresses only in t4-owned files | done | below |
| Acceptance: `diarchy-ssh --route lan -- true` argv | done | below |
| Messages to Main: `READY LOADER 0de47bb` | sent | |

## Files changed (client/, tests/)

New: `client/diarchy-config.sh`, `client/diarchy-ssh`, `client/diarchy-kvm-configure`, `client/diarchy-mac-fingerprint`,
`tests/test_config_loader.py`, `tests/test_diarchy_ssh.py`, `tests/test_kvm_configure.py`, `tests/test_mac_fingerprint.py`.
Modified: `client/diarchy-route.sh`, `client/install-lan-mouse.sh`, `client/diarchy-kvm-client-doctor`,
`client/install-clipboard-file-transfer.sh`, `client/diarchy-clipboard-file-transfer-client-doctor`,
`tests/test_route_selection.py`, `tests/test_file_transfer.py`, `tests/test_clipboard_bridge.py`,
`tests/test_lan_mouse_client.py`, `tests/test_clipboard_file_transfer_install.py`.
Deleted: `client/Diarchy KVM.applescript`.
Unchanged (byte-identical to base apart from reaching the new route): `diarchy-kvm`, `diarchy-clipboard-bridge`, `diarchy-file`,
`diarchy-file-portal-receiver`, `diarchy-file-portal-send`, `diarchy-kvm-accessibility-check`
(`diarchy-kvm-accessibility-check`/`diarchy-kvm` needed no edit: no hardcoded values found).

## Verification (real output, HEAD `0806fb4`)

Owned test modules (12 modules):

```
$ python3 -m unittest tests.test_config_loader tests.test_diarchy_ssh tests.test_kvm_configure tests.test_mac_fingerprint \
    tests.test_route_selection tests.test_file_transfer tests.test_clipboard_bridge tests.test_file_portal_receiver \
    tests.test_file_portal_send tests.test_diarchy_kvm_hook tests.test_lan_mouse_client tests.test_clipboard_file_transfer_install
Ran 275 tests in 165.361s
OK
```

Full suite (includes t4-owned `tests/test_client.py` etc., none broken by the loader):

```
$ python3 -m unittest discover -s tests
Ran 360 tests in 180.027s
OK
```

Acceptance grep:

```
$ grep -rnE '192\.168\.0\.(125|107)|100\.106\.228\.87' client/
client/diarchy-stream:4:LAN_HOST=${DIARCHY_LAN_HOST:-192.0.2.10}
client/diarchy-stream:5:TAILSCALE_HOST=${DIARCHY_TAILSCALE_HOST:-100.64.0.10}
```
Only the t4-owned `client/diarchy-stream`.

Smoke (temp HOME, fixture `diarchy.conf`, fake ping/route/nc/ssh on `*_BIN`): `diarchy-ssh --route lan -- true` exit 0, ssh argv
`-o ControlMaster=no -o ControlPath=none -o PermitLocalCommand=no -o ClearAllForwardings=yes -o User=labuser
-o HostKeyAlias=100.64.0.10 -o HostName=192.0.2.10 -o Port=22 -o IdentityFile=/keys/id_lab -o IdentitiesOnly=yes -o BatchMode=yes
-o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new diarchy-stream -- true`.
`diarchy-kvm-configure` with the same config wrote `~/.config/lan-mouse/config.toml` mode `600` with `position = "right"`,
`ips = ["192.0.2.10", "100.64.0.10"]` and the fingerprint lowercased (config holds uppercase).

## Contract notes for other teams

- Loader reasons/order are canonical (spec §4.5). After `b1cb5a8` the loader only gained cases inside existing reasons: empty file →
  first-line reason; config path not a regular readable file → `cannot read configuration file: <path>`; identity containing a newline
  or `"` → `DIARCHY_SSH_IDENTITY_FILE is invalid`; FPS evaluated base 10 and limited to 1–3 digits, port 1–5 digits (R11).
  Reference implementation is now `0806fb4` (`client/diarchy-config.sh`).
- `diarchy-ssh` argv tail is `<alias> -- <remote words>` (R9); usage errors exit 64 (R3); route/config failure exit 255 with the route or
  loader line on stderr (contains `unreachable` for route failure); ssh's own stderr and exit code pass through untouched.
  The real stderr prefix is `diarchy-route:` or `diarchy-config:`, never `diarchy-ssh:`.
- `config.toml` fingerprints are lowercase; `diarchy.conf` uppercase; doctors compare case-insensitively.
- `diarchy-route.sh` quotes an `IdentityFile` value only when it contains whitespace (ssh `-o` parsing); the clipboard doctor pins its
  sha256 (`EXPECTED_ROUTE_SHA256=c3ff31cf…c15c5f`).
- Installed layout: the two T1 installers together install 13 files into `~/.local/bin` (0755): `diarchy-kvm`,
  `diarchy-kvm-client-doctor`, `diarchy-kvm-accessibility-check`, `diarchy-config.sh`, `diarchy-route.sh`, `diarchy-ssh`,
  `diarchy-kvm-configure`, `diarchy-mac-fingerprint`, `diarchy-clipboard-bridge`, `diarchy-file`,
  `diarchy-clipboard-file-transfer-client-doctor`, `diarchy-file-portal-receiver`, `diarchy-file-portal-send`. Both must run before
  `client/install.sh` (t4 installs neither `diarchy-route.sh` nor `diarchy-config.sh`); spec §7 already orders them that way.

## Rulings

- R1 — Forced route to an unset side fails `diarchy-route: forced <LAN|Tailscale> route is not configured` — spec silent — breaks nothing cross-team.
- R2 — `diarchy-ssh` discards the route's `route=…` diagnostic on success, prints the route/config failure on failure — keeps the app's
  stderr first line meaningful — a consumer wanting route info loses it.
- R3 — `diarchy-ssh` usage errors exit 64 (supersedes the plan line saying 255) — spec silent — none.
- R4 — `diarchy-kvm-configure` backup names get `.1`, `.2` suffixes on collision — avoid clobbering backups — none.
- R5 — A foreign `Diarchy KVM.app` is left in place with `[info]` (only the owned `dev.sebastiangmz.diarchy-kvm` is removed) — none.
- R6 — `--authorize-fingerprint` removed from the client `install-lan-mouse.sh` (exit 64) — the fingerprint now lives in
  `DIARCHY_HOST_FINGERPRINT` — a caller still passing it fails.
- R7 — Clipboard installer's SSH preflight runs only when a config loads, otherwise `[info]` and continue — checklist step 1 runs before
  config exists — weaker preflight before configuration.
- R8 — Created user-level agents (an implementer and a reviewer) (copies of t2's, pinned to `anthropic/claude-sonnet-5-5`, high
  thinking) because the `task` tool has no model parameter and default agents resolve to an unavailable model — two files under
  the user-level agent definitions directory outside the repo. Task 1's first implementation ran on the default `implementer` agent; its review and fix round
  ran on the pinned agents.
- R9 — `diarchy-ssh` passes `--` between the alias and the remote words so a leading-dash word is never an ssh option (Main verified
  against a real sshd) — a test double asserting the tail without `--` would fail.
- R10 — The clipboard doctor surfaces the exact loader reason as `[fail] <reason>` (deferred from Task 5 to Task 6) — none.
- R11 — Loader FPS is 1–3 digits and port 1–5 digits (so `0060` is rejected) — matches the Swift codec — a value like `0060` that §4.2
  could read as valid is rejected.
- R12 — Route wraps `IdentityFile` in double quotes only when it contains whitespace; the loader rejects `"` and newline in the identity
  path so the quoting cannot be broken out of — a path with a literal backslash is passed as-is and its ssh handling was not tested.
- R13 — Doctors treat an empty configured `DIARCHY_HOST_FINGERPRINT` as `[fail] trusted host fingerprint is missing` (unpaired Mac is
  not healthy; the previous doctor also failed without a fingerprint).
- R14 — For a present-but-invalid `diarchy.conf`, both installers continue and print `[info] diarchy.conf is not usable (<reason>); …`
  (non-fatal) instead of the generic not-configured text.

## Deferred minors (non-blocking)

- INT/TERM traps in `diarchy-kvm-configure` and bash's deferred trap execution when only the script (not its process group) receives
  SIGTERM during the route probe in `diarchy-ssh`: cleanup happens after the probe command ends; no automated test for kvm-configure traps.
- The clipboard doctor pins only the route library digest; the loader sourced through it is unpinned (same trust as the user's own
  `~/.local/bin`).
- Doctor fingerprint match is an unanchored `grep -F` on the `"<fp>" = "host"` line.
- `diarchy-ssh` through a symlink fails with `cannot read route library` (installs are copies).
- `client/install.sh` and `host/*` installers still use `cd` without `CDPATH=` (not T1-owned).
- Two source-text tests (`test_doctor_has_no_hardcoded_hosts`, `test_client_installer_has_no_personal_ssh_alias_default`) pin text rather
  than behavior; low value, kept.
- Spec/plan text for `diarchy-ssh` should mention `--` (R9) and exit 64 (R3); Main noted §5.

## Only physical verification on a real Host/Mac can prove

- Real `ssh` with `StrictHostKeyChecking=accept-new` and a shared `HostKeyAlias` across LAN and Tailscale (fakes and `ssh -G` only here).
- Real Lan Mouse certificate fingerprint output on macOS LibreSSL (`diarchy-mac-fingerprint` was exercised with LibreSSL on this Mac
  against a throwaway cert by the reviewer, and with fakes for both output forms).
- Installers on a Mac with no config and then with the app-written config (fresh-Mac flow, spec §8.4 steps 1–2).
- Pairing, edge crossing per side, clipboard/file portal round trip against the real Host.
