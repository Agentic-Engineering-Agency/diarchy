import Foundation
import Testing
@testable import DiarchyCore

/// Records every duration the injected `sleep` was asked for and can run a side effect after the n-th call.
private final class SleepRecorder: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var durations: [Duration] = []

    var requested: [Duration] { lock.lock(); defer { lock.unlock() }; return durations }
    var total: Duration { requested.reduce(.zero, +) }

    /// Returns the call count after recording.
    func record(_ duration: Duration) -> Int {
        lock.lock()
        defer { lock.unlock() }
        durations.append(duration)
        return durations.count
    }
}

private func ok(_ stdout: String = "", stderr: String = "", exit: Int32 = 0) -> Result<CommandResult, CommandError> {
    .success(CommandResult(exitCode: exit, stdout: stdout, stderr: stderr))
}

private let agentPath = "~/.local/bin/diarchy-host"

@Suite struct PairingTests {
    private static let macFingerprint = Fixtures.fingerprintBB
    private static let missingCertificate = "diarchy-mac-fingerprint: Lan Mouse certificate not found; start Lan Mouse once"

    private static func initialConfig() -> DiarchyConfig {
        var config = DiarchyConfig(sshUser: "user", lanHost: "192.0.2.10", tailscaleHost: "100.64.0.10")
        config.sshPort = 2222
        config.kvmSide = .right
        config.hostFingerprint = Fixtures.fingerprintCC
        config.streamFPS = 30
        return config
    }

    struct Scenario {
        var config: DiarchyConfig? = PairingTests.initialConfig()
        /// The certificate file exists when the run starts.
        var certificateExists = true
        /// The fake `open` creates the certificate (as Lan Mouse does on its first start).
        var openCreatesCertificate = true
        var openResult = ok()
        /// The injected sleep creates the certificate on its n-th call.
        var certificateAfterSleeps: Int?
        var fingerprintOutput = PairingTests.macFingerprint + "\n"
        var authorize = ok(Fixtures.kvmAuthorize + "\n")
        var probe = ok(Fixtures.probe(actions: []) + "\n")
        var configure = ok(Fixtures.kvmConfigure + "\n")
        var configureOverride: Result<CommandResult, CommandError>?
        var kvmOff = ok("Diarchy KVM: disabled\n")
        var hostKeyStatus = ok("status=trusted\nfingerprint=SHA256:" + String(repeating: "A", count: 43) + "\n")
    }

    fileprivate struct Outcome {
        var result: Result<DiarchyConfig, PairingError>
        var runner: FakeCommandRunner
        var sleeps: SleepRecorder
        var stored: DiarchyConfig?
        var storedBytes: Data?
        var lines: [String]
    }

    fileprivate func run(_ scenario: Scenario = Scenario()) async throws -> Outcome {
        try await withTempDirAsync { home in
            let paths = Paths(home: home, payload: nil)
            let store = ConfigStore(url: home.appendingPathComponent(".config/diarchy/diarchy.conf"))
            if let config = scenario.config { try store.save(config) }
            let certificate = paths.lanMouseCertificate
            let createCertificate: @Sendable () -> Void = {
                try? FileManager.default.createDirectory(
                    at: certificate.deletingLastPathComponent(), withIntermediateDirectories: true)
                try? "pem".write(to: certificate, atomically: true, encoding: .utf8)
            }
            if scenario.certificateExists { createCertificate() }

            let runner = FakeCommandRunner()
            let sleeps = SleepRecorder()
            let controllerIP = ["--controller-ip", "192.0.2.55"]
            let openArguments = ["-gj", paths.lanMouseApp.path]
            let creates = scenario.openCreatesCertificate
            runner.when(
                {
                    guard ($0.executable as NSString).lastPathComponent == "open", $0.arguments == openArguments
                    else { return false }
                    if creates { createCertificate() }
                    return true
                }, return: scenario.openResult)
            runner.when(
                {
                    ($0.executable as NSString).lastPathComponent == "diarchy-mac-fingerprint"
                        && !FileManager.default.fileExists(atPath: certificate.path)
                }, return: ok(stderr: Self.missingCertificate + "\n", exit: 1))
            runner.when("diarchy-mac-fingerprint", [], return: ok(scenario.fingerprintOutput))
            runner.when(
                "diarchy-ssh",
                ["--", agentPath, "kvm", "authorize", "--fingerprint", Self.macFingerprint], return: scenario.authorize)
            runner.when(
                "diarchy-ssh", ["--", agentPath, "kvm", "authorize", "--fingerprint", Self.macFingerprint.lowercased()],
                return: scenario.authorize)
            runner.when("route", ["-n", "get", "192.0.2.10"], return: ok("  interface: en0\n"))
            runner.when("ipconfig", ["getifaddr", "en0"], return: ok("192.0.2.55\n"))
            runner.when("diarchy-ssh", ["--", agentPath, "probe"] + controllerIP, return: scenario.probe)
            runner.when("diarchy-kvm-configure", [], return: scenario.configureOverride ?? scenario.configure)
            runner.when("diarchy-kvm", ["off"], return: scenario.kvmOff)
            runner.when("diarchy-host-key", ["status"], return: scenario.hostKeyStatus)

            let after = scenario.certificateAfterSleeps
            let pairing = Pairing(
                paths: paths, store: store, runner: runner,
                agent: HostAgentClient(runner: runner, paths: paths),
                sleep: { duration in
                    let count = sleeps.record(duration)
                    if let after, count == after { createCertificate() }
                })

            let result: Result<DiarchyConfig, PairingError>
            do throws(PairingError) {
                result = .success(try await pairing.pair())
            } catch {
                result = .failure(error)
            }
            // The host key check runs first on every pairing; the tests below look at what follows it.
            let lines = runner.calls.filter { ($0.executable as NSString).lastPathComponent != "diarchy-host-key" }.map { call in
                ([(call.executable as NSString).lastPathComponent] + call.arguments).joined(separator: " ")
            }
            return Outcome(
                result: result, runner: runner, sleeps: sleeps, stored: try? store.load(),
                storedBytes: try? Data(contentsOf: store.url), lines: lines)
        }
    }

