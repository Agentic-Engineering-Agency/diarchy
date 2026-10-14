import Foundation
import Testing

@testable import DiarchyCore

private func ok(exit: Int32 = 0) -> Result<CommandResult, CommandError> {
    .success(CommandResult(exitCode: exit, stdout: "", stderr: ""))
}

/// Thread-safe call log shared by the fakes.
private final class Log: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var items: [String] = []
    var events: [String] { lock.lock(); defer { lock.unlock() }; return items }
    func record(_ event: String) { lock.lock(); items.append(event); lock.unlock() }
}

/// `isActive()` answers from `answers` in order and then keeps repeating the last one.
private final class FakeApp: ActivatableApp, @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private let answers: [Bool]
    private var asked = 0
    private let log: Log

    init(log: Log, answers: [Bool]) {
        self.log = log
        self.answers = answers
    }

    var timesAsked: Int { lock.withLock { asked } }

    func activate() async { log.record("activate") }

    func isActive() async -> Bool {
        lock.withLock {
            defer { asked += 1 }
            return answers[min(asked, answers.count - 1)]
        }
    }
}

/// The app exists from the `appearsOnLookup`-th lookup on (never when `nil`) and is gone after `disappearsAfter` lookups.
private final class Lookup: @unchecked Sendable {  // every access holds `lock`
    private let lock = NSLock()
    private var count = 0
    private var pids: [Int32] = []
    private let appearsOnLookup: Int?
    private let disappearsAfter: Int?
    private let app: FakeApp

    init(app: FakeApp, appearsOnLookup: Int?, disappearsAfter: Int?) {
        self.app = app
        self.appearsOnLookup = appearsOnLookup
        self.disappearsAfter = disappearsAfter
    }

    var lookups: Int { lock.withLock { count } }
    var requestedPIDs: [Int32] { lock.withLock { pids } }

    func callAsFunction(_ pid: Int32) async -> (any ActivatableApp)? {
        lock.withLock {
            count += 1
            pids.append(pid)
            guard let appearsOnLookup, count >= appearsOnLookup else { return nil }
            if let disappearsAfter, count > disappearsAfter { return nil }
            return app
        }
    }
}

private let quarter = "sleep 0.25 seconds"
private let half = "sleep 0.5 seconds"
private let watchTicks = 40  // 20 s every 500 ms
private let openArguments = ["-b", "com.moonlight-stream.Moonlight"]

