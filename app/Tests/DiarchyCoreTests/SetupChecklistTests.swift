import Foundation
import Testing
@testable import DiarchyCore

@Suite struct SetupChecklistTests {
    private static let agent = "~/.local/bin/diarchy-host"
    private static let macFingerprint = Fixtures.fingerprintBB
    private static let waiting = "Waiting for the earlier steps"
    private static let pinnedFingerprint = "SHA256:" + String(repeating: "A", count: 43)
    private static let titles = [
        "Mac Components", "Configuration", "Host Key", "SSH", "Host Agent", "Platform", "Required Actions",
        "Pairing",
    ]
    /// Executables under `~/.local/bin` that `diarchy-kvm on` and the app call.
    private static let macExecutables = [
        "diarchy-kvm", "diarchy-ssh", "diarchy-kvm-configure", "diarchy-mac-fingerprint", "diarchy-host-key",
        "diarchy-clipboard-bridge", "diarchy-file-portal-receiver", "diarchy-file-portal-send",
    ]
    /// Scripts sourced by `diarchy-ssh` and `diarchy-kvm-configure`: readable, not executable.
    private static let macScripts = ["diarchy-config.sh", "diarchy-route.sh"]
    private static let filePortalApp = "Diarchy File Portal.app"

    private static func defaultConfig() -> DiarchyConfig {
        var config = DiarchyConfig(sshUser: "user", lanHost: "192.0.2.10", tailscaleHost: "100.64.0.10")
        config.hostFingerprint = Fixtures.fingerprintAA
        return config
    }

    private static func ok(_ stdout: String = "", exit: Int32 = 0, stderr: String = "")
        -> Result<CommandResult, CommandError>
    {
        .success(CommandResult(exitCode: exit, stdout: stdout, stderr: stderr))
    }

    /// Everything the runner answers; the defaults describe a fully set-up Mac and Host.
    struct Scenario {
        var config: DiarchyConfig? = SetupChecklistTests.defaultConfig()
        var bundledVersion: String? = "1.0.0"
        /// The version the Mac install recorded; `nil` writes no file.
        var installedMacVersion: String? = "1.0.0"
        var ssh = SetupChecklistTests.ok()
        var version = SetupChecklistTests.ok(Fixtures.version + "\n")
        var probe = SetupChecklistTests.ok(Fixtures.probe(actions: []) + "\n")
        var route = SetupChecklistTests.ok("   route to: 192.0.2.10\n  interface: en0\n")
        var ipconfig = SetupChecklistTests.ok("192.0.2.55\n")
        var macFingerprint = SetupChecklistTests.ok(SetupChecklistTests.macFingerprint + "\n")
        var hostKey = SetupChecklistTests.ok("status=trusted\nfingerprint=" + SetupChecklistTests.pinnedFingerprint + "\n")
        var missing: Set<String> = []
        var nonExecutable: Set<String> = []
        var lanMouseIsFile = false
        var configText: String?
        var macInstallFailure: String?
        var xcodeSelect = SetupChecklistTests.ok("/Library/Developer/CommandLineTools\n")
    }

    private func run(_ scenario: Scenario = Scenario()) async throws -> (ChecklistResult, FakeCommandRunner) {
        try await withTempDirAsync { home in
            let paths = Paths(home: home, payload: nil)
            let fileManager = FileManager.default
            try fileManager.createDirectory(
                at: home.appendingPathComponent(".local/bin"), withIntermediateDirectories: true)
            for name in Self.macExecutables + Self.macScripts where !scenario.missing.contains(name) {
                let path = paths.localBin(name)
                try "#!/bin/sh\n".write(toFile: path, atomically: true, encoding: .utf8)
                let isScript = Self.macScripts.contains(name)
                let mode = isScript
                    ? (scenario.nonExecutable.contains(name) ? 0o000 : 0o644)
                    : (scenario.nonExecutable.contains(name) ? 0o644 : 0o755)
                try fileManager.setAttributes([.posixPermissions: mode], ofItemAtPath: path)
            }
            if !scenario.missing.contains(Self.filePortalApp) {
                let portal = home.appendingPathComponent("Applications/\(Self.filePortalApp)/Contents/MacOS")
                try fileManager.createDirectory(at: portal, withIntermediateDirectories: true)
                let binary = portal.appendingPathComponent("diarchy-file-portal").path
                try "#!/bin/sh\n".write(toFile: binary, atomically: true, encoding: .utf8)
                let mode = scenario.nonExecutable.contains(Self.filePortalApp) ? 0o644 : 0o755
                try fileManager.setAttributes([.posixPermissions: mode], ofItemAtPath: binary)
            }
            if !scenario.missing.contains("Lan Mouse.app") {
                try fileManager.createDirectory(
                    at: paths.lanMouseApp.deletingLastPathComponent(), withIntermediateDirectories: true)
                if scenario.lanMouseIsFile {
                    try "x".write(to: paths.lanMouseApp, atomically: true, encoding: .utf8)
                } else {
                    try fileManager.createDirectory(at: paths.lanMouseApp, withIntermediateDirectories: true)
                }
            }
            if let installed = scenario.installedMacVersion {
                try fileManager.createDirectory(
                    at: paths.macInstalledVersionURL.deletingLastPathComponent(), withIntermediateDirectories: true)
                try (installed + "\n").write(to: paths.macInstalledVersionURL, atomically: true, encoding: .utf8)
            }
            let store = ConfigStore(url: home.appendingPathComponent(".config/diarchy/diarchy.conf"))
            if let text = scenario.configText {
                try fileManager.createDirectory(
                    at: store.url.deletingLastPathComponent(), withIntermediateDirectories: true)
                try text.write(to: store.url, atomically: true, encoding: .utf8)
            } else if let config = scenario.config {
                try store.save(config)
            }

            let runner = FakeCommandRunner()
            let controllerIP = ["--controller-ip", "192.0.2.55"]
            runner.when("diarchy-ssh", ["--", "true"], return: scenario.ssh)
            runner.when("diarchy-ssh", ["--", Self.agent, "version"], return: scenario.version)
            runner.when("diarchy-ssh", ["--", Self.agent, "probe"] + controllerIP, return: scenario.probe)
            runner.when("diarchy-ssh", ["--", Self.agent, "probe"], return: scenario.probe)
            runner.when("route", ["-n", "get", "192.0.2.10"], return: scenario.route)
            runner.when("ipconfig", ["getifaddr", "en0"], return: scenario.ipconfig)
            runner.when("diarchy-mac-fingerprint", [], return: scenario.macFingerprint)
            runner.when("diarchy-host-key", ["status"], return: scenario.hostKey)
            runner.when("xcode-select", ["-p"], return: scenario.xcodeSelect)

            let checklist = SetupChecklist(
                paths: paths, store: store, runner: runner, bundledVersion: scenario.bundledVersion,
                macInstallFailure: scenario.macInstallFailure)
            return (await checklist.run(), runner)
        }
    }