    private func expectedHostConfig() -> DiarchyConfig {
        var expected = Self.initialConfig()
        expected.hostFingerprint = Fixtures.fingerprintAA  // the probe fixture's host fingerprint
        return expected
    }

    private func initialBytes() throws -> Data {
        try withTempDir { dir in
            let store = ConfigStore(url: dir.appendingPathComponent("diarchy.conf"))
            try store.save(Self.initialConfig())
            return try Data(contentsOf: store.url)
        }
    }

    // MARK: Green path

    @Test func greenPathRunsTheStepsInOrderAndSavesTheHostFingerprint() async throws {
        let outcome = try await run()
        #expect(try outcome.result.get() == expectedHostConfig())
        #expect(outcome.stored == expectedHostConfig())
        #expect(
            outcome.lines == [
                "diarchy-mac-fingerprint",
                "diarchy-ssh -- \(agentPath) kvm authorize --fingerprint \(Self.macFingerprint)",
                "route -n get 192.0.2.10",
                "ipconfig getifaddr en0",
                "diarchy-ssh -- \(agentPath) probe --controller-ip 192.0.2.55",
                "diarchy-kvm-configure",
                "diarchy-kvm off",
            ])
        #expect(outcome.sleeps.requested.isEmpty)
        // Local commands use the 20 s limit.
        let local = outcome.runner.calls.filter {
            ["diarchy-mac-fingerprint", "diarchy-kvm-configure", "diarchy-kvm"].contains(
                ($0.executable as NSString).lastPathComponent)
        }
        #expect(local.map(\.timeout) == [20, 20, 20])
    }

    @Test func fingerprintOutputIsTrimmedAndUppercasedBeforeTheRemoteCall() async throws {
        var scenario = Scenario()
        scenario.fingerprintOutput = "  \(Self.macFingerprint.lowercased())\r\n\n"
        let outcome = try await run(scenario)
        #expect(try outcome.result.get() == expectedHostConfig())
        #expect(outcome.lines[1].hasSuffix("--fingerprint \(Self.macFingerprint)"))
    }

    @Test(arguments: ["", "not a fingerprint\n", "AA:BB", Fixtures.fingerprintAA + ":AA", "ZZ" + Fixtures.fingerprintAA.dropFirst(2)])
    func malformedFingerprintIsRejectedBeforeAnyRemoteCall(output: String) async throws {
        var scenario = Scenario()
        scenario.fingerprintOutput = output
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.unexpectedFingerprintOutput))
        #expect(outcome.lines == ["diarchy-mac-fingerprint"])
        #expect(outcome.sleeps.requested.isEmpty)
        #expect(outcome.storedBytes == (try initialBytes()))
    }

    @Test func unexpectedFingerprintOutputHasItsOwnMessage() {
        let message = PairingError.unexpectedFingerprintOutput.userMessage
        #expect(message == "diarchy-mac-fingerprint returned an unexpected value")
        #expect(message != PairingError.noLocalCertificate.userMessage)
    }

    // MARK: Waiting for the certificate

    @Test func missingCertificateIsCreatedByOpenThenRetried() async throws {
        var scenario = Scenario()
        scenario.certificateExists = false
        let outcome = try await run(scenario)
        #expect(try outcome.result.get() == expectedHostConfig())
        let openLine = outcome.lines.first { $0.hasPrefix("open ") }
        #expect(openLine?.hasPrefix("open -gj ") == true)
        #expect(openLine?.hasSuffix("Applications/Lan Mouse.app") == true)
        #expect(outcome.lines.prefix(3).map { $0.split(separator: " ").first.map(String.init) ?? "" }
            == ["diarchy-mac-fingerprint", "open", "diarchy-mac-fingerprint"])
        #expect(outcome.sleeps.requested.isEmpty)  // the certificate already exists when the wait starts
    }

    @Test func certificateThatAppearsLaterIsWaitedForInHalfSecondSteps() async throws {
        var scenario = Scenario()
        scenario.certificateExists = false
        scenario.openCreatesCertificate = false
        scenario.certificateAfterSleeps = 3
        let outcome = try await run(scenario)
        #expect(try outcome.result.get() == expectedHostConfig())
        #expect(outcome.sleeps.requested == Array(repeating: .milliseconds(500), count: 3))
        #expect(outcome.sleeps.total <= .seconds(10))
    }

    @Test func certificateThatNeverAppearsFailsAfterAtMostTenSecondsAndMakesNoAgentCall() async throws {
        var scenario = Scenario()
        scenario.certificateExists = false
        scenario.openCreatesCertificate = false
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.noLocalCertificate))
        #expect(!outcome.sleeps.requested.isEmpty)
        #expect(outcome.sleeps.requested.allSatisfy { $0 == .milliseconds(500) })
        #expect(outcome.sleeps.total <= .seconds(10))
        // fingerprint, open, fingerprint (the single retry) and nothing else.
        #expect(outcome.lines.map { $0.split(separator: " ").first.map(String.init) ?? "" }
            == ["diarchy-mac-fingerprint", "open", "diarchy-mac-fingerprint"])
        #expect(outcome.storedBytes == (try initialBytes()))
    }

    @Test func failingOpenFailsWithoutWaiting() async throws {
        var scenario = Scenario()
        scenario.certificateExists = false
        scenario.openCreatesCertificate = false
        scenario.openResult = ok(stderr: "The application cannot be opened\n", exit: 1)
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.noLocalCertificate))
        #expect(outcome.sleeps.requested.isEmpty)
        #expect(!outcome.lines.contains { $0.hasPrefix("diarchy-ssh") })
    }

    // MARK: Agent failures

    @Test func authorizeRejectionIsAnAgentErrorAndLeavesTheConfigAlone() async throws {
        var scenario = Scenario()
        scenario.authorize = ok(
            Fixtures.failure(code: "invalid_fingerprint", message: "fingerprint must be 32 uppercase hex pairs",
                command: "kvm authorize") + "\n", exit: 1)
        let outcome = try await run(scenario)
        #expect(
            outcome.result
                == .failure(
                    .agent(.agent(code: .invalidFingerprint, message: "fingerprint must be 32 uppercase hex pairs"))))
        #expect(outcome.storedBytes == (try initialBytes()))
        #expect(!outcome.lines.contains { $0.hasSuffix("diarchy-kvm-configure") || $0.contains("probe") })
    }

    @Test func authorizeTransportFailureIsAnAgentErrorAndLeavesTheConfigAlone() async throws {
        var scenario = Scenario()
        scenario.authorize = ok(stderr: "ssh: connect to host 192.0.2.10 port 22: Connection refused\n", exit: 255)
        let outcome = try await run(scenario)
        #expect(
            outcome.result
                == .failure(.agent(.transport("ssh: connect to host 192.0.2.10 port 22: Connection refused"))))
        #expect(outcome.storedBytes == (try initialBytes()))
    }

    @Test func probeFailureIsAnAgentErrorAndLeavesTheConfigAlone() async throws {
        var scenario = Scenario()
        scenario.probe = ok(stderr: "ssh: Connection timed out\n", exit: 255)
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.agent(.transport("ssh: Connection timed out"))))
        #expect(outcome.storedBytes == (try initialBytes()))
        #expect(outcome.lines.last?.contains("probe") == true)
    }

    @Test func probeWithoutHostFingerprintIsHostHasNoCertificate() async throws {
        var scenario = Scenario()
        scenario.probe = ok(Fixtures.probeUnsupported + "\n")
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.hostHasNoCertificate))
        #expect(outcome.storedBytes == (try initialBytes()))
        #expect(!outcome.lines.contains("diarchy-kvm-configure"))
    }

    // MARK: Local configuration

    @Test func configureFailureKeepsTheSavedHostFingerprint() async throws {
        var scenario = Scenario()
        scenario.configure = ok(stderr: "diarchy-kvm-configure: cannot write config.toml\nsecond line\n", exit: 1)
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.configurationFailed("diarchy-kvm-configure: cannot write config.toml")))
        #expect(outcome.stored == expectedHostConfig())
    }

    @Test func configureLaunchFailureIsReported() async throws {
        var scenario = Scenario()
        scenario.configureOverride = .failure(.launchFailed(command: "diarchy-kvm-configure", message: "No such file"))
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.configurationFailed("No such file")))
        #expect(outcome.stored == expectedHostConfig())
    }

    @Test func configureTimeoutIsReported() async throws {
        var scenario = Scenario()
        scenario.configureOverride = .failure(.timedOut(command: "diarchy-kvm-configure"))
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.configurationFailed("diarchy-kvm-configure did not respond in time")))
        #expect(outcome.stored == expectedHostConfig())
        #expect(!outcome.lines.contains("diarchy-kvm off"))
    }

    // MARK: Restarting Lan Mouse

    @Test func configureFailureDoesNotStopLanMouse() async throws {
        var scenario = Scenario()
        scenario.configure = ok(stderr: "diarchy-kvm-configure: cannot write config.toml\n", exit: 1)
        let outcome = try await run(scenario)
        #expect(!outcome.lines.contains("diarchy-kvm off"))
    }

    @Test(arguments: [
        ok(stderr: "Diarchy KVM: cannot stop\n", exit: 1),
        Result<CommandResult, CommandError>.failure(.timedOut(command: "diarchy-kvm")),
        Result<CommandResult, CommandError>.failure(.launchFailed(command: "diarchy-kvm", message: "No such file")),
    ])
    func failingOffStillReturnsTheSavedConfig(off: Result<CommandResult, CommandError>) async throws {
        var scenario = Scenario()
        scenario.kvmOff = off
        let outcome = try await run(scenario)
        #expect(try outcome.result.get() == expectedHostConfig())
        #expect(outcome.stored == expectedHostConfig())
        #expect(outcome.lines.last == "diarchy-kvm off")
    }

    @Test func offRunsLocallyWithTheTwentySecondLimit() async throws {
        let outcome = try await run()
        let off = outcome.runner.calls.last
        #expect(off.map { ($0.executable as NSString).lastPathComponent } == "diarchy-kvm")
        #expect(off?.arguments == ["off"])
        #expect(off?.timeout == 20)
    }

    @Test func missingConfigurationFailsBeforeAnyCommand() async throws {
        var scenario = Scenario()
        scenario.config = nil
        let outcome = try await run(scenario)
        #expect(
            outcome.result
                == .failure(.config(ConfigError(reason: "Diarchy is not configured (open the app to set it up)"))))
        #expect(outcome.lines.isEmpty)
    }

    // MARK: Host key gate (SEC-04)

    @Test(arguments: [
        ok("status=missing\n"),
        ok("status=failed\nreason=boom\n", exit: 1),
        ok("", stderr: "no such file\n", exit: 127),
        ok("status=trusted\n"),
    ])
    func pairingStopsBeforeAnyOtherCommandWhenTheHostKeyIsNotTrusted(
        status: Result<CommandResult, CommandError>
    ) async throws {
        var scenario = Scenario()
        scenario.hostKeyStatus = status
        let outcome = try await run(scenario)
        #expect(outcome.result == .failure(.hostKeyNotConfirmed))
        #expect(outcome.runner.calls.map { ($0.executable as NSString).lastPathComponent } == ["diarchy-host-key"])
        #expect(outcome.runner.calls.first?.arguments == ["status"])
        #expect(outcome.storedBytes == (try initialBytes()))
    }

    // MARK: Messages

    @Test func userMessages() {
        #expect(PairingError.noLocalCertificate.userMessage.contains("Lan Mouse"))
        #expect(PairingError.hostHasNoCertificate.userMessage.contains("host"))
        #expect(PairingError.agent(.transport("boom")).userMessage == "boom")
        #expect(PairingError.configurationFailed("bad").userMessage == "bad")
        #expect(
            PairingError.config(ConfigError(reason: "no good")).userMessage == "diarchy-config: no good")
    }
}

