import Foundation
import Testing
@testable import DiarchyCore

@Suite struct HostAgentClientTests {
    private static let home = URL(fileURLWithPath: "/tmp/diarchy-test-home", isDirectory: true)
    private static let paths = Paths(home: home, payload: nil)
    private static let ssh = paths.localBin("diarchy-ssh")
    private static let agent = ["--", "~/.local/bin/diarchy-host"]

    private func fixture(
        _ stdout: String, exit: Int32 = 0, stderr: String = ""
    ) -> (client: HostAgentClient, runner: FakeCommandRunner) {
        let runner = FakeCommandRunner()
        runner.when(
            { _ in true },
            return: .success(CommandResult(exitCode: exit, stdout: stdout + "\n", stderr: stderr)))
        return (HostAgentClient(runner: runner, paths: Self.paths), runner)
    }

    private func failure<T>(_ body: () async throws(HostAgentError) -> T) async -> HostAgentError? {
        do {
            _ = try await body()
            return nil
        } catch {
            return error
        }
    }

    @Test func detachedStreamStopSpawnsTheSshAgentCallWithoutWaiting() {
        let runner = FakeCommandRunner()
        runner.whenSpawn("diarchy-ssh", returns: FakeChildProcess())
        HostAgentClient(runner: runner, paths: Self.paths).spawnDetachedStreamStop()
        #expect(runner.spawns.map(\.commandLine) == ["\(Self.ssh) -- ~/.local/bin/diarchy-host stream stop"])
        #expect(runner.calls.isEmpty)
    }

    // MARK: (a) argv

    struct ArgvCase: Sendable, CustomTestStringConvertible {
        var name: String
        var words: [String]
        var stdout: String
        /// 15 s for every command except `stream start` (30 s: the agent waits up to 12 s for Sunshine's listener)
        /// and `stream pair` (40 s: the agent retries `/api/pin` for up to 20 s).
        var timeout: TimeInterval = 15
        var run: @Sendable (HostAgentClient) async throws -> Void
        var testDescription: String { name }
    }

    static let argvCases: [ArgvCase] = [
        ArgvCase(name: "version", words: ["version"], stdout: Fixtures.version) { _ = try await $0.version() },
        ArgvCase(name: "probe", words: ["probe"], stdout: Fixtures.probeFull) {
            _ = try await $0.probe(controllerIP: nil)
        },
        ArgvCase(
            name: "probe ip", words: ["probe", "--controller-ip", "192.0.2.5"], stdout: Fixtures.probeFull
        ) { _ = try await $0.probe(controllerIP: "192.0.2.5") },
        ArgvCase(
            name: "probe ip streaming", words: ["probe", "--controller-ip", "192.0.2.5", "--streaming"],
            stdout: Fixtures.probeFull
        ) { _ = try await $0.probe(controllerIP: "192.0.2.5", streaming: true) },
        ArgvCase(name: "probe streaming", words: ["probe", "--streaming"], stdout: Fixtures.probeFull) {
            _ = try await $0.probe(controllerIP: nil, streaming: true)
        },
        ArgvCase(
            name: "kvm authorize",
            words: ["kvm", "authorize", "--fingerprint", Fixtures.fingerprintAA], stdout: Fixtures.kvmAuthorize
        ) { _ = try await $0.kvmAuthorize(fingerprint: Fixtures.fingerprintAA) },
        ArgvCase(name: "kvm start", words: ["kvm", "start"], stdout: Fixtures.kvmStart) {
            try await $0.kvmStart()
        },
        ArgvCase(
            name: "stream start", words: ["stream", "start", "--output", "auto"], stdout: Fixtures.streamStart,
            timeout: 30
        ) {
            try await $0.streamStart(output: "auto")
        },
        ArgvCase(name: "stream stop", words: ["stream", "stop"], stdout: Fixtures.streamStop) {
            try await $0.streamStop()
        },
        ArgvCase(
            name: "stream pair",
            words: ["stream", "pair", "--pin", "0042", "--name", "'Diarchy'"], stdout: Fixtures.streamPair,
            timeout: 40
        ) { try await $0.streamPair(pin: "0042", name: "Diarchy") },
    ]

    @Test(arguments: argvCases)
    func composesTheExactCommand(_ item: ArgvCase) async throws {
        let (client, runner) = fixture(item.stdout)
        try await item.run(client)
        let call = try #require(runner.calls.first)
        #expect(runner.calls.count == 1)
        #expect(call.executable == Self.ssh)
        #expect(call.arguments == Self.agent + item.words)
        #expect(call.timeout == item.timeout)
        #expect(call.stdin == nil)
    }

