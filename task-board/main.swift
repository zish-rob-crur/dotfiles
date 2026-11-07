import AppKit
import WebKit

// Supervised by hammerspoon/task_board.lua over newline-delimited JSON pipes.
// The page and business actions stay in Lua; this process owns only the window.
final class BoardApp: NSObject, NSApplicationDelegate, NSWindowDelegate,
                      WKScriptMessageHandler, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var html = ""
    private var board: [String: Any]?
    private var pageReady = false

    func emit(_ event: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: event) else { return }
        FileHandle.standardOutput.write(data + Data([10]))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Also set the running app icon when launched directly by hs.task.
        if let iconURL = Bundle.main.url(forResource: "TaskBoard", withExtension: "icns"),
           let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Task Board", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        // Standard edit commands are needed by the manually entered focus task.
        let editItem = NSMenuItem()
        menu.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        let windowItem = NSMenuItem()
        menu.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowItem.submenu = windowMenu
        NSApp.mainMenu = menu
        NSApp.windowsMenu = windowMenu

        // readLine buffers partial UTF-8/JSON records. EOF means the supervisor
        // exited or reloaded: do not leave an orphan window behind.
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            while let line = readLine() {
                DispatchQueue.main.async { self?.receive(line) }
            }
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
        emit(["event": "started"])
    }

    private func receive(_ line: String) {
        defer { emit(["event": "ack"]) }
        guard let data = line.data(using: .utf8),
              let message = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let command = message["command"] as? String else {
            emit(["event": "error", "message": "Invalid command"])
            return
        }
        switch command {
        case "load":
            guard window == nil, let page = message["html"] as? String,
                  let frame = message["frame"] as? [String: Double],
                  let x = frame["x"], let y = frame["y"],
                  let w = frame["w"], let h = frame["h"],
                  [x, y, w, h].allSatisfy({ $0.isFinite }), w >= 200, h >= 100 else { return }
            html = page
            buildWindow(frame: NSRect(x: x, y: desktopTop - y - h, width: w, height: h))
        case "update":
            board = message["board"] as? [String: Any]
            render()
        case "toggle":
            guard window != nil else { return }
            if window.isVisible && !window.isMiniaturized && !NSApp.isHidden {
                window.orderOut(nil)
            } else {
                showWindow()
            }
            reportFrame()
        case "inspect": inspect()
        default: emit(["event": "error", "message": "Unknown command: \(command)"])
        }
    }

    // Hammerspoon uses top-left coordinates relative to the primary display.
    private var desktopTop: CGFloat { NSScreen.screens.first?.frame.maxY ?? 0 }

    private func buildWindow(frame: NSRect) {
        window = NSWindow(contentRect: frame,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.setFrame(frame, display: false)
        window.title = "Task Board"
        window.isReleasedWhenClosed = false
        window.level = .normal
        window.collectionBehavior = [.canJoinAllSpaces]
        window.minSize = NSSize(width: 240, height: 180)
        window.delegate = self

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "board")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        window.contentView = webView
        webView.loadHTMLString(html, baseURL: nil)
        // Startup must not steal the active app's focus. Explicit toggles do.
        window.orderFront(nil)
        reportFrame()
    }

    private func showWindow() {
        if window.isMiniaturized { window.deminiaturize(nil) }
        NSApp.unhide(nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if window != nil { showWindow() }
        return true
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        reportFrame()
        return false
    }

    func windowDidMove(_ notification: Notification) { reportFrame() }
    func windowDidResize(_ notification: Notification) { reportFrame() }
    func applicationWillTerminate(_ notification: Notification) { reportFrame() }

    private func reportFrame() {
        guard let window = window else { return }
        let f = window.frame
        emit(["event": "frame", "frame": ["x": f.minX, "y": desktopTop - f.maxY,
                                                "w": f.width, "h": f.height]])
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let body = message.body as? [String: Any] else { return }
        if body["ready"] as? Bool == true {
            pageReady = true
            render()
        }
        emit(["event": "message", "body": body])
    }

    private func render() {
        guard pageReady, let board = board,
              let data = try? JSONSerialization.data(withJSONObject: board),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.update(\(json)); true") { [weak self] _, error in
            if let error = error {
                self?.emit(["event": "error", "message": error.localizedDescription])
            } else {
                self?.emit(["event": "rendered"])
            }
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        pageReady = false
        webView.loadHTMLString(html, baseURL: nil)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // Only the supplied HTML runs in this privileged message bridge.
        if navigationAction.request.url?.absoluteString == "about:blank" {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)
        }
    }

    private func inspect() {
        guard let window = window else { return }
        var status: [String: Any] = ["event": "status", "windowID": window.windowNumber,
            "class": String(describing: type(of: window)), "level": window.level.rawValue,
            "subrole": window.accessibilitySubrole() ?? "", "visible": window.isVisible,
            "minimized": window.isMiniaturized, "pageReady": pageReady]
        webView.evaluateJavaScript("({cards: document.querySelectorAll('.card').length, todos: document.querySelectorAll('.todo').length, hasUpdate: typeof window.update === 'function'})") { [weak self] result, error in
            status["page"] = result
            if let error = error { status["error"] = error.localizedDescription }
            self?.emit(status)
        }
    }
}

// A regular application + an actual NSWindow exposes AXStandardWindow to
// window switchers. NSPanel styling alone cannot provide that contract.
signal(SIGPIPE, SIG_IGN)
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = BoardApp()
app.delegate = delegate
app.run()
