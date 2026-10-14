import Foundation
import Testing

@testable import DiarchyCore

// MARK: - Test doubles

private func ok(_ stdout: String = "", stderr: String = "", exit: Int32 = 0) -> Result<CommandResult, CommandError> {
    .success(CommandResult(exitCode: exit, stdout: stdout, stderr: stderr))
}

private func hostOK(_ envelope: String) -> Result<CommandResult, CommandError> { ok(envelope + "\n") }

private func hostFailure(code: String, message: String, command: String) -> Result<CommandResult, CommandError> {
    ok(Fixtures.failure(code: code, message: message, command: command) + "\n", exit: 1)
}

/// Parks the call that matches it right after the fake resolved it: `arrived` fires when the call is in flight and the
/// caller stays suspended until the test fires `released`. No sleeps, no polling.
private final class Gate: @unchecked Sendable {  // OneShot guards its own state
    let arrived = OneShot()
    let released = OneShot()
}

/// A `FakeCommandRunner` that also keeps one ordered log of `run` and `spawn` calls (`host kvm start`,
/// `diarchy-kvm on`, `spawn diarchy-stream`, …), can park chosen calls behind a `Gate` and reports each call to `observe`.
private final class ScriptedRunner: CommandRunner, @unchecked Sendable {  // every access holds `lock`
    let fake = FakeCommandRunner()
    private let lock = NSLock()
    private var log: [String] = []
    private var gates: [String: Gate] = [:]
    private var observer: (@Sendable (String) -> Void)?

    var labels: [String] { locked { log } }
    func clearLog() { locked { log = [] } }
    func observe(_ observer: @escaping @Sendable (String) -> Void) { locked { self.observer = observer } }

    /// The next call with this label parks; later ones pass.
    func gate(_ label: String) -> Gate {
        let gate = Gate()
        locked { gates[label] = gate }
        return gate
    }

    static func label(_ executable: String, _ arguments: [String]) -> String {
        let name = (executable as NSString).lastPathComponent
        if name == "diarchy-ssh", arguments.count >= 2, arguments[0] == "--", arguments[1] == "~/.local/bin/diarchy-host" {
            // The stream output flag is not part of the event name; its own test checks it.
            var words = Array(arguments.dropFirst(2))
            if let flag = words.firstIndex(of: "--output"), flag + 1 < words.count { words.removeSubrange(flag...flag + 1) }
            return (["host"] + words).joined(separator: " ")
        }
        return ([name] + arguments).joined(separator: " ")
    }

    private func record(_ label: String) -> (observer: (@Sendable (String) -> Void)?, gate: Gate?) {
        locked {
            log.append(label)
            return (observer, gates.removeValue(forKey: label))
        }
    }

    func run(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval
    ) async throws -> CommandResult {
        let label = Self.label(executable, arguments)
        let hooks = record(label)
        hooks.observer?(label)
        let result: Result<CommandResult, any Error>
        do {
            result = .success(
                try await fake.run(
                    executable: executable, arguments: arguments, environment: environment, stdin: stdin,
                    timeout: timeout))
        } catch {
            result = .failure(error)
        }
        if let gate = hooks.gate {
            gate.arrived.fire()
            await gate.released.wait()
        }
        return try result.get()
    }

    func runStreaming(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval, onLine: @escaping @Sendable (String) -> Void
    ) async throws -> CommandResult {
        try await fake.runStreaming(
            executable: executable, arguments: arguments, environment: environment, stdin: stdin, timeout: timeout,
            onLine: onLine)
    }

    func spawn(executable: String, arguments: [String], environment: [String: String]?) throws -> any ChildProcess {
        let label = "spawn " + Self.label(executable, arguments)
        let hooks = record(label)
        hooks.observer?(label)
        return try fake.spawn(executable: executable, arguments: arguments, environment: environment)
    }

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}

private final class EventLog: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var items: [String] = []
    func record(_ item: String) { lock.lock(); items.append(item); lock.unlock() }
    var events: [String] { lock.lock(); defer { lock.unlock() }; return items }
}

/// Every `onChange` callback, in order, plus a stream to wait for a state without sleeping.
private final class ChangeRecorder: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var items: [ModeSnapshot] = []
    private let stream: AsyncStream<ModeSnapshot>
    private let continuation: AsyncStream<ModeSnapshot>.Continuation

    init() { (stream, continuation) = AsyncStream.makeStream(of: ModeSnapshot.self) }

    func record(_ snapshot: ModeSnapshot) {
        lock.lock()
        items.append(snapshot)
        lock.unlock()
        continuation.yield(snapshot)
    }

    var snapshots: [ModeSnapshot] { lock.lock(); defer { lock.unlock() }; return items }
    func clear() { lock.lock(); items = []; lock.unlock() }

    /// The first callback not yet consumed that satisfies `predicate`.
    func next(where predicate: (ModeSnapshot) -> Bool) async -> ModeSnapshot? {
        for await snapshot in stream where predicate(snapshot) { return snapshot }
        return nil
    }
}

/// Wraps a `FakeChildProcess` so the FIRST `waitForExit` (the controller's exit watcher) stays suspended after the child
/// exited until the test calls `releaseWatcher()`: models a watcher the scheduler has not run yet.
private final class ParkedWatcherChild: ChildProcess, @unchecked Sendable {  // `lock` guards `watcherCalls`
    private let inner = FakeChildProcess()
    private let lock = NSLock()
    private var watcherCalls = 0
    let watcherStarted = OneShot()
    private let watcherReleased = OneShot()

    var pid: Int32 { inner.pid }
    var hasExited: Bool { inner.hasExited }
    func terminate() { inner.terminate() }
    func kill() { inner.kill() }
    func releaseWatcher() { watcherReleased.fire() }

    private func claimWatcherRole() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        watcherCalls += 1
        return watcherCalls == 1
    }

    func waitForExit() async -> Int32 {
        guard claimWatcherRole() else { return await inner.waitForExit() }
        watcherStarted.fire()
        let code = await inner.waitForExit()
        await watcherReleased.wait()
        return code
    }
}

/// `true` for the first caller only.
private final class SpawnSequence: @unchecked Sendable {  // `lock` guards `spawned`
    private let lock = NSLock()
    private var spawned = false
    func isFirst() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        defer { spawned = true }
        return !spawned
    }
}

