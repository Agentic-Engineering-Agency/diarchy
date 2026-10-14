import DiarchyCore
import SwiftUI

/// The Settings window (spec §8.3): four tabs, always refreshed when shown. `AppModel.selectedTab` decides which
/// tab is open, so the menu can open the window on a given tab.
struct SettingsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        TabView(selection: $model.selectedTab) {
            ConnectionTab()
                .tabItem { Label("Connection", systemImage: "network") }
                .tag(SettingsTab.connection)
            ScreensTab()
                .tabItem { Label("Screens", systemImage: "display.2") }
                .tag(SettingsTab.screens)
            StreamingTab()
                .tabItem { Label("Streaming", systemImage: "play.display") }
                .tag(SettingsTab.streaming)
            DiagnosticsTab()
                .tabItem { Label("Diagnostics", systemImage: "stethoscope") }
                .tag(SettingsTab.diagnostics)
        }
        .alert(
            "Confirm Host Key",
            isPresented: Binding(get: { model.hostKeyPrompt != nil }, set: { _ in })
        ) {
            Button("Trust Host") { model.answerHostKey(true) }
            Button("Cancel", role: .cancel) { model.answerHostKey(false) }
        } message: {
            Text(
                "\(model.hostKeyPrompt ?? "")\n\nCompare this fingerprint with the output of `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the host."
            )
        }
        .frame(minWidth: 560, minHeight: 480)
        .task {
            model.refreshLoginItem()
            await model.refresh()
        }
    }
}
