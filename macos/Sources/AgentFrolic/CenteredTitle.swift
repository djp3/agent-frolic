// Copyright (C) 2026 Don Patterson
// SPDX-License-Identifier: GPL-3.0-or-later
import AppKit

/// A label that is not there for the mouse: a press or a drag on it goes to the title bar
/// beneath, so the window is moved, and zoomed by a double click, there as anywhere on the bar.
private final class TitleLabel: NSTextField {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

/// The window's title, in the middle of its title bar.
///
/// macOS puts a window's title at the leading edge of the bar, beside the buttons, and has
/// no setting for where it goes. So the system's own title is hidden, and this label stands
/// in for it: the title bar's font and colors, centered in the bar, dimmed while the window
/// is not the one in use, cut short with an ellipsis before it would reach the buttons.
///
/// The window keeps its title all the same. That is what the Dock, Mission Control, the
/// Window menu and VoiceOver go by; the label only draws it.
final class CenteredTitle {
    private let label = TitleLabel(labelWithString: "")
    private weak var window: NSWindow?
    private var watching: [NSObjectProtocol] = []

    init(in window: NSWindow) {
        self.window = window
        // The bar is where the buttons are. Without it (no such window is made here) the
        // system's title stays where it is.
        guard let bar = window.standardWindowButton(.closeButton)?.superview else { return }
        window.titleVisibility = .hidden
        label.font = .titleBarFont(ofSize: 0)
        label.alignment = .center
        label.lineBreakMode = .byTruncatingTail
        label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        label.setAccessibilityElement(false)          // VoiceOver has the window's own title
        label.translatesAutoresizingMaskIntoConstraints = false
        bar.addSubview(label)
        var held = [label.centerXAnchor.constraint(equalTo: bar.centerXAnchor),
                    label.centerYAnchor.constraint(equalTo: bar.centerYAnchor)]
        // In the middle, so what keeps it off the buttons on one side keeps it as far from
        // the other edge. Which side the buttons are on is the title bar's to say, not the
        // app's: on a Mac set to a right-to-left language they are on the right, in an app
        // like this one that is laid out left to right all the same. So the side is asked
        // of the window and said in left and right, which do not turn with the app.
        if let last = window.standardWindowButton(.zoomButton) {
            held.append(window.windowTitlebarLayoutDirection == .rightToLeft
                ? label.rightAnchor.constraint(lessThanOrEqualTo: last.leftAnchor, constant: -8)
                : label.leftAnchor.constraint(greaterThanOrEqualTo: last.rightAnchor, constant: 8))
        }
        NSLayoutConstraint.activate(held)

        let center = NotificationCenter.default
        for name in [NSWindow.didBecomeKeyNotification, NSWindow.didResignKeyNotification,
                     NSWindow.didBecomeMainNotification, NSWindow.didResignMainNotification] {
            watching.append(center.addObserver(forName: name, object: window, queue: .main) { [weak self] _ in
                self?.tint()
            })
        }
        tint()
    }

    deinit { watching.forEach(NotificationCenter.default.removeObserver) }

    /// The window's title, and what the label draws.
    func set(_ title: String) {
        window?.title = title
        label.stringValue = title
    }

    /// As the system's title is: full strength in the window in use, dimmed in the others.
    private func tint() {
        let inUse = window.map { $0.isKeyWindow || $0.isMainWindow } ?? false
        label.textColor = inUse ? .windowFrameTextColor : .tertiaryLabelColor
    }
}
