import Foundation
import Testing
@testable import DiarchyCore

private let goldenText = """
# Managed by Diarchy
DIARCHY_SSH_USER=omarchy
DIARCHY_LAN_HOST=192.0.2.10
DIARCHY_TAILSCALE_HOST=100.64.0.20
DIARCHY_SSH_PORT=22
DIARCHY_SSH_IDENTITY_FILE=
DIARCHY_HOST_KEY_ALIAS=
DIARCHY_KVM_SIDE=left
DIARCHY_HOST_FINGERPRINT=
DIARCHY_STREAM_RESOLUTION=1920x1080
DIARCHY_STREAM_FPS=60
DIARCHY_STREAM_OVERLAY=0
DIARCHY_STREAM_OUTPUT=auto

"""

private let header = "# Managed by Diarchy"
private let fingerprint = Array(repeating: "AA", count: 32).joined(separator: ":")

/// Header plus `user`/`LAN host` defaults, with `overrides` replacing or adding keys (in insertion order).
private func file(_ overrides: KeyValuePairs<String, String> = [:]) -> String {
    var pairs: [(String, String)] = [("DIARCHY_SSH_USER", "alice"), ("DIARCHY_LAN_HOST", "192.0.2.10")]
    for (key, value) in overrides {
        if let index = pairs.firstIndex(where: { $0.0 == key }) {
            pairs[index].1 = value
        } else {
            pairs.append((key, value))
        }
    }
    return header + "\n" + pairs.map { "\($0.0)=\($0.1)" }.joined(separator: "\n") + "\n"
}

private func failure(_ text: String) -> String? {
    do throws(ConfigError) {
        _ = try ConfigCodec.parse(text)
        return nil
    } catch {
        return error.reason
    }
}

private func parsed(_ text: String) throws -> DiarchyConfig {
    try ConfigCodec.parse(text)
}

struct ReasonCase: Sendable, CustomTestStringConvertible {
    let input: String
    let reason: String
    var testDescription: String { reason }
}

@Suite struct ConfigCodecTests {
    // MARK: Golden / defaults

    @Test func goldenParsesAndRendersByteForByte() throws {
        let config = try parsed(goldenText)
        #expect(config.sshUser == "omarchy")
        #expect(config.lanHost == "192.0.2.10")
        #expect(config.tailscaleHost == "100.64.0.20")
        #expect(config.sshPort == 22)
        #expect(config.kvmSide == .left)
        #expect(try ConfigCodec.render(config) == goldenText)
    }

    @Test func minimalFileGetsDefaults() throws {
        let config = try parsed("\(header)\nDIARCHY_SSH_USER=alice\nDIARCHY_TAILSCALE_HOST=100.64.0.20\n")
        #expect(config.sshPort == 22)
        #expect(config.kvmSide == .left)
        #expect(config.streamResolution == "1920x1080")
        #expect(config.streamFPS == 60)
        #expect(config.streamOverlay == false)
        #expect(config.sshIdentityFile == "")
        #expect(config.hostKeyAlias == "")
        #expect(config.hostFingerprint == "")
        #expect(config.lanHost == "")
    }

    @Test func emptyValuesFallBackToDefaultsLikeBash() throws {
        let config = try parsed(file([
            "DIARCHY_SSH_PORT": "", "DIARCHY_KVM_SIDE": "", "DIARCHY_STREAM_RESOLUTION": "",
            "DIARCHY_STREAM_FPS": "", "DIARCHY_STREAM_OVERLAY": "",
        ]))
        #expect(config.sshPort == 22)
        #expect(config.kvmSide == .left)
        #expect(config.streamResolution == "1920x1080")
        #expect(config.streamFPS == 60)
        #expect(config.streamOverlay == false)
    }

