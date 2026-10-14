import DiarchyCore
import SwiftUI

// Shared look of the app. Everything here uses system controls, materials, SF Symbols and semantic colors, so light
// and dark mode, accent color and accessibility settings come from the system. Liquid Glass is used on macOS 26 and
// later only, always with a plain-material fallback for macOS 14 and 15 (the deployment target). The glass APIs exist
// only in the macOS 26 SDK (Swift 6.2, Xcode 26), so `#if compiler(>=6.2)` keeps the app buildable with Xcode 16.

// MARK: Buttons

extension View {
    /// The one main action of a group (`Save`, `Run Diagnostics`).
    @ViewBuilder
    func primaryActionStyle() -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26, *) {
            buttonStyle(.glassProminent)
        } else {
            buttonStyle(.borderedProminent)
        }
        #else
        buttonStyle(.borderedProminent)
        #endif
    }

    /// Every other button in a group.
    @ViewBuilder
    func secondaryActionStyle() -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26, *) {
            buttonStyle(.glass)
        } else {
            buttonStyle(.bordered)
        }
        #else
        buttonStyle(.bordered)
        #endif
    }
}

// MARK: Selection surfaces

/// The background of a choosable segment or zone. Selected: the accent color, as glass tinted with it on macOS 26 and
/// later. Unselected: a quiet fill (`idleFill`), or nothing for segments that sit on a track of their own.
private struct SelectionSurface<S: Shape>: ViewModifier {
    let selected: Bool
    let enabled: Bool
    let idleFill: Bool
    let shape: S

    func body(content: Content) -> some View {
        let accent = Color.accentColor.opacity(enabled ? 1 : 0.6)
        if selected {
            #if compiler(>=6.2)
            if #available(macOS 26, *) {
                content.glassEffect(.regular.tint(accent), in: shape)
            } else {
                content.background(shape.fill(accent))
            }
            #else
            content.background(shape.fill(accent))
            #endif
        } else if idleFill {
            content.background(shape.fill(.quaternary))
        } else {
            content
        }
    }
}

extension View {
    func selectionSurface(
        selected: Bool, enabled: Bool = true, idleFill: Bool = true, in shape: some Shape
    ) -> some View {
        modifier(SelectionSurface(selected: selected, enabled: enabled, idleFill: idleFill, shape: shape))
    }
}

// MARK: Status

extension StatusLine.Tone {
    var symbol: String {
        switch self {
        case .error: "xmark.octagon.fill"
        case .attention: "exclamationmark.triangle.fill"
        case .active: "checkmark.circle.fill"
        case .inactive: "power.circle"
        case .busy: "arrow.triangle.2.circlepath.circle"
        }
    }

    var color: Color {
        switch self {
        case .error: .red
        case .attention: .orange
        case .active: .green
        case .inactive, .busy: .secondary
        }
    }

    var accessibilityLabel: String {
        switch self {
        case .error: "Error"
        case .attention: "Needs attention"
        case .active: "Active"
        case .inactive: "Inactive"
        case .busy: "Busy"
        }
    }
}

/// One line of feedback with a symbol and a semantic color: the same four kinds everywhere in the app.
struct InlineMessage: View {
    enum Kind {
        case error, warning, info, success

        var symbol: String {
            switch self {
            case .error: "xmark.octagon.fill"
            case .warning: "exclamationmark.triangle.fill"
            case .info: "info.circle"
            case .success: "checkmark.circle.fill"
            }
        }

        var color: Color {
            switch self {
            case .error: .red
            case .warning: .orange
            case .info: .secondary
            case .success: .green
            }
        }

        var accessibilityLabel: String {
            switch self {
            case .error: "Error"
            case .warning: "Warning"
            case .info: "Information"
            case .success: "Success"
            }
        }
    }

    let kind: Kind
    let text: String

    init(_ kind: Kind, _ text: String) {
        self.kind = kind
        self.text = text
    }

    var body: some View {
        Label {
            Text(text)
                .foregroundStyle(kind == .error ? Color.red : Color.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: kind.symbol)
                .foregroundStyle(kind.color)
                .accessibilityLabel(kind.accessibilityLabel)
        }
        .font(.callout)
    }
}

/// A command or output the user may copy: monospaced, selectable, on a quiet rounded surface.
struct CodeBlock: View {
    let text: String
    var secondary = false

    var body: some View {
        Text(text)
            .font(.system(.callout, design: .monospaced))
            .foregroundStyle(secondary ? .secondary : .primary)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }
}
