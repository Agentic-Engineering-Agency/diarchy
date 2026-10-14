# T2 host-agent — final report

Branch `feat/diarchy-app--host-agent`, base `bbe4c90`. Plan: `docs/superpowers/plans/2026-10-11-diarchy-app-t2-host-agent.md`.
Every task passed an independent task review (spec compliance + quality), then a whole-branch review and a scoped re-review of its fix wave. All subagents ran on `anthropic/claude-sonnet-5-5`, thinking `high`.

## Deliverables vs. brief checklist

| Brief item | Status | Where |
|---|---|---|
| Package `host/diarchy_host/` (no `stream.py`) | done | `common.py`, `cli.py`, `platform.py`, `kvmconfig.py`, `kvm.py`, `probe.py` |
| Launcher `host/diarchy-host`, `host/VERSION` (`1.0.0`) | done | launcher always emits one JSON envelope (Python < 3.11, missing package, broken package) |
| §6.1 envelope, exit codes 0/1/64, one stdout line even on internal exceptions | done | `cli.py`; stdout guard (R6); serialization inside the guard |
| t4 interfaces (`AgentError`, `run`, `systemctl_user`, `unit_active`, unit constants, `cli.main`, guarded `stream.handle`) | done | `common.py`, `cli.py`; `READY AGENT-SKELETON 1c0a57f` sent |
| §6.4 platform adapter seam (`PlatformAdapter`, `HyprlandArchAdapter`, `UnsupportedAdapter`, `select_adapter`) | done | `platform.py` |
| §6.3 `probe` (every field and every required-action rule, package mapping, `--controller-ip`, `uinput-access`, `lid-policy`) | done | `probe.py` |
| `kvm configure \| authorize \| start \| stop \| status`, stream-unit mutual exclusion, managed config writer (0600, atomic, unmanaged backup, preserve-on-configure), fingerprint validation | done | `kvm.py`, `kvmconfig.py` |
| `install-host.sh` gating per §7 | done | `host/install-host.sh` |
| `install-lan-mouse.sh` delegates config writing to the agent; doctor and `test_lan_mouse_host.py` updated | done | `host/install-lan-mouse.sh`, `host/diarchy-kvm-host-doctor` |
| Acceptance: probe against fakes for supported Omarchy, non-Arch, no Hyprland instance, missing packages, UFW with/without controller IP, Tailscale interface | done | `tests/test_diarchy_host_probe.py` |
| No `sudo` executed | done | fake `sudo` on the sandbox PATH asserted never called in probe, kvm and install-host tests |

`host/systemd/diarchy-kvm.service` is unchanged (no test required it).

## Files changed (21, +4114/-98)

`host/VERSION`, `host/diarchy-host`, `host/diarchy_host/{__init__,common,cli,platform,kvmconfig,kvm,probe}.py`, `host/install-host.sh`, `host/install-lan-mouse.sh`, `host/diarchy-kvm-host-doctor`, `tests/test_diarchy_host_{support,cli,platform,kvmconfig,kvm,probe}.py`, `tests/test_install_host.py`, `tests/test_lan_mouse_host.py`, plan and this report. Nothing outside the ownership list was touched (`host/install.sh`, `host/sunshine*`, `host/diarchy-*`, `host/diarchy_host/stream.py`, `client/`, `app/` are unchanged).

## Tests (real output)

Owned test modules:

```
$ python3 -m unittest tests.test_diarchy_host_cli tests.test_diarchy_host_platform tests.test_diarchy_host_kvmconfig tests.test_diarchy_host_kvm tests.test_diarchy_host_probe tests.test_lan_mouse_host tests.test_install_host
----------------------------------------------------------------------
Ran 223 tests in 63.790s

OK
```

Full suite, once at the end (after the last commit `a8f9e42`):

```
$ python3 -m unittest discover -s tests
----------------------------------------------------------------------
Ran 461 tests in 204.526s

OK
```

Smoke with the real launcher on this Mac (arm64, no Hyprland; temp `HOME`): `diarchy-host version` → `{"schema":1,"ok":true,"command":"version","agent_version":"1.0.0"}` exit 0; `probe --controller-ip 192.0.2.10` → valid envelope with `platform.supported=false`, four human reasons, `required_actions: []`; `kvm authorize --fingerprint zz` → `invalid_fingerprint`, exit 1, nothing written.

## Rulings

