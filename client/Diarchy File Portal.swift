import AppKit
import Darwin
import Foundation
import UserNotifications

private let triggerSize = NSSize(width: 3, height: 180)
private let revealedSize = NSSize(width: 52, height: 180)
private let expandedSize = NSSize(width: 72, height: 200)
private let edgeDragActivationWidth: CGFloat = 120
private let maximumDropItems = 32

private final class PortalPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect {
        return frameRect
    }
}

private final class BoundedOutputBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private let limit: Int
    private var data = Data()
    private var overflowed = false

    init(limit: Int = 8192) {
        self.limit = limit
    }

    func append(_ chunk: Data) {
        lock.lock()
        defer { lock.unlock() }
        let remaining = max(0, limit - data.count)
        data.append(chunk.prefix(remaining))
        if chunk.count > remaining {
            overflowed = true
        }
    }

    func text() -> String {
        lock.lock()
        defer { lock.unlock() }
        let decoded = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return overflowed ? decoded + "\n[output truncated]" : decoded
    }
}

private final class DropPortalView: NSView {
    private let label = NSTextField(labelWithString: "DROP\nTO\nHOST")
    private var highlighted = false
    private var sending = false
    private var mouseInside = false
    private var hideGeneration = 0
    private var edgeTrackingArea: NSTrackingArea?
    private var edgeDragMonitor: Any?
    private var edgeDragRevealed = false
    private let processLock = NSLock()
    private var activeProcesses: [ObjectIdentifier: Process] = [:]

    private func track(_ process: Process) {
        processLock.lock()
        activeProcesses[ObjectIdentifier(process)] = process
        processLock.unlock()
    }

    private func untrack(_ process: Process) {
        processLock.lock()
        activeProcesses.removeValue(forKey: ObjectIdentifier(process))
        processLock.unlock()
    }

    func cancelActiveTransfers() {
        processLock.lock()
        let processes = Array(activeProcesses.values)
        processLock.unlock()
        for process in processes where process.isRunning {
            process.terminate()
        }
        let deadline = Date(timeIntervalSinceNow: 6)
        while processes.contains(where: \.isRunning) && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        for process in processes where process.isRunning {
            kill(process.processIdentifier, SIGKILL)
            process.waitUntilExit()
        }
    }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor(calibratedRed: 0.09, green: 0.11, blue: 0.15, alpha: 0.96).cgColor
        layer?.borderColor = NSColor.systemTeal.cgColor
        layer?.borderWidth = 2
        layer?.cornerRadius = 12
        layer?.maskedCorners = [.layerMaxXMinYCorner, .layerMaxXMaxYCorner]
        layer?.opacity = 0

