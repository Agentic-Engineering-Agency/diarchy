import AppKit
import DiarchyCore
import MenuBarExtraAccess
import SwiftUI

/// The Settings window must open only when the menu asks for it, never at launch. macOS 15 has a scene modifier for
/// that (`defaultLaunchBehavior(.suppressed)`), but `SceneBuilder` cannot branch on `#available`, so the app has two
/// `App` types that share every scene and differ only by that modifier; the entry point picks one. On macOS 14
/// `AppDelegate` hides the window instead.
@main
enum Launcher {
    static func main() {
        if #available(macOS 15, *) {
            ModernApp.main()
        } else {
            LegacyApp.main()
        }
    }
}

@available(macOS 15, *)
struct ModernApp: App {
    @NSApplicationDelegateAdaptor(TerminationDelegate.self) private var delegate
    @State private var model = AppModel.shared

    var body: some Scene {
        menuBarScene(model: model)
        settingsScene(model: model)
            .defaultLaunchBehavior(.suppressed)
    }
}

struct LegacyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel.shared

    var body: some Scene {
        menuBarScene(model: model)
        settingsScene(model: model)
    }
}

@MainActor
private func menuBarScene(model: AppModel) -> some Scene {
    MenuBarExtra("Diarchy", systemImage: "display.2") {
        MenuContent()
            .environment(model)
    }
    .menuBarExtraAccess(isPresented: Bindable(model).isMenuPresented)
    .menuBarExtraStyle(.window)
}

@MainActor
private func settingsScene(model: AppModel) -> some Scene {
    Window(AppDelegate.settingsTitle, id: "settings") {
        SettingsView()
            .environment(model)
    }
    .defaultSize(width: 640, height: 560)
    .windowResizability(.contentMinSize)
}

/// The one termination path. Every way of quitting (menu `Quit`, Cmd-Q, AppleScript `quit`, logout, shutdown) ends in
/// `applicationShouldTerminate`, which stops a running stream or pairing first (`Termination` decides and bounds the
/// wait) so Moonlight and the host's Sunshine stream never outlive the app. AppKit keeps the app alive meanwhile
/// (`terminateLater`) and is told to go on once the cleanup is done or has timed out.
@MainActor
class TerminationDelegate: NSObject, NSApplicationDelegate {
    /// A terminate request arrived while the cleanup of an earlier one is still running.
    private var cleaningUp = false

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if cleaningUp { return .terminateLater }
        let model = AppModel.shared
        let reply = Termination.begin(
            mode: model.mode.mode, pairingRunning: model.pairingRunning,
            cleanup: { await model.prepareForTermination() },
            onTimeout: { await model.stopStreamOnQuitTimeout() },
            reply: {
                Task { @MainActor in NSApp.reply(toApplicationShouldTerminate: true) }
            })
        switch reply {
        case .now: return .terminateNow
        case .later:
            cleaningUp = true
            return .terminateLater
        }
    }
}

/// macOS 14 only (see `Launcher`): orders the Settings window out when it appears during launch. Event-driven: it
/// reacts to the window's own expose notification, no timer. After the launch run-loop turn every Settings window
/// is the user's (the menu opened it) and is left alone.
@MainActor
final class AppDelegate: TerminationDelegate {
    static let settingsTitle = "Settings"

    private var launching = true
    private var observer: (any NSObjectProtocol)?

    func applicationWillFinishLaunching(_ notification: Notification) {
        observer = NotificationCenter.default.addObserver(
            forName: NSWindow.didExposeNotification, object: nil, queue: .main
        ) { [weak self] note in
            guard let window = note.object as? NSWindow else { return }
            MainActor.assumeIsolated { self?.hideIfOpenedByLaunch(window) }
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        hideSettingsWindows()
        // Windows created later in the launch sequence still arrive before this block runs.
        DispatchQueue.main.async { [self] in
            hideSettingsWindows()
            launching = false
            if let observer { NotificationCenter.default.removeObserver(observer) }
            observer = nil
        }
    }

    private func hideIfOpenedByLaunch(_ window: NSWindow) {
        guard launching, window.title == Self.settingsTitle else { return }
        window.orderOut(nil)
    }

    private func hideSettingsWindows() {
        for window in NSApp.windows where window.title == Self.settingsTitle { window.orderOut(nil) }
    }
}
