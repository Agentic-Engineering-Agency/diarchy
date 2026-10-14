import Foundation
import Testing

@testable import DiarchyCore

/// A scripted login-item service: `status` is what the system reports, and register/unregister move it like the real
/// one does (a `requiresApproval` result is scripted through `statusAfterRegister`).
private final class FakeLoginItemService: LoginItemService, @unchecked Sendable {
    var status: LoginItemStatus
    var statusAfterRegister: LoginItemStatus = .enabled
    var registerError: (any Error)?
    var unregisterError: (any Error)?
    private(set) var registerCalls = 0
    private(set) var unregisterCalls = 0

    init(_ status: LoginItemStatus) { self.status = status }

    func register() throws {
        registerCalls += 1
        if let registerError { throw registerError }
        status = statusAfterRegister
    }

    func unregister() throws {
        unregisterCalls += 1
        if let unregisterError { throw unregisterError }
        status = .notRegistered
    }
}

private struct Boom: LocalizedError {
    var errorDescription: String? { "boom" }
}

@Suite struct LoginItemTests {
    @Test func enabledIsOnWithoutHint() {
        let state = LoginItem.state(for: .enabled)
        #expect(state.isOn)
        #expect(state.hint == nil)
        #expect(state.error == nil)
        #expect(!state.needsApproval)
    }

    @Test func notRegisteredIsOffWithoutHint() {
        let state = LoginItem.state(for: .notRegistered)
        #expect(!state.isOn)
        #expect(state.hint == nil)
        #expect(!state.needsApproval)
    }

    @Test func requiresApprovalStaysOnAndPointsToSystemSettings() {
        let state = LoginItem.state(for: .requiresApproval)
        #expect(state.isOn)
        let hint = state.hint ?? ""
        #expect(hint.contains("System Settings"))
        #expect(state.needsApproval)
    }

    /// `SMAppService.mainApp` reports `notFound` for an app that was never registered, even when it is installed, so a
    /// plain status read must look like any other "off".
    @Test func notFoundOnAPlainReadIsJustOffWithoutAHint() {
        let state = LoginItem.state(for: .notFound)
        #expect(!state.isOn)
        #expect(!state.needsApproval)
        #expect(state.hint == nil)
        #expect(state.error == nil)
    }

    @Test func notFoundAfterTheUserTurnedItOnExplainsTheAppMustBeInstalled() {
        let service = FakeLoginItemService(.notFound)
        service.statusAfterRegister = .notFound
        let state = LoginItem.setEnabled(true, service: service)
        #expect(service.registerCalls == 1)
        #expect(!state.isOn)
        #expect(state.hint?.contains("Applications") == true)
    }

    @Test func notFoundAfterAFailedRegisterKeepsTheHintAndTheError() {
        let service = FakeLoginItemService(.notFound)
        service.registerError = Boom()
        let state = LoginItem.setEnabled(true, service: service)
        #expect(state.hint != nil)
        #expect(state.error?.contains("boom") == true)
    }

    @Test func turningOffWhileTheSystemReportsNotFoundHasNoHintAndDoesNotTouchTheSystem() {
        let service = FakeLoginItemService(.notFound)
        let state = LoginItem.setEnabled(false, service: service)
        #expect(service.unregisterCalls == 0)
        #expect(!state.isOn)
        #expect(state.hint == nil)
    }

    @Test func turningOnRegistersAndReportsTheNewStatus() {
        let service = FakeLoginItemService(.notRegistered)
        let state = LoginItem.setEnabled(true, service: service)
        #expect(service.registerCalls == 1)
        #expect(state.isOn)
        #expect(state.error == nil)
    }

    @Test func turningOnCanLandInRequiresApprovalAndSaysSo() {
        let service = FakeLoginItemService(.notRegistered)
        service.statusAfterRegister = .requiresApproval
        let state = LoginItem.setEnabled(true, service: service)
        #expect(state.isOn)
        #expect(state.hint?.contains("System Settings") == true)
    }

    @Test func turningOffUnregisters() {
        let service = FakeLoginItemService(.enabled)
        let state = LoginItem.setEnabled(false, service: service)
        #expect(service.unregisterCalls == 1)
        #expect(!state.isOn)
    }

    @Test func turningOffAPendingApprovalUnregisters() {
        let service = FakeLoginItemService(.requiresApproval)
        let state = LoginItem.setEnabled(false, service: service)
        #expect(service.unregisterCalls == 1)
        #expect(!state.isOn)
    }

    @Test func alreadyInTheRequestedStateDoesNotCallTheSystem() {
        let on = FakeLoginItemService(.enabled)
        _ = LoginItem.setEnabled(true, service: on)
        #expect(on.registerCalls == 0)

        let pending = FakeLoginItemService(.requiresApproval)
        _ = LoginItem.setEnabled(true, service: pending)
        #expect(pending.registerCalls == 0)

        let off = FakeLoginItemService(.notRegistered)
        _ = LoginItem.setEnabled(false, service: off)
        #expect(off.unregisterCalls == 0)
    }

    @Test func aFailedRegisterReportsTheErrorAndTheRealStatus() {
        let service = FakeLoginItemService(.notRegistered)
        service.registerError = Boom()
        let state = LoginItem.setEnabled(true, service: service)
        #expect(!state.isOn)
        let message = state.error ?? ""
        #expect(message.contains("Open at Login"))
        #expect(message.contains("boom"))
    }

    @Test func aFailedUnregisterKeepsTheToggleOn() {
        let service = FakeLoginItemService(.enabled)
        service.unregisterError = Boom()
        let state = LoginItem.setEnabled(false, service: service)
        #expect(state.isOn)
        #expect(state.error != nil)
    }
}
