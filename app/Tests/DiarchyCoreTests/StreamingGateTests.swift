import Foundation
import Testing

@testable import DiarchyCore

private func probe(_ actions: [String]) throws -> ProbeResult {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(ProbeResult.self, from: Data(Fixtures.probe(actions: actions).utf8))
}

@Suite struct StreamingGateTests {
    @Test func noActionsLetStreamingStart() throws {
        #expect(StreamingGate.refusal(for: try probe([])) == nil)
    }

    @Test func recommendedActionsDoNotBlock() throws {
        let result = try probe([Fixtures.firewallLAN, Fixtures.lidPolicy, Fixtures.unknownAction])
        #expect(StreamingGate.refusal(for: result) == nil)
    }

    @Test func aRequiredActionBlocksWithTitleReasonAndTheVerbatimCommand() throws {
        let refusal = try #require(StreamingGate.refusal(for: try probe([Fixtures.uinputAccess])))
        let action = try #require(try probe([Fixtures.uinputAccess]).requiredActions.first)
        #expect(refusal.hasPrefix("Streaming is blocked."))
        #expect(refusal.contains(action.title))
        #expect(refusal.contains(action.reason))
        #expect(refusal.contains("Run on the host: \(action.command)"))
    }

    @Test func anUnknownRequiredActionFallsBackToTheAgentsText() throws {
        let unknown = Fixtures.unknownAction.replacingOccurrences(of: "\"recommended\"", with: "\"required\"")
        let refusal = try #require(StreamingGate.refusal(for: try probe([unknown])))
        #expect(refusal.contains("A future action"))
        #expect(refusal.contains("Something new"))
        #expect(refusal.contains("Run on the host: true"))
    }

    @Test func onlyTheRequiredActionsAreListedAmongMixedOnes() throws {
        let refusal = try #require(StreamingGate.refusal(for: try probe([Fixtures.lidPolicy, Fixtures.uinputAccess])))
        #expect(refusal.contains("/dev/uinput"))
        #expect(!refusal.contains("sudo mkdir -p /etc/systemd/logind.conf.d"))
    }

    @Test func severalRequiredActionsAreAllListedInOrder() throws {
        let refusal = try #require(
            StreamingGate.refusal(for: try probe([Fixtures.uinputAccess, Fixtures.installPackages])))
        let first = try #require(refusal.range(of: "Run on the host: echo "))
        let second = try #require(refusal.range(of: "Run on the host: sudo pacman -S --needed wl-clipboard rsync"))
        #expect(first.lowerBound < second.lowerBound)
        #expect(refusal.hasPrefix("Streaming is blocked."))
    }
}
