# Diarchy — T3 mac-app Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the native SwiftUI menu-bar app `Diarchy.app` as a SwiftPM package in `app/` (library `DiarchyCore`, executable `Diarchy`, tests `DiarchyCoreTests`) plus `app/scripts/build-app.sh`.

**Architecture:** All logic lives in `DiarchyCore` behind a `CommandRunner` protocol so every flow is unit-tested with a fake runner (no real ssh, no real Host). `Diarchy` is a thin SwiftUI shell over an `@MainActor @Observable AppModel` that calls Core off the main actor. The app orchestrates the §5 Mac commands and the §6 host agent over `diarchy-ssh`; it never re-implements them (ADR 0001) and never runs sudo (ADR 0002).

**Tech Stack:** Swift 6.4 toolchain (tools-version 6.0, language mode 6), macOS 14 deployment target, SwiftUI `MenuBarExtra`/`Window`, Swift Testing, Foundation only. No SwiftPM dependencies.

**Spec:** `docs/superpowers/specs/2026-10-11-diarchy-app-design.md` (§4, §5, §6, §7, §8, §9.3, §10, §11, §12). Team brief: `docs/superpowers/teams/t3-mac-app.md`. ADRs 0001, 0002. Vocabulary: `CONTEXT.md`.

## Global Constraints

- Ownership: edit only `app/**`, this plan, and `docs/superpowers/reports/2026-10-11-t3-mac-app.md`. Never touch `client/`, `host/`, `tests/`, `.gitignore`.
- Deployment target `platforms: [.macOS(.v14)]`; arm64; tests use Swift Testing (`import Testing`); no third-party dependencies.
- Build: `swift build --package-path app` (and `-c release`); tests: `swift test --package-path app`. Zero compiler warnings.
- Never run `sudo`; never contact the real Host (`ssh`, `diarchy-ssh`, `192.0.2.10` …). Tests use a fake `CommandRunner` and temp dirs.
- No hardcoded personal values (`192.0.2.10`, `100.64.0.10`, `192.0.2.20`, user `user`, key paths) in product code. Test fixtures: RFC 5737 (`192.0.2.x`, `198.51.100.x`), Tailscale `100.64.0.x`.
- User-facing text English; code, comments, logs, commit messages English.
- No timers and no background polling: no `Timer.scheduledTimer`, `Timer.publish`, repeating `Task.sleep` loops (except the bounded 10 s certificate wait inside the Pairing action, spec §8.5). Refresh on menu/window appearance and after actions only.
- Command timeouts: 15 s ssh commands, 20 s local commands, 900 s installers. Timeout error text: `Error: <command> did not respond in time`.
- Config file: first line `# Managed by Diarchy`, keys in §4.2 order, 0600 file / 0700 dir, atomic write (temp in same dir, fsync, rename), never `source`d.
- Bundle: id `dev.sebastiangmz.diarchy`, name `Diarchy`, `LSUIElement` true, `LSMinimumSystemVersion` 14.0, `CFBundleShortVersionString` = `host/VERSION`; payload under `Contents/Resources/payload/{client,host,VERSION}`; ad-hoc `codesign --force --deep --sign -`; never run `--install` ourselves.
- Commit on branch `feat/diarchy-app--mac-app` with explicit paths; no push/merge/rebase. Message READY lines go to Main via `send to the orchestrator: "[t3→Main] ..."`.

## Review Focus

Failure modes the spec implies but a happy-path test list would miss (each is pinned by a named test in the owning task):

1. **Pipe-buffer deadlock** — an installer or doctor that prints > 64 KiB to stdout while stderr is also filling; runner must drain both concurrently (Task 2).
2. **Paths with spaces** — `Lan Mouse.app`, `Diarchy.app`, identity files like `/Users/user/my keys/id key`; ssh-copy-id display command must shell-quote, argv must not (Tasks 4, 7).
3. **Double click / re-entrancy** — `setMode` called again while `transitioning`, or Moonlight exiting while the app is itself terminating it → no duplicate `stream stop`, no lost state (Task 6).
4. **Foreign config bytes** — CRLF, UTF-8 BOM, trailing spaces, lowercase fingerprint, `KEY =v`, quoted values, duplicate keys, port `0`/`65536`, identity with `..` (Task 1).
5. **ssh exit 255 vs. missing key** — host unreachable must surface the route reason (spec §11), not a misleading `ssh-copy-id` hint; `Permission denied (publickey…)` must give the hint (Task 4).

## Rulings (provisional, recorded here and in the report)

- Ruling: `StepState` is `pending | ok | actionRequired | failed`; the command(s) and the action button (`installMac | installHost | pair`) live on `ChecklistStep.commands` / `.action` instead of inside the enum payload — a step can list several commands (step 6) — cost if wrong: trivial rename.
- Ruling: "first non-empty host" for `ssh-copy-id` and Moonlight fallback is LAN first, then Tailscale (LAN preferred, CONTEXT.md "Route").
- Ruling: Moonlight pairing host = `diarchy-ssh --route lan -- true` exit 0 → LAN host, else Tailscale host (uses only the §5 contract; `diarchy_select_route` is a bash function we cannot call) — cost if wrong: pair against a wrong route; fix is one function.
- Ruling: step 6 blocks only on `required` actions whose id is not a streaming id (`uinput-access`, `lid-policy`); streaming ids are shown on the Streaming tab and gate only the Streaming mode — otherwise KVM would be blocked for hosts that merely have Sunshine installed.
- Ruling: config render writes the stored `DIARCHY_HOST_KEY_ALIAS` raw (empty stays empty; effective alias = Tailscale host, else LAN host, computed on read) so changing hosts never leaves a stale alias.
- Ruling: integer fields accept decimal digits only, leading zeros read as decimal.
- Ruling (Main, 2026-10-11, supersedes my proposed catalogue): error reasons and check order are spec §4.5, mirroring `git show b1cb5a8:client/diarchy-config.sh`; Swift fixtures mirror `git show b1cb5a8:tests/test_config_loader.py`. Swift never emits `LF line endings`/`must be an integer`/`must be set` wordings.
- Ruling: `StepState` of step 3 on exit 255: stderr containing `Permission denied` → `actionRequired` with ssh-copy-id; anything else → `failed(first stderr line)`.

## File Structure

```
app/Package.swift
app/Sources/DiarchyCore/
  ConfigCodec.swift      DiarchyConfig, KVMSide, ConfigError, ConfigCodec (parse/render/validate)
  ConfigStore.swift      load/save (atomic, 0600/0700), defaultURL
  CommandRunner.swift    CommandResult, CommandError, ChildProcess, CommandRunner, ProcessCommandRunner
  Paths.swift            Paths (home, ~/.local/bin, Lan Mouse app, payload, bundled version)
  HostAgentModels.swift  Codable envelope + payload types, AgentErrorCode
  HostAgentClient.swift  composes diarchy-ssh -- diarchy-host …, error mapping
  ControllerIP.swift     route/ipconfig parsing + resolver
  SetupChecklist.swift   ChecklistStep, StepID, StepState, SetupChecklist.run()
  Pairing.swift          KVM Pairing (§8.5) + MoonlightPairing (§9.3)
  ModeController.swift   actor, Mode, ModeTarget, ModeSnapshot (§8.6)
  Installer.swift        Mac + Host install flows (§7)
  Diagnostics.swift      three doctors + probe, combined text
app/Tests/DiarchyCoreTests/
  Support/FakeCommandRunner.swift, Support/TempDir.swift, Support/Fixtures.swift
  ConfigCodecTests, ConfigStoreTests, ProcessCommandRunnerTests, HostAgentClientTests,
  ControllerIPTests, SetupChecklistTests, PairingTests, ModeControllerTests, InstallerTests, DiagnosticsTests
app/Sources/Diarchy/
  DiarchyApp.swift (App, scenes), AppModel.swift, MenuContent.swift,
  SettingsView.swift, ConnectionTab.swift, ScreensTab.swift, StreamingTab.swift, DiagnosticsTab.swift, ChecklistView.swift
app/Resources/Info.plist.template, app/Resources/AppIcon.png (1024 source), app/scripts/build-app.sh
```

---

### Task 1: Package scaffold + ConfigCodec/ConfigStore

**Files:**
- Create: `app/Package.swift`, `app/Sources/DiarchyCore/ConfigCodec.swift`, `app/Sources/DiarchyCore/ConfigStore.swift`, `app/Sources/Diarchy/DiarchyApp.swift` (minimal `@main` App with an empty `MenuBarExtra` so the executable links; replaced in Task 8), `app/Tests/DiarchyCoreTests/Support/TempDir.swift`, `app/Tests/DiarchyCoreTests/ConfigCodecTests.swift`, `app/Tests/DiarchyCoreTests/ConfigStoreTests.swift`

