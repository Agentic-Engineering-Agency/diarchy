import Foundation
import Testing

@testable import DiarchyCore

/// Thread-safe line sink for `onLine`.
private final class Lines: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var storage: [String] = []
    var all: [String] { lock.lock(); defer { lock.unlock() }; return storage }
    func append(_ line: String) { lock.lock(); storage.append(line); lock.unlock() }
}

private func ok(_ stdout: String = "", stderr: String = "", exit: Int32 = 0) -> Result<CommandResult, CommandError> {
    .success(CommandResult(exitCode: exit, stdout: stdout, stderr: stderr))
}

private let macScripts = ["install-lan-mouse.sh", "install-clipboard-file-transfer.sh", "install.sh"]

/// A payload under `app dir/Diarchy.app/Contents/Resources/payload` and a home with a space in both (Review Focus 2).
private struct Fixture {
    let root: URL
    let paths: Paths
    let payload: URL
    let runner = FakeCommandRunner()

    init(root: URL, executable: Bool = true, scripts: [String] = macScripts) throws {
        self.root = root
        payload = root.appendingPathComponent("app dir/Diarchy.app/Contents/Resources/payload", isDirectory: true)
        let client = payload.appendingPathComponent("client", isDirectory: true)
        try FileManager.default.createDirectory(at: client, withIntermediateDirectories: true)
        for name in scripts {
            let url = client.appendingPathComponent(name)
            try "#!/bin/sh\nexit 0\n".write(to: url, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: executable ? 0o755 : 0o644], ofItemAtPath: url.path)
        }
        paths = Paths(home: root.appendingPathComponent("home dir", isDirectory: true), payload: payload)
    }

    func script(_ name: String) -> String { payload.appendingPathComponent("client/\(name)").path }
    var installer: Installer { Installer(paths: paths, runner: runner) }

    /// Every script succeeds with one stdout line `<name> out`, unless a rule given earlier matches first.
    func scriptsSucceed() {
        for name in macScripts {
            let path = script(name)
            runner.when({ $0.executable == path }, return: ok("\(name) out\n"))
        }
    }
}

