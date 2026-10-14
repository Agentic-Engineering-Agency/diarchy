import Foundation

// MARK: - KVM pairing (spec §8.5)

public enum PairingError: Error, Equatable, Sendable {
    /// `diarchy-host-key status` does not report a pinned key for the host.
    case hostKeyNotConfirmed
    /// Lan Mouse never produced a certificate, even after launching it and waiting.
    case noLocalCertificate
    /// `diarchy-mac-fingerprint` succeeded but printed something that is not a fingerprint.
    case unexpectedFingerprintOutput
    /// The host's `probe` reported no Lan Mouse certificate after authorizing this Mac.
    case hostHasNoCertificate
    case agent(HostAgentError)
    /// `diarchy-kvm-configure` failed: its first error line.
    case configurationFailed(String)
    case config(ConfigError)

    /// Text to show after `Error: `.
    public var userMessage: String {
        switch self {
        case .hostKeyNotConfirmed:
            "Host key not confirmed. Confirm the host key in Diarchy and try again."
        case .noLocalCertificate:
            "Lan Mouse did not create its certificate on this Mac. Open it once and try again."
        case .unexpectedFingerprintOutput:
            "diarchy-mac-fingerprint returned an unexpected value"
        case .hostHasNoCertificate:
            "The host has no Lan Mouse certificate yet. Start Lan Mouse on the host and try again."
        case .agent(let error): error.userMessage
        case .configurationFailed(let message): message
        case .config(let error): error.description
        }
    }
}

/// Pairs this Mac with the host's Lan Mouse (spec §8.5 steps 1-4). The caller re-runs the checklist (step 5).
public struct Pairing: Sendable {
    private let paths: Paths
    private let store: ConfigStore
    private let runner: any CommandRunner
    private let agent: HostAgentClient
    private let sleep: @Sendable (Duration) async throws -> Void

    private static let localTimeout: TimeInterval = 20
    /// The certificate wait: 20 steps of 0.5 s, 10 s in total.
    private static let certificateStep: Duration = .milliseconds(500)
    private static let certificateSteps = 20

    public init(
        paths: Paths, store: ConfigStore, runner: any CommandRunner, agent: HostAgentClient,
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        self.paths = paths
        self.store = store
        self.runner = runner
        self.agent = agent
        self.sleep = sleep
    }

    public func pair() async throws(PairingError) -> DiarchyConfig {
        var config = try loadConfig()

        // 0. Nothing goes over SSH until the user confirmed the host key (SEC-04).
        guard case .trusted = await HostKeyTrust(paths: paths, runner: runner).status() else {
            throw .hostKeyNotConfirmed
        }

        // 1. The Mac's own certificate; Lan Mouse creates it on its first start.
        var reading = await readFingerprint()
        if case .missing = reading {
            await launchLanMouseAndWaitForCertificate()
            reading = await readFingerprint()
        }
        let macFingerprint: String
        switch reading {
        case .value(let value): macFingerprint = value
        case .missing: throw .noLocalCertificate
        case .malformed: throw .unexpectedFingerprintOutput
        }

        // 2. Let the host trust this Mac.
        do throws(HostAgentError) {
            _ = try await agent.kvmAuthorize(fingerprint: macFingerprint)
        } catch {
            throw .agent(error)
        }

        // 3. Learn the host's certificate and remember it.
        let controllerIP = await ControllerIP.resolve(lanHost: config.lanHost, runner: runner)
        let probe: ProbeResult
        do throws(HostAgentError) {
            probe = try await agent.probe(controllerIP: controllerIP)
        } catch {
            throw .agent(error)
        }
        guard let hostFingerprint = probe.kvm.hostFingerprint, !hostFingerprint.isEmpty else {
            throw .hostHasNoCertificate
        }
        config.hostFingerprint = hostFingerprint.uppercased()
        do {
            try store.save(config)
        } catch let error as ConfigError {
            throw .config(error)
        } catch {
            throw .config(ConfigError(reason: "cannot write configuration file: \(store.url.path)"))
        }

        // 4. Render this Mac's Lan Mouse config; the host fingerprint is saved already, so a failure keeps it.
        let result: CommandResult
        do {
            result = try await runner.run(
                executable: paths.localBin("diarchy-kvm-configure"), arguments: [], environment: nil, stdin: nil,
                timeout: Self.localTimeout)
        } catch let error as CommandError {
            switch error {
            case .launchFailed(_, let message): throw .configurationFailed(message)
            case .timedOut(let command): throw .configurationFailed("\(command) did not respond in time")
            }
        } catch {
            throw .configurationFailed(error is CancellationError ? "cancelled" : String(describing: error))
        }
        guard result.succeeded else { throw .configurationFailed(result.firstErrorLine) }

        // Lan Mouse was started (by the installer or by step 1) before this config existed and `diarchy-kvm on`
        // leaves a running process alone: stop it so the next `on` starts one that reads the new config. Best
        // effort: the pairing itself is done, so a failing `off` is ignored.
        _ = try? await runner.run(
            executable: paths.localBin("diarchy-kvm"), arguments: ["off"], environment: nil, stdin: nil,
            timeout: Self.localTimeout)
        return config
    }

