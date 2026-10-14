import Foundation

public struct CommandResult: Equatable, Sendable {
    public var exitCode: Int32
    public var stdout: String
    public var stderr: String

    public init(exitCode: Int32, stdout: String = "", stderr: String = "") {
        self.exitCode = exitCode
        self.stdout = stdout
        self.stderr = stderr
    }

    public var succeeded: Bool { exitCode == 0 }

    /// First meaningful stderr line, else first meaningful stdout line, else `exit code N`. Lines made only of `@`
    /// and whitespace (OpenSSH banner frames) and ssh's `Warning: Permanently added …` notice are skipped, and `@`
    /// framing is trimmed off the others.
    public var firstErrorLine: String {
        Self.firstMeaningfulLine(stderr) ?? Self.firstMeaningfulLine(stdout) ?? "exit code \(exitCode)"
    }

    private static let framing = CharacterSet(charactersIn: "@").union(.whitespaces)
    private static let knownHostsNotice = "Warning: Permanently added"

    private static func firstMeaningfulLine(_ text: String) -> String? {
        for line in text.split(whereSeparator: \.isNewline) {
            let trimmed = line.trimmingCharacters(in: framing)
            if !trimmed.isEmpty, !trimmed.hasPrefix(knownHostsNotice) { return trimmed }
        }
        return nil
    }
}

public enum CommandError: Error, Equatable, Sendable {
    /// The process could not be started; `command` is the executable as given.
    case launchFailed(command: String, message: String)
    /// The process outlived its timeout and was killed; `command` is the executable basename.
    case timedOut(command: String)
}

/// A long-lived child started with `CommandRunner.spawn`. Signals go to the whole process group.
public protocol ChildProcess: Sendable {
    var pid: Int32 { get }
    /// Exit code; `128 + signal` when the child was signalled.
    func waitForExit() async -> Int32
    /// `true` once the child has exited. Never blocks.
    var hasExited: Bool { get }
    /// SIGTERM to the whole process group.
    func terminate()
    /// SIGKILL to the whole process group.
    func kill()
    /// Last non-empty line the child wrote to stderr so far; `nil` when it wrote none or stderr is not captured.
    var lastErrorLine: String? { get }
}

extension ChildProcess {
    public var lastErrorLine: String? { nil }

    /// Nothing when the child already exited; else SIGTERM, then up to `grace` (measured by the injected `sleep`) for
    /// it to go, then SIGKILL if it is still alive. Returns once the child has exited.
    func terminateThenKill(
        after grace: Duration, sleep: @escaping @Sendable (Duration) async throws -> Void
    ) async {
        guard !hasExited else { return }
        terminate()
        let gate = OneShot()
        let exitWatcher = Task {
            _ = await self.waitForExit()
            gate.fire()
        }
        let graceTimer = Task {
            try? await sleep(grace)
            gate.fire()
        }
        await gate.wait()
        graceTimer.cancel()
        if !hasExited { kill() }
        await exitWatcher.value
    }
}

public protocol CommandRunner: Sendable {
    /// `environment == nil` inherits the process environment; otherwise it replaces it entirely.
    func run(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval
    ) async throws -> CommandResult

    /// Like `run`, also delivering stdout and stderr lines (without terminators, empty lines skipped) to `onLine`
    /// in arrival order. The full output is still accumulated in the result.
    func runStreaming(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval, onLine: @escaping @Sendable (String) -> Void
    ) async throws -> CommandResult

    /// Starts a long-lived child with its output discarded; the caller owns its lifetime.
    func spawn(executable: String, arguments: [String], environment: [String: String]?) throws -> any ChildProcess
}

/// Runs real processes. Every child gets its own process group so a timeout takes grandchildren down too.
public struct ProcessCommandRunner: CommandRunner {
    /// Time between SIGTERM and SIGKILL when a command times out.
    private static let terminateGrace: Duration = .seconds(1)
    /// How long output may keep arriving after the leader exited (a background child can hold the pipes open).
    private static let drainGrace: Duration = .milliseconds(1500)

    public init() {}

