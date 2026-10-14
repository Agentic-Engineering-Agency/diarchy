import Foundation

/// The Diagnostics report (spec §8.3): the three Mac doctors followed by `diarchy-host probe`, as plain text. The text
/// is redacted (`Redactor`) because users paste it into public issues.
public struct Diagnostics: Sendable {
    private let paths: Paths
    private let runner: any CommandRunner
    private let isExecutable: @Sendable (String) -> Bool
    private let configExists: @Sendable (URL) -> Bool

    private static let doctors = [
        "diarchy-kvm-client-doctor",
        "diarchy-clipboard-file-transfer-client-doctor",
        "diarchy-client-doctor",
    ]
    private static let localTimeout: TimeInterval = 20
    private static let probeName = "diarchy-host probe"
    private static let mirrorDoctor = "diarchy-client-doctor"

    /// `isExecutable` and `configExists` decide which sections are skipped as not installed or not configured; they
    /// are parameters so tests do not depend on the machine they run on.
    public init(
        paths: Paths, runner: any CommandRunner,
        isExecutable: @escaping @Sendable (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) },
        configExists: @escaping @Sendable (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) }
    ) {
        self.paths = paths
        self.runner = runner
        self.isExecutable = isExecutable
        self.configExists = configExists
    }

    /// Sections in order, each headed `== <name> ==` and holding the command's stdout, stderr and exit code, or
    /// `Error: <reason>` when it could not run, timed out or (the probe) failed. One failing section never stops the
    /// others and nothing throws.
    public func run() async -> String {
        var sections: [String] = []
        for name in Self.doctors { sections.append(await doctorSection(name)) }
        sections.append(await probeSection())
        return Redactor.redact(sections.joined(separator: "\n"))
    }

    private func doctorSection(_ name: String) async -> String {
        guard isExecutable(paths.localBin(name)) else {
            let body = name == Self.mirrorDoctor
                ? "\(name): not installed (streaming is not installed)"
                : "\(name): not installed on this Mac"
            return Self.section(name, body)
        }
        let body: String
        do {
            let result = try await runner.run(
                executable: paths.localBin(name), arguments: [], environment: nil, stdin: nil,
                timeout: Self.localTimeout)
            body = Self.render(result)
        } catch {
            body = Self.render(error, command: name)
        }
        return Self.section(name, body)
    }

    private func probeSection() async -> String {
        let ssh = "diarchy-ssh"
        guard isExecutable(paths.localBin(ssh)) else {
            return Self.section(
                Self.probeName, "Skipped: diarchy-ssh is not installed on this Mac (use \"Install on This Mac\").")
        }
        guard configExists(paths.configURL) else {
            return Self.section(Self.probeName, "Skipped: set up the connection first.")
        }
        let agent = HostAgentClient(runner: runner, paths: paths)
        let body: String
        do throws(HostAgentError) {
            body = try await agent.probe(controllerIP: nil).rawJSON
        } catch {
            return Self.section(Self.probeName, "Error: \(error.userMessage)")
        }
        return Self.section(Self.probeName, body)
    }

    private static func section(_ name: String, _ body: String) -> String {
        "== \(name) ==\n\(body.trimmingCharacters(in: .newlines))\n"
    }

    private static func render(_ result: CommandResult) -> String {
        var lines: [String] = []
        let stdout = result.stdout.trimmingCharacters(in: .newlines)
        let stderr = result.stderr.trimmingCharacters(in: .newlines)
        if !stdout.isEmpty { lines.append(stdout) }
        if !stderr.isEmpty { lines.append(stderr) }
        lines.append("exit code: \(result.exitCode)")
        return lines.joined(separator: "\n")
    }

    private static func render(_ error: any Error, command: String) -> String {
        switch error {
        case let error as CommandError:
            switch error {
            case .launchFailed(_, let message): "Error: \(message)"
            case .timedOut: "Error: \(command) did not respond in time"
            }
        case is CancellationError: "Error: \(command) cancelled"
        default: "Error: \(String(describing: error))"
        }
    }
}
