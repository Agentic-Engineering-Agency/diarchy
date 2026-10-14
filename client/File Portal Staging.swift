import Darwin
import Foundation

final class DroppedItemAccess: @unchecked Sendable {
    let urls: [URL]
    private var scopedURLs: [URL]

    init(urls: [URL]) {
        self.urls = urls
        self.scopedURLs = urls.filter { $0.startAccessingSecurityScopedResource() }
    }

    deinit {
        stopAccessing()
    }

    func stage() throws -> StagedDrop {
        defer { stopAccessing() }
        let rootURL = try makeStagingDirectory()
        var stagedURLs: [URL] = []
        var basenames = Set<String>()

        do {
            for sourceURL in urls {
                let source = sourceURL.standardizedFileURL
                let basename = source.lastPathComponent
                guard !basename.isEmpty, basename != ".", basename != ".." else {
                    throw PortalStagingError.invalidBasename
                }
                guard basenames.insert(basename).inserted else {
                    throw PortalStagingError.duplicateBasename(basename)
                }
                let destination = rootURL.appendingPathComponent(basename)
                try FileManager.default.copyItem(at: source, to: destination)
                stagedURLs.append(destination)
            }
            return StagedDrop(rootURL: rootURL, urls: stagedURLs)
        } catch {
            try? FileManager.default.removeItem(at: rootURL)
            throw error
        }
    }

    private func stopAccessing() {
        for url in scopedURLs {
            url.stopAccessingSecurityScopedResource()
        }
        scopedURLs.removeAll()
    }
}

final class StagedDrop: @unchecked Sendable {
    let rootURL: URL
    let urls: [URL]
    private var removed = false

    fileprivate init(rootURL: URL, urls: [URL]) {
        self.rootURL = rootURL
        self.urls = urls
    }

    deinit {
        remove()
    }

    func remove() {
        guard !removed else { return }
        removed = true
        try? FileManager.default.removeItem(at: rootURL)
    }
}

private enum PortalStagingError: LocalizedError {
    case invalidBasename
    case duplicateBasename(String)

    var errorDescription: String? {
        switch self {
        case .invalidBasename:
            return "Dropped item has an invalid name"
        case .duplicateBasename(let basename):
            return "Multiple dropped items are named \(basename)"
        }
    }
}

private func makeStagingDirectory() throws -> URL {
    let templatePath = FileManager.default.temporaryDirectory
        .appendingPathComponent("diarchy-file-portal.XXXXXX")
        .path
    var template = Array(templatePath.utf8CString)
    let created = template.withUnsafeMutableBufferPointer { buffer in
        mkdtemp(buffer.baseAddress!) != nil
    }
    guard created else {
        throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
    }
    let path = template.withUnsafeBufferPointer { buffer in
        String(cString: buffer.baseAddress!)
    }
    return URL(fileURLWithPath: path, isDirectory: true)
}
