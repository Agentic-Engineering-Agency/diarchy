import SwiftUI

/// The install log (`AppModel.installLog`) in a monospaced, selectable box that follows the newest line. Shared by
/// Connection and Streaming. It scrolls on `installLogRevision`, which changes with every line even when the capped
/// log no longer changes its length; no timer is involved.
struct InstallLogView: View {
    @Environment(AppModel.self) private var model
    private static let bottom = "install-log-bottom"

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Label("Install Log", systemImage: "terminal")
                    .font(.body.weight(.semibold))
                Spacer()
                CopyButton(text: model.installLog.joined(separator: "\n"), title: "Copy Log")
            }
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(model.installLog.joined(separator: "\n"))
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Color.clear.frame(height: 1).id(Self.bottom)
                    }
                    .padding(8)
                }
                .frame(height: 180)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
                .onChange(of: model.installLogRevision) {
                    proxy.scrollTo(Self.bottom, anchor: .bottom)
                }
                .onAppear { proxy.scrollTo(Self.bottom, anchor: .bottom) }
            }
        }
    }
}
