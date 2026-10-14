import Testing

@testable import DiarchyCore

@Suite struct SettingsTabChoiceTests {
    @Test func diagnosticsAlwaysOpensTheDiagnosticsTab() {
        for current in [SettingsTab.connection, .screens, .streaming, .diagnostics] {
            for windowOpen in [false, true] {
                for hasConfig in [false, true] {
                    let tab = SettingsTabChoice.tab(
                        for: .diagnostics, current: current, windowOpen: windowOpen, hasConfig: hasConfig)
                    #expect(tab == .diagnostics)
                }
            }
        }
    }

    @Test func settingsKeepsTheCurrentTabWhileTheWindowIsOpen() {
        for current in [SettingsTab.connection, .screens, .streaming, .diagnostics] {
            let tab = SettingsTabChoice.tab(for: .settings, current: current, windowOpen: true, hasConfig: true)
            #expect(tab == current)
        }
    }

    @Test func settingsOpensOnConnectionWhenTheWindowIsClosed() {
        for current in [SettingsTab.connection, .screens, .streaming, .diagnostics] {
            let tab = SettingsTabChoice.tab(for: .settings, current: current, windowOpen: false, hasConfig: true)
            #expect(tab == .connection)
        }
    }

    @Test func settingsWithoutAConfigurationIsAlwaysConnection() {
        for current in [SettingsTab.connection, .screens, .streaming, .diagnostics] {
            for windowOpen in [false, true] {
                let tab = SettingsTabChoice.tab(
                    for: .settings, current: current, windowOpen: windowOpen, hasConfig: false)
                #expect(tab == .connection)
            }
        }
    }
}