**Interfaces:**
- Produces:
```swift
public enum KVMSide: String, CaseIterable, Sendable { case left, right, top, bottom }
public struct ConfigError: Error, Equatable, Sendable, CustomStringConvertible {
    public let reason: String                       // e.g. "DIARCHY_SSH_PORT is invalid" (spec §4.5)
    public var description: String { "diarchy-config: \(reason)" }
}
public struct DiarchyConfig: Equatable, Sendable {
    public var sshUser: String
    public var lanHost: String = ""
    public var tailscaleHost: String = ""
    public var sshPort: Int = 22
    public var sshIdentityFile: String = ""
    public var hostKeyAlias: String = ""            // stored raw; empty = default
    public var kvmSide: KVMSide = .left
    public var hostFingerprint: String = ""         // "" = unpaired
    public var streamResolution: String = "1920x1080"
    public var streamFPS: Int = 60
    public var streamOverlay: Bool = false
    public init(sshUser: String, lanHost: String = "", tailscaleHost: String = "")
    public var effectiveHostKeyAlias: String        // hostKeyAlias, else tailscaleHost, else lanHost
    public var preferredHost: String?               // lanHost if non-empty, else tailscaleHost, else nil
    public func validate() throws(ConfigError)
}
public enum ConfigCodec {
    public static let header = "# Managed by Diarchy"
    public static func parse(_ text: String) throws(ConfigError) -> DiarchyConfig
    public static func render(_ config: DiarchyConfig) throws(ConfigError) -> String   // uppercases fingerprint, validates, trailing "\n"
}
public struct ConfigStore: Sendable {
    public let url: URL
    public init(url: URL)
    public static func defaultURL(environment: [String: String], home: URL) -> URL  // DIARCHY_CONFIG else home/.config/diarchy/diarchy.conf
    public func load() throws -> DiarchyConfig?     // nil when the file does not exist; throws ConfigError otherwise
    public func save(_ config: DiarchyConfig) throws // creates dir 0700, temp file 0600 in same dir, fsync, rename
}
```
- Consumes: nothing.

Validation and check order — **spec §4.5 exactly** (reference: `git show b1cb5a8:client/diarchy-config.sh`, which wins over any text here). `parse` order: (2) not applicable to `parse`; (3) first line must equal the header exactly, else `first line must be "# Managed by Diarchy"` (blank first line and UTF-8 BOM fail it; a **zero-line file skips the header check** as in bash and then fails `DIARCHY_SSH_USER is required`); (4) per line from line 2 top to bottom: `line N: carriage returns are not allowed` (any `\r`), skip blank lines and lines starting `#`, `line N: expected KEY=VALUE` (line must match `^([A-Z0-9_]+)=(.*)$`, so `KEY =x`, `=x`, `junk`, lowercase keys fail), `line N: unknown key K`, `line N: duplicate key K`, then `quoted values are not allowed` (value contains `"` or `'`); (5) after parsing, apply defaults and validate in key order: `DIARCHY_SSH_USER is required` (empty), then `<KEY> is invalid` for user `^[a-z_][a-z0-9_-]{0,31}$`, LAN/Tailscale hosts (empty or `^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$`), port (`^[0-9]{1,5}$` and 1–65535, base 10), identity (empty or `^/[^\n]*$` and no `/../` segment when wrapped as `/<value>/`), alias (empty → effective default alias then HOST check), side, FP (strict uppercase `^([0-9A-F]{2}:){31}[0-9A-F]{2}$`), resolution `^[0-9]{3,4}x[0-9]{3,4}$`, FPS (digits and 30–240), overlay `0|1`; range violations use the same `is invalid` reason; (6) `at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required`. **Swift pitfall:** `"\r\n"` is a single `Character`; split and scan on the `utf8` view (bytes `0x0A`/`0x0D`), never on `Character`s or `split(separator: "\n")` over `String`. A trailing newline does not produce an extra empty line. `ConfigStore.load()`: missing file → `nil` (caller shows `Diarchy is not configured (open the app to set it up)`); existing but unreadable → `ConfigError(reason: "cannot read configuration file: <path>")`. `render` validates with the same function (after uppercasing the fingerprint) and throws the same reasons.
**Override (reference loader is now `git show 0de47bb:client/diarchy-config.sh`, cases in `tests/test_config_loader.py` at that commit — merged into this worktree, read the files directly):** an EMPTY (zero-byte) file fails with the first-line reason `first line must be "# Managed by Diarchy"` (NOT `DIARCHY_SSH_USER is required`); FPS must match `^[0-9]{1,3}$` and be 30–240 in base 10 (`060` valid → 60, `0060` invalid); identity regex is `^/.*$` plus the no-`/../` check. Everything else above stands.

- [ ] **Step 1: Write `Package.swift`** (tools 6.0; library `DiarchyCore`, executable `Diarchy` depending on it, test target `DiarchyCoreTests`; `platforms: [.macOS(.v14)]`). Create the minimal App stub and an empty `ConfigCodec.swift` so `swift build --package-path app` passes. Run it; expect success.
- [ ] **Step 2: Write failing tests `ConfigCodecTests`** using Swift Testing `@Test(arguments:)`. Golden file (also asserted byte-for-byte on render):

```
# Managed by Diarchy
DIARCHY_SSH_USER=omarchy
DIARCHY_LAN_HOST=192.0.2.10
DIARCHY_TAILSCALE_HOST=100.64.0.20
DIARCHY_SSH_PORT=22
DIARCHY_SSH_IDENTITY_FILE=
DIARCHY_HOST_KEY_ALIAS=
DIARCHY_KVM_SIDE=left
DIARCHY_HOST_FINGERPRINT=
DIARCHY_STREAM_RESOLUTION=1920x1080
DIARCHY_STREAM_FPS=60
DIARCHY_STREAM_OVERLAY=0
```
  Cases: mirror every case of `git show b1cb5a8:tests/test_config_loader.py` that is meaningful for a pure parser (header wrong/blank/BOM; CRLF → `line 2: carriage returns are not allowed`; missing trailing newline parses; `KEY =x`, `=x`, `junk`, `NOPE=x`, duplicate at `line 3`, double- and single-quoted values; comments/blank lines ignored; the full invalid-value table incl. `Root`, 33-char user, `-a`, `a_b`, 254-char host, port `0`/`65536`/`22a`, `relative`, `/tmp/../id`, alias `a_b`, side `middle`, lowercase-prefix fingerprint, `1920*1080`, `99x99`, fps `29`/`241`, overlay `2`; user required vs both-hosts-missing; effective alias defaults (Tailscale, then LAN, explicit wins); injection-looking host `$(touch MARKER)` → `DIARCHY_LAN_HOST is invalid` without side effects). Plus: golden file parses and `render(parse(x)) == x` byte-for-byte; minimal file (header + user + one host) gets defaults (22, left, 1920x1080, 60, false); `render` uppercases a lowercase fingerprint, writes table order with defaults explicit and throws the same reasons for invalid configs; accepted edges (port 1/65535, fps 30/240, user `_a-b9`, 32-char user, 253-char host, host `a`, identity `/Users/user/my keys/id key`); leading-zero port `022` is read as decimal 22; first-failure ordering (e.g. header error beats later errors; user required beats invalid host); empty (zero-byte) file → `DIARCHY_SSH_USER is required`.
  **Override:** empty (zero-byte) file → `first line must be "# Managed by Diarchy"`; add FPS `060` → 60 and `0060` → `DIARCHY_STREAM_FPS is invalid`. Port-ish leading zeros (`022`) are decimal.
- [ ] **Step 3: Run `swift test --package-path app --filter ConfigCodecTests`** — expect compile failure / FAIL.
- [ ] **Step 4: Implement `DiarchyConfig`, `ConfigError`, `ConfigCodec`.** Run the filter — expect PASS.
- [ ] **Step 5: Write failing `ConfigStoreTests`** (temp dir via `Support/TempDir.swift`): save creates dir mode `0o700` and file mode `0o600`; no `*.tmp*` leftovers in the dir after save; save then load round-trips; load on missing file returns `nil`; load on invalid file throws the `ConfigError` reason; overwrite replaces atomically (a stale temp file in the dir from a prior crash does not break save); `defaultURL` honors `DIARCHY_CONFIG` and falls back to `home/.config/diarchy/diarchy.conf`; file saved by `ConfigCodec.render` golden bytes equals file content.
- [ ] **Step 6: Implement `ConfigStore`** with POSIX `open(O_CREAT|O_EXCL|O_WRONLY, 0o600)`, `write`, `fsync`, `rename`, `mkdir 0o700`. Run `swift test --package-path app --filter "ConfigCodecTests|ConfigStoreTests"` — expect PASS, no warnings.
- [ ] **Step 7: Commit** `git add app/Package.swift app/Sources app/Tests && git commit -m "feat(app): scaffold package and config codec"`; then send `send to the orchestrator: "[t3→Main] READY CORE-CONFIG <hash>"`.

