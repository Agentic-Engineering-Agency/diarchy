import Foundation
import Testing
@testable import DiarchyCore

@Suite struct PathsTests {
    @Test func moonlightIsInstalledOnlyWhenItsExecutableExists() throws {
        try withTempDir { root in
            let paths = Paths(home: root, payload: nil)
            #expect(!paths.moonlightInstalled)
            let binary = URL(fileURLWithPath: paths.moonlightExecutable)
            try FileManager.default.createDirectory(
                at: binary.deletingLastPathComponent(), withIntermediateDirectories: true)
            try "#!/bin/sh\n".write(to: binary, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: binary.path)
            #expect(paths.moonlightInstalled)
        }
    }

    @Test func localBinIsAnAbsolutePathUnderTheGivenHome() throws {
        try withTempDir { root in
            let paths = Paths(home: root, payload: nil)
            let path = paths.localBin("diarchy-ssh")
            #expect(path.hasPrefix("/"))
            #expect(path.hasSuffix("/.local/bin/diarchy-ssh"))
            #expect(path == root.appendingPathComponent(".local/bin/diarchy-ssh").path)
        }
    }

    @Test func applicationAndConfigurationPaths() throws {
        try withTempDir { root in
            let paths = Paths(home: root, payload: nil)
            #expect(paths.lanMouseApp.path == root.appendingPathComponent("Applications/Lan Mouse.app").path)
            #expect(
                paths.moonlightExecutable
                    == root.appendingPathComponent("Applications/Moonlight.app/Contents/MacOS/Moonlight").path)
            #expect(
                paths.lanMouseCertificate.path
                    == root.appendingPathComponent(".config/lan-mouse/lan-mouse.pem").path)
        }
    }

    @Test func configURLFollowsConfigStoreDefault() throws {
        try withTempDir { root in
            let paths = Paths(home: root, payload: nil)
            let expected = ConfigStore.defaultURL(
                environment: ProcessInfo.processInfo.environment, home: root)
            #expect(paths.configURL == expected)
        }
    }

    @Test func nilPayloadYieldsNoPayloadPathAndNoVersion() throws {
        try withTempDir { root in
            let paths = Paths(home: root, payload: nil)
            #expect(paths.payloadPath("client/install.sh") == nil)
            #expect(paths.bundledVersion() == nil)
        }
    }

    @Test func payloadPathJoinsRelativeToThePayload() throws {
        try withTempDir { root in
            let payload = root.appendingPathComponent("payload", isDirectory: true)
            let paths = Paths(home: root, payload: payload)
            #expect(paths.payloadPath("client/install.sh") == payload.appendingPathComponent("client/install.sh").path)
        }
    }

    @Test func bundledVersionIsTrimmed() throws {
        try withTempDir { root in
            let payload = root.appendingPathComponent("payload", isDirectory: true)
            try FileManager.default.createDirectory(at: payload, withIntermediateDirectories: true)
            try "1.0.0\n".write(to: payload.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
            #expect(Paths(home: root, payload: payload).bundledVersion() == "1.0.0")
        }
    }

    @Test func emptyOrMissingVersionFileIsNil() throws {
        try withTempDir { root in
            let payload = root.appendingPathComponent("payload", isDirectory: true)
            try FileManager.default.createDirectory(at: payload, withIntermediateDirectories: true)
            let paths = Paths(home: root, payload: payload)
            #expect(paths.bundledVersion() == nil)
            try " \n".write(to: payload.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
            #expect(paths.bundledVersion() == nil)
        }
    }

    @Test func pathsWithSpacesSurviveUnescaped() throws {
        try withTempDir { root in
            let home = root.appendingPathComponent("a b", isDirectory: true)
            let payload = root.appendingPathComponent("Diarchy.app/Contents/Resources/payload", isDirectory: true)
            try FileManager.default.createDirectory(at: payload, withIntermediateDirectories: true)
            try "2.1.0\n".write(to: payload.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
            let paths = Paths(home: home, payload: payload)
            #expect(paths.localBin("diarchy-stream").hasSuffix("/a b/.local/bin/diarchy-stream"))
            #expect(!paths.localBin("diarchy-stream").contains("%20"))
            #expect(paths.lanMouseApp.path.hasSuffix("/a b/Applications/Lan Mouse.app"))
            #expect(paths.moonlightExecutable.hasSuffix("/a b/Applications/Moonlight.app/Contents/MacOS/Moonlight"))
            #expect(paths.payloadPath("host/install.sh")?.contains("Diarchy.app/Contents/Resources/payload/host/install.sh") == true)
            #expect(paths.bundledVersion() == "2.1.0")
        }
    }

    @Test func liveUsesTheCurrentUsersHome() {
        let paths = Paths.live()
        #expect(paths.home.path == FileManager.default.homeDirectoryForCurrentUser.path)
    }
}
