# Diarchy — T4 Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make streaming generic (any hardware encoder, any Mac display settings from config), light on the Mac, and pairable from the app via a `diarchy_host.stream` agent module.

**Architecture:** Host shell helpers (log/service checkers, installer, doctor) are generalized from NVENC-only to NVENC-or-VA-API; the checker prints the encoder family so the agent can report it. The Mac launcher `client/diarchy-stream` keeps its probing and profile policy but reads hosts/resolution/FPS/overlay from `diarchy.conf` through t1's loader. `diarchy_host/stream.py` (t2's package) implements `stream start|stop|status|pair`.

**Tech Stack:** bash, Python 3.11 stdlib (`unittest`, `urllib`), fake executables on `PATH` + temp `HOME`.

**Spec:** `docs/superpowers/specs/2026-10-11-diarchy-app-design.md` §2, §6.2 (`stream *`), §9, §10, §11, §12. Brief: `docs/superpowers/teams/t4-streaming.md`. Shared rules: `docs/superpowers/teams/00-shared-rules.md`.

## Global Constraints

- Ownership (edit only): `host/diarchy_host/stream.py`, `host/install.sh`, `host/sunshine`, `host/sunshine.conf`, `host/apps.json`, `host/diarchy-display`, `host/diarchy-host-doctor`, `host/diarchy-check-sunshine-log`, `host/diarchy-check-sunshine-service`, `host/systemd/diarchy-display.service`, `host/systemd/diarchy-sunshine.service`, `client/diarchy-stream`, `client/diarchy-client-doctor`, `client/install.sh`, `client/Diarchy.applescript` (delete), `tests/test_host.py`, `tests/test_display.py`, `tests/test_client.py`, `tests/test_diarchy_host_stream.py`.
- Python stdlib only on the host; no SwiftPM/third-party deps. Python ≥ 3.11.
- Never `sudo`; never contact the real Host (`ssh`, `diarchy-ssh`); tests use fakes. Required actions are shown, never executed (ADR 0002).
- User-facing app text is English; code, comments, logs, commit messages are English.
- No hardcoded personal values (`192.0.2.10`, `192.0.2.20`, `100.64.0.10`, user `user`, key paths) in product code. Fixtures use RFC 5737 (`192.0.2.x`, `198.51.100.x`) and `100.64.0.x`.
- Moonlight launched with `--video-decoder hardware` always; `--performance-overlay` only when `DIARCHY_STREAM_OVERLAY=1`.
- Profiles: LAN HEVC 25000 kbps; remote HEVC 15000 kbps; degraded H.264 8000 kbps at `1280x720` and `min(FPS, 60)`; LAN/remote resolution+FPS from config (`DIARCHY_STREAM_RESOLUTION` default `1920x1080`, `DIARCHY_STREAM_FPS` default `60`).
- `stream pair`: PIN `^[0-9]{4}$`; name `^[A-Za-z0-9 ._-]{1,40}$`; retry up to 20 s; failures map to `sunshine_unreachable` / `pair_failed`. Sunshine base URL `DIARCHY_SUNSHINE_URL` default `https://localhost:47990`.
- Acceptance grep must be empty: `grep -rnE '192\.168\.0\.(125|107)|100\.106\.228\.87|nvenc' client/diarchy-stream client/install.sh host/install.sh host/sunshine.conf`.
- Commit with explicit paths on branch `feat/diarchy-app--streaming`; never push/merge/rebase; run only owned tests (`python3 -m unittest tests.test_host tests.test_display tests.test_client tests.test_diarchy_host_stream -v` subsets) until the final full run.

## Sources (stage 1, docs-research agent; report `local://t4-docscout.md`, tags verified at Moonlight v6.1.0 and Sunshine v2026.516.143833)

- Moonlight `pair <host> [--pin NNNN]` exists (`app/cli/commandlineparser.cpp`, `pair.cpp`). Non-4-digit PIN → exit 1. **`pair` is GUI-bound (`CliPair.qml`) and exits 0 on both success and runtime failure.** Exit code is therefore not authoritative for the Mac side; the host-side `stream pair` result (`{"paired": true}` only when Sunshine returns `{"status":"true"}`) is. Reported to Main for t3 (§9.3 step 4).
- Sunshine `POST /api/pin` (`src/confighttp.cpp`): requires `Content-Type: application/json`, HTTP Basic with the plaintext Web UI user/password, body `{"pin","name"}`; returns HTTP 200 `{"status":"true"|"false"}`; `false` when no pairing request is pending (not an error status). 401 on bad credentials; 403 if the origin is outside `origin_web_ui_allowed` (ours is `pc`, i.e. localhost). CSRF skipped when no `Origin`/`Referer` header is sent.
- `sunshine --creds` stores only salted SHA-256 in `sunshine_state.json`; the plaintext is only in our sidecar `~/.config/sunshine/.diarchy-ui` (`username=` / `password=` lines, 0600) written by `host/install.sh`.
- Encoder log lines (`src/video.cpp`): `Found H.264 encoder: h264_nvenc [nvenc]`, `Found H.264 encoder: h264_vaapi [vaapi]`, `Found H.264 encoder: libx264 [software]`; HEVC likewise (`hevc_nvenc`, `hevc_vaapi`). Unset `encoder` → auto-probe `nvenc → vaapi → software`; software is the silent terminal fallback, so the checker is the hardware guard.

## Rulings (plan-time)