---

### Task 2: CommandRunner, Paths, test fakes

**Files:**
- Create: `app/Sources/DiarchyCore/CommandRunner.swift`, `app/Sources/DiarchyCore/Paths.swift`, `app/Tests/DiarchyCoreTests/Support/FakeCommandRunner.swift`, `app/Tests/DiarchyCoreTests/ProcessCommandRunnerTests.swift`, `app/Tests/DiarchyCoreTests/PathsTests.swift`

**Interfaces:**
- Produces:
```swift
public struct CommandResult: Equatable, Sendable {
    public var exitCode: Int32; public var stdout: String; public var stderr: String
    public init(exitCode: Int32, stdout: String = "", stderr: String = "")
    public var succeeded: Bool { exitCode == 0 }
    public var firstErrorLine: String   // first non-empty stderr line, else first non-empty stdout line, else "exit code N"
}
public enum CommandError: Error, Equatable, Sendable {
    case launchFailed(command: String, message: String)
    case timedOut(command: String)      // command = executable basename
}
public protocol ChildProcess: Sendable {
    var pid: Int32 { get }
    func waitForExit() async -> Int32   // exit code; 128+signal when signalled
    func terminate()                    // SIGTERM to the whole process group
    func kill()                         // SIGKILL to the whole process group
}
public protocol CommandRunner: Sendable {
    func run(executable: String, arguments: [String], environment: [String: String]?,
             stdin: Data?, timeout: TimeInterval) async throws -> CommandResult
    func runStreaming(executable: String, arguments: [String], environment: [String: String]?,
                      stdin: Data?, timeout: TimeInterval,
                      onLine: @escaping @Sendable (String) -> Void) async throws -> CommandResult  // stdout+stderr lines, in arrival order, also accumulated in the result
    func spawn(executable: String, arguments: [String], environment: [String: String]?) throws -> any ChildProcess
}
public struct ProcessCommandRunner: CommandRunner { public init() }
public struct Paths: Sendable {
    public init(home: URL, payload: URL?)
    public static func live() -> Paths                 // home = FileManager home; payload = Bundle.main.resourceURL/payload if it exists
    public let home: URL; public let payload: URL?
    public func localBin(_ name: String) -> String     // home/.local/bin/<name> absolute path
    public var lanMouseApp: URL                        // home/Applications/Lan Mouse.app
    public var moonlightExecutable: String             // home/Applications/Moonlight.app/Contents/MacOS/Moonlight
    public var configURL: URL                          // ConfigStore.defaultURL(env, home)
    public var lanMouseCertificate: URL                // home/.config/lan-mouse/lan-mouse.pem
    public func payloadPath(_ relative: String) -> String?  // nil when payload is nil
    public func bundledVersion() -> String?            // trimmed payload/VERSION, nil if absent/empty
}
```
- Consumes: Task 1 `ConfigStore.defaultURL`.

Env semantics: `environment == nil` inherits the process env; non-nil replaces it entirely **plus** a minimal `PATH` default is NOT added (callers pass what they need). `ProcessCommandRunner` uses `Process`, drains stdout and stderr concurrently (two readers), writes `stdin` then closes it, runs the child in its own process group (`posix_spawn` with `POSIX_SPAWN_SETPGROUP` or `setpgid`), on timeout sends SIGTERM to the group, waits 1 s, SIGKILL, then throws `.timedOut`. Launch failure (executable missing) → `.launchFailed`.

- [ ] **Step 1: Write `FakeCommandRunner`** (test support, `final class … @unchecked Sendable` guarded by a lock): records `calls: [Call]` (`executable`, `arguments`, `environment`, `stdin`); `when(_ matcher: (Call) -> Bool, return: Result<CommandResult, CommandError>)` rules matched first-to-last (later rules appended win only if earlier ones do not match); an unmatched call throws a test failure error naming the call (never silently succeeds); `runStreaming` feeds `onLine` with the result's stdout then stderr lines; `spawn` returns a `FakeChildProcess` whose `exit(code:)` resolves `waitForExit()`, records `terminated`/`killed`, and optionally ignores SIGTERM (`exitsOnTerminate: Bool`).
- [ ] **Step 2: Write failing `ProcessCommandRunnerTests`** using real `/bin/sh`, `/bin/echo`, `/bin/sleep`, `/usr/bin/true`: exit code + stdout + stderr captured separately; stdin is delivered (`/bin/cat` echoes it); **large output** — `/bin/sh -c 'head -c 300000 /dev/zero | tr "\0" a; head -c 300000 /dev/zero | tr "\0" b 1>&2'` completes without deadlock under a 10 s timeout and returns 300000 bytes on each stream (Review Focus 1); timeout — `/bin/sleep 30` with timeout 0.5 throws `.timedOut(command: "sleep")` in under 3 s and the process is gone (`kill(pid, 0)` fails); timeout kills grandchildren — `/bin/sh -c 'sleep 30 & wait'` leaves no `sleep` descendant (assert via the process group); missing executable throws `.launchFailed`; `runStreaming` delivers lines in arrival order and each line exactly once; `spawn` + `terminate()` makes `waitForExit()` return `143`; `kill()` on a child that traps TERM (`/bin/sh -c 'trap "" TERM; sleep 30'`) ends it; env: non-nil replaces env (`/usr/bin/env` prints only the given keys).
- [ ] **Step 3: Run** `swift test --package-path app --filter ProcessCommandRunnerTests` — expect FAIL (compile).
- [ ] **Step 4: Implement `CommandRunner.swift`.** Run — expect PASS.
- [ ] **Step 5: Write failing `PathsTests`** (temp home): `localBin("diarchy-ssh")` ends `/.local/bin/diarchy-ssh` under the given home; payload nil → `payloadPath` nil and `bundledVersion` nil; `VERSION` containing `1.0.0\n` → `"1.0.0"`; empty VERSION → nil; paths with spaces survive (`home` named `a b`).
- [ ] **Step 6: Implement `Paths.swift`.** Run `swift test --package-path app --filter "ProcessCommandRunnerTests|PathsTests"` — expect PASS.
- [ ] **Step 7: Commit** `git add app/Sources app/Tests && git commit -m "feat(app): command runner, paths and test fakes"`.

---

### Task 3: HostAgentClient

**Files:**
- Create: `app/Sources/DiarchyCore/HostAgentModels.swift`, `app/Sources/DiarchyCore/HostAgentClient.swift`, `app/Tests/DiarchyCoreTests/Support/Fixtures.swift`, `app/Tests/DiarchyCoreTests/HostAgentClientTests.swift`