    public func run(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval
    ) async throws -> CommandResult {
        try await execute(
            executable: executable, arguments: arguments, environment: environment, stdin: stdin,
            timeout: timeout, emitter: nil)
    }

    public func runStreaming(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval, onLine: @escaping @Sendable (String) -> Void
    ) async throws -> CommandResult {
        try await execute(
            executable: executable, arguments: arguments, environment: environment, stdin: stdin,
            timeout: timeout, emitter: LineEmitter(onLine))
    }

    public func spawn(executable: String, arguments: [String], environment: [String: String]?) throws -> any ChildProcess
    {
        let stderrPipe = try PosixSpawn.makePipe(for: executable)
        let pid: Int32
        do {
            pid = try PosixSpawn.start(
                executable: executable, arguments: arguments, environment: environment,
                stdin: nil, stdout: nil, stderr: stderrPipe.write)
        } catch {
            PosixSpawn.closeAll(stderrPipe.read, stderrPipe.write)
            throw error
        }
        PosixSpawn.closeAll(stderrPipe.write)
        let tail = StderrTail()
        tail.start(reading: stderrPipe.read)
        return SpawnedProcess(pid: pid, stderrTail: tail)
    }

    private enum Outcome: Sendable {
        case exited(Int32)
        case timedOut
        case cancelled
    }

    private func execute(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval, emitter: LineEmitter?
    ) async throws -> CommandResult {
        let stdinPipe = try stdin.map { _ in try PosixSpawn.makePipe(for: executable) }
        let stdoutPipe: (read: Int32, write: Int32)
        let stderrPipe: (read: Int32, write: Int32)
        do {
            stdoutPipe = try PosixSpawn.makePipe(for: executable)
        } catch {
            PosixSpawn.closeAll(stdinPipe?.read, stdinPipe?.write)
            throw error
        }
        do {
            stderrPipe = try PosixSpawn.makePipe(for: executable)
        } catch {
            PosixSpawn.closeAll(stdinPipe?.read, stdinPipe?.write, stdoutPipe.read, stdoutPipe.write)
            throw error
        }

        let pid: Int32
        do {
            pid = try PosixSpawn.start(
                executable: executable, arguments: arguments, environment: environment,
                stdin: stdinPipe?.read, stdout: stdoutPipe.write, stderr: stderrPipe.write)
        } catch {
            PosixSpawn.closeAll(
                stdinPipe?.read, stdinPipe?.write, stdoutPipe.read, stdoutPipe.write, stderrPipe.read,
                stderrPipe.write)
            throw error
        }
        // The parent keeps only its own ends, otherwise EOF would never arrive.
        PosixSpawn.closeAll(stdinPipe?.read, stdoutPipe.write, stderrPipe.write)

        let child = SpawnedProcess(pid: pid)
        // Two independent readers drain stdout and stderr, so a child filling one pipe never waits on the other.
        let stdoutReader = PipeReader(descriptor: stdoutPipe.read, emitter: emitter)
        let stderrReader = PipeReader(descriptor: stderrPipe.read, emitter: emitter)
        if let stdin, let stdinPipe { Self.feed(stdin, to: stdinPipe.write) }

        let outcome: Outcome = await withTaskGroup(of: Outcome.self) { group in
            group.addTask { .exited(await child.waitForExit()) }
            group.addTask {
                do {
                    try await Task.sleep(for: .seconds(timeout))
                    return .timedOut
                } catch {
                    return .cancelled
                }
            }
            let first = await group.next() ?? .cancelled
            group.cancelAll()
            if case .exited = first { return first }
            await Self.stop(child)
            // Something outside the group may still hold the pipes open; do not wait for it.
            stdoutReader.abandon()
            stderrReader.abandon()
            return first
        }

        switch outcome {
        case .exited(let code):
            await Self.drain(stdoutReader, stderrReader)
            return CommandResult(exitCode: code, stdout: stdoutReader.text, stderr: stderrReader.text)
        case .timedOut:
            throw CommandError.timedOut(command: (executable as NSString).lastPathComponent)
        case .cancelled:
            throw CancellationError()
        }
    }

