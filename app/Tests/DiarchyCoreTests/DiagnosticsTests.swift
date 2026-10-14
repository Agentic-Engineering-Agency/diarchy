import Foundation
import Testing

@testable import DiarchyCore

private func ok(_ stdout: String = "", stderr: String = "", exit: Int32 = 0) -> Result<CommandResult, CommandError> {
    .success(CommandResult(exitCode: exit, stdout: stdout, stderr: stderr))
}

private let doctors = [
    "diarchy-kvm-client-doctor",
    "diarchy-clipboard-file-transfer-client-doctor",
    "diarchy-client-doctor",
]
private let probeArguments = ["--", "~/.local/bin/diarchy-host", "probe"]

private struct Rig {
    let runner = FakeCommandRunner()
    let paths = Paths(home: URL(fileURLWithPath: "/tmp/diarchy home"), payload: nil)
    var installed: @Sendable (String) -> Bool = { _ in true }
    var configured = true
    var diagnostics: Diagnostics {
        let configured = configured
        return Diagnostics(paths: paths, runner: runner, isExecutable: installed, configExists: { _ in configured })
    }

    /// Each doctor prints `<name> ok` and the probe returns the `probeFull` envelope, unless an earlier rule matches.
    func allSucceed() {
        for name in doctors { runner.when(name, [], return: ok("\(name) ok\n")) }
        runner.when("diarchy-ssh", probeArguments, return: ok(Fixtures.probeFull + "\n"))
    }

    func headings(_ report: String) -> [String] {
        report.split(separator: "\n").filter { $0.hasPrefix("== ") }.map(String.init)
    }
}

