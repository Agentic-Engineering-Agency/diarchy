import Foundation

public enum StepID: Int, CaseIterable, Sendable {
    case components = 1, configuration, hostKey, ssh, agent, platform, requiredActions, pairing

    public var title: String {
        switch self {
        case .components: "Mac Components"
        case .configuration: "Configuration"
        case .hostKey: "Host Key"
        case .ssh: "SSH"
        case .agent: "Host Agent"
        case .platform: "Platform"
        case .requiredActions: "Required Actions"
        case .pairing: "Pairing"
        }
    }
}

public enum StepState: Equatable, Sendable { case pending, ok, actionRequired, failed }

/// The button a step offers.
public enum StepAction: Equatable, Sendable { case installMac, installHost, confirmHostKey, pair }

/// A command the user runs (and the UI offers to copy). It is only displayed, never executed by the app.
public struct CommandHint: Equatable, Sendable {
    public var title: String
    public var command: String
    public var blocking: Bool

    public init(title: String, command: String, blocking: Bool) {
        self.title = title
        self.command = command
        self.blocking = blocking
    }
}

public struct ChecklistStep: Equatable, Sendable, Identifiable {
    public var id: StepID
    public var title: String
    public var state: StepState
    public var message: String
    public var commands: [CommandHint]
    public var action: StepAction?

    public init(
        id: StepID, state: StepState, message: String, commands: [CommandHint] = [], action: StepAction? = nil
    ) {
        self.id = id
        self.title = id.title
        self.state = state
        self.message = message
        self.commands = commands
        self.action = action
    }
}

public struct ChecklistResult: Equatable, Sendable {
    /// Always eight steps, in `StepID` order.
    public var steps: [ChecklistStep]
    /// Set once step 2 loaded the configuration.
    public var config: DiarchyConfig?
    /// Set once step 6 received the host's probe.
    public var probe: ProbeResult?

    public init(steps: [ChecklistStep], config: DiarchyConfig?, probe: ProbeResult?) {
        self.steps = steps
        self.config = config
        self.probe = probe
    }

    /// All eight steps are `ok`.
    public var isComplete: Bool { steps.count == StepID.allCases.count && steps.allSatisfy { $0.state == .ok } }
}

/// The ordered setup checklist of spec §8.4. A step runs only when every earlier step is `ok`.
public struct SetupChecklist: Sendable {
    private let paths: Paths
    private let store: ConfigStore
    private let runner: any CommandRunner
    private let bundledVersion: String?
    private let agent: HostAgentClient
    private let macInstallFailure: String?

    /// Actions that only matter for streaming (spec §6.3). The KVM probe never reports them; should one appear, it
    /// is still listed but can never block step 6.
    private static let streamingActionIDs: Set<String> = ["uinput-access", "lid-policy", "firewall-streaming-lan", "wifi-roaming"]
    private static let localTimeout: TimeInterval = 20
    private static let waiting = "Waiting for the earlier steps"

    /// Shown by step 1 while a Mac component is missing.
    public static let commandLineToolsMessage =
        "Install the Command Line Tools: run `xcode-select --install`, then try again."

    /// `macInstallFailure` is the text of the last failed Mac install, if any.
    public init(
        paths: Paths, store: ConfigStore, runner: any CommandRunner, bundledVersion: String?,
        macInstallFailure: String? = nil
    ) {
        self.macInstallFailure = macInstallFailure
        self.paths = paths
        self.store = store
        self.runner = runner
        self.bundledVersion = bundledVersion
        self.agent = HostAgentClient(runner: runner, paths: paths)
    }

    /// `ssh-copy-id -p <port> [-i <identity>.pub] <user>@<host>` for display; `nil` when the configuration has no
    /// host. The host is the LAN host, else the Tailscale host. The identity is shell-quoted when needed.
    public static func sshCopyIDCommand(config: DiarchyConfig) -> String? {
        guard let host = config.preferredHost else { return nil }
        var words = ["ssh-copy-id", "-p", String(config.sshPort)]
        if !config.sshIdentityFile.isEmpty {
            let identity = config.sshIdentityFile
            let publicKey = identity.hasSuffix(".pub") ? identity : identity + ".pub"
            words += ["-i", shellQuoted(publicKey)]
        }
        words.append("\(config.sshUser)@\(host)")
        return words.joined(separator: " ")
    }

