# Host Clipboard and File Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution status (2026-07-30):** Tasks 1–6 and Task 7 Steps 1–5 are complete. Task 7 Step 6 remains open for final closure, push, and PR update. The checkboxes below preserve the immutable approved plan rather than duplicating the live execution tracker.

**Goal:** Add opt-in bidirectional text clipboard sharing tied to the KVM lifecycle and manual SSH/rsync file transfer with a native macOS drop target.

**Architecture:** The Mac selects LAN or Tailscale, then initiates every SSH connection through the existing `diarchy` alias while overriding only endpoint and port. Two standalone Python pumps exchange length-delimited clipboard frames; the host pump owns at most one foreground `wl-copy` child. Bash commands own route selection, file transfer, installers, doctors, and KVM lifecycle integration.

**Tech Stack:** Bash 3.2-compatible shell, Python 3.11+, OpenSSH, rsync protocol-compatible macOS/Linux binaries, wl-clipboard, AppleScript, Python `unittest`, deterministic artifact gates.

## Global Constraints

- Source of truth: `docs/superpowers/specs/2026-07-29-clipboard-file-transfer-design.md`.
- artifact-gate MUST be configured and produce an expected failing RED run before runtime implementation.
- Clipboard is text-only, bidirectional, maximum 102400 UTF-8 bytes, active only while KVM is enabled, and never persisted.
- Mac initiates all transport through the existing SSH alias; project code never names, copies, moves, or directly opens private key/config files.
- SSH route options are `ControlMaster=no`, `ControlPath=none`, selected `HostName`, and selected `Port`.
- File transfer uses fixed drop folders, `--ignore-existing` by default, itemized rsync output, and explicit `--overwrite` opt-in.
- No new listener, LaunchAgent, systemd unit, package-manager invocation, or root operation.
- Existing Lan Mouse and Sunshine protocols/configuration remain byte-identical.
- Every task follows RED → GREEN → regression check → atomic commit → safe push.

## File Map

- `evals/config.json`, `evals/cases/*.json`: mandatory artifact contract and report metadata.
- `client/diarchy-route.sh`: one shared route probe/SSH option API.
- `client/diarchy-clipboard-bridge`: Mac clipboard pump and SSH child lifecycle.
- `host/diarchy-clipboard-remote`: Wayland clipboard pump and managed `wl-copy` owner.
- `client/diarchy-file`: `send|get|list` CLI with rsync collision semantics.
- `client/install-clipboard-file-transfer.sh`, `host/install-clipboard-file-transfer.sh`: rootless installation.
- `client/diarchy-clipboard-file-transfer-client-doctor`, `host/diarchy-file-transfer-host-doctor`: read-only diagnostics.
- `client/Diarchy Drop.applescript`: drag-and-drop sender.
- `client/diarchy-kvm`: bridge lifecycle hook and status.
- `tests/test_*.py`: behavioral contracts.

---

### Task 1: artifact-gate RED Contract

**Files:**
- Create: `evals/config.json`
- Create: `evals/cases/clipboard-artifacts.json`
- Create: `evals/cases/file-transfer-artifacts.json`
- Create: `evals/cases/integration-artifacts.json`
- Create: `evals/reports/.gitkeep`
- Create: `evals/runs/.gitkeep`
- Create: `evals/traces/sanitized/.gitkeep`

**Interfaces:**
- Produces: smoke suite with critical `file_exists` assertions for every runtime and test artifact.
- Evidence: `evals/reports/<red-run>.md`, then a later green report with the same suite.

- [ ] **Step 1: Author critical deterministic cases**

```json
{
  "schema_version": "eval.case.v1",
  "case_id": "clipboard-runtime-artifacts",
  "title": "Clipboard runtime and tests exist",
  "suite": "smoke",
  "risk_tier": "critical",
  "task_type": "repository_smoke",
  "source": {"kind": "spec", "path": "docs/superpowers/specs/2026-07-29-clipboard-file-transfer-design.md"},
  "privacy": {"classification": "public", "sanitized": true},
  "expected": {"success_criteria": ["Required clipboard runtime and tests exist."]},
  "judge": {"type": "deterministic", "assertions": [{"type": "file_exists", "path": "client/diarchy-clipboard-bridge"}]}
}
```

- [ ] **Step 2: Validate the suite**

