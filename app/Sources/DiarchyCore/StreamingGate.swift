import Foundation

/// The check before streaming starts (spec §6.3, §9). Pure: the caller probes the host with `--streaming`.
public enum StreamingGate {
    /// `nil` when streaming may start. A `required` action still pending on the host (`uinput-access`, …) refuses,
    /// naming each one with its title and reason and the exact command to run on the host. Recommended
    /// actions never block. The app only shows the command; it never runs it.
    public static func refusal(for probe: ProbeResult) -> String? {
        let pending = probe.requiredActions.filter { $0.severity == .required }
        guard !pending.isEmpty else { return nil }
        let details = pending.map {
            "\($0.title): \($0.reason) Run on the host: \($0.command)"
        }
        return "Streaming is blocked. " + details.joined(separator: " ")
    }
}
