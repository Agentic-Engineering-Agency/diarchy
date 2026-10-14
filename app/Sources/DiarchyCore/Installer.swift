import Foundation

public enum InstallerError: Error, Equatable, Sendable {
    /// The app has no bundled payload to install from.
    case noPayload
    /// A payload script is missing, a directory, or lacks the execute bit; the associated value is its path.
    case scriptNotExecutable(String)
    /// `step` is the script name (`install.sh`, …) or `host`; `message` is the first error line of its output.
    case failed(step: String, message: String)
    /// The step outlived its limit; the associated value is the step name.
    case timedOut(String)

    /// Text to show after `Error: `.
    public var userMessage: String {
        switch self {
        case .noPayload: "This copy of Diarchy does not include the installer payload (client/, host/)"
        case .scriptNotExecutable(let path): "The installer script is not executable: \(path)"
        case .failed(let step, let message): "\(step): \(message)"
        case .timedOut(let step): "\(step) did not respond in time"
        }
    }

    /// Text for the "Mac Components" row after a failed install: the script's own failure line, without the step name.
    public var rowMessage: String {
        if case .failed(_, let message) = self { return message }
        return userMessage
    }
}

/// The output lines of one script, safe to fill from the runner's callback.
private final class OutputLines: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var storage: [String] = []
    var all: [String] { lock.lock(); defer { lock.unlock() }; return storage }
    func append(_ line: String) { lock.lock(); storage.append(line); lock.unlock() }
}

/// Runs the bundled installers (spec §7): the Mac scripts directly, the host installer as one tar-over-ssh pipeline.
public struct Installer: Sendable {
    private let paths: Paths
    private let runner: any CommandRunner

    private static let timeout: TimeInterval = 900
    private static let hostStep = "host"
    /// Honoured by `client/install-clipboard-file-transfer.sh`.
    private static let skipSSHPreflight = "DIARCHY_SKIP_SSH_PREFLIGHT"

    public init(paths: Paths, runner: any CommandRunner) {
        self.paths = paths
        self.runner = runner
    }

    // MARK: Mac

    /// Runs `client/install-lan-mouse.sh`, `client/install-clipboard-file-transfer.sh` and, with `includeStreaming`,
    /// `client/install.sh`, stopping at the first failure. The scripts are executed directly so their shebang picks the
    /// interpreter (`/bin/bash` on macOS is 3.2). Every output line goes to `onLine`, after a `==> <script>` header.
    public func installMac(
        includeStreaming: Bool, onLine: @escaping @Sendable (String) -> Void
    ) async throws(InstallerError) {
        guard paths.payload != nil else { throw .noPayload }
        var names = ["install-lan-mouse.sh", "install-clipboard-file-transfer.sh"]
        if includeStreaming { names.append("install.sh") }
        let scripts = names.compactMap { name in paths.payloadPath("client/\(name)").map { (name: name, path: $0) } }

        // Nothing runs when any script is unusable: a half-installed Mac is worse than a clear message.
        for script in scripts where !Self.isExecutableFile(script.path) {
            throw .scriptNotExecutable(script.path)
        }
        // `environment` replaces the inherited one entirely, so the app's own variables are carried over. The SSH
        // preflight of the clipboard installer is skipped: checklist step 3 owns SSH, and step 1 comes first.
        var environment = ProcessInfo.processInfo.environment
        environment[Self.skipSSHPreflight] = "1"
        for script in scripts {
            onLine("==> \(script.name)")
            try await run(
                step: script.name, executable: script.path, arguments: [], environment: environment,
                summarizesOutput: true, onLine: onLine)
        }
        try recordInstalledVersion()
    }

