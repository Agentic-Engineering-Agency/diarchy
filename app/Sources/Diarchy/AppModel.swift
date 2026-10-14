import AppKit
import Foundation
import DiarchyCore
import Observation

/// What the menu and the Settings window show, and every action they trigger.
///
/// All state lives on the main actor; every Core call runs off it, through `Backend`, and only plain results come
/// back. There are no timers: the model changes when the user acts, when a view asks for `refresh()`, and when the
/// one `ModeController` publishes a change (for example Moonlight quitting by itself).
@MainActor @Observable
final class AppModel {
    // MARK: State

    /// Editable copy of the configuration; `sshUser` is empty until one is configured.
    var draft = DiarchyConfig(sshUser: "")
    var checklist: ChecklistResult? {
        didSet {
            // The checklist has no probe while the host agent step fails, so the last answer is kept.
            if let probe = checklist?.probe { hostSunshineInstalled = probe.components.sunshine.installed }
        }
    }
    /// The last host probe said Sunshine is installed: a host reinstall from the checklist must keep it.
    private(set) var hostSunshineInstalled = false
    var checklistRunning = false
    var mode = ModeSnapshot(mode: .off)
    var installLog: [String] = []
    /// Bumped by every appended log line. The log is capped, so its length stops changing at the cap; views that
    /// follow the log scroll on this instead.
    private(set) var installLogRevision = 0
    var installRunning = false
    var diagnosticsOutput = ""
    var diagnosticsRunning = false
    var selectedTab: SettingsTab = .connection
    /// Text of the last failed action, without the `Error: ` prefix. It lives until the next action starts or a
    /// `refresh()` derives a new state, so a failed action is never wiped by the refresh that follows it.
    var lastError: String?
    /// Every action the host reports for streaming (`probe --streaming`); see `refreshStreamingActions()`.
    var streamingActions: [RequiredAction] = []
    /// A streaming probe has succeeded at least once, so an empty `streamingActions` means "nothing pending" rather
    /// than "not asked yet".
    private(set) var streamingProbed = false
    var pairingRunning = false
    /// Success note of the last Moonlight pairing; dropped when any other action starts.
    var pairingMessage: String?
    /// Failure of the last `probe --streaming` behind `streamingActions`; `nil` after a successful one.
    var streamingProbeError: String?
    /// Moonlight is installed on this Mac. Streaming only; read again by `refreshStreamingActions()`.
    private(set) var moonlightInstalled = true
    /// `Open at Login`: what the switch shows (see `refreshLoginItem()`).
    var loginItem = LoginItem.state(for: .notRegistered)
    /// The menu popover is open. Driven by MenuBarExtraAccess, so the app can also close it (`Settings…`).
    var isMenuPresented = false
    /// Monitor names the host reported for the Stream Output picker; empty until `refreshStreamMonitors()` succeeds.
    private(set) var streamMonitorNames: [String] = []
    var compatibilityRunning = false
    /// Result of the last "Copy Compatibility Report": a short confirmation or the reason it failed.
    var compatibilityMessage: String?

    /// The configuration as last read from disk; `nil` when there is none (or it cannot be read).
    private var loadedConfig: DiarchyConfig?
    /// What `draft` was last synchronised with; a refresh only replaces the draft while nothing was edited.
    private var draftBaseline = DiarchyConfig(sshUser: "")
    /// An action (or refresh) is running; every other action and refresh is ignored meanwhile.
    private var working = false
    private var streamingActionsRunning = false
    /// A refresh (configuration + checklist + mode) is running. It does not hold `working`, so a stop stays possible.
    private var refreshing = false
    /// The menu's cheap mode refresh is running.
    private var modeRefreshing = false
    /// The Mac install just succeeded, so the host install that follows appends to its log instead of replacing it.
    private var installChainOpen = false
    /// Text of the last failed Mac install; the "Mac Components" row shows it until an install works.
    private var macInstallFailure: String?

    private let backend: Backend
    private let loginItemService: any LoginItemService

    private static let installLogLimit = 2000

    /// The one model of the process: the scenes and `TerminationDelegate` (which exists before any scene) share it.
    static let shared = AppModel()