@Suite(.timeLimit(.minutes(1))) struct DiagnosticsTests {
    @Test func sectionsAreInOrderWithTheirHeadings() async {
        let rig = Rig()
        rig.allSucceed()
        let report = await rig.diagnostics.run()
        #expect(rig.headings(report) == [
            "== diarchy-kvm-client-doctor ==", "== diarchy-clipboard-file-transfer-client-doctor ==",
            "== diarchy-client-doctor ==", "== diarchy-host probe ==",
        ])
        #expect(report.contains("diarchy-kvm-client-doctor ok"))
        #expect(report.contains(Fixtures.probeFull))
    }

    @Test func doctorsRunLocallyWithTheTwentySecondLimitAndTheProbeWithFifteen() async {
        let rig = Rig()
        rig.allSucceed()
        _ = await rig.diagnostics.run()
        let calls = rig.runner.calls
        #expect(calls.map(\.executable) == doctors.map { "/tmp/diarchy home/.local/bin/\($0)" } + ["/tmp/diarchy home/.local/bin/diarchy-ssh"])
        #expect(calls.prefix(3).allSatisfy { $0.arguments.isEmpty && $0.timeout == 20 })
        #expect(calls[3].arguments == probeArguments)
        #expect(calls[3].timeout == 15)
    }

    @Test func stdoutStderrAndTheExitCodeOfEachDoctorAreIncluded() async {
        let rig = Rig()
        rig.runner.when(doctors[0], [], return: ok("kvm line\n", stderr: "kvm warning\n", exit: 2))
        rig.allSucceed()
        let report = await rig.diagnostics.run()
        let section = report.components(separatedBy: "== diarchy-clipboard-file-transfer-client-doctor ==")[0]
        #expect(section.contains("kvm line"))
        #expect(section.contains("kvm warning"))
        #expect(section.contains("exit code: 2"))
        #expect(report.contains("exit code: 0"))
    }

    @Test func aTimedOutDoctorYieldsItsOwnSectionAndTheRestStillRun() async {
        let rig = Rig()
        rig.runner.when(doctors[1], [], return: .failure(.timedOut(command: doctors[1])))
        rig.allSucceed()
        let report = await rig.diagnostics.run()
        #expect(rig.headings(report).count == 4)
        let middle = report.components(separatedBy: "== diarchy-clipboard-file-transfer-client-doctor ==")[1]
            .components(separatedBy: "== diarchy-client-doctor ==")[0]
        #expect(middle.contains("Error: diarchy-clipboard-file-transfer-client-doctor did not respond in time"))
        #expect(report.contains("diarchy-client-doctor ok"))
        #expect(report.contains(Fixtures.probeFull))
    }

    @Test func aMissingDoctorYieldsItsLaunchFailureAndTheRestStillRun() async {
        let rig = Rig()
        rig.runner.when(
            doctors[0], [], return: .failure(.launchFailed(command: "/tmp/diarchy home/.local/bin/\(doctors[0])", message: "No such file or directory")))
        rig.allSucceed()
        let report = await rig.diagnostics.run()
        #expect(report.contains("Error: No such file or directory"))
        #expect(rig.headings(report).count == 4)
        #expect(report.contains("diarchy-client-doctor ok"))
    }

    @Test func probeTransportFailureYieldsASectionWithTheReason() async {
        let rig = Rig()
        rig.runner.when("diarchy-ssh", probeArguments, return: ok(stderr: "ssh: connect to host 192.0.2.10 port 22: Connection refused\n", exit: 255))
        for name in doctors { rig.runner.when(name, [], return: ok("\(name) ok\n")) }
        let report = await rig.diagnostics.run()
        let probe = report.components(separatedBy: "== diarchy-host probe ==")[1]
        #expect(probe.contains("Error: ssh: connect to host <redacted> port 22: Connection refused"))
        #expect(report.contains("diarchy-kvm-client-doctor ok"))
    }

    @Test func probeTimeoutAndAgentErrorsAreSectionsToo() async {
        let timeoutRig = Rig()
        for name in doctors { timeoutRig.runner.when(name, [], return: ok()) }
        timeoutRig.runner.when("diarchy-ssh", probeArguments, return: .failure(.timedOut(command: "diarchy-ssh")))
        let timeoutReport = await timeoutRig.diagnostics.run()
        #expect(timeoutReport.contains("Error: diarchy-host probe did not respond in time"))

        let agentRig = Rig()
        for name in doctors { agentRig.runner.when(name, [], return: ok()) }
        agentRig.runner.when(
            "diarchy-ssh", probeArguments,
            return: ok(Fixtures.failure(code: "not_installed", message: "diarchy-host is not installed", command: "probe") + "\n", exit: 1))
        let agentReport = await agentRig.diagnostics.run()
        #expect(agentReport.contains("Error: diarchy-host is not installed"))
    }

    @Test func aDoctorWithNoOutputStillShowsItsExitCode() async {
        let rig = Rig()
        rig.runner.when(doctors[0], [], return: ok(exit: 0))
        rig.allSucceed()
        let report = await rig.diagnostics.run()
        #expect(report.hasPrefix("== diarchy-kvm-client-doctor ==\nexit code: 0\n"))
    }

    @Test func anUninstalledDoctorIsReportedAsNotInstalledAndTheRestStillRun() async {
        let rig = Rig(installed: { !$0.hasSuffix(doctors[0]) && !$0.hasSuffix(doctors[2]) })
        rig.allSucceed()
        let report = await rig.diagnostics.run()
        #expect(rig.headings(report).count == 4)
        #expect(report.contains("diarchy-kvm-client-doctor: not installed on this Mac"))
        #expect(report.contains(
            "diarchy-client-doctor: not installed (streaming is not installed)"))
        #expect(!report.contains("No such file"))
        #expect(report.contains("diarchy-clipboard-file-transfer-client-doctor ok"))
        #expect(!rig.runner.calls.map(\.executable).contains { $0.hasSuffix(doctors[0]) })
        #expect(report.contains(Fixtures.probeFull))
    }

    @Test func theProbeIsSkippedWhenDiarchySshIsNotInstalled() async {
        let rig = Rig(installed: { !$0.hasSuffix("diarchy-ssh") })
        rig.allSucceed()
        let report = await rig.diagnostics.run()
        let probe = report.components(separatedBy: "== diarchy-host probe ==")[1]
        #expect(probe.contains("Skipped: diarchy-ssh is not installed on this Mac (use \"Install on This Mac\")."))
        #expect(!probe.contains("Error"))
        #expect(!rig.runner.calls.map(\.executable).contains { $0.hasSuffix("diarchy-ssh") })
    }

    @Test func theProbeIsSkippedWithoutAConfiguration() async {
        let rig = Rig(configured: false)
        rig.allSucceed()
        let report = await rig.diagnostics.run()
        let probe = report.components(separatedBy: "== diarchy-host probe ==")[1]
        #expect(probe.contains("Skipped: set up the connection first."))
        #expect(!rig.runner.calls.map(\.executable).contains { $0.hasSuffix("diarchy-ssh") })
    }

    @Test func realProcessesRunThroughTheDefaultRunner() async throws {
        try await withTempDirAsync { root in
            let bin = root.appendingPathComponent("home dir/.local/bin", isDirectory: true)
            try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
            for name in doctors {
                let url = bin.appendingPathComponent(name)
                try "#!/bin/sh\necho \"\(name) out\"\necho \"\(name) err\" >&2\nexit 3\n".write(to: url, atomically: true, encoding: .utf8)
                try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
            }
            let ssh = bin.appendingPathComponent("diarchy-ssh")
            try "#!/bin/sh\necho 'ssh: no route' >&2\nexit 255\n".write(to: ssh, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: ssh.path)
            let paths = Paths(home: root.appendingPathComponent("home dir", isDirectory: true), payload: nil)
            let report = await Diagnostics(
                paths: paths, runner: ProcessCommandRunner(), configExists: { _ in true }
            ).run()
            for name in doctors {
                #expect(report.contains("\(name) out"))
                #expect(report.contains("\(name) err"))
            }
            #expect(report.contains("exit code: 3"))
            #expect(report.contains("Error: ssh: no route"))
        }
    }

    @Test func theReportIsRedactedBeforeItIsReturned() async {
        let rig = Rig()
        rig.runner.when(
            doctors[0], [],
            return: ok("host 192.0.2.10 via 100.64.0.10 mac 00:00:5e:00:53:01 /Users/user/.ssh user@example.com\nssid=example-ssid\n"))
        rig.runner.when(doctors[1], [], return: ok())
        rig.runner.when(doctors[2], [], return: ok())
        rig.runner.when("diarchy-ssh", probeArguments, return: ok(Fixtures.probeFull + "\n"))
        let report = await rig.diagnostics.run()
        for leaked in ["192.0.2.10", "100.64.0.10", "00:00:5e:00:53:01", "/Users/user", "user@example.com", "example-ssid"] {
            #expect(!report.contains(leaked), "\(leaked)")
        }
        #expect(report.contains("/Users/<redacted>/.ssh"))
        #expect(report.contains(Fixtures.probeFull))
    }
}
