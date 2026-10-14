import DiarchyCore
import SwiftUI

/// Streaming: stream settings, install, Moonlight pairing and the host actions streaming still needs.
struct StreamingTab: View {
    @Environment(AppModel.self) private var model

    /// Only this tab's own fields are checked (the FPS stepper and the toggle cannot be invalid), so a problem in
    /// the Connection fields never blocks or shows up here. `Save` saves the whole shared draft, including unsaved
    /// Connection edits.
    private var resolutionIsValid: Bool {
        model.draft.streamResolution.wholeMatch(of: /[0-9]{3,4}x[0-9]{3,4}/) != nil
    }

    private var working: Bool { model.isBusy || model.installRunning || model.pairingRunning }

    var body: some View {
        @Bindable var model = model
        Form {
            Section("Quality") {
                TextField("Resolution", text: $model.draft.streamResolution, prompt: Text("1920x1080"))
                    .autocorrectionDisabled()
                if !resolutionIsValid {
                    InlineMessage(.error, "The resolution must use the format WIDTHxHEIGHT, for example 1920x1080.")
                }
                Stepper(value: $model.draft.streamFPS, in: 30...240, step: 10) {
                    Text("FPS: \(model.draft.streamFPS)")
                }
                Toggle("Show Performance Overlay", isOn: $model.draft.streamOverlay)
                Picker("Stream Output", selection: $model.draft.streamOutput) {
                    ForEach(outputChoices, id: \.value) { choice in
                        Text(choice.title).tag(choice.value)
                    }
                }
            }
            .disabled(!model.hasConfig)

            Section {
                HStack(spacing: 8) {
                    if working { ProgressView().controlSize(.small) }
                    Spacer()
                    Button("Pair Moonlight") { Task { await model.pairMoonlight() } }
                        .secondaryActionStyle()
                        .disabled(working || !model.hasConfig)
                    Button("Install Streaming") { Task { await install() } }
                        .secondaryActionStyle()
                        .disabled(working || !model.hasConfig || model.installBlockedReason != nil)
                        .help(model.installBlockedReason ?? "")
                    Button("Save") { Task { await model.saveAndCheck() } }
                        .primaryActionStyle()
                        .disabled(working || !model.hasConfig || !resolutionIsValid)
                }
            } footer: {
                VStack(alignment: .leading, spacing: 6) {
                    if !model.hasConfig {
                        InlineMessage(.info, "Save the host connection in the Connection tab first.")
                    }
                    if model.hasConfig && !model.moonlightInstalled {
                        InlineMessage(.info, "Moonlight is not installed. Click Install Streaming to install it.")
                    }
                    if let reason = model.installBlockedReason {
                        InlineMessage(.info, reason)
                    }
                    if let error = model.lastError {
                        InlineMessage(.error, error)
                    }
                    if let message = model.pairingMessage {
                        InlineMessage(.success, message)
                    }
                }
            }

            if !model.installLog.isEmpty {
                Section { InstallLogView() }
            }

            if model.hasConfig { actionsSection }
        }
        .formStyle(.grouped)
        .task {
            await refreshActions()
            await model.refreshStreamMonitors()
        }
        .onChange(of: model.installRunning) { _, running in
            if !running { Task { await refreshActions() } }
        }
        .onChange(of: model.pairingRunning) { _, running in
            if !running { Task { await refreshActions() } }
        }
    }

    /// "Automatic" first, then the monitors the host reported. A saved value the host does not list (host offline,
    /// monitor unplugged) stays selectable, so opening the tab never changes the setting.
    private var outputChoices: [(value: String, title: String)] {
        var choices: [(value: String, title: String)] = [("auto", "Automatic")]
        for name in model.streamMonitorNames where name != "auto" && !choices.contains(where: { $0.value == name }) {
            choices.append((name, name))
        }
        let saved = model.draft.streamOutput
        if !choices.contains(where: { $0.value == saved }) { choices.append((saved, saved)) }
        return choices
    }

    /// The host probe needs a configuration to reach the host; without one it would only fail.
    private func refreshActions() async {
        guard model.hasConfig else { return }
        await model.refreshStreamingActions()
    }

    /// Mac first, then the host; the second step only runs when the first left no error.
    private func install() async {
        await model.installMac(streaming: true)
        guard model.lastError == nil else { return }
        await model.installHost(streaming: true)
    }

    /// Shown only with a configuration: without one the host cannot be asked, and the message above says why.
    private var actionsSection: some View {
        Section {
            if let error = model.streamingProbeError { InlineMessage(.error, error) }
            if model.streamingActions.isEmpty {
                if model.streamingProbed {
                    InlineMessage(.success, "No pending actions for streaming.")
                }
            } else {
                ForEach(model.streamingActions, id: \.id) { action in
                    actionRow(action)
                }
            }
        } header: {
            HStack {
                Text("Pending Actions on the Host")
                Spacer()
                Button("Check Again") { Task { await model.refreshStreamingActions() } }
                    .buttonStyle(.link)
            }
        } footer: {
            if !model.streamingActions.isEmpty {
                Text("Run these commands on the host. The app never runs them for you.")
            }
        }
    }

    private func actionRow(_ action: RequiredAction) -> some View {
        let isRequired = action.severity == .required
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: isRequired ? "exclamationmark.triangle.fill" : "info.circle")
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(isRequired ? Color.orange : Color.secondary)
                    .accessibilityHidden(true)
                Text(action.title)
                    .font(.body.weight(isRequired ? .semibold : .regular))
                Spacer()
                Text(isRequired ? "Required" : "Recommended")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(action.reason)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(alignment: .top, spacing: 8) {
                CodeBlock(text: action.command)
                CopyButton(text: action.command)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .contain)
    }
}
