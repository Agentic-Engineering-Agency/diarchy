# Team T4 — streaming

Team id: `t4`. Branch: `feat/diarchy-app--streaming`.

## Mission

Make streaming generic (any Omarchy host with a hardware encoder, any Mac display settings), light on the Mac, and
pairable from the app. Spec sections: §2, §6.2 `stream *` rows, §9 (all), §10, §11, §12.

## Ownership (edit only these)

- `host/diarchy_host/stream.py` (new; module inside t2's package)
- `host/install.sh`, `host/sunshine`, `host/sunshine.conf`, `host/apps.json`, `host/diarchy-display`,
  `host/diarchy-host-doctor`, `host/diarchy-check-sunshine-log`,
  `host/diarchy-check-sunshine-service`, `host/systemd/diarchy-display.service`,
  `host/systemd/diarchy-sunshine.service`
- `client/diarchy-stream`, `client/diarchy-client-doctor`, `client/install.sh`,
  `client/Diarchy.applescript` (delete)
- Tests: `tests/test_host.py`, `tests/test_display.py`, `tests/test_client.py`, new `tests/test_diarchy_host_stream.py`
- `docs/superpowers/plans/2026-10-11-diarchy-app-t4-streaming.md`,
  `docs/superpowers/reports/2026-10-11-t4-streaming.md`

NOT yours: other `host/diarchy_host/*` modules (t2), other `client/*` (t1), `app/` (t3).

## Dependencies (wait for broadcasts; do independent work meanwhile)

- `MERGE LOADER <hash>` from t1 → then make `client/diarchy-stream` and its doctor load `diarchy-config.sh`
  (installed next to them) and drop hardcoded hosts.
- `MERGE AGENT-SKELETON <hash>` from t2 → then implement `diarchy_host/stream.py` exposing
  `handle(args: list[str]) -> dict` using `diarchy_host.common` (`AgentError`, `run`, `systemctl_user`,
  `unit_active`, `UNIT_*` constants). Until then work on Sunshine/encoder/log checker and Moonlight launcher logic.

## Stages

1. a docs-research agent: verify (a) Moonlight Qt 6.1.0 CLI `pair` syntax and `--pin` support and exit codes, (b) Sunshine's
   `/api/pin` request shape, auth (basic auth with UI credentials), and where `sunshine --creds` stores state, (c)
   Sunshine log markers for VA-API vs NVENC selection. Record sources in your plan. If (a) or (b) do not exist as the
   spec assumes, message Main before designing an alternative.
2. Host: remove the NVENC pin; log checker accepts NVENC or VA-API (and still rejects software and capture
   failures); `diarchy-host-doctor` takes the controller address as its first argument.
3. Client: `diarchy-stream` per §9.1 (config-driven resolution/FPS/overlay, profiles, hardware decoder);
   `client/install.sh` installs Moonlight + commands only, no AppleScript launcher; delete
   `client/Diarchy.applescript`; update `diarchy-client-doctor` (no hardcoded hosts, launcher check
   keeps bundle id `dev.sebastiangmz.diarchy`).
4. `stream.py`: `start` (stops `UNIT_KVM` first), `stop`, `status` (encoder from the current invocation log via the
   checker), `pair --pin NNNN --name NAME` (validate `^[0-9]{4}$`; name limited to `^[A-Za-z0-9 ._-]{1,40}$`;
   retry up to 20 s; map failures to `sunshine_unreachable` / `pair_failed`). Env overrides for every binary and
   for the Sunshine base URL (`DIARCHY_SUNSHINE_URL`, default `https://localhost:47990`) so tests use a local fake
   HTTPS-free server or a fake `curl`.
5. Verification + report.

## Acceptance

- Owned tests pass; full suite run once at the end.
- `grep -rnE '192\.168\.0\.(125|107)|100\.106\.228\.87|nvenc' client/diarchy-stream client/install.sh host/install.sh host/sunshine.conf`
  shows no hardcoded address and no forced NVENC encoder setting.
- Moonlight is launched with `--video-decoder hardware` always and `--performance-overlay` only when overlay is 1.
