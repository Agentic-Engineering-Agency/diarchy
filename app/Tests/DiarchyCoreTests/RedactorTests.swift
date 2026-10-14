import Foundation
import Testing

@testable import DiarchyCore

@Suite struct RedactorTests {
    @Test(arguments: [
        ("connect to 192.0.2.10 port 22", "192.0.2.10"),
        ("tailscale 100.64.0.10 up", "100.64.0.10"),
        ("public 8.8.8.8 reached", "8.8.8.8"),
        ("link 00:00:5e:00:53:01 up", "00:00:5e:00:53:01"),
        ("link 00-00-5E-00-53-01 up", "00-00-5E-00-53-01"),
        ("addr 2001:db8::1 up", "2001:db8::1"),
        ("addr 2001:db8:0:0:0:0:0:1 up", "2001:db8:0:0:0:0:0:1"),
        ("addr fe80::1 up", "fe80::1"),
        ("key at /Users/user/.ssh/id", "user"),
        ("key at /home/user/.ssh/id", "user"),
        ("name omarchy.tail1234.ts.net up", "omarchy.tail1234.ts.net"),
        ("ssh user@example.com now", "user@example.com"),
        ("ssid=example-ssid", "example-ssid"),
        ("SSID = example ssid with spaces", "example ssid with spaces"),
        (#"{"ssid":"example-ssid","signal":70}"#, "example-ssid"),
        (#"{"connection": "example-connection"}"#, "example-connection"),
        (#"{"hostname":"host.example"}"#, "host.example"),
        ("bssid=00:00:5e:00:53:02", "00:00:5e:00:53:02"),
    ])
    func removesTheIdentifier(_ input: String, _ secret: String) {
        let output = Redactor.redact(input)
        #expect(!output.contains(secret), "\(output)")
        #expect(output.contains("<redacted>"), "\(output)")
    }

    @Test(arguments: [
        "Diarchy 0.1.0-beta.1 on macOS 15.4.1",
        "kernel 6.17.1-arch1-1, Hyprland 0.54.0, Sunshine 2026.516.143833",
        "time 12:30:45 elapsed",
        "== diarchy-host probe ==\nexit code: 0",
        "Mac15,3 Apple M3",
    ])
    func keepsTextThatIsNotAnIdentifier(_ input: String) {
        #expect(Redactor.redact(input) == input)
    }

    @Test func keepsAFingerprintOfHexPairsWhole() {
        let fingerprint = Array(repeating: "AA", count: 32).joined(separator: ":")
        #expect(Redactor.redact(fingerprint) == fingerprint)
    }

    @Test func keepsTheRestOfAPathAndKeepsJSONValid() throws {
        #expect(Redactor.redact("/Users/user/.ssh/known_hosts") == "/Users/<redacted>/.ssh/known_hosts")
        let json = #"{"network":{"ssid":"example-ssid","wifi":true},"ip":"192.0.2.10"}"#
        let object = try JSONSerialization.jsonObject(with: Data(Redactor.redact(json).utf8))
        #expect(object is [String: Any])
    }

    @Test func redactsEveryOccurrence() {
        let output = Redactor.redact("192.0.2.10 and 192.0.2.20 and 192.0.2.10")
        #expect(output == "<redacted> and <redacted> and <redacted>")
    }
}
