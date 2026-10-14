import Foundation
import Testing

@testable import DiarchyCore

/// Records what happened in order, from any task.
private final class Log: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [String] = []

    func add(_ item: String) {
        lock.lock()
        items.append(item)
        lock.unlock()
    }

    var all: [String] {
        lock.lock()
        defer { lock.unlock() }
        return items
    }
}

/// Runs `Termination.begin` and waits for the reply when the answer is `.later`.
private func begin(
    mode: Mode, pairingRunning: Bool = false, log: Log,
    sleep: @escaping @Sendable (Duration) async -> Void = { _ in await neverWakes() },
    cleanup: @escaping @Sendable () async -> Void,
    onTimeout: @escaping @Sendable () async -> Void = {}
) async -> Termination.Reply {
    await withCheckedContinuation { (outcome: CheckedContinuation<Termination.Reply, Never>) in
        let reply = Termination.begin(
            mode: mode, pairingRunning: pairingRunning, timeout: .seconds(15), sleep: sleep,
            cleanup: {
                log.add("cleanup start")
                await cleanup()
                log.add("cleanup end")
            },
            onTimeout: {
                log.add("timeout")
                await onTimeout()
            },
            reply: {
                log.add("reply")
                outcome.resume(returning: .later)
            })
        if reply == .now { outcome.resume(returning: .now) }
    }
}

/// A timer that does not fire in the lifetime of a test (until its task is cancelled).
private func neverWakes() async {
    try? await Task.sleep(for: .seconds(3600))
}

@Suite struct TerminationTests {
    @Test func offAndKVMTerminateImmediatelyWithoutCleanup() async {
        for mode in [Mode.off, .kvm(degraded: false), .kvm(degraded: true)] {
            let log = Log()
            let reply = await begin(mode: mode, log: log) {}
            #expect(reply == .now)
            #expect(log.all.isEmpty)
        }
    }

    @Test func streamingCleansUpThenRepliesOnce() async {
        let log = Log()
        let reply = await begin(mode: .streaming, log: log) { log.add("stop stream") }
        #expect(reply == .later)
        #expect(log.all == ["cleanup start", "stop stream", "cleanup end", "reply"])
    }

    @Test func aRunningPairingNeedsCleanupEvenWhenKVMIsOn() async {
        let log = Log()
        let reply = await begin(mode: .kvm(degraded: false), pairingRunning: true, log: log) {}
        #expect(reply == .later)
        #expect(log.all == ["cleanup start", "cleanup end", "reply"])
    }

    @Test func aModeChangeInFlightNeedsCleanup() async {
        let log = Log()
        let reply = await begin(mode: .transitioning, log: log) {}
        #expect(reply == .later)
        #expect(log.all.last == "reply")
    }

    @Test func aStuckCleanupRepliesAnywayWhenTheTimeoutFires() async {
        let log = Log()
        let (stuck, release) = AsyncStream.makeStream(of: Void.self)
        let reply = await begin(
            mode: .streaming, log: log,
            sleep: { _ in /* the timeout elapses at once */ },
            cleanup: { for await _ in stuck {} })
        #expect(reply == .later)
        #expect(log.all.first == "cleanup start")
        #expect(log.all.contains("reply"))
        #expect(!log.all.contains("cleanup end"))
        release.finish()
    }

    @Test func theReplyIsSentOnceWhenCleanupFinishesAfterTheTimeout() async throws {
        let log = Log()
        let (stuck, release) = AsyncStream.makeStream(of: Void.self)
        _ = await begin(
            mode: .streaming, log: log, sleep: { _ in }, cleanup: { for await _ in stuck {} })
        release.finish()
        try await Task.sleep(for: .milliseconds(100))
        #expect(log.all.filter { $0 == "reply" }.count == 1)
    }

    @Test func theTimeoutHookRunsBeforeTheReplyWhenCleanupIsStuck() async {
        let log = Log()
        let (stuck, release) = AsyncStream.makeStream(of: Void.self)
        _ = await begin(mode: .transitioning, log: log, sleep: { _ in }, cleanup: { for await _ in stuck {} })
        let events = log.all.filter { $0 == "timeout" || $0 == "reply" }
        #expect(events == ["timeout", "reply"])
        release.finish()
    }

    @Test func theTimeoutHookDoesNotRunWhenCleanupFinishesInTime() async {
        let log = Log()
        _ = await begin(mode: .streaming, log: log) {}
        #expect(!log.all.contains("timeout"))
    }
}
