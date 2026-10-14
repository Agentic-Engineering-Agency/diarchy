import Foundation

public enum Mode: Equatable, Sendable {
    case off
    case kvm(degraded: Bool)
    case streaming
    case transitioning
}

public enum ModeTarget: Equatable, Sendable {
    case off
    case kvm
    case streaming
}

public struct ModeSnapshot: Equatable, Sendable {
    public var mode: Mode
    /// Text to show after `Error: `; never carries the prefix itself.
    public var error: String?

    public init(mode: Mode, error: String? = nil) {
        self.mode = mode
        self.error = error
    }
}

/// The off / KVM / streaming state machine (spec §8.6 and §11). It runs the transitions as commands and owns the
/// Moonlight child; it keeps no timers, and the only sleep is the injected TERM→KILL race.
///
/// Transitions never overlap: while one runs the mode is `.transitioning` and `setMode`/`refresh` answer with the
/// current snapshot without running anything. Every transition is a chain of steps that each must succeed; on failure
/// the chain stops and its rollback runs.
public actor ModeController {
    private let paths: Paths
    private let runner: any CommandRunner
    private let agent: HostAgentClient
    private let sleep: @Sendable (Duration) async throws -> Void
    private let isExecutable: @Sendable (String) -> Bool
    private let now: @Sendable () -> ContinuousClock.Instant
    private let onChange: @Sendable (ModeSnapshot) -> Void
    private let windowActivator: any StreamWindowActivating

    private var mode: Mode = .off
    private var error: String?
    /// The running Moonlight child, `nil` unless streaming.
    private var child: (any ChildProcess)?
    /// Identifies the child the exit watcher is waiting for; bumped once per started child. A watcher whose child was
    /// released finds `child == nil`, or (after a restart) a newer number, and does nothing.
    private var generation = 0
    /// Exit watchers that have not finished yet, by generation; each removes itself when done. Only a test hook reads it.
    private var watchers: [Int: Task<Void, Never>] = [:]
    private var newestWatcher = 0
    /// Bumped on every state change, so a `refresh` that was suspended across one can tell its result is stale.
    private var epoch = 0
    /// A `diarchy-host stream stop` failed and the host stream may still run. The next refresh, or choosing Off, retries it.
    private var pendingStop = false
    /// Callers of `waitUntilIdle` suspended until the running transition ends.
    private var idleWaiters: [CheckedContinuation<ModeSnapshot, Never>] = []

    /// Limit for every local command (spec §11); ssh commands carry the agent client's own limit.
    private static let localTimeout: TimeInterval = 20
    /// A Moonlight child that exits sooner than this after its spawn is reported as having died at once.
    private static let quickExit: Duration = .seconds(5)
    private static let quickExitMessage = "Streaming closed immediately. Run Diagnostics to see why."
    private static let streamingNotInstalled =
        "Streaming is not installed on this Mac (use \"Install Streaming\" in Settings)."
    /// Time between SIGTERM and SIGKILL for the Moonlight child.
    private static let terminateGrace: Duration = .seconds(5)

    public init(
        paths: Paths, runner: any CommandRunner, agent: HostAgentClient,
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        isExecutable: @escaping @Sendable (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) },
        now: @escaping @Sendable () -> ContinuousClock.Instant = { ContinuousClock.now },
        onChange: @escaping @Sendable (ModeSnapshot) -> Void = { _ in },
        windowActivator: any StreamWindowActivating = NoWindowActivation()
    ) {
        self.paths = paths
        self.runner = runner
        self.agent = agent
        self.sleep = sleep
        self.isExecutable = isExecutable
        self.now = now
        self.onChange = onChange
        self.windowActivator = windowActivator
    }

    public var snapshot: ModeSnapshot { ModeSnapshot(mode: mode, error: error) }

    /// Suspends while a transition runs and returns the settled snapshot (at once when none runs). Callers are
    /// resumed by the state change that ends the transition, so nothing polls.
    public func waitUntilIdle() async -> ModeSnapshot {
        guard mode == .transitioning else { return snapshot }
        return await withCheckedContinuation { (continuation: CheckedContinuation<ModeSnapshot, Never>) in
            idleWaiters.append(continuation)
            idleWaiterObserver?()
        }
    }

    /// Test hook: called each time a caller starts waiting in `waitUntilIdle`, so a test can tell the waiter is parked.
    var idleWaiterObserver: (@Sendable () -> Void)?

    func observeIdleWaiters(_ observer: @escaping @Sendable () -> Void) { idleWaiterObserver = observer }

    // MARK: Refresh

    /// Derives the mode: a live Moonlight child means streaming (no command runs); otherwise `diarchy-kvm status`
    /// decides. Its error replaces whatever error the previous action left.
    public func refresh() async -> ModeSnapshot {
        guard mode != .transitioning else { return snapshot }
        if child != nil { return finish(Outcome(mode: .streaming)) }

        let startedAt = epoch
        let retriedStop = pendingStop ? await streamStopFailure() : nil
        var outcome: Outcome
        switch await runLocal("diarchy-kvm", ["status"]) {
        case .failure(let failure): outcome = Outcome(mode: .off, error: failure.message)
        case .success(let result): outcome = Self.derive(from: result)
        }
        if let retriedStop { outcome.error = retriedStop }
        // A transition (or another state change) ran while the command was in flight: its result is newer.
        guard epoch == startedAt else { return snapshot }
        return finish(outcome)
    }

    /// The first line is `Diarchy KVM: <enabled|degraded|disabled> (…)`; the exit code is not consulted because
    /// `degraded` and `disabled` exit 1.
    private static func derive(from result: CommandResult) -> Outcome {
        let prefix = "Diarchy KVM:"
        let firstLine = result.stdout.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
        if firstLine.hasPrefix(prefix) {
            switch firstLine.dropFirst(prefix.count).split(whereSeparator: \.isWhitespace).first {
            case "enabled": return Outcome(mode: .kvm(degraded: false))
            case "degraded": return Outcome(mode: .kvm(degraded: true))
            case "disabled": return Outcome(mode: .off)
            default: break
            }
        }
        return Outcome(mode: .off, error: "diarchy-kvm status: unrecognized output: \(result.firstErrorLine)")
    }

    // MARK: Transitions

    public func setMode(_ target: ModeTarget) async -> ModeSnapshot {
        let from = mode
        let retryStop = target == .off && pendingStop
        guard from != .transitioning, retryStop || !Self.reaches(target, from: from) else { return snapshot }
        begin()
        let outcome: Outcome
        switch target {
        case .off: outcome = await goOff(from: from)
        case .kvm: outcome = await goKVM(from: from)
        case .streaming: outcome = await goStreaming(from: from)
        }
        return finish(outcome)
    }

    /// `.kvm(degraded: true)` does not count as reached: selecting KVM repairs it.
    private static func reaches(_ target: ModeTarget, from mode: Mode) -> Bool {
        switch (target, mode) {
        case (.off, .off), (.kvm, .kvm(degraded: false)), (.streaming, .streaming): true
        default: false
        }
    }

    private struct Outcome {
        var mode: Mode
        var error: String?
    }

    private func goOff(from: Mode) async -> Outcome {
        switch from {
        case .streaming: return await stopStreaming()
        case .kvm:
            if let failure = await runChecked("diarchy-kvm", ["off"]) { return Outcome(mode: from, error: failure) }
            return Outcome(mode: .off)
        case .off: return Outcome(mode: from, error: await streamStopFailure())
        case .transitioning: return Outcome(mode: from)
        }
    }

    private func goKVM(from: Mode) async -> Outcome {
        switch from {
        case .streaming:
            let stopped = await stopStreaming()
            if stopped.error != nil { return stopped }
        case .kvm(degraded: true):
            // A half-running KVM is repaired by the normal sequence on a clean slate.
            if let failure = await runChecked("diarchy-kvm", ["off"]) { return Outcome(mode: from, error: failure) }
        case .off, .kvm(degraded: false), .transitioning: break
        }
        return await enableKVM()
    }

    private func goStreaming(from: Mode) async -> Outcome {
        // Nothing on the Mac or the host is touched when the Mac side cannot run a stream anyway.
        guard isExecutable(paths.localBin("diarchy-stream")), isExecutable(paths.moonlightExecutable) else {
            return Outcome(mode: from, error: Self.streamingNotInstalled)
        }
        if case .kvm = from, let failure = await runChecked("diarchy-kvm", ["off"]) {
            return Outcome(mode: from, error: failure)
        }
        return await startStreaming()
    }

    /// `diarchy-host kvm start` → `diarchy-kvm on` → `diarchy-kvm status` (exit 0 only when enabled). Once the host
    /// service is up, a failing step runs `diarchy-kvm off` as the rollback.
    private func enableKVM() async -> Outcome {
        do throws(HostAgentError) {
            try await agent.kvmStart()
        } catch {
            return Outcome(mode: .off, error: error.userMessage)
        }
        var failure = await runChecked("diarchy-kvm", ["on"])
        if failure == nil { failure = await runChecked("diarchy-kvm", ["status"]) }
        guard let failure else { return Outcome(mode: .kvm(degraded: false)) }
        if let rollback = await runChecked("diarchy-kvm", ["off"]) {
            return Outcome(mode: .off, error: "\(failure) (diarchy-kvm off also failed: \(rollback))")
        }
        return Outcome(mode: .off, error: failure)
    }

    /// `diarchy-host stream start` → spawn `diarchy-stream`; either failing stops the host stream again.
    private func startStreaming() async -> Outcome {
        do throws(HostAgentError) {
            try await agent.streamStart(output: streamOutput())
        } catch {
            _ = await streamStopFailure()  // best effort: the start error is the one that matters
            return Outcome(mode: .off, error: error.userMessage)
        }

        let process: any ChildProcess
        do {
            process = try runner.spawn(
                executable: paths.localBin("diarchy-stream"), arguments: [], environment: nil)
        } catch {
            var message = "Could not start diarchy-stream: \(Self.describe(error))"
            if let stopFailure = await streamStopFailure() {
                message += " (diarchy-host stream stop also failed: \(stopFailure))"
            }
            return Outcome(mode: .off, error: message)
        }

        child = process
        generation += 1
        let watching = generation
        newestWatcher = watching
        let now = self.now
        let spawnedAt = now()
        watchers[watching] = Task { [weak self] in
            let code = await process.waitForExit()
            let lifetime = spawnedAt.duration(to: now())
            await self?.moonlightExited(
                generation: watching, lifetime: lifetime, exitCode: code, errorLine: process.lastErrorLine)
            await self?.watcherFinished(watching)
        }
        windowActivator.bringToFront(pid: process.pid)
        return Outcome(mode: .streaming)
    }

    /// Releases the child first so its exit is not mistaken for Moonlight quitting on its own, then terminates it
    /// (TERM, the injected 5 s race, KILL) and stops the host stream.
    private func stopStreaming() async -> Outcome {
        windowActivator.cancel()
        let process = child
        child = nil
        await process?.terminateThenKill(after: Self.terminateGrace, sleep: sleep)
        return Outcome(mode: .off, error: await streamStopFailure())
    }

    /// Moonlight quit by itself: stop the host stream and go off. Ignored when the child was released meanwhile.
    /// A child that lived shorter than `quickExit` is reported: it most likely failed to start, and its output is lost.
    private func moonlightExited(
        generation exited: Int, lifetime: Duration, exitCode: Int32, errorLine: String?
    ) async {
        guard exited == generation, child != nil else { return }
        child = nil
        windowActivator.cancel()
        begin()
        var message = lifetime < Self.quickExit ? Self.quickExitMessage : nil
        if exitCode != 0 {
            let detail = "diarchy-stream exited with code \(exitCode)" + (errorLine.map { ": \($0)" } ?? "")
            message = message.map { "\($0) (\(detail))" } ?? detail
        }
        if let stopFailure = await streamStopFailure() {
            message = message.map { "\($0) (diarchy-host stream stop also failed: \(stopFailure))" } ?? stopFailure
        }
        finish(Outcome(mode: .off, error: message))
    }

    private func watcherFinished(_ generation: Int) { watchers[generation] = nil }

    /// Resolves once the exit watcher of session `generation` (default: the most recent one) has finished, whether it
    /// acted or was ignored. Generations count from 1 in the order the Moonlight children were started.
    func exitWatcherFinished(generation: Int? = nil) async {
        await watchers[generation ?? newestWatcher]?.value
    }

    // MARK: Commands

    private struct Failure: Error {
        var message: String
    }

    private func runLocal(_ name: String, _ arguments: [String]) async -> Result<CommandResult, Failure> {
        do {
            return .success(
                try await runner.run(
                    executable: paths.localBin(name), arguments: arguments, environment: nil, stdin: nil,
                    timeout: Self.localTimeout))
        } catch {
            return .failure(Failure(message: Self.describe(error)))
        }
    }

    /// `nil` on exit 0, else the message to show.
    private func runChecked(_ name: String, _ arguments: [String]) async -> String? {
        switch await runLocal(name, arguments) {
        case .failure(let failure): failure.message
        case .success(let result): result.succeeded ? nil : result.firstErrorLine
        }
    }

    /// `nil` when `diarchy-host stream stop` succeeded, else the message to show. A failure leaves a pending stop
    /// that a later refresh (or choosing Off) retries; a success clears it.
    private func streamStopFailure() async -> String? {
        do throws(HostAgentError) {
            try await agent.streamStop()
            pendingStop = false
            return nil
        } catch {
            pendingStop = true
            return error.userMessage
        }
    }

    /// Launch cleanup: SIGTERM to a `diarchy-stream` or Moonlight that an earlier run of the app left behind (this
    /// controller owns no child yet), then a host `stream stop` when anything was found. Does nothing while
    /// streaming or transitioning, so it can never stop a live stream.
    public func stopOrphanedStream() async {
        guard mode == .off, child == nil else { return }
        var found = false
        for path in [paths.localBin("diarchy-stream"), paths.moonlightExecutable] {
            let pattern = "^(/bin/bash |/bin/sh |/usr/bin/env )?" + NSRegularExpression.escapedPattern(for: path)
            let result = try? await runner.run(
                executable: "/usr/bin/pkill", arguments: ["-TERM", "-f", pattern], environment: nil, stdin: nil,
                timeout: Self.localTimeout)
            if result?.exitCode == 0 { found = true }
        }
        if found { pendingStop = true }
    }

    /// Quit-timeout fallback: starts `diarchy-ssh -- … diarchy-host stream stop` in its own process group and does
    /// not wait for it, so the host stream stops even when the app exits first.
    public func spawnDetachedStreamStop() {
        agent.spawnDetachedStreamStop()
    }

    /// The `DIARCHY_STREAM_OUTPUT` value for `diarchy-host stream start --output`. An absent, unreadable or invalid
    /// file gives `auto`, so the host picks the output itself.
    private func streamOutput() -> String {
        let config = try? ConfigStore(url: paths.configURL).load()
        return config?.streamOutput ?? "auto"
    }

    private static func describe(_ error: any Error) -> String {
        switch error {
        case let error as CommandError:
            switch error {
            case .launchFailed(_, let message): message
            case .timedOut(let command): "\(command) did not respond in time"
            }
        case is CancellationError: "cancelled"
        default: String(describing: error)
        }
    }

    // MARK: State

    private func begin() {
        epoch += 1
        mode = .transitioning
        error = nil
        onChange(snapshot)
    }

    @discardableResult
    private func finish(_ outcome: Outcome) -> ModeSnapshot {
        epoch += 1
        mode = outcome.mode
        error = outcome.error
        onChange(snapshot)
        let settled = snapshot
        let waiting = idleWaiters
        idleWaiters = []
        for waiter in waiting { waiter.resume(returning: settled) }
        return settled
    }
}
