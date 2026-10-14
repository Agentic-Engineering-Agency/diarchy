import Foundation

/// What the app does when asked to terminate by any route (menu `Quit`, Cmd-Q, AppleScript `quit`, logout,
/// shutdown): a running stream or pairing must be stopped first, or Moonlight and the host's Sunshine stream outlive
/// the app. Pure decision plus a bounded wait; the app delegate maps the result onto `NSApplication`.
public enum Termination {
    public enum Reply: Sendable, Equatable {
        /// Nothing to clean up: terminate now.
        case now
        /// Cleanup is running; `reply` is called exactly once when it is done or the timeout elapsed.
        case later
    }

    /// Longest the app waits for cleanup, so a logout or shutdown is never held up forever.
    public static let timeout: Duration = .seconds(15)

    /// Decides whether the app can terminate now. Only a running stream, a pairing (it owns a host stream) or a
    /// mode change in flight (it may end up streaming) need cleanup; off and KVM are left as they are.
    /// For `.later`, runs `cleanup` raced against `timeout` and then calls `reply` once.
    public static func begin(
        mode: Mode, pairingRunning: Bool, timeout: Duration = Termination.timeout,
        sleep: @escaping @Sendable (Duration) async -> Void = { try? await Task.sleep(for: $0) },
        cleanup: @escaping @Sendable () async -> Void,
        onTimeout: @escaping @Sendable () async -> Void = {},
        reply: @escaping @Sendable () -> Void
    ) -> Reply {
        guard needsCleanup(mode: mode, pairingRunning: pairingRunning) else { return .now }
        Task {
            if await raceCleanup(timeout: timeout, sleep: sleep, cleanup: cleanup) { await onTimeout() }
            reply()
        }
        return .later
    }

    static func needsCleanup(mode: Mode, pairingRunning: Bool) -> Bool {
        if pairingRunning { return true }
        switch mode {
        case .streaming, .transitioning: return true
        case .off, .kvm: return false
        }
    }

    /// Returns when `cleanup` finished or `timeout` elapsed, whichever is first; `true` when the timeout won. The
    /// loser is not awaited: a cleanup that ignores cancellation must not hold the app back.
    private static func raceCleanup(
        timeout: Duration, sleep: @escaping @Sendable (Duration) async -> Void,
        cleanup: @escaping @Sendable () async -> Void
    ) async -> Bool {
        await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            let once = Once(continuation)
            let timer = Task {
                await sleep(timeout)
                once.resume(returning: true)
            }
            Task {
                await cleanup()
                timer.cancel()
                once.resume(returning: false)
            }
        }
    }
}

/// Resumes its continuation the first time `resume()` is called and ignores every later call.
private final class Once: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Bool, Never>?

    init(_ continuation: CheckedContinuation<Bool, Never>) { self.continuation = continuation }

    func resume(returning value: Bool) {
        lock.lock()
        let taken = continuation
        continuation = nil
        lock.unlock()
        taken?.resume(returning: value)
    }
}
