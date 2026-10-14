import DiarchyCore
import SwiftUI

/// The setup checklist of spec §8.4: one row per step, with the commands the user may need to run and the button
/// each step offers, followed by the install log. Nothing here polls: the rows follow `AppModel.checklist`, and the
/// log scrolls when a line is appended.
struct ChecklistView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            if let checklist = model.checklist {
                ForEach(checklist.steps) { step in
                    ChecklistRow(step: step)
                }
            } else {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Checking the setup…")
                        .foregroundStyle(.secondary)
                }
            }

            if let error = model.lastError {
                InlineMessage(.error, error)
            }

            if !model.installLog.isEmpty {
                InstallLogView()
            }
        }
    }
}

private struct ChecklistRow: View {
    @Environment(AppModel.self) private var model
    let step: ChecklistStep

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol)
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(tint)
                .font(.title3)
                .frame(width: 22)
                .accessibilityLabel(stateLabel)

            VStack(alignment: .leading, spacing: 6) {
                Text(step.title)
                    .font(.body.weight(.semibold))
                Text(step.message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)

                ForEach(Array(step.commands.enumerated()), id: \.offset) { _, hint in
                    CommandHintRow(hint: hint)
                }

                if let action = visibleAction {
                    Button(title(for: action)) { run(action) }
                        .secondaryActionStyle()
                        .controlSize(.small)
                        .disabled(model.isBusy || model.installRunning || model.pairingRunning || installBlocked(action))
                        .help(installBlockedReason(action) ?? "")
                    if let reason = installBlockedReason(action) {
                        InlineMessage(.info, reason)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .contain)
    }

    /// Only the two install buttons wait for streaming or a mode change; pairing is not an install.
    private func installBlockedReason(_ action: StepAction) -> String? {
        switch action {
        case .installMac, .installHost: model.installBlockedReason
        case .confirmHostKey, .pair: nil
        }
    }

    private func installBlocked(_ action: StepAction) -> Bool { installBlockedReason(action) != nil }

    /// A failed platform check (step 5) offers no install: reinstalling cannot fix an unsupported host (§11).
    private var visibleAction: StepAction? {
        if step.id == .platform && step.state == .failed { return nil }
        return step.action
    }

    private var symbol: String {
        switch step.state {
        case .pending: return "circle.dashed"
        case .ok: return "checkmark.circle.fill"
        case .actionRequired: return "exclamationmark.triangle.fill"
        case .failed: return "xmark.octagon.fill"
        }
    }

    private var tint: Color {
        switch step.state {
        case .pending: return .secondary
        case .ok: return .green
        case .actionRequired: return .orange
        case .failed: return .red
        }
    }

    private var stateLabel: String {
        switch step.state {
        case .pending: return "Pending"
        case .ok: return "OK"
        case .actionRequired: return "Action required"
        case .failed: return "Failed"
        }
    }

    private func title(for action: StepAction) -> String {
        switch action {
        case .installMac: return "Install on This Mac"
        case .installHost: return "Install on the Host"
        case .confirmHostKey: return "Confirm Host Key"
        case .pair: return "Pair"
        }
    }

    private func run(_ action: StepAction) {
        Task {
            switch action {
            case .installMac: await model.installMac(streaming: false)
            case .installHost: await model.installHost(streaming: model.hostSunshineInstalled)
            case .confirmHostKey: await model.confirmHostKeyStep()
            case .pair: await model.pair()
            }
        }
    }
}

/// A command the user runs themselves; the app only shows it. Hints that do not block the step are secondary.
private struct CommandHintRow: View {
    let hint: CommandHint

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(hint.title)
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 8) {
                CodeBlock(text: hint.command, secondary: !hint.blocking)
                CopyButton(text: hint.command)
            }
        }
    }
}