    /// Single quotes (`'` becomes `'\''`) unless the word only holds `[A-Za-z0-9_@%+=:,./-]`.
    private static func shellQuoted(_ word: String) -> String {
        let safe = word.unicodeScalars.allSatisfy { scalar in
            ("a"..."z").contains(scalar) || ("A"..."Z").contains(scalar) || ("0"..."9").contains(scalar)
                || "_@%+=:,./-".unicodeScalars.contains(scalar)
        }
        if safe && !word.isEmpty { return word }
        return "'" + word.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    // MARK: Run

    private struct Gathered {
        var config: DiarchyConfig?
        var probe: ProbeResult?
    }

    public func run() async -> ChecklistResult {
        var gathered = Gathered()
        var steps: [ChecklistStep] = []
        for id in StepID.allCases {
            if steps.contains(where: { $0.state != .ok }) {
                steps.append(ChecklistStep(id: id, state: .pending, message: Self.waiting))
            } else {
                steps.append(await check(id, &gathered))
            }
        }
        return ChecklistResult(steps: steps, config: gathered.config, probe: gathered.probe)
    }

    private func check(_ id: StepID, _ gathered: inout Gathered) async -> ChecklistStep {
        switch id {
        case .components: return await checkComponents()
        case .configuration: return checkConfiguration(&gathered)
        case .hostKey: return await checkHostKey()
        case .ssh: return await checkSSH(gathered.config)
        case .agent: return await checkAgent()
        case .platform: return await checkPlatform(&gathered)
        case .requiredActions: return checkRequiredActions(gathered.probe)
        case .pairing: return await checkPairing(&gathered)
        }
    }

    // MARK: Steps

    /// What `diarchy-kvm on` and the other Mac scripts need at runtime, not only what the Lan Mouse installer
    /// copies: `diarchy-kvm` refuses to start without the clipboard bridge and the file portal pieces. Streaming-only
    /// pieces (`diarchy-stream`, its doctor, Moonlight) are deliberately absent.
    private static let macExecutables = [
        "diarchy-kvm", "diarchy-ssh", "diarchy-kvm-configure", "diarchy-mac-fingerprint", "diarchy-host-key",
        "diarchy-clipboard-bridge", "diarchy-file-portal-receiver", "diarchy-file-portal-send",
    ]
    /// Sourced (never executed) by `diarchy-ssh` and `diarchy-kvm-configure`.
    private static let macScripts = ["diarchy-config.sh", "diarchy-route.sh"]
    private static let filePortalApp = "Diarchy File Portal.app"
    /// `diarchy-kvm` checks `<app>/Contents/MacOS/diarchy-file-portal` is executable.
    private static let filePortalExecutable = "Contents/MacOS/diarchy-file-portal"

    private func checkComponents() async -> ChecklistStep {
        let fileManager = FileManager.default
        func isFile(_ path: String) -> Bool {
            var isDirectory: ObjCBool = false
            return fileManager.fileExists(atPath: path, isDirectory: &isDirectory) && !isDirectory.boolValue
        }
        var missing: [String] = []
        for name in Self.macExecutables {
            let path = paths.localBin(name)
            if !isFile(path) || !fileManager.isExecutableFile(atPath: path) { missing.append(name) }
        }
        for name in Self.macScripts {
            let path = paths.localBin(name)
            if !isFile(path) || !fileManager.isReadableFile(atPath: path) { missing.append(name) }
        }
        var isDirectory: ObjCBool = false
        let lanMouseExists = fileManager.fileExists(atPath: paths.lanMouseApp.path, isDirectory: &isDirectory)
        if !lanMouseExists || !isDirectory.boolValue { missing.append("Lan Mouse.app") }
        let portalApp = paths.home.appendingPathComponent("Applications/\(Self.filePortalApp)", isDirectory: true)
        let portalBinary = portalApp.appendingPathComponent(Self.filePortalExecutable).path
        if !isFile(portalBinary) || !fileManager.isExecutableFile(atPath: portalBinary) {
            missing.append(Self.filePortalApp)
        }
        let outdated = missing.isEmpty ? bundledVersion.flatMap { bundled in
            paths.installedMacVersion() == bundled ? nil : bundled
        } : nil
        guard missing.isEmpty, outdated == nil else {
            // A failed install is explained on the row; the button stays so the user can try again.
            if let failure = macInstallFailure {
                let message = await commandLineToolsInstalled() ? failure : Self.commandLineToolsMessage
                return ChecklistStep(id: .components, state: .failed, message: message, action: .installMac)
            }
            let message =
                outdated.map {
                    "The Mac components are version \(paths.installedMacVersion() ?? "unknown"); this app includes version \($0)."
                } ?? "Missing on this Mac: \(missing.joined(separator: ", "))"
            return ChecklistStep(id: .components, state: .actionRequired, message: message, action: .installMac)
        }
        return ChecklistStep(id: .components, state: .ok, message: "Components installed")
    }

    private func checkConfiguration(_ gathered: inout Gathered) -> ChecklistStep {
        do {
            guard let config = try store.load() else {
                return ChecklistStep(
                    id: .configuration, state: .failed,
                    message: "diarchy-config: Diarchy is not configured (open the app to set it up)")
            }
            gathered.config = config
            return ChecklistStep(id: .configuration, state: .ok, message: "Configuration is valid")
        } catch {
            return ChecklistStep(id: .configuration, state: .failed, message: "\(error)")
        }
    }

    /// Every SSH call pins the host key, so without a confirmed key the SSH step could only fail. The row comes
    /// first and offers the fingerprint confirmation (`AppModel.confirmHostKeyStep`).
    private func checkHostKey() async -> ChecklistStep {
        switch await HostKeyTrust(paths: paths, runner: runner).status() {
        case .trusted(let fingerprint):
            return ChecklistStep(id: .hostKey, state: .ok, message: "Host key confirmed (\(fingerprint))")
        case .missing:
            return ChecklistStep(
                id: .hostKey, state: .actionRequired,
                message: "The host key is not confirmed yet. Compare its fingerprint with the host's, then confirm it.",
                commands: [
                    CommandHint(
                        title: "Show the host fingerprint (run on the host)",
                        command: "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub", blocking: false)
                ],
                action: .confirmHostKey)
        case .unknown(let reason):
            return ChecklistStep(id: .hostKey, state: .failed, message: reason)
        }
    }

    private static let changedHostKeyMessage =
        "The host's SSH key changed (possible impersonation). Diarchy will not connect until you verify it."

    private func checkSSH(_ config: DiarchyConfig?) async -> ChecklistStep {
        let result = await agent.sshTrue()
        if result.succeeded { return ChecklistStep(id: .ssh, state: .ok, message: "SSH connection works") }
        // A changed host key prints a banner of "@" lines before the cause; name it instead of echoing the banner.
        if result.stderr.contains("REMOTE HOST IDENTIFICATION HAS CHANGED")
            || result.stderr.contains("Host key verification failed")
        {
            return ChecklistStep(id: .ssh, state: .failed, message: Self.changedHostKeyMessage)
        }
        // Only a rejected key is fixed by ssh-copy-id; any other failure (unreachable host, …) keeps its own reason.
        if result.exitCode == 255, result.stderr.contains("Permission denied"),
            let config, let command = Self.sshCopyIDCommand(config: config)
        {
            return ChecklistStep(
                id: .ssh, state: .actionRequired,
                message: "The host does not accept your SSH key. Copy it with the command below.",
                commands: [CommandHint(title: "Copy the SSH key to the host", command: command, blocking: true)])
        }
        return ChecklistStep(id: .ssh, state: .failed, message: result.firstErrorLine)
    }

    private func checkAgent() async -> ChecklistStep {
        let installed: String
        do throws(HostAgentError) {
            installed = try await agent.version()
        } catch {
            switch error {
            case .agent(.notInstalled, _), .malformedOutput, .unsupportedSchema:
                // Missing, broken or from another generation: installing the bundled agent is the fix.
                return ChecklistStep(
                    id: .agent, state: .actionRequired,
                    message: "The host agent is not installed or does not respond as expected.",
                    action: .installHost)
            default:
                return ChecklistStep(id: .agent, state: .failed, message: error.userMessage)
            }
        }
        guard installed == bundledVersion else {
            return ChecklistStep(
                id: .agent, state: .actionRequired,
                message:
                    "The host agent is version \(installed); this app includes version \(bundledVersion ?? "unknown").",
                action: .installHost)
        }
        return ChecklistStep(id: .agent, state: .ok, message: "Host agent \(installed)")
    }

    private func checkPlatform(_ gathered: inout Gathered) async -> ChecklistStep {
        let controllerIP = await ControllerIP.resolve(lanHost: gathered.config?.lanHost ?? "", runner: runner)
        let probe: ProbeResult
        do throws(HostAgentError) {
            probe = try await agent.probe(controllerIP: controllerIP)
        } catch {
            return ChecklistStep(id: .platform, state: .failed, message: error.userMessage)
        }
        gathered.probe = probe
        guard probe.platform.supported else {
            let reasons = probe.platform.reasons.joined(separator: "; ")
            return ChecklistStep(
                id: .platform, state: .failed,
                message: reasons.isEmpty ? "The host is not a supported platform" : reasons)
        }
        return ChecklistStep(id: .platform, state: .ok, message: "Supported platform (\(probe.platform.adapter))")
    }

    private func checkRequiredActions(_ probe: ProbeResult?) -> ChecklistStep {
        let actions = probe?.requiredActions ?? []
        func isBlocking(_ action: RequiredAction) -> Bool {
            action.severity == .required && !Self.streamingActionIDs.contains(action.id)
        }
        let hints = actions.map {
            CommandHint(title: $0.title, command: $0.command, blocking: isBlocking($0))
        }
        let blocking = actions.filter(isBlocking)
        guard blocking.isEmpty else {
            return ChecklistStep(
                id: .requiredActions, state: .actionRequired,
                message: "Run these commands on the host: "
                    + blocking.map(\.reason).joined(separator: " "),
                commands: hints)
        }
        // The host install stops at a pending required action before it installs these three; once the user cleared
        // the action, the install must run again, which the agent version alone (step 4) cannot tell.
        if let components = probe?.components {
            var missing: [String] = []
            if !components.lanMouse.installed { missing.append("Lan Mouse") }
            if !components.clipboard.installed { missing.append("clipboard") }
            if !components.filePortal.installed { missing.append("file portal") }
            if !missing.isEmpty {
                return ChecklistStep(
                    id: .requiredActions, state: .actionRequired,
                    message:
                        "Missing components on the host: \(missing.joined(separator: ", ")). Install on the host again.",
                    commands: hints, action: .installHost)
            }
        }
        let message =
            hints.isEmpty
            ? "No pending actions" : "No required actions (\(hints.count) recommended or streaming-only)"
        return ChecklistStep(id: .requiredActions, state: .ok, message: message, commands: hints)
    }

    private func checkPairing(_ gathered: inout Gathered) async -> ChecklistStep {
        func pair(_ message: String) -> ChecklistStep {
            ChecklistStep(
                id: .pairing, state: .actionRequired, message: "Pairing pending: " + message, action: .pair)
        }
        guard let probe = gathered.probe, let config = gathered.config else {
            return ChecklistStep(id: .pairing, state: .failed, message: "Host data is missing")
        }
        guard let macFingerprint = await macFingerprint() else {
            return pair("no local Lan Mouse certificate on this Mac.")
        }
        // The loader guarantees uppercase for the config; the script output and the agent are not validated here.
        guard probe.kvm.authorizedFingerprints.contains(where: { $0.uppercased() == macFingerprint.uppercased() })
        else {
            return pair("this Mac is not authorized on the host.")
        }
        let hostFingerprint = probe.kvm.hostFingerprint ?? ""
        if config.hostFingerprint.isEmpty {
            return pair("the host fingerprint differs: none is saved in the configuration yet.")
        }
        guard !hostFingerprint.isEmpty, config.hostFingerprint.uppercased() == hostFingerprint.uppercased() else {
            return pair("the host fingerprint differs from the one saved in the configuration.")
        }
        return ChecklistStep(id: .pairing, state: .ok, message: "Mac and host are paired")
    }

    /// `xcode-select -p` succeeds. Without the Command Line Tools, `/usr/bin/python3` is a stub that cannot run.
    private func commandLineToolsInstalled() async -> Bool {
        guard let result = try? await runner.run(
            executable: "/usr/bin/xcode-select", arguments: ["-p"], environment: nil, stdin: nil,
            timeout: Self.localTimeout)
        else { return false }
        return result.succeeded
    }

    /// Trimmed stdout of `diarchy-mac-fingerprint`; `nil` when it fails, cannot run or prints nothing.
    private func macFingerprint() async -> String? {
        guard let result = try? await runner.run(
            executable: paths.localBin("diarchy-mac-fingerprint"), arguments: [], environment: nil, stdin: nil,
            timeout: Self.localTimeout),
            result.succeeded
        else { return nil }
        let value = result.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }
}
