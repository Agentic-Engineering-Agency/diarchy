import Foundation

/// Whether the menu's mode picker can be used, and when not, why (spec §8.2: disabled with a tooltip while setup is
/// incomplete). Pure: no state, no I/O.
public enum ModeAvailability {
    /// The modes are available once a configuration is loaded and the whole checklist is `ok`. While KVM or streaming
    /// runs they stay available whatever the checklist says (the link may have dropped), otherwise the user could
    /// not switch the running mode off. `reason` is `nil` exactly when `enabled`.
    public static func evaluate(
        config: DiarchyConfig?, checklist: ChecklistResult?, mode: Mode
    ) -> (enabled: Bool, reason: String?) {
        switch mode {
        case .kvm, .streaming: return (true, nil)
        case .off, .transitioning: break
        }
        guard config != nil else { return (false, "Set up the host connection in Settings.") }
        guard let checklist else { return (false, "Checking the setup…") }
        guard let step = checklist.steps.first(where: { $0.state != .ok }) else {
            // An empty or short checklist is not a complete one.
            return checklist.isComplete ? (true, nil) : (false, "Checking the setup…")
        }
        return (false, "Complete the setup (\(step.title)): \(step.message)")
    }

    /// Choosing `target` while `current` runs changes nothing: the mode already runs and is healthy. A degraded KVM is
    /// not a no-op, because selecting KVM again repairs it; `.transitioning` is never one.
    public static func isNoOp(current: Mode, target: ModeTarget) -> Bool {
        switch (current, target) {
        case (.off, .off), (.kvm(degraded: false), .kvm), (.streaming, .streaming): true
        default: false
        }
    }

    /// Why "Install …" must wait, `nil` when installing is allowed. A host install reinstalls and restarts services
    /// (Lan Mouse among them), which must not happen under a running stream or half-way through a mode change.
    public static func installBlockedReason(mode: Mode) -> String? {
        switch mode {
        case .streaming:
            "Streaming is running. Switch to Off before installing, because the install restarts host services."
        case .transitioning:
            "A mode change is in progress. Wait for it to finish before installing."
        case .off, .kvm:
            nil
        }
    }

    /// Whether the picker's segment for `target` can be chosen. `Off` always can: it only runs local commands,
    /// and the user must be able to stop a running mode whatever the checklist says. KVM and streaming need a
    /// configuration and a complete checklist; the mode that already runs counts as chosen, not as a switch (and a
    /// degraded KVM can be chosen again to repair it).
    public static func isSelectable(
        _ target: ModeTarget, config: DiarchyConfig?, checklist: ChecklistResult?, mode: Mode
    ) -> Bool {
        switch (target, mode) {
        case (.off, _), (.kvm, .kvm), (.streaming, .streaming): return true
        default: return config != nil && (checklist?.isComplete ?? false)
        }
    }
}
