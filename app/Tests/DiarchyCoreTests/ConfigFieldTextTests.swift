import Testing

@testable import DiarchyCore

@Suite struct ConfigFieldTextTests {
    @Test(arguments: [
        ("DIARCHY_SSH_USER is required", "Enter the SSH user"),
        ("DIARCHY_SSH_USER is invalid", "SSH user is invalid"),
        ("DIARCHY_LAN_HOST is invalid", "LAN host is invalid"),
        ("DIARCHY_TAILSCALE_HOST is invalid", "Tailscale host is invalid"),
        ("DIARCHY_SSH_PORT is invalid", "SSH port is invalid"),
        ("DIARCHY_SSH_IDENTITY_FILE is invalid", "SSH key is invalid"),
        (
            "at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required",
            "Enter at least one host (LAN or Tailscale)"
        ),
        ("DIARCHY_STREAM_RESOLUTION is invalid", "Streaming resolution is invalid"),
        ("DIARCHY_STREAM_FPS is invalid", "Streaming FPS is invalid"),
        ("DIARCHY_STREAM_OUTPUT is invalid", "Stream output is invalid"),
    ])
    func everyKnownReasonHasAnEnglishLabel(reason: String, expected: String) {
        #expect(ConfigFieldText.message(for: reason) == expected)
    }

    @Test func anUnknownReasonIsShownAsIs() {
        #expect(ConfigFieldText.message(for: "cannot write configuration file: /x") == "cannot write configuration file: /x")
    }

    @Test func realValidatorReasonsAreAllMapped() {
        var config = DiarchyConfig(sshUser: "", lanHost: "host.example")
        #expect(ConfigFieldText.message(for: reasonOf(config)) == "Enter the SSH user")
        config.sshUser = "user"
        config.sshPort = 0
        #expect(ConfigFieldText.message(for: reasonOf(config)) == "SSH port is invalid")
        config.sshPort = 22
        config.lanHost = ""
        #expect(ConfigFieldText.message(for: reasonOf(config)) == "Enter at least one host (LAN or Tailscale)")
    }

    private func reasonOf(_ config: DiarchyConfig) -> String {
        do {
            try config.validate()
            return ""
        } catch {
            return error.reason
        }
    }

    @Test(arguments: [("", 22), ("  ", 22), ("22", 22), (" 2222 ", 2222), ("abc", 0), ("-1", 0), ("22x", 0)])
    func theTypedPortMapsToANumberAndEmptyMeansTheDefault(text: String, expected: Int) {
        #expect(ConfigFieldText.port(from: text) == expected)
    }
}
