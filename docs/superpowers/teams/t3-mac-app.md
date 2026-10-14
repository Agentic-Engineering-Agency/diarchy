# Team T3 — mac-app

Team id: `t3`. Branch: `feat/diarchy-app--mac-app`.

## Mission

Build the native SwiftUI menu-bar app `Diarchy.app` (spec §8, §9.3 app side, §11) as a SwiftPM package in
`app/`, plus the bundle build/install script. The app orchestrates commands defined in §5 and §6 — it never
re-implements them (ADR 0001).

## Ownership (edit only these)

- `app/**` (new): `app/Package.swift`, `app/Sources/DiarchyCore/**`, `app/Sources/Diarchy/**`,
  `app/Tests/DiarchyCoreTests/**`, `app/scripts/build-app.sh`, `app/Resources/**` (Info.plist template, icon)
- (`.gitignore` already ignores `app/.build/` and `app/build/`; do not edit it.)
- `docs/superpowers/plans/2026-10-11-diarchy-app-t3-mac-app.md`,
  `docs/superpowers/reports/2026-10-11-t3-mac-app.md`

NOT yours: everything in `client/`, `host/`, `tests/`.

## Toolchain facts (verified on this Mac)

Swift 6.4, Xcode 27.0, macOS 27 SDK, arm64. Deployment target macOS 14 (`platforms: [.macOS(.v14)]`). Use Swift
Testing (`import Testing`). Build: `swift build --package-path app`; tests: `swift test --package-path app`.
Read `skill://swiftpm-macos-app-bundle` before writing `build-app.sh`; here the payload lives under
`Contents/Resources/payload` (no SwiftPM resource bundles), so ad-hoc `codesign --force --deep --sign -` is fine.

## Architecture inside `app/`

- `DiarchyCore` (pure logic, fully unit-tested, no SwiftUI):
  - `ConfigCodec` / `DiarchyConfig` / `ConfigStore` — §4 exactly (same validation + same error reasons as the bash
    loader; atomic 0600 write; dir 0700).
  - `CommandRunner` protocol (`run(executable:arguments:environment:stdin:timeout:) async throws -> CommandResult`)
    + `ProcessCommandRunner` (kills on timeout) + test fake.
  - `Paths` (home, `~/.local/bin/*`, Lan Mouse app, payload dir in bundle).
  - `HostAgentClient` — composes `diarchy-ssh -- '~/.local/bin/diarchy-host' <args>`; decodes the §6.1 envelope
    and §6.3 probe into `Codable` types; maps `ok:false` to typed errors.
  - `SetupChecklist` — §8.4 steps and states; `ssh-copy-id` command composition; controller-IP resolution from
    `route -n get` + `ipconfig getifaddr` output.
  - `Pairing` — §8.5.
  - `ModeController` — §8.6 state machine (actor), including Moonlight child lifecycle and rollback.
  - `Installer` — §7 Mac and Host install flows (tar of payload piped into `diarchy-ssh`), streaming log lines.
- `Diarchy` (SwiftUI, thin): `MenuBarExtra` (§8.2) and a `Settings`/`Window` scene with the four tabs (§8.3).
  No timers, no polling; refresh in `.task`/`onAppear` and after actions; all commands off the main actor.

## Stages

1. Package scaffold + `ConfigCodec` with exhaustive tests (mirror the bash loader cases from spec §4.2).
   Commit, message `[t3→Main] READY CORE-CONFIG <hash>`.
2. `CommandRunner`, `HostAgentClient` (decode fixtures of every §6.2 payload and every error code), `SetupChecklist`.
3. `Pairing`, `ModeController` (every transition and failure path in §8.6 / §11), `Installer`.
4. SwiftUI shell: menu, four tabs, checklist rows with `Copy`, side picker diagram, diagnostics view.
5. `app/scripts/build-app.sh [--install]`: assembles the bundle per §8.1, copies `client/`, `host/`, and
   `host/VERSION` (if `host/VERSION` is absent at build time, fail with a clear message) into the payload. Do NOT run
   `--install` yourself; build without `--install` and verify with `plutil -lint` and `codesign --verify`.
6. Verification + report. Main performs the installed-app smoke after integration.

## Acceptance

- `swift build --package-path app -c release` and `swift test --package-path app` pass with zero warnings treated
  as errors where practical.
- Core tests cover every §8.4 step outcome, every §8.6 transition including Moonlight exit and timeouts, and config
  round-trip compatibility with the §4.1 sample.
- The app process has no repeating timers (grep shows no `Timer.scheduledTimer`, `Timer.publish`, polling loops).