**Interfaces:**
- Consumes: `CommandRunner`, `CommandResult`, `CommandError`, `Paths` (Task 2).
- Produces:
```swift
public enum AgentErrorCode: Equatable, Sendable { case usage, unsupportedPlatform, notInstalled, serviceFailed, invalidFingerprint,
    configWriteFailed, sunshineUnreachable, pairFailed, `internal`, unknown(String) }
public enum HostAgentError: Error, Equatable, Sendable {
    case agent(code: AgentErrorCode, message: String)   // envelope ok:false
    case transport(String)                               // ssh exit 255 / route failure: first stderr line
    case timedOut(command: String)                       // "diarchy-host <subcommand>"
    case malformedOutput(String)                         // not exactly one JSON envelope, or missing fields
    case unsupportedSchema(Int)
    case invalidArgument(String)                         // client-side validation before anything is sent
    public var userMessage: String                       // passthrough: message text for display after "Error: "
}
public struct RequiredAction: Equatable, Sendable, Decodable { public var id: String; severity: Severity(.required/.recommended); title, reason, command: String }
public struct ProbeResult: Equatable, Sendable, Decodable {
    agentVersion: String
    platform: Platform(supported: Bool, adapter: String, osId: String?, flavor: String?, compositor: String?, machine: String?, reasons: [String])
    session: Session(graphicalSessionActive: Bool)
    components: Components(lanMouse: LanMouse(installed, version: String?, serviceActive, listeningUdp), clipboard: Simple(installed), filePortal: Portal(installed, serviceActive), sunshine: Sunshine(installed, serviceActive))
    kvm: KVM(hostFingerprint: String?, authorizedFingerprints: [String], port: Int)
    requiredActions: [RequiredAction]
    public var rawJSON: String                           // the stdout line, for the Diagnostics tab
}
public struct StreamStatus: Equatable, Sendable { public var serviceActive: Bool; public var encoder: String? }
public struct HostAgentClient: Sendable {
    public init(runner: any CommandRunner, paths: Paths, timeout: TimeInterval = 15)
    public func version() async throws(HostAgentError) -> String
    public func probe(controllerIP: String?, streaming: Bool = false) async throws(HostAgentError) -> ProbeResult
    public func kvmAuthorize(fingerprint: String) async throws(HostAgentError) -> String?
    public func kvmConfigure() async throws(HostAgentError) -> String?
    public func kvmStart() async throws(HostAgentError)
    public func kvmStop() async throws(HostAgentError) -> Bool
    public func kvmStatus() async throws(HostAgentError) -> Bool
    public func streamStart() async throws(HostAgentError)
    public func streamStop() async throws(HostAgentError)
    public func streamStatus() async throws(HostAgentError) -> StreamStatus
    public func streamPair(pin: String, name: String) async throws(HostAgentError)
    public func sshTrue() async -> CommandResult         // `diarchy-ssh -- true`; callers inspect exit/stderr
}
```
Command composition (exact): executable `paths.localBin("diarchy-ssh")`; arguments `["--", "~/.local/bin/diarchy-host", <subcommand words…>]` (the `~` is intentionally unexpanded: it is expanded by the remote shell). `probe` adds `["--controller-ip", ip]` only when `ip` matches IPv4 dotted-quad. `kvm authorize` → `["kvm","authorize","--fingerprint",FP]` (FP must match `^([0-9A-F]{2}:){31}[0-9A-F]{2}$`, else `invalidArgument` **before** any process runs). `stream pair` → `["stream","pair","--pin",PIN,"--name","'Diarchy'"]`: PIN must match `^[0-9]{4}$`; name must match `^[A-Za-z0-9 ._-]{1,64}$` and is single-quoted so the remote shell keeps it one word (spec §10: only validated values reach the remote shell). Result mapping: exit 255 → `.transport(firstErrorLine)`; otherwise stdout must be exactly one JSON object (trailing `\n` only) with `schema == 1` — else `.malformedOutput`/`.unsupportedSchema`; `ok:false` → `.agent(code, message)` (unknown code string → `.unknown`); `CommandError.timedOut` → `.timedOut("diarchy-host <subcommand>")`; `launchFailed` → `.transport(message)`. Decoder uses `.convertFromSnakeCase`; payload fields absent where the spec says nullable decode as `nil`.
Probe flags (Main contract fact from t2, 2026-10-11): `probe` accepts optional `--streaming`; **only then** does the agent emit `uinput-access`/`lid-policy`. The KVM checklist never passes it; the Streaming tab, the streaming-install flow, Moonlight pairing and the pre-start streaming gate do. Argument order: `probe [--controller-ip IP] [--streaming]`. Fingerprints in agent JSON/config are uppercase. **Localization:** add `RequiredAction.localizedTitle`/`.localizedReason` (computed, in `HostAgentModels.swift`) returning English text keyed by id (`install-packages`, `firewall-lan`, `firewall-tailscale`, `uinput-access`, `lid-policy`) and falling back to the agent's English `title`/`reason` for unknown ids; `command` is never altered. Tests: each known id returns non-empty English text different from the fixture text; unknown id falls back verbatim; argv tests for `probe(controllerIP: "192.0.2.5", streaming: true)` = `[..., "probe", "--controller-ip", "192.0.2.5", "--streaming"]` and streaming-only = `[..., "probe", "--streaming"]`.

- [ ] **Step 1: Write `Fixtures.swift`** with one JSON string per §6.2 payload: `version` (`{"schema":1,"ok":true,"command":"version","agent_version":"1.0.0"}`), full `probe` (the §6.3 example with real values: fingerprints built from `AA:`… 32 groups), `probe` with `platform.supported=false` (adapter `unsupported`, two reasons) and `host_fingerprint:null`, `probe` with each required-action id (`install-packages`, `firewall-lan`, `firewall-tailscale`, `uinput-access`, `lid-policy`), `kvm authorize` (`{"authorized":"<FP>"}`), `kvm configure` (`authorized:null`), `kvm start`/`stop`/`status` (`service_active`), `stream start/stop/status` (`encoder` each of `nvenc`/`vaapi`/`unknown`/`null`), `stream pair` (`{"paired":true}`), and one failure envelope for **every** error code (`usage` is exit 64, the rest exit 1).
- [ ] **Step 2: Write failing `HostAgentClientTests`**: (a) exact argv per command (assert `executable`, `arguments` array equality) incl. `probe` with and without controller IP and with a malformed IP (`"192.0.2.999"`, `"abc"`) → flag omitted; (b) every success fixture decodes to the expected typed value (probe: platform, components, kvm fingerprints, required action severity/command); (c) every error code → `.agent(code:message:)` with the matching `AgentErrorCode`, and an unknown code → `.unknown("x")`; (d) exit 255 with stderr `diarchy-ssh: Host is unreachable on LAN and Tailscale` → `.transport("diarchy-ssh: Host is unreachable on LAN and Tailscale")`; (e) empty stdout, two JSON lines, stdout with a banner line before JSON, `schema:2`, invalid JSON, `ok:true` missing `service_active` → `.malformedOutput`/`.unsupportedSchema` as applicable; (f) timeout from the runner → `.timedOut("diarchy-host kvm start")`; (g) client-side validation: lowercase FP, FP of 31 groups, FP containing `;`, PIN `123`, PIN `12345`, PIN `12a4`, name `x'; rm -rf ~` → `.invalidArgument` and **zero** runner calls; (h) `streamPair` argv contains the single-quoted name word exactly `'Diarchy'`; (i) `sshTrue()` argv is `["--","true"]` and never throws (maps launch/timeout to a `CommandResult` exit 255 with the message).
- [ ] **Step 3: Run** `swift test --package-path app --filter HostAgentClientTests` — expect FAIL.
- [ ] **Step 4: Implement models and client.** Run — expect PASS.
- [ ] **Step 5: Commit** `git add app/Sources app/Tests && git commit -m "feat(app): host agent client and envelope models"`.

---

### Task 4: ControllerIP + SetupChecklist

**Files:**
- Create: `app/Sources/DiarchyCore/ControllerIP.swift`, `app/Sources/DiarchyCore/SetupChecklist.swift`, `app/Tests/DiarchyCoreTests/ControllerIPTests.swift`, `app/Tests/DiarchyCoreTests/SetupChecklistTests.swift`

**Interfaces:**
- Consumes: `CommandRunner`, `Paths`, `ConfigStore`, `DiarchyConfig`, `HostAgentClient`, `ProbeResult`, `HostAgentError` (Tasks 1–3).
- Produces:
```swift
public enum ControllerIP {
    public static func interface(fromRouteGet output: String) -> String?          // parses "   interface: en0"
    public static func resolve(lanHost: String, runner: any CommandRunner) async -> String?
        // runs /sbin/route -n get <lanHost> then /usr/sbin/ipconfig getifaddr <iface>; result must be IPv4 dotted-quad, else nil; empty lanHost → nil without running anything
}
public enum StepID: Int, CaseIterable, Sendable { case components = 1, configuration, ssh, agent, platform, requiredActions, pairing }
public enum StepState: Equatable, Sendable { case pending, ok, actionRequired, failed }
public enum StepAction: Equatable, Sendable { case installMac, installHost, pair }
public struct CommandHint: Equatable, Sendable { public var title: String; public var command: String; public var blocking: Bool }
public struct ChecklistStep: Equatable, Sendable, Identifiable {
    public var id: StepID; public var title: String; public var state: StepState
    public var message: String; public var commands: [CommandHint]; public var action: StepAction?
}
public struct ChecklistResult: Equatable, Sendable {
    public var steps: [ChecklistStep]            // always 7, in order
    public var config: DiarchyConfig?
    public var probe: ProbeResult?
    public var isComplete: Bool                  // all 7 ok
    // streaming-only actions (uinput-access, lid-policy) never appear in the KVM probe; the checklist keeps a defensive filter so a streaming id can never block step 6
}
public struct SetupChecklist: Sendable {
    public init(paths: Paths, store: ConfigStore, runner: any CommandRunner, bundledVersion: String?)
    public func run() async -> ChecklistResult
    public static func sshCopyIDCommand(config: DiarchyConfig) -> String?   // nil when no host
}
```
Rules: titles : `Mac components`, `Configuration`, `SSH`, `Host agent`, `Platform`, `Required actions`, `Pairing`. A step runs only when all earlier steps are `ok`; later steps are `.pending` with message `Waiting for earlier steps`. Step 1: executables `diarchy-kvm`, `diarchy-ssh`, `diarchy-kvm-configure`, `diarchy-mac-fingerprint` under `~/.local/bin` (FileManager `isExecutableFile`) and `Lan Mouse.app` directory present → else `actionRequired` + action `.installMac`. Step 2: `store.load()` nil → `failed("diarchy-config: Diarchy is not configured (open the app to set it up)")`; load error → `failed(error.description)`. Step 3: `sshTrue()` exit 0 → ok; exit 255 + stderr contains `Permission denied` → `actionRequired` with one blocking `CommandHint(command: sshCopyIDCommand)`; other non-zero → `failed(firstErrorLine)` (the route reason for unreachable). `sshCopyIDCommand` = `ssh-copy-id -p <port> [-i <identity>.pub] <user>@<preferredHost>`; `.pub` appended only when identity does not already end in `.pub`; identity is shell-quoted (single quotes, `'` → `'\''`) when it contains anything outside `[A-Za-z0-9_@%+=:,./-]`. Step 4: `version()` equals `bundledVersion` → ok; mismatch, `.agent(.notInstalled…)`, or malformed output / `command not found` (exit 127 without envelope) → `actionRequired` + action `.installHost`; `.transport`/timeout → `failed`. Step 5: `ControllerIP.resolve` then `probe`; `platform.supported == false` → `failed(reasons joined with "; ")` and no action; probe error → `failed(userMessage)`. Step 6: blocking = `severity == .required` and id ∉ {`uinput-access`, `lid-policy`}; any blocking → `actionRequired` with a `CommandHint` per action (recommended ones included with `blocking: false`); only recommended → `ok` with those hints listed. Step 7: Mac FP from `diarchy-mac-fingerprint` stdout (trimmed); ok only when `probe.kvm.authorizedFingerprints` contains it and `config.hostFingerprint == probe.kvm.hostFingerprint` (both non-empty); else `actionRequired` + `.pair` (message distinguishes "sin certificado local" / "no autorizado" / "huella del host distinta").