Run: `<artifact-gate> validate`
Expected: PASS schema validation.

- [ ] **Step 3: Run the required RED gate**

Run: `<artifact-gate> run --suite smoke --commit-range main..HEAD`
Expected: FAIL with critical missing runtime/test artifacts and a canonical report path.

- [ ] **Step 4: Start local enforcement**

Run: `<artifact-gate> enforce start --suite smoke --commit-range main..HEAD`
Expected: enforced mode active; no claim of green enforcement until implementation exists.

- [ ] **Step 5: Commit and push**

```bash
git add evals
git commit -m "test: define sharing artifact-gate contract"
```

### Task 2: Clipboard Pumps

**Files:**
- Create: `tests/test_clipboard_bridge.py`
- Create: `client/diarchy-clipboard-bridge`
- Create: `host/diarchy-clipboard-remote`

**Interfaces:**
- Frame: `CLIP <sha256> <length>\n<body>` with `length <= 102400`.
- Mac process: `diarchy-clipboard-bridge [--route auto|lan|tailscale] [--poll-ms N]`.
- Host process: `diarchy-clipboard-remote [--poll-ms N]`.
- Runtime ownership: `${TMPDIR}/diarchy-clipboard-bridge-$UID/pid` and at most one foreground `wl-copy` child.
- Test helpers defined in the test module: `load_script(path) -> ModuleType`, `run_pump(script, env, stdin=b\"\") -> CompletedProcess[bytes]`, and `FakeProcess` with recorded `terminate()`/`wait()` calls.

- [ ] **Step 1: Write failing protocol and lifecycle tests**

```python
class ClipboardBridgeTests(unittest.TestCase):
    def test_frame_boundary_and_hash_round_trip(self):
        payload = b\"line one\\nline two\"
        frame = self.client.encode_frame(payload)
        self.assertEqual(self.client.decode_frame(io.BytesIO(frame)), payload)

    def test_102401_bytes_is_dropped_without_emission(self):
        self.assertIsNone(self.client.frame_for_local_change(b\"x\" * 102401, None))

    def test_incoming_hash_is_not_echoed_back(self):
        digest = hashlib.sha256(b\"same\").hexdigest()
        self.assertFalse(self.client.should_emit(digest, digest))

    def test_remote_replaces_and_reaps_foreground_wl_copy(self):
        first, second = FakeProcess(), FakeProcess()
        owner = self.remote.replace_owner(first, second)
        self.assertTrue(first.terminated and first.waited)
        self.assertIs(owner, second)

    def test_bridge_singleton_pid_directory_is_cleaned_on_sigterm(self):
        result = run_pump(CLIENT, self.runtime_env, stdin=b\"\")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.runtime_dir.exists())
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_clipboard_bridge -v`
Expected: FAIL because both pumps are absent.

- [ ] **Step 3: Implement minimal pumps**

```python
MAX_BYTES = 102400
HEADER = re.compile(rb"^CLIP ([0-9a-f]{64}) ([0-9]+)\\n$")

def encode_frame(payload: bytes) -> bytes:
    digest = hashlib.sha256(payload).hexdigest().encode("ascii")
    return b"CLIP " + digest + b" " + str(len(payload)).encode("ascii") + b"\n" + payload
```

Use binary pipes, metadata-only stderr, signal handlers, bounded child waits, `pbpaste -Prefer txt`, `pbcopy`, `wl-paste --type text/plain --no-newline`, and an owned `wl-copy --foreground --type text/plain` child.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `python3 -m unittest tests.test_clipboard_bridge -v`
Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit and push**

```bash
git add client/diarchy-clipboard-bridge host/diarchy-clipboard-remote tests/test_clipboard_bridge.py
git commit -m "feat: add bidirectional text clipboard bridge"
```

### Task 3: Shared Route Selection

**Files:**
- Create: `tests/test_route_selection.py`
- Create: `client/diarchy-route.sh`

**Interfaces:**
- Consumes: `DIARCHY_SSH_ALIAS`, `DIARCHY_LAN_HOST`, `DIARCHY_TAILSCALE_HOST`, `DIARCHY_SSH_PORT`.
- Produces: `select_diarchy_route auto|lan|tailscale`, `DIARCHY_ROUTE_NAME`, `DIARCHY_ROUTE_HOST`, and `diarchy_ssh_options` array-safe output.
- Test helper defined in the test module: `run_route(scenario: str, forced: str = \"auto\") -> CompletedProcess[str]`; its injected `ping`, `route`, and `nc` stubs read `scenario`.

