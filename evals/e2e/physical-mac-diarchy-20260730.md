# Physical Mac + host E2E — 2026-07-30

Source of truth: `docs/superpowers/specs/2026-07-29-clipboard-file-transfer-design.md` §10.3.

Overall branch E2E verdict: **INCONCLUSIVE for the new edge-activation regression; historical transfer evidence remains PASS**. Earlier executable positive rows, all nine negative rows, transfer semantics, rollback, teardown, listener, collision, and process-lifecycle checks passed. After the user reported that Finder drags did not reveal the Mac tab and the host still showed the old large tab, the fix was installed on the host and its idle layer was observed at `3 × 180`. Cursor-dependent drag verification was stopped at the user's explicit request while they were working, so neither new drag-reveal path is promoted to PASS.

## Positive matrix

| Row | Verdict | Physical observation |
| --- | --- | --- |
| P1 | PASS | With the installed bridge enabled, Mac payload `p1-final-1785381514215555000` reached host byte-for-byte in 0.259628 s. |
| P2 | PASS | Host payload `p2-final-1785381514475228000` reached `pbpaste` byte-for-byte in 0.099477 s. |
| P3 | PASS | `diarchy-kvm off` completed in 0.363249 s. The runtime directory was absent, local bridge count and remote bridge count were 0, status was `disabled (clipboard bridge: off)`, and independent Mac/host values stayed independent for the one-second observation. |
| P4 | PASS | `diarchy-file send /tmp/diarchy-transfer-e2e-final-1785381540476581000.txt` printed exactly one `status=ok` line. Local and remote SHA-256 both equaled `0abd90fd1151150d81684c9978308099b2e1396ad0fd1b6123e69b89a4d38bd6`; the diagnostic selected `route=tailscale host=100.64.0.10`. |
| P5 | PASS | After the lid was opened, Cua reported ScreenCaptureKit healthy with two shareable displays. Finder showed exactly three selected temporary files, and a literal pointer drag onto `Diarchy Drop.app` launched the droplet. After its three sequential transfers completed, remote SHA-256 values exactly matched the local files: `ac691b589dc1ef43f9917b5bcd8b867a7cbb50501b465bba5ac4ae772557722a`, `38ec530b9b91acfe1724ae2152d37206150d306d56d48a8159a3acb4f04dc3df`, and `2367126a709f9a390f9932282419defd687e154e97e9214c8401f711fdfd094a`. The earlier collision check also kept the remote hash unchanged and surfaced the native alert. |
| P6 | PASS | `diarchy-file list` printed `name=notes-final-1785381083810258000.txt bytes=19 mtime=2026-07-30T03:11:23Z`, one machine-readable line for the prepared remote file. |
| P7 | PASS | `diarchy-file get notes-final-1785381083810258000.txt` printed `bytes=19 route=tailscale status=ok`; local and remote SHA-256 both equaled `4c79725db5cde135564baf41f79c960b114f95f5964dd774408c4379b81ebd97`; local mode was `0600`. A second get exited 1 with `status=skipped-exists` and left the local SHA-256 unchanged. |
| P8 | PASS | Host now owns the specified static `192.0.2.10/24` address on `wlan0` with gateway/DNS `198.51.100.1`. The client selected the LAN path over `en0`, TCP/22 was reachable, and `diarchy-file list` completed over LAN. A forced bounded-ICMP-silence check still passed because route/interface plus TCP/22 remained authoritative and SSH stayed healthy. |
| P9 | PASS | With `DIARCHY_LAN_HOST=127.0.0.2`, `diarchy-file list` selected `route=tailscale host=100.64.0.10 interface=utun4`; no route table was changed. |
| P10 | PASS | Both installed sharing doctors exited 0. Every non-empty line was `[ok]`; client Python ≥3.11, reviewed route digest, SSH BatchMode, and host `diarchy-kvm.service active` checks passed. |
| P11 | PASS | The exact alias/Tailscale override command exited 0, selected `route=tailscale host=100.64.0.10`, and retained the default `name=… bytes=… mtime=…` output shape. |

## Negative matrix

