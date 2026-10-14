import Foundation

/// What `CompatibilityReporter` learns about this Mac.
public struct MacFacts: Equatable, Sendable {
    public var diarchyVersion: String
    public var modelIdentifier: String
    public var chip: String
    public var macOSVersion: String
    public var moonlightVersion: String
    public var lanMouseVersion: String
    public var tailscaleInstalled: Bool

    public init(
        diarchyVersion: String, modelIdentifier: String, chip: String, macOSVersion: String,
        moonlightVersion: String, lanMouseVersion: String, tailscaleInstalled: Bool
    ) {
        self.diarchyVersion = diarchyVersion
        self.modelIdentifier = modelIdentifier
        self.chip = chip
        self.macOSVersion = macOSVersion
        self.moonlightVersion = moonlightVersion
        self.lanMouseVersion = lanMouseVersion
        self.tailscaleInstalled = tailscaleInstalled
    }
}

public enum CompatibilityReport {
    /// Markdown for a public issue: the Mac fields in contract order, then the host report as a `json` block, or
    /// `Host: not reachable` when there is none. The whole text passes through `Redactor`.
    public static func render(mac: MacFacts, route: String, hostReportJSON: String?) -> String {
        var lines = [
            "- Diarchy version: \(mac.diarchyVersion)",
            "- Mac model identifier: \(mac.modelIdentifier)",
            "- Chip: \(mac.chip)",
            "- macOS version: \(mac.macOSVersion)",
            "- Moonlight version: \(mac.moonlightVersion)",
            "- Lan Mouse version: \(mac.lanMouseVersion)",
            "- Tailscale installed: \(mac.tailscaleInstalled ? "yes" : "no")",
            "- Route used (lan/tailscale/none): \(route)",
            "",
        ]
        if let hostReportJSON {
            lines += ["Host report:", "", "```json", prettyPrinted(hostReportJSON), "```"]
        } else {
            lines.append("Host: not reachable")
        }
        return Redactor.redact(lines.joined(separator: "\n"))
    }

    /// Indented JSON with sorted keys; the input unchanged when it does not parse.
    private static func prettyPrinted(_ json: String) -> String {
        guard let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)),
            let data = try? JSONSerialization.data(
                withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]),
            let text = String(data: data, encoding: .utf8)
        else { return json }
        return text
    }
}

/// Collects the Mac facts, the route and the host report, and renders them with `CompatibilityReport`.
public struct CompatibilityReporter: Sendable {
    private let paths: Paths
    private let runner: any CommandRunner
    private let appVersion: @Sendable () -> String
    private let tailscaleInstalled: @Sendable () -> Bool

    private static let timeout: TimeInterval = 10
    private static let unknown = "unknown"

    public init(
        paths: Paths, runner: any CommandRunner,
        appVersion: @escaping @Sendable () -> String = {
            Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "Development build"
        },
        tailscaleInstalled: @escaping @Sendable () -> Bool = {
            FileManager.default.fileExists(atPath: "/Applications/Tailscale.app")
        }
    ) {
        self.paths = paths
        self.runner = runner
        self.appVersion = appVersion
        self.tailscaleInstalled = tailscaleInstalled
    }

    /// Never throws: a fact that cannot be read is `unknown`, an unreachable host is `Host: not reachable`.
    public func collect() async -> String {
        let mac = MacFacts(
            diarchyVersion: appVersion(),
            modelIdentifier: await output("/usr/sbin/sysctl", ["-n", "hw.model"]),
            chip: await output("/usr/sbin/sysctl", ["-n", "machdep.cpu.brand_string"]),
            macOSVersion: await output("/usr/bin/sw_vers", ["-productVersion"]),
            moonlightVersion: Self.bundleVersion(of: Self.moonlightApp(paths)),
            lanMouseVersion: Self.bundleVersion(of: paths.lanMouseApp),
            tailscaleInstalled: tailscaleInstalled())
        let route = await selectedRoute()
        var hostJSON: String?
        if route != "none" {
            hostJSON = try? await HostAgentClient(runner: runner, paths: paths).report().rawJSON
        }
        return CompatibilityReport.render(mac: mac, route: route, hostReportJSON: hostJSON)
    }

    /// `route=` value of `diarchy-ssh --print-route`; `none` when it fails or prints something else.
    private func selectedRoute() async -> String {
        guard
            let result = try? await runner.run(
                executable: paths.localBin("diarchy-ssh"), arguments: ["--print-route"], environment: nil,
                stdin: nil, timeout: Self.timeout),
            result.succeeded
        else { return "none" }
        for line in result.stdout.split(whereSeparator: \.isNewline) where line.hasPrefix("route=") {
            let value = line.dropFirst("route=".count).trimmingCharacters(in: .whitespaces)
            if ["lan", "tailscale", "none"].contains(value) { return value }
        }
        return "none"
    }

    private func output(_ executable: String, _ arguments: [String]) async -> String {
        guard
            let result = try? await runner.run(
                executable: executable, arguments: arguments, environment: nil, stdin: nil,
                timeout: Self.timeout),
            result.succeeded
        else { return Self.unknown }
        let text = result.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? Self.unknown : text
    }

    /// `Moonlight.app` from the executable path in `Paths` (`…/Moonlight.app/Contents/MacOS/Moonlight`).
    private static func moonlightApp(_ paths: Paths) -> URL {
        URL(fileURLWithPath: paths.moonlightExecutable)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    }

    private static func bundleVersion(of app: URL) -> String {
        let plist = app.appendingPathComponent("Contents/Info.plist")
        guard let info = NSDictionary(contentsOf: plist),
            let version = info["CFBundleShortVersionString"] as? String, !version.isEmpty
        else { return unknown }
        return version
    }
}