- [ ] **Step 1: Write failing route matrix tests**

```python
class RouteSelectionTests(unittest.TestCase):
    def test_auto_prefers_reachable_non_tunnel_lan(self):
        self.assertIn(\"route=lan\", run_route(\"lan-ok\").stderr)

    def test_utun_route_is_not_classified_as_lan(self):
        self.assertIn(\"route=tailscale\", run_route(\"lan-utun\").stderr)

    def test_auto_falls_back_to_tailscale(self):
        self.assertIn(\"route=tailscale\", run_route(\"lan-down\").stderr)

    def test_forced_unreachable_route_fails_closed(self):
        result = run_route(\"all-down\", forced=\"lan\")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(\"forced LAN route is unreachable\", result.stderr)

    def test_selected_ssh_options_disable_controlmaster_and_set_host_port(self):
        result = run_route(\"tailscale-ok\", forced=\"tailscale\")
        self.assertIn(\"ControlMaster=no\", result.stdout)
        self.assertIn(\"HostName=100.64.0.10\", result.stdout)
        self.assertIn(\"Port=22\", result.stdout)
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_route_selection -v`
Expected: FAIL because the library is absent.

- [ ] **Step 3: Implement the shared shell API**

```bash
DIARCHY_SSH_ALIAS=${DIARCHY_SSH_ALIAS:-diarchy}
DIARCHY_LAN_HOST=${DIARCHY_LAN_HOST:-192.0.2.10}
DIARCHY_TAILSCALE_HOST=${DIARCHY_TAILSCALE_HOST:-100.64.0.10}
DIARCHY_SSH_PORT=${DIARCHY_SSH_PORT:-22}
```

Reuse the existing `diarchy-stream` probe policy, reject tunnel interfaces for LAN, bound every probe, and never persist overrides.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `python3 -m unittest tests.test_route_selection -v`
Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit and push**

```bash
git add client/diarchy-route.sh tests/test_route_selection.py
git commit -m "feat: add SSH route selection"
```

### Task 4: File Transfer CLI

**Files:**
- Create: `tests/test_file_transfer.py`
- Create: `client/diarchy-file`

**Interfaces:**
- Consumes: route API from Task 3.
- Produces: `diarchy-file send|get|list`, `--overwrite`, `--dry-run`, `--route`.
- Test helper defined in the test module: `run_file(args: list[str], scenario: str) -> CompletedProcess[str]`; injected SSH/rsync stubs record argv and itemized output.

- [ ] **Step 1: Write failing CLI tests**

```python
class FileTransferTests(unittest.TestCase):
    def test_send_uses_ignore_existing_and_itemized_output(self):
        result = run_file([\"send\", self.source], \"created\")
        self.assertIn(\"--ignore-existing\", self.rsync_argv())
        self.assertIn(\"--itemize-changes\", self.rsync_argv())
        self.assertIn(\"status=ok\", result.stdout)

    def test_overwrite_omits_ignore_existing(self):
        run_file([\"send\", \"--overwrite\", self.source], \"updated\")
        self.assertNotIn(\"--ignore-existing\", self.rsync_argv())

    def test_no_itemized_change_reports_skipped_and_nonzero(self):
        result = run_file([\"send\", self.source], \"no-change\")
        self.assertEqual(result.returncode, 1)
        self.assertIn(\"status=skipped-exists\", result.stdout)

    def test_get_missing_leaves_no_partial_file(self):
        result = run_file([\"get\", \"missing.txt\"], \"missing\")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.download.exists())

    def test_list_is_non_recursive_and_skips_hidden_files(self):
        result = run_file([\"list\"], \"visible-hidden-nested\")
        self.assertEqual(result.stdout.count(\"name=\"), 1)

    def test_dry_run_executes_neither_ssh_nor_rsync(self):
        run_file([\"send\", \"--dry-run\", self.source], \"created\")
        self.assertFalse(self.execution_log.exists())
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_file_transfer -v`
Expected: FAIL because `diarchy-file` is absent.

- [ ] **Step 3: Implement CLI and collision semantics**