| Row | Verdict | Physical observation |
| --- | --- | --- |
| N1 | PASS | A 102401-byte Mac payload left the host sentinel unchanged and produced exactly one `dropped:cap direction=local bytes=102401 sha256=e936fe08`. The exact 102400-byte boundary then arrived in 0.215198 s. The opt-in diagnostic file was mode `0600` and contained metadata only. |
| N2 | PASS | `/usr/sbin/screencapture -c` exited 0, left host text unchanged, and did not increment the local `sent` count (3 before, 3 after). |
| N3 | PASS | Twenty tight-loop writes produced exactly one distinct local `sent` frame. Host reached the value in 0.153914 s and stayed unchanged for a five-second soak. |
| N4 | PASS | A changed second send exited non-zero with `status=skipped-exists`; remote SHA-256 stayed `f73c0b7c…`. `--overwrite` exited 0 and changed it to the edited local SHA-256 `72f5a7456119afa4926bc9f3d9ee1cee9c81ca916f72fb9b9b7fd7cc83822543`. |
| N5 | PASS | `get missing-final-1785381325677841000.txt` exited 1 with `status=failed reason=missing`; no local file was created. |
| N6 | PASS | Port 65534 on `127.0.0.2` was verified closed. With both endpoint overrides set to that host and port, `diarchy-file list` exited 1 with `Host is unreachable on LAN and Tailscale`; no transfer occurred. |
| N7 | PASS | A 90 KiB `pbcopy` followed immediately by `diarchy-kvm off` completed in 0.351886 s. The runtime directory was absent, local and remote bridge counts were 0, and no crash report appeared. |
| N8 | PASS | Two host installer runs and two client installer runs produced byte-identical `[ok]` stdout. Protected Mac SHA-256 stayed `1282a9d9918b9ad4f05bf5eeb05fa4ab4607d1d0fba2be16bbbd489850e782f4`; host stayed `fac1373ddd006a8f43a5d0bbdb7bd8e89d64f5f556c14dcce3df19a35ea4e9b8`. Both pre-existing KVM doctors passed while enabled, and the KVM was returned to disabled. |
| N9 | PASS | With forced Tailscale host `127.0.0.2` and verified-closed port 65534, the command exited 1 with `forced Tailscale route is unreachable`; stdout was empty and no transfer occurred. |

## Additional invariant evidence

- Directory send preserved the requested root layout: `diarchy-e2e-final-dir/nested.txt` existed exactly once remotely with matching SHA-256 `2fa604c68f3dd81f77a0fd3032b9dd1ca83f722814068d16e3c666cd3d059177`.
- Post-transfer scans found no `.rsync-partial` or `.diarchy-file-*` residue in either drop tree.
- A 134.065 s installed-system privacy exercise transmitted 20 clipboard changes and five files; only recipient drop-folder files changed, with no clipboard body in logs, caches, or history. The final bridge rerun separately verified that its opt-in diagnostic file is mode `0600`, bounded by deterministic coverage, and contains only decision, direction, byte count, and digest prefix.
- A final installed-system KVM on/off cycle added no TCP listener on either machine. Off-before, on, and off-after snapshots were byte-identical; final hashes were Mac `467ea39ebea65c2dc44638f78f5711ea75045469cd814a31c44cfe03da017f20` and the host `0f67ec1e88a12df738937217bb6c428d1f835a28fd3fd2471760f39811450d54`.
- After runtime SSH side-effect hardening, the physical Mac client reinstalled successfully, its doctor returned only `[ok]` lines, the transfer CLI returned the expected machine-readable record, and KVM teardown remained disabled. At that observation point the route was Tailscale; the later P8 repair re-established LAN preference.
- Reopened-lid retest: Cua's display prerequisite changed from zero to two shareable displays, enabling the literal P5 drag. A later network repair moved host to the specified `192.0.2.10/24`; P8 then passed over `en0` even with bounded ICMP deliberately silent.
- Final KVM state: disabled; the Lan Mouse socket was absent, the host UI was hidden, and both file-portal process counts were zero.
- Final deterministic suite and artifact-gate identifiers are recorded in the verification section below.

## Edge file-portal and hardening matrix