    /// Copies the payload `VERSION` to `Paths.macInstalledVersionURL`, so the checklist can tell an outdated Mac install.
    private func recordInstalledVersion() throws(InstallerError) {
        guard let version = paths.bundledVersion() else { return }
        let url = paths.macInstalledVersionURL
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try (version + "\n").write(to: url, atomically: true, encoding: .utf8)
        } catch {
            throw .failed(step: "VERSION", message: "Could not record the installed version: \(error.localizedDescription)")
        }
    }

    /// What to show when a Mac script fails: the last line that starts with `[fail]`, else the last non-empty line.
    static func failureLine(in lines: [String], requiresFailMarker: Bool = false) -> String? {
        let trimmed = lines.map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        return trimmed.last(where: { $0.hasPrefix("[fail]") }) ?? (requiresFailMarker ? nil : trimmed.last)
    }

    private static func isExecutableFile(_ path: String) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory) && !isDirectory.boolValue
            && FileManager.default.isExecutableFile(atPath: path)
    }

    // MARK: Host

    /// Positional arguments `$1` payload dir, `$2` `diarchy-ssh`, `$3…$5` the remote words. Nothing but this fixed text
    /// is ever shell source, so paths with spaces or quotes cannot change what runs, and `pipefail` makes a failing tar
    /// fail the whole pipeline even when `diarchy-ssh` exits 0. `--no-xattrs` keeps bsdtar's `*.xattr.*` pax headers
    /// (quarantine, provenance) out of the stream: GNU tar on the host would warn about them on stderr. It does not
    /// stop bsdtar from emitting AppleDouble `._*` members for those xattrs; `COPYFILE_DISABLE=1` does.
    private static let hostPipeline =
        #"set -o pipefail; COPYFILE_DISABLE=1 tar --no-xattrs -C "$1" -czf - host VERSION | "$2" -- "$3" "$4" "$5""#

    /// Runs on the host through `bash -c '…'`, so it must stay free of single quotes. It swaps the payload in place
    /// (`payload.new` → `payload`, the previous one removed) and finishes with the host installer.
    public static let hostRemoteScript =
        #"set -euo pipefail; base="$HOME/.local/share/diarchy"; mkdir -p "$base"; rm -rf "$base/payload.new"; "#
        + #"mkdir "$base/payload.new"; tar -xzf - -C "$base/payload.new"; rm -rf "$base/payload.old"; "#
        + #"if [ -e "$base/payload" ]; then mv "$base/payload" "$base/payload.old"; fi; "#
        + #"mv "$base/payload.new" "$base/payload"; rm -rf "$base/payload.old"; bash "$base/payload/host/install-host.sh""#

    /// The words after `diarchy-ssh --`: the script is one single-quoted word for the remote shell, with
    /// `--with-streaming` as the last token of its install line when requested.
    public static func remoteWords(withStreaming: Bool) -> [String] {
        ["bash", "-c", "'" + hostRemoteScript + (withStreaming ? " --with-streaming" : "") + "'"]
    }

    /// Streams `host/` and `VERSION` of the payload to the host over `diarchy-ssh` and runs `install-host.sh` there.
    public func installHost(
        withStreaming: Bool, onLine: @escaping @Sendable (String) -> Void
    ) async throws(InstallerError) {
        guard let payload = paths.payload else { throw .noPayload }
        let arguments =
            ["-c", Self.hostPipeline, "installer", payload.path, paths.localBin("diarchy-ssh")]
            + Self.remoteWords(withStreaming: withStreaming)
        try await run(
            step: Self.hostStep, executable: "/bin/bash", arguments: arguments, summarizesOutput: true,
            requiresFailMarker: true, onLine: onLine)
    }

    // MARK: Running

    private func run(
        step: String, executable: String, arguments: [String], environment: [String: String]? = nil,
        summarizesOutput: Bool = false, requiresFailMarker: Bool = false,
        onLine: @escaping @Sendable (String) -> Void
    ) async throws(InstallerError) {
        let output = OutputLines()
        let forward: @Sendable (String) -> Void = { line in
            output.append(line)
            onLine(line)
        }
        let result: CommandResult
        do {
            result = try await runner.runStreaming(
                executable: executable, arguments: arguments, environment: environment, stdin: nil,
                timeout: Self.timeout, onLine: forward)
        } catch let error as CommandError {
            switch error {
            case .launchFailed(_, let message): throw .failed(step: step, message: message)
            case .timedOut: throw .timedOut(step)
            }
        } catch is CancellationError {
            throw .failed(step: step, message: "cancelled")
        } catch {
            throw .failed(step: step, message: String(describing: error))
        }
        guard result.succeeded else {
            let summary = summarizesOutput ? Self.failureLine(in: output.all, requiresFailMarker: requiresFailMarker) : nil
            throw .failed(step: step, message: summary ?? result.firstErrorLine)
        }
    }
}
