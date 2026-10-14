import Foundation

/// What the system reports for the app's "open at login" registration. Mirrors `SMAppService.Status`, so the
/// logic here is testable without ServiceManagement.
public enum LoginItemStatus: Sendable, Equatable {
    case notRegistered, enabled, requiresApproval, notFound
}

/// The app's login-item registration; the app target adapts `SMAppService.mainApp` to it.
public protocol LoginItemService: Sendable {
    var status: LoginItemStatus { get }
    func register() throws
    func unregister() throws
}

/// The `Open at Login` toggle: what the switch shows and what to tell the user about it.
public enum LoginItem {
    public struct State: Equatable, Sendable {
        /// The switch position. `requiresApproval` stays on: the user asked for it and only the approval is missing.
        public let isOn: Bool
        /// The system waits for the user to approve the item in System Settings; the UI offers a way there.
        public let needsApproval: Bool
        /// Guidance shown under the switch (not a failure).
        public let hint: String?
        /// Why the last change failed.
        public let error: String?
    }

    /// The state for a plain status read (launch, window open, app activation). `notFound` is plain off here:
    /// `SMAppService.mainApp` reports it for an installed app that was never registered, so it says nothing about
    /// the installation.
    public static func state(for status: LoginItemStatus) -> State {
        state(for: status, error: nil, registerAttempted: false)
    }

    /// Applies the user's choice, then reports the status the system actually ended up with. Asking for the state it
    /// is already in does not touch the system.
    public static func setEnabled(_ enabled: Bool, service: some LoginItemService) -> State {
        let before = service.status
        let isOn = before == .enabled || before == .requiresApproval
        guard isOn != enabled else { return state(for: before) }
        do {
            if enabled {
                try service.register()
            } else {
                try service.unregister()
            }
            return state(for: service.status, error: nil, registerAttempted: enabled)
        } catch {
            return state(
                for: service.status,
                error: "Could not change “Open at Login”: \(error.localizedDescription)",
                registerAttempted: enabled)
        }
    }

    /// `registerAttempted`: the user just turned the item on, so a `notFound` result is a real answer (the system
    /// could not find the app to register) and worth explaining.
    private static func state(for status: LoginItemStatus, error: String?, registerAttempted: Bool) -> State {
        switch status {
        case .enabled:
            State(isOn: true, needsApproval: false, hint: nil, error: error)
        case .notRegistered:
            State(isOn: false, needsApproval: false, hint: nil, error: error)
        case .requiresApproval:
            State(
                isOn: true, needsApproval: true,
                hint: "Approval needed: turn it on in System Settings › General › Login Items & Extensions.",
                error: error)
        case .notFound:
            State(
                isOn: false, needsApproval: false,
                hint: registerAttempted
                    ? "macOS cannot find the app. Install it in Applications or ~/Applications and try again."
                    : nil,
                error: error)
        }
    }
}