    init(
        paths: Paths = .live(), runner: any CommandRunner = ProcessCommandRunner(),
        loginItemService: any LoginItemService = SystemLoginItem()
    ) {
        self.loginItemService = loginItemService
        loginItem = LoginItem.state(for: loginItemService.status)
        // One controller for the app's lifetime: its exit watcher and the Moonlight child outlive menu and window.
        // Its changes arrive through a stream, so they are applied in order.
        let (changes, continuation) = AsyncStream.makeStream(of: ModeSnapshot.self)
        let controller = ModeController(
            paths: paths, runner: runner, agent: HostAgentClient(runner: runner, paths: paths),
            onChange: { continuation.yield($0) },
            windowActivator: MoonlightWindowActivator(
                runner: runner, lookup: { RunningApplicationHandle.lookup(pid: $0) }))
        backend = Backend(
            paths: paths, store: ConfigStore(url: paths.configURL), runner: runner, controller: controller)
        Task { [weak self] in
            for await snapshot in changes {
                guard let self else { return }
                self.apply(published: snapshot)
            }
        }
        // The first full check, so the menu knows whether the modes are available before it is ever opened.
        // An earlier run of the app can have left Moonlight and a host stream behind: stop them before the first check.
        Task { [weak self] in
            await controller.stopOrphanedStream()
            await self?.refresh()
        }
    }

    // MARK: Derived

    var hasConfig: Bool { loadedConfig != nil }

    /// Something is running that must not overlap with a mode change.
    var isBusy: Bool {
        working || refreshing || checklistRunning || installRunning || pairingRunning || mode.mode == .transitioning
    }

    var statusLine: String {
        StatusLine.make(config: loadedConfig, checklist: checklist, mode: mode.mode, error: lastError ?? mode.error)
    }

    /// How the menu draws `statusLine`: the icon and its color (same inputs, so they never disagree).
    var statusTone: StatusLine.Tone {
        StatusLine.tone(config: loadedConfig, checklist: checklist, mode: mode.mode, error: lastError ?? mode.error)
    }

    /// Whether the picker's segment for `target` can be chosen now: nothing else may be running, and `ModeAvailability`
    /// decides the rest. The checklist counts as running (so KVM and streaming wait for it) but `Off` does not.
    func isSelectable(_ target: ModeTarget) -> Bool {
        guard !working, !installRunning, !pairingRunning, mode.mode != .transitioning else { return false }
        if target != .off && checklistRunning { return false }
        return ModeAvailability.isSelectable(target, config: loadedConfig, checklist: checklist, mode: mode.mode)
    }

    /// Why "Install …" cannot be used now (streaming running or a mode change in flight); `nil` when it can.
    var installBlockedReason: String? { ModeAvailability.installBlockedReason(mode: mode.mode) }

    /// Why KVM and streaming cannot be chosen yet; `nil` when the setup is complete.
    var modesDisabledReason: String? {
        ModeAvailability.evaluate(config: loadedConfig, checklist: checklist, mode: .off).reason
    }

    /// The reason `path` is not a valid SSH identity file, from the configuration validator; `nil` when it is valid
    /// or empty. Checked on a neutral configuration so the answer concerns the identity file alone.
    func validateIdentityPath(_ path: String) -> String? {
        var candidate = DiarchyConfig(sshUser: "user", lanHost: "host")
        candidate.sshIdentityFile = path
        do {
            try candidate.validate()
            return nil
        } catch {
            return error.reason
        }
    }

    /// Chooses the tab the Settings window shows after a menu command (`SettingsTabChoice` decides which).
    func showSettings(_ request: SettingsTabChoice.Request, windowOpen: Bool) {
        selectedTab = SettingsTabChoice.tab(
            for: request, current: selectedTab, windowOpen: windowOpen, hasConfig: hasConfig)
    }

    // MARK: Refresh

    /// Loads the configuration, runs the checklist and re-derives the mode. Single-flight: ignored while another
    /// refresh or action runs (an action ends with its own re-check). The previous error is dropped when it starts
    /// (unless a transition is running, which reports its own); the error the derivation produces arrives, like
    /// every mode change, through the controller's stream.
    func refresh() async {
        guard !working, !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        checklistRunning = true
        defer { checklistRunning = false }
        if mode.mode != .transitioning { lastError = nil }

        let loaded = await backend.loadConfig()
        applyLoaded(loaded)
        async let list = backend.runChecklist(macInstallFailure: macInstallFailure)
        async let derived: Void = backend.refreshMode(configured: loaded != nil)
        checklist = await list
        await derived
    }

