/// The tabs of the Settings window.
public enum SettingsTab: Hashable, Sendable { case connection, screens, streaming, diagnostics }

/// Which tab the Settings window shows after a menu command (spec §8.2).
public enum SettingsTabChoice {
    public enum Request: Sendable {
        /// `Settings…`
        case settings
        /// `Diagnostics…`
        case diagnostics
    }

    /// `Diagnostics…` always lands on Diagnostics, also while the window is open on another tab. `Settings…` leaves
    /// an open window where it is and otherwise starts on Connection; without a configuration only Connection makes sense.
    public static func tab(for request: Request, current: SettingsTab, windowOpen: Bool, hasConfig: Bool) -> SettingsTab {
        switch request {
        case .diagnostics: .diagnostics
        case .settings: windowOpen && hasConfig ? current : .connection
        }
    }
}
