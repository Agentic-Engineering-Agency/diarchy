import Foundation

public enum KVMSide: String, CaseIterable, Sendable { case left, right, top, bottom }

/// A configuration problem. `reason` is one of the canonical spec §4.5 reasons, shared with `client/diarchy-config.sh`.
public struct ConfigError: Error, Equatable, Sendable, CustomStringConvertible {
    public let reason: String

    public init(reason: String) {
        self.reason = reason
    }

    public var description: String { "diarchy-config: \(reason)" }
}

public struct DiarchyConfig: Equatable, Sendable {
    public var sshUser: String
    public var lanHost: String = ""
    public var tailscaleHost: String = ""
    public var sshPort: Int = 22
    public var sshIdentityFile: String = ""
    /// Stored raw; empty means "use the default" (see `effectiveHostKeyAlias`).
    public var hostKeyAlias: String = ""
    public var kvmSide: KVMSide = .left
    /// Uppercase SHA-256 in `AA:BB:…` form; empty means unpaired.
    public var hostFingerprint: String = ""
    public var streamResolution: String = "1920x1080"
    public var streamFPS: Int = 60
    public var streamOverlay: Bool = false
    /// `auto`, or the Hyprland connector name the host captures (for example `DP-1`).
    public var streamOutput: String = "auto"

    public init(sshUser: String, lanHost: String = "", tailscaleHost: String = "") {
        self.sshUser = sshUser
        self.lanHost = lanHost
        self.tailscaleHost = tailscaleHost
    }

    /// `hostKeyAlias`, else `tailscaleHost`, else `lanHost`.
    public var effectiveHostKeyAlias: String {
        if !hostKeyAlias.isEmpty { return hostKeyAlias }
        if !tailscaleHost.isEmpty { return tailscaleHost }
        return lanHost
    }

    /// `lanHost` if non-empty, else `tailscaleHost`, else `nil`.
    public var preferredHost: String? {
        if !lanHost.isEmpty { return lanHost }
        if !tailscaleHost.isEmpty { return tailscaleHost }
        return nil
    }

    /// Whether `other` renders a different Lan Mouse configuration (`diarchy-kvm-configure` reads only the LAN host,
    /// the Tailscale host, the KVM side and the host fingerprint).
    public func lanMouseConfigDiffers(from other: DiarchyConfig) -> Bool {
        lanHost != other.lanHost || tailscaleHost != other.tailscaleHost || kvmSide != other.kvmSide
            || hostFingerprint != other.hostFingerprint
    }

    /// Validates in spec §4.5 order and throws the first failure.
    public func validate() throws(ConfigError) {
        _ = try ConfigValues(self).build()
    }
}

public enum ConfigCodec {
    public static let header = "# Managed by Diarchy"

    /// Parses `diarchy.conf` text. Mirrors `diarchy_config_load` in `client/diarchy-config.sh` without the
    /// environment overrides, checking in spec §4.5 order and stopping at the first failure.
    public static func parse(_ text: String) throws(ConfigError) -> DiarchyConfig {
        var values = ConfigValues()
        var seen = Set<ConfigKey>()

        // "\r\n" is a single Character, so lines are split on the raw bytes.
        var lines = text.utf8.split(separator: 0x0A, omittingEmptySubsequences: false)
        if lines.last?.isEmpty == true { lines.removeLast() }

        if lines.isEmpty {
            throw ConfigError(reason: "first line must be \"\(header)\"")
        }

        for (index, line) in lines.enumerated() {
            let number = index + 1
            if number == 1 {
                guard line.elementsEqual(header.utf8) else {
                    throw ConfigError(reason: "first line must be \"\(header)\"")
                }
                continue
            }
            if line.contains(0x0D) {
                throw ConfigError(reason: "line \(number): carriage returns are not allowed")
            }
            if line.isEmpty || line.first == UInt8(ascii: "#") { continue }

            // ^([A-Z0-9_]+)=(.*)$ — keys cannot contain "=", so the first "=" splits key and value.
            guard let equals = line.firstIndex(of: UInt8(ascii: "=")), equals > line.startIndex,
                  line[line.startIndex..<equals].allSatisfy(isKeyByte) else {
                throw ConfigError(reason: "line \(number): expected KEY=VALUE")
            }
            let name = String(decoding: line[line.startIndex..<equals], as: UTF8.self)
            guard let key = ConfigKey(rawValue: name) else {
                throw ConfigError(reason: "line \(number): unknown key \(name)")
            }
            guard seen.insert(key).inserted else {
                throw ConfigError(reason: "line \(number): duplicate key \(name)")
            }
            let value = line[line.index(after: equals)...]
            if value.contains(UInt8(ascii: "\"")) || value.contains(UInt8(ascii: "'")) {
                throw ConfigError(reason: "quoted values are not allowed")
            }
            values[key] = String(decoding: value, as: UTF8.self)
        }

        return try values.applyingDefaults().build()
    }