    private func loadConfig() throws(PairingError) -> DiarchyConfig {
        do {
            guard let config = try store.load() else {
                throw ConfigError(reason: "Diarchy is not configured (open the app to set it up)")
            }
            return config
        } catch let error as ConfigError {
            throw .config(error)
        } catch {
            throw .config(ConfigError(reason: "cannot read configuration file: \(store.url.path)"))
        }
    }

    private enum Reading {
        case value(String)
        /// The script failed or could not run: there is no certificate yet.
        case missing
        /// The script succeeded but printed something that is not a fingerprint.
        case malformed
    }

    /// `diarchy-mac-fingerprint`, uppercased and validated; the script's output is never trusted as is.
    private func readFingerprint() async -> Reading {
        guard let result = try? await runner.run(
            executable: paths.localBin("diarchy-mac-fingerprint"), arguments: [], environment: nil, stdin: nil,
            timeout: Self.localTimeout),
            result.succeeded
        else { return .missing }
        let value = result.stdout.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        return HostAgentClient.isFingerprint(value) ? .value(value) : .malformed
    }

    /// `open -gj` starts Lan Mouse hidden and in the background; it writes its certificate on first start.
    /// Waiting is bounded: at most `certificateSteps` × `certificateStep`, through the injected `sleep`.
    private func launchLanMouseAndWaitForCertificate() async {
        guard let opened = try? await runner.run(
            executable: "/usr/bin/open", arguments: ["-gj", paths.lanMouseApp.path], environment: nil, stdin: nil,
            timeout: Self.localTimeout),
            opened.succeeded
        else { return }
        for _ in 0..<Self.certificateSteps {
            if FileManager.default.fileExists(atPath: paths.lanMouseCertificate.path) { return }
            do {
                try await sleep(Self.certificateStep)
            } catch {
                return
            }
        }
    }
}

// MARK: - Moonlight pairing (spec §9.3)

public enum MoonlightPairingError: Error, Equatable, Sendable {
    case agent(HostAgentError)
    /// Moonlight could not be started: the launch message.
    case launchFailed(String)
    /// Moonlight paired, but the host stream that was started for it could not be stopped again.
    case streamStopFailed(HostAgentError)
    /// `diarchy-stream --dry-run` found no route to Sunshine (LAN or Tailscale), or printed none.
    case hostUnreachable

    /// Text to show after `Error: `. The agent's English messages for the two pairing failures are replaced.
    public var userMessage: String {
        switch self {
        case .agent(.agent(code: .pairFailed, _)):
            "Moonlight did not finish pairing in time. Check that the host is reachable on the chosen route and try again."
        case .agent(.agent(code: .sunshineUnreachable, _)): "Sunshine is not responding on the host."
        case .agent(let error): error.userMessage
        case .launchFailed(let message): "Could not open Moonlight: \(message)"
        case .streamStopFailed(let error):
            "Moonlight is paired, but the stream could not be stopped on the host: \(error.userMessage)"
        case .hostUnreachable: "This Mac cannot reach Sunshine over the LAN or Tailscale."
        }
    }
}

/// How a successful Moonlight pairing ended.
public enum MoonlightPairingOutcome: Equatable, Sendable {
    /// The PIN flow ran and the agent confirmed it.
    case paired
    /// `Moonlight list` already answered for the host, so no pairing was attempted.
    case alreadyPaired

    /// Success note to show.
    public var userMessage: String {
        switch self {
        case .paired: "Moonlight is paired with the host."
        case .alreadyPaired: "Moonlight was already paired with the host."
        }
    }
}

public struct MoonlightPairing: Sendable {
    private let paths: Paths
    private let runner: any CommandRunner
    private let agent: HostAgentClient
    private let makePIN: @Sendable () -> String
    private let sleep: @Sendable (Duration) async throws -> Void

    private static let pairingName = "Diarchy"
    private static let routeTimeout: TimeInterval = 20
    private static let listTimeout: TimeInterval = 15
    /// Time between SIGTERM and SIGKILL for the Moonlight child.
    private static let terminateGrace: Duration = .seconds(5)
    /// Moonlight prints this on stdout before anything else; it is not an app.
    private static let logLinePrefix = "Redirecting log output"

