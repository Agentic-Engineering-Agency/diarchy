import Foundation

/// Runs `body` with a fresh, empty temporary directory and removes it afterwards.
func withTempDir<T>(_ body: (URL) throws -> T) throws -> T {
    let dir = FileManager.default.temporaryDirectory
        .appendingPathComponent("diarchy-core-tests-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: dir) }
    return try body(dir)
}

/// Async variant of `withTempDir`.
func withTempDirAsync<T>(_ body: (URL) async throws -> T) async throws -> T {
    let dir = FileManager.default.temporaryDirectory
        .appendingPathComponent("diarchy-core-tests-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: dir) }
    return try await body(dir)
}
