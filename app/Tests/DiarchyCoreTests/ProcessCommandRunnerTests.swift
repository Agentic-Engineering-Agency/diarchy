import Foundation
import Testing
@testable import DiarchyCore

/// Thread-safe line sink for `runStreaming`.
private final class LineBox: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var storage: [String] = []
    var lines: [String] { lock.lock(); defer { lock.unlock() }; return storage }
    func append(_ line: String) { lock.lock(); storage.append(line); lock.unlock() }
}

/// Records the group signals a `SpawnedProcess` sends and forwards them to the real `killpg`.
private final class SignalSpy: @unchecked Sendable {  // every access holds `lock`
    struct Sent: Equatable { var group: Int32; var signal: Int32 }
    private let lock = NSLock()
    private var storage: [Sent] = []
    var sent: [Sent] { lock.lock(); defer { lock.unlock() }; return storage }
    func send(_ group: Int32, _ signal: Int32) -> Int32 {
        lock.lock()
        storage.append(Sent(group: group, signal: signal))
        lock.unlock()
        return killpg(group, signal)
    }
}

private let runner = ProcessCommandRunner()

private func run(
    _ executable: String, _ arguments: [String] = [], environment: [String: String]? = nil,
    stdin: Data? = nil, timeout: TimeInterval = 10
) async throws -> CommandResult {
    try await runner.run(
        executable: executable, arguments: arguments, environment: environment, stdin: stdin, timeout: timeout)
}

private func processExists(_ pid: Int32) -> Bool { Darwin.kill(pid, 0) == 0 }

/// Waits (bounded) until `pid` is gone; a killed orphan is a zombie until launchd reaps it.
private func waitUntilGone(_ pid: Int32, within seconds: TimeInterval = 5) async -> Bool {
    let deadline = Date().addingTimeInterval(seconds)
    while processExists(pid) {
        if Date() > deadline { return false }
        try? await Task.sleep(for: .milliseconds(20))
    }
    return true
}