@Suite(.timeLimit(.minutes(1))) struct InstallerTests {
    // MARK: Mac

    @Test(arguments: [false, true])
    func macRunsTheScriptsInOrderByTheirExactPathWithoutArguments(includeStreaming: Bool) async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.scriptsSucceed()
            try await fixture.installer.installMac(includeStreaming: includeStreaming, onLine: { _ in })
            let expected = (includeStreaming ? macScripts : Array(macScripts.dropLast())).map(fixture.script)
            #expect(fixture.runner.calls.map(\.executable) == expected)
            #expect(fixture.runner.calls.allSatisfy { $0.arguments.isEmpty && $0.stdin == nil })
            // The flag is added to the inherited environment (which `environment` otherwise replaces).
            #expect(fixture.runner.calls.allSatisfy { $0.environment?["DIARCHY_SKIP_SSH_PREFLIGHT"] == "1" })
            #expect(
                fixture.runner.calls.allSatisfy { $0.environment?["PATH"] == ProcessInfo.processInfo.environment["PATH"] })
            #expect(fixture.runner.calls.allSatisfy { $0.timeout == 900 })
        }
    }

    @Test func macInstallRecordsThePayloadVersionAfterEveryScriptSucceeds() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.scriptsSucceed()
            try "2.3.4\n".write(
                to: fixture.payload.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
            try await fixture.installer.installMac(includeStreaming: false, onLine: { _ in })
            #expect(fixture.paths.installedMacVersion() == "2.3.4")
        }
    }

    @Test func aFailedMacInstallDoesNotRecordTheVersion() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when({ $0.executable == fixture.script(macScripts[0]) }, return: ok("[fail] broke\n", exit: 1))
            try "2.3.4\n".write(
                to: fixture.payload.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
            await #expect(throws: InstallerError.self) {
                try await fixture.installer.installMac(includeStreaming: false, onLine: { _ in })
            }
            #expect(fixture.paths.installedMacVersion() == nil)
        }
    }

    @Test func macPassesAPayloadPathWithSpacesUnmodified() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.scriptsSucceed()
            try await fixture.installer.installMac(includeStreaming: true, onLine: { _ in })
            let executables = fixture.runner.calls.map(\.executable)
            #expect(executables.allSatisfy { $0.contains("/Diarchy.app/Contents/Resources/payload/client/") })
            #expect(executables.first?.hasSuffix("/Diarchy.app/Contents/Resources/payload/client/install-lan-mouse.sh") == true)
        }
    }

    @Test func macDeliversAHeaderBeforeEachScriptAndEveryLineOnceInOrder() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when({ $0.executable == fixture.script(macScripts[0]) }, return: ok("lm 1\nlm 2\n", stderr: "lm warn\n"))
            fixture.runner.when({ $0.executable == fixture.script(macScripts[1]) }, return: ok("cb 1\n"))
            fixture.runner.when({ $0.executable == fixture.script(macScripts[2]) }, return: ok("st 1\n"))
            let lines = Lines()
            try await fixture.installer.installMac(includeStreaming: true, onLine: { lines.append($0) })
            #expect(lines.all == [
                "==> install-lan-mouse.sh", "lm 1", "lm 2", "lm warn",
                "==> install-clipboard-file-transfer.sh", "cb 1",
                "==> install.sh", "st 1",
            ])
        }
    }

    @Test func macWithoutStreamingNeverMentionsTheStreamingScript() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.scriptsSucceed()
            let lines = Lines()
            try await fixture.installer.installMac(includeStreaming: false, onLine: { lines.append($0) })
            #expect(!lines.all.contains("==> install.sh"))
        }
    }

    @Test func macStopsAtTheFirstFailureAndNeverRunsTheNextScript() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when(
                { $0.executable == fixture.script(macScripts[0]) }, return: ok("partial\n", stderr: "\ncurl: (6) Could not resolve host\nsecond\n", exit: 3))
            fixture.scriptsSucceed()
            let lines = Lines()
            await #expect(throws: InstallerError.failed(step: "install-lan-mouse.sh", message: "second")) {
                try await fixture.installer.installMac(includeStreaming: true, onLine: { lines.append($0) })
            }
            #expect(fixture.runner.calls.map(\.executable) == [fixture.script(macScripts[0])])
            #expect(lines.all.first == "==> install-lan-mouse.sh")
            #expect(!lines.all.contains("==> install-clipboard-file-transfer.sh"))
        }
    }

    @Test func macFailureShowsTheLastFailLine() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when(
                { $0.executable == fixture.script(macScripts[0]) },
                return: ok("[fail] first problem\nnoise\n", stderr: "[fail] python3 is too old\ntrailing note\n", exit: 1))
            await #expect(throws: InstallerError.failed(step: "install-lan-mouse.sh", message: "[fail] python3 is too old")) {
                try await fixture.installer.installMac(includeStreaming: false, onLine: { _ in })
            }
        }
    }

    @Test func macFailureWithoutAFailLineShowsTheLastNonEmptyLine() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when(
                { $0.executable == fixture.script(macScripts[0]) }, return: ok("one\ntwo\n\n  \n", exit: 1))
            await #expect(throws: InstallerError.failed(step: "install-lan-mouse.sh", message: "two")) {
                try await fixture.installer.installMac(includeStreaming: false, onLine: { _ in })
            }
        }
    }

    @Test func macReportsTheSecondScriptWhenOnlyItFails() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when({ $0.executable == fixture.script(macScripts[0]) }, return: ok())
            fixture.runner.when({ $0.executable == fixture.script(macScripts[1]) }, return: ok(exit: 7))
            fixture.scriptsSucceed()
            await #expect(throws: InstallerError.failed(step: "install-clipboard-file-transfer.sh", message: "exit code 7")) {
                try await fixture.installer.installMac(includeStreaming: true, onLine: { _ in })
            }
            #expect(fixture.runner.calls.count == 2)
        }
    }

    @Test(arguments: macScripts)
    func macWithANonExecutableScriptFailsBeforeRunningAnything(name: String) async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            let path = fixture.script(name)
            try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: path)
            let lines = Lines()
            await #expect(throws: InstallerError.scriptNotExecutable(path)) {
                try await fixture.installer.installMac(includeStreaming: true, onLine: { lines.append($0) })
            }
            #expect(fixture.runner.calls.isEmpty)
            #expect(lines.all.isEmpty)
        }
    }

    @Test func macIgnoresANonExecutableStreamingScriptWhenStreamingIsNotRequested() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: fixture.script("install.sh"))
            fixture.scriptsSucceed()
            try await fixture.installer.installMac(includeStreaming: false, onLine: { _ in })
            #expect(fixture.runner.calls.count == 2)
        }
    }

    @Test func macWithAMissingScriptIsNotExecutable() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root, scripts: Array(macScripts.dropLast()))
            await #expect(throws: InstallerError.scriptNotExecutable(fixture.script("install.sh"))) {
                try await fixture.installer.installMac(includeStreaming: true, onLine: { _ in })
            }
            #expect(fixture.runner.calls.isEmpty)
        }
    }

    @Test func macWithADirectoryInsteadOfAScriptIsNotExecutable() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root, scripts: Array(macScripts.dropLast()))
            try FileManager.default.createDirectory(
                atPath: fixture.script("install.sh"), withIntermediateDirectories: false)
            await #expect(throws: InstallerError.scriptNotExecutable(fixture.script("install.sh"))) {
                try await fixture.installer.installMac(includeStreaming: true, onLine: { _ in })
            }
            #expect(fixture.runner.calls.isEmpty)
        }
    }

    @Test func macTimeoutNamesTheScript() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when(
                { $0.executable == fixture.script(macScripts[0]) }, return: .failure(.timedOut(command: macScripts[0])))
            await #expect(throws: InstallerError.timedOut("install-lan-mouse.sh")) {
                try await fixture.installer.installMac(includeStreaming: true, onLine: { _ in })
            }
        }
    }

    @Test func macLaunchFailureIsAFailedStep() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when(
                { $0.executable == fixture.script(macScripts[0]) },
                return: .failure(.launchFailed(command: fixture.script(macScripts[0]), message: "Permission denied")))
            await #expect(throws: InstallerError.failed(step: "install-lan-mouse.sh", message: "Permission denied")) {
                try await fixture.installer.installMac(includeStreaming: false, onLine: { _ in })
            }
        }
    }

    @Test func macWithoutPayloadFailsWithNoPayload() async {
        let runner = FakeCommandRunner()
        let installer = Installer(paths: Paths(home: URL(fileURLWithPath: "/tmp/diarchy-home"), payload: nil), runner: runner)
        await #expect(throws: InstallerError.noPayload) {
            try await installer.installMac(includeStreaming: true, onLine: { _ in })
        }
        #expect(runner.calls.isEmpty)
    }

    @Test func macRunsRealScriptsFromAPayloadPathWithSpaces() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            for name in macScripts {
                try "#!/bin/sh\necho \"ran $(basename \"$0\") in $(basename \"$(dirname \"$(dirname \"$0\")\")\")\"\n"
                    .write(toFile: fixture.script(name), atomically: true, encoding: .utf8)
            }
            let lines = Lines()
            try await Installer(paths: fixture.paths, runner: ProcessCommandRunner())
                .installMac(includeStreaming: true, onLine: { lines.append($0) })
            #expect(lines.all == [
                "==> install-lan-mouse.sh", "ran install-lan-mouse.sh in payload",
                "==> install-clipboard-file-transfer.sh", "ran install-clipboard-file-transfer.sh in payload",
                "==> install.sh", "ran install.sh in payload",
            ])
        }
    }

    // MARK: Host: static contract

    private static let pipeline =
        #"set -o pipefail; COPYFILE_DISABLE=1 tar --no-xattrs -C "$1" -czf - host VERSION | "$2" -- "$3" "$4" "$5""#

    @Test func hostRemoteScriptIsTheDocumentedSequenceWithoutSingleQuotes() {
        #expect(!Installer.hostRemoteScript.contains("'"))
        #expect(
            Installer.hostRemoteScript
                == #"set -euo pipefail; base="$HOME/.local/share/diarchy"; mkdir -p "$base"; rm -rf "$base/payload.new"; mkdir "$base/payload.new"; tar -xzf - -C "$base/payload.new"; rm -rf "$base/payload.old"; if [ -e "$base/payload" ]; then mv "$base/payload" "$base/payload.old"; fi; mv "$base/payload.new" "$base/payload"; rm -rf "$base/payload.old"; bash "$base/payload/host/install-host.sh""#
        )
        #expect(!Installer.hostRemoteScript.contains("--with-streaming"))
        #expect(!Installer.hostRemoteScript.contains("\n"))
    }

    @Test func remoteWordsQuoteTheScriptAsOneWordAndAppendTheFlagToTheInstallLine() {
        let plain = Installer.remoteWords(withStreaming: false)
        #expect(plain == ["bash", "-c", "'" + Installer.hostRemoteScript + "'"])
        let streaming = Installer.remoteWords(withStreaming: true)
        #expect(streaming == ["bash", "-c", "'" + Installer.hostRemoteScript + " --with-streaming'"])
        #expect(streaming[2].hasSuffix(#"install-host.sh" --with-streaming'"#))
        #expect(!plain.joined().contains("--with-streaming"))
        for words in [plain, streaming] {
            #expect(words[2].dropFirst().dropLast().contains("'") == false)
        }
    }

    // MARK: Host: runner contract

    @Test(arguments: [false, true])
    func hostRunsOneBashPipelineWithEveryValueAsAPositionalArgument(withStreaming: Bool) async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when({ $0.executable == "/bin/bash" }, return: ok("installed\n"))
            let lines = Lines()
            try await fixture.installer.installHost(withStreaming: withStreaming, onLine: { lines.append($0) })
            let call = try #require(fixture.runner.calls.first)
            #expect(fixture.runner.calls.count == 1)
            #expect(call.executable == "/bin/bash")
            #expect(
                call.arguments
                    == ["-c", Self.pipeline, "installer", fixture.payload.path, fixture.paths.localBin("diarchy-ssh")]
                    + Installer.remoteWords(withStreaming: withStreaming))
            #expect(call.timeout == 900)
            #expect(call.environment == nil && call.stdin == nil)
            #expect(lines.all == ["installed"])
            #expect(call.arguments[1].contains("set -o pipefail"))
            #expect(call.arguments[1].contains("host VERSION"))
            #expect(!call.arguments[1].contains(fixture.payload.path))
            #expect(!call.arguments[1].contains("Diarchy"))
            #expect(call.arguments[3].contains(" "))  // the payload path really has a space and travels as one argv entry
            #expect(call.arguments[4].contains(" "))
            #expect(call.arguments[7].contains("--with-streaming") == withStreaming)
        }
    }

    @Test func hostFailureShowsTheFailLineNotTheDownloadProgressMeter() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when(
                { $0.executable == "/bin/bash" },
                return: ok(
                    "[fail] sunshine download failed\n",
                    stderr: "  % Total    % Received % Xferd  Average Speed\n100  1024  100  1024    0     0   2048      0 --:--:--\n",
                    exit: 1))
            await #expect(throws: InstallerError.failed(step: "host", message: "[fail] sunshine download failed")) {
                try await fixture.installer.installHost(withStreaming: false, onLine: { _ in })
            }
        }
    }

    @Test func hostFailureIsTheHostStepWithTheFirstErrorLineWhenNoFailLineExists() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when({ $0.executable == "/bin/bash" }, return: ok(stderr: "ssh: connect to host failed\nmore\n", exit: 255))
            await #expect(throws: InstallerError.failed(step: "host", message: "ssh: connect to host failed")) {
                try await fixture.installer.installHost(withStreaming: false, onLine: { _ in })
            }
        }
    }

    @Test func hostTimeoutAndLaunchFailure() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when({ $0.executable == "/bin/bash" }, return: .failure(.timedOut(command: "bash")))
            await #expect(throws: InstallerError.timedOut("host")) {
                try await fixture.installer.installHost(withStreaming: false, onLine: { _ in })
            }
        }
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            fixture.runner.when(
                { $0.executable == "/bin/bash" }, return: .failure(.launchFailed(command: "/bin/bash", message: "nope")))
            await #expect(throws: InstallerError.failed(step: "host", message: "nope")) {
                try await fixture.installer.installHost(withStreaming: false, onLine: { _ in })
            }
        }
    }

    @Test func hostWithoutPayloadFailsWithNoPayload() async {
        let runner = FakeCommandRunner()
        let installer = Installer(paths: Paths(home: URL(fileURLWithPath: "/tmp/diarchy-home"), payload: nil), runner: runner)
        await #expect(throws: InstallerError.noPayload) {
            try await installer.installHost(withStreaming: true, onLine: { _ in })
        }
        #expect(runner.calls.isEmpty)
    }

    // MARK: Host: real bash and tar against a stub diarchy-ssh

    /// `diarchy-ssh` stand-in under a home with a space: stores stdin and its arguments next to itself.
    private func installStubSSH(in paths: Paths) throws -> (tar: URL, args: URL) {
        let bin = URL(fileURLWithPath: paths.localBin("diarchy-ssh")).deletingLastPathComponent()
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        let stub = bin.appendingPathComponent("diarchy-ssh")
        let script = """
            #!/bin/sh
            dir=$(dirname "$0")
            cat > "$dir/received.tgz"
            for word in "$@"; do printf '%s\\n' "$word"; done > "$dir/args.txt"
            """
        try script.write(to: stub, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: stub.path)
        return (bin.appendingPathComponent("received.tgz"), bin.appendingPathComponent("args.txt"))
    }

    private func addHostFiles(to payload: URL) throws {
        let host = payload.appendingPathComponent("host", isDirectory: true)
        try FileManager.default.createDirectory(at: host, withIntermediateDirectories: true)
        try "#!/bin/bash\n".write(to: host.appendingPathComponent("install-host.sh"), atomically: true, encoding: .utf8)
        try "1.0.0\n".write(to: payload.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
    }

    @Test(arguments: [false, true])
    func hostPipelineTarsHostAndVersionAndHandsTheRemoteWordsToDiarchySSH(withStreaming: Bool) async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            try addHostFiles(to: fixture.payload)
            let (tarball, args) = try installStubSSH(in: fixture.paths)
            // A member that must NOT travel: only `host` and `VERSION` are sent.
            try "x".write(to: fixture.payload.appendingPathComponent("extra.txt"), atomically: true, encoding: .utf8)

            try await Installer(paths: fixture.paths, runner: ProcessCommandRunner())
                .installHost(withStreaming: withStreaming, onLine: { _ in })

            let listing = try await ProcessCommandRunner().run(
                executable: "/usr/bin/tar", arguments: ["-tzf", tarball.path], environment: nil, stdin: nil, timeout: 20)
            let members = Set(listing.stdout.split(separator: "\n").map(String.init))
            #expect(members == ["host/", "host/install-host.sh", "VERSION"])

            let received = try String(contentsOf: args, encoding: .utf8).split(separator: "\n", omittingEmptySubsequences: false)
            #expect(received.dropLast().map(String.init) == ["--"] + Installer.remoteWords(withStreaming: withStreaming))
        }
    }

    @Test func payloadFilesWithExtendedAttributesProduceNoXattrHeadersInTheStream() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            try addHostFiles(to: fixture.payload)
            let (tarball, _) = try installStubSSH(in: fixture.paths)
            let tagged = [
                fixture.payload.appendingPathComponent("host/install-host.sh"),
                fixture.payload.appendingPathComponent("VERSION"),
                fixture.payload.appendingPathComponent("host"),
            ]
            let plain = ProcessCommandRunner()
            for file in tagged {
                let set = try await plain.run(
                    executable: "/usr/bin/xattr", arguments: ["-w", "com.apple.test", "v", file.path], environment: nil,
                    stdin: nil, timeout: 20)
                try #require(set.succeeded, "xattr -w failed: \(set.stderr)")
            }

            try await Installer(paths: fixture.paths, runner: plain).installHost(withStreaming: false, onLine: { _ in })

            // Count lines of the decompressed stream that mention an xattr pax header (`LIBARCHIVE.xattr.*`, `SCHILY.xattr.*`).
            let count = try await plain.run(
                executable: "/bin/sh", arguments: ["-c", #"/usr/bin/gzip -dc "$1" | /usr/bin/grep -a -c -i xattr; true"#, "sh", tarball.path],
                environment: nil, stdin: nil, timeout: 20)
            #expect(count.stdout.trimmingCharacters(in: .whitespacesAndNewlines) == "0")
            let listing = try await plain.run(
                executable: "/usr/bin/tar", arguments: ["-tzf", tarball.path], environment: nil, stdin: nil, timeout: 20)
            #expect(Set(listing.stdout.split(separator: "\n").map(String.init)) == ["host/", "host/install-host.sh", "VERSION"])
        }
    }

    /// bsdtar emits AppleDouble `._*` members for xattrs even with `--no-xattrs` unless COPYFILE_DISABLE is set, and its
    /// own `-t` listing hides them, so the stream is read with Python's `tarfile`.
    @Test func payloadFilesWithExtendedAttributesProduceNoAppleDoubleMembers() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)
            let host = fixture.payload.appendingPathComponent("host", isDirectory: true)
            try FileManager.default.createDirectory(at: host, withIntermediateDirectories: true)
            try "x".write(to: host.appendingPathComponent("x"), atomically: true, encoding: .utf8)
            try "1.0.0\n".write(to: fixture.payload.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
            let (tarball, _) = try installStubSSH(in: fixture.paths)
            let plain = ProcessCommandRunner()
            for file in [host.appendingPathComponent("x"), fixture.payload.appendingPathComponent("VERSION")] {
                let set = try await plain.run(
                    executable: "/usr/bin/xattr", arguments: ["-w", "com.example.test", "1", file.path], environment: nil,
                    stdin: nil, timeout: 20)
                try #require(set.succeeded, "xattr -w failed: \(set.stderr)")
            }

            try await Installer(paths: fixture.paths, runner: plain).installHost(withStreaming: false, onLine: { _ in })

            let names = try await plain.run(
                executable: "/usr/bin/python3",
                arguments: ["-c", "import sys, tarfile\nfor m in tarfile.open(sys.argv[1]).getmembers(): print(m.name)", tarball.path],
                environment: nil, stdin: nil, timeout: 20)
            try #require(names.succeeded, "python3 failed: \(names.stderr)")
            let members = names.stdout.split(separator: "\n").map(String.init)
            #expect(members.contains("host/x") && members.contains("VERSION"))
            #expect(members.filter { $0.split(separator: "/").last?.hasPrefix("._") == true } == [])
        }
    }

    @Test func aTarFailureFailsTheWholePipeline() async throws {
        try await withTempDirAsync { root in
            let fixture = try Fixture(root: root)  // no `host` directory and no VERSION: tar cannot read its members
            _ = try installStubSSH(in: fixture.paths)  // the stub itself exits 0
            let error = await #expect(throws: InstallerError.self) {
                try await Installer(paths: fixture.paths, runner: ProcessCommandRunner())
                    .installHost(withStreaming: false, onLine: { _ in })
            }
            guard case .failed(let step, let message)? = error else {
                Issue.record("expected .failed, got \(String(describing: error))")
                return
            }
            #expect(step == "host")
            #expect(message.contains("host"))
        }
    }

    // MARK: Messages

    @Test func userMessages() {
        #expect(InstallerError.noPayload.userMessage.contains("payload"))
        #expect(InstallerError.scriptNotExecutable("/a b/install.sh").userMessage.contains("/a b/install.sh"))
        #expect(InstallerError.failed(step: "host", message: "boom").userMessage == "host: boom")
        #expect(InstallerError.timedOut("host").userMessage == "host did not respond in time")
    }
}
