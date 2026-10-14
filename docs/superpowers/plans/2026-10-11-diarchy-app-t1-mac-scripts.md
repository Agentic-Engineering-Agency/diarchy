# Diarchy App — T1 mac-scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Mac-side bash script generic and configured from `diarchy.conf`, and add the Mac commands the app calls (`diarchy-ssh`, `diarchy-kvm-configure`, `diarchy-mac-fingerprint`, loader library).

**Architecture:** One strict sourced bash loader (`client/diarchy-config.sh`) is the only reader of `diarchy.conf`. `diarchy-route.sh` calls it and builds ssh options from config; `diarchy-ssh` is a thin exec wrapper over the route library. `diarchy-kvm-configure` renders the Lan Mouse config from config. The client Lan Mouse installer stops writing config / building the toggle app. Existing scripts keep their behavior contracts (ADR 0001).

**Tech Stack:** bash 3.2 (macOS `/bin/bash` — no associative arrays, no namerefs, no `${v^^}`), Python 3 `unittest` (stdlib), fake executables on `PATH`, temp `HOME`.

**Spec:** `docs/superpowers/specs/2026-10-11-diarchy-app-design.md` (§2, §4, §5, §10, §12); team brief `docs/superpowers/teams/t1-mac-scripts.md`; rules `docs/superpowers/teams/00-shared-rules.md`; ADR 0001/0002.

## Global Constraints

- Config path `${DIARCHY_CONFIG:-$HOME/.config/diarchy/diarchy.conf}`; format §4.1: first line exactly `# Managed by Diarchy`, `KEY=VALUE`, no spaces/quotes/inline comments, unknown or duplicate key = error, LF only. The file is NEVER `source`d/`eval`ed.
- Keys/defaults/validation exactly §4.2 (table in Task 1). HOST = `^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$`; FP = `^([0-9A-F]{2}:){31}[0-9A-F]{2}$` (uppercase only in the loader).
- Loader prints exactly one `diarchy-config: <reason>` line to stderr and `return 1` (never `exit`); exports all §4.2 keys only on success.
- Every new/changed command: `set -euo pipefail`, failure line `<command>: <reason>`.
- Never `sudo`; never contact the real Host; tests use fakes. Python stdlib only; no new third-party deps. Code/comments/logs/commits in English.
- No product-code occurrences of `192.0.2.10`, `192.0.2.20`, `100.64.0.10`, user `user`, key paths. Fixtures use `192.0.2.x`, `198.51.100.x`, `100.64.0.x`.
- Edit ONLY files in the team ownership list. NOT ours: `client/diarchy-stream`, `client/diarchy-client-doctor`, `client/install.sh`, `client/Diarchy.applescript`, `host/`, `app/`.
- Commit on branch `feat/diarchy-app--mac-scripts` with explicit paths; never push/merge/rebase.
- Test style: copy `tests/test_route_selection.py` (fake executables via env `*_BIN`, temp dirs). Run owned tests only: `python3 -m unittest tests.test_x -v`; full suite once at the end.

## Review Focus

- Config value `$(touch /tmp/x)` / backticks / `;` in a value must never execute and must fail validation (Task 1 test).
- File with CRLF, BOM, trailing whitespace, missing trailing newline, or blank first line (Task 1 test: exact reason; missing trailing newline must still parse the last key).
- Empty env var (`DIARCHY_LAN_HOST=`) must NOT override a file value ("non-empty wins") (Task 1 test).
- Loader failing mid-parse must not leave partially exported keys (Task 1 test).
- `diarchy-ssh` with remote words containing spaces/quotes/`;` passes them through as single argv entries untouched; stdin passthrough; failure output first stderr line is the actual reason, not a `route=` diagnostic (Task 3 test).
- LAN-only / Tailscale-only configs: auto route skips the empty candidate, forced route to an unset side fails with a clear reason (Task 2 test).
- `diarchy-kvm-configure` on a managed file re-run is idempotent; unmanaged file backed up exactly once per run with distinct UTC timestamp; mode 0600 even if umask is lax (Task 4 test).
- `diarchy-mac-fingerprint` output uppercase FP form even if openssl prints lowercase; LibreSSL `SHA256 Fingerprint=` prefix handled (Task 4 test).
- Installer on a fresh Mac with NO config must succeed (checklist step 1 runs before config exists) (Task 6 test).