    /// Waits for both readers to reach EOF, but only `drainGrace` once the leader is gone: a daemon the command left
    /// behind may keep the pipes open forever. Then the readers are abandoned and what was collected is returned.
    /// The leftover processes are not touched.
    private static func drain(_ first: PipeReader, _ second: PipeReader) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask {
                await first.finished()
                await second.finished()
            }
            group.addTask { try? await Task.sleep(for: drainGrace) }
            await group.next()
            // Abandoning a reader that already saw EOF is a no-op; for a pending one it releases `finished()`.
            first.abandon()
            second.abandon()
            group.cancelAll()
        }
    }

    /// SIGTERM to the group, a grace period for the leader to exit, then SIGKILL to the group, then reap.
    private static func stop(_ child: SpawnedProcess) async {
        child.forceSignal(SIGTERM)
        let gate = OneShot()
        let exitWatcher = Task {
            _ = await child.waitForExit()
            gate.fire()
        }
        let graceTimer = Task {
            try? await Task.sleep(for: terminateGrace)
            gate.fire()
        }
        await gate.wait()
        graceTimer.cancel()
        // Also reaches grandchildren that ignored SIGTERM or outlived the leader.
        child.forceSignal(SIGKILL)
        await exitWatcher.value
    }

    /// Writes `data` on a dedicated thread and closes the descriptor; a child that never reads cannot stall the caller.
    private static func feed(_ data: Data, to descriptor: Int32) {
        Thread.detachNewThread {
            data.withUnsafeBytes { buffer in
                var offset = 0
                while offset < buffer.count {
                    let written = write(descriptor, buffer.baseAddress! + offset, buffer.count - offset)
                    if written < 0 {
                        if errno == EINTR { continue }
                        break  // EPIPE: the child closed stdin or died.
                    }
                    offset += written
                }
            }
            close(descriptor)
        }
    }
}

// MARK: - Spawning

enum PosixSpawn {
    static func makePipe(for executable: String) throws -> (read: Int32, write: Int32) {
        var fds: [Int32] = [-1, -1]
        guard pipe(&fds) == 0 else {
            throw CommandError.launchFailed(command: executable, message: String(cString: strerror(errno)))
        }
        for descriptor in fds { _ = fcntl(descriptor, F_SETFD, FD_CLOEXEC) }
        _ = fcntl(fds[1], F_SETNOSIGPIPE, 1)  // a vanished reader must not kill the app with SIGPIPE
        return (fds[0], fds[1])
    }

    static func closeAll(_ descriptors: Int32?...) {
        for case let descriptor? in descriptors { close(descriptor) }
    }

    /// `posix_spawn` in a fresh process group. Child-side descriptors that are `nil` map to `/dev/null`; every other
    /// descriptor is closed in the child (`POSIX_SPAWN_CLOEXEC_DEFAULT`).
    static func start(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Int32?, stdout: Int32?,
        stderr: Int32?
    ) throws -> Int32 {
        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }

        for (target, source) in [(Int32(0), stdin), (1, stdout), (2, stderr)] {
            if let source {
                posix_spawn_file_actions_adddup2(&actions, source, target)
            } else {
                posix_spawn_file_actions_addopen(&actions, target, "/dev/null", target == 0 ? O_RDONLY : O_WRONLY, 0)
            }
        }
        var defaultSignals = sigset_t()
        sigfillset(&defaultSignals)
        var emptyMask = sigset_t()
        sigemptyset(&emptyMask)
        posix_spawnattr_setsigdefault(&attributes, &defaultSignals)
        posix_spawnattr_setsigmask(&attributes, &emptyMask)
        posix_spawnattr_setpgroup(&attributes, 0)
        let flags = POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETSIGDEF
            | POSIX_SPAWN_SETSIGMASK
        posix_spawnattr_setflags(&attributes, Int16(flags))

        let argv: [UnsafeMutablePointer<CChar>?] = ([executable] + arguments).map { strdup($0) } + [nil]
        let env = (environment ?? ProcessInfo.processInfo.environment).map { strdup("\($0.key)=\($0.value)") }
        let envp: [UnsafeMutablePointer<CChar>?] = env + [nil]
        defer {
            for pointer in argv { free(pointer) }
            for pointer in env { free(pointer) }
        }