    /// Renders every key in table order with defaults explicit. Uppercases the fingerprint, validates with the
    /// same checks as `parse`, and ends with a newline.
    public static func render(_ config: DiarchyConfig) throws(ConfigError) -> String {
        var config = config
        config.hostFingerprint = config.hostFingerprint.uppercased()
        try config.validate()

        var out = header + "\n"
        for key in ConfigKey.allCases {
            out += "\(key.rawValue)=\(ConfigValues(config)[key])\n"
        }
        return out
    }

    private static func isKeyByte(_ byte: UInt8) -> Bool {
        (byte >= UInt8(ascii: "A") && byte <= UInt8(ascii: "Z"))
            || (byte >= UInt8(ascii: "0") && byte <= UInt8(ascii: "9"))
            || byte == UInt8(ascii: "_")
    }
}

/// Config keys in spec §4.2 table order.
private enum ConfigKey: String, CaseIterable {
    case sshUser = "DIARCHY_SSH_USER"
    case lanHost = "DIARCHY_LAN_HOST"
    case tailscaleHost = "DIARCHY_TAILSCALE_HOST"
    case sshPort = "DIARCHY_SSH_PORT"
    case sshIdentityFile = "DIARCHY_SSH_IDENTITY_FILE"
    case hostKeyAlias = "DIARCHY_HOST_KEY_ALIAS"
    case kvmSide = "DIARCHY_KVM_SIDE"
    case hostFingerprint = "DIARCHY_HOST_FINGERPRINT"
    case streamResolution = "DIARCHY_STREAM_RESOLUTION"
    case streamFPS = "DIARCHY_STREAM_FPS"
    case streamOverlay = "DIARCHY_STREAM_OVERLAY"
    case streamOutput = "DIARCHY_STREAM_OUTPUT"
}

/// The textual form of a configuration: parse fills it from the file, `DiarchyConfig` converts into it for
/// rendering and validation, so one `build()` implements the §4.5 value checks for both directions.
private struct ConfigValues {
    private var storage: [ConfigKey: String] = [:]

    init() {}

    init(_ config: DiarchyConfig) {
        self[.sshUser] = config.sshUser
        self[.lanHost] = config.lanHost
        self[.tailscaleHost] = config.tailscaleHost
        self[.sshPort] = String(config.sshPort)
        self[.sshIdentityFile] = config.sshIdentityFile
        self[.hostKeyAlias] = config.hostKeyAlias
        self[.kvmSide] = config.kvmSide.rawValue
        self[.hostFingerprint] = config.hostFingerprint
        self[.streamResolution] = config.streamResolution
        self[.streamFPS] = String(config.streamFPS)
        self[.streamOverlay] = config.streamOverlay ? "1" : "0"
        self[.streamOutput] = config.streamOutput
    }

    subscript(key: ConfigKey) -> String {
        get { storage[key] ?? "" }
        set { storage[key] = newValue }
    }

    /// An empty value selects the default, as in the bash loader.
    func applyingDefaults() -> ConfigValues {
        var result = self
        let defaults: [(ConfigKey, String)] = [
            (.sshPort, "22"), (.kvmSide, "left"), (.streamResolution, "1920x1080"),
            (.streamFPS, "60"), (.streamOverlay, "0"), (.streamOutput, "auto"),
        ]
        for (key, value) in defaults where result[key].isEmpty {
            result[key] = value
        }
        return result
    }

