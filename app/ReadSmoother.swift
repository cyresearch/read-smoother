// Read Smoother as a Mac app: one window, one Dock icon, no browser needed.
// It starts the local server if it is not running (and stops it again on quit), then shows the shelf in a WebKit view.
// Build with app/build-app.sh, which also writes the project folder and the app name into Info.plist.

import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKNavigationDelegate {
    let info = Bundle.main.infoDictionary ?? [:]
    lazy var projectDir = (info["RSProjectPath"] as? String) ?? (NSHomeDirectory() + "/projects/read-smoother")
    lazy var port = (info["RSPort"] as? Int) ?? 8765
    lazy var appName = (info["CFBundleName"] as? String) ?? "Read Smoother"
    lazy var base = URL(string: "http://127.0.0.1:\(port)/")!
    let zh = Locale.preferredLanguages.first?.hasPrefix("zh") ?? false
    var startedServer = false
    var windows: [NSWindow] = []
    var titleWatchers: [NSKeyValueObservation] = []
    var importFile: String { projectDir + "/data/localstorage-import.json" }

    lazy var webConfig: WKWebViewConfiguration = {
        let c = WKWebViewConfiguration()
        c.websiteDataStore = .default()
        c.mediaTypesRequiringUserActionForPlayback = []          // the reader starts audio from code
        c.preferences.javaScriptCanOpenWindowsAutomatically = true
        c.preferences.setValue(true, forKey: "developerExtrasEnabled")   // right-click > Inspect Element
        // Settings carried over from a browser (data/localstorage-import.json): copied once, never over existing keys.
        if let data = FileManager.default.contents(atPath: importFile),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: String],
           let json = try? JSONSerialization.data(withJSONObject: obj), let lit = String(data: json, encoding: .utf8) {
            let js = """
            (function () { try {
              if (location.host !== "127.0.0.1:\(port)" || localStorage.getItem("__imported")) return;
              var d = \(lit);
              for (var k in d) if (localStorage.getItem(k) === null) localStorage.setItem(k, d[k]);
              localStorage.setItem("__imported", "1");
            } catch (e) {} })();
            """
            c.userContentController.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        return c
    }()

    // MARK: launch and quit

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        let wv = newWindow(configuration: webConfig)
        wv.loadHTMLString(page(zh ? "正在启动…" : "Starting…", ""), baseURL: nil)
        DispatchQueue.global().async {
            if !self.serverUp() { self.startServer() }
            var up = false
            for _ in 0..<60 { if self.serverUp() { up = true; break }; Thread.sleep(forTimeInterval: 0.5) }
            DispatchQueue.main.async {
                if up { wv.load(URLRequest(url: self.base)) } else { self.showError(in: wv) }
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        guard startedServer else { return }   // a server someone else started keeps running
        var r = URLRequest(url: base.appendingPathComponent("api/quit"), timeoutInterval: 2)
        r.httpMethod = "POST"; r.httpBody = Data("{}".utf8); r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let done = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: r) { _, _, _ in done.signal() }.resume()
        _ = done.wait(timeout: .now() + 2)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // Clicking the Dock icon brings back a minimised window.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            if let w = windows.first(where: { $0.isMiniaturized }) { w.deminiaturize(nil); w.makeKeyAndOrderFront(nil) }
            else { newWindow(configuration: webConfig).load(URLRequest(url: base)) }
        }
        return true
    }

    func serverUp() -> Bool {
        var ok = false
        let done = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: URLRequest(url: base.appendingPathComponent("api/library"), timeoutInterval: 1)) { _, resp, _ in
            ok = (resp as? HTTPURLResponse)?.statusCode == 200; done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 2)
        return ok
    }

    func startServer() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "exec ./start.sh --no-browser >> server.log 2>&1 < /dev/null"]
        p.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        var env = ProcessInfo.processInfo.environment   // apps start with a short PATH; add where Homebrew and Claude Code live
        env["PATH"] = "\(NSHomeDirectory())/.local/bin:/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        p.environment = env
        do { try p.run(); startedServer = true } catch { NSLog("could not start the server: \(error)") }
    }

    func page(_ title: String, _ body: String) -> String {
        "<html><body style=\"font:15px -apple-system,sans-serif;color:#5a534a;background:#faf7f0;display:flex;height:90vh;align-items:center;justify-content:center;text-align:center\"><div><h3>\(title)</h3>\(body)</div></body></html>"
    }

    func showError(in wv: WKWebView) {
        let hint = zh ? "<p>请在终端里运行 <code>\(projectDir)/start.sh</code> 看看报错。</p><p><a href=\"\(base)\">重试</a></p>"
                      : "<p>Run <code>\(projectDir)/start.sh</code> in a terminal to see the error.</p><p><a href=\"\(base)\">Try again</a></p>"
        wv.loadHTMLString(page(zh ? "\(appName)没能启动" : "\(appName) did not start", hint), baseURL: nil)
    }

    // MARK: windows

    @discardableResult
    func newWindow(configuration: WKWebViewConfiguration) -> WKWebView {
        let wv = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 860), configuration: configuration)
        wv.uiDelegate = self
        wv.navigationDelegate = self
        if #available(macOS 13.3, *) { wv.isInspectable = true }
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        w.isReleasedWhenClosed = false
        w.delegate = self
        w.title = appName
        w.contentView = wv
        w.minSize = NSSize(width: 520, height: 400)
        if windows.isEmpty {
            if !w.setFrameUsingName("MainWindow") { w.center() }
            w.setFrameAutosaveName("MainWindow")
        } else if let last = windows.last {
            w.setFrame(last.frame, display: false)
            w.setFrameTopLeftPoint(w.cascadeTopLeft(from: NSPoint(x: last.frame.minX, y: last.frame.maxY)))
        }
        titleWatchers.append(wv.observe(\.title) { [weak w, appName] v, _ in
            if let t = v.title, !t.isEmpty { w?.title = t } else { w?.title = appName }
        })
        windows.append(w)
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        return wv
    }

    func windowWillClose(_ notification: Notification) {
        if let w = notification.object as? NSWindow { windows.removeAll { $0 === w } }
    }

    var currentWebView: WKWebView? { (NSApp.keyWindow ?? windows.first)?.contentView as? WKWebView }

    // MARK: links: the reader's own pages stay here, everything else goes to the system

    func isLocal(_ url: URL) -> Bool {
        (url.host == "127.0.0.1" || url.host == "localhost") && url.port == port
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, let scheme = url.scheme else { return decisionHandler(.allow) }
        if ["about", "blob", "data"].contains(scheme) || isLocal(url) || !(action.targetFrame?.isMainFrame ?? true) {
            return decisionHandler(.allow)
        }
        NSWorkspace.shared.open(url)   // zotero://, obsidian://, web links
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { showError(in: webView) }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, !isLocal(url) { NSWorkspace.shared.open(url); return nil }
        return newWindow(configuration: configuration)   // WebKit loads the page into it
    }

    func webViewDidClose(_ webView: WKWebView) { webView.window?.close() }

    // MARK: alert / confirm / prompt / file picker

    func alert(_ message: String, cancel: Bool) -> NSAlert {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: zh ? "好" : "OK")
        if cancel { a.addButton(withTitle: zh ? "取消" : "Cancel") }
        return a
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        _ = alert(message, cancel: false).runModal(); completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        completionHandler(alert(message, cancel: true).runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let a = alert(prompt, cancel: true)
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        field.stringValue = defaultText ?? ""
        a.accessoryView = field
        completionHandler(a.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let p = NSOpenPanel()
        p.allowsMultipleSelection = parameters.allowsMultipleSelection
        p.canChooseDirectories = parameters.allowsDirectories
        completionHandler(p.runModal() == .OK ? p.urls : nil)
    }

    // MARK: menus (the Edit menu is what makes copy and paste work in the page)

    @objc func reloadPage(_ sender: Any?) { currentWebView?.reload() }
    @objc func zoomIn(_ sender: Any?) { if let v = currentWebView { v.pageZoom = min(v.pageZoom + 0.1, 3) } }
    @objc func zoomOut(_ sender: Any?) { if let v = currentWebView { v.pageZoom = max(v.pageZoom - 0.1, 0.5) } }
    @objc func zoomReset(_ sender: Any?) { currentWebView?.pageZoom = 1 }
    @objc func showShelf(_ sender: Any?) {
        if let v = currentWebView { v.load(URLRequest(url: base)) } else { newWindow(configuration: webConfig).load(URLRequest(url: base)) }
    }

    func buildMenu() {
        func item(_ title: String, _ action: Selector?, _ key: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: title, action: action, keyEquivalent: key)
            i.keyEquivalentModifierMask = mods
            return i
        }
        func menu(_ title: String, _ items: [NSMenuItem]) -> NSMenuItem {
            let m = NSMenu(title: title); items.forEach { m.addItem($0) }
            let top = NSMenuItem(); top.submenu = m; return top
        }
        let main = NSMenu()
        main.addItem(menu(appName, [
            item(zh ? "关于\(appName)" : "About \(appName)", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""),
            .separator(),
            item(zh ? "隐藏\(appName)" : "Hide \(appName)", #selector(NSApplication.hide(_:)), "h"),
            item(zh ? "隐藏其他" : "Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            item(zh ? "全部显示" : "Show All", #selector(NSApplication.unhideAllApplications(_:)), ""),
            .separator(),
            item(zh ? "退出\(appName)" : "Quit \(appName)", #selector(NSApplication.terminate(_:)), "q"),
        ]))
        main.addItem(menu(zh ? "编辑" : "Edit", [
            item(zh ? "撤销" : "Undo", Selector(("undo:")), "z"),
            item(zh ? "重做" : "Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item(zh ? "剪切" : "Cut", #selector(NSText.cut(_:)), "x"),
            item(zh ? "拷贝" : "Copy", #selector(NSText.copy(_:)), "c"),
            item(zh ? "粘贴" : "Paste", #selector(NSText.paste(_:)), "v"),
            item(zh ? "全选" : "Select All", #selector(NSText.selectAll(_:)), "a"),
        ]))
        main.addItem(menu(zh ? "显示" : "View", [
            item(zh ? "书架" : "Shelf", #selector(showShelf(_:)), "l", [.command, .shift]),
            item(zh ? "重新载入" : "Reload", #selector(reloadPage(_:)), "r"),
            .separator(),
            item(zh ? "实际大小" : "Actual Size", #selector(zoomReset(_:)), "0"),
            item(zh ? "放大" : "Zoom In", #selector(zoomIn(_:)), "="),
            item(zh ? "缩小" : "Zoom Out", #selector(zoomOut(_:)), "-"),
            .separator(),
            item(zh ? "进入全屏幕" : "Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ]))
        let windowMenu = menu(zh ? "窗口" : "Window", [
            item(zh ? "最小化" : "Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item(zh ? "缩放" : "Zoom", #selector(NSWindow.performZoom(_:)), ""),
            item(zh ? "关闭窗口" : "Close Window", #selector(NSWindow.performClose(_:)), "w"),
            .separator(),
            item(zh ? "前置全部窗口" : "Bring All to Front", #selector(NSApplication.arrangeInFront(_:)), ""),
        ])
        main.addItem(windowMenu)
        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu.submenu
    }
}

#if RS_TESTHOOK
// Test build only (swiftc -D RS_TESTHOOK): after each page load, run the JS in $RS_TEST_JS and append the result,
// the window count and a snapshot to $RS_TEST_OUT.
extension AppDelegate {
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let env = ProcessInfo.processInfo.environment
        guard let jsPath = env["RS_TEST_JS"], let out = env["RS_TEST_OUT"], let js = try? String(contentsOfFile: jsPath, encoding: .utf8) else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
            webView.callAsyncJavaScript(js, arguments: [:], in: nil, in: .page) { result in
                let text: String
                switch result { case .success(let v): text = "\(v ?? "nil")"; case .failure(let e): text = "JS ERROR: \(e)" }
                let line = "[\(webView.url?.absoluteString ?? "?")] windows=\(self.windows.count) :: \(text)\n"
                if let h = FileHandle(forWritingAtPath: out) { h.seekToEndOfFile(); h.write(Data(line.utf8)); h.closeFile() }
                else { FileManager.default.createFile(atPath: out, contents: Data(line.utf8)) }
                webView.takeSnapshot(with: nil) { img, _ in
                    guard let img, let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                          let png = rep.representation(using: .png, properties: [:]) else { return }
                    let name = (webView.url?.path ?? "page").replacingOccurrences(of: "/", with: "_")
                    try? png.write(to: URL(fileURLWithPath: out + name + ".png"))
                }
            }
        }
    }
}
#endif

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
