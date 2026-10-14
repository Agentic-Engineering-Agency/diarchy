import Foundation

/// User-facing wording for the configuration form, kept out of the views so it can be tested.
public enum ConfigFieldText {
    private static let labels: [String: String] = [
        "DIARCHY_SSH_USER": "SSH user",
        "DIARCHY_LAN_HOST": "LAN host",
        "DIARCHY_TAILSCALE_HOST": "Tailscale host",
        "DIARCHY_SSH_PORT": "SSH port",
        "DIARCHY_SSH_IDENTITY_FILE": "SSH key",
        "DIARCHY_HOST_KEY_ALIAS": "Host key alias",
        "DIARCHY_HOST_FINGERPRINT": "Host fingerprint",
        "DIARCHY_KVM_SIDE": "KVM side",
        "DIARCHY_STREAM_RESOLUTION": "Streaming resolution",
        "DIARCHY_STREAM_FPS": "Streaming FPS",
        "DIARCHY_STREAM_OVERLAY": "Streaming overlay",
        "DIARCHY_STREAM_OUTPUT": "Stream output",
    ]

    /// The sentence for a `ConfigError.reason` (spec §4.5), naming the field as the form does. A reason this does not
    /// know (a file that cannot be written, say) is returned unchanged.
    public static func message(for reason: String) -> String {
        if reason.hasPrefix("at least one of ") { return "Enter at least one host (LAN or Tailscale)" }
        if reason.hasSuffix(" is required"), let name = labels[String(reason.dropLast(" is required".count))] {
            return "Enter the \(name)"
        }
        if reason.hasSuffix(" is invalid"), let name = labels[String(reason.dropLast(" is invalid".count))] {
            return "\(name) is invalid"
        }
        return reason
    }

    /// The port field's text as the number the draft holds: empty means the default 22, text that is not a number is
    /// 0 so the validator reports the port as invalid.
    public static func port(from text: String) -> Int {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { return 22 }
        return trimmed.utf8.allSatisfy { $0 >= UInt8(ascii: "0") && $0 <= UInt8(ascii: "9") } ? Int(trimmed) ?? 0 : 0
    }
}
