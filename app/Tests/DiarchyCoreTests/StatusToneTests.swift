import Testing

@testable import DiarchyCore

private let config = DiarchyConfig(sshUser: "user", lanHost: "host.example")

private func checklist(_ overrides: [StepID: StepState] = [:]) -> ChecklistResult {
    ChecklistResult(
        steps: StepID.allCases.map { ChecklistStep(id: $0, state: overrides[$0] ?? .ok, message: "") },
        config: nil, probe: nil)
}

/// The tone follows exactly the state the status line text reports, so the menu's icon never disagrees with it.
@Suite struct StatusToneTests {
    @Test func anErrorIsAnErrorWhateverTheModeIs() {
        let modes: [Mode] = [.off, .kvm(degraded: false), .kvm(degraded: true), .streaming, .transitioning]
        for mode in modes {
            #expect(StatusLine.tone(config: config, checklist: checklist(), mode: mode, error: "boom") == .error)
        }
    }

    @Test func noConfigurationNeedsAttention() {
        #expect(StatusLine.tone(config: nil, checklist: nil, mode: .off, error: nil) == .attention)
        #expect(
            StatusLine.tone(config: config, checklist: checklist([.configuration: .failed]), mode: .off, error: nil)
                == .attention)
    }

    @Test func aDegradedKVMNeedsAttention() {
        #expect(
            StatusLine.tone(config: config, checklist: checklist(), mode: .kvm(degraded: true), error: nil)
                == .attention)
    }

    @Test func aRunningModeIsActive() {
        #expect(
            StatusLine.tone(config: config, checklist: checklist(), mode: .kvm(degraded: false), error: nil)
                == .active)
        #expect(StatusLine.tone(config: config, checklist: checklist(), mode: .streaming, error: nil) == .active)
    }

    @Test func offIsInactiveEvenWithAnIncompleteChecklist() {
        #expect(StatusLine.tone(config: config, checklist: checklist(), mode: .off, error: nil) == .inactive)
        #expect(
            StatusLine.tone(config: config, checklist: checklist([.ssh: .failed]), mode: .off, error: nil)
                == .inactive)
    }

    @Test func aModeChangeIsBusy() {
        #expect(StatusLine.tone(config: config, checklist: checklist(), mode: .transitioning, error: nil) == .busy)
    }

    @Test func anEmptyErrorIsNoError() {
        #expect(StatusLine.tone(config: config, checklist: checklist(), mode: .off, error: "") == .inactive)
    }
}