private struct Script {
    var kvmStart = hostOK(Fixtures.kvmStart)
    var kvmOn = ok("Diarchy KVM: enabled\n")
    var kvmOff = ok("Diarchy KVM: disabled\n")
    var kvmStatus = ok("Diarchy KVM: enabled (clipboard bridge: active, file portals: active)\n")
    /// When set, the first `diarchy-kvm status` returns it and the next ones `kvmStatus`.
    var firstKvmStatus: Result<CommandResult, CommandError>?
    var streamStart = hostOK(Fixtures.streamStart)
    var streamStop = hostOK(Fixtures.streamStop)
    /// `nil` hands out `child`.
    var spawnFailure: CommandError?
    var child = FakeChildProcess()
    /// When set, the first spawn hands out `firstChild` and the next ones `child`.
    var firstChild: (any ChildProcess)?
}

private struct Rig {
    let runner: ScriptedRunner
    let child: FakeChildProcess
    let controller: ModeController
    let changes: ChangeRecorder
    let log: EventLog
    let clock: FakeClock
    var labels: [String] { runner.labels }
}

private let hostWords = ["--", "~/.local/bin/diarchy-host"]

/// Monotonic clock the test advances by hand.
private final class FakeClock: @unchecked Sendable {  // `lock` guards `current`
    private let lock = NSLock()
    private var current = ContinuousClock.now
    var now: ContinuousClock.Instant { lock.lock(); defer { lock.unlock() }; return current }
    func advance(by duration: Duration) { lock.lock(); current = current.advanced(by: duration); lock.unlock() }
}

/// Records the pid of every window activation the controller requests, and every cancellation.
private final class RecordingActivator: StreamWindowActivating, @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var items: [Int32] = []
    private var cancelled = 0
    var pids: [Int32] { lock.lock(); defer { lock.unlock() }; return items }
    var cancels: Int { lock.lock(); defer { lock.unlock() }; return cancelled }
    func bringToFront(pid: Int32) { lock.lock(); items.append(pid); lock.unlock() }
    func cancel() { lock.lock(); cancelled += 1; lock.unlock() }
}

/// `instantSleep: false` models the injected sleep that never resolves before it is cancelled.
private func makeRig(
    _ script: Script = Script(), instantSleep: Bool = false, clock: FakeClock = FakeClock(),
    isExecutable: @escaping @Sendable (String) -> Bool = { _ in true },
    windowActivator: any StreamWindowActivating = NoWindowActivation()
) -> Rig {
    let runner = ScriptedRunner()
    let child = script.child
    let log = EventLog()
    let changes = ChangeRecorder()
    runner.fake.when("diarchy-ssh", hostWords + ["kvm", "start"], return: script.kvmStart)
    runner.fake.when("diarchy-ssh", hostWords + ["stream", "start", "--output", "auto"], return: script.streamStart)
    runner.fake.when("diarchy-ssh", hostWords + ["stream", "stop"], return: script.streamStop)
    runner.fake.when("diarchy-kvm", ["on"], return: script.kvmOn)
    runner.fake.when("diarchy-kvm", ["off"], return: script.kvmOff)
    if let first = script.firstKvmStatus {
        let sequence = SpawnSequence()
        runner.fake.when(
            { ($0.executable as NSString).lastPathComponent == "diarchy-kvm" && $0.arguments == ["status"] && sequence.isFirst() },
            return: first)
    }
    runner.fake.when("diarchy-kvm", ["status"], return: script.kvmStatus)
    if let failure = script.spawnFailure {
        runner.fake.whenSpawn("diarchy-stream", [], fails: failure)
    } else {
        if let first = script.firstChild {
            let sequence = SpawnSequence()
            runner.fake.whenSpawn({ $0.executable.hasSuffix("diarchy-stream") && sequence.isFirst() }, returns: first)
        }
        runner.fake.whenSpawn("diarchy-stream", [], returns: child)
    }
    runner.observe { label in
        if label == "host stream stop" {
            log.record("stream stop terminated=\(child.terminated) killed=\(child.killed)")
        }
    }
    let paths = Paths(home: URL(fileURLWithPath: "/tmp/diarchy-home"), payload: nil)
    let controller = ModeController(
        paths: paths, runner: runner, agent: HostAgentClient(runner: runner, paths: paths),
        sleep: { duration in
            log.record("sleep \(duration == .seconds(5) ? "5s" : "\(duration)") terminated=\(child.terminated) killed=\(child.killed)")
            if !instantSleep { try await Task.sleep(for: .seconds(3600)) }
        },
        isExecutable: isExecutable,
        now: { clock.now },
        onChange: { changes.record($0) },
        windowActivator: windowActivator)
    return Rig(runner: runner, child: child, controller: controller, changes: changes, log: log, clock: clock)
}

extension Rig {
    /// Drives the controller to `.kvm(false)` and forgets the setup traffic.
    fileprivate func inKVM() async {
        let snapshot = await controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: false)))
        runner.clearLog()
        changes.clear()
    }

    /// Drives the controller to `.streaming` and forgets the setup traffic.
    fileprivate func inStreaming() async {
        let snapshot = await controller.setMode(.streaming)
        #expect(snapshot == ModeSnapshot(mode: .streaming))
        runner.clearLog()
        changes.clear()
    }
}

// MARK: - Tests

