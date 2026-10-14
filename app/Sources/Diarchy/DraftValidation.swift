import DiarchyCore

extension DiarchyConfig {
    /// Why the connection fields (user, hosts, port, identity file) cannot be saved, from Core's validator; `nil`
    /// when they are valid. The streaming fields belong to the Streaming tab, so they are reset to their defaults on
    /// a copy first: a half-typed resolution there must not block, or be reported in, Connection. (Saving always
    /// writes the whole shared draft, and the model's save validates all of it.)
    var connectionFieldsError: String? {
        var copy = self
        let defaults = DiarchyConfig(sshUser: "")
        copy.streamResolution = defaults.streamResolution
        copy.streamFPS = defaults.streamFPS
        copy.streamOverlay = defaults.streamOverlay
        do {
            try copy.validate()
            return nil
        } catch {
            return error.reason
        }
    }
}
