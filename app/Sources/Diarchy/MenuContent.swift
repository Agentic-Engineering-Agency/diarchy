import AppKit
import DiarchyCore
import SwiftUI

/// The menu bar popover (spec §8.2): status line, mode picker and the three commands.
///
/// Ruling: `MenuBarExtra` uses the `.window` style. Apple's documentation does not say when a `.menu`-style body is
/// rebuilt, and `.help` tooltips do not render inside system pull-down menus. A `.window` popover is an ordinary
/// view, so it re-derives the mode exactly when its window becomes key (the popover being opened), plus `.task` for
/// the first show. That refresh is `AppModel.refreshMode()`, local and cheap; the full checklist runs at launch, when
/// Settings opens and after actions, never when this popover opens. No timer is involved. The reason the modes are
/// unavailable is visible caption text; `.help` repeats it on the disabled segments only.
struct MenuContent: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @State private var host = HostWindow()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header

            modePicker

            if let reason = model.modesDisabledReason {
                Label {
                    Text(reason)
                        .fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "info.circle")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            Divider()

            VStack(spacing: 2) {
                MenuRow("Settings…", symbol: "gearshape") { openSettings(.settings) }
                MenuRow("Diagnostics…", symbol: "stethoscope") { openSettings(.diagnostics) }
                MenuRow("Quit", symbol: "power") { NSApp.terminate(nil) }
            }
        }
        .padding(12)
        .frame(width: 300, alignment: .leading)
        .background(WindowReader(host: host))
        .task { await model.refreshMode() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { note in
            guard let window = note.object as? NSWindow, window === host.window else { return }
            Task { await model.refreshMode() }
        }
    }

    /// The status as a colored SF Symbol plus the status line (an error stays selectable so it can be copied).
    private var header: some View {
        let tone = model.statusTone
        return HStack(spacing: 10) {
            Image(systemName: tone.symbol)
                .font(.title2)
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(tone.color)
                .frame(width: 28)
                .accessibilityLabel(tone.accessibilityLabel)
            VStack(alignment: .leading, spacing: 1) {
                Text("Diarchy")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(model.statusLine)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            Spacer(minLength: 0)
            if model.isBusy {
                ProgressView().controlSize(.small)
            }
        }
        .accessibilityElement(children: .contain)
    }

    /// A segmented control drawn by hand: a system segmented `Picker` can only be disabled as a whole and draws a
    /// disabled selected segment in grey on grey. Each segment here is enabled on its own (`Off` stays
    /// clickable while the checklist runs), and the selected one always keeps a readable label. The track is a
    /// quiet capsule; the selected segment is accent-colored, as tinted glass on macOS 26 and later.
    private var modePicker: some View {
        let current = MenuMode(model.mode.mode)
        return HStack(spacing: 2) {
            ForEach(MenuMode.allCases) { choice in
                let selected = choice == current
                let enabled = model.isSelectable(choice.target)
                // The selected segment of a degraded KVM stays clickable: choosing it again repairs it.
                let repairs = choice == .kvm && model.mode.mode == .kvm(degraded: true)
                Button {
                    Task { await model.setMode(choice.target) }
                } label: {
                    Text(choice.title)
                        .font(.callout.weight(selected ? .semibold : .regular))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 5)
                        .foregroundStyle(selected ? Color.white : (enabled ? Color.primary : Color.secondary))
                        .selectionSurface(selected: selected, enabled: enabled, idleFill: false, in: Capsule())
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(!enabled)
                .help(help(for: choice, enabled: enabled, repairs: repairs))
                .accessibilityLabel(choice.title)
                .accessibilityHint(repairs ? "Repairs KVM" : "")
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        .padding(2)
        .background(.quaternary, in: Capsule())
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Mode")
    }

    private func help(for choice: MenuMode, enabled: Bool, repairs: Bool) -> String {
        if repairs && enabled { return "Repairs KVM" }
        return enabled ? "Switch to \(choice.title)" : (model.modesDisabledReason ?? choice.title)
    }

    private func openSettings(_ request: SettingsTabChoice.Request) {
        let windowOpen = NSApp.windows.contains { $0.title == AppDelegate.settingsTitle && $0.isVisible }
        model.showSettings(request, windowOpen: windowOpen)
        openWindow(id: "settings")
        NSApp.activate()
        // MenuBarExtraAccess drives the popover's presented state, so closing it through that binding keeps the status
        // item and SwiftUI in sync (`orderOut` or a simulated click on the status item do not).
        model.isMenuPresented = false
    }
}

/// A command row of the popover. Like a native menu item it fills with the accent color under the pointer.
private struct MenuRow: View {
    let title: String
    let symbol: String
    let action: () -> Void
    @State private var hovering = false

    init(_ title: String, symbol: String, action: @escaping () -> Void) {
        self.title = title
        self.symbol = symbol
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .frame(width: 18)
                    .accessibilityHidden(true)
                Text(title)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .foregroundStyle(hovering ? Color.white : Color.primary)
            .background(hovering ? Color.accentColor : Color.clear, in: RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

private enum MenuMode: CaseIterable, Identifiable {
    case off, kvm, streaming

    var id: Self { self }

    init?(_ mode: Mode) {
        switch mode {
        case .off: self = .off
        case .kvm: self = .kvm
        case .streaming: self = .streaming
        case .transitioning: return nil
        }
    }

    var title: String {
        switch self {
        case .off: "Off"
        case .kvm: "KVM"
        case .streaming: "Streaming"
        }
    }

    var target: ModeTarget {
        switch self {
        case .off: .off
        case .kvm: .kvm
        case .streaming: .streaming
        }
    }
}

/// The popover's window, found once the view is attached, so the key notification of other windows is ignored.
@MainActor
private final class HostWindow {
    weak var window: NSWindow?
}

private struct WindowReader: NSViewRepresentable {
    let host: HostWindow

    func makeNSView(context: Context) -> WindowObservingView {
        let view = WindowObservingView()
        view.onWindowChange = { [host] in host.window = $0 }
        return view
    }

    func updateNSView(_ view: WindowObservingView, context: Context) {
        view.onWindowChange = { [host] in host.window = $0 }
    }
}

private final class WindowObservingView: NSView {
    var onWindowChange: ((NSWindow?) -> Void)?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        onWindowChange?(window)
    }
}
