import AppKit
import SwiftUI

/// Copies `text` to the general pasteboard.
@MainActor
func copyToPasteboard(_ text: String) {
    let pasteboard = NSPasteboard.general
    pasteboard.clearContents()
    pasteboard.setString(text, forType: .string)
}

/// A small `Copy` button used wherever the app shows a command or output the user may need to paste elsewhere.
struct CopyButton: View {
    let text: String
    var title: String = "Copy"

    var body: some View {
        Button { copyToPasteboard(text) } label: {
            Label(title, systemImage: "doc.on.doc")
        }
        .secondaryActionStyle()
        .controlSize(.small)
        .help("Copy to the clipboard")
    }
}