- Ruling: remove `encoder` from `sunshine.conf` entirely — Sunshine auto-probes nvenc→vaapi; the log checker rejects `software` — cost if wrong: a host with only software encoding fails install validation loudly instead of streaming slowly.
- Ruling: the checker still requires **both** H.264 and HEVC, from the **same** family (preserves the existing contract; `hevc_mode = 2` in `sunshine.conf` is unchanged) — cost if wrong: a VA-API host lacking HEVC fails validation until `hevc_mode` is relaxed.
- Ruling: checker prints the family (`nvenc`|`vaapi`) on stdout on success; `diarchy-check-sunshine-service` keeps printing the output name by default and prints the family only with `--encoder` — cost if wrong: one extra journal read in the doctor.
- Ruling: `host/install.sh` leaves both services **stopped and disabled** after validation, because both `diarchy-kvm.service` and the streaming units are `WantedBy=graphical-session.target` and would otherwise both inject input after login, violating §2 exclusivity; the agent's `stream start|stop` owns the lifecycle — cost if wrong: users of the old always-on flow must start streaming from the app.
- Ruling: `stream pair` uses `urllib` (stdlib), not `curl`, so the Web UI password never appears in `argv`; TLS verification is disabled only when the URL host is a loopback address — cost if wrong: none for default config.
- Ruling: `--diagnose` prints `host=unset` for an empty candidate host (never an empty token) — cost if wrong: t3 parsing, which does not consume `--diagnose`.

## Review Focus

- Journal contains a software fallback (`libx264 [software]`) → checker must fail (silent CPU encoding). Test in Task 1.
- H.264 from one family and HEVC from another (e.g. nvenc + vaapi) → reject. Task 1.
- Doctor argument that looks like an option or shell metacharacter (`-c`, `1.2.3.4;id`) → usage error 64, no `ping` call. Task 3.
- Config with only a Tailscale host (LAN empty) → launcher never pings an empty host and still streams. Task 5.
- FPS above 60 (e.g. 144) → LAN/remote use 144, degraded clamps to 60. Task 5.
- `stream pair` with a bad PIN/name, a Sunshine that keeps answering `{"status":"false"}`, wrong credentials, or nothing listening → distinct, non-hanging, non-"paired" results within the timeout. Task 6.
- Sunshine credentials sidecar missing/half-written → `not_installed`, not a traceback. Task 6.

## File Structure

| File | Responsibility |
|---|---|
| `host/diarchy-check-sunshine-log` | Validate capture output + hardware encoder from log text on stdin; print family. |
| `host/diarchy-check-sunshine-service` | Resolve current invocation logs, run log checker; `--encoder` prints family. |
| `host/sunshine.conf` | Sunshine policy without a pinned encoder. |
| `host/install.sh` | Sunshine installer: vendor-neutral preflight, validation, services left stopped+disabled. |
| `host/diarchy-host-doctor` | Diagnostics; first arg = controller address (optional). |
| `client/diarchy-stream` | Route probe + Moonlight launcher driven by config. |
| `client/install.sh` | Moonlight + commands only (no launcher bundle). |
| `client/diarchy-client-doctor` | Client diagnostics; no hardcoded hosts. |
| `host/diarchy_host/stream.py` | `handle(args) -> dict` for `stream start|stop|status|pair`. |

Task order (SDD runs sequentially): 1, 2, 3, 4 are independent of other teams. Task 5 needs `MERGE LOADER`; Task 6 needs `MERGE AGENT-SKELETON`. Final verification (Task 7) is the orchestrator's.

---

### Task 1: Generalize the Sunshine log and service checkers

**Files:**
- Modify: `host/diarchy-check-sunshine-log`, `host/diarchy-check-sunshine-service`
- Test: `tests/test_host.py`

**Interfaces:**
- Produces: `diarchy-check-sunshine-log OUTPUT` (logs on stdin) → exit 0 and stdout `nvenc` or `vaapi`; exit 1 with `[fail] …` on stderr otherwise. `diarchy-check-sunshine-service [--encoder]` → default stdout = selected output (unchanged); `--encoder` stdout = family; any other argument → usage on stderr, exit 64. Task 3 and Task 6 consume these.

- [ ] **Step 1: Write the failing tests** in `HostPolicyTests` (`tests/test_host.py`). Add a helper and replace the two existing checker tests' log literals so they are family-parametrised:

```python
    HEADER = (
        "Info: Screencasting with Wayland's protocol\n"
        "Info: [wayland] Name: eDP-1\n"
        "Info: [wlgrab] Selected monitor [Laptop panel (eDP-1)] for streaming\n"
    )

    @staticmethod
    def encoders(h264: str, hevc: str | None = None) -> str:
        hevc = hevc or h264
        return (
            f"Info: Found H.264 encoder: h264_{h264} [{h264}]\n"
            f"Info: Found HEVC encoder: hevc_{hevc} [{hevc}]\n"
        )

    def test_capture_log_checker_accepts_each_hardware_family_and_reports_it(self) -> None:
        for family in ("nvenc", "vaapi"):
            with self.subTest(family=family):
                result = self.run_capture_check("eDP-1", self.HEADER + self.encoders(family))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), family)

    def test_capture_log_checker_rejects_software_and_inconsistent_encoders(self) -> None:
        cases = {
            "software fallback": self.HEADER
            + "Info: Found H.264 encoder: libx264 [software]\n",
            "mixed families": self.HEADER + self.encoders("nvenc", "vaapi"),
            "missing HEVC": self.HEADER
            + "Info: Found H.264 encoder: h264_vaapi [vaapi]\n",
            "missing H.264": self.HEADER
            + "Info: Found HEVC encoder: hevc_vaapi [vaapi]\n",
            "no encoder lines": self.HEADER,
        }
        for label, logs in cases.items():
            with self.subTest(label=label):
                result = self.run_capture_check("eDP-1", logs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[fail]", result.stderr)
```

For the service checker add `run_service_check(..., args=())` support (append `*args` to the `subprocess.run` argv list) and:

```python
    def test_service_checker_encoder_flag_prints_family_and_default_is_unchanged(self) -> None:
        logs = self.HEADER + self.encoders("vaapi")
        default, _, _ = self.run_service_check(logs)
        flagged, _, _ = self.run_service_check(logs, args=("--encoder",))
        bogus, _, _ = self.run_service_check(logs, args=("--nope",))
        self.assertEqual(default.stdout.strip(), "eDP-1")
        self.assertEqual(flagged.stdout.strip(), "vaapi")
        self.assertEqual(bogus.returncode, 64)
```

Existing nvenc literals in the other checker tests stay valid (nvenc is still accepted); keep them.

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m unittest tests.test_host.HostPolicyTests -v`
Expected: the new tests FAIL (vaapi rejected; no stdout family; `--encoder` rejected/ignored).

- [ ] **Step 3: Implement.** In `diarchy-check-sunshine-log` replace the two final `require_fixed` NVENC checks with:

```bash
encoder_family() {
  sed -nE "s/.*Found $1 encoder: [^ ]+ \\[([a-z0-9]+)\\]\$/\\1/p" <<<"$logs" | head -n 1
}

h264_family=$(encoder_family 'H\.264')
hevc_family=$(encoder_family 'HEVC')
[[ -n $h264_family ]] || fail "Sunshine did not report an H.264 encoder"
case "$h264_family" in
  nvenc|vaapi) ;;
  *) fail "Sunshine selected a non-hardware H.264 encoder: $h264_family" ;;
esac
[[ -n $hevc_family ]] || fail "Sunshine did not report an HEVC encoder"
[[ $hevc_family == "$h264_family" ]] ||
  fail "Sunshine selected different H.264 ($h264_family) and HEVC ($hevc_family) encoders"
printf '%s\n' "$h264_family"
```

In `diarchy-check-sunshine-service` add before the first check:

```bash
mode=output
case "${1:-}" in
  "") ;;
  --encoder) mode=encoder ;;
  *) printf 'usage: %s [--encoder]\n' "$0" >&2; exit 64 ;;
