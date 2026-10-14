import Testing

@testable import DiarchyCore

private let config = DiarchyConfig(sshUser: "user", lanHost: "host.example")

private func checklist(_ overrides: [StepID: StepState] = [:], messages: [StepID: String] = [:]) -> ChecklistResult {
    ChecklistResult(
        steps: StepID.allCases.map {
            ChecklistStep(id: $0, state: overrides[$0] ?? .ok, message: messages[$0] ?? "")
        },
        config: nil, probe: nil)
}

@Suite struct ModeAvailabilityTests {
    @Test func aCompleteChecklistEnablesTheModesWithoutAReason() {
        let result = ModeAvailability.evaluate(config: config, checklist: checklist(), mode: .off)
        #expect(result.enabled)
        #expect(result.reason == nil)
    }

    @Test func noConfigurationDisablesWithTheSettingsReason() {
        let result = ModeAvailability.evaluate(config: nil, checklist: nil, mode: .off)
        #expect(!result.enabled)
        #expect(result.reason == "Set up the host connection in Settings.")
        // A complete checklist cannot enable the picker without a configuration.
        #expect(!ModeAvailability.evaluate(config: nil, checklist: checklist(), mode: .off).enabled)
    }

    @Test func aConfigurationBeforeTheFirstChecklistWaits() {
        let result = ModeAvailability.evaluate(config: config, checklist: nil, mode: .off)
        #expect(!result.enabled)
        #expect(result.reason == "Checking the setup…")
    }

    @Test func anIncompleteChecklistNamesTheFirstUnfinishedStepAndItsMessage() {
        let result = ModeAvailability.evaluate(
            config: config,
            checklist: checklist(
                [.ssh: .failed, .pairing: .actionRequired],
                messages: [.ssh: "No route to host", .pairing: "pendiente"]),
            mode: .off)
        #expect(!result.enabled)
        #expect(result.reason == "Complete the setup (SSH): No route to host")
    }

    @Test func pendingStepsCountAsUnfinished() {
        let result = ModeAvailability.evaluate(
            config: config, checklist: checklist([.agent: .pending], messages: [.agent: "Waiting"]), mode: .off)
        #expect(result.reason == "Complete the setup (Host Agent): Waiting")
    }

    @Test(arguments: [Mode.kvm(degraded: false), .kvm(degraded: true), .streaming])
    func aRunningModeStaysSelectableEvenWithAnIncompleteChecklistOrNoConfig(mode: Mode) {
        let broken = checklist([.ssh: .failed], messages: [.ssh: "down"])
        for (config, list) in [(config, broken), (config, nil), (nil, nil)] as [(DiarchyConfig?, ChecklistResult?)] {
            let result = ModeAvailability.evaluate(config: config, checklist: list, mode: mode)
            #expect(result.enabled)
            #expect(result.reason == nil)
        }
    }

    @Test func aTransitionFollowsTheChecklistRule() {
        #expect(ModeAvailability.evaluate(config: config, checklist: checklist(), mode: .transitioning).enabled)
        #expect(
            !ModeAvailability.evaluate(config: config, checklist: checklist([.ssh: .failed]), mode: .transitioning)
                .enabled)
    }

    @Test(arguments: [Mode.kvm(degraded: false), .kvm(degraded: true), .streaming])
    func offStaysSelectableWhileAModeRunsWhateverTheChecklistSays(mode: Mode) {
        let broken = checklist([.ssh: .failed])
        for (config, list) in [(config, broken), (config, nil), (nil, nil)] as [(DiarchyConfig?, ChecklistResult?)] {
            #expect(ModeAvailability.isSelectable(.off, config: config, checklist: list, mode: mode))
        }
    }

    @Test func kvmAndStreamingNeedAConfigurationAndACompleteChecklist() {
        for target in [ModeTarget.kvm, .streaming] {
            #expect(ModeAvailability.isSelectable(target, config: config, checklist: checklist(), mode: .off))
            #expect(!ModeAvailability.isSelectable(target, config: config, checklist: nil, mode: .off))
            #expect(
                !ModeAvailability.isSelectable(target, config: config, checklist: checklist([.ssh: .failed]), mode: .off))
            #expect(!ModeAvailability.isSelectable(target, config: nil, checklist: checklist(), mode: .off))
        }
    }

    @Test func switchingBetweenRunningModesStillNeedsAHealthyChecklist() {
        let broken = checklist([.ssh: .failed])
        #expect(!ModeAvailability.isSelectable(.streaming, config: config, checklist: broken, mode: .kvm(degraded: false)))
        #expect(!ModeAvailability.isSelectable(.kvm, config: config, checklist: nil, mode: .streaming))
        // The mode that already runs is the current choice, not a switch.
        #expect(ModeAvailability.isSelectable(.streaming, config: config, checklist: broken, mode: .streaming))
        #expect(ModeAvailability.isSelectable(.kvm, config: config, checklist: broken, mode: .kvm(degraded: true)))
    }

    @Test(arguments: [
        (Mode.off, ModeTarget.off, true), (.off, .kvm, false), (.off, .streaming, false),
        (.kvm(degraded: false), .off, false), (.kvm(degraded: false), .kvm, true),
        (.kvm(degraded: false), .streaming, false),
        // A degraded KVM is repaired by selecting KVM again, so that is a real transition.
        (.kvm(degraded: true), .off, false), (.kvm(degraded: true), .kvm, false),
        (.kvm(degraded: true), .streaming, false),
        (.streaming, .off, false), (.streaming, .kvm, false), (.streaming, .streaming, true),
        (.transitioning, .off, false), (.transitioning, .kvm, false), (.transitioning, .streaming, false),
    ] as [(Mode, ModeTarget, Bool)])
    func onlyReachingTheModeThatAlreadyRunsHealthyIsANoOp(current: Mode, target: ModeTarget, expected: Bool) {
        #expect(ModeAvailability.isNoOp(current: current, target: target) == expected)
    }

    @Test func aDegradedKVMCanBeSelectedToRepairItWithoutTheChecklist() {
        let broken = checklist([.ssh: .failed])
        #expect(ModeAvailability.isSelectable(.kvm, config: config, checklist: broken, mode: .kvm(degraded: true)))
        // The mode that already runs counts as the current choice whatever the checklist says.
        #expect(ModeAvailability.isSelectable(.kvm, config: config, checklist: broken, mode: .kvm(degraded: false)))
    }

    @Test(arguments: [
        (.off, false), (.kvm(degraded: false), false), (.kvm(degraded: true), false),
        (.streaming, true), (.transitioning, true),
    ] as [(Mode, Bool)])
    func installsAreBlockedOnlyWhileStreamingOrTransitioning(mode: Mode, blocked: Bool) {
        #expect((ModeAvailability.installBlockedReason(mode: mode) != nil) == blocked)
    }
}