    @Test func fullyPopulatedConfigRoundTrips() throws {
        var config = DiarchyConfig(sshUser: "omarchy", lanHost: "192.0.2.10", tailscaleHost: "omarchy.tail1234.ts.net")
        config.sshPort = 2222
        config.sshIdentityFile = "/Users/user/my keys/id key"
        config.hostKeyAlias = "diarchy-host"
        config.kvmSide = .bottom
        config.hostFingerprint = fingerprint
        config.streamResolution = "2560x1440"
        config.streamFPS = 120
        config.streamOverlay = true
        config.streamOutput = "DP-1"
        let text = try ConfigCodec.render(config)
        #expect(try parsed(text) == config)
        #expect(try ConfigCodec.render(parsed(text)) == text)
    }

    // MARK: Lines

    @Test func commentsAndBlankLinesAfterHeaderAreIgnored() throws {
        let config = try parsed("\(header)\n\n# comment\nDIARCHY_SSH_USER=alice\n# another\n\nDIARCHY_LAN_HOST=192.0.2.10\n")
        #expect(config.sshUser == "alice")
        #expect(config.lanHost == "192.0.2.10")
    }

    @Test func missingTrailingNewlineParses() throws {
        let text = String(file().dropLast())
        #expect(!text.hasSuffix("\n"))
        #expect(try parsed(text).sshUser == "alice")
    }

    @Test func valueMayContainEqualsSignAndIsKeptVerbatim() throws {
        #expect(failure(file(["DIARCHY_SSH_IDENTITY_FILE": "/tmp/a=b"])) == nil)
        #expect(try parsed(file(["DIARCHY_SSH_IDENTITY_FILE": "/tmp/a=b"])).sshIdentityFile == "/tmp/a=b")
    }

    @Test(arguments: [
        ReasonCase(input: "wrong\nDIARCHY_SSH_USER=alice\nDIARCHY_LAN_HOST=192.0.2.10\n", reason: "first line must be \"# Managed by Diarchy\""),
        ReasonCase(input: "\nDIARCHY_SSH_USER=alice\nDIARCHY_LAN_HOST=192.0.2.10\n", reason: "first line must be \"# Managed by Diarchy\""),
        ReasonCase(input: "\u{FEFF}\(header)\nDIARCHY_SSH_USER=alice\nDIARCHY_LAN_HOST=192.0.2.10\n", reason: "first line must be \"# Managed by Diarchy\""),
        ReasonCase(input: "\(header)\r\nDIARCHY_SSH_USER=alice\r\n", reason: "first line must be \"# Managed by Diarchy\""),
        ReasonCase(input: "\(header) \nDIARCHY_SSH_USER=alice\n", reason: "first line must be \"# Managed by Diarchy\""),
        ReasonCase(input: "\(header)\nDIARCHY_SSH_USER=alice\r\n", reason: "line 2: carriage returns are not allowed"),
        ReasonCase(input: "\(header)\nDIARCHY_SSH_USER=alice\nDIARCHY_LAN_HOST=192.0.2.10\r\n", reason: "line 3: carriage returns are not allowed"),
        ReasonCase(input: "\(header)\n\r\nDIARCHY_SSH_USER=alice\n", reason: "line 2: carriage returns are not allowed"),
        ReasonCase(input: "\(header)\nKEY =x\n", reason: "line 2: expected KEY=VALUE"),
        ReasonCase(input: "\(header)\n=x\n", reason: "line 2: expected KEY=VALUE"),
        ReasonCase(input: "\(header)\njunk\n", reason: "line 2: expected KEY=VALUE"),
        ReasonCase(input: "\(header)\ndiarchy_ssh_user=alice\n", reason: "line 2: expected KEY=VALUE"),
        ReasonCase(input: "\(header)\n DIARCHY_SSH_USER=alice\n", reason: "line 2: expected KEY=VALUE"),
        ReasonCase(input: "\(header)\nNOPE=x\n", reason: "line 2: unknown key NOPE"),
        ReasonCase(input: "\(header)\nDIARCHY_SSH_USER=alice\nDIARCHY_SSH_USER=bob\n", reason: "line 3: duplicate key DIARCHY_SSH_USER"),
        ReasonCase(input: "\(header)\nDIARCHY_SSH_USER=\"alice\"\n", reason: "quoted values are not allowed"),
        ReasonCase(input: "\(header)\nDIARCHY_SSH_USER='alice'\n", reason: "quoted values are not allowed"),
        // Per-line errors win over later value validation, in reading order.
        ReasonCase(input: "\(header)\nDIARCHY_SSH_USER=Root\nNOPE=x\n", reason: "line 3: unknown key NOPE"),
        ReasonCase(input: "\(header)\nDIARCHY_SSH_USER=alice\nDIARCHY_LAN_HOST=-bad\nDIARCHY_SSH_USER=\"b\"\n", reason: "line 4: duplicate key DIARCHY_SSH_USER"),
        // Header error beats everything else in the file.
        ReasonCase(input: "bad\nNOPE=x\r\n", reason: "first line must be \"# Managed by Diarchy\""),
    ])
    func lineLevelReasons(_ testCase: ReasonCase) {
        #expect(failure(testCase.input) == testCase.reason)
    }