// MARK: - Moonlight

/// Delegates to a `FakeCommandRunner` and keeps one ordered log of the lifecycle: `stream start`, `route`
/// (`diarchy-stream --dry-run`), `list` (`Moonlight list`), `spawn`, `stream pair`, `terminate`, `stream stop`.
private final class LifecycleRunner: CommandRunner, @unchecked Sendable {  // every access holds `lock`
    let fake = FakeCommandRunner()
    private let lock = NSLock()
    private var items: [String] = []

    var events: [String] { lock.lock(); defer { lock.unlock() }; return items }
    func record(_ event: String) { lock.lock(); items.append(event); lock.unlock() }

    func run(
        executable: String, arguments: [String], environment: [String: String]?, stdin: Data?,
        timeout: TimeInterval
    ) async throws -> CommandResult {
        if let index = arguments.firstIndex(of: "stream"), index + 1 < arguments.count {
            record("stream \(arguments[index + 1])")
        } else if (executable as NSString).lastPathComponent == "diarchy-stream" {
            record("route")
        } else if (executable as NSString).lastPathComponent == "Moonlight", arguments.first == "list" {
            record("list")
        }
        return try await fake.run(
            executable: executable, arguments: arguments, environment: environment, stdin: stdin, timeout: timeout)
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
        record("spawn")
        return LoggedChild(base: try fake.spawn(executable: executable, arguments: arguments, environment: environment), log: self)
    }
}

