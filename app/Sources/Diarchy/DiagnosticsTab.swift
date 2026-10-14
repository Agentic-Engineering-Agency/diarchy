import SwiftUI

/// `Diagnostics` tab: runs the backend diagnostics once on demand and shows the output. It also copies the
/// compatibility report for a bug report.
struct DiagnosticsTab: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Button {
                    Task { await model.runDiagnostics() }
                } label: {
                    Label("Run Diagnostics", systemImage: "play.fill")
                }
                .primaryActionStyle()
                .disabled(model.diagnosticsRunning || model.isBusy)

                if model.diagnosticsRunning {
                    ProgressView().controlSize(.small)
                }

                Spacer()

                CopyButton(text: model.diagnosticsOutput, title: "Copy All")
                    .disabled(model.diagnosticsOutput.isEmpty)
            }

            HStack(spacing: 8) {
                Button {
                    Task { await model.copyCompatibilityReport() }
                } label: {
                    Label("Copy Compatibility Report", systemImage: "doc.text")
                }
                .secondaryActionStyle()
                .disabled(model.compatibilityRunning)

                if model.compatibilityRunning {
                    ProgressView().controlSize(.small)
                } else if let message = model.compatibilityMessage {
                    Label(message, systemImage: "checkmark.circle.fill")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                Spacer()
            }

            if model.diagnosticsOutput.isEmpty {
                ContentUnavailableView {
                    Label("No Results", systemImage: "stethoscope")
                } description: {
                    Text("Click “Run Diagnostics” to check this Mac and the host.")
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    Text(model.diagnosticsOutput)
                        .font(.system(.body, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                }
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding()
    }
}
