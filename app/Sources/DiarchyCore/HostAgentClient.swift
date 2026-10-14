import Foundation

/// Typed wrapper around `diarchy-ssh -- ~/.local/bin/diarchy-host …` (spec §6). Every value that reaches the remote
/// shell is validated here first; nothing is ever interpolated unchecked. Only the agent commands the app calls are
/// exposed; the agent also offers `kvm configure|stop|status` and `stream stop|status` payloads (spec §6.2).
public struct HostAgentClient: Sendable {
    private let runner: any CommandRunner
    private let paths: Paths
    private let timeout: TimeInterval
    private let startTimeout: TimeInterval
    private let pairTimeout: TimeInterval

    /// Remote words that start every agent call. The `~` stays unexpanded: the remote shell expands it.
    private static let agentWords = ["--", "~/.local/bin/diarchy-host"]
    /// Matches the agent's `NAME_RE` (`[A-Za-z0-9 ._-]{1,40}`, host/diarchy_host/stream.py).
    private static let maxNameLength = 40

    /// `timeout` bounds every ssh command; `startTimeout` bounds `stream start`, which returns only once Sunshine
    /// listens (up to 12 s of agent runtime on top of route selection); `pairTimeout` bounds `stream pair`, which the
    /// agent itself retries for up to 20 s while Moonlight's request becomes pending, after route selection has
    /// already used part of the budget.
    public init(
        runner: any CommandRunner, paths: Paths, timeout: TimeInterval = 15, startTimeout: TimeInterval = 30,
        pairTimeout: TimeInterval = 40
    ) {
        self.runner = runner
        self.paths = paths
        self.timeout = timeout
        self.startTimeout = startTimeout
        self.pairTimeout = pairTimeout
    }

    // MARK: Commands

    public func version() async throws(HostAgentError) -> String {
        let payload = try await call(["version"], as: VersionPayload.self).payload
        return payload.agentVersion
    }

    /// `streaming` adds `--streaming`, which makes the agent also report `uinput-access`, `lid-policy`, `firewall-streaming-lan` and `wifi-roaming`.
    public func probe(controllerIP: String?, streaming: Bool = false) async throws(HostAgentError) -> ProbeResult {
        var flags: [String] = []
        if let controllerIP, Self.isIPv4(controllerIP) { flags += ["--controller-ip", controllerIP] }
        if streaming { flags.append("--streaming") }
        var (result, raw) = try await call(["probe"], flags: flags, as: ProbeResult.self)
        result.rawJSON = raw
        return result
    }

    public func kvmAuthorize(fingerprint: String) async throws(HostAgentError) -> String? {
        guard Self.isFingerprint(fingerprint) else {
            throw .invalidArgument("The fingerprint must be 32 uppercase hexadecimal pairs separated by \":\"")
        }
        let payload = try await call(["kvm", "authorize"], flags: ["--fingerprint", fingerprint], as: AuthorizedPayload.self)
            .payload
        return payload.authorized
    }

    public func kvmStart() async throws(HostAgentError) {
        let payload = try await call(["kvm", "start"], as: ServicePayload.self).payload
        guard payload.serviceActive else {
            throw .malformedOutput("diarchy-host kvm start reported service_active=false")
        }
    }

    /// `output` is `auto` or a Hyprland connector name; it becomes `--output <output>`.
    public func streamStart(output: String) async throws(HostAgentError) {
        guard Self.isStreamOutput(output) else {
            throw .invalidArgument("The stream output must be \"auto\" or a monitor name such as DP-1")
        }
        let payload = try await call(
            ["stream", "start"], flags: ["--output", output], timeout: startTimeout, as: ServicePayload.self
        ).payload
        guard payload.serviceActive else {
            throw .malformedOutput("diarchy-host stream start reported service_active=false")
        }
    }

    /// The host's view of the machine: monitors, GPUs, encoder, installed tools. `rawJSON` keeps the whole line.
    public func report() async throws(HostAgentError) -> HostReport {
        var (result, raw) = try await call(["report"], as: HostReport.self)
        result.rawJSON = raw
        return result
    }

