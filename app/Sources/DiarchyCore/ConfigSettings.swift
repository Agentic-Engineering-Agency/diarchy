import Foundation

extension DiarchyConfig {
    /// The configuration after the Settings window saved `draft`: the draft wins on every field the window edits
    /// (user, hosts, SSH port and identity, KVM side, stream resolution, FPS and overlay). `hostFingerprint` (written
    /// by pairing) and `hostKeyAlias` (not editable) stay as they are on the receiver, so saving never forgets them.
    public func applyingSettings(from draft: DiarchyConfig) -> DiarchyConfig {
        var merged = self
        merged.sshUser = draft.sshUser
        merged.lanHost = draft.lanHost
        merged.tailscaleHost = draft.tailscaleHost
        merged.sshPort = draft.sshPort
        merged.sshIdentityFile = draft.sshIdentityFile
        merged.kvmSide = draft.kvmSide
        merged.streamResolution = draft.streamResolution
        merged.streamFPS = draft.streamFPS
        merged.streamOverlay = draft.streamOverlay
        merged.streamOutput = draft.streamOutput
        return merged
    }
}

extension ConfigStore {
    /// Applies `edit` to the configuration on disk and saves it (validated before anything is written), so fields the
    /// caller does not touch survive. Returns exactly what a reload would give.
    ///
    /// The starting point is `fallback` when there is no file, and also when the file exists but its content is not a
    /// valid configuration: the Settings window is the repair path for a broken file, so the draft deliberately
    /// replaces it. A file that cannot be read at all (permissions, I/O) is NOT replaced; that is an error.
    @discardableResult
    public func update(
        fallback: DiarchyConfig, _ edit: (inout DiarchyConfig) -> Void
    ) throws(ConfigError) -> DiarchyConfig {
        var config: DiarchyConfig
        do {
            config = try load() ?? fallback
        } catch let error as ConfigError {
            guard !error.reason.hasPrefix(Self.unreadablePrefix) else { throw error }
            config = fallback
        } catch {
            throw ConfigError(reason: "\(Self.unreadablePrefix): \(url.path)")
        }
        edit(&config)
        // The file always holds the uppercase fingerprint (`ConfigCodec.render`); return that same form.
        config.hostFingerprint = config.hostFingerprint.uppercased()
        do {
            try save(config)
        } catch let error as ConfigError {
            throw error
        } catch {
            throw ConfigError(reason: "cannot write configuration file: \(url.path)")
        }
        return config
    }

    /// Saves what the Settings window holds on top of the file (see `DiarchyConfig.applyingSettings(from:)`).
    @discardableResult
    public func saveSettings(_ draft: DiarchyConfig) throws(ConfigError) -> DiarchyConfig {
        try update(fallback: draft) { $0 = $0.applyingSettings(from: draft) }
    }

    /// First words of the `ConfigError` `load()` throws when the file exists but cannot be read.
    private static let unreadablePrefix = "cannot read configuration file"
}
