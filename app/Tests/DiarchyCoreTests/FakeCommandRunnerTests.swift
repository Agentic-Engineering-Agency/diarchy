import Foundation
import Testing
@testable import DiarchyCore

@Suite struct FakeCommandRunnerTests {
    private func run(
        _ fake: FakeCommandRunner, _ executable: String, _ arguments: [String] = [], timeout: TimeInterval = 1
    ) async throws -> CommandResult {
        try await fake.run(
            executable: executable, arguments: arguments, environment: nil, stdin: nil, timeout: timeout)
    }

    @Test func recordsCallsAndReturnsTheMatchingRule() async throws {
        let fake = FakeCommandRunner()
        fake.when("diarchy-kvm", ["status"], return: .success(CommandResult(exitCode: 0, stdout: "ok\n")))
        let result = try await run(fake, "/h/.local/bin/diarchy-kvm", ["status"])
        #expect(result.stdout == "ok\n")
        #expect(fake.calls.map(\.arguments) == [["status"]])
    }

    @Test func recordsTheTimeoutOfRunAndRunStreaming() async throws {
        let fake = FakeCommandRunner()
        fake.when({ _ in true }, return: .success(CommandResult(exitCode: 0)))
        _ = try await run(fake, "/bin/a", timeout: 15)
        _ = try await fake.runStreaming(
            executable: "/bin/b", arguments: [], environment: nil, stdin: nil, timeout: 900, onLine: { _ in })
        #expect(fake.calls.map(\.timeout) == [15, 900])
    }

    @Test func firstMatchingRuleWins() async throws {
        let fake = FakeCommandRunner()
        fake.when({ $0.arguments == ["x"] }, return: .success(CommandResult(exitCode: 1)))
        fake.when({ _ in true }, return: .success(CommandResult(exitCode: 2)))
        #expect(try await run(fake, "/bin/a", ["x"]).exitCode == 1)
        #expect(try await run(fake, "/bin/a", ["y"]).exitCode == 2)
    }

    @Test func unmatchedCallThrowsNamingTheCall() async throws {
        let fake = FakeCommandRunner()
        let error = await #expect(throws: UnmatchedCommandError.self) { try await run(fake, "/bin/a", ["x"]) }
        #expect(error?.description.contains("/bin/a x") == true)
        #expect(fake.calls.count == 1)
    }

    @Test func rulesCanFailWithACommandError() async throws {
        let fake = FakeCommandRunner()
        fake.when({ _ in true }, return: .failure(.timedOut(command: "ssh")))
        await #expect(throws: CommandError.timedOut(command: "ssh")) { try await run(fake, "/bin/ssh") }
    }

    @Test func runStreamingFeedsStdoutThenStderrLines() async throws {
        let fake = FakeCommandRunner()
        fake.when(
            { _ in true }, return: .success(CommandResult(exitCode: 0, stdout: "a\nb\n", stderr: "c\n")))
        let lines = LockedLines()
        let result = try await fake.runStreaming(
            executable: "/bin/x", arguments: [], environment: nil, stdin: nil, timeout: 1,
            onLine: { lines.append($0) })
        #expect(lines.value == ["a", "b", "c"])
        #expect(result.stdout == "a\nb\n")
    }

    // MARK: spawn

    @Test func spawnWithoutARuleThrowsNamingTheCommand() throws {
        let fake = FakeCommandRunner()
        let error = #expect(throws: UnmatchedCommandError.self) {
            _ = try fake.spawn(executable: "/h/.local/bin/diarchy-stream", arguments: ["--x"], environment: nil)
        }
        #expect(error?.description.contains("spawn /h/.local/bin/diarchy-stream --x") == true)
        #expect(fake.spawns.count == 1, "an unmatched spawn is still recorded")
    }

    @Test func spawnReturnsTheChildOfTheMatchingRuleAndRecordsTheCall() async throws {
        let fake = FakeCommandRunner()
        let child = FakeChildProcess(pid: 77)
        fake.whenSpawn("diarchy-stream", returns: child)
        let spawned = try fake.spawn(
            executable: "/h/.local/bin/diarchy-stream", arguments: ["a b"], environment: ["K": "V"])
        #expect(spawned.pid == 77)
        #expect(
            fake.spawns == [
                FakeCommandRunner.SpawnCall(
                    executable: "/h/.local/bin/diarchy-stream", arguments: ["a b"], environment: ["K": "V"])
            ])
        #expect(fake.calls.isEmpty, "spawns are not run() calls")
        child.terminate()
        #expect(await spawned.waitForExit() == 143)
        #expect(child.terminated && !child.killed)
    }

    @Test func spawnRulesMatchByNameAndArgumentsAndFirstWins() throws {
        let fake = FakeCommandRunner()
        let first = FakeChildProcess(pid: 1)
        let second = FakeChildProcess(pid: 2)
        fake.whenSpawn("m", ["--a"], returns: first)
        fake.whenSpawn({ $0.arguments.isEmpty }, returns: second)
        #expect(try fake.spawn(executable: "/bin/m", arguments: ["--a"], environment: nil).pid == 1)
        #expect(try fake.spawn(executable: "/bin/m", arguments: [], environment: nil).pid == 2)
        #expect(throws: UnmatchedCommandError.self) {
            _ = try fake.spawn(executable: "/bin/m", arguments: ["--b"], environment: nil)
        }
    }

    @Test func spawnRuleCanFail() throws {
        let fake = FakeCommandRunner()
        fake.whenSpawn("m", fails: .launchFailed(command: "m", message: "nope"))
        #expect(throws: CommandError.launchFailed(command: "m", message: "nope")) {
            _ = try fake.spawn(executable: "/bin/m", arguments: [], environment: nil)
        }
    }

    @Test func spawnedChildCanIgnoreTerminateUntilKilled() async throws {
        let fake = FakeCommandRunner()
        let child = FakeChildProcess(exitsOnTerminate: false)
        fake.whenSpawn("m", returns: child)
        let spawned = try fake.spawn(executable: "/bin/m", arguments: [], environment: nil)
        spawned.terminate()
        #expect(!child.hasExited)
        spawned.kill()
        #expect(await spawned.waitForExit() == 137)
        #expect(child.killed)
    }

    @Test func manualExitResolvesWaiters() async throws {
        let child = FakeChildProcess()
        async let code = child.waitForExit()
        child.exit(code: 5)
        #expect(await code == 5)
        #expect(await child.waitForExit() == 5)
    }
}

private final class LockedLines: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var storage: [String] = []
    var value: [String] { lock.lock(); defer { lock.unlock() }; return storage }
    func append(_ line: String) { lock.lock(); storage.append(line); lock.unlock() }
}