    public func streamStop() async throws(HostAgentError) {
        _ = try await call(["stream", "stop"], as: ServicePayload.self)
    }

    /// Best-effort `stream stop` that outlives the app: spawned in its own process group, never awaited.
    public func spawnDetachedStreamStop() {
        _ = try? runner.spawn(
            executable: paths.localBin("diarchy-ssh"), arguments: Self.agentWords + ["stream", "stop"],
            environment: nil)
    }

    public func streamPair(pin: String, name: String) async throws(HostAgentError) {
        guard Self.isPin(pin) else { throw .invalidArgument("The PIN must be exactly 4 digits") }
        guard Self.isPairingName(name) else {
            throw .invalidArgument("The name must be 1 to 40 characters: letters, digits, space, dot, hyphen or underscore")
        }
        let payload = try await call(
            ["stream", "pair"], flags: ["--pin", pin, "--name", "'\(name)'"], timeout: pairTimeout, as: PairPayload.self
        ).payload
        guard payload.paired else {
            throw .malformedOutput("diarchy-host stream pair reported paired=false")
        }
    }

    /// `diarchy-ssh -- true`. Never throws: launch failures and timeouts become exit 255 with the message on stderr.
    public func sshTrue() async -> CommandResult {
        do {
            return try await runner.run(
                executable: paths.localBin("diarchy-ssh"), arguments: ["--", "true"], environment: nil, stdin: nil,
                timeout: timeout)
        } catch let error as CommandError {
            switch error {
            case .launchFailed(_, let message): return CommandResult(exitCode: 255, stderr: message)
            case .timedOut(let command): return CommandResult(exitCode: 255, stderr: "\(command) did not respond in time")
            }
        } catch is CancellationError {
            return CommandResult(exitCode: 255, stderr: "operation cancelled")
        } catch {
            return CommandResult(exitCode: 255, stderr: String(describing: error))
        }
    }

    // MARK: Transport and envelope

    private struct Header: Decodable {
        var schema: Int
        var ok: Bool
        var error: ErrorBody?
    }

    private struct ErrorBody: Decodable {
        var code: String
        var message: String
    }

    private struct VersionPayload: Decodable { var agentVersion: String }
    private struct AuthorizedPayload: Decodable { var authorized: String? }
    private struct ServicePayload: Decodable { var serviceActive: Bool }
    private struct PairPayload: Decodable { var paired: Bool }

    /// Runs `diarchy-host <words> <flags>` and decodes the payload of its success envelope.
    /// `words` is the subcommand (used in timeout messages); `flags` are validated arguments appended after it.
    private func call<Payload: Decodable>(
        _ words: [String], flags: [String] = [], timeout: TimeInterval? = nil, as type: Payload.Type
    ) async throws(HostAgentError) -> (payload: Payload, raw: String) {
        let subcommand = "diarchy-host " + words.joined(separator: " ")
        let result: CommandResult
        do {
            result = try await runner.run(
                executable: paths.localBin("diarchy-ssh"), arguments: Self.agentWords + words + flags,
                environment: nil, stdin: nil, timeout: timeout ?? self.timeout)
        } catch let error as CommandError {
            switch error {
            case .launchFailed(_, let message): throw .transport(message)
            case .timedOut: throw .timedOut(command: subcommand)
            }
        } catch is CancellationError {
            throw .transport("operation cancelled")
        } catch {
            throw .transport(String(describing: error))
        }

        if result.exitCode == 255 { throw .transport(result.firstErrorLine) }

        let envelopes = Self.jsonObjectLines(in: result.stdout)
        guard envelopes.count == 1, let envelope = envelopes.first else {
            if envelopes.isEmpty, result.stdout.allSatisfy(\.isWhitespace) {
                throw .malformedOutput("\(subcommand) returned nothing (\(result.firstErrorLine))")
            }
            throw .malformedOutput(
                envelopes.isEmpty
                    ? "\(subcommand) returned no JSON envelope" : "\(subcommand) returned more than one JSON envelope")
        }
        let line = envelope.line
        if let schema = Self.schema(of: envelope.object) { throw .unsupportedSchema(schema) }
        let data = Data(line.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let header: Header
        do {
            header = try decoder.decode(Header.self, from: data)
        } catch {
            throw .malformedOutput("\(subcommand) returned invalid JSON")
        }
        guard header.schema == 1 else { throw .unsupportedSchema(header.schema) }

        if !header.ok {
            guard let body = header.error else {
                throw .malformedOutput("\(subcommand) reported a failure without an error object")
            }
            throw .agent(code: AgentErrorCode(wire: body.code), message: body.message)
        }
        guard result.exitCode == 0 else {
            throw .malformedOutput("\(subcommand) reported success but exited with code \(result.exitCode)")
        }
        do {
            return (try decoder.decode(Payload.self, from: data), line)
        } catch {
            throw .malformedOutput("\(subcommand) returned unexpected data")
        }
    }

    /// Schema number of an envelope whose other fields may already differ from version 1 (an unsupported schema must
    /// be reported as such rather than as malformed output).
    private static func schema(of object: [String: Any]) -> Int? {
        guard let schema = object["schema"] as? Int, schema != 1 else { return nil }
        return schema
    }

    /// The lines of `stdout` that are JSON objects. The agent prints exactly one; a login banner, an MOTD or an ssh
    /// warning around it is not JSON and is ignored.
    private static func jsonObjectLines(in stdout: String) -> [(line: String, object: [String: Any])] {
        stdout.split(whereSeparator: \.isNewline).compactMap { rawLine in
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            guard line.hasPrefix("{"),
                let object = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any]
            else { return nil }
            return (line, object)
        }
    }