    /// The cheap refresh the menu runs when it opens: only re-derives the mode (`diarchy-kvm status`, local), so the
    /// picker is never held back by the checklist's network checks. Single-flight, and it takes no part in the
    /// action discipline, so it can neither block a mode change nor erase an error (a derived error only ever sets
    /// `lastError`, see `note(controllerError:)`).
    func refreshMode() async {
        guard !modeRefreshing, loadedConfig != nil else { return }
        modeRefreshing = true
        defer { modeRefreshing = false }
        await backend.refreshMode(configured: true)
    }

    /// Reloads the configuration and re-runs the checklist only. Used at the end of actions, so it never touches the
    /// mode and never clears `lastError`.
    private func recheck() async {
        checklistRunning = true
        defer { checklistRunning = false }
        applyLoaded(await backend.loadConfig())
        checklist = await backend.runChecklist(macInstallFailure: macInstallFailure)
    }

    private func applyLoaded(_ config: DiarchyConfig?) {
        let edited = draft != draftBaseline
        loadedConfig = config
        guard let config else { return }
        if !edited { draft = config }
        draftBaseline = config
    }

    /// Refreshes `streamingActions` from `probe --streaming`. Single-flight. Its failure goes to
    /// `streamingProbeError`, not `lastError`: this probe runs outside the action discipline, so it must neither
    /// overwrite a failed action's error nor survive the next action.
    func refreshStreamingActions() async {
        moonlightInstalled = backend.paths.moonlightInstalled
        guard !streamingActionsRunning else { return }
        streamingActionsRunning = true
        defer { streamingActionsRunning = false }
        switch await backend.probeStreaming(lanHost: loadedConfig?.lanHost ?? "") {
        case .success(let probe):
            streamingActions = probe.requiredActions
            streamingProbed = true
            streamingProbeError = nil
        case .failure(let error):
            streamingProbeError = error.userMessage
        }
    }

    // MARK: Configuration

    /// Validates the draft, saves it (the fields the Settings window does not edit, such as the host fingerprint,
    /// stay as they are on disk) and runs the checklist. When the save changes what the Lan Mouse configuration
    /// renders (a new host address, say), that configuration is rendered again, so KVM never dials a stale address.
    func saveAndCheck() async {
        guard beginWork() else { return }
        defer { working = false }
        lastError = nil
        let previous = loadedConfig
        switch await backend.saveSettings(draft) {
        case .failure(let error):
            lastError = error.description
            return
        case .success(let saved):
            loadedConfig = saved
            draftBaseline = saved
            draft = saved
            if previous.map(saved.lanMouseConfigDiffers(from:)) ?? true, backend.canConfigureKVM {
                await applyLanMouseConfig()
            }
        }
        await recheck()
    }

    /// Saves the new side, renders the Lan Mouse configuration, and when KVM is on restarts it (`off`, then `on`).
    func setSide(_ side: KVMSide) async {
        guard beginWork() else { return }
        defer { working = false }
        lastError = nil
        switch await backend.saveSide(side, fallback: draft) {
        case .failure(let error):
            lastError = error.description
            return
        case .success(let saved):
            loadedConfig = saved
            draftBaseline = saved
            draft.kvmSide = saved.kvmSide
        }
        await applyLanMouseConfig()
    }

    /// Renders the Lan Mouse configuration (`diarchy-kvm-configure`) and, when KVM is on, restarts it (`off`, then
    /// `kvm`) so the running instance reads it. A failure goes to `lastError` and stops the sequence.
    private func applyLanMouseConfig() async {
        if let failure = await backend.configureKVM() {
            lastError = failure
            return
        }
        guard case .kvm = mode.mode else { return }
        guard settled(await backend.controller.setMode(.off), expecting: .off) else { return }
        _ = settled(await backend.controller.setMode(.kvm), expecting: .kvm)
    }

    // MARK: Install and pairing