---

## File structure

| File | Responsibility |
|---|---|
| `client/diarchy-config.sh` (new) | `diarchy_config_load` strict loader; sourced library |
| `client/diarchy-route.sh` | route selection; loads config; builds `diarchy_ssh_options` |
| `client/diarchy-ssh` (new) | `exec ssh` with route options; ssh entry point for the app |
| `client/diarchy-kvm-configure` (new) | Render `~/.config/lan-mouse/config.toml` |
| `client/diarchy-mac-fingerprint` (new) | Print Mac Lan Mouse FP |
| `client/install-lan-mouse.sh`, `diarchy-kvm-client-doctor`, `install-clipboard-file-transfer.sh`, `diarchy-clipboard-file-transfer-client-doctor` | generalization / install new commands |
| `client/Diarchy KVM.applescript` | deleted |

## Reasons catalog (shared with Swift `ConfigCodec`; exact text after `diarchy-config: `)

```
Diarchy is not configured (open the app to set it up)
cannot read configuration file: <path>
first line must be "# Managed by Diarchy"
line <N>: carriage returns are not allowed
line <N>: expected KEY=VALUE
line <N>: unknown key <KEY>
line <N>: duplicate key <KEY>
line <N>: quoted values are not allowed
<KEY> is required                      (DIARCHY_SSH_USER)
<KEY> is invalid                       (any validation failure; value is never echoed)
at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required
```

---

### Task 1: Config loader (stage 1 — Main waits on this)

**Files:**
- Create: `client/diarchy-config.sh`
- Test: `tests/test_config_loader.py`

**Interfaces:**
- Produces: sourced function `diarchy_config_load` (no args; returns 0/1; exports `DIARCHY_SSH_USER DIARCHY_LAN_HOST DIARCHY_TAILSCALE_HOST DIARCHY_SSH_PORT DIARCHY_SSH_IDENTITY_FILE DIARCHY_HOST_KEY_ALIAS DIARCHY_KVM_SIDE DIARCHY_HOST_FINGERPRINT DIARCHY_STREAM_RESOLUTION DIARCHY_STREAM_FPS DIARCHY_STREAM_OVERLAY`). Library file has no side effects when sourced.

Defaults: SSH_PORT `22`, KVM_SIDE `left`, STREAM_RESOLUTION `1920x1080`, STREAM_FPS `60`, STREAM_OVERLAY `0`; others empty; HOST_KEY_ALIAS = Tailscale host if set else LAN host. Validation: SSH_USER `^[a-z_][a-z0-9_-]{0,31}$` (required); LAN/TAILSCALE/ALIAS empty or HOST; PORT integer 1–65535 (no leading zeros issue: use `^[0-9]{1,5}$` then numeric range, reject `0`); IDENTITY empty or `^/[^\n]*$` with no `..` path segment (split on `/`); SIDE in left|right|top|bottom; FP empty or FP; RESOLUTION `^[0-9]{3,4}x[0-9]{3,4}$`; FPS int 30–240; OVERLAY 0|1.