    public init(
        paths: Paths, runner: any CommandRunner, agent: HostAgentClient,
        makePIN: @escaping @Sendable () -> String = MoonlightPairing.randomPIN,
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        self.paths = paths
        self.runner = runner
        self.agent = agent
        self.makePIN = makePIN
        self.sleep = sleep
    }

    /// Four digits, zero-padded.
    public static func randomPIN() -> String {
        String(format: "%04d", Int.random(in: 0...9999))
    }

    /// The whole lifecycle (spec §9.3): `stream start` (Sunshine is stopped outside Streaming mode) → the host
    /// `diarchy-stream --dry-run` chooses → `Moonlight list` (already paired: done) → spawn `Moonlight pair` →
    /// `stream pair` → terminate Moonlight → `stream stop`. The stop is skipped when the stream was already running
    /// (`hostStreamAlreadyRunning`, i.e. the mode was Streaming); on any failure the stream this call started is
    /// still stopped, and the first error is the one reported. The caller makes sure KVM is off.
    @discardableResult
    public func pair(hostStreamAlreadyRunning: Bool = false) async throws(MoonlightPairingError)
        -> MoonlightPairingOutcome
    {
        var failure: MoonlightPairingError?
        var outcome = MoonlightPairingOutcome.paired
        do throws(HostAgentError) {
            try await agent.streamStart(output: "auto")
            do throws(MoonlightPairingError) {
                outcome = try await pairWhileStreamRuns()
            } catch {
                failure = error
            }
        } catch {
            failure = .agent(error)
        }
        if !hostStreamAlreadyRunning {
            do throws(HostAgentError) {
                try await agent.streamStop()
            } catch {
                if failure == nil { failure = .streamStopFailed(error) }
            }
        }
        if let failure { throw failure }
        return outcome
    }

    private func pairWhileStreamRuns() async throws(MoonlightPairingError) -> MoonlightPairingOutcome {
        guard let host = await routeHost() else { throw .hostUnreachable }
        if await isAlreadyPaired(host: host) { return .alreadyPaired }
        try await runPairing(host: host)
        return .paired
    }

    /// The `host=` value of the `route=` line of `diarchy-stream --dry-run`, which picks the route by probing
    /// Sunshine's own port. `nil` when it fails, times out, or prints no host.
    private func routeHost() async -> String? {
        guard
            let result = try? await runner.run(
                executable: paths.localBin("diarchy-stream"), arguments: ["--dry-run"], environment: nil, stdin: nil,
                timeout: Self.routeTimeout),
            result.succeeded
        else { return nil }
        for line in result.stdout.split(separator: "\n") where line.hasPrefix("route=") {
            for word in line.split(separator: " ") where word.hasPrefix("host=") {
                let host = String(word.dropFirst("host=".count))
                return host.isEmpty ? nil : host
            }
        }
        return nil
    }

    /// Moonlight stores the host it paired with, and `Moonlight pair` then never sends a pairing request, so the
    /// agent could only time out. `Moonlight list` succeeds with at least one app line only for a paired host.
    private func isAlreadyPaired(host: String) async -> Bool {
        guard
            let result = try? await runner.run(
                executable: paths.moonlightExecutable, arguments: ["list", host], environment: nil, stdin: nil,
                timeout: Self.listTimeout),
            result.succeeded
        else { return false }
        return result.stdout.split(separator: "\n").contains { line in
            let text = line.trimmingCharacters(in: .whitespaces)
            return !text.isEmpty && !text.hasPrefix(Self.logLinePrefix)
        }
    }

    /// Success is decided only by the agent: Moonlight's `pair` exits 0 whether it paired or not. Moonlight is
    /// started first so its request is pending while the agent submits the PIN, and is stopped once the agent answers.
    private func runPairing(host: String) async throws(MoonlightPairingError) {
        let pin = makePIN()
        let child: any ChildProcess
        do {
            child = try runner.spawn(
                executable: paths.moonlightExecutable, arguments: ["pair", host, "--pin", pin], environment: nil)
        } catch let error as CommandError {
            switch error {
            case .launchFailed(_, let message): throw .launchFailed(message)
            case .timedOut(let command): throw .launchFailed("\(command) did not respond in time")
            }
        } catch {
            throw .launchFailed(error is CancellationError ? "cancelled" : String(describing: error))
        }

        let outcome: HostAgentError?
        do throws(HostAgentError) {
            try await agent.streamPair(pin: pin, name: Self.pairingName)
            outcome = nil
        } catch {
            outcome = error
        }
        await child.terminateThenKill(after: Self.terminateGrace, sleep: sleep)
        if let outcome { throw .agent(outcome) }
    }
}
