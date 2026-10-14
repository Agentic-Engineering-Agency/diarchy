import AppKit
import DiarchyCore

/// `NSRunningApplication` as `ActivatableApp`. `NSRunningApplication` is documented as usable from any thread;
/// activation itself runs on the main actor, where this app yields its activation to the target first (macOS 14+),
/// as a background menu-bar app is otherwise not allowed to raise another app's windows.
struct RunningApplicationHandle: ActivatableApp, @unchecked Sendable {  // NSRunningApplication is thread-safe
    let application: NSRunningApplication

    /// `nil` until the process has registered as an application with LaunchServices.
    static func lookup(pid: Int32) -> RunningApplicationHandle? {
        NSRunningApplication(processIdentifier: pid).map(RunningApplicationHandle.init)
    }

    func activate() async {
        await MainActor.run {
            NSApp.yieldActivation(to: application)
            application.activate()
        }
    }

    func isActive() async -> Bool { application.isActive }
}