@Suite(.timeLimit(.minutes(1))) struct ModeControllerTests {
    private static let kvmOnBoom = ok(stderr: "kvm on boom\nsecond line\n", exit: 1)

    // MARK: off → kvm

    @Test func offToKVMRunsHostStartThenOnThenStatusAndPublishesTransitioningThenKVM() async {
        let rig = makeRig()
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: false), error: nil))
        #expect(rig.labels == ["host kvm start", "diarchy-kvm on", "diarchy-kvm status"])
        #expect(
            rig.changes.snapshots == [
                ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .kvm(degraded: false)),
            ])
        #expect(await rig.controller.snapshot == snapshot)
    }

    @Test func hostCallsUseTheFifteenSecondLimitAndLocalCommandsTheTwentySecondOne() async {
        let rig = makeRig()
        _ = await rig.controller.setMode(.kvm)
        let timeouts = rig.runner.fake.calls.map { (ScriptedRunner.label($0.executable, $0.arguments), $0.timeout) }
        #expect(timeouts.map(\.0) == ["host kvm start", "diarchy-kvm on", "diarchy-kvm status"])
        #expect(timeouts.map(\.1) == [15, 20, 20])
        #expect(rig.runner.fake.calls[1].executable == "/tmp/diarchy-home/.local/bin/diarchy-kvm")
    }

    @Test func hostKVMStartFailureStopsWithoutRollback() async {
        var script = Script()
        script.kvmStart = hostFailure(
            code: "service_failed", message: "diarchy-kvm.service failed to start", command: "kvm start")
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(
            snapshot
                == ModeSnapshot(
                    mode: .off, error: "A service failed on the host: diarchy-kvm.service failed to start"))
        #expect(rig.labels == ["host kvm start"])
        #expect(rig.changes.snapshots.last == snapshot)
    }

    @Test func hostKVMStartTimeoutNamesTheHostCommand() async {
        var script = Script()
        script.kvmStart = .failure(.timedOut(command: "diarchy-ssh"))
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .off, error: "diarchy-host kvm start did not respond in time"))
        #expect(rig.labels == ["host kvm start"])
    }

    @Test func kvmOnFailureRollsBackWithKVMOffAndShowsTheFirstStderrLine() async {
        var script = Script()
        script.kvmOn = Self.kvmOnBoom
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .off, error: "kvm on boom"))
        #expect(rig.labels == ["host kvm start", "diarchy-kvm on", "diarchy-kvm off"])
    }

    @Test func kvmStatusExitingNonZeroAfterOnRollsBack() async {
        var script = Script()
        script.kvmStatus = ok(
            "Diarchy KVM: degraded (clipboard bridge: stopped, file portals: active)\n", exit: 1)
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(
            snapshot
                == ModeSnapshot(
                    mode: .off,
                    error: "Diarchy KVM: degraded (clipboard bridge: stopped, file portals: active)"))
        #expect(rig.labels == ["host kvm start", "diarchy-kvm on", "diarchy-kvm status", "diarchy-kvm off"])
    }

    @Test func rollbackFailureIsAppendedToTheError() async {
        var script = Script()
        script.kvmOn = Self.kvmOnBoom
        script.kvmOff = ok(stderr: "off boom\n", exit: 1)
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot.mode == .off)
        #expect(snapshot.error == "kvm on boom (diarchy-kvm off also failed: off boom)")
        #expect(rig.labels == ["host kvm start", "diarchy-kvm on", "diarchy-kvm off"])
    }

    @Test func kvmOnTimeoutReportsTheCommandAndStillRollsBack() async {
        var script = Script()
        script.kvmOn = .failure(.timedOut(command: "diarchy-kvm"))
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .off, error: "diarchy-kvm did not respond in time"))
        #expect(rig.labels == ["host kvm start", "diarchy-kvm on", "diarchy-kvm off"])
    }

    @Test func kvmOnLaunchFailureShowsTheLaunchMessage() async {
        var script = Script()
        script.kvmOn = .failure(.launchFailed(command: "/tmp/diarchy-home/.local/bin/diarchy-kvm", message: "No such file"))
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .off, error: "No such file"))
        #expect(rig.labels.last == "diarchy-kvm off")
    }

    // MARK: kvm → off

    @Test func kvmToOffRunsKVMOff() async {
        let rig = makeRig()
        await rig.inKVM()
        let snapshot = await rig.controller.setMode(.off)
        #expect(snapshot == ModeSnapshot(mode: .off))
        #expect(rig.labels == ["diarchy-kvm off"])
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .off)])
    }

    @Test func kvmOffFailureLeavesKVMActiveAndShowsTheError() async {
        var script = Script()
        script.kvmOff = ok(stderr: "off boom\n", exit: 1)
        let rig = makeRig(script)
        await rig.inKVM()
        let snapshot = await rig.controller.setMode(.off)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: false), error: "off boom"))
        #expect(rig.labels == ["diarchy-kvm off"])
    }

    @Test func kvmOffFailureKeepsTheDegradedFlag() async {
        var script = Script()
        script.kvmStatus = ok("Diarchy KVM: degraded (clipboard bridge: stopped, file portals: active)\n", exit: 1)
        script.kvmOff = .failure(.timedOut(command: "diarchy-kvm"))
        let rig = makeRig(script)
        #expect(await rig.controller.refresh().mode == .kvm(degraded: true))
        let snapshot = await rig.controller.setMode(.off)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: true), error: "diarchy-kvm did not respond in time"))
    }

    // MARK: → streaming

    @Test func offToStreamingStartsTheHostThenSpawnsDiarchyStream() async {
        let rig = makeRig()
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot == ModeSnapshot(mode: .streaming))
        #expect(rig.labels == ["host stream start", "spawn diarchy-stream"])
        #expect(
            rig.runner.fake.spawns == [
                .init(executable: "/tmp/diarchy-home/.local/bin/diarchy-stream", arguments: [], environment: nil)
            ])
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .streaming)])
        #expect(!rig.child.terminated)
    }

    // MARK: Moonlight window activation

    @Test func startingStreamingAsksForTheMoonlightWindowToComeToFrontExactlyOnce() async {
        let activator = RecordingActivator()
        let rig = makeRig(Script(child: FakeChildProcess(pid: 4242)), windowActivator: activator)
        _ = await rig.controller.setMode(.streaming)
        #expect(activator.pids == [4242])
    }

    @Test func streamingFromKVMAlsoActivatesOnceAndLeavingStreamingNeverDoes() async {
        let activator = RecordingActivator()
        let rig = makeRig(windowActivator: activator)
        await rig.inKVM()
        #expect(activator.pids.isEmpty)
        _ = await rig.controller.setMode(.streaming)
        #expect(activator.pids == [1234])
        _ = await rig.controller.setMode(.off)
        _ = await rig.controller.setMode(.kvm)
        _ = await rig.controller.refresh()
        #expect(activator.pids == [1234])
    }

    @Test func kvmAndOffNeverActivateAnything() async {
        let activator = RecordingActivator()
        let rig = makeRig(windowActivator: activator)
        _ = await rig.controller.setMode(.kvm)
        _ = await rig.controller.setMode(.off)
        #expect(activator.pids.isEmpty)
    }

    @Test func aStreamThatFailsToStartActivatesNothing() async {
        let activator = RecordingActivator()
        var failingSpawn = Script()
        failingSpawn.spawnFailure = .launchFailed(command: "diarchy-stream", message: "nope")
        _ = await makeRig(failingSpawn, windowActivator: activator).controller.setMode(.streaming)
        var failingHost = Script()
        failingHost.streamStart = hostFailure(code: "service_failed", message: "boom", command: "stream start")
        _ = await makeRig(failingHost, windowActivator: activator).controller.setMode(.streaming)
        #expect(activator.pids.isEmpty)
    }

    @Test func eachStreamingStartActivatesAgain() async {
        let activator = RecordingActivator()
        let rig = makeRig(windowActivator: activator)
        _ = await rig.controller.setMode(.streaming)
        _ = await rig.controller.setMode(.off)
        _ = await rig.controller.setMode(.streaming)
        #expect(activator.pids.count == 2)
    }

    @Test func leavingStreamingCancelsTheWindowWatcher() async {
        let activator = RecordingActivator()
        let rig = makeRig(windowActivator: activator)
        await rig.inStreaming()
        #expect(activator.cancels == 0)
        _ = await rig.controller.setMode(.off)
        #expect(activator.cancels == 1)
    }

    @Test func moonlightQuittingByItselfCancelsTheWindowWatcher() async {
        let activator = RecordingActivator()
        let rig = makeRig(windowActivator: activator)
        await rig.inStreaming()
        rig.clock.advance(by: .seconds(6))
        rig.child.exit(code: 0)
        _ = await rig.changes.next { $0.mode == .off }
        #expect(activator.cancels == 1)
    }

    @Test func kvmToStreamingTurnsKVMOffFirst() async {
        let rig = makeRig()
        await rig.inKVM()
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot == ModeSnapshot(mode: .streaming))
        #expect(rig.labels == ["diarchy-kvm off", "host stream start", "spawn diarchy-stream"])
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .streaming)])
    }

    @Test func kvmOffFailureAbortsKVMToStreamingBeforeStreamStart() async {
        var script = Script()
        script.kvmOff = ok(stderr: "off boom\n", exit: 1)
        let rig = makeRig(script)
        await rig.inKVM()
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: false), error: "off boom"))
        #expect(rig.labels == ["diarchy-kvm off"])
    }

    @Test func streamStartFailureStopsTheStreamBestEffortAndGoesOff() async {
        var script = Script()
        script.streamStart = hostFailure(
            code: "service_failed", message: "sunshine.service failed to start", command: "stream start")
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(
            snapshot
                == ModeSnapshot(mode: .off, error: "A service failed on the host: sunshine.service failed to start")
        )
        #expect(rig.labels == ["host stream start", "host stream stop"])
        #expect(rig.runner.fake.spawns.isEmpty)
    }

    @Test func noSessionOnTheHostGoesOffWithTheRecoveryHint() async {
        var script = Script()
        script.streamStart = hostFailure(
            code: "no_session", message: "no Hyprland session is running for this user", command: "stream start")
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot.mode == .off)
        #expect(snapshot.error?.hasPrefix("No Omarchy session is open on the host") == true)
        #expect(snapshot.error?.hasSuffix("sudo systemctl restart sddm") == true)
    }

    @Test func streamStopFailingAfterAFailedStartDoesNotChangeTheError() async {
        var script = Script()
        script.streamStart = hostFailure(code: "service_failed", message: "start broke", command: "stream start")
        script.streamStop = ok(stderr: "ssh: Connection refused\n", exit: 255)
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot == ModeSnapshot(mode: .off, error: "A service failed on the host: start broke"))
        #expect(rig.labels == ["host stream start", "host stream stop"])
    }

    @Test func spawnFailureStopsTheStreamAndGoesOff() async {
        var script = Script()
        script.spawnFailure = .launchFailed(command: "/tmp/diarchy-home/.local/bin/diarchy-stream", message: "No such file")
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot.mode == .off)
        #expect(snapshot.error == "Could not start diarchy-stream: No such file")
        #expect(rig.labels == ["host stream start", "spawn diarchy-stream", "host stream stop"])
    }

    @Test func spawnFailureAppendsAFailingStreamStop() async {
        var script = Script()
        script.spawnFailure = .launchFailed(command: "diarchy-stream", message: "No such file")
        script.streamStop = hostFailure(code: "service_failed", message: "stop broke", command: "stream stop")
        let rig = makeRig(script)
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot.mode == .off)
        #expect(
            snapshot.error
                == "Could not start diarchy-stream: No such file (diarchy-host stream stop also failed: A service failed on the host: stop broke)")
    }

    // MARK: streaming → off

    @Test func streamingToOffTerminatesTheChildThenStopsTheStream() async {
        let rig = makeRig()
        await rig.inStreaming()
        let snapshot = await rig.controller.setMode(.off)
        #expect(snapshot == ModeSnapshot(mode: .off))
        #expect(rig.child.terminated)
        #expect(!rig.child.killed)
        #expect(rig.labels == ["host stream stop"])
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .off)])
        await rig.controller.exitWatcherFinished()
        #expect(rig.labels == ["host stream stop"])
    }

    @Test func childIgnoringTermIsKilledAfterTheFiveSecondRaceAndStreamStopComesLast() async {
        let rig = makeRig(Script(child: FakeChildProcess(exitsOnTerminate: false)), instantSleep: true)
        await rig.inStreaming()
        let snapshot = await rig.controller.setMode(.off)
        #expect(snapshot == ModeSnapshot(mode: .off))
        #expect(rig.child.terminated)
        #expect(rig.child.killed)
        // terminate() happened before the 5 s race, kill() after it and before `stream stop`.
        #expect(
            rig.log.events == [
                "sleep 5s terminated=true killed=false", "stream stop terminated=true killed=true",
            ])
        // The exit our own kill caused must not run a second `stream stop`.
        await rig.controller.exitWatcherFinished()
        #expect(rig.labels == ["host stream stop"])
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .off)])
    }

    @Test func childThatExitsOnTermIsNeverKilled() async {
        let rig = makeRig()
        await rig.inStreaming()
        _ = await rig.controller.setMode(.off)
        #expect(rig.child.terminated)
        #expect(!rig.child.killed)
    }

    @Test func streamStopFailureAfterStoppingTheChildGoesOffWithTheError() async {
        var script = Script()
        script.streamStop = hostFailure(code: "service_failed", message: "stop broke", command: "stream stop")
        let rig = makeRig(script)
        await rig.inStreaming()
        let snapshot = await rig.controller.setMode(.off)
        #expect(snapshot == ModeSnapshot(mode: .off, error: "A service failed on the host: stop broke"))
    }

    @Test func streamingToOffWhenTheChildAlreadyExitedDoesNotSignalIt() async {
        let rig = makeRig()
        await rig.inStreaming()
        rig.child.exit(code: 0)
        // Whichever of the exit watcher and this call reaches the actor first, exactly one `stream stop` runs.
        _ = await rig.controller.setMode(.off)
        _ = await rig.changes.next { $0.mode == .off }
        await rig.controller.exitWatcherFinished()
        #expect(rig.labels == ["host stream stop"])
        #expect(!rig.child.terminated)
        #expect(await rig.controller.snapshot.mode == .off)
    }

    // MARK: streaming → kvm

    @Test func streamingToKVMPassesThroughOff() async {
        let rig = makeRig()
        await rig.inStreaming()
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: false)))
        #expect(rig.labels == ["host stream stop", "host kvm start", "diarchy-kvm on", "diarchy-kvm status"])
        #expect(rig.child.terminated)
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .kvm(degraded: false))])
    }

    @Test func streamStopFailureAbortsStreamingToKVM() async {
        var script = Script()
        script.streamStop = hostFailure(code: "service_failed", message: "stop broke", command: "stream stop")
        let rig = makeRig(script)
        await rig.inStreaming()
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .off, error: "A service failed on the host: stop broke"))
        #expect(rig.labels == ["host stream stop"])
    }

    // MARK: Moonlight exits by itself

    @Test(arguments: [Int32(0)])
    func moonlightExitingByItselfStopsTheStreamOnceAndGoesOff(code: Int32) async {
        let rig = makeRig()
        await rig.inStreaming()
        rig.clock.advance(by: .seconds(6))
        rig.child.exit(code: code)
        let final = await rig.changes.next { $0.mode == .off }
        #expect(final == ModeSnapshot(mode: .off))
        #expect(rig.labels == ["host stream stop"])
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .off)])
        #expect(!rig.child.terminated)
        #expect(!rig.child.killed)
        #expect(await rig.controller.snapshot == ModeSnapshot(mode: .off))
        await rig.controller.exitWatcherFinished()
        #expect(rig.labels == ["host stream stop"])
    }

    @Test func aNonZeroMoonlightExitShowsTheExitCodeAndTheLastErrorLine() async {
        let rig = makeRig()
        await rig.inStreaming()
        rig.child.lastErrorLine = "no route to host"
        rig.clock.advance(by: .seconds(6))
        rig.child.exit(code: 9)
        let final = await rig.changes.next { $0.mode == .off }
        #expect(final == ModeSnapshot(mode: .off, error: "diarchy-stream exited with code 9: no route to host"))
    }

    @Test func aNonZeroMoonlightExitWithoutStderrShowsTheExitCode() async {
        let rig = makeRig()
        await rig.inStreaming()
        rig.clock.advance(by: .seconds(6))
        rig.child.exit(code: 9)
        let final = await rig.changes.next { $0.mode == .off }
        #expect(final == ModeSnapshot(mode: .off, error: "diarchy-stream exited with code 9"))
    }

    @Test func moonlightExitWithAFailingStreamStopGoesOffWithTheError() async {
        var script = Script()
        script.streamStop = hostFailure(code: "service_failed", message: "stop broke", command: "stream stop")
        let rig = makeRig(script)
        await rig.inStreaming()
        rig.clock.advance(by: .seconds(6))
        rig.child.exit(code: 0)
        let final = await rig.changes.next { $0.mode == .off }
        #expect(final == ModeSnapshot(mode: .off, error: "A service failed on the host: stop broke"))
    }

    @Test func aSecondSetModeWhileTheExitHandlerStopsTheStreamDoesNothing() async {
        let rig = makeRig()
        await rig.inStreaming()
        let gate = rig.runner.gate("host stream stop")
        rig.clock.advance(by: .seconds(6))
        rig.child.exit(code: 0)
        await gate.arrived.wait()  // the exit handler is suspended inside `stream stop`
        #expect(await rig.controller.snapshot == ModeSnapshot(mode: .transitioning))
        #expect(await rig.controller.setMode(.off) == ModeSnapshot(mode: .transitioning))
        #expect(await rig.controller.setMode(.kvm) == ModeSnapshot(mode: .transitioning))
        #expect(rig.labels == ["host stream stop"])
        gate.released.fire()
        let final = await rig.changes.next { $0.mode == .off }
        #expect(final == ModeSnapshot(mode: .off))
        await rig.controller.exitWatcherFinished()
        #expect(rig.labels == ["host stream stop"])
        #expect(!rig.child.terminated)
    }

    @Test func aMoonlightExitAfterOurTerminationNeverStopsTheStreamAgain() async {
        let rig = makeRig()
        await rig.inStreaming()
        let gate = rig.runner.gate("host stream stop")
        async let stopped = rig.controller.setMode(.off)
        await gate.arrived.wait()  // the child is gone (TERM) and the watcher was released; we are inside our own `stream stop`
        await rig.controller.exitWatcherFinished()  // the watcher saw the exit and had to ignore it
        #expect(rig.labels == ["host stream stop"])
        gate.released.fire()
        #expect(await stopped == ModeSnapshot(mode: .off))
        #expect(rig.labels == ["host stream stop"])
    }

    @Test func aStaleExitWatcherFiringAfterARestartLeavesTheNewSessionStreaming() async {
        let first = ParkedWatcherChild()
        var script = Script()
        script.firstChild = first
        let rig = makeRig(script)
        #expect(await rig.controller.setMode(.streaming) == ModeSnapshot(mode: .streaming))
        await first.watcherStarted.wait()
        #expect(await rig.controller.setMode(.off) == ModeSnapshot(mode: .off))  // first.exit happened, its watcher is still parked
        #expect(await rig.controller.setMode(.streaming) == ModeSnapshot(mode: .streaming))
        rig.runner.clearLog()
        rig.changes.clear()

        first.releaseWatcher()
        await rig.controller.exitWatcherFinished(generation: 1)  // the stale watcher ran and had to ignore its own, long released child
        #expect(await rig.controller.snapshot == ModeSnapshot(mode: .streaming))
        #expect(rig.labels.isEmpty)  // no extra `stream stop`
        #expect(rig.changes.snapshots.isEmpty)
        #expect(!rig.child.terminated)
    }

    // MARK: Re-entrancy and no-ops

    @Test func aSecondSetModeWhileTransitioningReturnsTransitioningAndRunsNothing() async {
        let rig = makeRig()
        let gate = rig.runner.gate("diarchy-kvm on")
        async let first = rig.controller.setMode(.kvm)
        await gate.arrived.wait()  // the first transition is suspended inside `diarchy-kvm on`
        let before = rig.labels
        #expect(before == ["host kvm start", "diarchy-kvm on"])

        #expect(await rig.controller.setMode(.streaming) == ModeSnapshot(mode: .transitioning))
        #expect(await rig.controller.setMode(.kvm) == ModeSnapshot(mode: .transitioning))
        #expect(await rig.controller.setMode(.off) == ModeSnapshot(mode: .transitioning))
        #expect(await rig.controller.refresh() == ModeSnapshot(mode: .transitioning))
        #expect(rig.labels == before)
        #expect(rig.runner.fake.spawns.isEmpty)

        gate.released.fire()
        #expect(await first == ModeSnapshot(mode: .kvm(degraded: false)))
        #expect(rig.labels == ["host kvm start", "diarchy-kvm on", "diarchy-kvm status"])
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .kvm(degraded: false))])
    }

    @Test func settingTheCurrentModeIsANoOp() async {
        let rig = makeRig()
        #expect(await rig.controller.setMode(.off) == ModeSnapshot(mode: .off))
        #expect(rig.labels.isEmpty)
        #expect(rig.changes.snapshots.isEmpty)

        await rig.inKVM()
        #expect(await rig.controller.setMode(.kvm) == ModeSnapshot(mode: .kvm(degraded: false)))
        #expect(rig.labels.isEmpty)
        #expect(rig.changes.snapshots.isEmpty)

        _ = await rig.controller.setMode(.off)
        await rig.inStreaming()
        #expect(await rig.controller.setMode(.streaming) == ModeSnapshot(mode: .streaming))
        #expect(rig.labels.isEmpty)
        #expect(rig.changes.snapshots.isEmpty)
    }

    // MARK: refresh

    @Test func refreshDerivesEnabledAsKVMNotDegraded() async {
        let rig = makeRig()
        let snapshot = await rig.controller.refresh()
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: false)))
        #expect(rig.labels == ["diarchy-kvm status"])
        #expect(await rig.controller.snapshot == snapshot)
        #expect(rig.changes.snapshots == [snapshot])
    }

    @Test func refreshDerivesDegradedFromAnExitOneStatus() async {
        var script = Script()
        script.kvmStatus = ok("Diarchy KVM: degraded (clipboard bridge: stopped, file portals: active)\n", exit: 1)
        let rig = makeRig(script)
        #expect(await rig.controller.refresh() == ModeSnapshot(mode: .kvm(degraded: true)))
    }

    @Test func refreshDerivesDisabledAsOff() async {
        var script = Script()
        script.kvmStatus = ok("Diarchy KVM: disabled (clipboard bridge: stopped, file portals: stopped)\n", exit: 1)
        let rig = makeRig(script)
        #expect(await rig.controller.refresh() == ModeSnapshot(mode: .off))
    }

    @Test func aLiveMoonlightChildWinsOverStatusAndRunsNoCommand() async {
        let rig = makeRig()
        await rig.inStreaming()
        #expect(await rig.controller.refresh() == ModeSnapshot(mode: .streaming))
        #expect(rig.labels.isEmpty)
    }

    @Test func garbageStatusOutputIsOffWithAnError() async {
        var script = Script()
        script.kvmStatus = ok("hello world\n")
        let rig = makeRig(script)
        let snapshot = await rig.controller.refresh()
        #expect(snapshot.mode == .off)
        #expect(snapshot.error == "diarchy-kvm status: unrecognized output: hello world")
    }

    @Test func emptyStatusOutputIsOffWithAnError() async {
        var script = Script()
        script.kvmStatus = ok(stderr: "diarchy-kvm: config missing\n", exit: 2)
        let rig = makeRig(script)
        let snapshot = await rig.controller.refresh()
        #expect(snapshot == ModeSnapshot(mode: .off, error: "diarchy-kvm status: unrecognized output: diarchy-kvm: config missing"))
    }

    @Test func statusTimeoutIsOffWithTheTimeoutMessage() async {
        var script = Script()
        script.kvmStatus = .failure(.timedOut(command: "diarchy-kvm"))
        let rig = makeRig(script)
        #expect(
            await rig.controller.refresh() == ModeSnapshot(mode: .off, error: "diarchy-kvm did not respond in time"))
    }

    @Test func aRefreshThatLosesTheRaceAgainstATransitionDoesNotOverwriteIt() async {
        var script = Script()
        script.kvmStatus = ok("Diarchy KVM: disabled\n", exit: 1)
        let rig = makeRig(script)
        let gate = rig.runner.gate("diarchy-kvm status")
        async let refreshed = rig.controller.refresh()
        await gate.arrived.wait()  // refresh is suspended inside `diarchy-kvm status`
        #expect(await rig.controller.setMode(.streaming) == ModeSnapshot(mode: .streaming))
        gate.released.fire()
        #expect(await refreshed == ModeSnapshot(mode: .streaming))
        #expect(await rig.controller.snapshot == ModeSnapshot(mode: .streaming))
    }

    // MARK: waitUntilIdle

    @Test func waitUntilIdleReturnsAtOnceWhenNoTransitionRuns() async {
        let rig = makeRig()
        #expect(await rig.controller.waitUntilIdle() == ModeSnapshot(mode: .off))
        #expect(rig.labels.isEmpty)
        await rig.inKVM()
        #expect(await rig.controller.waitUntilIdle() == ModeSnapshot(mode: .kvm(degraded: false)))
    }

    @Test func waitUntilIdleSuspendsUntilTheRunningTransitionSettles() async {
        let rig = makeRig()
        let (registrations, registered) = AsyncStream.makeStream(of: Void.self)
        await rig.controller.observeIdleWaiters { registered.yield() }
        let gate = rig.runner.gate("diarchy-kvm on")
        async let transition = rig.controller.setMode(.kvm)
        await gate.arrived.wait()  // suspended inside `diarchy-kvm on`
        async let waiters = withTaskGroup(of: ModeSnapshot.self) { group in
            for _ in 0..<3 { group.addTask { await rig.controller.waitUntilIdle() } }
            return await group.reduce(into: []) { $0.append($1) }
        }
        var parked = 0
        for await _ in registrations {
            parked += 1
            if parked == 3 { break }  // all three called in while the transition is still running
        }
        gate.released.fire()
        #expect(await transition == ModeSnapshot(mode: .kvm(degraded: false)))
        let settled = ModeSnapshot(mode: .kvm(degraded: false))
        #expect(await waiters == [settled, settled, settled])
        // The waiters ran no command of their own.
        #expect(rig.labels == ["host kvm start", "diarchy-kvm on", "diarchy-kvm status"])
    }

    @Test func waitUntilIdleAlsoSettlesAfterAFailedTransition() async {
        var script = Script()
        script.kvmOn = Self.kvmOnBoom
        let rig = makeRig(script)
        let (registrations, registered) = AsyncStream.makeStream(of: Void.self)
        await rig.controller.observeIdleWaiters { registered.yield() }
        let gate = rig.runner.gate("diarchy-kvm on")
        async let transition = rig.controller.setMode(.kvm)
        await gate.arrived.wait()
        async let waited = rig.controller.waitUntilIdle()
        for await _ in registrations { break }
        gate.released.fire()
        let failed = await transition
        #expect(failed.mode == .off)
        #expect(failed.error == "kvm on boom")
        #expect(await waited == failed)
    }

    @Test func waitUntilIdleSettlesAfterMoonlightQuitsByItself() async {
        let rig = makeRig()
        await rig.inStreaming()
        let gate = rig.runner.gate("host stream stop")
        rig.clock.advance(by: .seconds(6))
        rig.child.exit(code: 0)
        await gate.arrived.wait()  // the exit handler is suspended inside `stream stop`
        let (registrations, registered) = AsyncStream.makeStream(of: Void.self)
        await rig.controller.observeIdleWaiters { registered.yield() }
        async let waited = rig.controller.waitUntilIdle()
        for await _ in registrations { break }
        gate.released.fire()
        #expect(await waited == ModeSnapshot(mode: .off))
    }

    // MARK: Degraded KVM is repaired on select

    private static let degradedStatus = ok(
        "Diarchy KVM: degraded (clipboard bridge: stopped, file portals: stopped)\n", exit: 1)

    /// First `diarchy-kvm status` reports degraded, later ones enabled (the state after the repair).
    private func degradedRig(_ script: Script = Script()) -> Rig {
        var script = script
        script.firstKvmStatus = Self.degradedStatus
        return makeRig(script)
    }

    @Test func selectingKVMWhileDegradedTurnsKVMOffThenRunsTheNormalSequence() async {
        let rig = degradedRig()
        #expect(await rig.controller.refresh() == ModeSnapshot(mode: .kvm(degraded: true)))
        rig.runner.clearLog()
        rig.changes.clear()
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: false)))
        #expect(
            rig.labels == ["diarchy-kvm off", "host kvm start", "diarchy-kvm on", "diarchy-kvm status"])
        #expect(rig.changes.snapshots == [ModeSnapshot(mode: .transitioning), ModeSnapshot(mode: .kvm(degraded: false))])
    }

    @Test func degradedRepairStopsWhenKVMOffFailsAndStaysDegraded() async {
        var script = Script()
        script.kvmOff = ok(stderr: "off boom\n", exit: 1)
        let rig = degradedRig(script)
        _ = await rig.controller.refresh()
        rig.runner.clearLog()
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: true), error: "off boom"))
        #expect(rig.labels == ["diarchy-kvm off"])
    }

    @Test func degradedRepairEndsOffWhenTheHostStartFailsAfterwards() async {
        var script = Script()
        script.kvmStart = hostFailure(
            code: "service_failed", message: "diarchy-kvm.service failed to start", command: "kvm start")
        let rig = degradedRig(script)
        _ = await rig.controller.refresh()
        rig.runner.clearLog()
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(
            snapshot
                == ModeSnapshot(
                    mode: .off, error: "A service failed on the host: diarchy-kvm.service failed to start"))
        #expect(rig.labels == ["diarchy-kvm off", "host kvm start"])
    }

    @Test func degradedRepairRollsBackWhenKVMOnFailsAfterwards() async {
        var script = Script()
        script.kvmOn = Self.kvmOnBoom
        let rig = degradedRig(script)
        _ = await rig.controller.refresh()
        rig.runner.clearLog()
        let snapshot = await rig.controller.setMode(.kvm)
        #expect(snapshot == ModeSnapshot(mode: .off, error: "kvm on boom"))
        #expect(rig.labels == ["diarchy-kvm off", "host kvm start", "diarchy-kvm on", "diarchy-kvm off"])
    }

    @Test func selectingOffWhileDegradedStillJustTurnsKVMOff() async {
        let rig = degradedRig()
        _ = await rig.controller.refresh()
        rig.runner.clearLog()
        #expect(await rig.controller.setMode(.off) == ModeSnapshot(mode: .off))
        #expect(rig.labels == ["diarchy-kvm off"])
    }

    // MARK: Streaming needs the Mac side

    private static let mirrorPath = "/tmp/diarchy-home/.local/bin/diarchy-stream"
    private static let moonlightPath = "/tmp/diarchy-home/Applications/Moonlight.app/Contents/MacOS/Moonlight"
    private static let notInstalled = "Streaming is not installed on this Mac (use \"Install Streaming\" in Settings)."

    @Test(arguments: [mirrorPath, moonlightPath])
    func streamingWithAMissingMacPieceMakesNoHostCallAndStaysOff(missing: String) async {
        let rig = makeRig(isExecutable: { $0 != missing })
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot == ModeSnapshot(mode: .off, error: Self.notInstalled))
        #expect(rig.labels.isEmpty)
        #expect(rig.runner.fake.spawns.isEmpty)
        #expect(rig.changes.snapshots.last == snapshot)
    }

    @Test func streamingFromKVMWithAMissingMacPieceStaysInKVMAndRunsNothing() async {
        let rig = makeRig(isExecutable: { $0 != Self.moonlightPath })
        await rig.inKVM()
        let snapshot = await rig.controller.setMode(.streaming)
        #expect(snapshot == ModeSnapshot(mode: .kvm(degraded: false), error: Self.notInstalled))
        #expect(rig.labels.isEmpty)
    }

    @Test func theMacSideCheckLooksAtBothExactPaths() async {
        let asked = EventLog()
        let rig = makeRig(isExecutable: { asked.record($0); return true })
        _ = await rig.controller.setMode(.streaming)
        #expect(Set(asked.events) == [Self.mirrorPath, Self.moonlightPath])
    }

    // MARK: Failed stream stop stays retryable

    @Test func aFailedStopIsRetriedAtTheNextRefreshAndClearsOnSuccess() async {
        var script = Script()
        script.streamStop = hostFailure(code: "service_failed", message: "stop broke", command: "stream stop")
        let rig = makeRig(script)
        await rig.inStreaming()
        let failed = await rig.controller.setMode(.off)
        #expect(failed.error == "A service failed on the host: stop broke")
        rig.runner.clearLog()
        let retried = await rig.controller.refresh()
        #expect(rig.labels.first == "host stream stop")
        #expect(retried.error == "A service failed on the host: stop broke")
    }

    @Test func choosingOffRetriesAPendingStopEvenThoughTheModeIsOff() async {
        var script = Script()
        script.streamStop = hostFailure(code: "service_failed", message: "stop broke", command: "stream stop")
        let rig = makeRig(script)
        await rig.inStreaming()
        _ = await rig.controller.setMode(.off)
        rig.runner.clearLog()
        let snapshot = await rig.controller.setMode(.off)
        #expect(rig.labels == ["host stream stop"])
        #expect(snapshot.error == "A service failed on the host: stop broke")
    }

    @Test func aRefreshWithoutAFailedStopDoesNotRunStreamStop() async {
        let rig = makeRig()
        _ = await rig.controller.refresh()
        #expect(!rig.labels.contains("host stream stop"))
    }

    // MARK: Streaming that dies right away

    private static let diedAtOnce = "Streaming closed immediately. Run Diagnostics to see why."

    @Test(arguments: [Duration.zero, .milliseconds(4999)])
    func aChildExitingWithinFiveSecondsOfItsSpawnIsReported(lifetime: Duration) async {
        let rig = makeRig()
        await rig.inStreaming()
        rig.clock.advance(by: lifetime)
        rig.child.exit(code: 1)
        let final = await rig.changes.next { $0.mode == .off }
        #expect(final == ModeSnapshot(mode: .off, error: "\(Self.diedAtOnce) (diarchy-stream exited with code 1)"))
        #expect(rig.labels == ["host stream stop"])
        #expect(await rig.controller.snapshot == final)
    }

    @Test(arguments: [Duration.seconds(5), .seconds(6), .seconds(3600)])
    func aChildExitingAfterFiveSecondsEndsWithoutAnError(lifetime: Duration) async {
        let rig = makeRig()
        await rig.inStreaming()
        rig.clock.advance(by: lifetime)
        rig.child.exit(code: 0)
        let final = await rig.changes.next { $0.mode == .off }
        #expect(final == ModeSnapshot(mode: .off))
    }

    @Test func anImmediateExitKeepsAFailingStreamStopTextAppended() async {
        var script = Script()
        script.streamStop = hostFailure(code: "service_failed", message: "stop broke", command: "stream stop")
        let rig = makeRig(script)
        await rig.inStreaming()
        rig.clock.advance(by: .seconds(1))
        rig.child.exit(code: 0)
        let final = await rig.changes.next { $0.mode == .off }
        #expect(
            final
                == ModeSnapshot(
                    mode: .off,
                    error: "\(Self.diedAtOnce) (diarchy-host stream stop also failed: A service failed on the host: stop broke)"))
    }

    @Test func stoppingWithinFiveSecondsOfTheSpawnReportsNoError() async {
        let rig = makeRig()
        await rig.inStreaming()
        rig.clock.advance(by: .seconds(1))
        #expect(await rig.controller.setMode(.off) == ModeSnapshot(mode: .off))
    }
}

