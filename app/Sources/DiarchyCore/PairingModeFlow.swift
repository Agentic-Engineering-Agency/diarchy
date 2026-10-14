import Foundation

/// What `PairingModeFlow.run` did, step by step; a step that did not run is `nil`.
public struct PairingRun: Sendable {
    /// The `off` the controller answered when KVM had to be switched off for the pairing.
    public var suspended: ModeSnapshot?
    /// The pairing's result; `nil` when KVM could not be switched off and nothing ran.
    public var pairing: Result<MoonlightPairingOutcome, MoonlightPairingError>?
    /// The `kvm` the controller answered when KVM was brought back after the pairing.
    public var restored: ModeSnapshot?

    public init(
        suspended: ModeSnapshot? = nil, pairing: Result<MoonlightPairingOutcome, MoonlightPairingError>? = nil,
        restored: ModeSnapshot? = nil
    ) {
        self.suspended = suspended
        self.pairing = pairing
        self.restored = restored
    }
}

/// Moonlight pairing is a transient operation: it leaves the app in the mode it found (spec §8.6). KVM and streaming
/// are exclusive and pairing needs the host stream, so an active KVM is switched off first and, whatever the pairing's
/// outcome, brought back through the normal mode path. A running stream is left running (the pairing then does not
/// stop it); off stays off.
public enum PairingModeFlow {
    /// `target` is the settled mode the app was in (`transitioning` is the caller's to refuse).
    public static func run(
        from target: ModeTarget, controller: ModeController,
        pair: @Sendable (_ hostStreamAlreadyRunning: Bool) async -> Result<MoonlightPairingOutcome, MoonlightPairingError>
    ) async -> PairingRun {
        switch target {
        case .off:
            return PairingRun(pairing: await pair(false))
        case .streaming:
            return PairingRun(pairing: await pair(true))
        case .kvm:
            let suspended = await controller.setMode(.off)
            guard suspended.mode == .off, suspended.error == nil else { return PairingRun(suspended: suspended) }
            let pairing = await pair(false)
            return PairingRun(suspended: suspended, pairing: pairing, restored: await controller.setMode(.kvm))
        }
    }
}