- [ ] **Step 1: Write failing `ControllerIPTests`**: real macOS `route -n get` sample output (include `route to: 192.0.2.10`, `interface: en0`, `flags: <UP,HOST,DONE,LLINFO,WASCLONED>`) → `en0`; output without `interface:` → nil; empty LAN host → nil and zero runner calls; `ipconfig getifaddr en0` prints `192.0.2.55\n` → `"192.0.2.55"`; ipconfig exit 1 / empty / `fe80::1` / `192.0.2.999` → nil; `route` failing → nil.
- [ ] **Step 2: Implement `ControllerIP`; run** `swift test --package-path app --filter ControllerIPTests` — PASS.
- [ ] **Step 3: Write failing `SetupChecklistTests`** with a fake runner and a temp home: build a helper that creates the four executables + `Lan Mouse.app`, a valid config, and scripts runner rules. Cases: every step's `ok` outcome (full green path → `isComplete`, exactly 7 steps); step 1 not ok (each missing item individually, non-executable file) → `.installMac`, steps 2–7 `.pending`, **no** runner calls; step 2 missing config and invalid config (reason text preserved); step 3: exit 0, 255 `Permission denied (publickey).` → exact command `ssh-copy-id -p 22 192.0.2.10`-style for configs with/without identity, custom port, identity with spaces (`-i '/Users/user/my keys/id key.pub'`), identity already `.pub`, Tailscale-only config; 255 `diarchy-ssh: Host is unreachable on LAN and Tailscale` → `failed` with that exact reason and **no** `ssh-copy-id` hint (Review Focus 5); `Host key verification failed` → failed; step 4: matching version ok, mismatch, `not_installed` envelope, exit 127, ssh 255, timeout; step 5: unsupported platform lists all reasons, probe called with the resolved controller IP when LAN host exists and without `--controller-ip` when not; step 6: required `install-packages` blocks and lists its exact command, recommended-only is ok with non-blocking hints, `uinput-access` required does NOT block KVM but appears in `streamingBlockers`; step 7: all four mismatch combinations (no local cert → mac-fingerprint exit 1; FP not authorized; config FP empty; config FP ≠ host FP) → `.pair`; match → ok. A step never runs once an earlier one is not ok (assert call counts).
  **Override (t2 contract, wins over the sentence above):** there is no `streamingBlockers` field; `uinput-access` is never in the KVM probe (no `--streaming`). Test instead that a probe fixture containing `uinput-access`/`lid-policy` with `severity: required` does NOT block step 6 (defensive filter), and that the checklist's `probe` call argv never contains `--streaming`.
- [ ] **Step 4: Implement `SetupChecklist`; run** `swift test --package-path app --filter "ControllerIPTests|SetupChecklistTests"` — PASS.
- [ ] **Step 5: Commit** `git add app/Sources app/Tests && git commit -m "feat(app): setup checklist and controller ip resolution"`; `send to the orchestrator: "[t3→Main] READY CORE-AGENT-CLIENT <hash> (HostAgentClient + checklist decode §6 fixtures)"` (informational).

---

### Task 5: Pairing (KVM §8.5 and Moonlight §9.3)

**Files:**
- Create: `app/Sources/DiarchyCore/Pairing.swift`, `app/Tests/DiarchyCoreTests/PairingTests.swift`

**Interfaces:**
- Consumes: `CommandRunner`, `Paths`, `ConfigStore`, `HostAgentClient`, `ControllerIP`, `ChildProcess` (Tasks 1–4).
- Produces:
```swift
public enum PairingError: Error, Equatable, Sendable {
    case noLocalCertificate            // after launching Lan Mouse and waiting 10 s
    case hostHasNoCertificate          // probe.kvm.hostFingerprint nil after authorize
    case agent(HostAgentError)
    case configurationFailed(String)   // diarchy-kvm-configure non-zero: first error line
    case config(ConfigError)
    var userMessage: String
}
public struct Pairing: Sendable {
    public init(paths: Paths, store: ConfigStore, runner: any CommandRunner, agent: HostAgentClient,
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) })
    public func pair() async throws(PairingError) -> DiarchyConfig   // §8.5 steps 1–4; caller re-runs the checklist (step 5)
}
public enum MoonlightPairingError: Error, Equatable, Sendable { case agent(HostAgentError); case moonlightFailed(String); case launchFailed(String); var userMessage: String }
public struct MoonlightPairing: Sendable {
    public init(paths: Paths, runner: any CommandRunner, agent: HostAgentClient,
                makePIN: @escaping @Sendable () -> String = MoonlightPairing.randomPIN)
    public static func randomPIN() -> String        // 4 digits, zero-padded, "0000"–"9999"
    public func resolveHost(config: DiarchyConfig) async -> String?   // `diarchy-ssh --route lan -- true` exit 0 → lanHost, else tailscaleHost (nil only if both empty)
    public func pair(host: String) async throws(MoonlightPairingError)
}
```
KVM pairing: (1) `diarchy-mac-fingerprint` (local, 20 s); on non-zero: `/usr/bin/open -gj <home>/Applications/Lan Mouse.app` (argv, not shell), wait until `lan-mouse.pem` exists with the injected `sleep` in 0.5 s steps for at most 10 s total (bounded, only inside this action), retry the fingerprint command once; still failing → `.noLocalCertificate`. (2) `agent.kvmAuthorize(fingerprint:)`. (3) `agent.probe(controllerIP:)` (controller IP via `ControllerIP.resolve` when LAN host set); `hostFingerprint == nil` → `.hostHasNoCertificate`; save config with `hostFingerprint` = probe value. (4) `diarchy-kvm-configure` (local, 20 s) non-zero → `.configurationFailed`. Moonlight: `agent` runs `streamPair(pin:name:"Diarchy")` concurrently with `spawn(paths.moonlightExecutable, ["pair", host, "--pin", pin])`; success only when both exit 0; if the agent call fails, terminate the Moonlight child and throw `.agent`; if Moonlight exits non-zero, throw `.moonlightFailed("exit code N")`; **the exact Moonlight CLI form must be confirmed by docs-research agent (spec §9.3) before this step; record the verified form in the report**.