@Suite(.timeLimit(.minutes(1))) struct MoonlightWindowActivatorTests {
    private struct Rig {
        let activator: MoonlightWindowActivator
        let app: FakeApp
        let lookup: Lookup
        let runner: FakeCommandRunner
        let log: Log
        var opens: Int { runner.calls.filter { $0.executable == "/usr/bin/open" && $0.arguments == openArguments }.count }
    }

    /// `parkAtSleep`: the n-th (1-based) sleep never returns on its own and only ends when cancelled.
    /// `aliveForChecks`: the process counts as alive for that many `isAlive` checks and is gone afterwards (`nil`: always).
    private func rig(
        appearsOnLookup: Int? = 1, disappearsAfter: Int? = nil, answers: [Bool] = [true],
        open: Result<CommandResult, CommandError> = ok(), parkAtSleep: Int? = nil, aliveForChecks: Int? = nil
    ) -> Rig {
        let log = Log()
        let app = FakeApp(log: log, answers: answers)
        let lookup = Lookup(app: app, appearsOnLookup: appearsOnLookup, disappearsAfter: disappearsAfter)
        let runner = FakeCommandRunner()
        runner.when("open", openArguments, return: open)
        let sleeps = Log()
        let checks = Log()
        let activator = MoonlightWindowActivator(
            runner: runner, lookup: { await lookup($0) },
            isAlive: { _ in
                checks.record("")
                return aliveForChecks.map { checks.events.count <= $0 } ?? true
            },
            sleep: { duration in
                log.record("sleep \(duration)")
                sleeps.record("")
                if sleeps.events.count == parkAtSleep {
                    do { try await Task.sleep(for: .seconds(3600)) } catch {
                        log.record("parked sleep cancelled")
                        throw error
                    }
                }
            })
        return Rig(activator: activator, app: app, lookup: lookup, runner: runner, log: log)
    }

    // MARK: Getting to the front

    @Test func anAppThatIsThereAtOnceIsActivatedWithoutPollingAndNeedsNoOpen() async {
        let rig = rig()
        await rig.activator.activate(pid: 4321)
        #expect(rig.lookup.requestedPIDs.first == 4321)
        #expect(Array(rig.log.events.prefix(2)) == ["activate", quarter])  // activate, then the settle wait
        #expect(rig.opens == 0)
    }

    @Test func theAppIsPolledEveryQuarterSecondUntilItExistsAndThenActivated() async {
        let rig = rig(appearsOnLookup: 4)
        await rig.activator.activate(pid: 77)
        #expect(Array(rig.log.events.prefix(5)) == [quarter, quarter, quarter, "activate", quarter])
        #expect(rig.opens == 0)
    }

    // MARK: Waiting for Moonlight to register (Fix G3)
    // `diarchy-stream` runs `stream start` over ssh (~10 s) before it `exec`s Moonlight, so the pid exists long
    // before the app does.

    @Test func anAppThatRegistersThirteenSecondsLaterIsStillActivatedAndWatched() async {
        let rig = rig(appearsOnLookup: 53)  // 52 quarter-second waits = 13 s
        await rig.activator.activate(pid: 77)
        let events = rig.log.events
        #expect(Array(events.prefix(52)) == Array(repeating: quarter, count: 52))
        #expect(Array(events.dropFirst(52).prefix(2)) == ["activate", quarter])
        #expect(events.suffix(40) == Array(repeating: half, count: 40)[...])  // the 20 s watch starts at registration
        #expect(rig.opens == 0)
    }

    @Test func aRegistrationAfterTheStreamStartTimeoutOfThirtySecondsIsStillCaught() async {
        let rig = rig(appearsOnLookup: 121)  // 120 waits = 30 s
        await rig.activator.activate(pid: 77)
        #expect(rig.log.events.filter { $0 == "activate" }.count == 1)
    }

    @Test func anAppThatNeverRegistersIsGivenUpOnAfterSixtySecondsWithoutActivationOrOpen() async {
        let rig = rig(appearsOnLookup: nil)
        await rig.activator.activate(pid: 77)
        #expect(rig.lookup.lookups == 240)
        #expect(rig.log.events == Array(repeating: quarter, count: 239))  // 240 looks, 59.75 s asleep
        #expect(rig.opens == 0)
    }

    @Test func aProcessThatDiesBeforeRegisteringEndsTheWaitWithoutActivationOrOpen() async {
        let rig = rig(appearsOnLookup: nil, aliveForChecks: 3)  // alive at three checks, gone at the fourth
        await rig.activator.activate(pid: 77)
        #expect(rig.lookup.lookups == 4)
        #expect(rig.log.events == [quarter, quarter, quarter])
        #expect(rig.opens == 0)
    }

    @Test func aProcessThatRegistersExactlyAsItsLastCheckPassesIsStillActivated() async {
        let rig = rig(appearsOnLookup: 3, aliveForChecks: 2)
        await rig.activator.activate(pid: 77)
        #expect(rig.log.events.filter { $0 == "activate" }.count == 1)
    }

    @Test func cancellingDuringTheWaitActivatesAndOpensNothing() async throws {
        let rig = rig(appearsOnLookup: nil, parkAtSleep: 5)
        rig.activator.bringToFront(pid: 77)
        for _ in 0..<500 where rig.log.events.filter({ $0 == quarter }).count < 5 {
            try await Task.sleep(for: .milliseconds(10))
        }
        rig.activator.cancel()
        await rig.activator.waitUntilFinished()
        #expect(rig.log.events.last == "parked sleep cancelled")
        #expect(!rig.log.events.contains("activate"))
        #expect(rig.opens == 0)
    }

    @Test func anAppStillInactiveRightAfterActivationIsOpenedByBundle() async {
        let rig = rig(answers: [false, true])  // inactive at the settle check, active from then on
        await rig.activator.activate(pid: 77)
        #expect(Array(rig.log.events.prefix(2)) == ["activate", quarter])
        #expect(rig.runner.calls.map(\.executable) == ["/usr/bin/open"])
        #expect(rig.runner.calls.map(\.arguments) == [openArguments])
    }

    // MARK: Staying in front (Fix G2)

    @Test func anAppThatStaysActiveIsWatchedForTwentySecondsWithoutAnyExtraOpen() async {
        let rig = rig()
        await rig.activator.activate(pid: 77)
        #expect(rig.log.events == ["activate", quarter] + Array(repeating: half, count: watchTicks))
        #expect(rig.opens == 0)
        #expect(rig.app.timesAsked == 1 + watchTicks)  // the settle check and one per tick
    }

    @Test func anAppThatLosesTheFrontAfterConnectingIsOpenedAgain() async {
        // settle check active, ticks 1-2 active, tick 3 inactive (the session went fullscreen), then active again
        let rig = rig(answers: [true, true, true, false, true])
        await rig.activator.activate(pid: 77)
        #expect(rig.opens == 1)
        #expect(rig.log.events == ["activate", quarter] + Array(repeating: half, count: watchTicks))
    }

    @Test func aStubbornAppIsReopenedAtMostFourTimesAfterTheFirstCheck() async {
        let rig = rig(answers: [false])  // never active
        await rig.activator.activate(pid: 77)
        #expect(rig.opens == 1 + 4)  // the settle fallback, then four re-activations
        #expect(rig.log.events == ["activate", quarter] + Array(repeating: half, count: 5))  // the 5th tick finds the cap
    }

    @Test func aFailingOpenStillCountsTowardsTheCap() async {
        let rig = rig(answers: [false], open: .failure(.timedOut(command: "open")))
        await rig.activator.activate(pid: 77)
        #expect(rig.opens == 5)
    }

    @Test func watchingStopsAsSoonAsTheProcessIsGone() async {
        let rig = rig(disappearsAfter: 3, answers: [false])  // found, tick 1 and 2 see it, tick 3's lookup finds nothing
        await rig.activator.activate(pid: 77)
        #expect(rig.log.events == ["activate", quarter, half, half, half])
        #expect(rig.opens == 1 + 2)
    }

    // MARK: Cancellation

    /// Waits (bounded) until `condition` holds; the fakes run on other tasks.
    private func eventually(_ condition: () -> Bool) async throws {
        for _ in 0..<500 where !condition() { try await Task.sleep(for: .milliseconds(10)) }
    }

    @Test func cancellingTheTaskStopsTheWatcherWithoutFurtherOpens() async throws {
        let rig = rig(answers: [false], parkAtSleep: 3)  // parked in the 2nd tick's wait
        let task = Task { await rig.activator.activate(pid: 77) }
        try await eventually { rig.log.events.filter({ $0 == half }).count == 2 }
        #expect(rig.opens == 2)  // the settle fallback and the first tick's re-activation
        task.cancel()
        await task.value
        #expect(rig.log.events.last == "parked sleep cancelled")
        #expect(rig.opens == 2)
    }

    @Test func cancelStopsTheBackgroundWatcher() async throws {
        let rig = rig(answers: [false], parkAtSleep: 3)
        rig.activator.bringToFront(pid: 77)
        try await eventually { rig.log.events.filter({ $0 == half }).count == 2 }
        rig.activator.cancel()
        await rig.activator.waitUntilFinished()
        #expect(rig.log.events.last == "parked sleep cancelled")
        #expect(rig.opens == 2)
    }

    @Test func aNewBringToFrontCancelsThePreviousWatcher() async throws {
        let rig = rig(parkAtSleep: 3)  // the first watcher parks in its 2nd tick wait
        rig.activator.bringToFront(pid: 1)
        try await eventually { rig.log.events.filter({ $0 == half }).count == 2 }
        rig.activator.bringToFront(pid: 2)
        try await eventually { rig.log.events.contains("parked sleep cancelled") }
        await rig.activator.waitUntilFinished()
        #expect(rig.log.events.contains("parked sleep cancelled"))
        #expect(rig.lookup.requestedPIDs.contains(2))
    }

    @Test func aFailingOpenIsSwallowedAndBringToFrontReturnsAtOnce() async {
        let rig = rig(answers: [false], open: .failure(.timedOut(command: "open")))
        rig.activator.bringToFront(pid: 99)
        await rig.activator.waitUntilFinished()
        #expect(rig.lookup.requestedPIDs.first == 99)
        #expect(rig.opens == 5)
    }
}
