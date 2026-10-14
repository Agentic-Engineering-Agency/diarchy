import Testing

@testable import DiarchyCore

private let config = DiarchyConfig(sshUser: "user", lanHost: "host.example")

/// Seven steps in order, all `ok` except the ones in `overrides`.
private func checklist(_ overrides: [StepID: StepState] = [:]) -> ChecklistResult {
    ChecklistResult(
        steps: StepID.allCases.map { ChecklistStep(id: $0, state: overrides[$0] ?? .ok, message: "") },
        config: nil, probe: nil)
}

@Suite struct StatusLineTests {
    @Test func noConfigurationShowsNotConfigured() {
        #expect(StatusLine.make(config: nil, checklist: nil, mode: .off, error: nil) == "Not configured")
        #expect(StatusLine.make(config: nil, checklist: checklist(), mode: .off, error: nil) == "Not configured")
    }

    @Test func noConfigurationBeatsTheMode() {
        #expect(
            StatusLine.make(config: nil, checklist: nil, mode: .kvm(degraded: false), error: nil) == "Not configured")
    }

    @Test func failedConfigurationStepShowsNotConfigured() {
        let result = checklist([.configuration: .failed, .ssh: .pending])
        #expect(StatusLine.make(config: config, checklist: result, mode: .off, error: nil) == "Not configured")
    }

    @Test func offWithACompleteChecklistIsDisconnected() {
        #expect(StatusLine.make(config: config, checklist: checklist(), mode: .off, error: nil) == "Disconnected")
    }

    @Test func offWithAnIncompleteChecklistIsDisconnected() {
        let result = checklist([.ssh: .failed])
        #expect(StatusLine.make(config: config, checklist: result, mode: .off, error: nil) == "Disconnected")
    }

    @Test func offBeforeAnyChecklistRunIsDisconnected() {
        #expect(StatusLine.make(config: config, checklist: nil, mode: .off, error: nil) == "Disconnected")
    }

    @Test func kvmEnabled() {
        #expect(
            StatusLine.make(config: config, checklist: checklist(), mode: .kvm(degraded: false), error: nil)
                == "KVM active")
    }

    @Test func kvmDegraded() {
        #expect(
            StatusLine.make(config: config, checklist: checklist(), mode: .kvm(degraded: true), error: nil)
                == "KVM degraded")
    }

    @Test func streaming() {
        #expect(
            StatusLine.make(config: config, checklist: checklist(), mode: .streaming, error: nil)
                == "Streaming active")
    }

    @Test func theModeShowsEvenWhenTheChecklistIsIncomplete() {
        let result = checklist([.pairing: .actionRequired])
        #expect(
            StatusLine.make(config: config, checklist: result, mode: .kvm(degraded: false), error: nil)
                == "KVM active")
    }

    @Test func transitioning() {
        #expect(
            StatusLine.make(config: config, checklist: checklist(), mode: .transitioning, error: nil)
                == "Switching mode…")
    }

    @Test func errorBeatsEveryOtherState() {
        let modes: [Mode] = [.off, .kvm(degraded: false), .kvm(degraded: true), .streaming, .transitioning]
        for mode in modes {
            #expect(
                StatusLine.make(config: config, checklist: checklist(), mode: mode, error: "boom") == "Error: boom")
        }
        #expect(StatusLine.make(config: nil, checklist: nil, mode: .off, error: "boom") == "Error: boom")
        #expect(
            StatusLine.make(config: config, checklist: checklist([.configuration: .failed]), mode: .off, error: "boom")
                == "Error: boom")
    }

    @Test func anEmptyErrorIsNoError() {
        #expect(StatusLine.make(config: config, checklist: checklist(), mode: .off, error: "") == "Disconnected")
    }
}