- [ ] **Step 1: Write failing `PairingTests`**: green path asserts the exact ordered runner calls (`diarchy-mac-fingerprint`, `diarchy-host kvm authorize --fingerprint <macFP>`, `diarchy-host probe`, `diarchy-kvm-configure`) and the saved config (`hostFingerprint` = probe value, other fields untouched); fingerprint missing then appears after `open -gj` (fake certificate file created by the fake `open` rule; injected sleep records requested durations summing ≤ 10 s) → success; never appears → `.noLocalCertificate` after ≤ 10 s of recorded sleep and **no** agent call; `kvm authorize` failure (`invalid_fingerprint`, transport) → `.agent` and config not modified; probe without host fingerprint → `.hostHasNoCertificate`, config not modified; `diarchy-kvm-configure` exit 1 → `.configurationFailed(stderr first line)` but the config **is** saved (step 3 already done); fingerprint output with trailing newline/whitespace is trimmed and a non-FP output is rejected before any remote call. Moonlight: argv exact (`pair <host> --pin <pin>`); the PIN passed to Moonlight equals the PIN passed to `stream pair`; `randomPIN()` property over 1000 draws always matches `^[0-9]{4}$`; Moonlight exit 0 + agent ok → success; Moonlight exit 1 → `.moonlightFailed`; agent `pair_failed` → child terminated and `.agent`; `resolveHost`: lan probe ok → lan host, lan probe fails → tailscale host, only-tailscale config makes no probe call.
  **Override — spec §9.3 as updated 2026-10-11 (wins over the Moonlight sentences above and in the Interfaces):** Moonlight 6.1.0 `pair` is GUI-bound and exits 0 on success **and** failure, so its exit code is ignored. `MoonlightPairing.pair(host:)` success is decided **only** by `agent.streamPair` returning `{paired:true}`. Flow: `agent.probe(controllerIP:streaming:true)` is not needed here; spawn Moonlight (`pair <host> --pin <pin>`) and run `agent.streamPair` concurrently; after the agent answers (success or failure) terminate the Moonlight child if still alive (`terminate()`, wait up to 5 s via the injected `sleep` race, then `kill()`), then return/throw. `MoonlightPairingError` loses `.moonlightFailed`; it keeps `.agent(HostAgentError)` and `.launchFailed(String)`. Tests (replace the Moonlight-exit-code cases): agent ok + Moonlight exits 0 → success; agent `pair_failed` + Moonlight exits 0 → throws `.agent` (Moonlight exit 0 must NOT count as success); agent ok + Moonlight still running → child terminated; child ignoring SIGTERM → `kill()` after the 5 s race; child already exited → neither `terminate()` nor `kill()` called; spawn failure → `.launchFailed` and the agent call is not made.
- [ ] **Step 2: Run** `swift test --package-path app --filter PairingTests` — FAIL.
- [ ] **Step 3: Implement `Pairing.swift`; run** the filter — PASS.
- [ ] **Step 4: Commit** `git add app/Sources app/Tests && git commit -m "feat(app): kvm and moonlight pairing flows"`.

---

### Task 6: ModeController (§8.6 / §11)

**Files:**
- Create: `app/Sources/DiarchyCore/ModeController.swift`, `app/Tests/DiarchyCoreTests/ModeControllerTests.swift`

**Interfaces:**
- Consumes: `CommandRunner`, `ChildProcess`, `Paths`, `HostAgentClient`, `HostAgentError` (Tasks 2–3).
- Produces:
```swift
public enum Mode: Equatable, Sendable { case off, kvm(degraded: Bool), streaming, transitioning }
public enum ModeTarget: Equatable, Sendable { case off, kvm, streaming }
public struct ModeSnapshot: Equatable, Sendable { public var mode: Mode; public var error: String? /* already "Error: …"-free text */ }
public actor ModeController {
    public init(paths: Paths, runner: any CommandRunner, agent: HostAgentClient,
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
                onChange: @escaping @Sendable (ModeSnapshot) -> Void = { _ in })
    public var snapshot: ModeSnapshot { get }
    public func refresh() async -> ModeSnapshot                   // derive from `diarchy-kvm status` + live Moonlight child
    public func setMode(_ target: ModeTarget) async -> ModeSnapshot
}
```
State derivation: child alive → `.streaming`; else `diarchy-kvm status` first line `Diarchy KVM: enabled` → `.kvm(false)`, `degraded` → `.kvm(true)`, `disabled` → `.off`; unparsable or timeout → `.off` with `error`. `setMode` while `.transitioning` returns the current snapshot unchanged and runs **no** commands (Review Focus 3). Transitions (each step must succeed; on failure stop, run the rollback, publish `error` = first stderr line / `<command> did not respond in time`):
- `off→kvm`: `diarchy-host kvm start` (via agent) → `diarchy-kvm on` → `diarchy-kvm status` exit 0. Failure of step 1: no rollback, state `.off`. Failure of step 2 or 3: run `diarchy-kvm off` (result ignored but its failure appended to the error), state `.off`.
- `kvm→off`: `diarchy-kvm off`; failure → remains `.kvm`, error shown.
- `*→streaming`: if kvm: `diarchy-kvm off` (failure aborts, stays kvm); `agent.streamStart()`; `runner.spawn(paths.localBin("diarchy-stream"), [])` as child. `streamStart` failure → best-effort `agent.streamStop()`, `.off`. Spawn failure → `agent.streamStop()`, `.off`. State `.streaming`.
- Child exit (not caused by us): an event-driven `Task` awaiting `waitForExit()`; on exit → `agent.streamStop()` → `.off`, error only when `streamStop` fails. Exits caused by `setMode(.off)` are tagged by generation and never trigger a second `streamStop`.
- `streaming→off`: `child.terminate()`; wait up to 5 s (race `waitForExit()` against `sleep(.seconds(5))`); then `child.kill()`; `agent.streamStop()`; `.off`; `streamStop` failure → `.off` + error.
- `kvm→streaming`/`streaming→kvm` pass through `.off` (`streaming→kvm` = off then kvm).
- `setMode(target)` equal to the current mode is a no-op (no commands).
- All `onChange` callbacks fire for `.transitioning` and for the final state.

- [ ] **Step 1: Write failing `ModeControllerTests`** with the fake runner (rules keyed on executable basename + first args) and `FakeChildProcess`: every transition's exact ordered call list; every failure path (host kvm start fails; `kvm on` fails → rollback `kvm off` called, state off, error text; `kvm status` exit 1 after `on` → rollback; rollback itself fails → combined error; `kvm off` fails from kvm → stays kvm; `stream start` fails → `stream stop` best-effort, off; spawn launchFailed → `stream stop`; kvm→streaming where `kvm off` fails aborts before `stream start`); timeouts (`CommandError.timedOut` from `kvm on` → error `diarchy-kvm did not respond in time`, rollback runs); refresh derives `.kvm(false)`, `.kvm(true)`, `.off`, `.streaming` (child alive wins over status), garbage status → off+error; **Moonlight exit** — fake child exits with code 0 and code 9 while streaming → exactly one `stream stop`, state `.off`; **stop with an ignoring-TERM child** — injected sleep resolves immediately, assert `terminate()` then `kill()` then `stream stop` in that order and no second `stream stop` from the exit watcher; terminate that exits promptly → `kill()` never called; **re-entrancy** — a second `setMode` issued while the first is suspended inside a fake runner returns immediately with `.transitioning` and adds zero calls; same-target no-op; `onChange` sequence for off→kvm is `[.transitioning, .kvm(false)]`.
- [ ] **Step 2: Run** `swift test --package-path app --filter ModeControllerTests` — FAIL.
- [ ] **Step 3: Implement the actor** (no `Task.sleep` loops; the only sleep is the 5 s kill race). Run — PASS.
- [ ] **Step 4: Commit** `git add app/Sources app/Tests && git commit -m "feat(app): mode state machine"`.

---

### Task 7: Installer + Diagnostics

**Files:**
- Create: `app/Sources/DiarchyCore/Installer.swift`, `app/Sources/DiarchyCore/Diagnostics.swift`, `app/Tests/DiarchyCoreTests/InstallerTests.swift`, `app/Tests/DiarchyCoreTests/DiagnosticsTests.swift`