        var pid: pid_t = 0
        let status = posix_spawn(&pid, executable, &actions, &attributes, argv, envp)
        guard status == 0 else {
            throw CommandError.launchFailed(command: executable, message: String(cString: strerror(status)))
        }
        return pid
    }
}

/// Keeps the last non-empty line of a pipe. A dedicated thread reads until EOF and closes the descriptor.
final class StderrTail: @unchecked Sendable {  // `line` is guarded by `lock`
    private static let maxLineBytes = 512
    private let lock = NSLock()
    private var line: String?

    var lastLine: String? {
        lock.lock()
        defer { lock.unlock() }
        return line
    }

    func start(reading descriptor: Int32) {
        Thread.detachNewThread { [self] in
            var pending = Data()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while true {
                let count = read(descriptor, &buffer, buffer.count)
                if count < 0, errno == EINTR { continue }
                if count <= 0 { break }
                pending.append(contentsOf: buffer[0..<count])
                while let newline = pending.firstIndex(of: 0x0A) {
                    record(pending[pending.startIndex..<newline])
                    pending.removeSubrange(pending.startIndex...newline)
                }
                if pending.count > Self.maxLineBytes { pending = pending.suffix(Self.maxLineBytes) }
            }
            record(pending)
            close(descriptor)
        }
    }

    private func record(_ bytes: Data) {
        let text = String(decoding: bytes.suffix(Self.maxLineBytes), as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        lock.lock()
        line = text
        lock.unlock()
    }
}

/// A started process. A dedicated thread reaps it, so `waitForExit()` costs no polling.
final class SpawnedProcess: ChildProcess, @unchecked Sendable {  // mutable state is guarded by `lock`
    let pid: Int32
    private let signalGroup: @Sendable (Int32, Int32) -> Int32
    private let lock = NSLock()
    private var exitCode: Int32?
    private var waiters: [CheckedContinuation<Int32, Never>] = []
    private var reaped = false
    private let stderrTail: StderrTail?

    var lastErrorLine: String? { stderrTail?.lastLine }

    /// `signalGroup(pgid, signal)` defaults to `killpg`; tests inject a spy.
    init(
        pid: Int32, signalGroup: @escaping @Sendable (Int32, Int32) -> Int32 = { killpg($0, $1) },
        stderrTail: StderrTail? = nil
    ) {
        self.pid = pid
        self.signalGroup = signalGroup
        self.stderrTail = stderrTail
        Thread.detachNewThread { [self] in reap() }
    }

    var hasExited: Bool {
        lock.lock()
        defer { lock.unlock() }
        return exitCode != nil
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

    /// Public signals are no-ops once the leader was reaped: its pid, and with it the group id, may be recycled for an
    /// unrelated group. The flag is set under `lock` before `waitpid` frees the pid, and signals are sent under the
    /// same lock, so a signal can only go out while the (possibly zombie) leader still pins the group id.
    func terminate() { signalWhileLeaderIsNotReaped(SIGTERM) }
    func kill() { signalWhileLeaderIsNotReaped(SIGKILL) }

    /// Signals the group even after the leader exited. Only for cleanup right after a timeout, to reach grandchildren
    /// of a leader that was reaped moments ago.
    func forceSignal(_ signal: Int32) { _ = signalGroup(pid, signal) }

    private func signalWhileLeaderIsNotReaped(_ signal: Int32) {
        lock.lock()
        defer { lock.unlock() }
        if !reaped { _ = signalGroup(pid, signal) }
    }

    /// Waits for the exit without reaping (`WNOWAIT`), publishes `reaped` under the lock, and only then frees the pid.
    private func reap() {
        var info = siginfo_t()
        while waitid(P_PID, id_t(pid), &info, WEXITED | WNOWAIT) < 0 {
            if errno != EINTR { break }
        }
        lock.lock()
        reaped = true
        lock.unlock()

        var status: Int32 = 0
        var reapedStatus = true
        while waitpid(pid, &status, 0) < 0 {
            if errno != EINTR {
                reapedStatus = false
                break
            }
        }
        let signal = status & 0x7f
        // A wait failure (the pid vanished, e.g. someone else reaped it) must not look like a clean exit.
        let code: Int32 = !reapedStatus ? 255 : signal == 0 ? (status >> 8) & 0xff : 128 + signal
        lock.lock()
        exitCode = code
        let pending = waiters
        waiters = []
        lock.unlock()
        for waiter in pending { waiter.resume(returning: code) }
    }
}

// MARK: - Output draining

/// Serialises `onLine` calls from the two readers so lines never interleave mid-line.
private final class LineEmitter: @unchecked Sendable {  // `onLine` is only called under `lock`
    private let lock = NSLock()
    private let onLine: @Sendable (String) -> Void

