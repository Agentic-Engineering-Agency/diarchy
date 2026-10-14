# T4 Streaming — Final Report

Branch `feat/diarchy-app--streaming`, base `bbe4c90`. Merged from Main: `MERGE LOADER 0de47bb`, `MERGE AGENT-SKELETON 1c0a57f`.
Plan: `docs/superpowers/plans/2026-10-11-diarchy-app-t4-streaming.md`.

## Deliverables vs. brief

| Brief item | Status | Where |
|---|---|---|
| Stage 1 docs-research agent (Moonlight `pair`/`--pin`, Sunshine `/api/pin`, log markers) | Done; sources in plan "Sources" | plan |
| NVENC pin removed (`sunshine.conf`, `install.sh`) | Done | `host/sunshine.conf`, `host/install.sh` |
| Log checker accepts NVENC or VA-API, rejects software/capture failure/mixed/missing codecs | Done; prints family | `host/diarchy-check-sunshine-log` |
| Service checker `--encoder` | Done | `host/diarchy-check-sunshine-service` |
| `host-doctor` controller address as first argument | Done (validated, optional) | `host/diarchy-host-doctor` |
| `diarchy-stream` per §9.1 (config-driven, profiles, hardware decoder, overlay only if `1`) | Done | `client/diarchy-stream` |
| `client/install.sh` Moonlight + commands only; AppleScript deleted | Done (also installs `diarchy-config.sh`) | `client/install.sh`, `client/Diarchy.applescript` (deleted) |
| `diarchy-client-doctor` no hardcoded hosts, bundle id kept | Done (+ config load check) | `client/diarchy-client-doctor` |
| `stream.py`: start/stop/status/pair | Done | `host/diarchy_host/stream.py` |
| Tests | Done | `tests/test_host.py`, `tests/test_client.py`, `tests/test_diarchy_host_stream.py` (`tests/test_display.py` needed no change) |

## Files changed (t4-owned; 13 files, +909/−142 vs. base)

`host/diarchy_host/stream.py` (new), `host/install.sh`, `host/sunshine.conf`, `host/diarchy-host-doctor`, `host/diarchy-check-sunshine-log`, `host/diarchy-check-sunshine-service`, `client/diarchy-stream`, `client/diarchy-client-doctor`, `client/install.sh`, `client/Diarchy.applescript` (deleted), `tests/test_host.py`, `tests/test_client.py`, `tests/test_diarchy_host_stream.py` (new).

## Verification (real output)

```
$ python3 -m unittest tests.test_host tests.test_display tests.test_client tests.test_diarchy_host_stream
Ran 84 tests in 26.011s
OK

$ python3 -m unittest discover -s tests          # full suite, run once at the end
Ran 323 tests in 148.084s
OK

$ grep -rnE '192\.168\.0\.(125|107)|100\.106\.228\.87|nvenc' client/diarchy-stream client/install.sh host/install.sh host/sunshine.conf
(no output)   grep-exit=1

$ python3 -W error::ResourceWarning -m unittest tests.test_diarchy_host_stream
Ran 15 tests in 9.182s
OK
```

Final whole-branch review: no Critical/Important findings; one Minor (blank line at EOF of `client/install.sh:110`, deferred).

## Rulings

- Ruling: remove `encoder` from `sunshine.conf`; Sunshine auto-probes nvenc→vaapi and the log checker rejects `software` — cost if wrong: a host with software-only encoding fails install validation instead of streaming slowly.
- Ruling: checker requires BOTH H.264 and HEVC from the SAME family (`hevc_mode = 2` unchanged) — cost if wrong: a VA-API host without HEVC fails validation until `hevc_mode` is relaxed.
- Ruling: checker prints the family on success; the service checker prints the output name by default and the family only with `--encoder` — cost if wrong: one extra journal read in the doctor.
- Ruling: `host/install.sh` leaves both streaming units stopped and disabled after validation (`diarchy-kvm.service` and the streaming units are all `WantedBy=graphical-session.target`; leaving them enabled would run both input injectors after login, violating §2 exclusivity). The agent's `stream start|stop` owns the lifecycle — cost if wrong: users of the old always-on flow must start streaming from the app. Main/t2 should confirm `diarchy-host stream start` is the only start path.
- Ruling: `stream pair` uses `urllib` (stdlib), not `curl`, so the Web UI password never appears in argv; TLS verification disabled only for loopback https URLs — cost if wrong: none for the default config.
- Ruling: `diarchy-stream --diagnose` prints `host=unset` for an empty candidate host — cost if wrong: parsers of `--diagnose` (none known).
- Ruling: loader override env is `DIARCHY_CONFIG_SH` (Main, at MERGE LOADER) — cost if wrong: none.
- Ruling: `client/install.sh` also installs `diarchy-config.sh` (0644, same bytes as t1's) so the streaming install is self-sufficient — cost if wrong: duplicate install of the same file if t1's installer also copies it (idempotent).
- Ruling: install preflight accepts `/dev/dri/renderD*` or `/dev/nvidia0` as the hardware-encoder device evidence — cost if wrong: a host with a render node but no working encoder passes preflight and is caught by the log checker instead.
- Ruling: the LAN profile's default FPS is now the configured `DIARCHY_STREAM_FPS` (default 60), not the old hardcoded 75 — cost if wrong: users who relied on 75 fps on LAN must set 75 in config.
- Ruling: Sunshine `/api/pin` result is read from JSON `status`; HTTP 200 + `"false"` means "no Moonlight request pending yet" and is retried until the deadline (`DIARCHY_STREAM_PAIR_TIMEOUT`, default 20 s).

## Cross-team notes for Main / t3 (spec §9.3)

- docs-research agent verified Moonlight Qt 6.1.0: `Moonlight pair <host> [--pin NNNN]` exists, but the command is GUI-bound (`CliPair.qml`) and **exits 0 on success and on runtime failure** (only argument errors exit 1). Pairing success must therefore be decided by the host agent result `{"paired": true}`; the Moonlight exit code is not authoritative (§9.3 step 4 "both processes exit 0" should be updated). Behavior without a display (`QT_QPA_PLATFORM=offscreen`) is unverified.
- Sunshine's plaintext Web UI password is not retrievable from `sunshine_state.json`; the agent reads the sidecar `~/.config/sunshine/.diarchy-ui` written by `host/install.sh`. A host where Sunshine was configured by other means reports `not_installed` for `stream pair`.
- Sunshine's Web UI port is `port + 1` (default 47990); a custom `port` in `sunshine.conf` requires `DIARCHY_SUNSHINE_URL`.

## Deferred minors

- `client/install.sh:110`: extra blank line at EOF (`git diff --check`).
- `host/diarchy-check-sunshine-log` `Name:` / `Selected monitor` regexes still anchor with `$` directly (pre-existing); only the new encoder lines tolerate trailing whitespace/CRLF.
- Subagent model: the `task` tool exposes no model parameter, so subagents ran on the session default model; `reviewer`/`oracle`/`task` agent types failed (upstream auth error) and reviews were run on the `implementer` agent type in read-only mode.

## Only physical verification on a real Host can prove

- Sunshine actually selecting VA-API on a non-NVIDIA GPU and NVENC on NVIDIA (log markers come from upstream source, not from a real run).
- `Moonlight pair` end-to-end against Sunshine with the app-generated PIN (GUI-bound; exit-code behavior above).
- `uinput-access` and `lid-policy` required actions taking effect (shown by the agent probe, not exercised here).
- KVM ↔ Streaming exclusivity at login with both units disabled by `install.sh`.
- LAN/remote profile feel at configured resolution/FPS; hardware decoding on the Mac.