- [ ] **Step 1: Write failing tests** in `tests/test_config_loader.py`. Harness: write a tiny driver `bash -c 'source "$LOADER"; diarchy_config_load; rc=$?; env | sort'` run with `env -i` + `PATH`, `HOME=<temp>`, optional `DIARCHY_CONFIG`; assert rc, stderr (exactly one line), exported values. Cases (each its own test method, exact reason lines from the catalog):
  - missing file, no env → `not configured` reason, rc 1
  - missing file + env `DIARCHY_SSH_USER`+`DIARCHY_LAN_HOST` → rc 0, defaults applied; env with user but no host → not-configured reason
  - unreadable file (mode 000) → `cannot read configuration file: <path>`
  - header wrong / blank first line / BOM → first-line reason
  - CRLF → `line 2: carriage returns are not allowed`
  - missing-trailing-newline file parses
  - `KEY =x`, `=x`, `junk` → `expected KEY=VALUE`; unknown key; duplicate key (assert line number of 2nd occurrence); quoted `"x"` and `'x'` values
  - comment lines (`# ...`) and blank lines ignored
  - one failure test per key (SSH_USER missing/`Root`/33 chars; LAN host `-a`, `a_b`, 254 chars; PORT `0`,`65536`,`22a`; IDENTITY relative, with `/../`, empty OK; ALIAS; SIDE `middle`; FP lowercase, wrong length; RESOLUTION `1920*1080`, `99x99`; FPS `29`,`241`; OVERLAY `2`) → `<KEY> is invalid`
  - neither host → at-least-one reason
  - HOST_KEY_ALIAS default rule: both → tailscale; LAN only → LAN; explicit wins
  - env precedence: non-empty env overrides file value; empty env does not
  - injection: value `$(touch MARKER)` and `` `touch MARKER` `` → `<KEY> is invalid`, MARKER absent; a valid file never creates files
  - no partial export on failure: after rc 1, none of the 11 keys exported (in a subshell that pre-sets none)
  - function uses `return` not `exit`: driver continues after failure (prints sentinel)
- [ ] **Step 2: Run** `python3 -m unittest tests.test_config_loader -v` → FAIL (file missing).
- [ ] **Step 3: Implement** `client/diarchy-config.sh`. Structure: key list in table order; `_hlc_fail reason` prints `diarchy-config: reason` to stderr, returns 1. Parse with `while IFS= read -r line || [[ -n $line ]]` over the file, line counter; line 1 must equal header exactly; reject `$'\r'`; skip blank and `#` lines; match `^([A-Z0-9_]+)=(.*)$` via `[[ =~ ]]` (store regex in a variable — bash 3.2); unknown key check against list; duplicate via `_hlc_seen_<KEY>`; any `"` or `'` in value → quoted reason; store file values with `printf -v "_hlc_file_$key" '%s'`. Effective value = env if non-empty (`${!key:-}`) else file else default. Validate with bash `[[ =~ ]]` using regexes in variables (never `eval`). Work only in `_hlc_*` locals; export all keys only after every check passes. Error ordering: parse errors in file order, then SSH_USER required/invalid, then per-key validation in table order, then at-least-one-host.
- [ ] **Step 4: Run tests** → PASS. Also `bash --noprofile --norc -n client/diarchy-config.sh` and `/bin/bash` (3.2) is used by the driver.
- [ ] **Step 5: Commit** `git add client/diarchy-config.sh tests/test_config_loader.py && git commit -m "feat(client): add strict diarchy.conf bash loader"`; then `send to the orchestrator: "[t1→Main] READY LOADER <hash>"`.

---

### Task 2: Generalize `diarchy-route.sh` + migrate route test

**Files:**
- Modify: `client/diarchy-route.sh`, `tests/test_route_selection.py`

**Interfaces:**
- Consumes: `diarchy_config_load` (Task 1).
- Produces: `select_diarchy_route auto|lan|tailscale` loads config itself (library has no import-time side effects), then as before sets `DIARCHY_ROUTE_NAME DIARCHY_ROUTE_HOST DIARCHY_ROUTE_INTERFACE DIARCHY_ROUTE_LOSS_PCT DIARCHY_ROUTE_MEDIAN_MS` and array `diarchy_ssh_options`; destination token `DIARCHY_SSH_ALIAS` default `diarchy`. Config loader path: `${DIARCHY_CONFIG_SH:-<dir of diarchy-route.sh>/diarchy-config.sh}`.
- `diarchy_ssh_options` content (order): existing `ControlMaster=no ControlPath=none PermitLocalCommand=no ClearAllForwardings=yes`, `User=$DIARCHY_SSH_USER`, `HostKeyAlias=$DIARCHY_HOST_KEY_ALIAS`, `HostName=$DIARCHY_ROUTE_HOST`, `Port=$DIARCHY_SSH_PORT`, and, only when `DIARCHY_SSH_IDENTITY_FILE` non-empty, `IdentityFile=<path>` and `IdentitiesOnly=yes`. Each `-o "Key=Value"` as two array entries (as now).