    // MARK: Validation

    private static func isHexUpper(_ scalar: Unicode.Scalar) -> Bool {
        ("0"..."9").contains(scalar) || ("A"..."F").contains(scalar)
    }

    private static func isDigit(_ scalar: Unicode.Scalar) -> Bool { ("0"..."9").contains(scalar) }

    /// `^([0-9A-F]{2}:){31}[0-9A-F]{2}$`
    static func isFingerprint(_ value: String) -> Bool {
        let scalars = Array(value.unicodeScalars)
        guard scalars.count == 32 * 3 - 1 else { return false }
        for (index, scalar) in scalars.enumerated() {
            if index % 3 == 2 {
                if scalar != ":" { return false }
            } else if !isHexUpper(scalar) {
                return false
            }
        }
        return true
    }

    /// `^[0-9]{4}$`
    static func isPin(_ value: String) -> Bool {
        let scalars = Array(value.unicodeScalars)
        return scalars.count == 4 && scalars.allSatisfy(isDigit)
    }

    /// `^[A-Za-z0-9 ._-]{1,40}$`
    static func isPairingName(_ value: String) -> Bool {
        let scalars = Array(value.unicodeScalars)
        guard (1...maxNameLength).contains(scalars.count) else { return false }
        return scalars.allSatisfy { scalar in
            ("a"..."z").contains(scalar) || ("A"..."Z").contains(scalar) || isDigit(scalar)
                || scalar == " " || scalar == "." || scalar == "_" || scalar == "-"
        }
    }

    /// `auto`, or a connector name: `^[A-Za-z0-9-]{1,32}$` (the same rule as `DIARCHY_STREAM_OUTPUT`).
    static func isStreamOutput(_ value: String) -> Bool {
        let scalars = Array(value.unicodeScalars)
        guard (1...32).contains(scalars.count) else { return false }
        return scalars.allSatisfy { scalar in
            ("a"..."z").contains(scalar) || ("A"..."Z").contains(scalar) || isDigit(scalar) || scalar == "-"
        }
    }

    /// Dotted quad of four decimal octets (1-3 digits, no leading zero, at most 255).
    static func isIPv4(_ value: String) -> Bool {
        let octets = value.split(separator: ".", omittingEmptySubsequences: false)
        guard octets.count == 4 else { return false }
        return octets.allSatisfy { octet in
            let scalars = Array(octet.unicodeScalars)
            guard (1...3).contains(scalars.count), scalars.allSatisfy(isDigit) else { return false }
            if scalars.count > 1, scalars[0] == "0" { return false }
            return Int(octet).map { $0 <= 255 } ?? false
        }
    }
}
