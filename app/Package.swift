// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Diarchy",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "DiarchyCore", targets: ["DiarchyCore"]),
        .executable(name: "Diarchy", targets: ["Diarchy"]),
    ],
    dependencies: [
        .package(url: "https://github.com/orchetect/MenuBarExtraAccess", exact: "1.3.1"),
    ],
    targets: [
        .target(name: "DiarchyCore"),
        .executableTarget(
            name: "Diarchy",
            dependencies: [
                "DiarchyCore",
                .product(name: "MenuBarExtraAccess", package: "MenuBarExtraAccess"),
            ]),
        .testTarget(name: "DiarchyCoreTests", dependencies: ["DiarchyCore"]),
    ]
)