- [ ] **Step 1: Failing tests.** Rewrite fixtures: `LAN_HOST="192.0.2.10"`, `TAILSCALE_HOST="100.64.0.10"`, write a temp `diarchy.conf` per run (`DIARCHY_CONFIG`), keep scenarios. Add: LAN-only config (tailscale scenario skipped, `auto` with LAN down → unreachable reason), Tailscale-only config (`auto` never probes LAN: fake ping logs hosts), forced `lan` with empty LAN → `diarchy-route: forced LAN route is not configured` (Ruling R1), forced `tailscale` likewise; options array contains `User=`, `HostKeyAlias=<alias>`, no `IdentityFile` when unset, `IdentityFile=`+`IdentitiesOnly=yes` when set; custom `DIARCHY_SSH_PORT` flows to nc and `Port=`; missing config → `diarchy-config: ...` line and rc 1; the existing `route=… host=…` stderr line unchanged.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** Remove the three hardcoded host defaults and `DIARCHY_SSH_PORT` default; source the loader lazily inside `select_diarchy_route`; skip probing when host empty; replace `HostKeyAlias=$DIARCHY_TAILSCALE_HOST`; add User/Identity options; alias default `diarchy-stream`.
- [ ] **Step 4:** `python3 -m unittest tests.test_route_selection -v` → PASS.
- [ ] **Step 5: Commit** `feat(client): route selection reads diarchy.conf` (paths: the two files).

---

### Task 3: `diarchy-ssh`

**Files:**
- Create: `client/diarchy-ssh`
- Test: `tests/test_diarchy_ssh.py`

**Interfaces:**
- Consumes: `select_diarchy_route`, `diarchy_ssh_options`, `DIARCHY_SSH_ALIAS` (Task 2). Env overrides for tests: `SSH_BIN` (default `/usr/bin/ssh`), `DIARCHY_ROUTE_SH` (default `<dir>/diarchy-route.sh`), plus the route fakes (`PING_BIN`, `ROUTE_BIN`, `CONNECT_BIN`).
- Produces: CLI `diarchy-ssh [--route auto|lan|tailscale] -- <remote words...>`: argv to ssh = `diarchy_ssh_options… -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "$DIARCHY_SSH_ALIAS" "$@"`; `exec`; stdin passes through. Exit 255 on usage error, config failure, route failure (print the failing line(s) to stderr); ssh's own exit otherwise.

Rulings: R2 — the route diagnostic line (`route=… host=…`) is captured and discarded on success and printed only on failure, so the app's "stderr first line" is the real error. R3 — usage error exits 64 with `diarchy-ssh: usage: ...` (a missing `--` counts as usage error).
Main contract (t3, 2026-10-11): on route failure `diarchy-ssh` exits 255 and stderr contains the route reason (contains `unreachable`); ssh's own stderr is NEVER suppressed or altered (so `Permission denied (publickey)` reaches the app); only the success-path `route=…` diagnostic from the route library is dropped (R2).

- [ ] **Step 1: Failing tests.** Fake `ssh` records argv NUL-separated and stdin to files. Cases: argv contains `User=…`, `HostKeyAlias=…`, `BatchMode=yes`, `ConnectTimeout=5`, `StrictHostKeyChecking=accept-new`, `HostName=<lan>`, `Port=`; `IdentityFile=…`+`IdentitiesOnly=yes` only when config sets it; `--route tailscale` picks Tailscale host; remote words with spaces/quotes/`;` arrive as the exact trailing argv entries; stdin bytes reach ssh; ssh exit code 7 propagated; all routes down → exit 255 and stderr is exactly `diarchy-route: Host is unreachable on LAN and Tailscale`; no config → exit 255 with the `diarchy-config:` line and no `route=` noise; no `--` → exit 64; invalid `--route x`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** (`#!/usr/bin/env bash`, `set -euo pipefail`; `source "$route_sh"`; `diag=$(select_diarchy_route "$mode" 2>&1)`; on rc≠0 print `$diag` and `exit 255`; `exec "$ssh_bin" "${diarchy_ssh_options[@]}" -o … "$DIARCHY_SSH_ALIAS" "$@"`). Mode 0755 via `chmod`.
- [ ] **Step 4:** `python3 -m unittest tests.test_diarchy_ssh -v` → PASS.
- [ ] **Step 5: Commit** `feat(client): add diarchy-ssh entry point`.