    func installMac(streaming: Bool) async {
        guard installBlockedReason == nil else {
            lastError = installBlockedReason
            return
        }
        guard beginWork() else { return }
        defer { working = false }
        lastError = nil
        installLog = []
        installChainOpen = false
        installRunning = true
        macInstallFailure = nil
        let failure = await collectingLog { onLine in
            await self.backend.installMac(includeStreaming: streaming, onLine: onLine)
        }
        installRunning = false
        installChainOpen = failure == nil
        macInstallFailure = failure?.rowMessage
        lastError = failure?.userMessage
        await recheck()
    }

    func installHost(streaming: Bool) async {
        guard installBlockedReason == nil else {
            lastError = installBlockedReason
            return
        }
        guard beginWork() else { return }
        defer { working = false }
        lastError = nil
        if !installChainOpen { installLog = [] }
        installChainOpen = false
        installRunning = true
        appendLog("==> Installing on the host")
        let failure = await collectingLog { onLine in
            await self.backend.installHost(withStreaming: streaming, onLine: onLine)
        }
        installRunning = false
        lastError = failure?.userMessage
        await recheck()
    }

    /// Runs `run` with a line sink and appends the lines to `installLog` in the order they arrive.
    private func collectingLog(
        _ run: (@escaping @Sendable (String) -> Void) async -> InstallerError?
    ) async -> InstallerError? {
        let (lines, continuation) = AsyncStream.makeStream(of: String.self)
        let consumer = Task {
            for await line in lines { appendLog(line) }
        }
        let failure = await run { continuation.yield($0) }
        continuation.finish()
        await consumer.value
        return failure
    }

    private func appendLog(_ line: String) {
        installLog.append(line)
        installLogRevision &+= 1
        if installLog.count > Self.installLogLimit { installLog.removeFirst(installLog.count - Self.installLogLimit) }
    }

    /// The "Confirm Host Key" row: scans the host key, asks the user to compare the fingerprint, pins it on
    /// "Trust Host", and re-runs the checklist.
    func confirmHostKeyStep() async {
        guard beginWork() else { return }
        defer { working = false }
        lastError = nil
        _ = await confirmHostKey()
        await recheck()
    }

    /// KVM pairing (spec §8.5); the checklist re-run is its last step.
    func pair() async {
        guard beginWork() else { return }
        defer { working = false }
        lastError = nil
        guard await confirmHostKey() else { return }
        pairingRunning = true
        if case .failure(let error) = await backend.pair() { lastError = error.userMessage }
        pairingRunning = false
        // Pairing runs `diarchy-kvm off`, so the mode may have changed.
        await refreshMode()
        await recheck()
    }

    /// Moonlight pairing (spec §9.3) against the host `diarchy-stream --dry-run` picks (the one whose Sunshine port is
    /// reachable). Sunshine only runs in Streaming mode, so the pairing starts the host stream itself (and stops it
    /// again unless Streaming was already on). Pairing is transient: KVM, which cannot coexist with the stream, is
    /// switched off for it and brought back afterwards whatever the outcome (`PairingModeFlow`).
    func pairMoonlight() async {
        guard beginWork() else { return }
        defer { working = false }
        lastError = nil
        guard loadedConfig != nil else {
            lastError = "Set up the host connection before pairing Moonlight."
            return
        }
        let target: ModeTarget
        switch mode.mode {
        case .transitioning:
            lastError = "Another mode change is in progress. Try again when it finishes."
            return
        case .kvm: target = .kvm
        case .streaming: target = .streaming
        case .off: target = .off
        }
        guard await confirmHostKey() else { return }
        pairingRunning = true
        defer {
            pairingRunning = false
            let waiting = pairingWaiters
            pairingWaiters = []
            for waiter in waiting { waiter.resume() }
        }
        let backend = self.backend
        let run = await PairingModeFlow.run(from: target, controller: backend.controller) { alreadyRunning in
            await backend.pairMoonlight(streamAlreadyRunning: alreadyRunning)
        }
        if let suspended = run.suspended, run.pairing == nil {
            _ = settled(suspended, expecting: .off)  // KVM would not switch off: nothing was paired
            return
        }
        switch run.pairing {
        case .success(let outcome)?: pairingMessage = outcome.userMessage
        case .failure(let error)?: lastError = error.userMessage
        case nil: break
        }
        if let restored = run.restored {
            // A failed pairing keeps its own message; a failed return to KVM is shown when nothing else is.
            let pairingError = lastError
            _ = settled(restored, expecting: .kvm)
            if let pairingError { lastError = pairingError }
        }
    }