    @Test func timeoutsAreConfigurable() async throws {
        let runner = FakeCommandRunner()
        runner.when({ _ in true }, return: .success(CommandResult(exitCode: 0, stdout: Fixtures.version + "\n")))
        _ = try await HostAgentClient(runner: runner, paths: Self.paths, timeout: 3).version()
        #expect(runner.calls.first?.timeout == 3)

        let pairRunner = FakeCommandRunner()
        pairRunner.when({ _ in true }, return: .success(CommandResult(exitCode: 0, stdout: Fixtures.streamPair + "\n")))
        let client = HostAgentClient(runner: pairRunner, paths: Self.paths, timeout: 3, startTimeout: 5, pairTimeout: 7)
        try await client.streamPair(pin: "1234", name: "Diarchy")
        #expect(pairRunner.calls.first?.timeout == 7)
        let startRunner = FakeCommandRunner()
        startRunner.when({ _ in true }, return: .success(CommandResult(exitCode: 0, stdout: Fixtures.streamStart + "\n")))
        try await HostAgentClient(runner: startRunner, paths: Self.paths, timeout: 3, startTimeout: 5).streamStart(output: "auto")
        #expect(startRunner.calls.first?.timeout == 5)
    }

    static let badIPs: [String] = [
        "192.0.2.999", "abc", "192.0.2", "192.0.2.5.6", "192.0.2.05", "", "192.0.2.5; id", "::1", "1.2.3.-4",
    ]

    @Test(arguments: badIPs)
    func malformedControllerIPOmitsTheFlag(_ ip: String) async throws {
        let (client, runner) = fixture(Fixtures.probeFull)
        _ = try await client.probe(controllerIP: ip)
        #expect(runner.calls.first?.arguments == Self.agent + ["probe"])
        let (streamingClient, streamingRunner) = fixture(Fixtures.probeFull)
        _ = try await streamingClient.probe(controllerIP: ip, streaming: true)
        #expect(streamingRunner.calls.first?.arguments == Self.agent + ["probe", "--streaming"])
    }

    @Test func pairedNameIsOneSingleQuotedWord() async throws {
        let (client, runner) = fixture(Fixtures.streamPair)
        try await client.streamPair(pin: "1234", name: "Diarchy")
        let arguments = try #require(runner.calls.first?.arguments)
        #expect(arguments.contains("'Diarchy'"))
        #expect(arguments.last == "'Diarchy'")
    }

    // MARK: (b) success payloads

    @Test func versionDecodes() async throws {
        let (client, _) = fixture(Fixtures.version)
        #expect(try await client.version() == "1.0.0")
    }