---

### Task 4: `diarchy-kvm-configure` and `diarchy-mac-fingerprint`

**Files:**
- Create: `client/diarchy-kvm-configure`, `client/diarchy-mac-fingerprint`
- Test: `tests/test_kvm_configure.py`, `tests/test_mac_fingerprint.py`

**Interfaces:**
- Consumes: `diarchy_config_load` via `${DIARCHY_CONFIG_SH:-<dir>/diarchy-config.sh}`.
- `diarchy-kvm-configure` produces `${LAN_MOUSE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/lan-mouse/config.toml}` (dir mode 0700 created if absent, file 0600 enforced with `chmod` after atomic `mktemp`+`mv` in the same dir) with exactly (blank lines as shown):

```
# Managed by diarchy
release_bind = ["KeyA", "KeyS", "KeyD", "KeyF"]
port = 4242

[authorized_fingerprints]
"<fp lowercase>" = "host"            # only when DIARCHY_HOST_FINGERPRINT non-empty

[[clients]]
position = "<side>"
hostname = "host"
activate_on_startup = true
ips = ["<LAN>", "<Tailscale>"]   # non-empty ones only, LAN first
port = 4242
```
(Main ruling 2026-10-11, spec §4.2: the fingerprint in `config.toml` is written LOWERCASE; `diarchy.conf` keeps uppercase; add a test with an uppercase config FP expecting lowercase in the toml. Doctors compare fingerprints case-insensitively.)
  Unmanaged pre-existing file (first line ≠ `# Managed by diarchy`) → `config.toml.backup.<UTC %Y%m%dT%H%M%SZ>` (0600) first, plus `[info] Preserved existing Lan Mouse config at <path>`. Final stdout line `[ok] Lan Mouse configuration written (side: <side>)`. Config failure → loader line, exit 1.
- `diarchy-mac-fingerprint` prints one line FP (uppercase) from `${LAN_MOUSE_CERT:-<config dir>/lan-mouse.pem}` using `${OPENSSL_BIN:-/usr/bin/openssl} x509 -in <pem> -noout -fingerprint -sha256`; value after the first `=`, uppercased, validated against FP; absent cert → `diarchy-mac-fingerprint: Lan Mouse certificate not found; start Lan Mouse once` exit 1; openssl failure/garbage → `diarchy-mac-fingerprint: Lan Mouse certificate is invalid` exit 1. Does NOT call the loader.

- [ ] **Step 1: Failing tests.** kvm-configure: byte-exact rendering for each side × {both hosts, LAN only, Tailscale only} × {with FP, without FP}; mode 0600 under `umask 000`; idempotent second run (no backup created for managed file); unmanaged backup (content preserved, mode 0600, one backup); two unmanaged runs within one second do not clobber (backup name uniqueness — suffix `.1` if exists; Ruling R4); missing config → loader reason, target untouched; no temp files left in the dir. mac-fingerprint: LibreSSL (`SHA256 Fingerprint=aa:..`) and OpenSSL (`sha256 Fingerprint=AA:..`) forms; missing pem; openssl nonzero.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** both (chmod 0755).
- [ ] **Step 4:** `python3 -m unittest tests.test_kvm_configure tests.test_mac_fingerprint -v` → PASS.
- [ ] **Step 5: Commit** `feat(client): add diarchy-kvm-configure and diarchy-mac-fingerprint`.

---

### Task 5: Migrate route consumers and their tests (clipboard, file, portals, doctor hash)