    // MARK: Mode

    func setMode(_ target: ModeTarget) async {
        guard !ModeAvailability.isNoOp(current: mode.mode, target: target) else { return }
        guard target == .off ? beginStop() : beginWork() else { return }
        defer { working = false }
        lastError = nil

        if target == .streaming, let refusal = await streamingGateRefusal() {
            lastError = refusal
            return
        }
        _ = settled(await backend.controller.setMode(target), expecting: nil)
    }

    /// Streaming needs the host's `required` actions done (`uinput-access`, …). The app never runs them: it refuses
    /// and tells the user which command to run on the host (`StreamingGate`). `nil` when streaming may start.
    private func streamingGateRefusal() async -> String? {
        switch await backend.probeStreaming(lanHost: loadedConfig?.lanHost ?? "") {
        case .failure(let error):
            return error.userMessage
        case .success(let probe):
            streamingActions = probe.requiredActions
            streamingProbed = true
            return StreamingGate.refusal(for: probe)
        }
    }

    /// Judges the controller's answer to a transition the model asked for; it never writes `mode` (see
    /// `apply(published:)`). A busy answer is another transition's state, not a result. Returns `true` when the
    /// transition reached `expecting` (any settled state when `nil`) without an error.
    private func settled(_ snapshot: ModeSnapshot, expecting: ModeTarget?) -> Bool {
        if snapshot.mode == .transitioning {
            lastError = "Another mode change is in progress. Try again when it finishes."
            return false
        }
        note(controllerError: snapshot.error)
        guard snapshot.error == nil else { return false }
        switch (expecting, snapshot.mode) {
        case (nil, _): return true
        case (.some(let target), let mode): return ModeAvailability.isNoOp(current: mode, target: target)
        }
    }

    /// The one writer of `mode`: the controller's change stream, in the order the controller produced it (a returned
    /// snapshot is never written, so it can neither be stale nor race the stream). Moonlight quitting on its own
    /// arrives here too.
    private func apply(published snapshot: ModeSnapshot) {
        mode = snapshot
        note(controllerError: snapshot.error)
    }

    /// The one writer of controller-derived errors into `lastError`, shared by the stream and `settled`: it only ever
    /// sets a message, never blanks one, so whichever of the two runs last cannot erase an error the other showed.
    /// Errors go away when an action starts or a refresh begins.
    private func note(controllerError error: String?) {
        if let error { lastError = error }
    }

    /// Callers of `prepareForTermination()` suspended until the running pairing has cleaned up after itself.
    @ObservationIgnored private var pairingWaiters: [CheckedContinuation<Void, Never>] = []

    private func waitForPairing() async {
        guard pairingRunning else { return }
        await withCheckedContinuation { pairingWaiters.append($0) }
    }

    /// What must happen before the app may terminate, whatever asked it to (see `TerminationDelegate`; `Quit` just
    /// calls `NSApp.terminate`). A running pairing is waited for first (it stops the host stream it started and
    /// returns KVM), then a running stream is stopped (Moonlight is terminated, then `diarchy-host stream stop`) so
    /// no orphaned Moonlight or host stream outlives the app; KVM is left as it is. Waits for a transition that is
    /// already running instead of racing it. Deliberately skips `beginWork`: a refresh in flight must not block
    /// quitting, and the app terminates whatever the stop's outcome.
    func prepareForTermination() async {
        await waitForPairing()
        var state = await backend.controller.waitUntilIdle()
        if state.mode == .streaming {
            state = await backend.controller.setMode(.off)
            if state.mode == .transitioning { _ = await backend.controller.waitUntilIdle() }
        }
    }

    /// Quit timed out while a stream was starting or stopping: starts a detached `stream stop` so the host stream does
    /// not outlive the app.
    func stopStreamOnQuitTimeout() async {
        await backend.controller.spawnDetachedStreamStop()
    }

    // MARK: Login item