esac
(( $# <= 1 )) || { printf 'usage: %s [--encoder]\n' "$0" >&2; exit 64; }
```

and replace the checker invocation/print tail with:

```bash
if ! encoder=$(printf '%s\n' "$sunshine_logs" | "$LOG_CHECKER_BIN" "$selected_output"); then
  exit 1
fi
if [[ $mode == encoder ]]; then
  printf '%s\n' "$encoder"
else
  printf '%s\n' "$selected_output"
fi
```

- [ ] **Step 4: Run to confirm pass**

Run: `python3 -m unittest tests.test_host.HostPolicyTests -v`
Expected: all PASS (`test_installer_*` tests are rewritten in Task 2; they must still pass here).

- [ ] **Step 5: Commit**

```bash
git add host/diarchy-check-sunshine-log host/diarchy-check-sunshine-service tests/test_host.py
git commit -m "feat(host): accept NVENC or VA-API in Sunshine log checks and report the family"
```

---

### Task 2: Drop the NVENC pin and vendor lock from `sunshine.conf` and `install.sh`

**Files:**
- Modify: `host/sunshine.conf`, `host/install.sh`
- Test: `tests/test_host.py`

**Interfaces:**
- Consumes: Task 1 `check-sunshine-service`.
- Produces: installed `~/.config/sunshine/.diarchy-ui` format unchanged (`username=`, `password=`); both units installed, **disabled and stopped** at exit 0; dry-run text without the lowercase word `nvenc`.

- [ ] **Step 1: Write/modify the failing tests** in `tests/test_host.py`:
  - In `test_sunshine_policy_uses_wlr_hardware_encoding_and_no_audio` remove `"encoder": "nvenc"` from `required` and replace the last assertion with `self.assertNotIn("encoder", parsed)`; rename it `test_sunshine_policy_lets_sunshine_pick_a_hardware_encoder_and_has_no_audio`.
  - Replace `test_installer_preflights_the_configured_nvenc_device` with:

```python
    def test_installer_preflight_is_vendor_neutral(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn("/dev/dri/renderD", installer)
        for forbidden in ("nvidia-smi", "nvenc", "NVENC"):
            self.assertNotIn(forbidden, installer)
        self.assertNotIn("sudo", installer.replace("never run sudo", ""))

    def test_installer_leaves_services_stopped_and_disabled(self) -> None:
        installer = self.require_file("install.sh").read_text()

        self.assertIn(
            "systemctl --user disable diarchy-display.service "
            "diarchy-sunshine.service",
            installer,
        )
        self.assertIn(
            "systemctl --user stop diarchy-sunshine.service "
            "diarchy-display.service",
            installer,
        )
        self.assertNotIn("reenable", installer)
```

  - Replace `test_installer_reenables_services_after_unit_target_changes` with the test above (delete it). Keep `test_installer_restarts_services_after_updating_managed_files` (restart is still used for validation).
  - In `test_installer_dry_run_is_rootless_and_does_not_mutate_home` add `self.assertNotIn("nvenc", result.stdout.lower())` and `self.assertIn("hardware", result.stdout.lower())`.

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m unittest tests.test_host.HostPolicyTests -v`
Expected: the four changed tests FAIL.

- [ ] **Step 3: Implement.**
  - `host/sunshine.conf`: delete the line `encoder = nvenc`; add a comment line above `capture = wlr`: `# No encoder is pinned: Sunshine selects a hardware encoder; diarchy-check-sunshine-log rejects software.` (the comment must not contain the lowercase word `nvenc`).
  - `host/install.sh`:
    - dry-run bullet 3 becomes `- Configure wlroots capture with hardware video encoding (Sunshine selects the encoder)`; add bullet `- Leave both services stopped and disabled; the Diarchy agent starts them for Streaming mode`.
    - command list: drop `nvidia-smi`.
    - Replace the three NVIDIA checks (`/dev/nvidia0`, `gpu_name`) with:

```bash
shopt -s nullglob
render_nodes=(/dev/dri/renderD* /dev/nvidia0)
shopt -u nullglob
(( ${#render_nodes[@]} > 0 )) || fail "No GPU render device found (/dev/dri/renderD*): hardware encoding is unavailable"
```
    - `uinput` failure message: `fail "The current user cannot write /dev/uinput; run the uinput-access command shown by 'diarchy-host probe' (never run sudo from this script)"`.
    - Replace `systemctl --user reenable …` with `systemctl --user disable diarchy-display.service diarchy-sunshine.service` (keep the preceding `daemon-reload`, keep `restart`, keep the active checks and the 30-iteration validation loop).
    - Failure text: `"Sunshine capture or hardware encoder validation failed in its current invocation"`; success: `[info] Sunshine capture validated on <output> with <family> encoding` (get family with a second `"$BIN_DIR/diarchy-check-sunshine-service" --encoder`).
    - After the validation block add:

```bash
step "Leaving streaming services stopped"
systemctl --user stop diarchy-sunshine.service diarchy-display.service
```
    - Final lines: `Streaming is started from the Diarchy app (or: diarchy-host stream start).` and `Then run %s/diarchy-host-doctor <controller-address> for diagnostics.`

- [ ] **Step 4: Run to confirm pass**

Run: `python3 -m unittest tests.test_host -v && bash -n host/install.sh && grep -nE 'nvenc' host/install.sh host/sunshine.conf; echo grep-exit=$?`
Expected: tests PASS; `bash -n` silent; grep prints nothing and `grep-exit=1`.

- [ ] **Step 5: Commit**

```bash
git add host/sunshine.conf host/install.sh tests/test_host.py
git commit -m "feat(host): let Sunshine pick a hardware encoder and leave services to the agent"
```

---

### Task 3: Controller address argument for the host doctor

**Files:**
- Modify: `host/diarchy-host-doctor`
- Test: `tests/test_host.py` (new class `HostDoctorTests`)

**Interfaces:**
- Consumes: Task 1 `check-sunshine-service [--encoder]`.
- Produces: `diarchy-host-doctor [CONTROLLER_ADDRESS]`; address must match `^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$` else stderr `usage: diarchy-host-doctor [controller-address]` and exit 64 without running any check; more than one argument → same usage/64. `PING_BIN` env override (default `ping`).

- [ ] **Step 1: Write the failing tests**

```python
class HostDoctorTests(unittest.TestCase):
    DOCTOR = HOST / "diarchy-host-doctor"

    def run_doctor(self, *args: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ping_log = temp / "ping.log"
            for name, body in {
                "ping": '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"$FAKE_PING_LOG"\n'
                'echo "10 packets transmitted, 10 packets received, 0.0% packet loss"\n'
                'echo "round-trip min/avg/max/stddev = 1.0/2.0/3.0/0.5 ms"\n',
                "systemctl": "#!/usr/bin/env bash\nexit 0\n",
                "systemd-analyze": "#!/usr/bin/env bash\nexit 0\n",
            }.items():
                (temp / name).write_text(body)
                (temp / name).chmod(0o755)
            result = subprocess.run(
                [str(self.DOCTOR), *args],
                env=os.environ
                | {
                    "HOME": str(temp),
                    "PATH": f"{temp}:{os.environ['PATH']}",
                    "PING_BIN": str(temp / "ping"),
                    "FAKE_PING_LOG": str(ping_log),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            calls = ping_log.read_text().splitlines() if ping_log.exists() else []
            return result, calls

    def test_probes_the_given_controller_address(self) -> None:
        result, calls = self.run_doctor("198.51.100.20")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("198.51.100.20"), calls)
        self.assertIn("Mac LAN path is healthy", result.stdout)

    def test_skips_the_loss_probe_without_an_address(self) -> None:
        result, calls = self.run_doctor()
        self.assertEqual(calls, [])
        self.assertIn("skipped", result.stdout.lower())
        self.assertNotIn("192.0.2.20", self.DOCTOR.read_text())

    def test_rejects_option_like_or_shell_looking_addresses(self) -> None:
        for bad in ("-c", "1.2.3.4;id", "a b", "-", ".", ""):
            with self.subTest(address=bad):
                result, calls = self.run_doctor(bad)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("usage:", result.stderr)
                self.assertEqual(calls, [])

    def test_rejects_more_than_one_argument(self) -> None:
        result, calls = self.run_doctor("198.51.100.20", "198.51.100.21")
        self.assertEqual(result.returncode, 64)
        self.assertEqual(calls, [])
```

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m unittest tests.test_host.HostDoctorTests -v`
Expected: FAIL (hardcoded ping target, no argument handling).

- [ ] **Step 3: Implement** at the top of `diarchy-host-doctor` (after the shebang/`set`):

```bash
PING_BIN=${PING_BIN:-ping}
HOST_PATTERN='^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'
usage() { printf 'usage: %s [controller-address]\n' "${0##*/}" >&2; exit 64; }
(( $# <= 1 )) || usage
controller=${1-}
if (( $# == 1 )) && ! [[ $controller =~ $HOST_PATTERN ]]; then usage; fi
```

Replace the hardcoded probe block with:

```bash
if [[ -z $controller ]]; then
  printf '[info] Mac LAN path probe skipped (no controller address given)\n'
else
  ping_output=$("$PING_BIN" -c 10 -W 1 "$controller" 2>&1 || true)
  # existing loss/average parsing, unchanged, then the unchanged warn/pass ladder
fi
```

Keep the parsing and the warn/pass ladder verbatim inside the `else` branch. Also change the encoder check to report the family:

```bash
if selected_output=$("$HOME/.local/bin/diarchy-check-sunshine-service") &&
  encoder=$("$HOME/.local/bin/diarchy-check-sunshine-service" --encoder); then
  pass "Sunshine capture and $encoder hardware encoding match $selected_output in the current invocation"
else
  fail "Sunshine capture or hardware encoding does not match the current invocation"
fi
```

- [ ] **Step 4: Run to confirm pass**

Run: `python3 -m unittest tests.test_host -v && bash -n host/diarchy-host-doctor`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add host/diarchy-host-doctor tests/test_host.py
git commit -m "feat(host): take the controller address as an argument in the host doctor"
```

---

### Task 4: Client installer and doctor without the AppleScript launcher

**Files:**
- Modify: `client/install.sh`, `client/diarchy-client-doctor`, `tests/test_client.py`
- Delete: `client/Diarchy.applescript`

**Interfaces:**
- Produces: `client/install.sh` installs Moonlight + `diarchy-stream` + `diarchy-client-doctor` into `$BIN_DIR` only. It no longer reads `OSACOMPILE_BIN`, never compiles or replaces `~/Applications/Diarchy.app`, and its dry-run output lists no launcher bundle. The doctor still checks the bundle id `dev.sebastiangmz.diarchy` of `$DIARCHY_APP` (default `~/Applications/Diarchy.app`, installed by the t3 app) and `$BIN_DIR/diarchy-stream`.

- [ ] **Step 1: Write/modify failing tests** in `tests/test_client.py`:
  - Delete the `APPLESCRIPT` constant and `test_native_launcher_has_no_background_service_or_shared_log`.
  - Replace fixtures `LAN_HOST = "198.51.100.10"`, `TAILSCALE_HOST = "100.64.0.10"` (used again in Task 5).
  - `test_installer_dry_run_is_rootless_and_pinned`: remove the `Diarchy.app` assertion, add `self.assertNotIn("Launcher destination", result.stdout)` and `self.assertNotIn("osacompile", result.stdout)`.
  - `test_installer_is_idempotent_and_installs_owned_bundles`: rewrite expectations so only `Moonlight.app` and the two commands exist after installing, `Diarchy.app` does **not** exist, a second run succeeds, and the fake environment no longer provides `OSACOMPILE_BIN` (delete it from `make_install_environment`). Add:

```python
    def test_installer_never_touches_an_existing_launcher_bundle(self) -> None:
        home, environment = self.make_install_environment()
        marker = home / "Applications" / "Diarchy.app" / "Contents" / "marker"
        marker.parent.mkdir(parents=True)
        marker.write_text("keep")
        result = subprocess.run([str(INSTALLER)], env=environment, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), "keep")
```
  - Add a doctor test (fake `codesign`, `spctl`, `PlistBuddy` via env, fake launcher via `DIARCHY_STREAM_BIN` whose `--diagnose` exits 0, bundle plist with id `dev.sebastiangmz.diarchy`) asserting `[ok] Diarchy launcher` and exit 0; and a variant with a foreign bundle id asserting `[fail] Diarchy launcher` and exit 1.

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m unittest tests.test_client.ClientInstallerTests -v`
Expected: changed tests FAIL (installer still compiles the launcher and requires `osacompile`).

- [ ] **Step 3: Implement.**
  - `client/install.sh`: remove `LAUNCHER_BUNDLE_ID`, `LAUNCHER_APP`, `OSACOMPILE_BIN`, the `launcher_stage`, the `assert_replaceable "$LAUNCHER_APP" …` call, the whole `osacompile`/PlistBuddy/codesign launcher block, and the `Launcher destination` and `Installed Diarchy at` lines; drop `"$OSACOMPILE_BIN"` from the executable check. Keep Moonlight install and the two `install -m 0755` commands. Final line: `[info] Run $BIN_DIR/diarchy-client-doctor to verify routes and signatures` and `[info] Open Diarchy.app to start Streaming`.
  - Delete the AppleScript: `git rm "client/Diarchy.applescript"`.
  - `client/diarchy-client-doctor`: change the label `Diarchy launcher` (check name unchanged: bundle id check), keep `EXPECTED_LAUNCHER_ID=dev.sebastiangmz.diarchy`, rename `Route/profile launcher` to `Streaming launcher script`; no host values exist in the file. (Config loading is added in Task 5.)

- [ ] **Step 4: Run to confirm pass**

Run: `python3 -m unittest tests.test_client.ClientInstallerTests -v && bash -n client/install.sh client/diarchy-client-doctor`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/install.sh client/diarchy-client-doctor tests/test_client.py
git commit -m "feat(client): install Moonlight and commands only; remove the AppleScript launcher"
```

---

### Task 5: `diarchy-stream` driven by `diarchy.conf` (requires `MERGE LOADER`)

**Files:**
- Modify: `client/diarchy-stream`, `client/diarchy-client-doctor`, `client/install.sh`, `tests/test_client.py`

**Interfaces:**
- Consumes (t1, spec §4.3): `diarchy-config.sh` exposing `diarchy_config_load` (no args; exports `DIARCHY_LAN_HOST`, `DIARCHY_TAILSCALE_HOST`, `DIARCHY_STREAM_RESOLUTION`, `DIARCHY_STREAM_FPS`, `DIARCHY_STREAM_OVERLAY`, …; prints `diarchy-config: <reason>` and returns 1 on error). In env-only mode (no file) it needs `DIARCHY_SSH_USER` and one host in the environment. **Before dispatch the orchestrator confirms the real file path and name after the merge and amends this task if they differ.**
- Produces: launcher sources `$DIARCHY_CONFIG_SH` (Main ruling at MERGE LOADER 0de47bb; default `$(dirname "$0")/diarchy-config.sh`-equivalent, resolved with `pwd -P`); on a missing library prints `diarchy-stream: diarchy-config.sh not found next to the launcher` and exits 1; on loader failure prints `diarchy-stream: configuration could not be loaded` and exits 1. `client/install.sh` additionally installs `diarchy-config.sh` (0644) next to the commands (idempotent; same file t1 installs). The doctor sources the loader and reports `[ok] host configuration loads` / `[fail] host configuration loads`. Loader merged at `client/diarchy-config.sh` (verified present).

- [ ] **Step 1: Write/modify failing tests** in `ClientLauncherTests` (`tests/test_client.py`):
  - In `run_client` replace the env block to set `DIARCHY_CONFIG` to a non-existent path in the temp dir, `DIARCHY_SSH_USER=tester`, `DIARCHY_LAN_HOST`/`DIARCHY_TAILSCALE_HOST` from arguments (allow `""`), plus optional `DIARCHY_STREAM_RESOLUTION`, `DIARCHY_STREAM_FPS`, `DIARCHY_STREAM_OVERLAY` extras via a new `extra_env` parameter. Probes built only for non-empty hosts.
  - `test_healthy_local_path_uses_lan_1080p75` → rename `test_healthy_local_path_uses_config_resolution_and_fps`, expect `--resolution 1920x1080 --fps 60 --bitrate 25000 --video-codec HEVC --video-decoder hardware` and **no** `--performance-overlay`.
  - New tests:

```python
    def test_overlay_flag_only_when_enabled(self) -> None:
        on = self.run_client(lan=(0, (3.0, 3.0, 3.0, 3.0)), remote=None,
                             extra_env={"DIARCHY_STREAM_OVERLAY": "1"})
        off = self.run_client(lan=(0, (3.0, 3.0, 3.0, 3.0)), remote=None,
                              extra_env={"DIARCHY_STREAM_OVERLAY": "0"})
        self.assertIn("--performance-overlay", on[3][0])
        self.assertNotIn("--performance-overlay", off[3][0])

    def test_config_resolution_and_fps_apply_to_lan_and_remote_but_degraded_clamps(self) -> None:
        extra = {"DIARCHY_STREAM_RESOLUTION": "2560x1440", "DIARCHY_STREAM_FPS": "144"}
        lan = self.run_client(lan=(0, (3.0,) * 4), remote=None, extra_env=extra)[3][0]
        remote = self.run_client(lan=None, remote=(0, (30.0,) * 4), extra_env=extra)[3][0]
        degraded = self.run_client(lan=(10, (40.0,) * 4), remote=None, extra_env=extra)[3][0]
        for argv, res, fps in ((lan, "2560x1440", "144"), (remote, "2560x1440", "144"),
                               (degraded, "1280x720", "60")):
            self.assertEqual(argv[argv.index("--resolution") + 1], res)
            self.assertEqual(argv[argv.index("--fps") + 1], fps)
        self.assertEqual(degraded[degraded.index("--video-codec") + 1], "H.264")
        self.assertEqual(degraded[degraded.index("--bitrate") + 1], "8000")

    def test_degraded_keeps_lower_configured_fps(self) -> None:
        argv = self.run_client(lan=(10, (40.0,) * 4), remote=None,
                               extra_env={"DIARCHY_STREAM_FPS": "30"})[3][0]
        self.assertEqual(argv[argv.index("--fps") + 1], "30")

    def test_empty_lan_host_is_never_probed(self) -> None:
        result, pings, routes, launches = self.run_client(
            lan=None, remote=(0, (30.0,) * 4), lan_host="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[-1] for call in pings], [TAILSCALE_HOST])
        self.assertEqual(len(launches), 1)

    def test_diagnose_prints_unset_for_missing_host(self) -> None:
        result, *_ = self.run_client(lan=None, remote=(0, (30.0,) * 4),
                                     lan_host="", args=("--diagnose",))
        self.assertIn("candidate=lan host=unset reachable=false", result.stdout)

    def test_unconfigured_launcher_fails_with_loader_reason(self) -> None:
        # DIARCHY_CONFIG points at a missing file and no env override is given.
        result = self.run_launcher_without_env()
        self.assertEqual(result.returncode, 1)
        self.assertIn("diarchy-stream: configuration could not be loaded", result.stderr)
```
  - (`run_launcher_without_env` runs the launcher with a clean env containing only `PATH`, `HOME=<temp>`, `DIARCHY_CONFIG=<temp>/missing.conf`.) Ensure the existing route-selection tests keep passing with the new fixtures.
  - Add a doctor test: with the fake env-only config the doctor prints `[ok] host configuration loads`; with a missing config it prints `[fail] host configuration loads` and exits 1.
  - Installer test: after install `$BIN_DIR/diarchy-config.sh` exists (mode 0644) and is byte-identical to `client/diarchy-config.sh`.

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m unittest tests.test_client -v`
Expected: new tests FAIL (hardcoded defaults, no loader, overlay always on).

- [ ] **Step 3: Implement `client/diarchy-stream`.**
  - Replace lines 4–5 (`LAN_HOST=${…:-192…}`) with, after the helper `fail`:

```bash
SCRIPT_PATH=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
CONFIG_LIB=${DIARCHY_CONFIG_SH:-$SCRIPT_PATH/diarchy-config.sh}
[[ -r $CONFIG_LIB ]] || fail "diarchy-config.sh not found next to the launcher"
# shellcheck source=/dev/null
source "$CONFIG_LIB"
diarchy_config_load || fail "configuration could not be loaded"
LAN_HOST=$DIARCHY_LAN_HOST
TAILSCALE_HOST=$DIARCHY_TAILSCALE_HOST
```
    (`fail` must be defined before this block; move it up. Config loading happens after argument parsing for `--help` so `--help` works unconfigured: parse `--help` first.)
  - `probe_candidate`: first lines `if [[ -z $host ]]; then probe_reachable=false; return 1; fi` (no ping/route/nc call).
  - `print_candidate`: print `host=${host:-unset}`; `route=` line unchanged for the chosen host.
  - `launch_moonlight`: remove hardcoded `fps=75`; set
    `resolution=$DIARCHY_STREAM_RESOLUTION; fps=$DIARCHY_STREAM_FPS` for `lan` and `remote`; for `degraded`: `resolution=1280x720; fps=$(( DIARCHY_STREAM_FPS < 60 ? DIARCHY_STREAM_FPS : 60 ))`. Build the argument array and append `--performance-overlay` only when `[[ $DIARCHY_STREAM_OVERLAY == 1 ]]`; `--video-decoder hardware` is always present.
- [ ] **Step 3b: Implement doctor + installer.** Doctor: add before the route check

```bash
CONFIG_LIB=${DIARCHY_CONFIG_SH:-"$(dirname "$LAUNCHER_BIN")/diarchy-config.sh"}
if [[ -r $CONFIG_LIB ]] && ( source "$CONFIG_LIB" && diarchy_config_load ) >/dev/null 2>&1; then
  printf '[ok] host configuration loads\n'
else
  printf '[fail] host configuration loads\n' >&2
  failures=$((failures + 1))
fi
```
    Installer: `install -m 0644 "$SOURCE_DIR/diarchy-config.sh" "$BIN_DIR/diarchy-config.sh"` beside the other two installs, and list it in the dry-run command destinations.

- [ ] **Step 4: Run to confirm pass**

Run: `python3 -m unittest tests.test_client -v && bash -n client/diarchy-stream client/install.sh client/diarchy-client-doctor && grep -rnE '192\.168\.0\.(125|107)|100\.106\.228\.87|nvenc' client/diarchy-stream client/install.sh; echo grep-exit=$?`
Expected: PASS; grep empty (`grep-exit=1`).

- [ ] **Step 5: Commit**

```bash
git add client/diarchy-stream client/diarchy-client-doctor client/install.sh tests/test_client.py
git commit -m "feat(client): drive diarchy-stream from diarchy.conf with hardware decoding and optional overlay"
```

---

### Task 6: `diarchy_host/stream.py` (requires `MERGE AGENT-SKELETON`)

**Files:**
- Create: `host/diarchy_host/stream.py`
- Test: `tests/test_diarchy_host_stream.py`

**Interfaces:**
- Consumes (t2, verified after MERGE AGENT-SKELETON 1c0a57f in `host/diarchy_host/common.py` and `cli.py`): `AgentError(code: str, message: str)` with `.code`/`.message`; `run(argv: list[str], timeout: float = 20.0, input: str | None = None) -> subprocess.CompletedProcess[str]` (raises `AgentError("internal", …)` on timeout/OSError); `systemctl_user(*args: str) -> CompletedProcess` (uses `$SYSTEMCTL_BIN`, runs `systemctl --user <args>`); `unit_active(unit: str) -> bool`; `resolve_bin(env_var: str, default: str) -> str`; `home() -> Path`; constants `UNIT_KVM`, `UNIT_STREAM_DISPLAY`, `UNIT_STREAM_SUNSHINE`. `cli.py` imports `diarchy_host.stream` lazily and calls `stream.handle(args)` with the tokens after `stream`; it wraps the returned dict in the envelope and rejects payload keys `schema`/`ok`/`command`. Tests reuse `tests.test_diarchy_host_support.AgentCase` (temp HOME, tools dir as the only `PATH` entry, `self.tool(name, body, env_var)`, `self.agent(*args)` running the real launcher and returning `(CompletedProcess, envelope)`); the fake Sunshine HTTP server runs in a thread of the test process and the launcher is pointed at it through `DIARCHY_SUNSHINE_URL`. Use `UNIT_STREAM_DISPLAY`/`UNIT_STREAM_SUNSHINE` wherever this task says display/sunshine unit.
- Produces: `handle(args: list[str]) -> dict` returning the payload merged into the envelope by the dispatcher:
  - `["start"]` → stops `UNIT_KVM` if active, `systemctl --user start UNIT_DISPLAY UNIT_SUNSHINE`, verifies both active → `{"service_active": True, "encoder": "nvenc"|"vaapi"|"unknown"}`; raises `not_installed` when `~/.local/bin/sunshine` is not executable; `service_failed` when a unit is not active after start.
  - `["stop"]` → stops `UNIT_SUNSHINE` then `UNIT_DISPLAY` → `{"service_active": False, "encoder": None}`.
  - `["status"]` → `{"service_active": bool, "encoder": "nvenc"|"vaapi"|"unknown"|None}`; `service_active` = both units active; `encoder` None when not active; otherwise the family from `$DIARCHY_SUNSHINE_CHECK_BIN` (default `~/.local/bin/diarchy-check-sunshine-service`) run with `--encoder`; non-zero/other output → `"unknown"`.
  - `["pair", "--pin", PIN, "--name", NAME]` → `{"paired": True}`; usage errors (`usage`) for bad/missing/duplicate arguments, PIN not `^[0-9]{4}$`, name not `^[A-Za-z0-9 ._-]{1,40}$`, unknown subcommand; **no HTTP request is made on usage errors**.
- Pair environment (all optional): `DIARCHY_SUNSHINE_URL` (default `https://localhost:47990`), `DIARCHY_SUNSHINE_CREDENTIALS` (default `~/.config/sunshine/.diarchy-ui`), `DIARCHY_STREAM_PAIR_TIMEOUT` seconds (default `20`), `DIARCHY_STREAM_PAIR_INTERVAL` seconds (default `1`).

- [ ] **Step 1: Write the failing tests** (`tests/test_diarchy_host_stream.py`, style of `tests/test_route_selection.py`: temp `HOME`, fake executables on `PATH`, a stdlib `http.server.ThreadingHTTPServer` on `127.0.0.1:0` standing in for Sunshine over plain HTTP, run via `handle` imported with `sys.path` pointing at `host/`). The server records `(method, path, headers, body)` and answers per a scripted queue. Cover, each with real assertions:
  1. `test_pair_posts_pin_and_name_with_basic_auth_and_json_content_type` — fake server returns `{"status":"true"}`; `handle(["pair","--pin","1234","--name","Diarchy"])` == `{"paired": True}`; recorded request is `POST /api/pin`, `Content-Type: application/json`, `Authorization: Basic <b64(user:pass)>` from the sidecar file, body `{"pin":"1234","name":"Diarchy"}`.
  2. `test_pair_retries_until_moonlights_request_is_pending` — first two replies `{"status":"false"}`, third `{"status":"true"}` with interval `0.01` → paired, 3 requests.
  3. `test_pair_fails_when_sunshine_never_sees_a_pending_request` — always `{"status":"false"}`, timeout `0.2` → `AgentError.code == "pair_failed"`.
  4. `test_pair_wrong_credentials_fail_immediately` — server answers 401 → `pair_failed`, exactly 1 request, elapsed well under timeout.
  5. `test_pair_maps_connection_refused_to_sunshine_unreachable` — URL points at a closed port, timeout `0.2` → `sunshine_unreachable`.
  6. `test_pair_rejects_bad_arguments_without_any_request` — for PIN in (`123`, `12345`, `12a4`, ``, ` 123`), names (`""`, `"a"*41`, `"x;rm"`, `"-n"` is *allowed* by the regex and must be accepted, `"é"`), missing `--name`, repeated `--pin`, unknown flag, extra positional → `AgentError.code == "usage"`, server request count 0.
  7. `test_pair_without_credentials_reports_not_installed` — sidecar missing, empty, or lacking `password=` → `not_installed`.
  8. `test_password_never_appears_in_any_spawned_argv` — fake `PATH` executables log argv; after a pair, no logged argv contains the password.
  9. `test_start_stops_kvm_first_then_starts_streaming_units_and_reports_encoder` — fake `systemctl` logging calls and an `is-active` state file; assert call order `stop <kvm>` before `start <display> <sunshine>`; fake checker prints `vaapi` → payload encoder `vaapi`.
  10. `test_start_raises_service_failed_when_a_unit_stays_inactive`; `test_start_without_sunshine_binary_is_not_installed`.
  11. `test_stop_stops_sunshine_before_display`; `test_status_reports_none_encoder_when_inactive_and_unknown_when_checker_fails`.
  12. `test_unknown_subcommand_is_usage_error`.

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m unittest tests.test_diarchy_host_stream -v`
Expected: FAIL with `ModuleNotFoundError: diarchy_host.stream` (or missing `handle`).

- [ ] **Step 3: Implement `host/diarchy_host/stream.py`.** Structure:

```python
"""Streaming commands for the Diarchy agent."""
from __future__ import annotations

import base64, json, os, re, ssl, time, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import common
from .common import AgentError  # + UNIT_* constants / helpers per the merged skeleton

PIN_RE = re.compile(r"[0-9]{4}")
NAME_RE = re.compile(r"[A-Za-z0-9 ._-]{1,40}")
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _parse_pair(args: list[str]) -> tuple[str, str]: ...      # strict flag parser, usage errors
def _credentials() -> tuple[str, str]: ...                     # sidecar KEY=VALUE, not_installed on any gap
def _post_pin(url, user, password, pin, name, timeout) -> tuple[int, object]: ...
def pair(args: list[str]) -> dict: ...                         # loop to deadline, mapping per Interfaces
def handle(args: list[str]) -> dict: ...                       # dispatch start|stop|status|pair
```

  Requirements for the bodies: `_parse_pair` uses `PIN_RE.fullmatch` / `NAME_RE.fullmatch` (never `match`, never `$` — a trailing newline must fail). `_post_pin` builds the `Authorization` header in-process (never via argv), sends `Content-Type: application/json`, uses an unverified `ssl.SSLContext` only when `urlsplit(url).hostname in LOOPBACK` and scheme is `https`, and treats HTTP 401/403 as a final `pair_failed`. The deadline loop: connection errors/timeouts record `sunshine_unreachable` and retry; HTTP 200 with JSON `status` in (`"true"`, `True`) → return `{"paired": True}`; `"false"`/`False` → retry; other statuses → `pair_failed` immediately; at the deadline raise the last recorded error code (`pair_failed` for repeated `status:false`). Never include the password or the raw response body in an error message.
  Fill every function body fully; adapt names/signatures to the merged `common.py`. Env defaults and `DIARCHY_*` overrides as in the Interfaces block.

- [ ] **Step 4: Run to confirm pass**

Run: `python3 -m unittest tests.test_diarchy_host_stream -v`
Expected: PASS, no test sleeping longer than ~1 s total per case.

- [ ] **Step 5: Commit**

```bash
git add host/diarchy_host/stream.py tests/test_diarchy_host_stream.py
git commit -m "feat(agent): stream start/stop/status/pair commands"
```

---

### Task 7: Verification and report (orchestrator)

**Files:**
- Create: `docs/superpowers/reports/2026-10-11-t4-streaming.md`

- [ ] **Step 1:** Run `python3 -m unittest tests.test_host tests.test_display tests.test_client tests.test_diarchy_host_stream -v` and the acceptance grep; paste real output into the report.
- [ ] **Step 2:** Run the full suite once: `python3 -m unittest discover -s tests`; record the result and any failures owned by other teams.
- [ ] **Step 3:** Whole-branch review (most capable model) on the diff since base `bbe4c90`; one fix wave if needed.
- [ ] **Step 4:** Write the report (deliverables vs. brief checklist, files changed, commands + real output, every `Ruling:` from the ledger and this plan, deferred minors, items only physical verification can prove: VA-API/NVENC selection on real hardware, Moonlight pair GUI behavior headless, uinput/lid udev rules) and commit it.
- [ ] **Step 5:** `send to the orchestrator: "[t4→Main] DONE <final commit hash> report=docs/superpowers/reports/2026-10-11-t4-streaming.md"`.