    /// The commands the runner saw, without the host key status call every run makes (it is checked on its own).
    private func names(_ runner: FakeCommandRunner) -> [String] {
        runner.calls.map { call in
            let name = (call.executable as NSString).lastPathComponent
            return ([name] + call.arguments).joined(separator: " ")
        }.filter { $0 != "diarchy-host-key status" }
    }

    private func expectPending(_ result: ChecklistResult, after index: Int) {
        for step in result.steps[(index + 1)...] {
            #expect(step.state == .pending, "\(step.title)")
            #expect(step.message == Self.waiting)
            #expect(step.commands.isEmpty)
            #expect(step.action == nil)
        }
    }

    // MARK: Full green path

    @Test func fullGreenPath() async throws {
        let (result, runner) = try await run()
        #expect(result.steps.count == 8)
        #expect(result.steps.map(\.id) == StepID.allCases)
        #expect(result.steps.map(\.title) == Self.titles)
        #expect(result.steps.allSatisfy { $0.state == .ok && $0.action == nil && $0.commands.isEmpty })
        #expect(result.isComplete)
        #expect(result.config == Self.defaultConfig())
        #expect(result.probe?.agentVersion == "1.0.0")
        #expect(names(runner) == [
            "diarchy-ssh -- true",
            "diarchy-ssh -- \(Self.agent) version",
            "route -n get 192.0.2.10",
            "ipconfig getifaddr en0",
            "diarchy-ssh -- \(Self.agent) probe --controller-ip 192.0.2.55",
            "diarchy-mac-fingerprint",
        ])
    }

    @Test func stepIDsAreNumberedOneToEight() {
        #expect(StepID.allCases.map(\.rawValue) == [1, 2, 3, 4, 5, 6, 7, 8])
    }

    // MARK: Step 3 – Host key

    @Test func aPinnedHostKeyMovesOn() async throws {
        let (result, runner) = try await run()
        #expect(result.steps[2].state == .ok)
        #expect(result.steps[2].message.contains(Self.pinnedFingerprint))
        #expect(result.steps[2].action == nil)
        #expect(runner.calls.contains { $0.arguments == ["status"] })
    }

    @Test func aMissingHostKeyOffersTheConfirmationAndBlocksSSH() async throws {
        var scenario = Scenario()
        scenario.hostKey = Self.ok("status=missing\n")
        let (result, runner) = try await run(scenario)
        let step = result.steps[2]
        #expect(step.state == .actionRequired)
        #expect(step.action == .confirmHostKey)
        #expect(step.message.contains("not confirmed"))
        #expect(step.commands.map(\.command) == ["ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"])
        #expect(step.commands.allSatisfy { !$0.blocking })
        expectPending(result, after: 2)
        #expect(names(runner).isEmpty)
        #expect(!result.isComplete)
    }

    @Test func aHostKeyScriptFailureFailsTheStepWithItsReason() async throws {
        var scenario = Scenario()
        scenario.hostKey = Self.ok("status=failed\nreason=known_hosts is not readable\n", exit: 1)
        let (result, runner) = try await run(scenario)
        let step = result.steps[2]
        #expect(step.state == .failed)
        #expect(step.message == "known_hosts is not readable")
        #expect(step.action == nil)
        expectPending(result, after: 2)
        #expect(names(runner).isEmpty)
    }

    @Test func theHostKeyStepWaitsForAValidConfiguration() async throws {
        var scenario = Scenario()
        scenario.config = nil
        let (result, runner) = try await run(scenario)
        #expect(result.steps[1].state == .failed)
        #expect(result.steps[2].state == .pending)
        #expect(runner.calls.isEmpty)
    }

    // MARK: Step 1 – Mac components

    @Test(
        "a missing item blocks everything after step 1",
        arguments: macExecutables + macScripts + ["Lan Mouse.app", filePortalApp])
    func missingItem(item: String) async throws {
        var scenario = Scenario()
        scenario.missing = [item]
        let (result, runner) = try await run(scenario)
        let step = result.steps[0]
        #expect(step.state == .actionRequired)
        #expect(step.action == .installMac)
        #expect(step.message.contains(item))
        #expect(step.commands.isEmpty)
        expectPending(result, after: 0)
        #expect(runner.calls.isEmpty)
        #expect(!result.isComplete)
        #expect(result.config == nil && result.probe == nil)
    }

    @Test func failedInstallShowsItsFailureOnTheRowAndKeepsTheButton() async throws {
        var scenario = Scenario()
        scenario.missing = ["Lan Mouse.app"]
        scenario.macInstallFailure = "[fail] install-lan-mouse.sh: download failed"
        let (result, _) = try await run(scenario)
        let step = result.steps[0]
        #expect(step.state == .failed)
        #expect(step.message == "[fail] install-lan-mouse.sh: download failed")
        #expect(step.action == .installMac)
        expectPending(result, after: 0)
    }

    @Test func failedInstallWithoutCommandLineToolsTellsTheUserToInstallThem() async throws {
        var scenario = Scenario()
        scenario.missing = ["Lan Mouse.app"]
        scenario.macInstallFailure = "[fail] required Python version is 3.9 or newer"
        scenario.xcodeSelect = Self.ok(exit: 2, stderr: "xcode-select: error: unable to get active developer directory")
        let (result, _) = try await run(scenario)
        let step = result.steps[0]
        #expect(step.message == "Install the Command Line Tools: run `xcode-select --install`, then try again.")
        #expect(step.action == .installMac)
    }

    @Test(
        "a non-executable file counts as missing",
        arguments: macExecutables + [filePortalApp])
    func nonExecutable(item: String) async throws {
        var scenario = Scenario()
        scenario.nonExecutable = [item]
        let (result, runner) = try await run(scenario)
        #expect(result.steps[0].state == .actionRequired)
        #expect(result.steps[0].action == .installMac)
        #expect(result.steps[0].message.contains(item))
        expectPending(result, after: 0)
        #expect(runner.calls.isEmpty)
    }

    @Test func lanMouseThatIsAFileIsMissing() async throws {
        var scenario = Scenario()
        scenario.lanMouseIsFile = true
        let (result, runner) = try await run(scenario)
        #expect(result.steps[0].state == .actionRequired)
        #expect(result.steps[0].message.contains("Lan Mouse.app"))
        #expect(runner.calls.isEmpty)
    }

    @Test func everythingMissingListsEveryItem() async throws {
        var scenario = Scenario()
        scenario.missing = Set(Self.macExecutables + Self.macScripts + ["Lan Mouse.app", Self.filePortalApp])
        let (result, _) = try await run(scenario)
        for item in Self.macExecutables + Self.macScripts + ["Lan Mouse.app", Self.filePortalApp] {
            #expect(result.steps[0].message.contains(item), "\(item)")
        }
    }

    @Test(
        "an unreadable script counts as missing", arguments: macScripts)
    func unreadableScript(item: String) async throws {
        var scenario = Scenario()
        scenario.nonExecutable = [item]
        let (result, runner) = try await run(scenario)
        #expect(result.steps[0].state == .actionRequired)
        #expect(result.steps[0].action == .installMac)
        #expect(result.steps[0].message.contains(item))
        expectPending(result, after: 0)
        #expect(runner.calls.isEmpty)
    }

    @Test func scriptsNeedOnlyBeReadable() async throws {
        let (result, _) = try await run()
        #expect(result.steps[0].state == .ok)
    }

    @Test func streamingOnlyPiecesAreNotRequired() async throws {
        // The fixture never creates diarchy-stream, diarchy-client-doctor or Moonlight.
        let (result, _) = try await run()
        #expect(result.steps[0].state == .ok)
        #expect(!result.steps[0].message.contains("diarchy-stream"))
        #expect(!result.steps[0].message.contains("Moonlight"))
    }

    // MARK: Step 2 – configuration

    @Test func missingConfigurationFails() async throws {
        var scenario = Scenario()
        scenario.config = nil
        let (result, runner) = try await run(scenario)
        let step = result.steps[1]
        #expect(result.steps[0].state == .ok)
        #expect(step.state == .failed)
        #expect(step.message == "diarchy-config: Diarchy is not configured (open the app to set it up)")
        #expect(step.action == nil)
        expectPending(result, after: 1)
        #expect(runner.calls.isEmpty)
        #expect(result.config == nil)
    }

    @Test func invalidConfigurationKeepsTheLoaderReason() async throws {
        let text = "# Managed by Diarchy\nDIARCHY_SSH_USER=user\nDIARCHY_LAN_HOST=192.0.2.10\nDIARCHY_SSH_PORT=0\n"
        let expected: String
        do {
            _ = try ConfigCodec.parse(text)
            Issue.record("fixture must be invalid")
            return
        } catch {
            expected = "\(error)"
        }
        #expect(expected.hasPrefix("diarchy-config: "))
        var scenario = Scenario()
        scenario.configText = text
        let (result, runner) = try await run(scenario)
        #expect(result.steps[1].state == .failed)
        #expect(result.steps[1].message == expected)
        expectPending(result, after: 1)
        #expect(runner.calls.isEmpty)
    }

    @Test func unreadableConfigurationFails() async throws {
        var scenario = Scenario()
        scenario.configText = "not a managed file\n"
        let (result, _) = try await run(scenario)
        #expect(result.steps[1].state == .failed)
        #expect(result.steps[1].message.hasPrefix("diarchy-config: "))
    }

    // MARK: Step 3 – SSH

    private static func config(
        user: String = "user", lan: String = "192.0.2.10", tailscale: String = "", port: Int = 22, identity: String = ""
    ) -> DiarchyConfig {
        var config = DiarchyConfig(sshUser: user, lanHost: lan, tailscaleHost: tailscale)
        config.sshPort = port
        config.sshIdentityFile = identity
        return config
    }

    struct CopyIDCase: Sendable, CustomTestStringConvertible {
        var name: String
        var config: DiarchyConfig
        var command: String?
        var testDescription: String { name }
    }

    static let copyIDCases: [CopyIDCase] = [
        CopyIDCase(name: "no identity", config: config(), command: "ssh-copy-id -p 22 user@192.0.2.10"),
        CopyIDCase(
            name: "identity", config: config(identity: "/Users/a/.ssh/id_ed25519"),
            command: "ssh-copy-id -p 22 -i /Users/a/.ssh/id_ed25519.pub user@192.0.2.10"),
        CopyIDCase(
            name: "identity already .pub", config: config(identity: "/Users/a/.ssh/id_ed25519.pub"),
            command: "ssh-copy-id -p 22 -i /Users/a/.ssh/id_ed25519.pub user@192.0.2.10"),
        CopyIDCase(
            name: "custom port", config: config(port: 2222), command: "ssh-copy-id -p 2222 user@192.0.2.10"),
        CopyIDCase(
            name: "identity with spaces", config: config(identity: "/Users/a b/.ssh/id key"),
            command: "ssh-copy-id -p 22 -i '/Users/a b/.ssh/id key.pub' user@192.0.2.10"),
        CopyIDCase(
            name: "identity with spaces already .pub", config: config(identity: "/Users/a b/.ssh/id key.pub"),
            command: "ssh-copy-id -p 22 -i '/Users/a b/.ssh/id key.pub' user@192.0.2.10"),
        CopyIDCase(
            name: "identity with shell metacharacters", config: config(identity: "/tmp/$HOME/`id`;x"),
            command: "ssh-copy-id -p 22 -i '/tmp/$HOME/`id`;x.pub' user@192.0.2.10"),
        CopyIDCase(
            name: "identity with a quote", config: config(identity: "/tmp/it's"),
            command: "ssh-copy-id -p 22 -i '/tmp/it'\\''s.pub' user@192.0.2.10"),
        CopyIDCase(
            name: "identity with safe punctuation only", config: config(identity: "/a/b_c-d.e/f@g%h+i=j:k,l"),
            command: "ssh-copy-id -p 22 -i /a/b_c-d.e/f@g%h+i=j:k,l.pub user@192.0.2.10"),
        CopyIDCase(
            name: "tailscale only", config: config(lan: "", tailscale: "100.64.0.10"),
            command: "ssh-copy-id -p 22 user@100.64.0.10"),
        CopyIDCase(
            name: "lan wins over tailscale", config: config(tailscale: "100.64.0.10"),
            command: "ssh-copy-id -p 22 user@192.0.2.10"),
        CopyIDCase(name: "no host", config: config(lan: ""), command: nil),
    ]

    @Test("ssh-copy-id display command", arguments: copyIDCases)
    func sshCopyIDCommand(_ item: CopyIDCase) {
        #expect(SetupChecklist.sshCopyIDCommand(config: item.config) == item.command)
    }

    @Test func sshOkMovesOn() async throws {
        let (result, runner) = try await run()
        #expect(result.steps[3].state == .ok)
        #expect(names(runner).first == "diarchy-ssh -- true")
    }

    @Test func permissionDeniedOffersSSHCopyID() async throws {
        var scenario = Scenario()
        scenario.config = Self.config(identity: "/Users/a b/.ssh/id key")
        scenario.ssh = Self.ok(exit: 255, stderr: "user@192.0.2.10: Permission denied (publickey).\n")
        let (result, runner) = try await run(scenario)
        let step = result.steps[3]
        #expect(step.state == .actionRequired)
        #expect(step.action == nil)
        #expect(step.commands.count == 1)
        #expect(step.commands.first?.command == "ssh-copy-id -p 22 -i '/Users/a b/.ssh/id key.pub' user@192.0.2.10")
        #expect(step.commands.first?.blocking == true)
        expectPending(result, after: 3)
        #expect(names(runner) == ["diarchy-ssh -- true"])
    }

    @Test func permissionDeniedOnTailscaleOnlyConfigUsesTheTailscaleHost() async throws {
        var scenario = Scenario()
        scenario.config = Self.config(lan: "", tailscale: "100.64.0.10", port: 2222)
        scenario.ssh = Self.ok(exit: 255, stderr: "Permission denied (publickey).")
        let (result, _) = try await run(scenario)
        #expect(result.steps[3].commands.map(\.command) == ["ssh-copy-id -p 2222 user@100.64.0.10"])
    }

    // Review Focus 5: an unreachable host must surface the route reason, never an ssh-copy-id hint.
    @Test func unreachableHostFailsWithTheRouteReasonAndNoHint() async throws {
        let reason = "diarchy-ssh: Host is unreachable on LAN and Tailscale"
        var scenario = Scenario()
        scenario.ssh = Self.ok(exit: 255, stderr: reason + "\n")
        let (result, runner) = try await run(scenario)
        let step = result.steps[3]
        #expect(step.state == .failed)
        #expect(step.message == reason)
        #expect(step.commands.isEmpty)
        #expect(step.action == nil)
        #expect(!step.message.contains("ssh-copy-id"))
        expectPending(result, after: 3)
        #expect(names(runner) == ["diarchy-ssh -- true"])
    }

    @Test func hostKeyVerificationFailureIsFailed() async throws {
        var scenario = Scenario()
        scenario.ssh = Self.ok(exit: 255, stderr: "Host key verification failed.\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[3].state == .failed)
        #expect(result.steps[3].message == Self.changedHostKeyMessage)
        #expect(result.steps[3].commands.isEmpty)
        #expect(result.steps[3].action == nil)
    }

    // The full banner OpenSSH prints for a changed host key: the first line is only "@" characters.
    private static let changedKeyBanner = """
        @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
        @    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
        @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
        IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!
        Someone could be eavesdropping on you right now (man-in-the-middle attack)!
        It is also possible that a host key has just been changed.
        The fingerprint for the ED25519 key sent by the remote host is
        SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG.
        Please contact your system administrator.
        Add correct host key in /Users/a b/.ssh/known_hosts to get rid of this message.
        Offending ED25519 key in /Users/a b/.ssh/known_hosts:7
        Host key for 192.0.2.10 has changed and you have requested strict checking.
        Host key verification failed.

        """

    private static let changedHostKeyMessage =
        "The host's SSH key changed (possible impersonation). Diarchy will not connect until you verify it."

    @Test func changedHostKeyBannerGivesAClearMessageAndNoHint() async throws {
        var scenario = Scenario()
        scenario.ssh = Self.ok(exit: 255, stderr: Self.changedKeyBanner)
        let (result, runner) = try await run(scenario)
        let step = result.steps[3]
        #expect(step.state == .failed)
        #expect(step.message == Self.changedHostKeyMessage)
        #expect(step.commands.isEmpty)
        #expect(step.action == nil)
        expectPending(result, after: 3)
        #expect(names(runner) == ["diarchy-ssh -- true"])
    }

    @Test func changedHostKeyWithoutTheFinalLineIsStillRecognised() async throws {
        var scenario = Scenario()
        scenario.ssh = Self.ok(
            exit: 255, stderr: "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[3].message == Self.changedHostKeyMessage)
    }

    @Test func permissionDeniedWithoutExit255IsFailed() async throws {
        var scenario = Scenario()
        scenario.ssh = Self.ok(exit: 1, stderr: "Permission denied while reading the identity\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[3].state == .failed)
        #expect(result.steps[3].commands.isEmpty)
    }

    @Test func otherSSHFailuresUseTheFirstErrorLine() async throws {
        var scenario = Scenario()
        scenario.ssh = Self.ok(exit: 2, stderr: "\ndiarchy-ssh: identity file missing\nsecond line\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[3].state == .failed)
        #expect(result.steps[3].message == "diarchy-ssh: identity file missing")
    }

    @Test func sshTimeoutIsFailedWithoutAHint() async throws {
        var scenario = Scenario()
        scenario.ssh = .failure(.timedOut(command: "diarchy-ssh"))
        let (result, _) = try await run(scenario)
        #expect(result.steps[3].state == .failed)
        #expect(result.steps[3].message.contains("diarchy-ssh"))
        #expect(result.steps[3].commands.isEmpty)
    }

    @Test func anOutdatedMacInstallRequiresTheMacInstallAgain() async throws {
        var scenario = Scenario()
        scenario.installedMacVersion = "0.9.0"
        let (result, _) = try await run(scenario)
        let step = result.steps[0]
        #expect(step.state == .actionRequired)
        #expect(step.action == .installMac)
        #expect(step.message.contains("0.9.0") && step.message.contains("1.0.0"))
        expectPending(result, after: 0)
    }

    @Test func aMacInstallWithoutAVersionRecordRequiresTheMacInstallAgain() async throws {
        var scenario = Scenario()
        scenario.installedMacVersion = nil
        let (result, _) = try await run(scenario)
        #expect(result.steps[0].state == .actionRequired)
        #expect(result.steps[0].action == .installMac)
        #expect(result.steps[0].message.contains("unknown"))
    }

    // MARK: Step 4 – host agent

    @Test func agentVersionMismatchOffersInstall() async throws {
        var scenario = Scenario()
        scenario.bundledVersion = "1.1.0"
        scenario.installedMacVersion = "1.1.0"
        let (result, runner) = try await run(scenario)
        let step = result.steps[4]
        #expect(step.state == .actionRequired)
        #expect(step.action == .installHost)
        #expect(step.message.contains("1.0.0") && step.message.contains("1.1.0"))
        expectPending(result, after: 4)
        #expect(names(runner) == ["diarchy-ssh -- true", "diarchy-ssh -- \(Self.agent) version"])
    }

    @Test func unknownBundledVersionNeverMatches() async throws {
        var scenario = Scenario()
        scenario.bundledVersion = nil
        let (result, _) = try await run(scenario)
        #expect(result.steps[4].state == .actionRequired)
        #expect(result.steps[4].action == .installHost)
    }

    @Test func notInstalledEnvelopeOffersInstall() async throws {
        var scenario = Scenario()
        scenario.version = Self.ok(
            Fixtures.failure(code: "not_installed", message: "diarchy-host is not installed", command: "version") + "\n",
            exit: 1)
        let (result, _) = try await run(scenario)
        #expect(result.steps[4].state == .actionRequired)
        #expect(result.steps[4].action == .installHost)
        expectPending(result, after: 4)
    }

    @Test func commandNotFoundOffersInstall() async throws {
        var scenario = Scenario()
        scenario.version = Self.ok(exit: 127, stderr: "bash: line 1: ~/.local/bin/diarchy-host: command not found\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[4].state == .actionRequired)
        #expect(result.steps[4].action == .installHost)
    }

    @Test func malformedOutputOffersInstall() async throws {
        var scenario = Scenario()
        scenario.version = Self.ok("this is not json\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[4].state == .actionRequired)
        #expect(result.steps[4].action == .installHost)
    }

    @Test func unsupportedSchemaOffersInstall() async throws {
        var scenario = Scenario()
        scenario.version = Self.ok(#"{"schema":2,"ok":true,"command":"version","agent_version":"9.0.0"}"# + "\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[4].state == .actionRequired)
        #expect(result.steps[4].action == .installHost)
    }

    @Test func transportFailureOfTheVersionCallFails() async throws {
        var scenario = Scenario()
        scenario.version = Self.ok(exit: 255, stderr: "ssh: connect to host 192.0.2.10 port 22: Connection refused\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[4].state == .failed)
        #expect(result.steps[4].message == "ssh: connect to host 192.0.2.10 port 22: Connection refused")
        #expect(result.steps[4].action == nil)
        expectPending(result, after: 4)
    }

    @Test func versionTimeoutFails() async throws {
        var scenario = Scenario()
        scenario.version = .failure(.timedOut(command: "diarchy-ssh"))
        let (result, _) = try await run(scenario)
        #expect(result.steps[4].state == .failed)
        #expect(result.steps[4].message == "diarchy-host version did not respond in time")
        #expect(result.steps[4].action == nil)
    }

    @Test func otherAgentErrorsFail() async throws {
        var scenario = Scenario()
        scenario.version = Self.ok(
            Fixtures.failure(code: "internal", message: "unexpected KeyError", command: "version") + "\n", exit: 1)
        let (result, _) = try await run(scenario)
        #expect(result.steps[4].state == .failed)
        #expect(result.steps[4].message == "unexpected KeyError")
        #expect(result.steps[4].action == nil)
    }

    // MARK: Step 5 – platform

    @Test func unsupportedPlatformListsEveryReason() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(Fixtures.probeUnsupported + "\n")
        let (result, runner) = try await run(scenario)
        let step = result.steps[5]
        #expect(step.state == .failed)
        #expect(step.message == "host is not x86_64; no live Hyprland instance")
        #expect(step.action == nil)
        #expect(step.commands.isEmpty)
        expectPending(result, after: 5)
        #expect(!names(runner).contains("diarchy-mac-fingerprint"))
        #expect(result.probe?.platform.supported == false)
    }

    @Test func probeFailureFailsWithTheUserMessage() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(exit: 255, stderr: "Connection reset by peer\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[5].state == .failed)
        #expect(result.steps[5].message == "Connection reset by peer")
        #expect(result.probe == nil)
        expectPending(result, after: 5)
    }

    @Test func probeReceivesTheResolvedControllerIP() async throws {
        let (_, runner) = try await run()
        let probe = try #require(runner.calls.first { $0.arguments.contains("probe") })
        #expect(probe.arguments == ["--", Self.agent, "probe", "--controller-ip", "192.0.2.55"])
    }

    @Test func probeOmitsControllerIPWithoutALanHost() async throws {
        var scenario = Scenario()
        scenario.config = Self.config(lan: "", tailscale: "100.64.0.10")
        let (result, runner) = try await run(scenario)
        #expect(result.steps[5].state == .ok)
        let probe = try #require(runner.calls.first { $0.arguments.contains("probe") })
        #expect(probe.arguments == ["--", Self.agent, "probe"])
        #expect(!names(runner).contains { $0.hasPrefix("route") || $0.hasPrefix("ipconfig") })
    }

    @Test func probeOmitsControllerIPWhenNoAddressIsFound() async throws {
        var scenario = Scenario()
        scenario.ipconfig = Self.ok(exit: 1)
        let (result, runner) = try await run(scenario)
        #expect(result.steps[5].state == .ok)
        let probe = try #require(runner.calls.first { $0.arguments.contains("probe") })
        #expect(probe.arguments == ["--", Self.agent, "probe"])
    }

    @Test func theProbeNeverAsksForStreaming() async throws {
        let (_, runner) = try await run()
        #expect(runner.calls.allSatisfy { !$0.arguments.contains("--streaming") })
    }

    // MARK: Step 6 – required actions

    private func probe(_ actions: [String]) -> String {
        Fixtures.probe(actions: actions) + "\n"
    }

    @Test func requiredActionBlocksAndListsItsCommand() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(probe([Fixtures.installPackages]))
        let (result, runner) = try await run(scenario)
        let step = result.steps[6]
        #expect(step.state == .actionRequired)
        #expect(step.action == nil)
        #expect(step.commands == [
            CommandHint(
                title: "Install missing packages",
                command: "sudo pacman -S --needed wl-clipboard rsync", blocking: true)
        ])
        expectPending(result, after: 6)
        #expect(!names(runner).contains("diarchy-mac-fingerprint"))
    }

    @Test func recommendedActionsAreListedButDoNotBlock() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(probe([Fixtures.firewallLAN, Fixtures.firewallTailscale]))
        let (result, runner) = try await run(scenario)
        let step = result.steps[6]
        #expect(step.state == .ok)
        #expect(step.commands.map(\.blocking) == [false, false])
        #expect(step.commands.map(\.title) == [
            "Allow Lan Mouse through ufw (LAN)",
            "Allow Lan Mouse through ufw (Tailscale)",
        ])
        #expect(step.commands.map(\.command) == [
            "sudo ufw allow proto udp from 192.0.2.5 to any port 4242",
            "sudo ufw allow in on tailscale0 to any port 4242 proto udp",
        ])
        #expect(result.steps[7].state == .ok)
        #expect(result.isComplete)
        #expect(names(runner).contains("diarchy-mac-fingerprint"))
    }

    @Test func requiredAndRecommendedAreBothListed() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(probe([Fixtures.firewallLAN, Fixtures.installPackages]))
        let (result, _) = try await run(scenario)
        let step = result.steps[6]
        #expect(step.state == .actionRequired)
        #expect(step.commands.map(\.blocking) == [false, true])
        #expect(step.commands.map(\.command) == [
            "sudo ufw allow proto udp from 192.0.2.5 to any port 4242",
            "sudo pacman -S --needed wl-clipboard rsync",
        ])
    }

    @Test func unknownRequiredActionBlocksWithTheAgentsTitle() async throws {
        var scenario = Scenario()
        let unknown = Fixtures.unknownAction.replacingOccurrences(of: "recommended", with: "required")
        scenario.probe = Self.ok(probe([unknown]))
        let (result, _) = try await run(scenario)
        #expect(result.steps[6].state == .actionRequired)
        #expect(result.steps[6].commands == [CommandHint(title: "A future action", command: "true", blocking: true)])
    }

    @Test func streamingActionsNeverBlockEvenWhenRequired() async throws {
        var scenario = Scenario()
        let lid = Fixtures.lidPolicy.replacingOccurrences(of: "recommended", with: "required")
        scenario.probe = Self.ok(probe([Fixtures.uinputAccess, lid]))
        let (result, _) = try await run(scenario)
        let step = result.steps[6]
        #expect(step.state == .ok)
        #expect(step.commands.map(\.blocking) == [false, false])
        #expect(result.steps[7].state == .ok)
        #expect(result.isComplete)
    }

    @Test func theStreamingFirewallActionNeverBlocksEvenWhenRequired() async throws {
        var scenario = Scenario()
        let streamingFirewall = Fixtures.firewallStreamingLAN.replacingOccurrences(of: "recommended", with: "required")
        scenario.probe = Self.ok(probe([streamingFirewall]))
        let (result, _) = try await run(scenario)
        #expect(result.steps[6].state == .ok)
        #expect(result.steps[6].commands.map(\.blocking) == [false])
        #expect(result.isComplete)
    }

    @Test func noActionsIsOkWithoutHints() async throws {
        let (result, _) = try await run()
        #expect(result.steps[6].state == .ok)
        #expect(result.steps[6].commands.isEmpty)
    }

    // MARK: Step 7 – pairing

    private func pairing(
        macFingerprint: Result<CommandResult, CommandError>? = nil, hostFingerprint: String? = nil
    ) async throws -> (ChecklistResult, FakeCommandRunner) {
        var scenario = Scenario()
        if let macFingerprint { scenario.macFingerprint = macFingerprint }
        if let hostFingerprint {
            var config = Self.defaultConfig()
            config.hostFingerprint = hostFingerprint
            scenario.config = config
        }
        return try await run(scenario)
    }

    @Test func pairedMacAndHostIsOk() async throws {
        let (result, _) = try await pairing()
        #expect(result.steps[7].state == .ok)
        #expect(result.steps[7].action == nil)
        #expect(result.isComplete)
    }

    @Test func fingerprintsAreComparedCaseInsensitively() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(
            Fixtures.probe(actions: [])
                .replacingOccurrences(of: Fixtures.fingerprintAA, with: Fixtures.fingerprintAA.lowercased())
                .replacingOccurrences(of: Fixtures.fingerprintBB, with: Fixtures.fingerprintBB.lowercased()) + "\n")
        scenario.macFingerprint = Self.ok(Fixtures.fingerprintBB.lowercased() + "\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[7].state == .ok)
        #expect(result.isComplete)
    }

    @Test func lowercaseMacFingerprintAgainstUppercaseHostIsAuthorized() async throws {
        let (result, _) = try await pairing(macFingerprint: Self.ok(Fixtures.fingerprintBB.lowercased() + "\n"))
        #expect(result.steps[7].state == .ok)
    }

    @Test func lowercaseHostFingerprintAgainstUppercaseConfigMatches() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(
            Fixtures.probe(actions: [])
                .replacingOccurrences(of: Fixtures.fingerprintAA, with: Fixtures.fingerprintAA.lowercased()) + "\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[7].state == .ok)
    }

    @Test func caseInsensitivityDoesNotHideARealMismatch() async throws {
        var scenario = Scenario()
        scenario.macFingerprint = Self.ok(Fixtures.fingerprintCC.lowercased() + "\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[7].state == .actionRequired)
        #expect(result.steps[7].message.contains("not authorized"))
    }

    @Test(
        "no local certificate",
        arguments: [
            SetupChecklistTests.ok(exit: 1, stderr: "diarchy-mac-fingerprint: certificate not found\n"),
            SetupChecklistTests.ok("\n"),
            .failure(.launchFailed(command: "diarchy-mac-fingerprint", message: "No such file")),
        ])
    func noLocalCertificate(result fingerprint: Result<CommandResult, CommandError>) async throws {
        let (result, _) = try await pairing(macFingerprint: fingerprint)
        let step = result.steps[7]
        #expect(step.state == .actionRequired)
        #expect(step.action == .pair)
        #expect(step.message.contains("no local Lan Mouse certificate"))
        #expect(!result.isComplete)
    }

    @Test func macFingerprintNotAuthorizedOnTheHost() async throws {
        let (result, _) = try await pairing(macFingerprint: Self.ok(Fixtures.fingerprintCC + "\n"))
        let step = result.steps[7]
        #expect(step.state == .actionRequired)
        #expect(step.action == .pair)
        #expect(step.message.contains("not authorized"))
    }

    @Test func missingConfigFingerprintNeedsPairing() async throws {
        let (result, _) = try await pairing(hostFingerprint: "")
        let step = result.steps[7]
        #expect(step.state == .actionRequired)
        #expect(step.action == .pair)
        #expect(step.message.contains("host fingerprint differs"))
    }

    @Test func differentConfigFingerprintNeedsPairing() async throws {
        let (result, _) = try await pairing(hostFingerprint: Fixtures.fingerprintCC)
        let step = result.steps[7]
        #expect(step.state == .actionRequired)
        #expect(step.action == .pair)
        #expect(step.message.contains("host fingerprint differs"))
    }

    @Test func hostWithoutFingerprintNeedsPairing() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(
            Fixtures.probe(actions: []).replacingOccurrences(
                of: "\"host_fingerprint\":\"\(Fixtures.fingerprintAA)\"", with: "\"host_fingerprint\":null") + "\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[7].state == .actionRequired)
        #expect(result.steps[7].action == .pair)
    }

    // MARK: Host components (the gated host install never reached them)

    @Test func missingLanMouseOnTheHostOffersTheHostInstallAtStep6() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(
            Fixtures.probe(actions: []).replacingOccurrences(
                of: #""lan_mouse":{"installed":true"#, with: #""lan_mouse":{"installed":false"#) + "\n")
        let (result, _) = try await run(scenario)
        let step = result.steps[6]
        #expect(step.state == .actionRequired)
        #expect(step.action == .installHost)
        #expect(step.message == "Missing components on the host: Lan Mouse. Install on the host again.")
        #expect(result.steps[7].state == .pending)
    }

    @Test func everyMissingHostComponentIsListed() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(
            Fixtures.probe(actions: [])
                .replacingOccurrences(of: #""lan_mouse":{"installed":true"#, with: #""lan_mouse":{"installed":false"#)
                .replacingOccurrences(of: #""clipboard":{"installed":true}"#, with: #""clipboard":{"installed":false}"#)
                .replacingOccurrences(of: #""file_portal":{"installed":true"#, with: #""file_portal":{"installed":false"#)
                + "\n")
        let (result, _) = try await run(scenario)
        #expect(
            result.steps[6].message
                == "Missing components on the host: Lan Mouse, clipboard, file portal. Install on the host again.")
    }

    @Test func aPendingRequiredActionComesBeforeTheMissingComponentsAndOffersNoInstall() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(
            Fixtures.probe(actions: [Fixtures.installPackages]).replacingOccurrences(
                of: #""lan_mouse":{"installed":true"#, with: #""lan_mouse":{"installed":false"#) + "\n")
        let (result, _) = try await run(scenario)
        #expect(result.steps[6].state == .actionRequired)
        #expect(result.steps[6].action == nil)
        #expect(result.steps[6].message.hasPrefix("Run these commands on the host"))
    }

    // MARK: Ordering

    @Test func aStepNeverRunsOnceAnEarlierOneIsNotOk() async throws {
        var scenario = Scenario()
        scenario.bundledVersion = "2.0.0"
        scenario.installedMacVersion = "2.0.0"
        let (result, runner) = try await run(scenario)
        #expect(result.steps.map(\.state) == [.ok, .ok, .ok, .ok, .actionRequired, .pending, .pending, .pending])
        #expect(runner.calls.count == 3)
        #expect(runner.spawns.isEmpty)
    }

    @Test func resultsKeepTheDataGatheredBeforeTheFirstFailure() async throws {
        var scenario = Scenario()
        scenario.probe = Self.ok(probe([Fixtures.installPackages]))
        let (result, _) = try await run(scenario)
        #expect(result.config == Self.defaultConfig())
        #expect(result.probe?.requiredActions.count == 1)
        #expect(result.steps.map(\.state) == [.ok, .ok, .ok, .ok, .ok, .ok, .actionRequired, .pending])
    }
}