    /// Re-reads the registration from the system: the user may have changed it in System Settings.
    func refreshLoginItem() {
        loginItem = LoginItem.state(for: loginItemService.status)
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        loginItem = LoginItem.setEnabled(enabled, service: loginItemService)
    }

    // MARK: Host key

    /// Fingerprint the user must confirm before the first SSH call of a pairing; `nil` when no question is open.
    private(set) var hostKeyPrompt: String?
    @ObservationIgnored private var hostKeyAnswer: CheckedContinuation<Bool, Never>?

    /// Answers the open host key question (`Trust Host` is `true`, `Cancel` is `false`).
    func answerHostKey(_ trusted: Bool) {
        hostKeyAnswer?.resume(returning: trusted)
        hostKeyAnswer = nil
    }

    /// Makes sure the host key is pinned. When it is not, scans it and asks the user to compare the fingerprint
    /// with the host's. Only an explicit `Trust Host` pins the key. Failures go to `lastError`.
    private func confirmHostKey() async -> Bool {
        let trust = backend.hostKeyTrust
        if case .trusted = await trust.status() { return true }
        let fingerprint: String
        do throws(HostKeyError) {
            fingerprint = try await trust.scan()
        } catch {
            lastError = "Could not read the host key: \(error)"
            return false
        }
        hostKeyPrompt = fingerprint
        let accepted = await withCheckedContinuation { hostKeyAnswer = $0 }
        hostKeyPrompt = nil
        guard accepted else {
            lastError = "The host key was not confirmed."
            return false
        }
        do throws(HostKeyError) {
            try await trust.trust(fingerprint: fingerprint)
        } catch {
            lastError = "Could not save the host key: \(error)"
            return false
        }
        return true
    }

    // MARK: Diagnostics

    func runDiagnostics() async {
        guard !diagnosticsRunning else { return }
        diagnosticsRunning = true
        defer { diagnosticsRunning = false }
        diagnosticsOutput = await backend.diagnostics()
    }

    /// Builds the compatibility report (redacted by Core) and puts the Markdown on the clipboard.
    func copyCompatibilityReport() async {
        guard !compatibilityRunning else { return }
        compatibilityRunning = true
        compatibilityMessage = nil
        defer { compatibilityRunning = false }
        let report = await backend.compatibilityReport()
        copyToPasteboard(report)
        compatibilityMessage = "Copied"
    }

    /// Asks the host for its monitors. Without a configuration or when the host is not reachable the list stays
    /// empty, so the picker shows only "Automatic" and the saved value.
    func refreshStreamMonitors() async {
        guard loadedConfig != nil else { return }
        streamMonitorNames = await backend.monitorNames()
    }

    // MARK: Plumbing

    /// Starts an action: nothing else may run meanwhile, and the previous action's success note is dropped. Refused
    /// while a refresh runs too (an action ends with its own re-check).
    private func beginWork() -> Bool {
        guard !working, !refreshing else { return false }
        working = true
        pairingMessage = nil
        return true
    }

    /// Like `beginWork`, but allowed while a refresh runs: stopping a mode only runs local commands, so the user
    /// must not wait for the checklist's network checks to switch KVM or streaming off.
    private func beginStop() -> Bool {
        guard !working else { return false }
        working = true
        pairingMessage = nil
        return true
    }
}

/// Everything that touches Core. A `Sendable` value, so its `async` methods run off the main actor and only plain
/// results cross back.
private struct Backend: Sendable {
    let paths: Paths
    let store: ConfigStore
    let runner: any CommandRunner
    let controller: ModeController

    private static let localTimeout: TimeInterval = 20

    private var agent: HostAgentClient { HostAgentClient(runner: runner, paths: paths) }

    /// `nil` when there is no configuration or it cannot be read (the checklist's step 2 explains why).
    func loadConfig() async -> DiarchyConfig? {
        (try? store.load()).flatMap { $0 }
    }

    func runChecklist(macInstallFailure: String?) async -> ChecklistResult {
        await SetupChecklist(
            paths: paths, store: store, runner: runner, bundledVersion: paths.bundledVersion(),
            macInstallFailure: macInstallFailure
        ).run()
    }

