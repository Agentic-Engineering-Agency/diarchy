import Foundation

/// The menu's status line (spec §8.2), derived from what the app knows. Pure: no state, no I/O.
public enum StatusLine {
    /// How the menu should present the status next to its text (icon and color).
    public enum Tone: Sendable, Equatable {
        /// An action failed.
        case error
        /// Something needs the user: no usable configuration, or KVM running degraded.
        case attention
        /// KVM or streaming is running.
        case active
        /// Everything is off.
        case inactive
        /// A mode change is in flight.
        case busy
    }

    /// Precedence: an error, then "no configuration", then the mode.
    /// - `config` is the loaded configuration, `nil` when there is none.
    /// - A `checklist` whose configuration step failed means the configuration is not usable, so it also reads as
    ///   `Not configured` (the two can disagree when the file changed between the load and the checklist run).
    /// - `mode` off is always `Disconnected`, whether or not the checklist is complete.
    /// - `error` is the text after `Error: `; an empty string counts as no error.
    public static func make(config: DiarchyConfig?, checklist: ChecklistResult?, mode: Mode, error: String?) -> String {
        resolve(config: config, checklist: checklist, mode: mode, error: error).text
    }

    /// The tone of the text `make` returns for the same inputs.
    public static func tone(config: DiarchyConfig?, checklist: ChecklistResult?, mode: Mode, error: String?) -> Tone {
        resolve(config: config, checklist: checklist, mode: mode, error: error).tone
    }

    private static func resolve(
        config: DiarchyConfig?, checklist: ChecklistResult?, mode: Mode, error: String?
    ) -> (text: String, tone: Tone) {
        if let error, !error.isEmpty { return ("Error: \(error)", .error) }
        let configurationFailed = checklist?.steps.contains { $0.id == .configuration && $0.state == .failed } ?? false
        if config == nil || configurationFailed { return ("Not configured", .attention) }
        switch mode {
        case .off: return ("Disconnected", .inactive)
        case .kvm(degraded: false): return ("KVM active", .active)
        case .kvm(degraded: true): return ("KVM degraded", .attention)
        case .streaming: return ("Streaming active", .active)
        case .transitioning: return ("Switching mode…", .busy)
        }
    }
}