| Check | Verdict | Physical observation |
| --- | --- | --- |
| Mac → host file and directory | PASS | Native portal ingress transferred a regular file and a nested directory; destination bytes, SHA-256 values, tree shape, and normalized modes matched. |
| Host → Mac file and directory | PASS | The host edge portal staged a regular file and nested directory; the receiver published exact bytes, SHA-256 values, tree shape, and normalized modes under `~/Drop/from-host`. |
| Collision handling | PASS | Existing destinations on both machines remained unchanged and the new items were rejected without merge or overwrite. |
| Unsafe source types | PASS | Symlink and FIFO inputs were rejected; no final destination or staging residue was published. |
| Full KVM lifecycle | PASS | `on` started the mandatory Lan Mouse, clipboard, receiver, and Mac portal stack; `off` removed every owned runtime. Final state was disabled, with no socket and zero portal processes. |
| Delayed host staging | PASS | A deliberately delayed staging response completed after 11.001 s without an early timeout or duplicate request. |
| Drop bound and disconnect | PASS | The Mac portal enforced the 32-item maximum. A pending transfer interrupted by disconnect produced a failure notification rather than a false success. |
| Host installer rollback | PASS | Injected portal-enable failure restored exact managed-file hashes, file modes, and the prior service enabled/active state. |
| Mac edge hover appearance | INCONCLUSIVE | Source geometry and typecheck verify the 3 px trigger, 52/72 px reveal widths, 500 ms hide delay, nonactivating panel, and accessory policy. A fresh cursor-dependent screenshot/hover was not attempted because the user explicitly asked that the cursor not be moved while they worked. |
| Host hidden idle portal regression | PASS | After reinstall and service restart, `hyprctl layers -j` reported the live `gtk-layer-shell` portal at `3 × 180` on the right edge, replacing the prior permanent 52-pixel labeled tab. |
| Mac external-drag reveal regression | INCONCLUSIVE | Source/typecheck and focused tests verify a global `.leftMouseDragged`/`.leftMouseUp` monitor, invisible 120-pixel drag-only activation band, 72-pixel reveal, and monitor teardown. Physical drag automation was stopped because the user explicitly asked that the cursor not be moved while they worked. |
| Mac protected-folder handoff | PASS (headless) | The failed `report.pdf` drops were traced without GUI interaction to three kernel `System Policy: Python ... deny(1) file-read-data` events for the protected source parent. The corrected AppKit-side staging path then copied a new fixture from `~/Documents` into a private temporary directory, invoked the installed sender on the staged path, and published it under `~/Drop/from-mac/`; local and remote SHA-256 both equaled `99c8c86008a53fe6e99f0699a9d257e02ac155d062d3c954ea00cba253fc9a4b`. The uniquely named fixture and remote result were removed. A fresh literal drag remains covered by the separate INCONCLUSIVE row because cursor movement is prohibited while the user works. |
| Host drag reveal regression | INCONCLUSIVE | Source and focused tests verify the 3/52/72-pixel state machine and 500 ms delayed collapse. A fresh physical drag was not performed because it requires cursor movement prohibited by the user during this run. |

## Final verification

- Deterministic suite after the protected-folder staging fix: `python3 -m unittest discover -s tests` — 245 tests passed in 175.680 s. The 44-test portal/sender/installer subset passed in 76.231 s, and production Swift/AppKit optimized compilation passed.
- The corrected Mac portal was installed atomically. After verifying the stale portal PID's exact executable, only that accessory process was terminated; the idempotent `diarchy-kvm on` path launched the installed binary and refreshed its managed PID without toggling Lan Mouse, moving the cursor, or taking focus. Final status was `enabled (clipboard bridge: on, file portals: on)`.
- Prior artifact smoke gate: `<run-id>` — 4 passed, 0 failed, 0 critical regressions, privacy `sanitized`; report `evals/reports/<run-id>.md`.
- The regression contract compiles and executes the real Swift staging helper. It verifies an isolated parent, exact file and nested-directory bytes, preservation of top-level and nested symlinks for downstream rejection, and removal of the temporary tree.
- The live host layer and deterministic regressions were verified without moving the user's cursor. Fresh Mac → host and the host → Mac edge drags remain explicitly `INCONCLUSIVE` until the user is no longer actively working and permits cursor-dependent verification.