private struct LoggedChild: ChildProcess {
    let base: any ChildProcess
    let log: LifecycleRunner

    var pid: Int32 { base.pid }
    var hasExited: Bool { base.hasExited }
    func waitForExit() async -> Int32 { await base.waitForExit() }
    func terminate() { log.record("terminate"); base.terminate() }
    func kill() { log.record("kill"); base.kill() }
}

@Suite struct MoonlightPairingTests {
    private static let pin = "4821"
    private static let host = "192.0.2.10"
    private static let startArguments = ["--", agentPath, "stream", "start", "--output", "auto"]
    private static let stopArguments = ["--", agentPath, "stream", "stop"]
    private static let pairArguments = [
        "--", agentPath, "stream", "pair", "--pin", pin, "--name", "'Diarchy'",
    ]
    private static let startOK = ok(Fixtures.streamStart + "\n")
    private static let stopOK = ok(Fixtures.streamStop + "\n")
    private static let dryRunArguments = ["--dry-run"]
    private static let listArguments = ["list", host]
    /// What `diarchy-stream --dry-run` prints, with the route's host (`remote` here: the LAN is firewalled).
    private static func dryRunOK(host: String = MoonlightPairingTests.host) -> Result<CommandResult, CommandError> {
        ok("route=remote host=\(host) profile=remote loss_pct=0.0 median_ms=12 interface=utun4\n")
    }
    /// `Moonlight list` fails: this Mac is not paired with the host yet, so the normal pairing flow runs.
    private static let listNotPaired = ok(stderr: "Failed to connect\n", exit: 1)

