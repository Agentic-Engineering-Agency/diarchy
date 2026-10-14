import Foundation

public enum HostKeyStatus: Equatable, Sendable {
    /// The pinned file holds a key for the host. The value is its `SHA256:` fingerprint.
    case trusted(String)
    case missing
    /// `diarchy-host-key` could not answer: the reason.
    case unknown(String)
}

public enum HostKeyError: Error, Equatable, Sendable {
    /// `diarchy-host-key` reported a failure: its reason.
    case failed(String)
    /// The fingerprint is not `SHA256:` followed by 43 base64 characters.
    case invalidFingerprint

    /// Text to show after `Error: `.
    public var userMessage: String {
        switch self {
        case .failed(let reason): reason
        case .invalidFingerprint: "The host key fingerprint must look like SHA256:<43 base64 characters>"
        }
    }
}

/// Wraps `~/.local/bin/diarchy-host-key`: scan the host key, show its fingerprint, then pin it once the user agrees.
/// The script prints `key=value` lines; nothing here reads or writes `known_hosts` directly.
public struct HostKeyTrust: Sendable {
    private let paths: Paths
    private let runner: any CommandRunner

    private static let scanTimeout: TimeInterval = 20
    private static let localTimeout: TimeInterval = 10

    public init(paths: Paths, runner: any CommandRunner) {
        self.paths = paths
        self.runner = runner
    }

    /// Scans the host and returns the fingerprint of the key it offers. The key waits as pending until `trust`.
    public func scan() async throws(HostKeyError) -> String {
        let values = try await run(["scan"], timeout: Self.scanTimeout)
        guard values["status"] == "scanned", let fingerprint = values["fingerprint"],
            Self.isFingerprint(fingerprint)
        else { throw .failed("diarchy-host-key scan returned an unexpected answer") }
        return fingerprint
    }

    /// Pins the pending key when its fingerprint equals `fingerprint`.
    public func trust(fingerprint: String) async throws(HostKeyError) {
        guard Self.isFingerprint(fingerprint) else { throw .invalidFingerprint }
        let values = try await run(["trust", fingerprint], timeout: Self.localTimeout)
        guard values["status"] == "trusted" else {
            throw .failed("diarchy-host-key trust returned an unexpected answer")
        }
    }

    /// Never throws: a script that cannot run or answers badly gives `.unknown`.
    public func status() async -> HostKeyStatus {
        let values: [String: String]
        do throws(HostKeyError) {
            values = try await run(["status"], timeout: Self.localTimeout)
        } catch {
            return .unknown(error.userMessage)
        }
        switch values["status"] {
        case "trusted":
            guard let fingerprint = values["fingerprint"], Self.isFingerprint(fingerprint) else {
                return .unknown("diarchy-host-key status returned a trusted host without a fingerprint")
            }
            return .trusted(fingerprint)
        case "missing": return .missing
        default: return .unknown("diarchy-host-key status returned an unexpected answer")
        }
    }

    /// Runs the script and parses its `key=value` lines. `status=failed` and a launch failure throw.
    private func run(_ arguments: [String], timeout: TimeInterval) async throws(HostKeyError) -> [String: String] {
        let name = "diarchy-host-key"
        let result: CommandResult
        do {
            result = try await runner.run(
                executable: paths.localBin(name), arguments: arguments, environment: nil, stdin: nil,
                timeout: timeout)
        } catch let error as CommandError {
            switch error {
            case .launchFailed(_, let message): throw .failed(message)
            case .timedOut: throw .failed("\(name) did not respond in time")
            }
        } catch is CancellationError {
            throw .failed("cancelled")
        } catch {
            throw .failed(String(describing: error))
        }

        var values: [String: String] = [:]
        for line in result.stdout.split(whereSeparator: \.isNewline) {
            guard let equals = line.firstIndex(of: "=") else { continue }
            values[String(line[line.startIndex..<equals])] = String(line[line.index(after: equals)...])
        }
        if values["status"] == "failed" {
            throw .failed(values["reason"] ?? "\(name) failed")
        }
        guard result.succeeded || values["status"] != nil else { throw .failed(result.firstErrorLine) }
        return values
    }

    /// `^SHA256:[A-Za-z0-9+/]{43}$`
    static func isFingerprint(_ value: String) -> Bool {
        let prefix = "SHA256:"
        guard value.hasPrefix(prefix) else { return false }
        let body = value.unicodeScalars.dropFirst(prefix.unicodeScalars.count)
        return body.count == 43
            && body.allSatisfy { ("A"..."Z").contains($0) || ("a"..."z").contains($0) || ("0"..."9").contains($0) || $0 == "+" || $0 == "/" }
    }
}
