import Foundation
import Testing

@testable import DiarchyCore

/// What is on disk: every field set, including the two the Settings window never edits.
private func onDisk() -> DiarchyConfig {
    var config = DiarchyConfig(sshUser: "omarchy", lanHost: "192.0.2.10", tailscaleHost: "diarchy.example")
    config.sshPort = 2222
    config.sshIdentityFile = "/Users/user/my keys/id key"
    config.hostKeyAlias = "alias.example"
    config.kvmSide = .right
    config.hostFingerprint = Fixtures.fingerprintAA
    config.streamResolution = "2560x1440"
    config.streamFPS = 120
    config.streamOverlay = true
    config.streamOutput = "eDP-1"
    return config
}

/// What the window holds: every editable field different, and the two hidden ones at their blank defaults.
private func draft() -> DiarchyConfig {
    var config = DiarchyConfig(sshUser: "other", lanHost: "192.0.2.77", tailscaleHost: "")
    config.sshPort = 22
    config.sshIdentityFile = ""
    config.kvmSide = .bottom
    config.streamResolution = "1280x720"
    config.streamFPS = 30
    config.streamOverlay = false
    config.streamOutput = "DP-1"
    return config
}

@Suite struct ConfigSettingsTests {
    // MARK: applyingSettings

    @Test func theDraftWinsOnEveryEditableField() {
        let merged = onDisk().applyingSettings(from: draft())
        #expect(merged.sshUser == "other")
        #expect(merged.lanHost == "192.0.2.77")
        #expect(merged.tailscaleHost == "")
        #expect(merged.sshPort == 22)
        #expect(merged.sshIdentityFile == "")
        #expect(merged.kvmSide == .bottom)
        #expect(merged.streamResolution == "1280x720")
        #expect(merged.streamFPS == 30)
        #expect(merged.streamOverlay == false)
        #expect(merged.streamOutput == "DP-1")
    }

    @Test func theHostFingerprintAndKeyAliasComeFromTheReceiver() {
        var edited = draft()
        edited.hostFingerprint = Fixtures.fingerprintBB
        edited.hostKeyAlias = "draft-alias.example"
        let merged = onDisk().applyingSettings(from: edited)
        #expect(merged.hostFingerprint == Fixtures.fingerprintAA)
        #expect(merged.hostKeyAlias == "alias.example")
    }

    // MARK: saveSettings

    @Test func savingKeepsTheFingerprintAndAliasOnDisk() throws {
        try withTempDir { root in
            let store = ConfigStore(url: root.appendingPathComponent("diarchy.conf"))
            try store.save(onDisk())
            let saved = try store.saveSettings(draft())
            let reloaded = try #require(try store.load())
            #expect(saved == reloaded)
            #expect(reloaded.hostFingerprint == Fixtures.fingerprintAA)
            #expect(reloaded.hostKeyAlias == "alias.example")
            #expect(reloaded.sshUser == "other")
            #expect(reloaded.kvmSide == .bottom)
        }
    }

    @Test func savingWithoutAFileWritesTheDraft() throws {
        try withTempDir { root in
            let store = ConfigStore(url: root.appendingPathComponent("nested/diarchy.conf"))
            let saved = try store.saveSettings(draft())
            #expect(saved == draft())
            #expect(try store.load() == draft())
        }
    }

    @Test func anInvalidDraftIsRejectedBeforeAnythingIsWritten() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            let store = ConfigStore(url: url)
            try store.save(onDisk())
            let before = try Data(contentsOf: url)

            var invalid = draft()
            invalid.sshPort = 70000
            #expect(throws: ConfigError(reason: "DIARCHY_SSH_PORT is invalid")) { try store.saveSettings(invalid) }
            #expect(try Data(contentsOf: url) == before)
        }
    }

    @Test func anInvalidDraftWithoutAFileCreatesNothing() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            var invalid = draft()
            invalid.sshUser = ""
            #expect(throws: ConfigError(reason: "DIARCHY_SSH_USER is required")) {
                try ConfigStore(url: url).saveSettings(invalid)
            }
            #expect(!FileManager.default.fileExists(atPath: url.path))
        }
    }

    @Test func aFileWithInvalidContentIsRepairedByTheDraft() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            try Data("not a host config\n".utf8).write(to: url)
            let store = ConfigStore(url: url)
            let saved = try store.saveSettings(draft())
            #expect(saved == draft())
            #expect(try store.load() == draft())
        }
    }

    @Test func anUnreadableFileIsNotOverwritten() throws {
        try withTempDir { root in
            let url = root.appendingPathComponent("diarchy.conf")
            let store = ConfigStore(url: url)
            try store.save(onDisk())
            let before = try Data(contentsOf: url)
            try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: url.path)
            defer { try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path) }

            #expect(throws: ConfigError.self) { try store.saveSettings(draft()) }
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            #expect(try Data(contentsOf: url) == before)
        }
    }

    @Test func aWriteFailureIsAConfigError() throws {
        try withTempDir { root in
            // The parent of the config is a regular file, so the directory cannot be created.
            let blocker = root.appendingPathComponent("blocker")
            try Data().write(to: blocker)
            let store = ConfigStore(url: blocker.appendingPathComponent("diarchy.conf"))
            #expect(throws: ConfigError(reason: "cannot write configuration file: \(store.url.path)")) {
                try store.saveSettings(draft())
            }
        }
    }

    // MARK: update

    @Test func updateEditsOnlyWhatTheClosureTouches() throws {
        try withTempDir { root in
            let store = ConfigStore(url: root.appendingPathComponent("diarchy.conf"))
            try store.save(onDisk())
            let saved = try store.update(fallback: draft()) { $0.kvmSide = .top }
            var expected = onDisk()
            expected.kvmSide = .top
            #expect(saved == expected)
            #expect(try store.load() == expected)
        }
    }

    @Test func updateStartsFromTheFallbackWithoutAFile() throws {
        try withTempDir { root in
            let store = ConfigStore(url: root.appendingPathComponent("diarchy.conf"))
            let saved = try store.update(fallback: draft()) { $0.kvmSide = .top }
            var expected = draft()
            expected.kvmSide = .top
            #expect(saved == expected)
        }
    }

    @Test func updateReturnsTheValueAReloadGives() throws {
        try withTempDir { root in
            let store = ConfigStore(url: root.appendingPathComponent("diarchy.conf"))
            let saved = try store.update(fallback: draft()) { $0.hostFingerprint = Fixtures.fingerprintAA.lowercased() }
            #expect(saved.hostFingerprint == Fixtures.fingerprintAA)
            #expect(try store.load() == saved)
        }
    }
}
