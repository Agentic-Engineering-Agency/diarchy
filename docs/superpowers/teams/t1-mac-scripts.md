# Team T1 — mac-scripts

Team id: `t1`. Branch: `feat/diarchy-app--mac-scripts`.

## Mission

Make every Mac-side bash script generic and configured from `diarchy.conf`, and add the new Mac commands the app
calls. Spec sections: §2, §4 (all), §5 (all), §10, §12.

## Ownership (edit only these)

- `client/diarchy-config.sh` (new), `client/diarchy-ssh` (new), `client/diarchy-kvm-configure` (new),
  `client/diarchy-mac-fingerprint` (new)
- `client/diarchy-route.sh`, `client/diarchy-kvm`, `client/diarchy-kvm-client-doctor`,
  `client/diarchy-kvm-accessibility-check`, `client/install-lan-mouse.sh`, `client/Diarchy KVM.applescript`
  (delete), `client/install-clipboard-file-transfer.sh`, `client/diarchy-clipboard-file-transfer-client-doctor`,
  `client/diarchy-clipboard-bridge`, `client/diarchy-file`, `client/diarchy-file-portal-receiver`,
  `client/diarchy-file-portal-send`
- Tests: `tests/test_route_selection.py`, `tests/test_file_transfer.py`, `tests/test_clipboard_bridge.py`,
  `tests/test_file_portal_receiver.py`, `tests/test_file_portal_send.py`, `tests/test_diarchy_kvm_hook.py`,
  `tests/test_lan_mouse_client.py`, `tests/test_clipboard_file_transfer_install.py`, and new
  `tests/test_config_loader.py`, `tests/test_diarchy_ssh.py`, `tests/test_kvm_configure.py`,
  `tests/test_mac_fingerprint.py`
- `docs/superpowers/plans/2026-10-11-diarchy-app-t1-mac-scripts.md`,
  `docs/superpowers/reports/2026-10-11-t1-mac-scripts.md`

NOT yours: `client/diarchy-stream`, `client/diarchy-client-doctor`, `client/install.sh`,
`client/Diarchy.applescript` (team t4); anything in `host/` or `app/`.

## Stages

1. **Loader first (other teams wait on it).** `client/diarchy-config.sh` per spec §4.3 with
   `tests/test_config_loader.py` covering: missing file with/without env fallback, every key's validation failure
   (exact `diarchy-config: <reason>` line), duplicates, unknown keys, quoted values rejected, env precedence,
   defaults (including `DIARCHY_HOST_KEY_ALIAS` default rule), and that the file is never executed (a value like
   `$(touch /tmp/x)` must not run). Commit, then message `[t1→Main] READY LOADER <hash>`.
2. Route + ssh: generalize `diarchy-route.sh` (§5 row), add `diarchy-ssh`. Update every existing test that drives
   the route so it supplies a config (temp `DIARCHY_CONFIG` file) — existing assertions about IPs become fixture
   values. Update `EXPECTED_ROUTE_SHA256` in the clipboard doctor to the new route hash and keep its test green.
3. `diarchy-kvm-configure` (all four sides, LAN-only, Tailscale-only, both, with/without fingerprint, unmanaged
   backup) and `diarchy-mac-fingerprint`.
4. `install-lan-mouse.sh` (client): stop writing config itself and stop building `Diarchy KVM.app`; remove an owned
   one; install the four new commands; call `diarchy-kvm-configure` only when a config loads. Delete
   `client/Diarchy KVM.applescript` and its test references. Generalize `diarchy-kvm-client-doctor` (side and hosts
   from config; fingerprint label stays `"host"`). `install-clipboard-file-transfer.sh` installs
   `diarchy-config.sh` and `diarchy-ssh` too if it installs the route library.
5. Verification: run all owned test files, then the full suite once; report.

## Acceptance

- `grep -rnE '192\.168\.0\.(125|107)|100\.106\.228\.87' client/` prints only lines inside t4-owned files.
- All owned tests pass; full suite passes except failures in t4-owned tests caused by the shared loader (report them
  to Main with the exact failing test names).
- `diarchy-ssh --route lan -- true` with fakes produces argv containing `User=`, `HostKeyAlias=`, `BatchMode=yes`,
  `ConnectTimeout=5`, `StrictHostKeyChecking=accept-new`, and `IdentityFile=`/`IdentitiesOnly=yes` only when set.
