import Testing

@testable import DiarchyCore

/// `diarchy-kvm-configure` renders the Lan Mouse config from the LAN host, Tailscale host, KVM side and host
/// fingerprint; any other field changing must not trigger a re-render.
@Suite struct LanMouseConfigDiffTests {
    private func base() -> DiarchyConfig {
        var config = DiarchyConfig(sshUser: "omarchy", lanHost: "192.0.2.10", tailscaleHost: "100.64.0.10")
        config.kvmSide = .left
        config.hostFingerprint = Fixtures.fingerprintAA
        return config
    }

    @Test func identicalConfigsDoNotDiffer() {
        #expect(!base().lanMouseConfigDiffers(from: base()))
    }

    @Test func aChangedLanHostDiffers() {
        var other = base()
        other.lanHost = "192.0.2.11"
        #expect(base().lanMouseConfigDiffers(from: other))
    }

    @Test func aChangedTailscaleHostDiffers() {
        var other = base()
        other.tailscaleHost = "100.64.0.20"
        #expect(base().lanMouseConfigDiffers(from: other))
    }

    @Test func aChangedKVMSideDiffers() {
        var other = base()
        other.kvmSide = .top
        #expect(base().lanMouseConfigDiffers(from: other))
    }

    @Test func aChangedHostFingerprintDiffers() {
        var other = base()
        other.hostFingerprint = ""
        #expect(base().lanMouseConfigDiffers(from: other))
    }

    @Test func fieldsLanMouseDoesNotRenderDoNotDiffer() {
        var other = base()
        other.sshUser = "someone"
        other.sshPort = 2222
        other.sshIdentityFile = "/tmp/key"
        other.hostKeyAlias = "alias.example"
        other.streamResolution = "1280x720"
        other.streamFPS = 30
        other.streamOverlay = true
        #expect(!base().lanMouseConfigDiffers(from: other))
    }
}
