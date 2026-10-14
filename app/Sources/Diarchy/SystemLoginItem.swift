import DiarchyCore
import ServiceManagement

/// The real login item: this app's own bundle registered through `SMAppService.mainApp` (macOS 13+). It keeps no
/// state; every call asks the system, so a change made in System Settings is seen on the next read.
struct SystemLoginItem: LoginItemService {
    var status: LoginItemStatus {
        switch SMAppService.mainApp.status {
        case .enabled: .enabled
        case .requiresApproval: .requiresApproval
        case .notFound: .notFound
        case .notRegistered: .notRegistered
        @unknown default: .notRegistered
        }
    }

    func register() throws { try SMAppService.mainApp.register() }

    func unregister() throws { try SMAppService.mainApp.unregister() }

    /// System Settings › General › Login Items, where the user approves a pending item.
    static func openSystemSettings() { SMAppService.openSystemSettingsLoginItems() }
}
