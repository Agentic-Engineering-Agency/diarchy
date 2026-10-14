import Foundation

/// Removes personal and network identifiers from text the user may paste into a public issue.
///
/// It removes IPv4 and IPv6 addresses, MAC addresses, home directory user names, `*.ts.net` names, `user@host`
/// pairs, and the values of `ssid`, `bssid`, `connection` and `hostname` fields (`key=value` text and JSON).
/// Each removed value becomes `<redacted>`.
public enum Redactor {
    public static let token = "<redacted>"

    public static func redact(_ text: String) -> String {
        var result = text
        for (pattern, template) in rules {
            result = result.replacingOccurrences(of: pattern, with: template, options: .regularExpression)
        }
        return result
    }

    private static let hex = "[0-9A-Fa-f]"
    private static let group = "[0-9A-Fa-f]{1,4}"
    private static let fields = "(?i:ssid|bssid|connection|hostname)"

    /// Applied in order. Earlier rules must not leave text that a later rule would handle differently.
    private static let rules: [(pattern: String, template: String)] = [
        // JSON field values: "ssid": "value"
        ("(\"\(fields)\"\\s*:\\s*)\"[^\"]*\"", "$1\"\(token)\""),
        // key=value text: the value runs to the end of the line.
        ("(?<![A-Za-z0-9_\"])(\(fields))(\\s*=\\s*)[^\\n]*", "$1$2\(token)"),
        // user@host, including a *.ts.net host.
        ("[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+", token),
        ("[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)*\\.ts\\.net", token),
        // /Users/<name> and /home/<name>: the name only, so the rest of the path stays readable.
        ("(/Users/)[^/\\s\"'<]+", "$1\(token)"),
        ("(/home/)[^/\\s\"'<]+", "$1\(token)"),
        // MAC addresses, with ":" or "-".
        ("(?<![0-9A-Fa-f:-])\(hex){2}(?:[:-]\(hex){2}){5}(?![0-9A-Fa-f:-])", token),
        // IPv4: every address, not only private ones.
        ("(?<![0-9.])(?:[0-9]{1,3}\\.){3}[0-9]{1,3}(?![0-9])", token),
        // IPv6: full form, and the compressed form with "::".
        ("(?<![0-9A-Fa-f:])(?:\(group):){7}\(group)(?![0-9A-Fa-f:])", token),
        ("(?<![0-9A-Fa-f:])(?:\(group)(?::\(group)){0,6})?::(?:\(group)(?::\(group)){0,6})?(?![0-9A-Fa-f:])", token),
    ]
}