    init(_ onLine: @escaping @Sendable (String) -> Void) { self.onLine = onLine }

    func emit(_ line: String) {
        lock.lock()
        defer { lock.unlock() }
        onLine(line)
    }
}

/// Drains one pipe on its own serial queue with a non-blocking read source until EOF.
private final class PipeReader: @unchecked Sendable {  // `collected` is guarded by `lock`; `pending` only touched on `queue`
    private let queue = DispatchQueue(label: "dev.sebastiangmz.diarchy.pipe-reader")
    private let group = DispatchGroup()
    private let lock = NSLock()
    private var collected = Data()
    private var pending = Data()  // bytes after the last newline; only touched on `queue`
    private let emitter: LineEmitter?
    private let source: any DispatchSourceRead

    init(descriptor: Int32, emitter: LineEmitter?) {
        self.emitter = emitter
        _ = fcntl(descriptor, F_SETFL, fcntl(descriptor, F_GETFL) | O_NONBLOCK)
        source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: queue)
        group.enter()
        // Strong captures: the reader -> source -> handler cycle is released by GCD once the source is cancelled,
        // and every path (EOF, error, `abandon()`) cancels it.
        source.setEventHandler { [self] in drain(descriptor) }
        source.setCancelHandler { [self] in
            flushPending()
            close(descriptor)
            group.leave()
        }
        source.resume()
    }

    /// Resolves once EOF was reached or the reader was abandoned.
    func finished() async {
        await withCheckedContinuation { continuation in
            group.notify(queue: .global()) { continuation.resume() }
        }
    }

    func abandon() { source.cancel() }

    var text: String {
        lock.lock()
        defer { lock.unlock() }
        return String(decoding: collected, as: UTF8.self)
    }

    private func drain(_ descriptor: Int32) {
        var buffer = [UInt8](repeating: 0, count: 65_536)
        let count = read(descriptor, &buffer, buffer.count)
        if count > 0 {
            let chunk = buffer[0..<count]
            lock.lock()
            collected.append(contentsOf: chunk)
            lock.unlock()
            if emitter != nil {
                pending.append(contentsOf: chunk)
                emitCompleteLines()
            }
        } else if count == 0 || (errno != EAGAIN && errno != EINTR) {
            source.cancel()
        }
    }

    private func emitCompleteLines() {
        while let newline = pending.firstIndex(of: 0x0A) {
            let line = pending[pending.startIndex..<newline]
            emit(line)
            pending.removeSubrange(pending.startIndex...newline)
        }
    }

    private func flushPending() {
        guard emitter != nil, !pending.isEmpty else { return }
        emit(pending[...])
        pending.removeAll()
    }

    private func emit(_ bytes: Data.SubSequence) {
        let trimmed = bytes.last == 0x0D ? bytes.dropLast() : bytes
        guard !trimmed.isEmpty else { return }
        emitter?.emit(String(decoding: trimmed, as: UTF8.self))
    }
}

/// Resolves `wait()` once `fire()` was called, however often and in whatever order.
final class OneShot: @unchecked Sendable {  // all state is guarded by `lock`
    private let lock = NSLock()
    private var fired = false
    private var continuation: CheckedContinuation<Void, Never>?

    func fire() {
        lock.lock()
        fired = true
        let waiting = continuation
        continuation = nil
        lock.unlock()
        waiting?.resume()
    }

    func wait() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            lock.lock()
            if fired {
                lock.unlock()
                continuation.resume()
            } else {
                self.continuation = continuation
                lock.unlock()
            }
        }
    }
}