    /// Asks the controller to re-derive the mode (its change reaches the model through the controller's stream), but
    /// only when `diarchy-kvm status` can mean something: with no configuration or no `diarchy-kvm` the command
    /// would only fail, so nothing runs.
    func refreshMode(configured: Bool) async {
        let installed = FileManager.default.isExecutableFile(atPath: paths.localBin("diarchy-kvm"))
        guard configured, installed else { return }
        _ = await controller.refresh()
    }

    /// Saves the Settings draft on top of the file (`ConfigStore.saveSettings`): validated before writing, with the
    /// host fingerprint and key alias kept from disk.
    func saveSettings(_ draft: DiarchyConfig) async -> Result<DiarchyConfig, ConfigError> {
        do throws(ConfigError) {
            return .success(try store.saveSettings(draft))
        } catch {
            return .failure(error)
        }
    }

    /// Saves only the KVM side; `fallback` is the starting point when there is no usable file.
    func saveSide(_ side: KVMSide, fallback: DiarchyConfig) async -> Result<DiarchyConfig, ConfigError> {
        do throws(ConfigError) {
            return .success(try store.update(fallback: fallback) { $0.kvmSide = side })
        } catch {
            return .failure(error)
        }
    }

    /// `diarchy-kvm-configure` is installed; without it (Mac installer not run yet) there is nothing to render.
    var canConfigureKVM: Bool { FileManager.default.isExecutableFile(atPath: paths.localBin("diarchy-kvm-configure")) }

    /// `diarchy-kvm-configure`; `nil` on success, else the message to show.
    func configureKVM() async -> String? {
        do {
            let result = try await runner.run(
                executable: paths.localBin("diarchy-kvm-configure"), arguments: [], environment: nil, stdin: nil,
                timeout: Self.localTimeout)
            return result.succeeded ? nil : result.firstErrorLine
        } catch let error as CommandError {
            switch error {
            case .launchFailed(_, let message): return message
            case .timedOut: return "diarchy-kvm-configure did not respond in time"
            }
        } catch {
            return String(describing: error)
        }
    }

    func probeStreaming(lanHost: String) async -> Result<ProbeResult, HostAgentError> {
        let controllerIP = await ControllerIP.resolve(lanHost: lanHost, runner: runner)
        do throws(HostAgentError) {
            return .success(try await agent.probe(controllerIP: controllerIP, streaming: true))
        } catch {
            return .failure(error)
        }
    }

    func installMac(
        includeStreaming: Bool, onLine: @escaping @Sendable (String) -> Void
    ) async -> InstallerError? {
        do throws(InstallerError) {
            try await Installer(paths: paths, runner: runner).installMac(includeStreaming: includeStreaming, onLine: onLine)
            return nil
        } catch {
            return error
        }
    }

    func installHost(withStreaming: Bool, onLine: @escaping @Sendable (String) -> Void) async -> InstallerError? {
        do throws(InstallerError) {
            try await Installer(paths: paths, runner: runner).installHost(withStreaming: withStreaming, onLine: onLine)
            return nil
        } catch {
            return error
        }
    }

    var hostKeyTrust: HostKeyTrust { HostKeyTrust(paths: paths, runner: runner) }

    func pair() async -> Result<DiarchyConfig, PairingError> {
        do throws(PairingError) {
            return .success(try await Pairing(paths: paths, store: store, runner: runner, agent: agent).pair())
        } catch {
            return .failure(error)
        }
    }

    func pairMoonlight(streamAlreadyRunning: Bool) async -> Result<MoonlightPairingOutcome, MoonlightPairingError> {
        let pairing = MoonlightPairing(paths: paths, runner: runner, agent: agent)
        do throws(MoonlightPairingError) {
            return .success(try await pairing.pair(hostStreamAlreadyRunning: streamAlreadyRunning))
        } catch {
            return .failure(error)
        }
    }

    func diagnostics() async -> String {
        await Diagnostics(paths: paths, runner: runner).run()
    }

    func compatibilityReport() async -> String {
        await CompatibilityReporter(paths: paths, runner: runner).collect()
    }

    /// Monitor names from `diarchy-host report`; empty when the host cannot be reached.
    func monitorNames() async -> [String] {
        guard let report = try? await agent.report() else { return [] }
        return report.monitors.map(\.name)
    }
}
