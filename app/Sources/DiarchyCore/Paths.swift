import Foundation

/// Filesystem locations the app reads and launches. Everything derives from `home` and the bundled `payload`.
public struct Paths: Sendable {
    public let home: URL
    public let payload: URL?

    public init(home: URL, payload: URL?) {
        self.home = home
        self.payload = payload
    }

    /// The current user's home and `Bundle.main.resourceURL/payload` when that directory exists.
    public static func live() -> Paths {
        let candidate = Bundle.main.resourceURL?.appendingPathComponent("payload", isDirectory: true)
        var isDirectory: ObjCBool = false
        let payload = candidate.flatMap {
            FileManager.default.fileExists(atPath: $0.path, isDirectory: &isDirectory) && isDirectory.boolValue
                ? $0 : nil
        }
        return Paths(home: FileManager.default.homeDirectoryForCurrentUser, payload: payload)
    }

    /// Absolute path of `home/.local/bin/<name>`.
    public func localBin(_ name: String) -> String {
        home.appendingPathComponent(".local/bin/\(name)").path
    }

    public var lanMouseApp: URL {
        home.appendingPathComponent("Applications/Lan Mouse.app", isDirectory: true)
    }

    public var moonlightExecutable: String {
        home.appendingPathComponent("Applications/Moonlight.app/Contents/MacOS/Moonlight").path
    }

    /// Moonlight is installed by the streaming installer; a clean Mac does not have it.
    public var moonlightInstalled: Bool {
        FileManager.default.isExecutableFile(atPath: moonlightExecutable)
    }

    public var configURL: URL {
        ConfigStore.defaultURL(environment: ProcessInfo.processInfo.environment, home: home)
    }

    public var lanMouseCertificate: URL {
        home.appendingPathComponent(".config/lan-mouse/lan-mouse.pem")
    }

    /// `payload/<relative>` as a path; `nil` when there is no payload.
    public func payloadPath(_ relative: String) -> String? {
        payload?.appendingPathComponent(relative).path
    }

    /// Where the Mac install records which payload version it installed.
    public var macInstalledVersionURL: URL {
        home.appendingPathComponent(".local/share/diarchy/VERSION")
    }

    /// Trimmed contents of `macInstalledVersionURL`; `nil` when absent, unreadable or empty.
    public func installedMacVersion() -> String? {
        guard let text = try? String(contentsOf: macInstalledVersionURL, encoding: .utf8) else { return nil }
        let version = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return version.isEmpty ? nil : version
    }

    /// Trimmed contents of `payload/VERSION`; `nil` when absent, unreadable or empty.
    public func bundledVersion() -> String? {
        guard let payload,
            let text = try? String(contentsOf: payload.appendingPathComponent("VERSION"), encoding: .utf8)
        else { return nil }
        let version = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return version.isEmpty ? nil : version
    }
}
