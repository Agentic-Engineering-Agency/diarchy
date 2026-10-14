import Foundation

/// Reads and atomically writes `diarchy.conf`.
public struct ConfigStore: Sendable {
    public let url: URL

    public init(url: URL) {
        self.url = url
    }

    /// `DIARCHY_CONFIG` when non-empty, else `home/.config/diarchy/diarchy.conf`.
    public static func defaultURL(environment: [String: String], home: URL) -> URL {
        if let override = environment["DIARCHY_CONFIG"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        return home.appendingPathComponent(".config/diarchy/diarchy.conf")
    }

    /// `nil` when the file does not exist; throws `ConfigError` when it exists but cannot be read or is invalid.
    public func load() throws -> DiarchyConfig? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        guard let data = try? Data(contentsOf: url), String(data: data, encoding: .utf8) != nil else {
            throw ConfigError(reason: "cannot read configuration file: \(url.path)")
        }
        // Decode the bytes directly so a UTF-8 BOM stays visible to the header check.
        return try ConfigCodec.parse(String(decoding: data, as: UTF8.self))
    }

    /// Validates and renders `config`, then writes it atomically: directory `0700`, temp file `0600` in the same
    /// directory, `fsync`, `rename`.
    public func save(_ config: DiarchyConfig) throws {
        let text = try ConfigCodec.render(config)
        let directory = url.deletingLastPathComponent()
        try Self.ensureDirectory(directory)

        let temporary = directory.appendingPathComponent(
            ".\(url.lastPathComponent).tmp.\(getpid()).\(UUID().uuidString)")
        let descriptor = open(temporary.path, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw Self.posixError() }

        var published = false
        defer {
            if !published { unlink(temporary.path) }
        }
        do {
            // The creation mode is filtered by the umask; the contract is exactly 0600.
            guard fchmod(descriptor, 0o600) == 0 else { throw Self.posixError() }
            try Self.writeAll(Array(text.utf8), to: descriptor)
            guard fsync(descriptor) == 0 else { throw Self.posixError() }
        } catch {
            close(descriptor)
            throw error
        }
        guard close(descriptor) == 0 else { throw Self.posixError() }
        guard rename(temporary.path, url.path) == 0 else { throw Self.posixError() }
        published = true
    }

    private static func ensureDirectory(_ directory: URL) throws {
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDirectory) {
            guard isDirectory.boolValue else { throw posixError(ENOTDIR) }
            return
        }
        // Missing ancestors get the default mode; only the directory that holds the config is private.
        try FileManager.default.createDirectory(
            at: directory.deletingLastPathComponent(), withIntermediateDirectories: true)
        if mkdir(directory.path, 0o700) == 0 {
            guard chmod(directory.path, 0o700) == 0 else { throw posixError() }
        } else if errno != EEXIST {
            throw posixError()
        }
    }

    private static func writeAll(_ bytes: [UInt8], to descriptor: Int32) throws {
        try bytes.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let written = write(descriptor, buffer.baseAddress! + offset, buffer.count - offset)
                if written < 0 {
                    if errno == EINTR { continue }
                    throw posixError()
                }
                offset += written
            }
        }
    }

    private static func posixError(_ code: Int32 = errno) -> NSError {
        NSError(domain: NSPOSIXErrorDomain, code: Int(code))
    }
}
