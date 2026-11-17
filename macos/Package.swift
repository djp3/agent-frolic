// swift-tools-version:5.9
// Copyright (C) 2026 Don Patterson
// SPDX-License-Identifier: GPL-3.0-or-later
// The native shell of agent-frolic: a window hosting a SwiftTerm terminal that runs the
// PyInstaller-built Python TUI (agent-frolic-core), plus Sparkle for updates.
// scripts/build_app.sh builds this and assembles dist/agent-frolic.app around it.
import PackageDescription

let package = Package(
    name: "AgentFrolic",
    // The same as LSMinimumSystemVersion in Info.plist. It follows the libraries bundled with
    // the Python side (Homebrew builds them for the macOS they were built on);
    // scripts/check_macos_minimum.sh stops the build when anything in the app needs more.
    platforms: [.macOS("27.0")],
    dependencies: [
        .package(url: "https://github.com/migueldeicaza/SwiftTerm.git", from: "1.99.3"),
        .package(url: "https://github.com/sparkle-project/Sparkle.git", from: "2.10.1"),
    ],
    targets: [
        .executableTarget(
            name: "AgentFrolic",
            dependencies: [
                .product(name: "SwiftTerm", package: "SwiftTerm"),
                .product(name: "Sparkle", package: "Sparkle"),
            ],
            path: "Sources/AgentFrolic",
            linkerSettings: [
                // Sparkle.framework is copied into agent-frolic.app/Contents/Frameworks by build_app.sh.
                .unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"]),
            ]
        ),
    ]
)