    private struct Setup {
        var runner: FakeCommandRunner
        var log: LifecycleRunner
        var child = FakeChildProcess()
        var sleeps = SleepRecorder()
        var pairing: MoonlightPairing
    }

    /// `sleep` defaults to never returning before it is cancelled, so only an explicit test can make the grace period expire.
    private func makeSetup(
        child: FakeChildProcess = FakeChildProcess(), agent: Result<CommandResult, CommandError>,
        start: Result<CommandResult, CommandError> = MoonlightPairingTests.startOK,
        stop: Result<CommandResult, CommandError> = MoonlightPairingTests.stopOK,
        dryRun: Result<CommandResult, CommandError> = MoonlightPairingTests.dryRunOK(),
        list: Result<CommandResult, CommandError> = MoonlightPairingTests.listNotPaired,
        instantSleep: Bool = false, home: URL = URL(fileURLWithPath: "/tmp/diarchy-home")
    ) -> Setup {
        let log = LifecycleRunner()
        let runner = log.fake
        let sleeps = SleepRecorder()
        runner.whenSpawn("Moonlight", ["pair", Self.host, "--pin", Self.pin], returns: child)
        runner.when("diarchy-ssh", Self.startArguments, return: start)
        runner.when("diarchy-ssh", Self.stopArguments, return: stop)
        runner.when("diarchy-ssh", Self.pairArguments, return: agent)
        runner.when("diarchy-stream", Self.dryRunArguments, return: dryRun)
        runner.when("Moonlight", Self.listArguments, return: list)
        let paths = Paths(home: home, payload: nil)
        let pairing = MoonlightPairing(
            paths: paths, runner: log, agent: HostAgentClient(runner: log, paths: paths),
            makePIN: { Self.pin },
            sleep: { duration in
                _ = sleeps.record(duration)
                if !instantSleep { try await Task.sleep(for: .seconds(3600)) }
            })
        return Setup(runner: runner, log: log, child: child, sleeps: sleeps, pairing: pairing)
    }

    private static func failure(_ code: String, _ message: String, _ command: String) -> Result<CommandResult, CommandError> {
        ok(Fixtures.failure(code: code, message: message, command: command) + "\n", exit: 1)
    }

    // MARK: Lifecycle (Sunshine runs only while the host stream is started)

