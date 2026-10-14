import Foundation
import Testing

@testable import DiarchyCore

@Suite struct ModeControllerStreamOutputTests {
    private func startArguments(config: String?) async throws -> [String]? {
        try await withTempDirAsync { home in
            let paths = Paths(home: home, payload: nil)
            if let config {
                try FileManager.default.createDirectory(
                    at: paths.configURL.deletingLastPathComponent(), withIntermediateDirectories: true)
                try Data(config.utf8).write(to: paths.configURL)
            }
            let runner = FakeCommandRunner()
            runner.when({ _ in true }, return: .success(CommandResult(exitCode: 0, stdout: Fixtures.streamStart + "\n")))
            let child = FakeChildProcess()
            runner.whenSpawn("diarchy-stream", returns: child)
            let controller = ModeController(
                paths: paths, runner: runner, agent: HostAgentClient(runner: runner, paths: paths),
                isExecutable: { _ in true })
            _ = await controller.setMode(.streaming)
            let call = runner.calls.first { $0.arguments.contains("start") }
            _ = await controller.setMode(.off)
            return call?.arguments
        }
    }

    private static let base = """
        # Managed by Diarchy
        DIARCHY_SSH_USER=alice
        DIARCHY_LAN_HOST=192.0.2.10

        """

    @Test func theConfiguredOutputIsSentToTheHost() async throws {
        let arguments = try await startArguments(config: Self.base + "DIARCHY_STREAM_OUTPUT=DP-1\n")
        #expect(arguments == ["--", "~/.local/bin/diarchy-host", "stream", "start", "--output", "DP-1"])
    }

    @Test(arguments: [nil, "not a config\n", "# Managed by Diarchy\nDIARCHY_SSH_USER=alice\n"])
    func aMissingOrBrokenConfigurationSendsAuto(config: String?) async throws {
        let arguments = try await startArguments(config: config)
        #expect(arguments == ["--", "~/.local/bin/diarchy-host", "stream", "start", "--output", "auto"])
    }
}