**Interfaces:**
- Consumes: `CommandRunner.runStreaming`, `Paths`, `HostAgentClient` (Tasks 2–3).
- Produces:
```swift
public enum InstallerError: Error, Equatable, Sendable { case noPayload; case scriptNotExecutable(String); case failed(step: String, message: String); case timedOut(String)
    var userMessage: String }
public struct Installer: Sendable {
    public init(paths: Paths, runner: any CommandRunner)
    public func installMac(includeStreaming: Bool, onLine: @escaping @Sendable (String) -> Void) async throws(InstallerError)
    public func installHost(withStreaming: Bool, onLine: @escaping @Sendable (String) -> Void) async throws(InstallerError)
    public static let hostRemoteScript: String      // contains no single quote
    public static func remoteWords(withStreaming: Bool) -> [String]   // ["bash","-c","'<script>'"] with "--with-streaming" appended inside the script's last line when requested
}
public struct Diagnostics: Sendable {
    public init(paths: Paths, runner: any CommandRunner)
    public func run() async -> String               // sections in order, each headed "== <name> ==", never throws
}
```
Mac install (§7): execute directly (shebang; not `/bin/bash`, which is 3.2) in order `payload/client/install-lan-mouse.sh`, `payload/client/install-clipboard-file-transfer.sh`, and only when `includeStreaming` `payload/client/install.sh`; `runStreaming`, timeout 900 s; stop at the first failure → `.failed(step: "<script name>", message: firstErrorLine)`; a script without the exec bit → `.scriptNotExecutable(path)` before running anything. `onLine` receives every line, plus a header line `==> <script name>` before each script. Host install: one process `/bin/bash -c 'set -o pipefail; tar -C "$1" -czf - host VERSION | "$2" -- "$3" "$4" "$5"' installer <payload> <diarchy-ssh path> bash -c '<quoted script>'` — all values as positional args, never interpolated into the shell text; so a tar failure fails the pipeline. Remote script: `set -euo pipefail; base="$HOME/.local/share/diarchy"; mkdir -p "$base"; rm -rf "$base/payload.new"; mkdir "$base/payload.new"; tar -xzf - -C "$base/payload.new"; rm -rf "$base/payload.old"; if [ -e "$base/payload" ]; then mv "$base/payload" "$base/payload.old"; fi; mv "$base/payload.new" "$base/payload"; rm -rf "$base/payload.old"; bash "$base/payload/host/install-host.sh"` (+ ` --with-streaming`). Timeout 900 s. Diagnostics (§8.3): run `diarchy-kvm-client-doctor`, `diarchy-clipboard-file-transfer-client-doctor`, `diarchy-client-doctor` (local, 20 s) then `diarchy-ssh -- ~/.local/bin/diarchy-host probe` via `HostAgentClient` raw output (15 s); each section contains stdout+stderr+exit code; a missing/timed-out command yields a section with the reason, never aborts the rest.

- [ ] **Step 1: Write failing `InstallerTests`**: Mac order and exact executables for both flag values (temp payload with executable stub scripts created by the test); header lines and streamed lines delivered once each in order; stop at first failure (second script never called) with `.failed(step:message:)`; non-executable script → `.scriptNotExecutable` and no calls; `payload == nil` → `.noPayload`; timeout 900 s passed to the runner; paths with spaces (`payload` under `Diarchy.app/…`) are passed as argv unmodified (Review Focus 2); host: the single runner call is `/bin/bash` with arguments `["-c", <fixed pipeline text>, "installer", payload, diarchySSH, "bash", "-c", "'<script>'"]`, the pipeline text contains no payload path, `hostRemoteScript` has no `'`, `--with-streaming` appears only when requested and as the last token of the install line, `set -o pipefail` is present, `host VERSION` are the tar members; non-zero exit → `.failed(step: "host", message: first error line)`; timedOut → `.timedOut`.
- [ ] **Step 2: Write failing `DiagnosticsTests`**: section order and headings; stderr and non-zero exit are included; one command timing out still yields the other sections; probe transport failure yields a section with the reason.
- [ ] **Step 3: Run** `swift test --package-path app --filter "InstallerTests|DiagnosticsTests"` — FAIL.
- [ ] **Step 4: Implement both; run** the filter — PASS.
- [ ] **Step 5: Commit** `git add app/Sources app/Tests && git commit -m "feat(app): installers and diagnostics"`.

---

### Task 8: SwiftUI shell — AppModel and menu bar

Before writing code: dispatch a docs-research agent for (a) SwiftUI `MenuBarExtra` (`.menu` vs `.window` style: how to refresh content on every open without timers, how to disable a Picker with a tooltip), (b) macOS 14 `Window` scene + `openWindow` + `NSApp.activate` from an `LSUIElement` app, (c) `@Observable` with Swift 6 strict concurrency (`@MainActor`). Record the choice as a `Ruling:`.

**Files:**
- Create: `app/Sources/Diarchy/AppModel.swift`, `app/Sources/Diarchy/MenuContent.swift`; Modify: `app/Sources/Diarchy/DiarchyApp.swift`

**Interfaces:**
- Consumes: everything in `DiarchyCore`.
- Produces:
```swift
@MainActor @Observable final class AppModel {
    enum SettingsTab: Hashable { case connection, screens, streaming, diagnostics }
    var draft: DiarchyConfig            // editable copy; empty user until configured
    var hasConfig: Bool
    var checklist: ChecklistResult?
    var checklistRunning: Bool
    var mode: ModeSnapshot
    var installLog: [String]; var installRunning: Bool
    var diagnosticsOutput: String; var diagnosticsRunning: Bool
    var selectedTab: SettingsTab
    var statusLine: String              // §8.2 strings exactly: "No configurado" / "Desconectado" / "KVM activo" / "KVM degradado" / "Streaming activo" / "Error: <message>"
    var modesEnabled: Bool; var modesDisabledReason: String?   // checklist incomplete → tooltip text; `setMode(.streaming)` first runs `probe(streaming: true)` and, if a `required` action is pending, refuses with that action's localized message and command instead of starting
    func refresh() async                // load config, run checklist + mode refresh; called on menu/window appearance and after actions
    func saveAndCheck() async; func installMac(streaming: Bool) async; func installHost(streaming: Bool) async
    func pair() async; func pairMoonlight() async; func setMode(_ t: ModeTarget) async
    func setSide(_ side: KVMSide) async  // save, diarchy-kvm-configure, and if KVM enabled `diarchy-kvm off` then `on`
    func runDiagnostics() async
}
```
- Menu (§8.2): `MenuBarExtra` with symbol `display.2`; status line, mode picker (`Off`, `KVM`, `Streaming`; disabled with `.help(modesDisabledReason)` while setup incomplete), `Settings…`, `Diagnostics…`, `Quit` (`NSApplication.shared.terminate`). Refresh exactly when the menu opens (mechanism per docs-research agent ruling), never on a timer. `Settings…`/`Diagnostics…` open the Settings `Window` scene and `NSApp.activate`. If no config: the app opens Settings on Connection the first time the menu action is used.
- Commands run in `Task`s that call Core (all Core calls are `nonisolated`/actor work off the main actor); the model only publishes results.

- [ ] **Step 1:** dispatch docs-research agent, record rulings.
- [ ] **Step 2:** implement `AppModel` + menu + scene wiring. Add one pure-logic helper `StatusLine.make(config:checklist:mode:)` **in Core** (`app/Sources/DiarchyCore/StatusLine.swift`) with Swift Testing tests (`StatusLineTests`: every §8.2 string; error beats everything; `Not configured` when no config; `Desconectado` when checklist incomplete and mode off) so the model has no untested branching; write those tests first and watch them fail, then implement.
- [ ] **Step 3: Verify** `swift build --package-path app` and `swift test --package-path app --filter StatusLineTests`, then `grep -rnE "Timer\.(scheduledTimer|publish)|while true" app/Sources` returns nothing.
- [ ] **Step 4: Commit** `git add app/Sources app/Tests && git commit -m "feat(app): app model and menu bar"`.

---

### Task 9: SwiftUI shell — Settings window and four tabs

**Files:**
- Create: `app/Sources/Diarchy/SettingsView.swift`, `ConnectionTab.swift`, `ScreensTab.swift`, `StreamingTab.swift`, `DiagnosticsTab.swift`, `ChecklistView.swift`; Modify: `DiarchyApp.swift`

**Interfaces:** Consumes `AppModel` (Task 8). Produces views only.

- `SettingsView`: `TabView(selection: $model.selectedTab)` with tabs `Connection`, `Displays`, `Streaming`, `Diagnostics` . `.task`/`.onAppear` calls `model.refresh()`.
- `Connection`: Form — `SSH user`, `LAN host`, `Tailscale host`, `SSH port` (validated numeric), `Clave SSH (opcional)` with `NSOpenPanel` picker (`fileImporter` is fine), `Save and check` (disabled while `checklistRunning`; shows the `ConfigError` reason inline when `draft` is invalid). Below: `ChecklistView` — one row per `ChecklistStep`: SF Symbol per state (`circle.dashed` pending, `checkmark.circle.fill` ok, `exclamationmark.triangle.fill` actionRequired, `xmark.octagon.fill` failed), title, message, each `CommandHint` as monospaced selectable text with a `Copy` button (`NSPasteboard.general`), and action buttons `Install on this Mac` / `Install on the host` / `Pair` mapped from `StepAction` (hidden for step 5 failures per §11). Install log (`installLog`) in a monospaced selectable `ScrollView` beneath.
  **Main contract delta (2026-10-11):** the identity file must match `^/[^\n"]*$` with no `..` segment; the picker (NSOpenPanel, `showsHiddenFiles = true`, starting in `~/.ssh`) must run the chosen path through `DiarchyConfig.validate()` and, on failure, NOT store it and show the exact reason (`DIARCHY_SSH_IDENTITY_FILE is invalid`) inline. Core tests (Task 1 follow-up): identity with `"` and with a newline → `DIARCHY_SSH_IDENTITY_FILE is invalid`; config path that is a directory → `ConfigStore.load` throws `cannot read configuration file: <path>`.