**Files:**
- Modify: `client/diarchy-clipboard-bridge`, `client/diarchy-file`, `client/diarchy-file-portal-receiver`, `client/diarchy-file-portal-send` (only if they reference removed route defaults/alias), `client/diarchy-clipboard-file-transfer-client-doctor`, `tests/test_file_transfer.py`, `tests/test_clipboard_bridge.py`, `tests/test_file_portal_receiver.py`, `tests/test_file_portal_send.py`, `tests/test_clipboard_file_transfer_install.py` (route/doctor portions only)

**Interfaces:**
- Consumes: Task 2 route contract (loader invoked inside `select_diarchy_route`; `DIARCHY_SSH_ALIAS` default `diarchy`).
- Produces: all consumers work unchanged against the new route; doctor's `EXPECTED_ROUTE_SHA256` equals `shasum -a 256 client/diarchy-route.sh`; doctor reads hosts from config and sources the route through the config path.

- [ ] **Step 1:** Run the five owned route-driving tests now → record failures (expected: IP fixtures / missing config). Update each test to write a temp `diarchy.conf` (`DIARCHY_CONFIG`) with RFC 5737 / `100.64.0.x` fixture values; former hardcoded IP assertions become fixture values. No assertion weakening.
- [ ] **Step 2:** Fix consumer code only where the tests prove a real incompatibility (e.g. scripts that sourced the route and read `$DIARCHY_LAN_HOST` before `select_diarchy_route`, or hardcoded `diarchy` alias expectations).
- [ ] **Step 3:** Recompute and update `EXPECTED_ROUTE_SHA256` in the clipboard doctor; keep its test green (the test must compute the expected hash from the file, not pin a literal — if it pins a literal, change it to compute).
- [ ] **Step 4:** `python3 -m unittest tests.test_file_transfer tests.test_clipboard_bridge tests.test_file_portal_receiver tests.test_file_portal_send tests.test_clipboard_file_transfer_install -v` → PASS.
- [ ] **Step 5: Commit** `refactor(client): clipboard and file portals use config-driven route`.

---

### Task 6: Installers, KVM doctor, delete AppleScript KVM toggle

**Files:**
- Modify: `client/install-lan-mouse.sh`, `client/diarchy-kvm-client-doctor`, `client/install-clipboard-file-transfer.sh`, `client/diarchy-kvm`, `client/diarchy-kvm-accessibility-check` (only if tests/greps require), `tests/test_lan_mouse_client.py`, `tests/test_diarchy_kvm_hook.py`, `tests/test_clipboard_file_transfer_install.py` (installer portions)
- Delete: `client/Diarchy KVM.applescript`

**Interfaces:**
- Consumes: `diarchy-kvm-configure` (Task 4), loader (Task 1).
- `install-lan-mouse.sh` (client): keeps pinned download/verify/Lan Mouse.app install; installs `diarchy-kvm`, `diarchy-kvm-client-doctor`, `diarchy-kvm-accessibility-check`, `diarchy-config.sh`, `diarchy-ssh`, `diarchy-kvm-configure`, `diarchy-mac-fingerprint` (and `diarchy-route.sh`, which `diarchy-ssh` sources) into `$BIN_DIR` mode 0755; no `osacompile`, no toggle bundle; removes `$APPLICATIONS_DIR/Diarchy KVM.app` only when its bundle id is `dev.sebastiangmz.diarchy-kvm` (foreign bundle: leave in place, print `[info]`, no failure — Ruling R5); after install, if `DIARCHY_CONFIG`/default config loads (subshell `source diarchy-config.sh; diarchy_config_load`), runs installed `diarchy-kvm-configure`; otherwise prints `[info] Not configured yet; open Diarchy to create diarchy.conf` and succeeds. `--authorize-fingerprint` is removed (usage exit 64) — host fingerprint now lives in `DIARCHY_HOST_FINGERPRINT` (Ruling R6). `--dry-run` plan text updated (no toggle app; mentions new commands). `OSACOMPILE_BIN` dependency dropped.
- `diarchy-kvm-client-doctor`: loads config (failure → `[fail] <loader reason>`); expects `position = "<DIARCHY_KVM_SIDE>"`, ips line built exactly as `diarchy-kvm-configure` renders it, route detection over configured LAN/Tailscale hosts (skip empty); fingerprint label stays `"host"`; messages `trusted <side>-edge client configuration`.
(Doctor fingerprint check is case-insensitive; keep the `"host"` label.)
- `install-clipboard-file-transfer.sh`: also installs `diarchy-config.sh`, `diarchy-ssh` (plus keeps `diarchy-route.sh`). SSH preflight no longer uses a personal alias: it runs only when the config loads (via the installed/source loader + route, argv as `diarchy-ssh`) and otherwise prints `[info]` and continues — checklist step 1 runs before config exists (Ruling R7).