    @Test func startsTheHostStreamBeforeMoonlightAndStopsItLast() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"))
        let outcome = try await setup.pairing.pair()
        #expect(outcome == .paired)
        #expect(setup.log.events == ["stream start", "route", "list", "spawn", "stream pair", "terminate", "stream stop"])
        #expect(
            setup.runner.calls.map(\.arguments)
                == [
                    Self.startArguments, Self.dryRunArguments, Self.listArguments, Self.pairArguments,
                    Self.stopArguments,
                ])
    }

    @Test func failedPairingStillTerminatesMoonlightAndStopsTheHostStream() async throws {
        let setup = makeSetup(agent: Self.failure("pair_failed", "Sunshine rejected the PIN", "stream pair"))
        await #expect(throws: MoonlightPairingError.agent(.agent(code: .pairFailed, message: "Sunshine rejected the PIN"))) {
            try await setup.pairing.pair()
        }
        #expect(setup.log.events == ["stream start", "route", "list", "spawn", "stream pair", "terminate", "stream stop"])
    }

    @Test func startFailureSpawnsNothingAndStopsTheHostStreamBestEffort() async throws {
        let setup = makeSetup(
            agent: ok(Fixtures.streamPair + "\n"),
            start: Self.failure("service_failed", "Sunshine did not start listening", "stream start"))
        await #expect(throws: MoonlightPairingError.agent(.agent(code: .serviceFailed, message: "Sunshine did not start listening"))) {
            try await setup.pairing.pair()
        }
        #expect(setup.log.events == ["stream start", "stream stop"])
        #expect(setup.runner.spawns.isEmpty)
    }

    @Test func alreadyStreamingLeavesTheHostStreamRunning() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"))
        try await setup.pairing.pair(hostStreamAlreadyRunning: true)
        #expect(setup.log.events == ["stream start", "route", "list", "spawn", "stream pair", "terminate"])
    }

    @Test func alreadyStreamingFailureAlsoLeavesTheHostStreamRunning() async throws {
        let setup = makeSetup(agent: Self.failure("pair_failed", "Sunshine rejected the PIN", "stream pair"))
        await #expect(throws: MoonlightPairingError.agent(.agent(code: .pairFailed, message: "Sunshine rejected the PIN"))) {
            try await setup.pairing.pair(hostStreamAlreadyRunning: true)
        }
        #expect(!setup.log.events.contains("stream stop"))
    }

    @Test func stopFailureAfterASuccessfulPairingIsReported() async throws {
        let setup = makeSetup(
            agent: ok(Fixtures.streamPair + "\n"), stop: ok(stderr: "ssh: Connection refused\n", exit: 255))
        await #expect(throws: MoonlightPairingError.streamStopFailed(.transport("ssh: Connection refused"))) {
            try await setup.pairing.pair()
        }
        #expect(MoonlightPairingError.streamStopFailed(.transport("boom")).userMessage.contains("boom"))
    }

    @Test func stopFailureDoesNotHideThePairingError() async throws {
        let setup = makeSetup(
            agent: Self.failure("pair_failed", "Sunshine rejected the PIN", "stream pair"),
            stop: ok(stderr: "ssh: Connection refused\n", exit: 255))
        await #expect(throws: MoonlightPairingError.agent(.agent(code: .pairFailed, message: "Sunshine rejected the PIN"))) {
            try await setup.pairing.pair()
        }
    }

    @Test func startUsesTheLongerSshTimeout() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"))
        try await setup.pairing.pair()
        let timeouts = setup.runner.calls.map(\.timeout)
        #expect(timeouts == [30, 20, 15, 40, 15])
    }

    // MARK: Route host (Fix D): the host `diarchy-stream --dry-run` chooses, after the stream started

    @Test func moonlightIsPairedWithTheHostChosenByDiarchyStreamDryRun() async throws {
        let remote = "100.64.0.10"
        let log = LifecycleRunner()
        let runner = log.fake
        runner.when("diarchy-ssh", Self.startArguments, return: Self.startOK)
        runner.when("diarchy-ssh", Self.stopArguments, return: Self.stopOK)
        runner.when("diarchy-ssh", Self.pairArguments, return: ok(Fixtures.streamPair + "\n"))
        runner.when("diarchy-stream", Self.dryRunArguments, return: Self.dryRunOK(host: remote))
        runner.when("Moonlight", ["list", remote], return: Self.listNotPaired)
        runner.whenSpawn("Moonlight", ["pair", remote, "--pin", Self.pin], returns: FakeChildProcess())
        let paths = Paths(home: URL(fileURLWithPath: "/tmp/diarchy-home"), payload: nil)
        let pairing = MoonlightPairing(
            paths: paths, runner: log, agent: HostAgentClient(runner: log, paths: paths), makePIN: { Self.pin })
        try await pairing.pair()
        #expect(runner.spawns.map(\.arguments) == [["pair", remote, "--pin", Self.pin]])
        let dryRun = try #require(runner.calls.first { $0.arguments == Self.dryRunArguments })
        #expect(dryRun.executable.hasSuffix("/.local/bin/diarchy-stream"))
    }

    @Test(arguments: [
        ok(stderr: "Host is unreachable on LAN and Tailscale\n", exit: 1),
        ok("no route line here\n"),
        ok("route=lan host= profile=lan\n"),
        Result<CommandResult, CommandError>.failure(.timedOut(command: "diarchy-stream")),
        Result<CommandResult, CommandError>.failure(.launchFailed(command: "diarchy-stream", message: "nope")),
    ])
    func aFailingDryRunAbortsWithAnErrorAndStillStopsTheHostStream(
        dryRun: Result<CommandResult, CommandError>
    ) async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"), dryRun: dryRun)
        await #expect(throws: MoonlightPairingError.hostUnreachable) {
            try await setup.pairing.pair()
        }
        #expect(setup.log.events == ["stream start", "route", "stream stop"])
        #expect(setup.runner.spawns.isEmpty)
        #expect(
            MoonlightPairingError.hostUnreachable.userMessage
                == "This Mac cannot reach Sunshine over the LAN or Tailscale.")
    }

    @Test func aFailingDryRunLeavesAnAlreadyRunningHostStreamAlone() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"), dryRun: ok(exit: 1))
        await #expect(throws: MoonlightPairingError.hostUnreachable) {
            try await setup.pairing.pair(hostStreamAlreadyRunning: true)
        }
        #expect(setup.log.events == ["stream start", "route"])
    }

    @Test func theDryRunDoesNotRunWhenTheHostStreamCannotStart() async throws {
        let setup = makeSetup(
            agent: ok(Fixtures.streamPair + "\n"),
            start: Self.failure("service_failed", "Sunshine did not start listening", "stream start"))
        await #expect(throws: MoonlightPairingError.self) { try await setup.pairing.pair() }
        #expect(!setup.log.events.contains("route"))
    }

    // MARK: Already paired (Fix F): `Moonlight list <host>` before the pairing flow

    @Test func anAppListedByMoonlightMeansAlreadyPairedSoNoPairingRuns() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"), list: ok("Desktop\n"))
        let outcome = try await setup.pairing.pair()
        #expect(outcome == .alreadyPaired)
        #expect(setup.log.events == ["stream start", "route", "list", "stream stop"])
        #expect(setup.runner.spawns.isEmpty)
        #expect(!setup.runner.calls.map(\.arguments).contains(Self.pairArguments))
        let list = try #require(setup.runner.calls.first { $0.arguments == Self.listArguments })
        #expect(list.executable.hasSuffix("/Applications/Moonlight.app/Contents/MacOS/Moonlight"))
        #expect(list.timeout == 15)
    }

    @Test func alreadyPairedLeavesARunningHostStreamAlone() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"), list: ok("Desktop\n"))
        let outcome = try await setup.pairing.pair(hostStreamAlreadyRunning: true)
        #expect(outcome == .alreadyPaired)
        #expect(setup.log.events == ["stream start", "route", "list"])
    }

    @Test func logLinesAreIgnoredWhenDecidingIfMoonlightIsPaired() async throws {
        let setup = makeSetup(
            agent: ok(Fixtures.streamPair + "\n"),
            list: ok("Redirecting log output to /tmp/moonlight.log\n\n  \n"))
        let outcome = try await setup.pairing.pair()
        #expect(outcome == .paired)
        #expect(setup.log.events == ["stream start", "route", "list", "spawn", "stream pair", "terminate", "stream stop"])
    }

    @Test(arguments: [
        ok("Desktop\n", exit: 1),
        ok(exit: 0),
        Result<CommandResult, CommandError>.failure(.timedOut(command: "Moonlight")),
        Result<CommandResult, CommandError>.failure(.launchFailed(command: "Moonlight", message: "nope")),
    ])
    func anyOtherListOutcomeRunsTheNormalPairingFlow(list: Result<CommandResult, CommandError>) async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"), list: list)
        let outcome = try await setup.pairing.pair()
        #expect(outcome == .paired)
        #expect(setup.log.events == ["stream start", "route", "list", "spawn", "stream pair", "terminate", "stream stop"])
    }

    @Test func theOutcomeMessagesAreEnglish() {
        #expect(MoonlightPairingOutcome.alreadyPaired.userMessage == "Moonlight was already paired with the host.")
        #expect(MoonlightPairingOutcome.paired.userMessage == "Moonlight is paired with the host.")
    }

    // MARK: Agent failures (Fix E)

    @Test func pairFailedIsShownWithoutTheAgentsMessage() {
        let error = MoonlightPairingError.agent(.agent(code: .pairFailed, message: "Sunshine pairing timed out"))
        #expect(
            error.userMessage
                == "Moonlight did not finish pairing in time. Check that the host is reachable on the chosen route and try again."
        )
        #expect(!error.userMessage.contains("Sunshine pairing timed out"))
    }

    @Test func sunshineUnreachableIsShownWithoutTheAgentsMessage() {
        let error = MoonlightPairingError.agent(.agent(code: .sunshineUnreachable, message: "Sunshine is unreachable"))
        #expect(error.userMessage == "Sunshine is not responding on the host.")
    }

    @Test func otherAgentFailuresShowTheHostAgentUserMessage() {
        #expect(MoonlightPairingError.agent(.agent(code: .notInstalled, message: "boom")).userMessage == "boom")
        #expect(
            MoonlightPairingError.agent(.agent(code: .serviceFailed, message: "boom")).userMessage
                == "A service failed on the host: boom")
    }

    @Test func randomPINIsAlwaysFourDigits() {
        for _ in 0..<1000 {
            let pin = MoonlightPairing.randomPIN()
            #expect(pin.range(of: "^[0-9]{4}$", options: .regularExpression) != nil, "\(pin)")
        }
    }

    @Test func spawnsMoonlightWithTheExactArgvAndSamePINAsTheAgent() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"))
        try await setup.pairing.pair()
        #expect(setup.runner.spawns.count == 1)
        let spawn = try #require(setup.runner.spawns.first)
        #expect(spawn.executable.hasSuffix("/Applications/Moonlight.app/Contents/MacOS/Moonlight"))
        #expect(spawn.arguments == ["pair", Self.host, "--pin", Self.pin])
    }

    @Test func agentSuccessWithMoonlightStillRunningTerminatesTheChild() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"))
        try await setup.pairing.pair()
        #expect(setup.child.terminated)
        #expect(!setup.child.killed)
        #expect(setup.child.hasExited)
    }

    @Test func agentSuccessWithMoonlightExitZeroSucceedsWithoutSignals() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"))
        setup.child.exit(code: 0)
        try await setup.pairing.pair()
        #expect(!setup.child.terminated)
        #expect(!setup.child.killed)
        #expect(setup.sleeps.requested.isEmpty)
    }

    @Test func agentSuccessWithMoonlightExitOneStillSucceedsWithoutSignals() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"))
        setup.child.exit(code: 1)
        try await setup.pairing.pair()
        #expect(!setup.child.terminated)
        #expect(!setup.child.killed)
        #expect(setup.sleeps.requested.isEmpty)
    }

    @Test func agentFailureWithMoonlightExitZeroStillThrows() async throws {
        let setup = makeSetup(
            agent: ok(Fixtures.failure(code: "pair_failed", message: "Sunshine rejected the PIN", command: "stream pair") + "\n", exit: 1))
        setup.child.exit(code: 0)
        await #expect(throws: MoonlightPairingError.agent(.agent(code: .pairFailed, message: "Sunshine rejected the PIN"))) {
            try await setup.pairing.pair()
        }
        #expect(!setup.child.terminated)
        #expect(!setup.child.killed)
    }

    @Test func agentFailureTerminatesTheRunningChild() async throws {
        let setup = makeSetup(
            agent: ok(Fixtures.failure(code: "pair_failed", message: "Sunshine rejected the PIN", command: "stream pair") + "\n", exit: 1))
        await #expect(throws: MoonlightPairingError.agent(.agent(code: .pairFailed, message: "Sunshine rejected the PIN"))) {
            try await setup.pairing.pair()
        }
        #expect(setup.child.terminated)
        #expect(!setup.child.killed)
    }

    @Test func agentTransportFailureIsAnAgentError() async throws {
        let setup = makeSetup(agent: ok(stderr: "ssh: Connection refused\n", exit: 255))
        await #expect(throws: MoonlightPairingError.agent(.transport("ssh: Connection refused"))) {
            try await setup.pairing.pair()
        }
        #expect(setup.child.terminated)
    }

    @Test func childIgnoringSigtermIsKilledAfterTheFiveSecondGrace() async throws {
        let child = FakeChildProcess(exitsOnTerminate: false)
        let setup = makeSetup(child: child, agent: ok(Fixtures.streamPair + "\n"), instantSleep: true)
        try await setup.pairing.pair()
        #expect(child.terminated)
        #expect(child.killed)
        #expect(setup.sleeps.requested == [.seconds(5)])
    }

    @Test func childThatExitsOnSigtermIsNotKilled() async throws {
        let setup = makeSetup(agent: ok(Fixtures.streamPair + "\n"), instantSleep: true)
        try await setup.pairing.pair()
        #expect(setup.child.terminated)
        #expect(!setup.child.killed)
    }

    /// A runner whose host stream starts and stops, whose route is `host`, and whose Moonlight is not paired yet.
    private func plainRunner() -> LifecycleRunner {
        let runner = LifecycleRunner()
        runner.fake.when("diarchy-ssh", Self.startArguments, return: Self.startOK)
        runner.fake.when("diarchy-ssh", Self.stopArguments, return: Self.stopOK)
        runner.fake.when("diarchy-stream", Self.dryRunArguments, return: Self.dryRunOK())
        runner.fake.when("Moonlight", Self.listArguments, return: Self.listNotPaired)
        return runner
    }

    @Test func spawnTimeoutIsReportedAndStopsTheHostStream() async throws {
        let runner = plainRunner()
        runner.fake.whenSpawn("Moonlight", fails: .timedOut(command: "Moonlight"))
        let paths = Paths(home: URL(fileURLWithPath: "/tmp/diarchy-home"), payload: nil)
        let pairing = MoonlightPairing(
            paths: paths, runner: runner, agent: HostAgentClient(runner: runner, paths: paths), makePIN: { Self.pin })
        await #expect(throws: MoonlightPairingError.launchFailed("Moonlight did not respond in time")) {
            try await pairing.pair()
        }
        #expect(runner.events == ["stream start", "route", "list", "spawn", "stream stop"])
    }

    @Test func spawnFailureIsLaunchFailedAndStopsTheHostStreamWithoutPairing() async throws {
        let runner = plainRunner()
        runner.fake.whenSpawn(
            "Moonlight", fails: .launchFailed(command: "Moonlight", message: "No such file or directory"))
        let paths = Paths(home: URL(fileURLWithPath: "/tmp/diarchy-home"), payload: nil)
        let pairing = MoonlightPairing(
            paths: paths, runner: runner, agent: HostAgentClient(runner: runner, paths: paths), makePIN: { Self.pin })
        await #expect(throws: MoonlightPairingError.launchFailed("No such file or directory")) {
            try await pairing.pair()
        }
        #expect(
            runner.fake.calls.map(\.arguments)
                == [Self.startArguments, Self.dryRunArguments, Self.listArguments, Self.stopArguments])
    }

    @Test func userMessages() {
        #expect(MoonlightPairingError.agent(.transport("boom")).userMessage == "boom")
        #expect(MoonlightPairingError.launchFailed("nope").userMessage.contains("nope"))
    }
}