```bash
RSYNC_FLAGS=(--archive --partial --partial-dir=.rsync-partial --itemize-changes --out-format=%i)
(( overwrite )) || RSYNC_FLAGS+=(--ignore-existing)
```

Quote every local path, reject names containing `/` for `get`, aggregate per-item status, chmod successful downloads to 0600, and use one remote stat round-trip for `list`.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `python3 -m unittest tests.test_file_transfer -v`
Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit and push**

```bash
git add client/diarchy-file tests/test_file_transfer.py
git commit -m "feat: add safe file transfer commands"
```

### Task 5: Rootless Installers, Doctors, and Drop App

**Files:**
- Create: `tests/test_clipboard_file_transfer_install.py`
- Create: `client/install-clipboard-file-transfer.sh`
- Create: `host/install-clipboard-file-transfer.sh`
- Create: `client/diarchy-clipboard-file-transfer-client-doctor`
- Create: `host/diarchy-file-transfer-host-doctor`
- Create: `client/Diarchy Drop.applescript`

**Interfaces:**
- Installs scripts under `~/.local/bin`, drop folders mode 0700, and `~/Applications/Diarchy Drop.app` with bundle id `dev.sebastiangmz.diarchy-drop`.
- Doctors are read-only and use `[ok]`, `[warn]`, `[fail]` lines.
- Test helpers defined in the test module: `run_installer(side, args, home)`, `run_doctor(side, home)`, `tree_hash(path)`, and injected executable stubs that record argv without touching the real machine.

- [ ] **Step 1: Write failing installer/doctor tests**

```python
class ClipboardFileTransferInstallTests(unittest.TestCase):
    def test_both_dry_runs_are_rootless_and_non_mutating(self):
        before = tree_hash(self.home)
        for side in (\"client\", \"host\"):
            self.assertEqual(run_installer(side, [\"--dry-run\"], self.home).returncode, 0)
        self.assertEqual(tree_hash(self.home), before)

    def test_installers_are_idempotent_and_preserve_kvm_streaming_fixtures(self):
        protected = tree_hash(self.protected_tree)
        first = run_installer(\"client\", [], self.home)
        second = run_installer(\"client\", [], self.home)
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        self.assertEqual(tree_hash(self.protected_tree), protected)

    def test_project_code_never_directly_opens_poisoned_dot_ssh(self):
        run_installer(\"client\", [], self.home)
        self.assertFalse((self.home / \".ssh\" / \"project-accessed\").exists())

    def test_client_refuses_foreign_drop_bundle(self):
        result = run_installer(\"client\", [], self.home_with_foreign_bundle)
        self.assertNotEqual(result.returncode, 0)

    def test_doctors_fail_on_missing_binary_wrong_mode_and_unreachable_ssh(self):
        for scenario in (\"missing-binary\", \"wrong-mode\", \"ssh-down\"):
            self.assertNotEqual(run_doctor(\"client\", self.home_for(scenario)).returncode, 0)

    def test_drop_applescript_sends_every_opened_item(self):
        source = APPLESCRIPT.read_text()
        self.assertIn(\"repeat with theItem in theItems\", source)
        self.assertIn(\"diarchy-file\", source)
        self.assertIn(\" send \", source)
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_clipboard_file_transfer_install -v`
Expected: FAIL because artifacts are absent.

- [ ] **Step 3: Implement rootless artifacts**

Install with `install -m 755`, `install -d -m 700`, atomic app staging, `/usr/bin/osacompile`, ad-hoc codesign, bundle-id ownership checks, and BatchMode SSH liveness only on the Mac.

```applescript
on open theItems
  repeat with theItem in theItems
    do shell script quoted form of (POSIX path of (path to home folder) & ".local/bin/diarchy-file") & " send " & quoted form of POSIX path of theItem
  end repeat
end open
```

- [ ] **Step 4: Verify GREEN and regressions**

Run: `python3 -m unittest tests.test_clipboard_file_transfer_install -v`
Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit and push**

```bash
git add client/install-clipboard-file-transfer.sh host/install-clipboard-file-transfer.sh client/diarchy-clipboard-file-transfer-client-doctor host/diarchy-file-transfer-host-doctor client/Diarchy\ Drop.applescript tests/test_clipboard_file_transfer_install.py
git commit -m "feat: install clipboard and file sharing"
```

### Task 6: KVM Lifecycle Hook

