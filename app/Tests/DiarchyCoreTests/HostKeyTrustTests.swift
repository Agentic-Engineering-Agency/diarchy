import Foundation
import Testing

@testable import DiarchyCore

private let fingerprint = "SHA256:" + String(repeating: "A", count: 43)
private let script = "/tmp/diarchy-home/.local/bin/diarchy-host-key"

private func ok(_ stdout: String, exit: Int32 = 0, stderr: String = "") -> Result<CommandResult, CommandError> {
    .success(CommandResult(exitCode: exit, stdout: stdout, stderr: stderr))
}

@Suite struct HostKeyTrustTests {
    private func trust(_ arguments: [String], _ result: Result<CommandResult, CommandError>) -> (HostKeyTrust, FakeCommandRunner) {
        let runner = FakeCommandRunner()
        runner.when("diarchy-host-key", arguments, return: result)
        let paths = Paths(home: URL(fileURLWithPath: "/tmp/diarchy-home"), payload: nil)
        return (HostKeyTrust(paths: paths, runner: runner), runner)
    }

    @Test func scanReturnsTheFingerprint() async throws {
        let (trust, runner) = trust(["scan"], ok("status=scanned\nfingerprint=\(fingerprint)\n"))
        #expect(try await trust.scan() == fingerprint)
        #expect(runner.calls.first?.executable == script)
    }

    @Test func scanFailureCarriesTheScriptReason() async {
        let (trust, _) = trust(["scan"], ok("status=failed\nreason=No route to the host\n", exit: 1))
        await #expect(throws: HostKeyError.failed("No route to the host")) { try await trust.scan() }
    }

    @Test(arguments: [
        ok("status=scanned\n"),
        ok("status=scanned\nfingerprint=SHA256:short\n"),
        ok("garbage\n"),
        ok("", exit: 127, stderr: "not found\n"),
    ])
    func scanRejectsAnAnswerWithoutAValidFingerprint(result: Result<CommandResult, CommandError>) async {
        let (trust, _) = trust(["scan"], result)
        await #expect(throws: HostKeyError.self) { try await trust.scan() }
    }

    @Test func trustSendsTheFingerprintAndAcceptsStatusTrusted() async throws {
        let (trust, runner) = trust(["trust", fingerprint], ok("status=trusted\n"))
        try await trust.trust(fingerprint: fingerprint)
        #expect(runner.calls.first?.arguments == ["trust", fingerprint])
    }

    @Test(arguments: ["", "SHA256:", "MD5:aa", "SHA256:" + String(repeating: "A", count: 42), "SHA256:" + String(repeating: "A", count: 42) + "-", "--x"])
    func trustRefusesAMalformedFingerprintBeforeRunningAnything(value: String) async {
        let (trust, runner) = trust(["trust", value], ok("status=trusted\n"))
        await #expect(throws: HostKeyError.invalidFingerprint) { try await trust.trust(fingerprint: value) }
        #expect(runner.calls.isEmpty)
    }

    @Test func trustFailureCarriesTheScriptReason() async {
        let (trust, _) = trust(["trust", fingerprint], ok("status=failed\nreason=The fingerprint does not match the scanned key.\n", exit: 1))
        await #expect(throws: HostKeyError.failed("The fingerprint does not match the scanned key.")) {
            try await trust.trust(fingerprint: fingerprint)
        }
    }

    @Test func statusMapsTheThreeAnswers() async {
        let (trusted, _) = trust(["status"], ok("status=trusted\nfingerprint=\(fingerprint)\n"))
        #expect(await trusted.status() == .trusted(fingerprint))
        let (missing, _) = trust(["status"], ok("status=missing\n"))
        #expect(await missing.status() == .missing)
        let (broken, _) = trust(["status"], .failure(.launchFailed(command: script, message: "No such file")))
        #expect(await broken.status() == .unknown("No such file"))
        let (noFingerprint, _) = trust(["status"], ok("status=trusted\n"))
        guard case .unknown = await noFingerprint.status() else {
            Issue.record("a trusted answer without a fingerprint must be unknown")
            return
        }
    }
}
