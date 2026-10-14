import DiarchyCore
import SwiftUI

/// Screens: which edge of the Mac's screen leads to the host.
struct ScreensTab: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Form {
            Section {
                VStack(spacing: 16) {
                    Text("Choose which edge of the Mac's screen moves the pointer to the host.")
                        .font(.headline)
                        .multilineTextAlignment(.center)

                    diagram

                    HStack(spacing: 6) {
                        Text(caption)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                        if model.isBusy { ProgressView().controlSize(.small) }
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
            } footer: {
                VStack(alignment: .leading, spacing: 6) {
                    if !model.hasConfig {
                        InlineMessage(.info, "Save the host connection in the Connection tab first.")
                    }
                    if let error = model.lastError {
                        InlineMessage(.error, error)
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    private var caption: String {
        "When the pointer crosses the \(Self.edgeName(model.draft.kvmSide)) edge of the Mac's screen, it moves to the host."
    }

    private var diagram: some View {
        Grid(horizontalSpacing: 10, verticalSpacing: 10) {
            GridRow {
                Color.clear.frame(width: 90, height: 44)
                zone(.top)
                Color.clear.frame(width: 90, height: 44)
            }
            GridRow {
                zone(.left)
                macBox
                zone(.right)
            }
            GridRow {
                Color.clear.frame(width: 90, height: 44)
                zone(.bottom)
                Color.clear.frame(width: 90, height: 44)
            }
        }
    }

    private var macBox: some View {
        RoundedRectangle(cornerRadius: 14, style: .continuous)
            .fill(.quaternary)
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(.separator, lineWidth: 1))
            .overlay {
                VStack(spacing: 4) {
                    Image(systemName: "laptopcomputer")
                        .font(.title)
                    Text("Mac").font(.headline)
                }
                .foregroundStyle(.secondary)
            }
            .frame(width: 180, height: 120)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Mac")
    }

    private func zone(_ side: KVMSide) -> some View {
        let selected = model.draft.kvmSide == side
        return Button {
            if !selected { Task { await model.setSide(side) } }
        } label: {
            Text(Self.zoneTitle(side))
                .font(.body.weight(selected ? .semibold : .regular))
                .frame(width: side == .top || side == .bottom ? 180 : 90, height: 44)
                .foregroundStyle(selected ? Color.white : Color.primary)
                .selectionSurface(selected: selected, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
        .disabled(model.isBusy || !model.hasConfig)
        .help("The pointer moves to the host across the \(Self.edgeName(side)) edge")
        .accessibilityLabel("\(Self.zoneTitle(side)) zone")
        .accessibilityValue(selected ? "Selected" : "Not selected")
        .accessibilityHint("The pointer moves to the host across the \(Self.edgeName(side)) edge of the screen")
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }

    private static func zoneTitle(_ side: KVMSide) -> String {
        switch side {
        case .left: "Left"
        case .right: "Right"
        case .top: "Top"
        case .bottom: "Bottom"
        }
    }

    private static func edgeName(_ side: KVMSide) -> String {
        switch side {
        case .left: "left"
        case .right: "right"
        case .top: "top"
        case .bottom: "bottom"
        }
    }
}