private func readPid(_ url: URL) throws -> Int32 {
    let text = try String(contentsOf: url, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
    return try #require(Int32(text))
}

@Suite struct ProcessCommandRunnerTests {
    @Test func capturesExitCodeStdoutAndStderrSeparately() async throws {
        let result = try await run("/bin/sh", ["-c", "echo out; echo err 1>&2; exit 3"])
        #expect(result == CommandResult(exitCode: 3, stdout: "out\n", stderr: "err\n"))
        #expect(!result.succeeded)
    }

    @Test func successfulCommandSucceeds() async throws {
        let result = try await run("/usr/bin/true")
        #expect(result.succeeded)
        #expect(result.stdout.isEmpty && result.stderr.isEmpty)
        #expect(try await run("/bin/echo", ["hello", "a b"]).stdout == "hello a b\n")
    }

    @Test func deliversStdin() async throws {
        let result = try await run("/bin/cat", stdin: Data("payload\nsecond".utf8))
        #expect(result.stdout == "payload\nsecond")
    }

    @Test func nilStdinReadsEndOfFileImmediately() async throws {
        let result = try await run("/bin/cat", timeout: 5)
        #expect(result == CommandResult(exitCode: 0))
    }

    @Test func largeStdinAndLargeOutputOnBothStreamsDoNotDeadlock() async throws {
        // Review Focus 1: > 64 KiB on stdout while stderr also fills must not block the child.
        let script =
            #"head -c 300000 /dev/zero | tr "\0" a; head -c 300000 /dev/zero | tr "\0" b 1>&2"#
        let started = Date()
        let result = try await run("/bin/sh", ["-c", script], timeout: 10)
        #expect(result.exitCode == 0)
        #expect(result.stdout.utf8.count == 300_000)
        #expect(result.stderr.utf8.count == 300_000)
        #expect(result.stdout.allSatisfy { $0 == "a" })
        #expect(result.stderr.allSatisfy { $0 == "b" })
        #expect(Date().timeIntervalSince(started) < 10)
    }

    @Test func interleavedLargeOutputOnBothStreamsDoesNotDeadlock() async throws {
        let script = #"""
            i=0
            while [ $i -lt 400 ]; do
              head -c 1000 /dev/zero | tr "\0" a
              head -c 1000 /dev/zero | tr "\0" b 1>&2
              i=$((i+1))
            done
            """#
        let result = try await run("/bin/sh", ["-c", script], timeout: 20)
        #expect(result.stdout.utf8.count == 400_000)
        #expect(result.stderr.utf8.count == 400_000)
    }

    @Test func largeStdinIsWrittenWhileOutputIsDrained() async throws {
        let big = Data(repeating: 0x78, count: 500_000)
        let result = try await run("/bin/cat", stdin: big, timeout: 10)
        #expect(result.stdout.utf8.count == 500_000)
    }

    @Test func leaderExitWithGrandchildHoldingPipesReturnsNormallyAndSparesTheGrandchild() async throws {
        try await withTempDirAsync { root in
            let pidFile = root.appendingPathComponent("grandchild.pid")
            let started = Date()
            // The background sleep inherits stdout/stderr, so the pipes never reach EOF while it lives.
            let result = try await run(
                "/bin/sh", ["-c", "/bin/sleep 30 & echo $! > '\(pidFile.path)'; echo hi"], timeout: 5)
            let elapsed = Date().timeIntervalSince(started)
            let grandchild = try readPid(pidFile)
            defer { Darwin.kill(grandchild, SIGKILL) }
            #expect(result == CommandResult(exitCode: 0, stdout: "hi\n"))
            #expect(elapsed < 4)
            #expect(processExists(grandchild), "a daemon left behind by the command must not be killed")
        }
    }

    @Test func runStreamingWithGrandchildHoldingPipesAlsoReturnsNormally() async throws {
        try await withTempDirAsync { root in
            let pidFile = root.appendingPathComponent("grandchild.pid")
            let box = LineBox()
            let started = Date()
            let result = try await runner.runStreaming(
                executable: "/bin/sh",
                arguments: ["-c", "/bin/sleep 30 & echo $! > '\(pidFile.path)'; echo hi; echo oops 1>&2"],
                environment: nil, stdin: nil, timeout: 5, onLine: { box.append($0) })
            let elapsed = Date().timeIntervalSince(started)
            let grandchild = try readPid(pidFile)
            defer { Darwin.kill(grandchild, SIGKILL) }
            #expect(result == CommandResult(exitCode: 0, stdout: "hi\n", stderr: "oops\n"))
            #expect(box.lines.sorted() == ["hi", "oops"])
            #expect(elapsed < 4)
            #expect(processExists(grandchild))
        }
    }

    @Test func outputWrittenRightBeforeLeaderExitIsStillCollectedWhenAGrandchildHoldsThePipes() async throws {
        try await withTempDirAsync { root in
            let pidFile = root.appendingPathComponent("grandchild.pid")
            let script = "/bin/sleep 30 & echo $! > '\(pidFile.path)'; head -c 200000 /dev/zero | tr '\\0' a"
            let result = try await run("/bin/sh", ["-c", script], timeout: 5)
            defer { if let pid = try? readPid(pidFile) { Darwin.kill(pid, SIGKILL) } }
            #expect(result.stdout.utf8.count == 200_000)
        }
    }

    @Test func aSpawnedChildKeepsItsLastStderrLine() async throws {
        let child = try ProcessCommandRunner().spawn(
            executable: "/bin/sh", arguments: ["-c", "echo first >&2; echo 'second line' >&2; exit 3"],
            environment: nil)
        #expect(await child.waitForExit() == 3)
        for _ in 0..<100 where child.lastErrorLine != "second line" { try await Task.sleep(for: .milliseconds(20)) }
        #expect(child.lastErrorLine == "second line")
    }

    @Test func publicSignalsAreNoOpsOnceTheLeaderWasReaped() async throws {
        // A reaped leader's pid (and so the pgid) may be recycled; signalling it could hit an unrelated group.
        let spy = SignalSpy()
        let pid = try PosixSpawn.start(
            executable: "/usr/bin/true", arguments: [], environment: nil, stdin: nil, stdout: nil, stderr: nil)
        let child = SpawnedProcess(pid: pid, signalGroup: spy.send)
        #expect(await child.waitForExit() == 0)
        child.terminate()
        child.kill()
        #expect(spy.sent.isEmpty)
    }

    @Test func publicSignalsReachTheGroupWhileTheLeaderLives() async throws {
        let spy = SignalSpy()
        let pid = try PosixSpawn.start(
            executable: "/bin/sleep", arguments: ["30"], environment: nil, stdin: nil, stdout: nil, stderr: nil)
        let child = SpawnedProcess(pid: pid, signalGroup: spy.send)
        child.terminate()
        #expect(await child.waitForExit() == 128 + SIGTERM)
        #expect(spy.sent == [SignalSpy.Sent(group: pid, signal: SIGTERM)])
    }

    @Test func timeoutThrowsTimedOutAndReapsTheProcess() async throws {
        try await withTempDirAsync { root in
            let pidFile = root.appendingPathComponent("pid")
            let started = Date()
            await #expect(throws: CommandError.timedOut(command: "sh")) {
                try await run(
                    "/bin/sh", ["-c", "echo $$ > '\(pidFile.path)'; exec /bin/sleep 30"], timeout: 0.5)
            }
            #expect(Date().timeIntervalSince(started) < 3)
            let pid = try readPid(pidFile)
            #expect(!processExists(pid))
        }
    }

    @Test func timedOutCommandNameIsTheExecutableBasename() async throws {
        await #expect(throws: CommandError.timedOut(command: "sleep")) {
            try await run("/bin/sleep", ["30"], timeout: 0.3)
        }
    }

    @Test func timeoutKillsTheWholeProcessGroup() async throws {
        try await withTempDirAsync { root in
            let pidFile = root.appendingPathComponent("grandchild.pid")
            let script = "/bin/sleep 30 & echo $! > '\(pidFile.path)'; wait"
            await #expect(throws: CommandError.timedOut(command: "sh")) {
                try await run("/bin/sh", ["-c", script], timeout: 1)
            }
            let grandchild = try readPid(pidFile)
            #expect(await waitUntilGone(grandchild))
        }
    }

    @Test func timeoutEscalatesToKillWhenTermIsIgnored() async throws {
        let started = Date()
        await #expect(throws: CommandError.timedOut(command: "sh")) {
            try await run("/bin/sh", ["-c", #"trap "" TERM; /bin/sleep 30; /bin/sleep 30"#], timeout: 0.3)
        }
        #expect(Date().timeIntervalSince(started) < 4)
    }

    @Test func missingExecutableThrowsLaunchFailed() async throws {
        do {
            _ = try await run("/nonexistent/definitely-not-here")
            Issue.record("expected launchFailed")
        } catch let error as CommandError {
            guard case .launchFailed(let command, let message) = error else {
                Issue.record("unexpected \(error)")
                return
            }
            #expect(command.hasSuffix("definitely-not-here"))
            #expect(!message.isEmpty)
        }
    }

    @Test func nonNilEnvironmentReplacesTheProcessEnvironment() async throws {
        let result = try await run("/usr/bin/env", environment: ["DIARCHY_TEST_ONLY": "1"])
        #expect(result.stdout == "DIARCHY_TEST_ONLY=1\n")
    }

    @Test func nilEnvironmentInheritsTheProcessEnvironment() async throws {
        let result = try await run("/usr/bin/env")
        #expect(result.stdout.contains("PATH="))
    }

    @Test func runStreamingDeliversEachLineOnceInArrivalOrder() async throws {
        let box = LineBox()
        let script = "echo one; sleep 0.2; echo two 1>&2; sleep 0.2; echo three; printf tail"
        let result = try await runner.runStreaming(
            executable: "/bin/sh", arguments: ["-c", script], environment: nil, stdin: nil, timeout: 10,
            onLine: { box.append($0) })
        #expect(box.lines == ["one", "two", "three", "tail"])
        #expect(result.stdout == "one\nthree\ntail")
        #expect(result.stderr == "two\n")
        #expect(result.exitCode == 0)
    }

    @Test func runStreamingStripsCarriageReturnsAndKeepsEmptyLinesOut() async throws {
        let box = LineBox()
        _ = try await runner.runStreaming(
            executable: "/usr/bin/printf", arguments: ["a\\r\\nb\\n"], environment: nil, stdin: nil,
            timeout: 10, onLine: { box.append($0) })
        #expect(box.lines == ["a", "b"])
    }

    @Test func runStreamingTimesOut() async throws {
        let box = LineBox()
        await #expect(throws: CommandError.timedOut(command: "sh")) {
            try await runner.runStreaming(
                executable: "/bin/sh", arguments: ["-c", "echo started; sleep 30"], environment: nil,
                stdin: nil, timeout: 0.5, onLine: { box.append($0) })
        }
        #expect(box.lines == ["started"])
    }

    @Test func spawnThenTerminateExitsWith143() async throws {
        let child = try runner.spawn(executable: "/bin/sleep", arguments: ["30"], environment: nil)
        #expect(processExists(child.pid))
        child.terminate()
        #expect(await child.waitForExit() == 143)
        #expect(!processExists(child.pid))
    }

    @Test func waitForExitReturnsExitCodeAndIsRepeatable() async throws {
        let child = try runner.spawn(executable: "/bin/sh", arguments: ["-c", "exit 7"], environment: nil)
        #expect(await child.waitForExit() == 7)
        #expect(await child.waitForExit() == 7)
    }

    @Test func hasExitedFlipsOnceTheChildIsReaped() async throws {
        let child = try runner.spawn(executable: "/bin/sleep", arguments: ["30"], environment: nil)
        #expect(!child.hasExited)
        child.terminate()
        _ = await child.waitForExit()
        #expect(child.hasExited)
    }

    @Test func killEndsAChildThatTrapsTerm() async throws {
        try await withTempDirAsync { root in
            let readyFile = root.appendingPathComponent("ready")
            let child = try runner.spawn(
                executable: "/bin/sh",
                arguments: ["-c", #"trap "" TERM; echo ready > '\#(readyFile.path)'; sleep 30"#],
                environment: nil)
            for _ in 0..<500 where !FileManager.default.fileExists(atPath: readyFile.path) {
                try await Task.sleep(for: .milliseconds(10))
            }
            child.terminate()
            try await Task.sleep(for: .milliseconds(100))
            #expect(processExists(child.pid), "SIGTERM must be ignored by this child")
            child.kill()
            #expect(await child.waitForExit() == 128 + SIGKILL)
            #expect(!processExists(child.pid))
        }
    }

    @Test func spawnedChildRunsInItsOwnProcessGroup() async throws {
        let child = try runner.spawn(executable: "/bin/sleep", arguments: ["30"], environment: nil)
        defer { child.kill() }
        #expect(getpgid(child.pid) == child.pid)
        #expect(getpgid(child.pid) != getpgrp())
    }

    @Test func killTakesDownGrandchildrenOfASpawnedChild() async throws {
        try await withTempDirAsync { root in
            let pidFile = root.appendingPathComponent("grandchild.pid")
            let child = try runner.spawn(
                executable: "/bin/sh", arguments: ["-c", "/bin/sleep 30 & echo $! > '\(pidFile.path)'; wait"],
                environment: nil)
            // The pid file appears once the shell has forked.
            for _ in 0..<200 where !FileManager.default.fileExists(atPath: pidFile.path) {
                try await Task.sleep(for: .milliseconds(10))
            }
            let grandchild = try readPid(pidFile)
            child.kill()
            _ = await child.waitForExit()
            #expect(await waitUntilGone(grandchild))
        }
    }

    @Test func spawnMissingExecutableThrowsLaunchFailed() async throws {
        #expect(throws: CommandError.self) {
            _ = try runner.spawn(executable: "/nonexistent/definitely-not-here", arguments: [], environment: nil)
        }
    }

    @Test func commandResultFirstErrorLine() {
        #expect(CommandResult(exitCode: 1, stdout: "o\n", stderr: "\n  \nboom\nlater").firstErrorLine == "boom")
        #expect(CommandResult(exitCode: 1, stdout: "\nout line\nmore", stderr: " \n").firstErrorLine == "out line")
        #expect(CommandResult(exitCode: 9).firstErrorLine == "exit code 9")
        // Lines of only "@" and whitespace carry no message; a banner line loses its "@" framing.
        #expect(CommandResult(exitCode: 255, stderr: "@@@@@@@@\n@   \n@@@\nreal reason\n").firstErrorLine == "real reason")
        #expect(
            CommandResult(exitCode: 255, stderr: "@@@@@@@@@@\n@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n@@@@@@@@@@\n")
                .firstErrorLine == "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!")
        #expect(CommandResult(exitCode: 255, stdout: "from stdout\n", stderr: "@@@@\n @ @ \n").firstErrorLine == "from stdout")
        #expect(CommandResult(exitCode: 3, stderr: "@@@@\n").firstErrorLine == "exit code 3")
    }
}