- **R1 FP casing** (accepted by Main, spec `e812eee`): host `config.toml` stores the fingerprint lowercase; readers compare case-insensitively; every value returned in JSON is uppercase. Cost if wrong: none for the host (matches the physically verified form).
- **R2** `command` field is `"kvm start"`, `"stream pair"`, `"probe"`, `"version"`; unknown tokens fall back to the first non-option token or `""`. The launcher's own failure envelopes follow the same rule (drift-guard test).
- **R3** `cli.main` defaults `XDG_RUNTIME_DIR=/run/user/<uid>` so `systemctl --user`/`hyprctl` work over non-login ssh. Cost if wrong: none; not provable without a real Host.
- **R4** `probe --streaming` additive flag (§6.3 says "or requested" with no flag). Accepted by Main. `install-host.sh --with-streaming` passes it.
- **R5** probe degrades, never crashes: every missing/failing tool yields the conservative value.
- **R6** stdout guard: handlers run with `sys.stdout` redirected to stderr.
- **R7** unsupported platform: `required_actions` is `[]`; `session.graphical_session_active` and components are computed directly (best-effort), never through the unsupported adapter.
- **R8** agent strings are English; the app localizes by `id`/`code` (accepted by Main).
- **R9** `install-host.sh` final line prints `payload/VERSION` when present, else `host/VERSION`.
- **Payload key collisions** (`schema`/`ok`/`command`) raise `internal` instead of silently overriding envelope keys — surfaces handler bugs; no current handler is affected.
- **`unit_running`** (final-review fix): KVM/stream exclusion and restart-if-active use `systemctl --user show -p ActiveState --value` and treat `active|activating|reloading|deactivating` as running, because `is-active` is non-zero for `activating` (Sunshine's `ExecStartPre`, Lan Mouse's `Restart=on-failure`). `unit_active` is unchanged for t4, and `kvm status`/post-start verification keep "is-active" semantics. **Main: t4's `stream start` should use `common.unit_running` for the peer-unit stop (`UNIT_KVM`) for the same reason.**
- **`kvm stop`** on an inactive/unloaded unit is a successful no-op (`service_active: false`), so the app's cleanup `off` never errors on a Host where KVM was never installed.
- **Adapter fallback:** `UnsupportedAdapter.kvm_capture_backend()` raises `unsupported_platform` and `graphical_session_active()` returns `False`; probe never calls them on an unsupported host.
- **Config dir:** `kvmconfig.write` chmods the Lan Mouse config dir to 0700 even when it already exists (it holds `lan-mouse.pem`), matching the old installer's `install -d -m 700`.
- **openssl** is only called when the certificate file exists; an empty `HandleLidSwitch=` resets to the systemd default (`suspend`); `file_portal.service_active` is reported even when the portal binary is not installed.

## Deferred minors (reviewed, not fixed)

- `UnsupportedAdapter`/os-release: misleading reason "unreadable os-release" when `ID` is missing; `home()` raises `KeyError` if `HOME` is unset (envelope turns it into `internal`).
- `HyprlandArchAdapter.detect()` runs twice per probe (`select_adapter` + `handle`); if state flaps between them `adapter` and `required_actions` could disagree.
- No overall probe deadline: each external call has the 20 s default timeout, many calls per probe if the user bus hangs (the app kills the command at 15 s anyway). Probe tests take ~35 s because each runs the real launcher.
- `kvm_capture_backend()` is defined but unused (`kvmconfig` hard-codes `layer-shell`); the next adapter must wire it.
- `lid-policy` precedence across logind dirs is approximate (real logind reads all main files first, then all drop-ins by filename).
- `firewall-*` reasons say "not opened" although the agent cannot read UFW rules as a normal user (ADR 0002 consequence).
- `kvmconfig`: managed-but-non-UTF-8 file drops the preserved fingerprint on `configure`; strict section parsing (`[authorized_fingerprints] # comment` not recognized); same-second backup retry hits `O_EXCL`; a symlinked `config.toml` is replaced by a regular file (the backup holds the content); "managed" means only that the first line is the header, so hand-added `[[clients]]` in a managed file is overwritten without backup.
- Config-written-before-restart: a restart failure reports `service_failed` after the config is already written (idempotent retry).
- `install-lan-mouse.sh`: up to three restarts of an active unit per install; agent failure message is the raw JSON line; no test for configure-ok / authorize-fails ordering (verified safe by reading: the new fingerprint is not trusted).
- `install-host.sh`: `install -d -m 700` also chmods an existing `~/.local/bin` to 700 (same as the sibling installers); `one_line` collapses whitespace in printed commands; stale `*.new.<pid>` dirs after SIGKILL; CRLF `VERSION`; unset `HOME` gives a bash "unbound variable" instead of a `[fail]` line.
- `host/VERSION` and `payload/VERSION` are installed/printed independently: they must be bumped together or app step 4 (agent version == bundled `VERSION`) will loop on "reinstall" (build-time concern for the app team).
- `probe.file_portal` unit path still uses `$HOME/.config` instead of `XDG_CONFIG_HOME`.

## Only physical verification on a real Host can prove

- `XDG_RUNTIME_DIR` default (R3) works over a real non-login ssh session; real `hyprctl instances -j` output with and without a running Hyprland; real `systemctl --user is-system-running` states.
- Real `systemctl --user show -p ActiveState` behaviour for `activating`/auto-restart units and the `start`/`stop` exit codes (e.g. exit 5 for an unloaded unit).
- Lan Mouse accepting the lowercase fingerprint line written by the agent (the form physically verified before this project), plus the Mac-side form chosen by t1.
- GNU `cp -R`/`find`/`install` behaviour of `install-host.sh` on Arch (tests ran with BSD tools on macOS).
- `/dev/uinput` writability check as a non-root user (`os.access` is always true as root) and the logind `HandleLidSwitch` resolution on a real Omarchy.
- Real `ufw.service` state and `tailscale0` interface detection; pacman package-name mapping on Omarchy.

## Process notes

- The `task` tool has no per-call model parameter, so two pinned agent definitions (an implementer agent and a reviewer agent: `anthropic/claude-sonnet-5-5`, thinkingLevel high) were created in the user-level agent definitions directory to satisfy the shared rule; they are removed after the final `DONE`.