- `Displays`: diagram — a rounded rectangle labeled `Mac` in the center with four drop zones (`Left`, `Right`, `Top`, `Bottom`) as buttons/`dropDestination` targets around it, the selected side highlighted with the accent color; selecting calls `model.setSide`. Accessible labels in English.
- `Streaming`: resolution field (`^[0-9]{3,4}x[0-9]{3,4}$`), FPS stepper 30–240, overlay toggle (all saved through `saveAndCheck`), `Install streaming` (`installMac(streaming:true)` then `installHost(streaming:true)`), `Pair Moonlight` (`pairMoonlight`), and the list of streaming-relevant required/recommended actions from `checklist.probe` (ids `uinput-access`, `lid-policy`) with `Copy`.
- `Diagnostics`: `Run diagnostics` button, monospaced selectable `Text` of `diagnosticsOutput`, `Copy all`.
- No timers. All strings English; no personal values; no placeholder values prefilled with real addresses (placeholders use `192.0.2.10` only as grey prompt text).

- [ ] **Step 1:** implement views.
- [ ] **Step 2: Verify build** `swift build --package-path app -c release` (zero warnings) and run the app executable directly for 3 s (`.build/release/Diarchy &` then `kill`) to confirm it starts without crashing; capture one window screenshot if a Settings window can be opened non-interactively, otherwise record the limit in the report (Main performs the installed-app smoke).
- [ ] **Step 3: Grep acceptance** `grep -rnE "Timer\.(scheduledTimer|publish)|<personal-data patterns>" app/Sources` returns nothing.
- [ ] **Step 4: Commit** `git add app/Sources && git commit -m "feat(app): settings window and tabs"`.

---

### Task 10: build-app.sh, Info.plist, icon

Read `skill://swiftpm-macos-app-bundle` first (copy the binary, never symlink; `plutil -lint`; `lsregister`). Here the payload lives under `Contents/Resources/payload` (no SwiftPM resource bundles), so ad-hoc `codesign --force --deep --sign -` is valid.

**Files:**
- Create: `app/scripts/build-app.sh`, `app/Resources/Info.plist.template`, `app/Resources/AppIcon.png` (1024×1024 generated by a throwaway script, e.g. CoreGraphics via `swift` or `sips`; checked in as the source asset), `app/Tests/DiarchyCoreTests` unchanged.

**Interfaces:** `app/scripts/build-app.sh [--install]` (bash, `set -euo pipefail`, repo root derived from the script path).

Behavior:
1. `repo_root` from `$0`; `VERSION_FILE="$repo_root/host/VERSION"`; if missing or empty → print `build-app.sh: host/VERSION is missing (the host team creates it); cannot build the payload` to stderr, exit 1, **before** compiling.
2. `swift build --package-path app -c release` (arch arm64); when env `DIARCHY_SCRATCH_PATH` is set, add `--scratch-path "$DIARCHY_SCRATCH_PATH"` to both the build and the `--show-bin-path` call (lets parallel work avoid the SwiftPM `.build` lock — Main ruling 2026-10-11); locate the binary via `swift build ... --show-bin-path`.
3. Assemble `app/build/Diarchy.app/Contents/{MacOS/Diarchy (copy), Info.plist, Resources/AppIcon.icns, Resources/payload/{client/, host/, VERSION}}`: `rm -rf` the old bundle first; copy `client/` and `host/` verbatim (no exclusions) with `cp -pR`, copy `host/VERSION` to `payload/VERSION`; preserve modes and `chmod +x` every file under `payload/client` and `payload/host` whose name has no extension or ends in `.sh`.
4. Info.plist from the template via `sed` substitution of `@VERSION@` only (`CFBundleIdentifier dev.sebastiangmz.diarchy`, `CFBundleName`/`CFBundleDisplayName` `Diarchy`, `CFBundleExecutable Diarchy`, `CFBundlePackageType APPL`, `CFBundleShortVersionString`/`CFBundleVersion` = VERSION, `LSUIElement true`, `LSMinimumSystemVersion 14.0`, `CFBundleIconFile AppIcon`, `NSHumanReadableCopyright` absent). `plutil -lint` must pass.
5. Icon: `sips`-based iconset from `AppIcon.png` + `iconutil -c icns`.
6. `codesign --force --deep --sign - "<bundle>"` then `codesign --verify --deep --strict "<bundle>"`.
7. `--install`: only when `~/Applications/Diarchy.app` is absent **or** its `CFBundleIdentifier` (`defaults read …/Contents/Info CFBundleIdentifier`) is `dev.sebastiangmz.diarchy`; otherwise print `build-app.sh: refusing to replace <path> (not owned by Diarchy)` and exit 1; replace with `rm -rf` + `cp -R` (never symlink), then `lsregister -f`. The script prints the bundle path on success. **We do not run `--install`.**

- [ ] **Step 1:** write a throwaway shell harness (not committed) that creates a fake repo layout in a temp dir (client/, host/ without VERSION) and asserts the clear failure message and exit code 1 without invoking swift; then with `host/VERSION` present use the real repo layout.
- [ ] **Step 2:** implement the script and template. Because `host/VERSION` does not exist on this branch yet, create it **only in a throwaway temp copy of the repo** for verification (never commit it from this branch; the host team owns `host/`): copy the worktree to a temp dir, add `host/VERSION` containing `1.0.0`, and run `bash app/scripts/build-app.sh` from there.
- [ ] **Step 3: Verify** the built bundle: `plutil -lint "<bundle>/Contents/Info.plist"`, `codesign --verify --deep --strict "<bundle>"`, `defaults read "<bundle>/Contents/Info" CFBundleShortVersionString` → `1.0.0`, `test -x "<bundle>/Contents/MacOS/Diarchy"`, `! test -L "<bundle>/Contents/MacOS/Diarchy"`, payload contains `client/diarchy-kvm`, `host/…`, `VERSION`, exec bits preserved; and `build-app.sh --install` is never executed (grep the transcript).
- [ ] **Step 4: Commit** `git add app/scripts app/Resources && git commit -m "feat(app): bundle build script"`; `send to the orchestrator: "[t3→Main] READY APP-BUNDLE <hash> build-app.sh needs host/VERSION from t2"`.

---

### Task 11: Verification and report

**Files:** Create `docs/superpowers/reports/2026-10-11-t3-mac-app.md`.

- [ ] **Step 1:** `swift build --package-path app -c release 2>&1` — zero warnings; `swift test --package-path app 2>&1` — all pass. Paste real output.
- [ ] **Step 2:** acceptance greps: `grep -rnE "Timer\.(scheduledTimer|publish)" app/Sources` (empty); `grep -rnE "<personal-data patterns>|sudo" app/Sources` (only comments referencing the no-sudo rule, if any); `git diff --stat bbe4c90..HEAD` shows only owned paths.
- [ ] **Step 3:** skill `verification-before-completion`; write the report with: deliverables vs. brief checklist, files changed, commands + real output, every `Ruling:`, deferred minors, what only physical verification can prove (menu open refresh, Settings rendering, pairing against a real host, KVM/streaming flows, installed-app smoke, `host/VERSION` dependency).
- [ ] **Step 4: Commit** `git add docs/superpowers/reports/2026-10-11-t3-mac-app.md && git commit -m "docs(app): t3 report"`; `send to the orchestrator: "[t3→Main] DONE <hash> report=docs/superpowers/reports/2026-10-11-t3-mac-app.md"`.

---

## Self-Review

- Spec coverage: §4.4 → T1; §8.1 → T10; §8.2 → T8; §8.3 → T9 (+ T7 diagnostics); §8.4 → T4; §8.5 → T5; §8.6/§11 → T6; §7 Mac/Host install → T7; §9.3 app side → T5; §10 (validated remote args) → T3/T7; §12 Swift tests list → T1,T3,T4,T5,T6 (+ `ssh-copy-id`, controller IP in T4). No gap found.
- Placeholders: none; reason catalogue and every command string are exact.
- Type consistency: `HostAgentClient`, `CommandRunner.spawn`/`ChildProcess`, `ModeSnapshot` names are used identically in Tasks 3–9.
- Review Focus 1→T2, 2→T4/T7, 3→T6, 4→T1, 5→T4.
- Known cross-team dependencies: `host/VERSION` (t2/t4), bash loader reasons (t1), Sunshine `uinput-access` emitted only when Sunshine installed or "requested" (§6.3 does not say how `probe` is told) — app filters by id; Main informed in the plan message.