**Files:**
- Create: `tests/test_diarchy_kvm_hook.py`
- Modify: `client/diarchy-kvm`

**Interfaces:**
- Consumes: bridge runtime directory and PID contract from Task 2.
- Preserves: `on|off|toggle|status` exit codes and existing Lan Mouse behavior.
- Adds: bridge state suffix `on|off|absent|crashed`.
- Test helper defined in the test module: `run_kvm(command: str, state: KvmFixture) -> CompletedProcess[str]`; `KvmFixture` supplies fake Lan Mouse, bridge, PID, `ps`, `kill`, and sleep binaries.

- [ ] **Step 1: Write failing hook tests**

```python
class DiarchyKvmHookTests(unittest.TestCase):
    def test_on_starts_exactly_one_bridge_after_lan_mouse(self):
        result = run_kvm(\"on\", self.disabled_with_bridge)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.bridge_start_count(), 1)

    def test_on_without_bridge_preserves_existing_behavior(self):
        result = run_kvm(\"on\", self.disabled_without_bridge)
        self.assertEqual(result.stdout, \"Diarchy KVM: enabled\\n\")

    def test_off_verifies_pid_then_reaps_bridge(self):
        run_kvm(\"off\", self.enabled_with_bridge)
        self.assertEqual(self.signalled_pid(), self.bridge_pid)
        self.assertFalse(self.runtime_dir.exists())

    def test_stale_pid_is_cleaned_without_signalling_unrelated_process(self):
        run_kvm(\"off\", self.enabled_with_reused_pid)
        self.assertIsNone(self.signalled_pid())

    def test_status_reports_absent_off_on_and_crashed(self):
        self.assertIn(\"absent\", run_kvm(\"status\", self.enabled_without_bridge).stdout)
        self.assertIn(\"on\", run_kvm(\"status\", self.enabled_with_bridge).stdout)
        self.assertIn(\"crashed\", run_kvm(\"status\", self.enabled_with_stale_pid).stdout)
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_diarchy_kvm_hook -v`
Expected: FAIL because the hook is absent.

- [ ] **Step 3: Implement additive hook**

Keep existing KVM functions unchanged except calls to focused `clipboard_start`, `clipboard_stop`, and `clipboard_status` helpers. Never kill a PID until `/bin/ps` confirms `diarchy-clipboard-bridge` in its command line.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `python3 -m unittest tests.test_diarchy_kvm_hook tests.test_lan_mouse_client -v`
Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit and push**

```bash
git add client/diarchy-kvm tests/test_diarchy_kvm_hook.py
git commit -m "feat: bind clipboard bridge to KVM lifecycle"
```

### Task 7: Final Gates and Physical E2E

**Files:**
- Modify only if a verified failure requires a source fix.
- Evidence: `evals/reports/<green-run>.md`, PR body, command outputs.

**Interfaces:**
- Consumes all prior tasks.
- Produces final PASS/FAIL/INCONCLUSIVE matrix matching spec §10.3.

- [ ] **Step 1: Run full deterministic suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: all old and new tests pass.

- [ ] **Step 2: Run artifact-gate GREEN gate**

Run: `<artifact-gate> check --suite smoke --commit-range main..HEAD`
Expected: PASS, zero critical regressions, canonical report path, sanitized public metadata.

- [ ] **Step 3: Audit trace relevance**

Run: `<artifact-gate> traces`
Expected: no trace fixtures; report `not applicable` because behavior is deterministic and no raw/session trace is used.

- [ ] **Step 4: Install on the real pair and run the frozen matrix**

Run host installer through the existing `ssh diarchy`, run Mac installer locally, then exercise clipboard directions, KVM off, exact size boundary, non-text drop, file send/collision/overwrite/get/list, drop app, route fallback, doctors, no-listener diff, process cleanup, and byte-identity snapshots. Never mutate `authorized_keys`, host keys, interfaces, or route tables.

- [ ] **Step 5: Run validator and reviewer agents**

Validator must return binary PASS against spec §9. Reviewer must return PASS against goal, security, quality, and E2E evidence; any INCONCLUSIVE row must name the environmental blocker.

- [ ] **Step 6: Close the slice, commit any final evidence-only updates, push, and update PR**

```bash
git commit -m "test: verify clipboard and file sharing"
```

Use the consent-gated push and GitHub wrappers. Do not merge.