    @Test func zeroByteFileFailsHeaderCheck() {
        #expect(failure("") == "first line must be \"# Managed by Diarchy\"")
    }

    @Test func headerOnlyFileFailsUserRequired() {
        #expect(failure("\(header)\n") == "DIARCHY_SSH_USER is required")
    }

    // MARK: Values

    @Test(arguments: invalidValueCases())
    func invalidValues(_ testCase: ValueCase) {
        #expect(failure(file([testCase.key: testCase.value])) == "\(testCase.key) is invalid")
    }

    @Test func userRequiredBeatsInvalidHost() {
        let text = "\(header)\nDIARCHY_LAN_HOST=-bad\n"
        #expect(failure(text) == "DIARCHY_SSH_USER is required")
        #expect(failure("\(header)\nDIARCHY_SSH_USER=\nDIARCHY_LAN_HOST=192.0.2.10\n") == "DIARCHY_SSH_USER is required")
    }

    @Test func invalidValuesAreReportedInKeyOrder() {
        // Port is invalid and so is the side; the port key comes first in the table.
        #expect(failure(file(["DIARCHY_KVM_SIDE": "up", "DIARCHY_SSH_PORT": "0"])) == "DIARCHY_SSH_PORT is invalid")
        // An invalid user beats an invalid host even when it appears later in the file.
        #expect(failure("\(header)\nDIARCHY_LAN_HOST=-a\nDIARCHY_SSH_USER=Root\n") == "DIARCHY_SSH_USER is invalid")
        // Value errors beat the missing-host error.
        #expect(failure(file(["DIARCHY_STREAM_FPS": "1", "DIARCHY_LAN_HOST": ""])) == "DIARCHY_STREAM_FPS is invalid")
    }