// MARK: - Pairing restores the mode that was active (Fix K)

/// Records what the pairing step observed (`hostStreamAlreadyRunning` and the controller commands run so far).
private final class PairObserver: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var calls: [(alreadyRunning: Bool, labels: [String])] = []
    var seen: [(alreadyRunning: Bool, labels: [String])] { lock.lock(); defer { lock.unlock() }; return calls }
    func record(_ alreadyRunning: Bool, _ labels: [String]) {
        lock.lock()
        calls.append((alreadyRunning, labels))
        lock.unlock()
    }
}

@Suite(.timeLimit(.minutes(1))) struct PairingModeFlowTests {
    private static let kvmBack = ["diarchy-kvm off", "host kvm start", "diarchy-kvm on", "diarchy-kvm status"]

    private func run(
        _ rig: Rig, from target: ModeTarget, pairing: Result<MoonlightPairingOutcome, MoonlightPairingError> = .success(.paired)
    ) async -> (PairingRun, PairObserver) {
        let observer = PairObserver()
        let result = await PairingModeFlow.run(from: target, controller: rig.controller) { alreadyRunning in
            observer.record(alreadyRunning, rig.labels)
            return pairing
        }
        return (result, observer)
    }

    @Test func kvmIsSwitchedOffForThePairingAndBroughtBackAfterASuccess() async {
        let rig = makeRig()
        await rig.inKVM()
        let (result, observer) = await run(rig, from: .kvm)
        #expect(observer.seen.count == 1)
        #expect(observer.seen.first?.alreadyRunning == false)
        #expect(observer.seen.first?.labels == ["diarchy-kvm off"])  // KVM was off while pairing
        #expect(rig.labels == Self.kvmBack)
        #expect(result.suspended == ModeSnapshot(mode: .off))
        #expect(result.pairing == .success(.paired))
        #expect(result.restored == ModeSnapshot(mode: .kvm(degraded: false)))
        #expect(await rig.controller.snapshot == ModeSnapshot(mode: .kvm(degraded: false)))
    }

    @Test func kvmIsBroughtBackAfterAFailedPairingToo() async {
        let rig = makeRig()
        await rig.inKVM()
        let failure = MoonlightPairingError.hostUnreachable
        let (result, _) = await run(rig, from: .kvm, pairing: .failure(failure))
        #expect(result.pairing == .failure(failure))
        #expect(rig.labels == Self.kvmBack)
        #expect(result.restored == ModeSnapshot(mode: .kvm(degraded: false)))
    }

    @Test func anAlreadyPairedResultAlsoBringsKVMBack() async {
        let rig = makeRig()
        await rig.inKVM()
        let (result, _) = await run(rig, from: .kvm, pairing: .success(.alreadyPaired))
        #expect(result.pairing == .success(.alreadyPaired))
        #expect(result.restored == ModeSnapshot(mode: .kvm(degraded: false)))
    }

    @Test func aKVMThatCannotBeSwitchedOffRunsNoPairingAndNoRestore() async {
        var script = Script()
        script.kvmOff = ok(stderr: "off boom\n", exit: 1)
        let rig = makeRig(script)
        await rig.inKVM()
        let (result, observer) = await run(rig, from: .kvm)
        #expect(observer.seen.isEmpty)
        #expect(result.pairing == nil)
        #expect(result.restored == nil)
        #expect(result.suspended?.error == "off boom")
        #expect(rig.labels == ["diarchy-kvm off"])
    }

    @Test func aKVMThatFailsToComeBackIsReportedInTheRestoreSnapshot() async {
        var script = Script()
        script.kvmStart = hostFailure(code: "service_failed", message: "kvm broke", command: "kvm start")
        let rig = makeRig(script)  // the controller is off; the flow is told KVM was active
        let (result, _) = await run(rig, from: .kvm)
        #expect(result.pairing == .success(.paired))
        #expect(result.restored == ModeSnapshot(mode: .off, error: "A service failed on the host: kvm broke"))
    }

    @Test func offStaysOffAndRunsNoModeCommands() async {
        let rig = makeRig()
        let (result, observer) = await run(rig, from: .off)
        #expect(observer.seen.first?.alreadyRunning == false)
        #expect(rig.labels.isEmpty)
        #expect(result.suspended == nil)
        #expect(result.restored == nil)
    }

    @Test func streamingKeepsRunningAndRunsNoModeCommands() async {
        let rig = makeRig()
        await rig.inStreaming()
        let (result, observer) = await run(rig, from: .streaming)
        #expect(observer.seen.first?.alreadyRunning == true)
        #expect(rig.labels.isEmpty)
        #expect(result.suspended == nil)
        #expect(result.restored == nil)
        #expect(await rig.controller.snapshot == ModeSnapshot(mode: .streaming))
    }
}