    @Test func fullProbeDecodes() async throws {
        let (client, _) = fixture(Fixtures.probeFull)
        let probe = try await client.probe(controllerIP: "192.0.2.5")
        #expect(probe.agentVersion == "1.0.0")
        #expect(
            probe.platform
                == ProbeResult.Platform(
                    supported: true, adapter: "hyprland-arch", osId: "arch", flavor: "omarchy",
                    compositor: "hyprland", machine: "x86_64", reasons: []))
        #expect(probe.session.graphicalSessionActive)
        #expect(
            probe.components.lanMouse
                == ProbeResult.Components.LanMouse(
                    installed: true, version: "0.11.0", serviceActive: true, listeningUdp: true))
        #expect(probe.components.clipboard.installed)
        #expect(probe.components.filePortal == ProbeResult.Components.Portal(installed: true, serviceActive: true))
        #expect(probe.components.sunshine == ProbeResult.Components.Sunshine(installed: false, serviceActive: false))
        #expect(probe.kvm.hostFingerprint == Fixtures.fingerprintAA)
        #expect(probe.kvm.authorizedFingerprints == [Fixtures.fingerprintBB])
        #expect(probe.kvm.port == 4242)
        let action = try #require(probe.requiredActions.first)
        #expect(probe.requiredActions.count == 1)
        #expect(action.id == "install-packages")
        #expect(action.severity == .required)
        #expect(action.command == "sudo pacman -S --needed wl-clipboard rsync")
        #expect(probe.rawJSON == Fixtures.probeFull)
    }

    @Test func unsupportedProbeDecodesNullableFieldsAsNil() async throws {
        let (client, _) = fixture(Fixtures.probeUnsupported)
        let probe = try await client.probe(controllerIP: nil)
        #expect(!probe.platform.supported)
        #expect(probe.platform.adapter == "unsupported")
        #expect(probe.platform.reasons == ["host is not x86_64", "no live Hyprland instance"])
        #expect(probe.platform.flavor == nil)
        #expect(probe.platform.compositor == nil)
        #expect(probe.kvm.hostFingerprint == nil)
        #expect(probe.kvm.authorizedFingerprints.isEmpty)
        #expect(probe.components.lanMouse.version == nil)
        #expect(!probe.session.graphicalSessionActive)
        #expect(probe.requiredActions.isEmpty)
    }

    struct ActionCase: Sendable, CustomTestStringConvertible {
        var id: String
        var json: String
        var severity: RequiredAction.Severity
        var commandPrefix: String
        var testDescription: String { id }
    }

    static let actionCases: [ActionCase] = [
        ActionCase(id: "install-packages", json: Fixtures.installPackages, severity: .required, commandPrefix: "sudo pacman"),
        ActionCase(id: "firewall-lan", json: Fixtures.firewallLAN, severity: .recommended, commandPrefix: "sudo ufw allow proto udp"),
        ActionCase(id: "firewall-tailscale", json: Fixtures.firewallTailscale, severity: .recommended, commandPrefix: "sudo ufw allow in on tailscale0"),
        ActionCase(id: "uinput-access", json: Fixtures.uinputAccess, severity: .required, commandPrefix: "echo 'KERNEL=="),
        ActionCase(id: "lid-policy", json: Fixtures.lidPolicy, severity: .recommended, commandPrefix: "sudo mkdir"),
        ActionCase(id: "wifi-roaming", json: Fixtures.wifiRoaming, severity: .recommended, commandPrefix: "nmcli connection modify"),
    ]

    @Test(arguments: actionCases)
    func everyRequiredActionDecodes(_ item: ActionCase) async throws {
        let (client, _) = fixture(Fixtures.probe(actions: [item.json]))
        let action = try #require(try await client.probe(controllerIP: nil, streaming: true).requiredActions.first)
        #expect(action.id == item.id)
        #expect(action.severity == item.severity)
        #expect(action.command.hasPrefix(item.commandPrefix))
        #expect(!action.title.isEmpty)
        #expect(!action.reason.isEmpty)
    }

    @Test func unknownSeverityDecodesAsRecommendedWithoutFailingTheProbe() async throws {
        let future = Fixtures.installPackages.replacingOccurrences(of: #""severity":"required""#, with: #""severity":"blocking""#)
        #expect(future != Fixtures.installPackages)
        let (client, _) = fixture(Fixtures.probe(actions: [future, Fixtures.lidPolicy]))
        let probe = try await client.probe(controllerIP: nil)
        #expect(probe.requiredActions.map(\.id) == ["install-packages", "lid-policy"])
        #expect(probe.requiredActions.map(\.severity) == [.recommended, .recommended])
        #expect(probe.requiredActions[0].command == "sudo pacman -S --needed wl-clipboard rsync")
    }

    @Test func unknownActionIdKeepsTheAgentText() async throws {
        let (client, _) = fixture(Fixtures.probe(actions: [Fixtures.unknownAction]))
        let action = try #require(try await client.probe(controllerIP: nil).requiredActions.first)
        #expect(action.title == "A future action")
        #expect(action.reason == "Something new")
        #expect(action.command == "true")
    }

    @Test func theCommandIsNeverAltered() async throws {
        let (client, _) = fixture(Fixtures.probe(actions: [Fixtures.firewallLAN]))
        let action = try #require(try await client.probe(controllerIP: "192.0.2.5").requiredActions.first)
        #expect(action.command == "sudo ufw allow proto udp from 192.0.2.5 to any port 4242")
    }

    @Test func kvmAuthorizeReturnsTheAuthorizedFingerprint() async throws {
        let (client, _) = fixture(Fixtures.kvmAuthorize)
        #expect(try await client.kvmAuthorize(fingerprint: Fixtures.fingerprintAA) == Fixtures.fingerprintAA)
    }

    @Test func startReportingAnInactiveServiceIsRejected() async {
        let inactive = #""service_active":true"#
        let (kvm, _) = fixture(Fixtures.kvmStart.replacingOccurrences(of: inactive, with: #""service_active":false"#))
        let kvmError = await failure { () async throws(HostAgentError) in try await kvm.kvmStart() }
        guard case .malformedOutput = kvmError else {
            Issue.record("expected malformedOutput, got \(String(describing: kvmError))")
            return
        }
        let (stream, _) = fixture(Fixtures.streamStart.replacingOccurrences(of: inactive, with: #""service_active":false"#))
        let streamError = await failure { () async throws(HostAgentError) in try await stream.streamStart(output: "auto") }
        guard case .malformedOutput = streamError else {
            Issue.record("expected malformedOutput, got \(String(describing: streamError))")
            return
        }
    }

    @Test func pairReportingNotPairedIsRejected() async {
        let (client, _) = fixture(#"{"schema":1,"ok":true,"command":"stream pair","paired":false}"#)
        let error = await failure { () async throws(HostAgentError) in
            try await client.streamPair(pin: "1234", name: "Diarchy")
        }
        guard case .malformedOutput = error else {
            Issue.record("expected malformedOutput, got \(String(describing: error))")
            return
        }
    }

    // MARK: (c) agent errors

    @Test(arguments: Fixtures.failures.map { $0.code })
    func everyErrorCodeMapsToAnAgentError(_ wire: String) async throws {
        let item = try #require(Fixtures.failures.first { $0.code == wire })
        let (client, _) = fixture(Fixtures.failure(code: item.code, message: item.message), exit: item.exit)
        let expected: AgentErrorCode =
            switch wire {
            case "usage": .usage
            case "unsupported_platform": .unsupportedPlatform
            case "not_installed": .notInstalled
            case "no_session": .noSession
            case "service_failed": .serviceFailed
            case "invalid_fingerprint": .invalidFingerprint
            case "config_write_failed": .configWriteFailed
            case "sunshine_unreachable": .sunshineUnreachable
            case "pair_failed": .pairFailed
            default: .internal
            }
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(error == .agent(code: expected, message: item.message))
        // The two codes the user can act on read in Spanish; every other agent message is shown verbatim.
        switch expected {
        case .noSession, .serviceFailed: #expect(error?.userMessage != item.message)
        default: #expect(error?.userMessage == item.message)
        }
    }

    @Test func failureFixturesCoverEverySpecCode() {
        #expect(
            Fixtures.failures.map { $0.code } == [
                "usage", "unsupported_platform", "not_installed", "no_session", "service_failed", "invalid_fingerprint",
                "config_write_failed", "sunshine_unreachable", "pair_failed", "internal",
            ])
        #expect(Fixtures.failures.filter { $0.exit == 64 }.map { $0.code } == ["usage"])
    }

    @Test func unknownErrorCodeIsKeptVerbatim() async {
        let (client, _) = fixture(Fixtures.failure(code: "x", message: "boom"), exit: 1)
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(error == .agent(code: .unknown("x"), message: "boom"))
    }

    @Test func noSessionReadsWithTheRemoteRecovery() {
        let error = HostAgentError.agent(code: .noSession, message: "no Hyprland session is running for this user")
        #expect(
            error.userMessage
                == "No Omarchy session is open on the host (did it log out?). Log in on the host or, remotely, restart the login screen over SSH with: sudo systemctl restart sddm"
        )
    }

    @Test func serviceFailedKeepsTheAgentMessage() {
        let error = HostAgentError.agent(code: .serviceFailed, message: "streaming service failed to start")
        #expect(error.userMessage == "A service failed on the host: streaming service failed to start")
    }

    @Test func unknownCodesStayVerbatim() {
        #expect(HostAgentError.agent(code: .unknown("x"), message: "boom").userMessage == "boom")
        #expect(HostAgentError.agent(code: .notInstalled, message: "not here").userMessage == "not here")
    }

    @Test func agentErrorsFromProbeSurfaceToo() async {
        let (probe, _) = fixture(
            Fixtures.failure(code: "unsupported_platform", message: "nope", command: "probe"), exit: 1)
        let probeError = await failure { () async throws(HostAgentError) in
            try await probe.probe(controllerIP: nil)
        }
        #expect(probeError == .agent(code: .unsupportedPlatform, message: "nope"))
    }

    // MARK: (d) transport

    @Test func exit255IsATransportErrorWithTheFirstStderrLine() async {
        let message = "diarchy-ssh: Host is unreachable on LAN and Tailscale"
        let (client, _) = fixture("", exit: 255, stderr: message + "\nsecond line\n")
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(error == .transport(message))
        #expect(error?.userMessage == message)
    }

    @Test func exit255IgnoresAnyStdout() async {
        let (client, _) = fixture(Fixtures.kvmStart, exit: 255, stderr: "ssh: connect to host failed\n")
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(error == .transport("ssh: connect to host failed"))
    }

    @Test func launchFailureIsATransportError() async {
        let runner = FakeCommandRunner()
        runner.when(
            { _ in true }, return: .failure(.launchFailed(command: Self.ssh, message: "No such file or directory")))
        let client = HostAgentClient(runner: runner, paths: Self.paths)
        let error = await failure { () async throws(HostAgentError) in try await client.version() }
        #expect(error == .transport("No such file or directory"))
    }

    // MARK: (e) malformed output

    static let malformedOutputs: [String] = {
        let line = Fixtures.kvmStart
        return [
            "",
            "\n\n",
            line + "\n" + line,
            "Welcome to the host\nlast login: never\n",
            "not json at all",
            #"{"schema":1,"ok":true,"command":"kvm start""#,
            #"["schema",1]"#,
            #"{"ok":true,"command":"kvm start","service_active":true}"#,
            #"{"schema":1,"command":"kvm start","service_active":true}"#,
            #"{"schema":1,"ok":true,"command":"kvm start"}"#,
            #"{"schema":1,"ok":true,"command":"kvm start","service_active":"yes"}"#,
            #"{"schema":1,"ok":false,"command":"kvm start"}"#,
            #"{"schema":1,"ok":false,"command":"kvm start","error":{"code":"internal"}}"#,
        ]
    }()

    @Test(arguments: malformedOutputs)
    func malformedStdoutIsRejected(_ stdout: String) async {
        let runner = FakeCommandRunner()
        runner.when({ _ in true }, return: .success(CommandResult(exitCode: 0, stdout: stdout)))
        let client = HostAgentClient(runner: runner, paths: Self.paths)
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        guard case .malformedOutput(let message) = error else {
            Issue.record("expected malformedOutput for \(stdout.debugDescription), got \(String(describing: error))")
            return
        }
        #expect(!message.isEmpty)
    }

    @Test func stdoutWithoutTrailingNewlineIsAccepted() async throws {
        let runner = FakeCommandRunner()
        runner.when({ _ in true }, return: .success(CommandResult(exitCode: 0, stdout: Fixtures.kvmStart)))
        let client = HostAgentClient(runner: runner, paths: Self.paths)
        try await client.kvmStart()
    }

    private static let knownHostsWarning =
        "Warning: Permanently added '192.0.2.5' (ED25519) to the list of known hosts."

    @Test func loginBannerAndKnownHostsWarningBeforeTheEnvelopeAreIgnored() async throws {
        let banner = Self.knownHostsWarning + "\nWelcome to Omarchy\n\n"
        let (client, _) = fixture(banner + Fixtures.probeFull)
        let probe = try await client.probe(controllerIP: nil)
        #expect(probe.agentVersion == "1.0.0")
        #expect(probe.rawJSON == Fixtures.probeFull)
    }

    @Test func bannerAfterTheEnvelopeIsIgnoredToo() async throws {
        let (client, _) = fixture(Fixtures.version + "\nConnection to 192.0.2.5 closed.")
        #expect(try await client.version() == "1.0.0")
    }

    @Test func twoEnvelopesAmongBannersAreStillRejected() async {
        let (client, _) = fixture("Welcome\n" + Fixtures.version + "\nnoise\n" + Fixtures.version)
        let error = await failure { () async throws(HostAgentError) in try await client.version() }
        guard case .malformedOutput = error else {
            Issue.record("expected malformedOutput, got \(String(describing: error))")
            return
        }
    }

    @Test func knownHostsWarningIsNotTheTransportMessage() async {
        let message = "diarchy-ssh: Host is unreachable on LAN and Tailscale"
        let (client, _) = fixture("", exit: 255, stderr: Self.knownHostsWarning + "\n" + message + "\n")
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(error == .transport(message))
    }

    @Test func onlyAKnownHostsWarningFallsBackToTheExitCode() async {
        let (client, _) = fixture("", exit: 255, stderr: Self.knownHostsWarning + "\n")
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(error == .transport("exit code 255"))
    }

    @Test func emptyStdoutReasonSkipsTheKnownHostsWarning() async {
        let reason = "bash: diarchy-host: No such file or directory"
        let (client, _) = fixture("", exit: 127, stderr: Self.knownHostsWarning + "\n" + reason + "\n")
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        guard case .malformedOutput(let message) = error else {
            Issue.record("expected malformedOutput, got \(String(describing: error))")
            return
        }
        #expect(message.contains(reason))
        #expect(!message.contains("Permanently added"))
    }

    @Test func emptyStdoutMentionsTheStderrReason() async {
        let runner = FakeCommandRunner()
        runner.when(
            { _ in true },
            return: .success(
                CommandResult(exitCode: 127, stdout: "", stderr: "bash: diarchy-host: No such file or directory\n")))
        let client = HostAgentClient(runner: runner, paths: Self.paths)
        let error = await failure { () async throws(HostAgentError) in try await client.version() }
        guard case .malformedOutput(let message) = error else {
            Issue.record("expected malformedOutput, got \(String(describing: error))")
            return
        }
        #expect(message.contains("No such file or directory"))
    }

    @Test func unsupportedSchemaIsReportedWithItsNumber() async {
        let (client, _) = fixture(#"{"schema":2,"ok":true,"command":"kvm status","service_active":true}"#)
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(error == .unsupportedSchema(2))
    }

    @Test func unsupportedSchemaWinsOverAChangedPayload() async {
        let (client, _) = fixture(#"{"schema":3,"ok":false,"command":"kvm status","error":"flat string now"}"#)
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(error == .unsupportedSchema(3))
    }

    @Test func successEnvelopeWithANonZeroExitIsRejected() async {
        let (client, _) = fixture(Fixtures.kvmStart, exit: 1)
        let error = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        guard case .malformedOutput = error else {
            Issue.record("expected malformedOutput, got \(String(describing: error))")
            return
        }
    }

    // MARK: (f) timeout

    @Test func runnerTimeoutBecomesTimedOutWithTheSubcommand() async {
        let runner = FakeCommandRunner()
        runner.when({ _ in true }, return: .failure(.timedOut(command: "diarchy-ssh")))
        let client = HostAgentClient(runner: runner, paths: Self.paths)
        let start = await failure { () async throws(HostAgentError) in try await client.kvmStart() }
        #expect(start == .timedOut(command: "diarchy-host kvm start"))
        #expect(start?.userMessage == "diarchy-host kvm start did not respond in time")
        let probe = await failure { () async throws(HostAgentError) in
            try await client.probe(controllerIP: "192.0.2.5", streaming: true)
        }
        #expect(probe == .timedOut(command: "diarchy-host probe"))
        let pair = await failure { () async throws(HostAgentError) in
            try await client.streamPair(pin: "1234", name: "Diarchy")
        }
        #expect(pair == .timedOut(command: "diarchy-host stream pair"))
    }

    @Test func unexpectedRunnerErrorsBecomeTransportErrors() async {
        let runner = FakeCommandRunner()  // no rule: the fake throws UnmatchedCommandError
        let client = HostAgentClient(runner: runner, paths: Self.paths)
        let error = await failure { () async throws(HostAgentError) in try await client.version() }
        guard case .transport = error else {
            Issue.record("expected transport, got \(String(describing: error))")
            return
        }
    }

    // MARK: (g) client-side validation

    static let invalidFingerprints: [String] = {
        let valid = Fixtures.fingerprintAA
        let groups31 = Array(repeating: "AA", count: 31).joined(separator: ":")
        let groups33 = Array(repeating: "AA", count: 33).joined(separator: ":")
        let nonHex = Array(repeating: "GG", count: 32).joined(separator: ":")
        let dashed = Array(repeating: "AA", count: 32).joined(separator: "-")
        return [valid.lowercased(), groups31, groups33, valid + ";", valid + "\n", valid + " ", groups31 + ":A;", nonHex, dashed, ""]
    }()

    @Test(arguments: invalidFingerprints)
    func invalidFingerprintsNeverReachTheRunner(_ fingerprint: String) async {
        let (client, runner) = fixture(Fixtures.kvmAuthorize)
        let error = await failure { () async throws(HostAgentError) in
            try await client.kvmAuthorize(fingerprint: fingerprint)
        }
        guard case .invalidArgument(let message) = error else {
            Issue.record("expected invalidArgument, got \(String(describing: error))")
            return
        }
        #expect(!message.isEmpty)
        #expect(runner.calls.isEmpty)
    }

    static let invalidPins: [String] = ["123", "12345", "12a4", "", "١٢٣٤", "12 4", "1234\n", "-123", "+123"]

    @Test(arguments: invalidPins)
    func invalidPinsNeverReachTheRunner(_ pin: String) async {
        let (client, runner) = fixture(Fixtures.streamPair)
        let error = await failure { () async throws(HostAgentError) in
            try await client.streamPair(pin: pin, name: "Diarchy")
        }
        guard case .invalidArgument = error else {
            Issue.record("expected invalidArgument, got \(String(describing: error))")
            return
        }
        #expect(runner.calls.isEmpty)
    }

    static let invalidNames: [String] = [
        "x'; rm -rf ~", "", "a$b", "a`b`", "a\"b", "a\nb", "ñandú", "a;b", "a|b", "a&b", "a(b)",
        String(repeating: "a", count: 41), String(repeating: "a", count: 64),
    ]

    @Test(arguments: invalidNames)
    func invalidNamesNeverReachTheRunner(_ name: String) async {
        let (client, runner) = fixture(Fixtures.streamPair)
        let error = await failure { () async throws(HostAgentError) in
            try await client.streamPair(pin: "1234", name: name)
        }
        guard case .invalidArgument = error else {
            Issue.record("expected invalidArgument, got \(String(describing: error))")
            return
        }
        #expect(runner.calls.isEmpty)
    }

    @Test func boundaryNamesAreAccepted() async throws {
        for name in ["a", String(repeating: "a", count: 40), "My Mac_1.local-2"] {
            let (client, runner) = fixture(Fixtures.streamPair)
            try await client.streamPair(pin: "1234", name: name)
            #expect(runner.calls.first?.arguments.last == "'\(name)'")
        }
    }

    // MARK: (i) sshTrue

    @Test func sshTrueRunsDiarchySshTrueAndReturnsTheResult() async {
        let runner = FakeCommandRunner()
        let result = CommandResult(exitCode: 255, stdout: "", stderr: "Permission denied (publickey).\n")
        runner.when({ _ in true }, return: .success(result))
        let client = HostAgentClient(runner: runner, paths: Self.paths)
        #expect(await client.sshTrue() == result)
        #expect(runner.calls.first?.executable == Self.ssh)
        #expect(runner.calls.first?.arguments == ["--", "true"])
        #expect(runner.calls.first?.timeout == 15)
    }

    @Test func sshTrueMapsRunnerFailuresToExit255() async {
        let timedOut = FakeCommandRunner()
        timedOut.when({ _ in true }, return: .failure(.timedOut(command: "diarchy-ssh")))
        let timedOutResult = await HostAgentClient(runner: timedOut, paths: Self.paths).sshTrue()
        #expect(timedOutResult.exitCode == 255)
        #expect(timedOutResult.firstErrorLine.contains("diarchy-ssh"))

        let launch = FakeCommandRunner()
        launch.when(
            { _ in true }, return: .failure(.launchFailed(command: Self.ssh, message: "No such file or directory")))
        let launchResult = await HostAgentClient(runner: launch, paths: Self.paths).sshTrue()
        #expect(launchResult.exitCode == 255)
        #expect(launchResult.stderr.contains("No such file or directory"))

        let unmatched = await HostAgentClient(runner: FakeCommandRunner(), paths: Self.paths).sshTrue()
        #expect(unmatched.exitCode == 255)
        #expect(!unmatched.stderr.isEmpty)
    }

    // MARK: user messages

    @Test func userMessagesPassTheirTextThrough() {
        #expect(HostAgentError.agent(code: .pairFailed, message: "Sunshine rejected the PIN").userMessage == "Sunshine rejected the PIN")
        #expect(HostAgentError.transport("ssh: no route").userMessage == "ssh: no route")
        #expect(HostAgentError.malformedOutput("empty output").userMessage == "empty output")
        #expect(HostAgentError.invalidArgument("PIN must be 4 digits").userMessage == "PIN must be 4 digits")
        #expect(HostAgentError.unsupportedSchema(2).userMessage.contains("2"))
    }

    // MARK: English local text

    private func localMessage(
        stdout: String = "", exit: Int32 = 0, stderr: String = "",
        _ body: @Sendable (HostAgentClient) async throws(HostAgentError) -> Void
    ) async -> String {
        let (client, _) = fixture(stdout, exit: exit, stderr: stderr)
        do {
            try await body(client)
            return "no error"
        } catch {
            return error.userMessage
        }
    }

    @Test func locallyGeneratedMessagesNameTheCommandAndAreDistinct() async {
        let start: @Sendable (HostAgentClient) async throws(HostAgentError) -> Void = { try await $0.kvmStart() }
        let pair: @Sendable (HostAgentClient) async throws(HostAgentError) -> Void = {
            try await $0.streamPair(pin: "1234", name: "Diarchy")
        }
        let inactive = Fixtures.kvmStart.replacingOccurrences(of: #""service_active":true"#, with: #""service_active":false"#)
        let messages: [String] = [
            await localMessage(stdout: "", start),
            await localMessage(stdout: "Welcome\n", start),
            await localMessage(stdout: Fixtures.kvmStart + "\n" + Fixtures.kvmStart, start),
            await localMessage(stdout: "{\"schema\":1,\"ok\":true,\"command\":\"kvm start\",\"service_active\":\"x\"}", start),
            await localMessage(stdout: Fixtures.kvmStart, exit: 1, start),
            await localMessage(stdout: inactive, start),
            await localMessage(stdout: #"{"schema":1,"ok":false,"command":"kvm start"}"#, start),
            await localMessage(stdout: #"{"schema":1,"ok":true,"command":"stream pair","paired":false}"#, pair),
        ]
        for message in messages {
            #expect(message.contains("diarchy-host"), "\(message)")
        }
        #expect(Set(messages).count == messages.count)
    }

    @Test func clientSideValidationMessagesAreDistinct() async {
        let (client, _) = fixture(Fixtures.streamPair)
        let messages = [
            await failure { () async throws(HostAgentError) in try await client.kvmAuthorize(fingerprint: "x") },
            await failure { () async throws(HostAgentError) in try await client.streamPair(pin: "1", name: "a") },
            await failure { () async throws(HostAgentError) in try await client.streamPair(pin: "1234", name: "a;") },
            await failure { () async throws(HostAgentError) in try await client.streamStart(output: "DP 1") },
        ].compactMap { $0?.userMessage }
        #expect(messages.count == 4)
        #expect(Set(messages).count == 4)
    }

    @Test func unsupportedSchemaMessageCarriesTheNumber() {
        let message = HostAgentError.unsupportedSchema(2).userMessage
        #expect(message.contains("diarchy-host") && message.contains("2"))
    }

    @Test func sshTrueTimeoutNamesTheCommand() async {
        let runner = FakeCommandRunner()
        runner.when({ _ in true }, return: .failure(.timedOut(command: "diarchy-ssh")))
        let result = await HostAgentClient(runner: runner, paths: Self.paths).sshTrue()
        #expect(result.exitCode == 255)
        #expect(result.stderr == "diarchy-ssh did not respond in time")
    }

    // MARK: stream output and report

    @Test func streamStartSendsTheOutputFlag() async throws {
        let (client, runner) = fixture(Fixtures.streamStart)
        try await client.streamStart(output: "DP-1")
        let call = try #require(runner.calls.first)
        #expect(call.arguments == Self.agent + ["stream", "start", "--output", "DP-1"])
    }

    @Test(arguments: ["", "DP 1", "DP_1", "a;b", "$(x)", "DP-1\nx", String(repeating: "a", count: 33)])
    func streamStartRefusesAnInvalidOutputBeforeSendingAnything(_ output: String) async {
        let (client, runner) = fixture(Fixtures.streamStart)
        let error = await failure { () async throws(HostAgentError) in try await client.streamStart(output: output) }
        guard case .invalidArgument = error else {
            Issue.record("expected invalidArgument, got \(String(describing: error))")
            return
        }
        #expect(runner.calls.isEmpty)
    }

    @Test func reportDecodesMonitorsAndKeepsTheRawLine() async throws {
        let (client, runner) = fixture(Fixtures.report)
        let report = try await client.report()
        #expect(runner.calls.first?.arguments == Self.agent + ["report"])
        #expect(report.rawJSON == Fixtures.report)
        #expect(report.monitors == [
            HostMonitor(name: "eDP-1", internal: true, enabled: true),
            HostMonitor(name: "DP-1", internal: false, enabled: false),
        ])
    }

    @Test func reportWithoutMonitorsDecodesAsEmpty() async throws {
        let bare = #"{"schema":1,"ok":true,"command":"report","future_field":{"x":1}}"#
        let (client, _) = fixture(bare)
        let report = try await client.report()
        #expect(report.monitors.isEmpty)
        #expect(report.rawJSON == bare)
    }

    /// Contract section 2: unknown values are `null`. One monitor with null fields must not lose the whole report.
    @Test func reportWithNullMonitorFieldsKeepsTheNamedMonitors() async throws {
        let line = #"{"schema":1,"ok":true,"command":"report","hyprland":{"version":null,"monitors":["#
            + #"{"name":"DP-1","width":null,"height":null,"refresh":null,"internal":null,"enabled":null},"#
            + #"{"name":null,"internal":true,"enabled":true}]}}"#
        let (client, _) = fixture(line)
        let report = try await client.report()
        #expect(report.monitors == [HostMonitor(name: "DP-1", internal: false, enabled: true)])
    }
}