    @Test func bothHostsMissingIsReportedLast() {
        #expect(failure("\(header)\nDIARCHY_SSH_USER=alice\n")
            == "at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required")
        #expect(failure(file(["DIARCHY_LAN_HOST": ""]))
            == "at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required")
    }

    @Test func injectionLookingValueIsRejectedWithoutSideEffects() throws {
        try withTempDir { dir in
            let marker = dir.appendingPathComponent("MARKER")
            let value = "$(touch \(marker.path))"
            #expect(failure(file(["DIARCHY_LAN_HOST": value])) == "DIARCHY_LAN_HOST is invalid")
            #expect(!FileManager.default.fileExists(atPath: marker.path))
        }
    }

    // MARK: Accepted edges

    @Test func acceptedEdgeValues() throws {
        #expect(try parsed(file(["DIARCHY_SSH_PORT": "1"])).sshPort == 1)
        #expect(try parsed(file(["DIARCHY_SSH_PORT": "65535"])).sshPort == 65535)
        #expect(try parsed(file(["DIARCHY_STREAM_FPS": "30"])).streamFPS == 30)
        #expect(try parsed(file(["DIARCHY_STREAM_FPS": "240"])).streamFPS == 240)
        #expect(try parsed(file(["DIARCHY_SSH_USER": "_a-b9"])).sshUser == "_a-b9")
        let user32 = String(repeating: "a", count: 32)
        #expect(try parsed(file(["DIARCHY_SSH_USER": user32])).sshUser == user32)
        let host253 = String(repeating: "a", count: 253)
        #expect(try parsed(file(["DIARCHY_LAN_HOST": host253])).lanHost == host253)
        #expect(try parsed(file(["DIARCHY_LAN_HOST": "a"])).lanHost == "a")
        #expect(try parsed(file(["DIARCHY_LAN_HOST": "nas.example-host.lan"])).lanHost == "nas.example-host.lan")
        let identity = "/Users/user/my keys/id key"
        #expect(try parsed(file(["DIARCHY_SSH_IDENTITY_FILE": identity])).sshIdentityFile == identity)
        #expect(try parsed(file(["DIARCHY_SSH_IDENTITY_FILE": "/a/..b/c.."])).sshIdentityFile == "/a/..b/c..")
        #expect(try parsed(file(["DIARCHY_HOST_FINGERPRINT": fingerprint])).hostFingerprint == fingerprint)
        #expect(try parsed(file(["DIARCHY_STREAM_OVERLAY": "1"])).streamOverlay == true)
        #expect(try parsed(file(["DIARCHY_STREAM_RESOLUTION": "800x600"])).streamResolution == "800x600")
        for side in KVMSide.allCases {
            #expect(try parsed(file(["DIARCHY_KVM_SIDE": side.rawValue])).kvmSide == side)
        }
    }

    @Test func leadingZeroPortIsDecimal() throws {
        #expect(try parsed(file(["DIARCHY_SSH_PORT": "022"])).sshPort == 22)
        #expect(try parsed(file(["DIARCHY_SSH_PORT": "00022"])).sshPort == 22)
        #expect(try parsed(file(["DIARCHY_STREAM_FPS": "060"])).streamFPS == 60)
        #expect(try parsed(file(["DIARCHY_STREAM_FPS": "089"])).streamFPS == 89)
        #expect(try parsed(file(["DIARCHY_SSH_PORT": "08080"])).sshPort == 8080)
        #expect(try parsed(file(["DIARCHY_SSH_PORT": "0022"])).sshPort == 22)
    }

    // MARK: Derived values

    @Test func effectiveHostKeyAliasPrecedence() {
        var config = DiarchyConfig(sshUser: "alice", lanHost: "192.0.2.10", tailscaleHost: "100.64.0.10")
        #expect(config.effectiveHostKeyAlias == "100.64.0.10")
        config.hostKeyAlias = "alias"
        #expect(config.effectiveHostKeyAlias == "alias")
        config.hostKeyAlias = ""
        config.tailscaleHost = ""
        #expect(config.effectiveHostKeyAlias == "192.0.2.10")
        config.lanHost = ""
        #expect(config.effectiveHostKeyAlias == "")
    }

    @Test func effectiveAliasFromParsedFiles() throws {
        #expect(try parsed(file(["DIARCHY_TAILSCALE_HOST": "100.64.0.10"])).effectiveHostKeyAlias == "100.64.0.10")
        #expect(try parsed(file()).effectiveHostKeyAlias == "192.0.2.10")
        #expect(try parsed(file(["DIARCHY_TAILSCALE_HOST": "100.64.0.10", "DIARCHY_HOST_KEY_ALIAS": "alias"])).effectiveHostKeyAlias == "alias")
        // Stored raw: no alias is materialised in the parsed config.
        #expect(try parsed(file()).hostKeyAlias == "")
    }

    @Test func preferredHostPrefersLAN() {
        var config = DiarchyConfig(sshUser: "alice", lanHost: "192.0.2.10", tailscaleHost: "100.64.0.10")
        #expect(config.preferredHost == "192.0.2.10")
        config.lanHost = ""
        #expect(config.preferredHost == "100.64.0.10")
        config.tailscaleHost = ""
        #expect(config.preferredHost == nil)
    }

    // MARK: Render

    @Test func renderUppercasesFingerprintAndWritesTableOrderWithExplicitDefaults() throws {
        var config = DiarchyConfig(sshUser: "alice", lanHost: "192.0.2.10")
        config.hostFingerprint = fingerprint.lowercased()
        let text = try ConfigCodec.render(config)
        #expect(text == """
        # Managed by Diarchy
        DIARCHY_SSH_USER=alice
        DIARCHY_LAN_HOST=192.0.2.10
        DIARCHY_TAILSCALE_HOST=
        DIARCHY_SSH_PORT=22
        DIARCHY_SSH_IDENTITY_FILE=
        DIARCHY_HOST_KEY_ALIAS=
        DIARCHY_KVM_SIDE=left
        DIARCHY_HOST_FINGERPRINT=\(fingerprint)
        DIARCHY_STREAM_RESOLUTION=1920x1080
        DIARCHY_STREAM_FPS=60
        DIARCHY_STREAM_OVERLAY=0
        DIARCHY_STREAM_OUTPUT=auto

        """)
        #expect(text.hasSuffix("\n"))
    }

    @Test func renderOverlayTrueWritesOne() throws {
        var config = DiarchyConfig(sshUser: "alice", lanHost: "192.0.2.10")
        config.streamOverlay = true
        #expect(try ConfigCodec.render(config).contains("DIARCHY_STREAM_OVERLAY=1\n"))
    }

    @Test func streamOutputRoundTripsAndDefaultsToAuto() throws {
        #expect(try parsed(file()).streamOutput == "auto")
        #expect(try parsed(file(["DIARCHY_STREAM_OUTPUT": ""])).streamOutput == "auto")
        #expect(try parsed(file(["DIARCHY_STREAM_OUTPUT": "DP-1"])).streamOutput == "DP-1")
        var config = DiarchyConfig(sshUser: "alice", lanHost: "192.0.2.10")
        config.streamOutput = "eDP-1"
        let text = try ConfigCodec.render(config)
        #expect(text.hasSuffix("DIARCHY_STREAM_OUTPUT=eDP-1\n"))
        #expect(try parsed(text).streamOutput == "eDP-1")
    }

    @Test(arguments: invalidConfigs())
    func renderThrowsTheSameReasons(_ testCase: InvalidConfigCase) {
        do throws(ConfigError) {
            _ = try ConfigCodec.render(testCase.config)
            Issue.record("render accepted \(testCase.reason)")
        } catch {
            #expect(error.reason == testCase.reason)
        }
        do throws(ConfigError) {
            try testCase.config.validate()
            Issue.record("validate accepted \(testCase.reason)")
        } catch {
            #expect(error.reason == testCase.reason)
        }
    }

    @Test func validateAcceptsDefaultsWithOneHost() throws {
        try DiarchyConfig(sshUser: "alice", tailscaleHost: "100.64.0.20").validate()
    }

    @Test func configErrorDescriptionHasPrefix() {
        #expect(ConfigError(reason: "DIARCHY_SSH_PORT is invalid").description == "diarchy-config: DIARCHY_SSH_PORT is invalid")
    }
}

struct InvalidConfigCase: Sendable, CustomTestStringConvertible {
    let config: DiarchyConfig
    let reason: String
    var testDescription: String { reason }
}

private func invalidConfigs() -> [InvalidConfigCase] {
    func make(_ reason: String, _ change: (inout DiarchyConfig) -> Void) -> InvalidConfigCase {
        var config = DiarchyConfig(sshUser: "alice", lanHost: "192.0.2.10")
        change(&config)
        return InvalidConfigCase(config: config, reason: reason)
    }
    return [
        make("DIARCHY_SSH_USER is required") { $0.sshUser = "" },
        make("DIARCHY_SSH_USER is invalid") { $0.sshUser = "Root" },
        make("DIARCHY_LAN_HOST is invalid") { $0.lanHost = "-bad" },
        make("DIARCHY_TAILSCALE_HOST is invalid") { $0.tailscaleHost = "a_b" },
        make("DIARCHY_SSH_PORT is invalid") { $0.sshPort = 0 },
        make("DIARCHY_SSH_PORT is invalid") { $0.sshPort = 65536 },
        make("DIARCHY_SSH_PORT is invalid") { $0.sshPort = -1 },
        make("DIARCHY_SSH_IDENTITY_FILE is invalid") { $0.sshIdentityFile = "relative" },
        make("DIARCHY_SSH_IDENTITY_FILE is invalid") { $0.sshIdentityFile = "/tmp/../id" },
        make("DIARCHY_SSH_IDENTITY_FILE is invalid") { $0.sshIdentityFile = "/tmp/a\nDIARCHY_KVM_SIDE=top" },
        make("DIARCHY_SSH_IDENTITY_FILE is invalid") { $0.sshIdentityFile = "/tmp/o'brien" },
        make("DIARCHY_SSH_IDENTITY_FILE is invalid") { $0.sshIdentityFile = "/tmp/a\"b" },
        make("DIARCHY_SSH_IDENTITY_FILE is invalid") { $0.sshIdentityFile = "/tmp/a\nb" },
        make("DIARCHY_SSH_IDENTITY_FILE is invalid") { $0.sshIdentityFile = "/tmp/a\r" },
        make("DIARCHY_HOST_KEY_ALIAS is invalid") { $0.hostKeyAlias = "a_b" },
        make("DIARCHY_HOST_FINGERPRINT is invalid") { $0.hostFingerprint = "AA:BB" },
        make("DIARCHY_STREAM_RESOLUTION is invalid") { $0.streamResolution = "1920*1080" },
        make("DIARCHY_STREAM_RESOLUTION is invalid") { $0.streamResolution = "1920x1080\nDIARCHY_SSH_USER=x" },
        make("DIARCHY_STREAM_FPS is invalid") { $0.streamFPS = 29 },
        make("DIARCHY_STREAM_FPS is invalid") { $0.streamFPS = 241 },
        make("DIARCHY_STREAM_OUTPUT is invalid") { $0.streamOutput = "DP 1" },
        make("at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required") { $0.lanHost = "" },
    ]
}

struct ValueCase: Sendable, CustomTestStringConvertible {
    let key: String
    let value: String
    var testDescription: String { "\(key)=\(value.prefix(40))" }
}

private func invalidValueCases() -> [ValueCase] {
    var cases: [ValueCase] = []
    func add(_ key: String, _ values: [String]) {
        cases += values.map { ValueCase(key: key, value: $0) }
    }
    let groups31 = Array(repeating: "AA", count: 31).joined(separator: ":")
    let groups33 = Array(repeating: "AA", count: 33).joined(separator: ":")
    let lowercaseFirst = "aa" + fingerprint.dropFirst(2)

    add("DIARCHY_SSH_USER", ["Root", "Bad", "1abc", String(repeating: "a", count: 33), "a b", " alice", "alice "])
    add("DIARCHY_LAN_HOST", [
        "-a", "-bad.example", "a_b", "bad_host", "a b", "a-", String(repeating: "a", count: 254),
        "$(touch MARKER)", "`touch MARKER`", " 192.0.2.10", "192.0.2.10 ",
    ])
    add("DIARCHY_TAILSCALE_HOST", ["-a", "a_b"])
    add("DIARCHY_SSH_PORT", ["0", "65536", "22a", "-1", "+22", " 22", "000022", "\u{0662}\u{0662}"])
    add("DIARCHY_SSH_IDENTITY_FILE", ["relative", "/tmp/../id", "/a/..", "/../a"])
    add("DIARCHY_HOST_KEY_ALIAS", ["a_b", "-a"])
    add("DIARCHY_KVM_SIDE", ["middle", "up", "Left"])
    add("DIARCHY_HOST_FINGERPRINT", [lowercaseFirst, fingerprint.lowercased(), groups31, groups33])
    add("DIARCHY_STREAM_RESOLUTION", ["1920*1080", "99x99", "99x1080", "12345x1"])
    add("DIARCHY_STREAM_FPS", ["29", "241", "6o", "-60", "99999999999999999999", "029", "0300", "abc", "0", "0060"])
    add("DIARCHY_STREAM_OVERLAY", ["2", "true", "01"])
    add("DIARCHY_STREAM_OUTPUT", ["DP 1", "DP_1", String(repeating: "a", count: 33), "-é"])
    return cases
}