    func build() throws(ConfigError) -> DiarchyConfig {
        // Regex is not Sendable, so the patterns are local rather than static.
        let userPattern = /[a-z_][a-z0-9_-]{0,31}/
        let hostPattern = /[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?/
        let fingerprintPattern = /([0-9A-F]{2}:){31}[0-9A-F]{2}/
        let resolutionPattern = /[0-9]{3,4}x[0-9]{3,4}/
        let outputPattern = /[A-Za-z0-9-]{1,32}/
        func invalid(_ key: ConfigKey) -> ConfigError { ConfigError(reason: "\(key.rawValue) is invalid") }

        let user = self[.sshUser]
        guard !user.isEmpty else { throw ConfigError(reason: "\(ConfigKey.sshUser.rawValue) is required") }
        guard user.wholeMatch(of: userPattern) != nil else { throw invalid(.sshUser) }

        let lan = self[.lanHost]
        guard lan.isEmpty || lan.wholeMatch(of: hostPattern) != nil else { throw invalid(.lanHost) }
        let tailscale = self[.tailscaleHost]
        guard tailscale.isEmpty || tailscale.wholeMatch(of: hostPattern) != nil else {
            throw invalid(.tailscaleHost)
        }

        guard let port = Self.decimal(self[.sshPort], maxDigits: 5), (1...65535).contains(port) else {
            throw invalid(.sshPort)
        }

        // Absolute, one line, no ".." segment. Quotes and carriage returns are also refused so that anything
        // `render` writes is a file the bash loader accepts.
        let identity = self[.sshIdentityFile]
        guard identity.isEmpty || Self.isValidIdentity(identity) else { throw invalid(.sshIdentityFile) }

        var config = DiarchyConfig(sshUser: user, lanHost: lan, tailscaleHost: tailscale)
        config.sshPort = port
        config.sshIdentityFile = identity
        config.hostKeyAlias = self[.hostKeyAlias]
        let alias = config.effectiveHostKeyAlias
        guard alias.isEmpty || alias.wholeMatch(of: hostPattern) != nil else { throw invalid(.hostKeyAlias) }

        guard let side = KVMSide(rawValue: self[.kvmSide]) else { throw invalid(.kvmSide) }
        config.kvmSide = side

        let fingerprint = self[.hostFingerprint]
        guard fingerprint.isEmpty || fingerprint.wholeMatch(of: fingerprintPattern) != nil else {
            throw invalid(.hostFingerprint)
        }
        config.hostFingerprint = fingerprint

        let resolution = self[.streamResolution]
        guard resolution.wholeMatch(of: resolutionPattern) != nil else { throw invalid(.streamResolution) }
        config.streamResolution = resolution

        guard let fps = Self.decimal(self[.streamFPS], maxDigits: 3), (30...240).contains(fps) else {
            throw invalid(.streamFPS)
        }
        config.streamFPS = fps

        switch self[.streamOverlay] {
        case "0": config.streamOverlay = false
        case "1": config.streamOverlay = true
        default: throw invalid(.streamOverlay)
        }

        let output = self[.streamOutput]
        guard output.wholeMatch(of: outputPattern) != nil else { throw invalid(.streamOutput) }
        config.streamOutput = output

        guard !lan.isEmpty || !tailscale.isEmpty else {
            throw ConfigError(
                reason: "at least one of \(ConfigKey.lanHost.rawValue) or \(ConfigKey.tailscaleHost.rawValue) is required")
        }
        return config
    }

    /// ASCII decimal digits only (no sign, no whitespace, no other scripts), read in base 10.
    private static func decimal(_ text: String, maxDigits: Int) -> Int? {
        let digits = text.utf8
        guard !digits.isEmpty, digits.allSatisfy({ $0 >= UInt8(ascii: "0") && $0 <= UInt8(ascii: "9") }) else {
            return nil
        }
        if digits.count > maxDigits { return nil }
        return Int(text, radix: 10)
    }

    private static func isValidIdentity(_ path: String) -> Bool {
        let bytes = path.utf8
        guard bytes.first == UInt8(ascii: "/") else { return false }
        let forbidden: [UInt8] = [0x0A, 0x0D, UInt8(ascii: "\""), UInt8(ascii: "'")]
        guard !bytes.contains(where: forbidden.contains) else { return false }
        return !("/" + path + "/").contains("/../")
    }
}
