import AppKit
import DiarchyCore
import SwiftUI

/// Settings › Connection: the connection fields, `Save and Check`, and the setup checklist below.
struct ConnectionTab: View {
    @Environment(AppModel.self) private var model
    /// What the user typed in the port field. The draft holds the number, or 0 when the text is not a number, so
    /// the configuration validator reports the reason for it like for any other field.
    @State private var portText = ""
    /// Why the last chosen identity file was refused; the refused path is never stored.
    @State private var identityError: String?

    private static let pristineHint = "Enter the connection details for the host."

    private static let version =
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "Development build"

    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                TextField("SSH User", text: $model.draft.sshUser)
                TextField("LAN Host", text: $model.draft.lanHost, prompt: Text("192.0.2.10"))
                TextField("Tailscale Host", text: $model.draft.tailscaleHost, prompt: Text("Optional"))
                TextField("SSH Port", text: $portText, prompt: Text("22"))
                    .onChange(of: portText) { _, text in
                        model.draft.sshPort = ConfigFieldText.port(from: text)
                    }
                identityRow
            } header: {
                Text("Host Connection")
            } footer: {
                Text("Tailscale is optional. The LAN route works without it.")
            }

            Section {
                saveRow
            } footer: {
                saveFooter
            }

            Section {
                Toggle("Open at Login", isOn: launchAtLogin)
            } header: {
                Text("General")
            } footer: {
                loginItemFooter
            }

            Section("Checklist") {
                ChecklistView()
            }

            Section("About") {
                LabeledContent("Version", value: Self.version)
            }
        }
        .formStyle(.grouped)
        .onAppear {
            syncPortText()
            model.refreshLoginItem()
        }
        .onChange(of: model.draft.sshPort) { syncPortText() }
        // The user may approve the login item in System Settings and come back.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            model.refreshLoginItem()
        }
    }

    /// Makes the port text follow the draft when the draft changed from outside (a refresh loaded the saved file,
    /// or the tab appeared for the first time). Text that is not a number maps to 0 like the draft does, so it is
    /// left alone: coming back to the tab must not turn "abc" into "0".
    private func syncPortText() {
        if ConfigFieldText.port(from: portText) != model.draft.sshPort {
            portText = String(model.draft.sshPort)
        }
    }

    // MARK: Identity file

    @ViewBuilder
    private var identityRow: some View {
        LabeledContent("SSH Key (Optional)") {
            VStack(alignment: .trailing, spacing: 4) {
                HStack(spacing: 8) {
                    Text(model.draft.sshIdentityFile.isEmpty ? "None" : model.draft.sshIdentityFile)
                        .font(.system(.callout, design: .monospaced))
                        .foregroundStyle(model.draft.sshIdentityFile.isEmpty ? .secondary : .primary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                    Button("Choose…") { chooseIdentity() }
                        .secondaryActionStyle()
                        .controlSize(.small)
                    if !model.draft.sshIdentityFile.isEmpty {
                        Button("Remove") {
                            model.draft.sshIdentityFile = ""
                            identityError = nil
                        }
                        .secondaryActionStyle()
                        .controlSize(.small)
                    }
                }
                if let identityError {
                    VStack(alignment: .trailing, spacing: 2) {
                        InlineMessage(.error, ConfigFieldText.message(for: identityError))
                        Text(identityError)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.trailing)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func chooseIdentity() {
        let panel = NSOpenPanel()
        panel.title = "SSH Key"
        panel.prompt = "Choose"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser.appending(path: ".ssh")
        Task { @MainActor in
            guard await panel.begin() == .OK, let path = panel.url?.path else { return }
            if let reason = model.validateIdentityPath(path) {
                identityError = reason
                return
            }
            identityError = nil
            model.draft.sshIdentityFile = path
        }
    }

    // MARK: Save

    /// The reason the connection fields cannot be saved, from the validator the model's save runs (scoped to this
    /// tab's fields; see `DiarchyConfig.connectionFieldsError`). `Save and Check` saves the whole shared
    /// draft, including unsaved edits made in the Streaming tab.
    private var draftError: String? { model.draft.connectionFieldsError }

    /// Nothing has been typed yet: the draft is still the empty one of a fresh install, so the reason is held back
    /// until the first edit.
    private var draftIsPristine: Bool {
        let draft = model.draft
        return draft.sshUser.isEmpty && draft.lanHost.isEmpty && draft.tailscaleHost.isEmpty
            && draft.sshPort == 22 && draft.sshIdentityFile.isEmpty
    }

    private var saveRow: some View {
        HStack(spacing: 8) {
            if model.checklistRunning {
                ProgressView().controlSize(.small)
            }
            Spacer()
            Button("Save and Check") {
                Task { await model.saveAndCheck() }
            }
            .keyboardShortcut(.defaultAction)
            .disabled(model.checklistRunning || model.isBusy || draftError != nil)
            .primaryActionStyle()
        }
    }

    /// Why saving is not possible yet, shown under the button like the explanation of a System Settings row.
    @ViewBuilder
    private var saveFooter: some View {
        if let draftError {
            Text(draftIsPristine ? Self.pristineHint : ConfigFieldText.message(for: draftError))
                .foregroundStyle(draftIsPristine ? Color.secondary : Color.red)
                .help(draftError)
        }
    }

    // MARK: Login item

    private var launchAtLogin: Binding<Bool> {
        Binding(get: { model.loginItem.isOn }, set: { model.setLaunchAtLogin($0) })
    }

    @ViewBuilder
    private var loginItemFooter: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let error = model.loginItem.error {
                InlineMessage(.error, error)
            }
            if let hint = model.loginItem.hint {
                InlineMessage(.warning, hint)
            }
            if model.loginItem.needsApproval {
                Button("Open System Settings") { SystemLoginItem.openSystemSettings() }
                    .buttonStyle(.link)
            }
        }
    }
}
