import Foundation

/// The Mac's IPv4 address on the interface that routes to the LAN host (spec §8.4); passed to `diarchy-host probe
/// --controller-ip` so the agent can name the right source in its firewall command.
public enum ControllerIP {
    private static let timeout: TimeInterval = 10

    /// The value of the `interface:` line of `route -n get` output, e.g. `en0`.
    public static func interface(fromRouteGet output: String) -> String? {
        for line in output.split(whereSeparator: \.isNewline) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard trimmed.hasPrefix("interface:") else { continue }
            let name = trimmed.dropFirst("interface:".count).trimmingCharacters(in: .whitespaces)
            return name.isEmpty || name.contains(where: \.isWhitespace) ? nil : name
        }
        return nil
    }

    /// `route -n get <lanHost>` → interface → `ipconfig getifaddr <interface>`. `nil` when there is no LAN host, a
    /// command fails, or the result is not a dotted-quad IPv4 address. Nothing runs for an empty `lanHost`.
    public static func resolve(lanHost: String, runner: any CommandRunner) async -> String? {
        guard !lanHost.isEmpty else { return nil }
        guard let route = try? await runner.run(
            executable: "/sbin/route", arguments: ["-n", "get", lanHost], environment: nil, stdin: nil,
            timeout: timeout),
            route.succeeded, let interface = interface(fromRouteGet: route.stdout)
        else { return nil }
        guard let address = try? await runner.run(
            executable: "/usr/sbin/ipconfig", arguments: ["getifaddr", interface], environment: nil, stdin: nil,
            timeout: timeout),
            address.succeeded
        else { return nil }
        let value = address.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        return HostAgentClient.isIPv4(value) ? value : nil
    }
}