        label.alignment = .center
        label.textColor = .white
        label.font = .boldSystemFont(ofSize: 11)
        label.maximumNumberOfLines = 4
        label.frame = bounds.insetBy(dx: 5, dy: 0)
        label.autoresizingMask = [.width, .height]
        addSubview(label)
        registerForDraggedTypes([.fileURL])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        removeEdgeDragMonitor()
        guard window != nil else { return }
        edgeDragMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDragged, .leftMouseUp]
        ) { [weak self] event in
            DispatchQueue.main.async {
                self?.handleExternalDragEvent(event)
            }
        }
    }

    deinit {
        removeEdgeDragMonitor()
    }

    private func removeEdgeDragMonitor() {
        if let edgeDragMonitor {
            NSEvent.removeMonitor(edgeDragMonitor)
            self.edgeDragMonitor = nil
        }
    }

    private func externalDragContainsFileURLs() -> Bool {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        return NSPasteboard(name: .drag).canReadObject(
            forClasses: [NSURL.self],
            options: options
        )
    }

    private func handleExternalDragEvent(_ event: NSEvent) {
        if event.type == .leftMouseUp {
            if edgeDragRevealed {
                edgeDragRevealed = false
                scheduleHide()
            }
            return
        }
        guard !sending,
              !highlighted,
              externalDragContainsFileURLs(),
              let panel = window as? NSPanel,
              panel.frame.width <= triggerSize.width
        else {
            return
        }
        let edgeRect = NSRect(
            x: panel.frame.minX,
            y: panel.frame.midY - triggerSize.height / 2,
            width: edgeDragActivationWidth,
            height: triggerSize.height
        )
        guard NSMouseInRect(NSEvent.mouseLocation, edgeRect, false) else { return }
        edgeDragRevealed = true
        reveal(expanded: true)
    }

    override func updateTrackingAreas() {
        if let edgeTrackingArea {
            removeTrackingArea(edgeTrackingArea)
        }
        let trackingArea = NSTrackingArea(
            rect: bounds,
            options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(trackingArea)
        edgeTrackingArea = trackingArea
        super.updateTrackingAreas()
    }

    override func mouseEntered(with event: NSEvent) {
        mouseInside = true
        reveal(expanded: false)
    }

    override func mouseExited(with event: NSEvent) {
        mouseInside = false
        scheduleHide()
    }

    private func setPanelSize(_ size: NSSize, animate: Bool) {
        guard let panel = window as? NSPanel else { return }
        let oldFrame = panel.frame
        let newFrame = NSRect(
            x: oldFrame.minX,
            y: oldFrame.midY - size.height / 2,
            width: size.width,
            height: size.height
        )
        panel.setFrame(newFrame, display: true, animate: animate)
    }

    private func reveal(expanded: Bool) {
        hideGeneration += 1
        layer?.opacity = 1
        label.isHidden = false
        window?.hasShadow = true
        setPanelSize(expanded ? expandedSize : revealedSize, animate: true)
    }

    private func collapseToEdge() {
        guard !mouseInside, !highlighted, !sending else { return }
        edgeDragRevealed = false
        layer?.opacity = 0
        label.isHidden = true
        window?.hasShadow = false
        setPanelSize(triggerSize, animate: true)
    }

    private func scheduleHide() {
        hideGeneration += 1
        let generation = hideGeneration
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self, self.hideGeneration == generation else { return }
            self.collapseToEdge()
        }
    }

    private func droppedFileURLs(from sender: NSDraggingInfo) -> [URL] {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        let objects = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: options) as? [NSURL]
        return objects?.map { $0 as URL } ?? []
    }

    private func acceptedFileURLs(from sender: NSDraggingInfo) throws -> [URL] {
        let urls = droppedFileURLs(from: sender)
        guard !urls.isEmpty else {
            throw PortalError("Drop contains no local files")
        }
        guard urls.count <= maximumDropItems else {
            throw PortalError("Drop at most \(maximumDropItems) items at once")
        }
        guard urls.allSatisfy(\.isFileURL) else {
            throw PortalError("Only local files are supported")
        }
        return urls
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard !sending, (try? acceptedFileURLs(from: sender)) != nil else {
            return []
        }
        edgeDragRevealed = false
        reveal(expanded: true)
        setHighlighted(true)
        return .copy
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard !sending, (try? acceptedFileURLs(from: sender)) != nil else {
            return []
        }
        return .copy
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        setHighlighted(false)
        scheduleHide()
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        do {
            let urls = try acceptedFileURLs(from: sender)
            setHighlighted(false)
            startTransfer(urls)
            return true
        } catch {
            report(title: "Transfer rejected", detail: error.localizedDescription, failed: true)
            scheduleHide()
            return false
        }
    }

    private func setHighlighted(_ active: Bool) {
        highlighted = active
        layer?.backgroundColor = active
            ? NSColor.systemTeal.withAlphaComponent(0.96).cgColor
            : NSColor(calibratedRed: 0.09, green: 0.11, blue: 0.15, alpha: 0.96).cgColor
        label.stringValue = active ? "RELEASE\nTO SEND" : "DROP\nTO\nHOST"
        if active {
            reveal(expanded: true)
        } else if mouseInside || sending {
            reveal(expanded: false)
        }
    }

    private func startTransfer(_ urls: [URL]) {
        sending = true
        reveal(expanded: false)
        label.stringValue = "SENDING\n\(urls.count) ITEM\(urls.count == 1 ? "" : "S")"
        let droppedItems = DroppedItemAccess(urls: urls)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                let staging = try droppedItems.stage()
                defer { staging.remove() }
                let paths = staging.urls.map(\.path)
                let process = Process()
                let stdoutPipe = Pipe()
                let stderrPipe = Pipe()
                let stdoutBuffer = BoundedOutputBuffer()
                let stderrBuffer = BoundedOutputBuffer()
                let stdoutFinished = DispatchSemaphore(value: 0)
                let stderrFinished = DispatchSemaphore(value: 0)
                stdoutPipe.fileHandleForReading.readabilityHandler = { handle in
                    let chunk = handle.availableData
                    if chunk.isEmpty {
                        handle.readabilityHandler = nil
                        stdoutFinished.signal()
                    } else {
                        stdoutBuffer.append(chunk)
                    }
                }
                stderrPipe.fileHandleForReading.readabilityHandler = { handle in
                    let chunk = handle.availableData
                    if chunk.isEmpty {
                        handle.readabilityHandler = nil
                        stderrFinished.signal()
                    } else {
                        stderrBuffer.append(chunk)
                    }
                }
                let executable = ProcessInfo.processInfo.environment["DIARCHY_FILE_PORTAL_SENDER_BIN"]
                    ?? FileManager.default.homeDirectoryForCurrentUser
                        .appendingPathComponent(".local/bin/diarchy-file-portal-send").path
                process.executableURL = URL(fileURLWithPath: executable)
                process.arguments = ["--"] + paths
                var environment = ProcessInfo.processInfo.environment
                environment["DIARCHY_FILE_PORTAL_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
                process.environment = environment
                process.standardOutput = stdoutPipe
                process.standardError = stderrPipe
                track(process)
                defer { untrack(process) }
                try process.run()
                process.waitUntilExit()
                _ = stdoutFinished.wait(timeout: .now() + 1)
                _ = stderrFinished.wait(timeout: .now() + 1)
                stdoutPipe.fileHandleForReading.readabilityHandler = nil
                stderrPipe.fileHandleForReading.readabilityHandler = nil
                let stdout = stdoutBuffer.text()
                let stderr = stderrBuffer.text()
                let succeeded = process.terminationStatus == 0
                let lines = (stdout + (stdout.isEmpty || stderr.isEmpty ? "" : "\n") + stderr)
                    .split(separator: "\n")
                    .map(String.init)
                    .filter {
                        $0.hasPrefix("portal-send name=")
                            || $0.hasPrefix("portal-send-summary ")
                            || $0.hasPrefix("[output truncated]")
                    }
                let detail = lines.isEmpty
                    ? (succeeded ? "Sent \(paths.count) item(s) to the host" : "Portal sender failed")
                    : String(lines.joined(separator: "\n").prefix(1024))
                DispatchQueue.main.async {
                    self.report(
                        title: succeeded ? "Transfer complete" : "Transfer failed",
                        detail: detail,
                        failed: !succeeded
                    )
                }
            } catch {
                DispatchQueue.main.async {
                    self.report(title: "Transfer failed", detail: error.localizedDescription, failed: true)
                }
            }
        }
    }

    private func report(title: String, detail: String, failed: Bool) {
        sending = false
        highlighted = false
        label.stringValue = failed ? "FAILED" : "SENT"
        layer?.backgroundColor = failed
            ? NSColor.systemRed.withAlphaComponent(0.92).cgColor
            : NSColor.systemGreen.withAlphaComponent(0.92).cgColor

        if Bundle.main.bundleIdentifier != nil {
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = detail
            let request = UNNotificationRequest(
                identifier: UUID().uuidString,
                content: content,
                trigger: nil
            )
            UNUserNotificationCenter.current().add(request)
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { [weak self] in
            self?.scheduleHide()
        }
    }
}

private struct PortalError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private var panel: PortalPanel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        NSApp.unhideWithoutActivation()
        if Bundle.main.bundleIdentifier != nil {
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
        guard let screen = NSScreen.main else {
            NSApp.terminate(nil)
            return
        }
        let screenFrame = screen.frame
        let frame = NSRect(
            x: screenFrame.minX,
            y: screenFrame.midY - triggerSize.height / 2,
            width: triggerSize.width,
            height: triggerSize.height
        )
        let panel = PortalPanel(
            contentRect: frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.isMovable = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.isReleasedWhenClosed = false
        let portalView = DropPortalView(frame: NSRect(origin: .zero, size: triggerSize))
        portalView.autoresizingMask = [.width, .height]
        panel.contentView = portalView
        panel.orderFrontRegardless()
        self.panel = panel
    }

    func applicationWillTerminate(_ notification: Notification) {
        (panel?.contentView as? DropPortalView)?.cancelActiveTransfers()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

@main
private struct PortalApplication {
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.delegate = delegate
        application.run()
    }
}
