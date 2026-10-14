import Foundation

/// Error codes of the host agent's failure envelope (spec §6.1). Unrecognised codes are kept verbatim.
public enum AgentErrorCode: Equatable, Sendable {
    case usage
    case unsupportedPlatform
    case notInstalled
    case noSession
    case serviceFailed
    case invalidFingerprint
    case configWriteFailed
    case sunshineUnreachable
    case pairFailed
    case `internal`
    case unknown(String)

    init(wire: String) {
        switch wire {
        case "usage": self = .usage
        case "unsupported_platform": self = .unsupportedPlatform
        case "not_installed": self = .notInstalled
        case "no_session": self = .noSession
        case "service_failed": self = .serviceFailed
        case "invalid_fingerprint": self = .invalidFingerprint
        case "config_write_failed": self = .configWriteFailed
        case "sunshine_unreachable": self = .sunshineUnreachable
        case "pair_failed": self = .pairFailed
        case "internal": self = .internal
        default: self = .unknown(wire)
        }
    }
}

public enum HostAgentError: Error, Equatable, Sendable {
    /// Envelope `ok:false`.
    case agent(code: AgentErrorCode, message: String)
    /// ssh exit 255 or the process could not start: the first stderr line (or launch message).
    case transport(String)
    /// The command outlived its timeout; `command` is `diarchy-host <subcommand>`.
    case timedOut(command: String)
    /// stdout was not exactly one JSON envelope, or a required field was missing.
    case malformedOutput(String)
    case unsupportedSchema(Int)
    /// Client-side validation failed before anything was sent.
    case invalidArgument(String)

    /// Text to show after `Error: `.
    public var userMessage: String {
        switch self {
        case .agent(.noSession, _):
            "No Omarchy session is open on the host (did it log out?). Log in on the host or, remotely, restart the login screen over SSH with: sudo systemctl restart sddm"
        case .agent(.serviceFailed, let message): "A service failed on the host: \(message)"
        case .agent(_, let message): message
        case .transport(let message): message
        case .timedOut(let command): "\(command) did not respond in time"
        case .malformedOutput(let message): message
        case .unsupportedSchema(let schema): "diarchy-host schema \(schema) is not supported by this version of the app"
        case .invalidArgument(let message): message
        }
    }
}

public struct RequiredAction: Equatable, Sendable, Decodable {
    public enum Severity: String, Equatable, Sendable, Decodable {
        case required
        case recommended

        /// A severity added by a newer agent must not fail the whole probe: it reads as the milder `recommended`.
        public init(from decoder: any Decoder) throws {
            let value = try decoder.singleValueContainer().decode(String.self)
            self = Severity(rawValue: value) ?? .recommended
        }
    }

    public var id: String
    public var severity: Severity
    public var title: String
    public var reason: String
    /// Shell command for the user to run on the host; never altered or executed by the app.
    public var command: String
}

public struct ProbeResult: Equatable, Sendable, Decodable {
    public struct Platform: Equatable, Sendable, Decodable {
        public var supported: Bool
        public var adapter: String
        public var osId: String?
        public var flavor: String?
        public var compositor: String?
        public var machine: String?
        public var reasons: [String]
    }

    public struct Session: Equatable, Sendable, Decodable {
        public var graphicalSessionActive: Bool
    }

    public struct Components: Equatable, Sendable, Decodable {
        public struct LanMouse: Equatable, Sendable, Decodable {
            public var installed: Bool
            public var version: String?
            public var serviceActive: Bool
            public var listeningUdp: Bool
        }

        public struct Simple: Equatable, Sendable, Decodable {
            public var installed: Bool
        }

        public struct Portal: Equatable, Sendable, Decodable {
            public var installed: Bool
            public var serviceActive: Bool
        }

        public struct Sunshine: Equatable, Sendable, Decodable {
            public var installed: Bool
            public var serviceActive: Bool
        }

        public var lanMouse: LanMouse
        public var clipboard: Simple
        public var filePortal: Portal
        public var sunshine: Sunshine
    }

    public struct KVM: Equatable, Sendable, Decodable {
        public var hostFingerprint: String?
        public var authorizedFingerprints: [String]
        public var port: Int
    }

    public var agentVersion: String
    public var platform: Platform
    public var session: Session
    public var components: Components
    public var kvm: KVM
    public var requiredActions: [RequiredAction]
    /// The envelope line exactly as the agent printed it (without the line terminator).
    public var rawJSON: String = ""

    private enum CodingKeys: String, CodingKey {
        case agentVersion, platform, session, components, kvm, requiredActions
    }
}

/// One Hyprland monitor from `diarchy-host report`.
public struct HostMonitor: Equatable, Sendable, Decodable {
    public var name: String
    public var `internal`: Bool
    public var enabled: Bool

    public init(name: String, internal: Bool, enabled: Bool) {
        self.name = name
        self.internal = `internal`
        self.enabled = enabled
    }
}

/// The payload of `diarchy-host report`. Only the monitors are decoded; every other field is kept in `rawJSON`.
public struct HostReport: Equatable, Sendable, Decodable {
    public var monitors: [HostMonitor]
    /// The envelope line exactly as the agent printed it (without the line terminator).
    public var rawJSON: String = ""

    public init(monitors: [HostMonitor], rawJSON: String = "") {
        self.monitors = monitors
        self.rawJSON = rawJSON
    }

    /// Contract section 2: every value can be `null`. A monitor without a name is dropped; an unknown `internal` is
    /// `false` and an unknown `enabled` is `true`, so the Settings picker still lists the monitor.
    private struct RawMonitor: Decodable {
        var name: String?
        var `internal`: Bool?
        var enabled: Bool?
    }

    private struct Hyprland: Decodable {
        var monitors: [RawMonitor]?
    }

    private enum CodingKeys: String, CodingKey { case hyprland }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let hyprland = try container.decodeIfPresent(Hyprland.self, forKey: .hyprland)
        monitors = (hyprland?.monitors ?? []).compactMap { raw in
            guard let name = raw.name, !name.isEmpty else { return nil }
            return HostMonitor(name: name, internal: raw.internal ?? false, enabled: raw.enabled ?? true)
        }
    }
}
