import Foundation
import Testing
@testable import DiarchyCore

private func mode(of url: URL) throws -> Int {
    let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
    return (attributes[.posixPermissions] as? NSNumber)?.intValue ?? -1
}

private func sampleConfig() -> DiarchyConfig {
    var config = DiarchyConfig(sshUser: "omarchy", lanHost: "192.0.2.10", tailscaleHost: "100.64.0.20")
    config.sshIdentityFile = "/Users/user/my keys/id key"
    config.kvmSide = .top
    config.streamOverlay = true
    return config
}

@Suite struct ConfigStoreTests {
    @Test func saveCreatesDirectory0700AndFile0600() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("config-dir/diarchy.conf")
            try ConfigStore(url: url).save(sampleConfig())
            #expect(try mode(of: url.deletingLastPathComponent()) == 0o700)
            #expect(try mode(of: url) == 0o600)
        }
    }

    @Test func saveLeavesNoTemporaryFiles() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            let store = ConfigStore(url: url)
            try store.save(sampleConfig())
            try store.save(sampleConfig())
            let names = try FileManager.default.contentsOfDirectory(atPath: root.path)
            #expect(names == ["diarchy.conf"])
        }
    }

    @Test func saveThenLoadRoundTrips() throws {
        try withTempDir { root in
            let store = ConfigStore(url: root.appendingPathComponent("nested/diarchy.conf"))
            let config = sampleConfig()
            try store.save(config)
            #expect(try store.load() == config)
        }
    }

    @Test func savedBytesAreTheRenderedGoldenText() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            try ConfigStore(url: url).save(DiarchyConfig(sshUser: "omarchy", lanHost: "192.0.2.10", tailscaleHost: "100.64.0.20"))
            let expected = """
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
            #expect(try Data(contentsOf: url) == Data(expected.utf8))
        }
    }

    @Test func loadOfMissingFileIsNil() throws {
        try withTempDir { root in
            let loaded = try ConfigStore(url: root.appendingPathComponent("absent.conf")).load()
            #expect(loaded == nil)
        }
    }

    @Test func loadOfMissingDirectoryIsNil() throws {
        try withTempDir { root in
            let loaded = try ConfigStore(url: root.appendingPathComponent("no/such/dir/diarchy.conf")).load()
            #expect(loaded == nil)
        }
    }

    @Test func loadOfInvalidFileThrowsConfigErrorReason() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            try Data("not a config\n".utf8).write(to: url)
            #expect(throws: ConfigError(reason: "first line must be \"# Managed by Diarchy\"")) {
                _ = try ConfigStore(url: url).load()
            }
        }
    }

    @Test func loadOfInvalidFileThrowsFromUntypedLoad() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            try Data("# Managed by Diarchy\nDIARCHY_SSH_USER=alice\nDIARCHY_SSH_PORT=0\nDIARCHY_LAN_HOST=192.0.2.10\n".utf8).write(to: url)
            #expect(throws: ConfigError(reason: "DIARCHY_SSH_PORT is invalid")) {
                _ = try ConfigStore(url: url).load()
            }
        }
    }

    @Test func loadKeepsUTF8BOMSoItFailsTheHeaderCheck() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            try Data([0xEF, 0xBB, 0xBF] + Array("# Managed by Diarchy\nDIARCHY_SSH_USER=alice\nDIARCHY_LAN_HOST=192.0.2.10\n".utf8)).write(to: url)
            #expect(throws: ConfigError(reason: "first line must be \"# Managed by Diarchy\"")) {
                _ = try ConfigStore(url: url).load()
            }
        }
    }

    @Test func loadOfNonUTF8FileIsUnreadable() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            try Data([0xFF, 0xFE, 0x00, 0x80]).write(to: url)
            #expect(throws: ConfigError(reason: "cannot read configuration file: \(url.path)")) {
                _ = try ConfigStore(url: url).load()
            }
        }
    }

    @Test func loadOfUnreadableFileReportsPath() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            try ConfigStore(url: url).save(sampleConfig())
            try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: url.path)
            defer { try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path) }
            guard geteuid() != 0 else { return }
            #expect(throws: ConfigError(reason: "cannot read configuration file: \(url.path)")) {
                _ = try ConfigStore(url: url).load()
            }
        }
    }

    @Test func loadOfDirectoryAtPathIsUnreadable() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf", isDirectory: true)
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false)
            #expect(throws: ConfigError(reason: "cannot read configuration file: \(url.path)")) {
                _ = try ConfigStore(url: url).load()
            }
        }
    }

    @Test func overwriteReplacesContentAndIgnoresStaleTemporaryFiles() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            let store = ConfigStore(url: url)
            try store.save(sampleConfig())

            // Leftovers of a crashed earlier save, including the names a naive implementation would reuse.
            for name in ["diarchy.conf.tmp", ".diarchy.conf.tmp", ".diarchy.conf.tmp.1", "diarchy.conf.tmp.stale"] {
                try Data("stale".utf8).write(to: root.appendingPathComponent(name))
            }

            var changed = sampleConfig()
            changed.sshPort = 2222
            changed.kvmSide = .bottom
            try store.save(changed)

            #expect(try store.load() == changed)
            #expect(try mode(of: url) == 0o600)
            for name in ["diarchy.conf.tmp", ".diarchy.conf.tmp", ".diarchy.conf.tmp.1", "diarchy.conf.tmp.stale"] {
                #expect(try Data(contentsOf: root.appendingPathComponent(name)) == Data("stale".utf8))
            }
        }
    }

    @Test func overwriteTightensLooseExistingFilePermissions() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            try Data("old".utf8).write(to: url)
            try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: url.path)
            try ConfigStore(url: url).save(sampleConfig())
            #expect(try mode(of: url) == 0o600)
        }
    }

    @Test func saveOfInvalidConfigThrowsAndLeavesExistingFileUntouched() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            let store = ConfigStore(url: url)
            try store.save(sampleConfig())
            let before = try Data(contentsOf: url)

            var invalid = sampleConfig()
            invalid.sshPort = 0
            #expect(throws: ConfigError(reason: "DIARCHY_SSH_PORT is invalid")) {
                try store.save(invalid)
            }
            #expect(try Data(contentsOf: url) == before)
            #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["diarchy.conf"])
        }
    }

    @Test func saveToUnwritableLocationThrowsAndLeavesNothingBehind() throws {
        try withTempDir { root in
            let blocker = root.appendingPathComponent("blocker")
            try Data("file".utf8).write(to: blocker)
            let store = ConfigStore(url: blocker.appendingPathComponent("diarchy.conf"))
            #expect(throws: (any Error).self) {
                try store.save(sampleConfig())
            }
            #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["blocker"])
        }
    }

    @Test func defaultURLHonorsDiarchyConfig() {
        let home = URL(fileURLWithPath: "/Users/user", isDirectory: true)
        let url = ConfigStore.defaultURL(environment: ["DIARCHY_CONFIG": "/tmp/custom dir/x.conf"], home: home)
        #expect(url.path == "/tmp/custom dir/x.conf")
    }

    @Test func defaultURLFallsBackToHomeConfigDirectory() {
        let home = URL(fileURLWithPath: "/Users/user", isDirectory: true)
        let expected = "/Users/user/.config/diarchy/diarchy.conf"
        #expect(ConfigStore.defaultURL(environment: [:], home: home).path == expected)
        #expect(ConfigStore.defaultURL(environment: ["DIARCHY_CONFIG": ""], home: home).path == expected)
        #expect(ConfigStore.defaultURL(environment: ["OTHER": "x"], home: home).path == expected)
    }
}
