import Foundation
@testable import DiarchyCore

/// Thrown by `FakeCommandRunner` when no rule matches a call, so a test can never pass by silently succeeding.
struct UnmatchedCommandError: Error, CustomStringConvertible, Equatable {
    /// `executable arguments…`, prefixed with `spawn ` for an unmatched `spawn`.
    var commandLine: String
    var description: String { "FakeCommandRunner: no rule matches \(commandLine)" }
}

/// Scriptable `CommandRunner` for unit tests. Rules are matched first-to-last; the first matching rule wins.
/// `run`/`runStreaming` need `when` rules, `spawn` needs `whenSpawn` rules; anything unmatched throws.
final class FakeCommandRunner: CommandRunner, @unchecked Sendable {  // every access holds `lock`
    /// A `run` or `runStreaming` call.
    struct Call: Equatable, Sendable {
        var executable: String
        var arguments: [String]
        var environment: [String: String]?
        var stdin: Data?
        var timeout: TimeInterval
        var commandLine: String { ([executable] + arguments).joined(separator: " ") }
    }

    /// A `spawn` call.
    struct SpawnCall: Equatable, Sendable {
        var executable: String
        var arguments: [String]
        var environment: [String: String]?
        var commandLine: String { ([executable] + arguments).joined(separator: " ") }
    }

    typealias Matcher = @Sendable (Call) -> Bool
    typealias SpawnMatcher = @Sendable (SpawnCall) -> Bool

    private struct Rule {
        var matcher: Matcher
        var result: Result<CommandResult, CommandError>
    }

    private struct SpawnRule {
        var matcher: SpawnMatcher
        var result: Result<any ChildProcess, CommandError>
    }

    private let lock = NSLock()
    private var recordedCalls: [Call] = []
    private var recordedSpawns: [SpawnCall] = []
    private var rules: [Rule] = []
    private var spawnRules: [SpawnRule] = []

    /// Every `run`/`runStreaming` call, in order (matched or not).
    var calls: [Call] { locked { recordedCalls } }
    /// Every `spawn` call, in order (matched or not).
    var spawns: [SpawnCall] { locked { recordedSpawns } }

    /// Appends a rule; an earlier matching rule always wins over a later one.
    func when(_ matcher: @escaping Matcher, return result: Result<CommandResult, CommandError>) {
        locked { rules.append(Rule(matcher: matcher, result: result)) }
    }

    /// Convenience: match by executable basename and exact argument list.
    func when(
        _ executableName: String, _ arguments: [String], return result: Result<CommandResult, CommandError>
    ) {
        when(
            { ($0.executable as NSString).lastPathComponent == executableName && $0.arguments == arguments },
            return: result)
    }

    /// Appends a spawn rule that hands out `child`.
    func whenSpawn(_ matcher: @escaping SpawnMatcher, returns child: any ChildProcess) {
        locked { spawnRules.append(SpawnRule(matcher: matcher, result: .success(child))) }
    }

    /// Appends a spawn rule that makes `spawn` throw `error`.
    func whenSpawn(_ matcher: @escaping SpawnMatcher, fails error: CommandError) {
        locked { spawnRules.append(SpawnRule(matcher: matcher, result: .failure(error))) }
    }

    /// Convenience: match by executable basename and, when given, the exact argument list.
    func whenSpawn(_ executableName: String, _ arguments: [String]? = nil, returns child: any ChildProcess) {
        whenSpawn(Self.spawnMatcher(executableName, arguments), returns: child)
    }

    /// Convenience: match by executable basename and, when given, the exact argument list.
    func whenSpawn(_ executableName: String, _ arguments: [String]? = nil, fails error: CommandError) {
        whenSpawn(Self.spawnMatcher(executableName, arguments), fails: error)
    }

    private static func spawnMatcher(_ executableName: String, _ arguments: [String]?) -> SpawnMatcher {
        { call in
            (call.executable as NSString).lastPathComponent == executableName
                && (arguments == nil || call.arguments == arguments!)
        }
    }

    func run(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval
    ) async throws -> CommandResult {
        let call = Call(
            executable: executable, arguments: arguments, environment: environment, stdin: stdin, timeout: timeout)
        return try resolve(call).get()
    }

    func runStreaming(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval, onLine: @escaping @Sendable (String) -> Void
    ) async throws -> CommandResult {
        let call = Call(
            executable: executable, arguments: arguments, environment: environment, stdin: stdin, timeout: timeout)
        let result = try resolve(call).get()
        for text in [result.stdout, result.stderr] {
            for line in text.split(separator: "\n", omittingEmptySubsequences: true) { onLine(String(line)) }
        }
        return result
    }

    func spawn(executable: String, arguments: [String], environment: [String: String]?) throws -> any ChildProcess {
        let call = SpawnCall(executable: executable, arguments: arguments, environment: environment)
        return try locked {
            recordedSpawns.append(call)
            guard let rule = spawnRules.first(where: { $0.matcher(call) }) else {
                throw UnmatchedCommandError(commandLine: "spawn \(call.commandLine)")
            }
            return try rule.result.get()
        }
    }

    private func resolve(_ call: Call) throws -> Result<CommandResult, CommandError> {
        try locked {
            recordedCalls.append(call)
            guard let rule = rules.first(where: { $0.matcher(call) }) else {
                throw UnmatchedCommandError(commandLine: call.commandLine)
            }
            return rule.result
        }
    }

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try body()
    }
}

/// Child whose lifetime the test controls: `exit(code:)` resolves `waitForExit()`.
final class FakeChildProcess: ChildProcess, @unchecked Sendable {  // every access holds `lock`
    let pid: Int32
    private let exitsOnTerminate: Bool
    private let lock = NSLock()
    private var exitCode: Int32?
    private var waiters: [CheckedContinuation<Int32, Never>] = []
    private var wasTerminated = false
    private var wasKilled = false
    private var errorLine: String?

    /// `exitsOnTerminate: false` models a child that ignores SIGTERM until it is killed.
    init(pid: Int32 = 1234, exitsOnTerminate: Bool = true) {
        self.pid = pid
        self.exitsOnTerminate = exitsOnTerminate
    }

    var terminated: Bool { lock.lock(); defer { lock.unlock() }; return wasTerminated }
    var killed: Bool { lock.lock(); defer { lock.unlock() }; return wasKilled }
    var hasExited: Bool { lock.lock(); defer { lock.unlock() }; return exitCode != nil }
    var lastErrorLine: String? {
        get { lock.lock(); defer { lock.unlock() }; return errorLine }
        set { lock.lock(); errorLine = newValue; lock.unlock() }
    }

    /// Ends the child; the first call wins.
    func exit(code: Int32) {
        lock.lock()
        guard exitCode == nil else {
            lock.unlock()
            return
        }
        exitCode = code
        let pending = waiters
        waiters = []
        lock.unlock()
        for waiter in pending { waiter.resume(returning: code) }
    }

    func waitForExit() async -> Int32 {
        await withCheckedContinuation { continuation in
            lock.lock()
            if let code = exitCode {
                lock.unlock()
                continuation.resume(returning: code)
            } else {
                waiters.append(continuation)
                lock.unlock()
            }
        }
    }

    func terminate() {
        lock.lock()
        wasTerminated = true
        lock.unlock()
        if exitsOnTerminate { exit(code: 143) }
    }

    func kill() {
        lock.lock()
        wasKilled = true
        lock.unlock()
        exit(code: 137)
    }
}
