import Foundation

/// Brings the Moonlight window of a freshly started stream in front of the other apps and keeps it there while the
/// session connects: `diarchy-stream` `exec`s Moonlight, whose window otherwise opens behind the terminal the user is
/// looking at, and drops back when the session goes fullscreen.
public protocol StreamWindowActivating: Sendable {
    /// Returns at once; waiting for the app to show up, activating it and watching it continue in the background. A
    /// call replaces any earlier one still running.
    func bringToFront(pid: Int32)
    /// Stops whatever `bringToFront` still does in the background.
    func cancel()
}

/// Does nothing: used where no UI is involved.
public struct NoWindowActivation: StreamWindowActivating {
    public init() {}
    public func bringToFront(pid: Int32) {}
    public func cancel() {}
}

/// A running macOS app, as far as activation needs it. The AppKit adapter lives in the app target.
public protocol ActivatableApp: Sendable {
    func activate() async
    func isActive() async -> Bool
}

/// Waits for the process to register as a running app, every 250 ms for at most 60 s, or until the process is gone:
/// `diarchy-stream` runs `stream start` over ssh (about 10 s, up to its 30 s limit) before it `exec`s Moonlight, so
/// the pid exists long before the app does. Then it activates the app and, for 20 s (every 500 ms), whenever the app
/// is not active, opens it by bundle (`open -b`, which LaunchServices always brings to the front), at most 4 times;
/// that watch ends early when the process is gone. No AppleScript: that would trigger an Automation permission prompt.
public final class MoonlightWindowActivator: StreamWindowActivating, @unchecked Sendable {  // `lock` guards `task`
    private let runner: any CommandRunner
    private let lookup: @Sendable (Int32) async -> (any ActivatableApp)?
    private let isAlive: @Sendable (Int32) -> Bool
    private let sleep: @Sendable (Duration) async throws -> Void
    private let lock = NSLock()
    private var task: Task<Void, Never>?

    static let bundleIdentifier = "com.moonlight-stream.Moonlight"
    private static let pollInterval: Duration = .milliseconds(250)
    private static let pollAttempts = 240  // 60 s
    private static let watchInterval: Duration = .milliseconds(500)
    private static let watchTicks = 40
    private static let maxReactivations = 4
    private static let openTimeout: TimeInterval = 10

    public init(
        runner: any CommandRunner,
        lookup: @escaping @Sendable (Int32) async -> (any ActivatableApp)?,
        isAlive: @escaping @Sendable (Int32) -> Bool = MoonlightWindowActivator.processIsAlive,
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        self.runner = runner
        self.lookup = lookup
        self.isAlive = isAlive
        self.sleep = sleep
    }

    /// `kill(pid, 0)` sends nothing: it only reports whether the process exists (and may be signalled).
    public static func processIsAlive(_ pid: Int32) -> Bool {
        kill(pid, 0) == 0 || errno == EPERM
    }

    public func bringToFront(pid: Int32) {
        let next = Task { [self] in await activate(pid: pid) }
        let previous = lock.withLock {
            defer { task = next }
            return task
        }
        previous?.cancel()
    }

    public func cancel() {
        lock.withLock { task }?.cancel()
    }

    /// Test hook: resolves once the most recent background run has finished.
    func waitUntilFinished() async {
        await lock.withLock { task }?.value
    }

    /// The whole procedure; `bringToFront` runs it in the background.
    func activate(pid: Int32) async {
        guard let app = await waitForApp(pid: pid) else { return }
        await app.activate()
        // Activation is asynchronous: give it a moment before judging it.
        guard await pause(Self.pollInterval) else { return }
        if await !app.isActive() { await openBundle() }
        await watch(pid: pid)
    }

    /// Re-opens the bundle whenever the app is not active, until the cap, the end of the window, the process's exit,
    /// or cancellation.
    private func watch(pid: Int32) async {
        var reactivations = 0
        for _ in 0..<Self.watchTicks {
            guard await pause(Self.watchInterval) else { return }
            guard let app = await lookup(pid) else { return }  // the process exited
            guard await !app.isActive() else { continue }
            guard reactivations < Self.maxReactivations else { return }
            reactivations += 1
            await openBundle()
        }
    }

    /// Polls until the app registers. Ends without it when the process is gone, the cap is reached, or the task is
    /// cancelled.
    private func waitForApp(pid: Int32) async -> (any ActivatableApp)? {
        for attempt in 0..<Self.pollAttempts {
            if let app = await lookup(pid) { return app }
            guard attempt < Self.pollAttempts - 1, isAlive(pid) else { return nil }
            guard await pause(Self.pollInterval) else { return nil }
        }
        return nil
    }

    /// `false` when the task was cancelled meanwhile.
    private func pause(_ duration: Duration) async -> Bool {
        do { try await sleep(duration) } catch { return false }
        return !Task.isCancelled
    }

    private func openBundle() async {
        _ = try? await runner.run(
            executable: "/usr/bin/open", arguments: ["-b", Self.bundleIdentifier], environment: nil, stdin: nil,
            timeout: Self.openTimeout)
    }
}
