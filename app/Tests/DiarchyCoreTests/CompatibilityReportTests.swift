import Foundation
import Testing

@testable import DiarchyCore

private let mac = MacFacts(
    diarchyVersion: "0.1.0-beta.1", modelIdentifier: "Mac15,3", chip: "Apple M3", macOSVersion: "15.4.1",
    moonlightVersion: "6.1.0", lanMouseVersion: "0.11.0", tailscaleInstalled: true)

private func ok(_ stdout: String) -> Result<CommandResult, CommandError> {
    .success(CommandResult(exitCode: 0, stdout: stdout))
}

@Suite struct CompatibilityReportTests {
    @Test func fieldsAreInContractOrder() {
        let text = CompatibilityReport.render(mac: mac, route: "lan", hostReportJSON: #"{"schema":1}"#)
        let labels = [
            "Diarchy version", "Mac model identifier", "Chip", "macOS version", "Moonlight version",
            "Lan Mouse version", "Tailscale installed", "Route used (lan/tailscale/none)",
        ]
        var cursor = text.startIndex
        for label in labels {
            guard let range = text.range(of: "- \(label): ", range: cursor..<text.endIndex) else {
                Issue.record("missing or out of order: \(label)")
                return
            }
            cursor = range.upperBound
        }
        let block = text.range(of: "```json")
        #expect(block != nil)
        #expect(block.map { $0.lowerBound > cursor } == true)
        #expect(text.contains("- Diarchy version: 0.1.0-beta.1\n"))
        #expect(text.contains("- Tailscale installed: yes\n"))
        #expect(text.contains("- Route used (lan/tailscale/none): lan\n"))
        #expect(text.hasSuffix("```"))
    }

    @Test func anUnreachableHostEndsTheReportWithOneLine() {
        let text = CompatibilityReport.render(mac: mac, route: "none", hostReportJSON: nil)
        #expect(text.hasSuffix("\nHost: not reachable"))
        #expect(!text.contains("```"))
    }

    @Test func theHostJSONAndTheMacFieldsAreRedacted() {
        var facts = mac
        facts.chip = "Apple M3 at /Users/user"
        let host = #"{"network":{"ssid":"example-ssid","ip":"192.0.2.10","tailscale":"100.64.0.10","mac":"00:00:5e:00:53:01","who":"user@example.com"}}"#
        let text = CompatibilityReport.render(mac: facts, route: "lan", hostReportJSON: host)
        for leaked in ["192.0.2.10", "100.64.0.10", "00:00:5e:00:53:01", "/Users/user", "user@example.com", "example-ssid"] {
            #expect(!text.contains(leaked), "\(leaked)")
        }
    }

    // MARK: Reporter

    private func reporter(
        home: URL, runner: FakeCommandRunner, tailscale: Bool = true
    ) -> CompatibilityReporter {
        CompatibilityReporter(
            paths: Paths(home: home, payload: nil), runner: runner, appVersion: { "0.1.0-beta.1" },
            tailscaleInstalled: { tailscale })
    }

    private func plist(_ app: URL, version: String) throws {
        let contents = app.appendingPathComponent("Contents", isDirectory: true)
        try FileManager.default.createDirectory(at: contents, withIntermediateDirectories: true)
        try (["CFBundleShortVersionString": version] as NSDictionary).write(
            to: contents.appendingPathComponent("Info.plist"))
    }

    @Test func collectsTheMacFactsTheRouteAndTheHostReport() async throws {
        try await withTempDirAsync { home in
            let paths = Paths(home: home, payload: nil)
            try plist(paths.lanMouseApp, version: "0.11.0")
            try plist(home.appendingPathComponent("Applications/Moonlight.app"), version: "6.1.0")
            let runner = FakeCommandRunner()
            runner.when("sysctl", ["-n", "hw.model"], return: ok("Mac15,3\n"))
            runner.when("sysctl", ["-n", "machdep.cpu.brand_string"], return: ok("Apple M3\n"))
            runner.when("sw_vers", ["-productVersion"], return: ok("15.4.1\n"))
            runner.when("diarchy-ssh", ["--print-route"], return: ok("route=tailscale\n"))
            runner.when("diarchy-ssh", ["--", "~/.local/bin/diarchy-host", "report"], return: ok(Fixtures.report + "\n"))
            let text = await reporter(home: home, runner: runner).collect()
            #expect(text.contains("- Mac model identifier: Mac15,3\n"))
            #expect(text.contains("- Chip: Apple M3\n"))
            #expect(text.contains("- macOS version: 15.4.1\n"))
            #expect(text.contains("- Moonlight version: 6.1.0\n"))
            #expect(text.contains("- Lan Mouse version: 0.11.0\n"))
            #expect(text.contains("- Route used (lan/tailscale/none): tailscale\n"))
            #expect(text.contains("\"diarchy_host_version\" : \"0.1.0-beta.1\""))
            #expect(!text.contains("Host: not reachable"))
        }
    }

    @Test func noRouteMeansNoHostCallAndNotReachable() async throws {
        try await withTempDirAsync { home in
            let runner = FakeCommandRunner()
            runner.when("sysctl", ["-n", "hw.model"], return: ok("Mac15,3\n"))
            runner.when("sysctl", ["-n", "machdep.cpu.brand_string"], return: ok("Apple M3\n"))
            runner.when("sw_vers", ["-productVersion"], return: ok("15.4.1\n"))
            runner.when("diarchy-ssh", ["--print-route"], return: ok("route=none\n"))
            let text = await reporter(home: home, runner: runner, tailscale: false).collect()
            #expect(text.contains("- Moonlight version: unknown\n"))
            #expect(text.contains("- Lan Mouse version: unknown\n"))
            #expect(text.contains("- Tailscale installed: no\n"))
            #expect(text.contains("- Route used (lan/tailscale/none): none\n"))
            #expect(text.hasSuffix("Host: not reachable"))
            #expect(!runner.calls.contains { $0.arguments.contains("report") })
        }
    }

    @Test func aFailingHostReportOrFactCommandDegradesInsteadOfThrowing() async throws {
        try await withTempDirAsync { home in
            let runner = FakeCommandRunner()
            runner.when("sysctl", ["-n", "hw.model"], return: .failure(.timedOut(command: "sysctl")))
            runner.when("sysctl", ["-n", "machdep.cpu.brand_string"], return: ok(""))
            runner.when("sw_vers", ["-productVersion"], return: ok("15.4.1\n"))
            runner.when("diarchy-ssh", ["--print-route"], return: ok("route=lan\n"))
            runner.when(
                "diarchy-ssh", ["--", "~/.local/bin/diarchy-host", "report"],
                return: .success(CommandResult(exitCode: 255, stderr: "ssh: no route")))
            let text = await reporter(home: home, runner: runner).collect()
            #expect(text.contains("- Mac model identifier: unknown\n"))
            #expect(text.contains("- Chip: unknown\n"))
            #expect(text.hasSuffix("Host: not reachable"))
        }
    }
}
