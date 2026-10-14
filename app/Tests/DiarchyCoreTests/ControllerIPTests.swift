import Foundation
import Testing
@testable import DiarchyCore

@Suite struct ControllerIPTests {
    private static let routeOutput = """
           route to: 192.0.2.10
        destination: 192.0.2.10
          interface: en0
              flags: <UP,HOST,DONE,LLINFO,WASCLONED>
         recvpipe  sendpipe  ssthresh  rtt,msec    rttvar  hopcount      mtu     expire
               0         0         0         0         0         0      1500      1181

        """

    private func runner(
        route: CommandResult = CommandResult(exitCode: 0, stdout: routeOutput),
        ipconfig: CommandResult? = CommandResult(exitCode: 0, stdout: "192.0.2.55\n")
    ) -> FakeCommandRunner {
        let runner = FakeCommandRunner()
        runner.when("route", ["-n", "get", "192.0.2.10"], return: .success(route))
        if let ipconfig { runner.when("ipconfig", ["getifaddr", "en0"], return: .success(ipconfig)) }
        return runner
    }

    // MARK: interface(fromRouteGet:)

    @Test func parsesInterfaceFromRealRouteOutput() {
        #expect(ControllerIP.interface(fromRouteGet: Self.routeOutput) == "en0")
    }

    @Test func routeOutputWithoutInterfaceYieldsNil() {
        let output = "   route to: 192.0.2.10\ndestination: 192.0.2.10\n      flags: <UP,HOST,DONE>\n"
        #expect(ControllerIP.interface(fromRouteGet: output) == nil)
        #expect(ControllerIP.interface(fromRouteGet: "") == nil)
        #expect(ControllerIP.interface(fromRouteGet: "interface:   \n") == nil)
    }

    // MARK: resolve

    @Test func resolvesTheIPv4OfTheRoutingInterface() async {
        let runner = runner()
        let ip = await ControllerIP.resolve(lanHost: "192.0.2.10", runner: runner)
        #expect(ip == "192.0.2.55")
        #expect(runner.calls.map(\.commandLine) == [
            "/sbin/route -n get 192.0.2.10", "/usr/sbin/ipconfig getifaddr en0",
        ])
    }

    @Test func emptyLanHostRunsNothing() async {
        let runner = FakeCommandRunner()
        let ip = await ControllerIP.resolve(lanHost: "", runner: runner)
        #expect(ip == nil)
        #expect(runner.calls.isEmpty)
    }

    @Test(
        "ipconfig results that are not an IPv4 address",
        arguments: [
            CommandResult(exitCode: 1, stdout: "192.0.2.55\n"),
            CommandResult(exitCode: 0, stdout: ""),
            CommandResult(exitCode: 0, stdout: "\n"),
            CommandResult(exitCode: 0, stdout: "fe80::1\n"),
            CommandResult(exitCode: 0, stdout: "192.0.2.999\n"),
            CommandResult(exitCode: 0, stdout: "192.0.2.55\n192.0.2.56\n"),
        ])
    func rejectsInvalidAddress(ipconfig: CommandResult) async {
        let ip = await ControllerIP.resolve(lanHost: "192.0.2.10", runner: runner(ipconfig: ipconfig))
        #expect(ip == nil)
    }

    @Test func failingRouteYieldsNilAndSkipsIpconfig() async {
        let runner = runner(route: CommandResult(exitCode: 1, stderr: "route: writing to routing socket: not in table"))
        let ip = await ControllerIP.resolve(lanHost: "192.0.2.10", runner: runner)
        #expect(ip == nil)
        #expect(runner.calls.count == 1)
    }

    @Test func routeWithoutInterfaceSkipsIpconfig() async {
        let runner = runner(route: CommandResult(exitCode: 0, stdout: "   route to: 192.0.2.10\n"))
        let ip = await ControllerIP.resolve(lanHost: "192.0.2.10", runner: runner)
        #expect(ip == nil)
        #expect(runner.calls.count == 1)
    }

    @Test func runnerErrorsYieldNil() async {
        let runner = FakeCommandRunner()
        runner.when("route", ["-n", "get", "192.0.2.10"], return: .failure(.timedOut(command: "route")))
        #expect(await ControllerIP.resolve(lanHost: "192.0.2.10", runner: runner) == nil)

        let second = FakeCommandRunner()
        second.when("route", ["-n", "get", "192.0.2.10"], return: .success(CommandResult(exitCode: 0, stdout: Self.routeOutput)))
        second.when("ipconfig", ["getifaddr", "en0"], return: .failure(.launchFailed(command: "ipconfig", message: "x")))
        #expect(await ControllerIP.resolve(lanHost: "192.0.2.10", runner: second) == nil)
    }
}