- [ ] **Step 1: Failing tests.** Update `tests/test_lan_mouse_client.py`: no Diarchy KVM.app build, owned legacy app removed / foreign kept, new commands installed (modes), config rendered only via `diarchy-kvm-configure` when config loads, no config → success + info line, `--authorize-fingerprint` → exit 64, dry-run text; drop osacompile fakes. Update doctor tests: per-side expectations (all four sides), LAN-only and Tailscale-only ips line, `[fail]` when config missing. Update `tests/test_diarchy_kvm_hook.py` and installer tests where they referenced IPs, the toggle app, or the preflight alias.
- [ ] **Step 2:** run the three test modules → FAIL where expected.
- [ ] **Step 3: Implement** per the interfaces; `git rm "client/Diarchy KVM.applescript"`.
- [ ] **Step 4:** `python3 -m unittest tests.test_lan_mouse_client tests.test_diarchy_kvm_hook tests.test_clipboard_file_transfer_install -v` → PASS; `grep -rnE '192\.168\.0\.(125|107)|100\.106\.228\.87' client/` prints only lines inside t4-owned files (`client/diarchy-stream`).
- [ ] **Step 5: Commit** `refactor(client): installers and KVM doctor driven by diarchy.conf`.

---

### Task 7: Verification and report

**Files:**
- Create: `docs/superpowers/reports/2026-10-11-t1-mac-scripts.md`

- [ ] **Step 1:** Run all owned test files by name, then once `python3 -m unittest discover -s tests`; paste real output. Failures in t4-owned tests caused by the loader (`tests/test_client.py` etc.) are reported to Main with exact test names, not fixed.
- [ ] **Step 2:** Run the acceptance grep and a manual smoke: `DIARCHY_CONFIG=<temp conf> SSH_BIN=<fake> diarchy-ssh --route lan -- true` against fakes; `diarchy-kvm-configure` against a temp `HOME`.
- [ ] **Step 3:** Write the report: deliverables vs. brief checklist, files changed, commands + output, every `Ruling:` (R1–R7 plus any new), deferred minors, items only physical verification can prove (real ssh with `StrictHostKeyChecking=accept-new`, real Lan Mouse cert via macOS LibreSSL, installer on a Mac without config).
- [ ] **Step 4: Commit**, then `send to the orchestrator: "[t1→Main] DONE <hash> report=docs/superpowers/reports/2026-10-11-t1-mac-scripts.md"`.

---

## Self-review

- Spec coverage: §4.3 loader → T1; §5 route/ssh/configure/fingerprint/doctors/install → T2–T6; §10 StrictHostKeyChecking/HostKeyAlias → T2/T3; §12 tests → every task; brief stages 1–5 map to T1, T2+T3+T5, T4, T6, T7.
- Interfaces consistent: `diarchy_config_load`, `select_diarchy_route`, `diarchy_ssh_options`, `DIARCHY_CONFIG_SH`, `DIARCHY_ROUTE_SH`, `SSH_BIN` names are used identically across tasks.
- Doc check of external CLIs: none new (openssl LibreSSL/OpenSSL output forms covered by fixtures); no docs-research agent needed.
- Rulings tracked: R1 forced route to unset side message; R2 route diagnostic suppressed on success in `diarchy-ssh`; R3 usage exit 64; R4 backup name uniqueness; R5 foreign Diarchy KVM.app left in place; R6 `--authorize-fingerprint` removed from client installer; R7 clipboard installer SSH preflight skipped without config.
