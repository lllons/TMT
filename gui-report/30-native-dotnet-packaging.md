# Native Windows / .NET front ends + Python engine + packaging (research, 2026-10-01)

Status: FINAL (second pass done). Star counts: GitHub REST API on 2026-10-01 (https://api.github.com/repos/<owner>/<repo>). NuGet figures: nuget.org package pages fetched 2026-10-01. Items marked (UNVERIFIED) or (ESTIMATE) were not confirmed by a second source or are my judgement.

## TL;DR
0. Signing (NZ): Artifact Signing is open to NZ organisations but NOT NZ individuals; TMT is Apache-2.0 and public, so SignPath Foundation (free) is the fallback; Certum Open Source cloud (about $58/yr) is the cheapest paid one. See Part 3.
1. Keep Python as a child process speaking JSON-RPC 2.0 over stdio (StreamJsonRpc on the .NET side). Do not embed Python in-process.
2. Front end: **WPF + WPF-UI** is the lowest-risk pick; **Avalonia 12** is the strongest alternative; WinUI 3 has the best native look and the best 2026 momentum but still the weakest tooling and the most deployment friction.
3. Prefer Velopack over MSIX: MSIX virtualizes %APPDATA% writes for the app and all its child processes (an agent that runs pip/npm/git would be affected; see Part 5).
4. Packaging: PyInstaller onedir engine + self-contained .NET publish -> Azure Artifact Signing -> Velopack -> GitHub Releases + winget (via winget-releaser/Komac).

---

## Part 1 - UI frameworks

| Framework | Language | License | Popularity | Win11 look | Markdown / diff options | Verdict |
|---|---|---|---|---|---|---|
| WinUI 3 / Windows App SDK ("WinUI" since Build 2026) | C# + XAML | MIT | microsoft-ui-xaml 8,484 stars, 2,422 open issues (https://api.github.com/repos/microsoft/microsoft-ui-xaml) | Best: native Fluent, Mica/Acrylic | CommunityToolkit MarkdownTextBlock (Markdig-based, Labs repo 483 stars, https://api.github.com/repos/CommunityToolkit/Labs-Windows); WebView2+Monaco | Best look, Microsoft's declared future, weakest tooling; unpackaged is "niche" |
| WPF + Fluent theme | C# + XAML | MIT | Built into .NET (no star count meaningful) | .NET 9 added Fluent theme via `ThemeMode`, system accent colour; still flagged "experimental" in the .NET 9 docs (https://learn.microsoft.com/en-us/dotnet/desktop/wpf/whats-new/net90). Mica/backdrop was a .NET 10 plan item (https://github.com/dotnet/wpf/discussions/10387); the shipped .NET 10 notes only list more Fluent control styles (DatePicker, GroupBox, RichTextBox, TextBox...) and say "Fluent UI style support is still in progress", with no mention of Mica (https://learn.microsoft.com/en-us/dotnet/desktop/wpf/whats-new/net100), so use WPF-UI for Mica | Markdig + custom renderer; MdXaml (338 stars, last push 2026-03-08, https://api.github.com/repos/whistyun/MdXaml); WebView2+Monaco | Mature, fully supported through .NET 11 per Microsoft (https://developer.mescius.com/blogs/microsoft-recommitted-winui-what-build-2026-means-for-windows-developers) |
| WPF-UI (lepoco) | C# | MIT | 9,672 stars, last push 2026-06-27 (https://api.github.com/repos/lepoco/wpfui); NuGet 1.2M total, v4.3.0 updated 2026-05-04 (https://www.nuget.org/packages/WPF-UI) | Good: Fluent controls, Mica, NavigationView, dark mode | n/a (use with Markdig) | Best way to make WPF look Win11 |
| WinForms | C# | MIT | n/a | Dated | WebView2 only | Not recommended |
| Avalonia UI 12 | C# + XAML | MIT | 31,607 stars, pushed 2026-09-30 (https://api.github.com/repos/AvaloniaUI/Avalonia); NuGet 24.4M total, v12.1.3 (2026-09-22), ~133k/day (https://www.nuget.org/packages/Avalonia) | Good via Fluent theme but self-drawn, not native controls | Markdown.Avalonia (419 stars, last push 2026-07-13, https://api.github.com/repos/whistyun/Markdown.Avalonia); AvaloniaEdit; WebView now open-source in v12 (https://avaloniaui.net/blog/the-avalonia-webview-is-going-open-source) | Strongest community; best if cross-platform could matter later |
| Uno Platform | C# + WinUI XAML | Apache-2.0 | 10,063 stars (https://api.github.com/repos/unoplatform/uno) | Native WinUI on Windows | as WinUI | Overkill for Windows-only |
| .NET MAUI | C# + XAML | MIT | 23,322 stars (https://api.github.com/repos/dotnet/maui) | WinUI 3 under the hood | Weak desktop control set | Mobile-first; poor fit for dense desktop chat app |
| Flutter desktop + serious_python | Dart | BSD-3 | Flutter 179,164 stars; serious_python 325 stars (https://api.github.com/repos/flet-dev/serious-python) | Self-drawn, not native | flutter_markdown etc. | Second language and a tiny embedding ecosystem; you would still use a subprocess; no |
| Qt/QML (C++) | C++/QML | LGPL / commercial | stars not collected | FluentWinUI3 style since Qt 6.8, some controls fall back to Fusion (https://doc.qt.io/qt-6/qtquickcontrols-fluentwinui3.html) | QTextDocument markdown / WebEngine | Heavy toolchain, LGPL duties; no |
| Rust: Slint / egui / iced | Rust | Slint GPLv3 / royalty-free / commercial; egui Apache-2.0; iced MIT | Slint 24,029; egui 30,774; iced 31,632 | Not native | Weak rich text | No |

### WinUI status check (two sources each)
- Commitment: Build 2026 positioned WinUI as the "primary framework for modern Windows applications", dropped the "3" in branding, no replacement planned (https://www.windowscentral.com/software-apps/microsoft-drives-developers-toward-native-windows-apps-at-build-2026; https://developer.mescius.com/blogs/microsoft-recommitted-winui-what-build-2026-means-for-windows-developers).
- Open source (now confirmed by several sources): Microsoft ran a 4-phase move of WinUI to open development (Microsoft's own tracking thread: https://github.com/microsoft/microsoft-ui-xaml/discussions/10700); phase 3 finished May 2026 and mainline development is now public on GitHub (https://www.neowin.net/news/windows-11s-native-framework-winui-is-now-truly-open-source/; https://www.opensourceforu.com/2026/08/microsofts-winui-becomes-fully-open-source/). Caveat: pull requests are still mostly from Microsoft staff while the process is tested; outside contributions are a later milestone (https://xenospectrum.com/en/winui-mainline-development-github/).
- Performance concerns: Microsoft claimed a 25% speedup for the WinUI parts of File Explorer, opt-in because it breaks things; critics say WinUI 3 is "measurably slower than WPF and UWP" and each control action crosses WinRT interop (https://www.theregister.com/oses/2026/05/13/microsoft-aims-to-speed-windows-with-leap-forward-in-winui-3-perf/5239559).
- Trust: a GitHub discussion titled "WinUI3 is really dead!" exists (https://github.com/microsoft/microsoft-ui-xaml/discussions/9417) (date not checked, may be old).
- Tooling complaints: no Visual Studio drag-and-drop XAML designer for WinUI 3 (https://learn.microsoft.com/en-us/windows/apps/develop/ui/xaml-runtime-design-tools); Hot Reload breakage reports (https://github.com/microsoft/microsoft-ui-xaml/issues/7043, /7636, /7687, mostly older issues, 2022-era); a Q&A report of an app crashing at launch after packaging with .NET 9/10 (https://learn.microsoft.com/en-us/answers/questions/5757976/winui3-app-crashing-at-launch). Build 2026 response: `dotnet new winui-desktop`, a VS Code "WinApp" extension, an agent plugin for Copilot and Claude Code.
- Unpackaged: supported, but Microsoft says it means no auto-update, no package identity, you bundle the Windows App SDK runtime or go self-contained; single-file EXE only for unpackaged self-contained (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/unpackage-winui-app and https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/choose-distribution-path).

### Head-to-head for THIS app (WPF+WPF-UI vs WinUI 3 vs Avalonia)

| Concern | WPF + WPF-UI | WinUI 3 | Avalonia 12 |
|---|---|---|---|
| Streaming markdown | No official control. Options: (a) FlowDocument via MdXaml/Markdig, re-render only the last block; (b) custom ItemsControl of block elements in a VirtualizingStackPanel. Community write-ups report FlowDocument rebuilds per token are the bottleneck and recommend re-rendering only the trailing block on a timer (https://github.com/QuickerOrg/WpfMarkdownViewer, https://github.com/IgorShaposhnikov/MarkdownWPF) | CommunityToolkit MarkdownTextBlock still a Labs experiment (https://github.com/CommunityToolkit/Labs-Windows/issues/606); less proven for streaming; one developer wrote of rewriting a "stalled" WinUI 3 markdown viewer in WPF (https://zenn.dev/como/articles/article-ko-md-viewer-wpf?locale=en) | Markdown.Avalonia (419 stars) renders to Avalonia controls; streaming behaviour UNVERIFIED |
| Diff view | WebView2 + Monaco diff editor, or custom colored TextBlocks; AvalonEdit for code | WebView2 + Monaco, or RichTextBlock | AvaloniaEdit + custom diff, or open-source WebView + Monaco |
| WebView2 + Monaco | Mature (WebView2 is a WPF-native control). Serve Monaco (~2 MB) offline from a folder via `SetVirtualHostNameToFolderMapping` (https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.setvirtualhostnametofoldermapping) | Supported (WebView2 control in WinUI 3) | Avalonia's NativeWebView now open source in 12.0 (https://avaloniaui.net/blog/avalonia-12); less battle-tested |
| Dark mode / Mica | `ThemeMode=System` from .NET 9; WPF-UI gives Mica + title bar | Automatic, native | Fluent theme dark variant; Mica via window transparency hints (UNVERIFIED details) |
| Unpackaged deploy | Trivial (self-contained publish or framework-dependent) | Possible but "niche", runtime bundling pain | Trivial (self-contained publish) |
| Hot reload / designer | XAML Hot Reload in VS works; designer present | No designer; Hot Reload historically flaky | Hot reload (XAML and C#) works in Visual Studio; Rider needs `dotnet watch` or the file watcher; XAML previewer in VS, VS Code, and Rider via the third-party AvaloniaRider plugin (https://avaloniaui.net/hot-reload; https://docs.avaloniaui.net/docs/get-started/set-up-your-ide) |
| 2025-26 complaints | Fluent theme experimental; no Mica built in | Tooling, perf, legacy WinRT baggage, packaging crashes, trust | Self-drawn controls not native; v12 breaking renames (e.g. SystemDecorations -> WindowDecorations) |

Dependency-injected look: the .NET Community Toolkit MVVM (CommunityToolkit.Mvvm 8.4.2, 29.5M downloads, https://www.nuget.org/packages/CommunityToolkit.Mvvm) works with all three.

### Effort estimate (ESTIMATE, one developer, engine already separated from terminal UI; includes chat/markdown/diff, prompt box, approval + question dialogs, 10-agent sidebar, settings, cancel, tests, installer)
- WPF + WPF-UI: about 8-10 weeks. Mature controls, known streaming-markdown patterns, WebView2 Monaco mature, fewest packaging surprises.
- Avalonia 12: about 9-12 weeks. Same XAML skills; add time for markdown/streaming tuning, WebView/diff story, fewer samples for a chat UI.
- WinUI 3: about 11-15 weeks. Add time for tooling friction (no designer, flaky hot reload), MarkdownTextBlock being a Labs control, unpackaged/MSIX plumbing, perf tuning with WinRT interop.
These are my reasoned numbers, not sourced.

---

## Part 2 - Connecting UI to the Python engine

| Approach | How | Pros | Cons | Verdict |
|---|---|---|---|---|
| Child process + JSON-RPC 2.0 over stdio | StreamJsonRpc (MIT; 938 stars; NuGet 45.6M downloads, v2.25.29 of 2026-06-15, https://www.nuget.org/packages/StreamJsonRpc; transports Stream/WebSocket/Pipelines, cancellation, https://github.com/microsoft/vs-streamjsonrpc). Same model as LSP/MCP | Crash isolation, bidirectional requests (approvals), cancellation, no ports, matches existing CLI design, engine Python version independent, TMT's own Job Object sandbox untouched | Must keep stdout clean (logs to stderr); ship a Python runtime; second framing implementation in Python (small) | RECOMMENDED |
| Named pipe / localhost HTTP / WebSocket / gRPC | Engine listens | Detachable engine, multi-client | Port conflicts, firewall prompt, auth token, more code | Only if engine must outlive UI |
| pythonnet | In-process CPython | Direct calls | MIT, 5,516 stars, active; since 3.0 the GIL is not held by default so every call needs `Py.GIL()` (https://pythonnet.github.io/pythonnet/dotnet.html); GIL/thread pitfalls (https://github.com/pythonnet/pythonnet/discussions/2209); free-threaded 3.14 support only tracked (https://github.com/pythonnet/pythonnet/issues/2720); Python fault takes down the UI | No |
| CSnakes | Source generator over CPython C-API | Typed wrappers, active: 1,118 stars, last push 2026-08-23; Runtime 2.0.0-beta.350 on 2026-07-15 (https://www.nuget.org/packages/CSnakes.Runtime); Python 3.9-3.13, .NET 8/9 (https://github.com/tonybaloney/CSnakes) | Still beta (second source on beta status: NuGet version string), in-process risks, Python 3.14 support not seen | Not for this |
| IronPython | .NET implementation | n/a | Latest 3.4.2 (2024-12-19) targets Python 3.4 plus f-strings; no CPython C-extensions (https://ironpython.net/download/) - cannot run a 3.8+ codebase | No |

Python runtime notes: embeddable zip has no pip (use `ensurepip`) and no tkinter (https://github.com/python/cpython/issues/99566); stdlib-only TMT does not care. Python 3.13.15 and 3.14.7 both ship embeddable packages (https://docs.python.org/3.13/using/windows.html). PyInstaller supports 3.13 (6.10.0) and 3.14 (6.15.0), changelog at https://pyinstaller.org/en/latest/CHANGES.html.

### C# sketch (WPF; StreamJsonRpc + Job Object)

```csharp
// NuGet: StreamJsonRpc, Microsoft.Windows.CsWin32 (CreateJobObject, SetInformationJobObject, AssignProcessToJobObject)
sealed class EngineClient : IAsyncDisposable {
    readonly Process _proc; readonly JsonRpc _rpc; readonly IntPtr _job;
    readonly Dispatcher _ui = Application.Current.Dispatcher;
    public event Action<string>? StreamText;
    public Func<ApprovalRequest, Task<string>>? AskApproval;      // returns "y" | "always" | "n"

    public EngineClient(string exe, string args) {
        _job = CreateJobObject(IntPtr.Zero, null);
        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(_job, JobObjectInfoType.ExtendedLimitInformation, ref info, (uint)Marshal.SizeOf(info));

        _proc = Process.Start(new ProcessStartInfo(exe, args) {
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden,
            StandardOutputEncoding = new UTF8Encoding(false) })!;
        AssignProcessToJobObject(_job, _proc.SafeHandle);       // children of the engine inherit the job
        _proc.ErrorDataReceived += (_, e) => Log.Debug(e.Data); _proc.BeginErrorReadLine();

        // NDJSON framing: ctor is (writer stream, reader stream, formatter)
        var handler = new NewLineDelimitedMessageHandler(_proc.StandardInput.BaseStream,
                          _proc.StandardOutput.BaseStream, new SystemTextJsonFormatter());
        _rpc = new JsonRpc(handler);
        _rpc.AddLocalRpcMethod("stream.text", new Action<string>(chunk => _ui.BeginInvoke(() => StreamText?.Invoke(chunk))));
        _rpc.AddLocalRpcMethod("approval.request", async (ApprovalRequest r) =>
            await await _ui.InvokeAsync(() => AskApproval!(r)));   // marshal to UI thread, await dialog result
        _rpc.StartListening();
    }
    public Task<TurnResult> StartTurn(string text) =>
        _rpc.InvokeWithParameterObjectAsync<TurnResult>("turn.start", new { text });   // named params for Python **kwargs
    public Task Cancel() => _rpc.NotifyAsync("turn.cancel");
    public async ValueTask DisposeAsync() { _rpc.Dispose(); CloseHandle(_job); }   // job close kills engine tree
}

// UI side (WPF-UI / any dialog):
engine.AskApproval = async r => {
    var res = new ApprovalDialog(r) { Owner = Application.Current.MainWindow }.ShowDialog();   // modal
    return res switch { true => "y", null => "always", _ => "n" };                              // map button -> answer
};
```
Notes: framing gap resolved. `NewLineDelimitedMessageHandler(Stream writer, Stream reader, IJsonRpcMessageTextFormatter)` exists and works with `SystemTextJsonFormatter` (https://learn.microsoft.com/en-us/dotnet/api/streamjsonrpc.newlinedelimitedmessagehandler?view=streamjsonrpc-2.21; docs say to use it only with UTF-8 text formatters that emit no raw newlines). That is far simpler for Python than Content-Length framing. `HeaderDelimitedMessageHandler` (LSP framing) is the alternative. StreamJsonRpc sends positional (array) params for `InvokeAsync(method, a, b)`; use `InvokeWithParameterObjectAsync` to send named params that map to Python `**kwargs`. Use `BeginInvoke` for fire-and-forget text chunks and batch them every 30-60 ms before re-rendering markdown. Local methods must be added before `StartListening()`. Sketch not compiled.

### Python-side sketch (stdlib only; passes `python -m py_compile`, not run end to end)

```python
import itertools
import json
import queue
import sys
import threading


class Rpc:
    """JSON-RPC 2.0, one JSON document per line (matches NewLineDelimitedMessageHandler)."""

    def __init__(self, handlers):
        # Keep a private handle on the real stdout; anything else that prints goes to stderr.
        self._out = sys.stdout.buffer
        sys.stdout = sys.stderr
        self._in = sys.stdin.buffer
        self._lock = threading.Lock()
        self._handlers = handlers          # {"turn.start": fn, "turn.cancel": fn}
        self._pending = {}
        self._ids = itertools.count(1)

    def _send(self, obj):
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._lock:                   # engine threads (background agents) share one pipe
            self._out.write(data)
            self._out.flush()

    def notify(self, method, **params):    # engine -> UI, no reply
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method, **params):   # engine -> UI, blocks only the calling thread
        ident = next(self._ids)
        box = queue.Queue(1)
        self._pending[ident] = box
        self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        return box.get()                   # None if the UI went away (see serve)

    def serve(self):                       # run on a reader thread
        for line in iter(self._in.readline, b""):
            msg = json.loads(line)
            if "method" in msg:
                threading.Thread(target=self._dispatch, args=(msg,), daemon=True).start()
            else:
                box = self._pending.pop(msg.get("id"), None)
                if box is not None:
                    box.put(msg.get("result"))
        for box in list(self._pending.values()):   # EOF: UI is gone, unblock waiters with "no"
            box.put(None)

    def _dispatch(self, msg):
        params = msg.get("params") or {}
        try:
            fn = self._handlers[msg["method"]]
            result = fn(**params) if isinstance(params, dict) else fn(*params)
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}
        except Exception as error:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32000, "message": str(error)}}
        if "id" in msg:
            self._send(reply)


def gui_callbacks(rpc):
    """Drop-in replacements for the two callables TMT.py puts in the action context."""

    def approve(question, pattern=""):
        # agent_bash._ask() accepts True, or a string it maps via _YES / _ALWAYS
        # ("y"... / "a", "always"...); anything else, including None, is "no".
        return rpc.request("approval.request", question=question, pattern=pattern)

    def choose(text, keys):
        # Contract from TMT._question_asker: the chosen key; "" = dismissed (Esc);
        # None = nobody was asked / input ended.
        return rpc.request("question.ask", text=text, keys=[str(k) for k in keys])

    return approve, choose
```
Grounding in TMT's real code (read-only inspection):
- `TMT.py::_command_approval()` returns `approve(question, pattern="")`; `agent_bash._ask()` maps the reply: `True` or a word in `_YES` ({"y","yes","true","1","run","once"}) = once; a word in `_ALWAYS` ({"a","always","allow","remember"}) = always; anything else (including None) = no (agent_bash.py ~lines 338-390). So the UI replying "y" / "always" / "n" matches, and `None` on disconnect is safely "no".
- `TMT.py::_question_asker()` returns `choose(text, keys)`: the chosen key, `""` when dismissed (Esc), `None` when nobody could be asked; `agent_ask.answer` distinguishes those, so the GUI must map close/Esc to `""` and a dead UI to `None`.
- MISMATCH 1: `question` and `text` are pre-formatted terminal strings (`approve` appends "Type y and Enter..." from `_APPROVE_KEYS`/`_APPROVE_ONCE`; `text` goes through `relay.write_above`). A GUI wants structured fields (command, rule, reason, pattern); `agent_bash` builds the question around `"%s: %s" % (decision.rule, decision.reason)`, so a small refactor is needed to expose the pieces. The sketch passes the raw strings as a first step.
- MISMATCH 2: both callables call `agent_menu.is_interactive(sys.stdout)` first and answer "no"/None when False. Over a pipe that is always False, so the GUI adapters must replace them wholesale (as `gui_callbacks` does). The type-ahead reader and `live_panel` relay are terminal-only and have no GUI equivalent (the GUI prompt box replaces them).
- MISMATCH 3: the engine must not print to stdout; TMT's `console.print` (rich) writes to stdout. The sketch rebinds `sys.stdout` to stderr, but a rich Console created earlier may hold its own file handle (check where `console` is built in TMT.py) (UNVERIFIED).
- Job Objects: `agent_sandbox.py` creates a job per command with `_create_job()` (`CreateJobObjectW`, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, optional ActiveProcessLimit / PerProcessUserTimeLimit / ProcessMemoryLimit via `JOB_OBJECT_EXTENDED_LIMIT_INFORMATION`) and `_assign_job()` (`AssignProcessToJobObject`). The C# host's job around the engine is therefore an outer job. Windows 8+ supports nested jobs, so the per-command jobs keep working inside it; set only KILL_ON_JOB_CLOSE on the outer job so no limit propagates to children (nesting semantics from https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects; not tested here). The 64-bit HANDLE argtypes care in `_win32()` is also what a C# P/Invoke (or CsWin32) gets right for free.

---

## Part 3 - Packaging and distribution

### Freezing the Python engine
- **PyInstaller** (13,116 stars; GPL with bootloader exception, API reports NOASSERTION): use **onedir**. Onefile unpacks to temp at launch, which matches malware behaviour and drives AV false positives (https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/). Velopack's Python guide requires `--onedir` too (https://docs.velopack.io/getting-started/python).
- **Nuitka** (15,176 stars, AGPL-3.0 for the compiler; the AGPL does not apply to programs you compile; commercial plugins also leave your output unrestricted - https://nuitka.net/doc/commercial.html and search summary of https://nuitka.net/doc/license.html, which itself returned 404). More build time. Anecdotal reports of fewer AV hits: one developer saw 11/61 engines drop to 1/60 after PyInstaller -> Nuitka (https://dev.to/weisshufer/from-pyinstaller-to-nuitka-convert-python-to-exe-without-false-positives-19jf); PyInstaller 6+ is reported to have many VirusTotal false positives (https://github.com/pyinstaller/pyinstaller/issues/8164). Nuitka standalone can still be flagged. Evidence is anecdotal, so test both on VirusTotal before choosing.
- **Embeddable zip**: smallest, most transparent, no extraction; you ship your .py/.pyc yourself. Good fallback if PyInstaller flags persist.
- **Briefcase**: not researched (UNVERIFIED).
- Mitigations: onedir, sign every EXE/DLL (done in the pipeline), keep binaries from different builds identical in path/name, submit false positives to Microsoft (https://www.microsoft.com/en-us/wdsi/filesubmission); Smart App Control may block unsigned files outright on Win11 (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

### Code signing (NZ developer focus)
- Name/price (two sources): Trusted Signing was renamed **Artifact Signing**; Basic $9.99/month (5,000 signatures), Premium $99.99/month (100,000), $0.005 per extra signature (https://www.devclass.com/security/2026/01/14/code-signing-windows-apps-may-be-easier-and-more-secure-with-new-azure-artifact-service/4079554; https://azure.microsoft.com/en-us/products/artifact-signing; Microsoft Learn "starts at $9.99/month", https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation). Needs a paid Azure subscription (free/trial/sponsored not allowed, https://learn.microsoft.com/en-us/azure/artifact-signing/faq). 24-hour certs, FIPS 140-3 L3 HSM, no EV and none planned (same FAQ).
- **NZ eligibility, resolved, with a conflict between Microsoft pages:**
  - The quickstart (page updated 2026-09-29) says: "Public Trust certificates are available to organizations in the United States, Canada, the European Union, the United Kingdom, Australia, New Zealand, Japan, South Korea, Singapore, Switzerland, Norway, and Israel. Individual developers must be located in the United States or Canada." (https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart). So a **NZ registered organisation: yes; NZ individual: no.**
  - The "Code signing options" page (dated 2026-08-29) still lists only USA/Canada/EU/UK for organisations (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options). The quickstart is newer and carries the region list, so I treat NZ organisations as eligible, but confirm in the portal's identity-validation country dropdown before committing. Private Trust certs have no geographic limit but are not publicly trusted.
  - A Microsoft Q&A answer for a French individual confirms individuals outside US/Canada are refused (https://learn.microsoft.com/en-us/answers/questions/5810735/cant-create-a-new-trusted-signing-individual-ident; the answer is AI-generated, treat as indicative).
  - Organisation validation takes 1-20 business days and needs legal entity name, business identifier, website, and a monitored email on a domain you own (quickstart). A NZ sole trader with no company probably does not count as an "organization" (UNVERIFIED); a NZ company (NZBN) is the safe route. Service endpoints exist only in specific Azure regions (e.g. Japan East `jpe.codesigning.azure.net`, East/West US; no Australia or NZ region listed), so pick the closest, e.g. Japan East.
  - GitHub Actions: `azure/artifact-signing-action@v2` with OIDC (README example in the pipeline below).
- SmartScreen: EV no longer bypasses it; OV, EV and Artifact Signing all build reputation by download volume ("several weeks and hundreds of clean installs") (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation). Velopack's docs claim Artifact Signing gives "instant SmartScreen reputation" (https://docs.velopack.io/packaging/signing); Microsoft's code-signing page explicitly says it does not (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options). Trust Microsoft.
- **Alternatives if you are an NZ individual or sole trader:**

| Option | 2026 price | Fit | GitHub Actions |
|---|---|---|---|
| SignPath Foundation (free OSS signing) | Free | TMT's LICENSE is Apache License 2.0 (checked in the repo root LICENSE; Apache-2.0 is OSI-approved) and the repo is public (github.com/lllons/TMT). Conditions: OSI license with no commercial dual-licensing for any component; no proprietary components; actively maintained; already released in the form to be signed; MFA on SignPath and GitHub; sign only your own builds (https://signpath.org/terms.html). Publisher shown is "SignPath Foundation", not you. Microsoft's own page recommends it for OSS (code-signing-options). Approval time and NZ acceptance not checked (UNVERIFIED). If TMT ever becomes closed-source or dual-licensed it stops qualifying | `SignPath/github-action-submit-signing-request` (inputs: api-token, organization-id, project-slug, signing-policy-slug, github-artifact-id, output-artifact-directory). It requires the build to have run on GitHub-hosted runners and the artifact to be uploaded as a workflow artifact, which SignPath verifies (https://github.com/SignPath/github-action-submit-signing-request; https://about.signpath.io/documentation/trusted-build-systems/github) |
| Certum Open Source Code Signing in the Cloud (SimplySign) | from EUR 49 (about $58) per year (https://shop.certum.eu/open-source-code-signing-on-simplysign.html) | Cheapest paid route, open to individuals, cloud HSM so it meets the June 2023 hardware-key rule; certificates capped at 459 days from 2026-02-27; 5,000 signatures/month | No official CI support: community actions automate SimplySign Desktop with a stored TOTP secret on Windows x64/ARM64 runners then call signtool (https://github.com/jay0lee/certum-cloud-code-sign; https://www.devas.life/how-to-automate-signing-your-windows-app-with-certum/). Works, but fragile (GUI automation) and the TOTP secret must live in GitHub secrets |
| SSL.com OV + eSigner cloud signing | OV $129/yr plus eSigner tier from $180/yr (240 signings) = about $309/yr minimum (https://www.ssl.com/guide/esigner-pricing-for-code-signing/) | Cloud signing, no token | Official action `SSLcom/esigner-codesign` (name from search summary) |
| Sectigo / DigiCert OV via resellers | $150-300/yr per Microsoft (code-signing-options); USB token or cloud HSM | Traditional | Cloud-HSM products via signtool or custom steps (UNVERIFIED) |
| Microsoft Store MSIX | Free signing; Partner Center account | Store re-signs, no SmartScreen warning at all (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation); but MSIX has the virtualization pitfalls in Part 5 | Partner Center submission |

  The June 2023 CA/B Forum rule (OV private keys on HSM or hardware) is confirmed by https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options, so a plain downloadable .pfx OV cert is no longer sold; every paid route above is cloud-HSM or token based.
- **Recommendation for an NZ developer:** (1) if you can register an NZ company, Artifact Signing (about $10/month, native GitHub Action, best CI story); (2) otherwise, since TMT is Apache-2.0 and public, apply to SignPath Foundation (free); (3) otherwise Certum Open Source cloud (about $58/yr) or SSL.com. SignPath signatures show the Foundation as publisher, fine for OSS but not for a product you intend to close-source.

### Installers / updaters
| Option | Auto-update | Notes |
|---|---|---|
| Velopack | Yes, delta updates, GitHub Releases hosting | MIT, 2,355 stars, NuGet 1.9M downloads, v1.2.161 on 2026-09-29 (https://www.nuget.org/packages/Velopack); per-user install with no admin; Python SDK `pip install velopack`; `vpk pack` needs .NET SDK; supports `--azureTrustedSignFile` and `--signParams` |
| MSIX + .appinstaller | Yes (Windows-driven) | Needs CA-trusted cert; one-click `ms-appinstaller:` protocol disabled since Dec 2023 (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/choose-distribution-path); Python engine inside MSIX needs care (virtualised filesystem for writes) (UNVERIFIED) |
| Inno Setup | No | Very active: 6.7.x in Jan-May 2026 and 7.0.2 on 2026-07-14 (search summary, https://jrsoftware.org/isdl.php) |
| WiX | No built-in | v6 released 2025-04-07; Open Source Maintenance Fee for organisations >$10k revenue, enforced by EULA acceptance from v7 (https://docs.firegiant.com/wix/osmf/; https://robmensching.com/blog/posts/2025/05/12/open-source-maintenance-fee-two-months-in/). Avoid for a small project |
| Advanced Installer | Paid feature | Not researched (UNVERIFIED) |

### Distribution channels
- Microsoft Store: accepts unpackaged MSI/EXE (publisher hosts HTTPS installer, must sign with a Trusted Root Program CA, no Store-managed updates) or MSIX (free re-signing, built-in updates) (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/choose-distribution-path). MSIX route is the only way to skip SmartScreen entirely.
- winget: manifest PR to microsoft/winget-pkgs; automate with `vedantmgoyal9/winget-releaser` (uses Komac; needs a classic PAT `public_repo` and a winget-pkgs fork) (https://github.com/marketplace/actions/winget-releaser). First submission is manual.
- GitHub Releases: Velopack's natural host.

### API keys on Windows
- Python `keyring` WinVaultKeyring uses Credential Manager; UTF-16 encoding limits to about 1,280 characters per secret (https://github.com/jaraco/keyring/issues/439 and search summary) - fine for API keys; known problems under PyInstaller (same issue) so test the frozen build.
- .NET side: `Windows.Security.Credentials.PasswordVault` (packaged or unpackaged) or DPAPI `ProtectedData` with `CurrentUser` scope.
- Recommended: UI process owns secrets; sends them to the engine via an RPC `config.set` call or an inherited env var, never on the command line.

### Recommended pipeline (GitHub Actions on windows-latest)

```yaml
name: release
on: { push: { tags: ["v*"] } }
permissions: { id-token: write, contents: write }
jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with: { python-version: "3.13" }
      - name: Python tests
        run: |
          python -m pip install -r requirements-dev.txt
          python -m pytest -q
      - name: Build engine (PyInstaller onedir)
        run: |
          python -m pip install pyinstaller
          pyinstaller --noconfirm --onedir --name tmt-engine tmt/__main__.py
      - uses: actions/setup-dotnet@v5
        with: { dotnet-version: "10.0.x" }
      - name: Publish UI (self-contained)
        run: dotnet publish ui/Tmt.Ui -c Release -r win-x64 --self-contained -o publish
      - name: Stage engine next to UI
        run: Copy-Item -Recurse dist/tmt-engine publish/engine
      - uses: azure/login@v3
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - name: Sign EXE/DLL (Artifact Signing)
        uses: azure/artifact-signing-action@v2
        with:
          endpoint: https://eus.codesigning.azure.net/
          signing-account-name: ${{ vars.SIGN_ACCOUNT }}
          certificate-profile-name: ${{ vars.SIGN_PROFILE }}
          files-folder: ${{ github.workspace }}\publish
          files-folder-filter: exe,dll,pyd
          file-digest: SHA256
          timestamp-rfc3161: http://timestamp.acs.microsoft.com
          timestamp-digest: SHA256
      - name: Pack with Velopack
        run: |
          dotnet tool install -g vpk
          vpk download github --repoUrl ${{ github.server_url }}/${{ github.repository }} --token ${{ secrets.GITHUB_TOKEN }}
          vpk pack --packId TooManyTools --packVersion ${{ github.ref_name }} --packDir publish --mainExe Tmt.Ui.exe --azureTrustedSignFile sign.json
          vpk upload github --repoUrl ${{ github.server_url }}/${{ github.repository }} --token ${{ secrets.GITHUB_TOKEN }} --publish --tag ${{ github.ref_name }}
  winget:
    needs: build
    runs-on: windows-latest
    steps:
      - uses: vedantmgoyal9/winget-releaser@main
        with:
          identifier: YourName.TooManyTools
          installers-regex: 'Setup\.exe$'
          token: ${{ secrets.WINGET_ACC_TOKEN }}
```
Per-step notes:
1. Python tests: keep the 2,600 tests on Linux too if desired; Windows run is the one that gates the release.
2. PyInstaller: onedir only; pin PyInstaller version; build from a clean venv; exclude unused stdlib; test the frozen exe launches under the Job Object sandbox before continuing.
3. dotnet publish: self-contained avoids a .NET runtime install; do not use PublishSingleFile for WinUI packaged apps (https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/choose-distribution-path).
4. Signing: the artifact-signing-action and OIDC setup (Entra app, federated credential, role "Artifact Signing Certificate Profile Signer") are from https://github.com/Azure/artifact-signing-action (README example uses `azure/login@v3` and `artifact-signing-action@v2`). Sign before `vpk pack` so Velopack's packages contain signed files; also sign the Velopack Setup.exe (`--azureTrustedSignFile` in vpk does this, https://docs.velopack.io/packaging/signing; the sign.json must be created in an earlier step from secrets). Action versions shown are from the README; the other action versions (checkout/setup-*) are my assumption, verify on first run.
5. Velopack: `vpk download github` first so delta packages can be generated against the previous release; the first run may need `--channel` flags; flags `--packId/--packVersion/--packDir/--mainExe/--azureTrustedSignFile/--signParams/--channel/--delta` and `vpk download|upload github --token` are confirmed in https://docs.velopack.io/reference/cli/content/vpk-windows.
6. winget: manifest requires an installer URL of a stable release asset; the first version of a new package must be submitted by hand (wingetcreate new); afterwards winget-releaser opens PRs. Velopack's `Setup.exe` is not an MSI/MSIX; winget accepts `InstallerType: exe` if the manifest declares silent switches. Velopack's Setup.exe supports `-s/--silent` (hides dialogs, answers yes, does not launch the app afterwards) and `-t/--installto <dir>` (https://docs.velopack.io/reference/cli/content/setup-windows), so use `Silent: --silent`. Because `installers-regex: 'Setup\.exe$'` must match your release asset name, name the asset consistently (Velopack names it `<packId>-win-Setup.exe`, UNVERIFIED).

### Recommended packaging stack
Self-contained .NET UI + PyInstaller onedir engine in `engine\` -> Artifact Signing (NZ company) or SignPath Foundation (OSS, free) -> Velopack per-user installer with delta updates -> GitHub Releases -> winget via winget-releaser. Optionally add a Microsoft Store MSIX listing later to eliminate SmartScreen warnings.

---

## Top 2 picks for this project and why
1. **WPF + WPF-UI + Markdig/custom block renderer + WebView2/Monaco for diffs.** Lowest risk and effort (about 8-10 weeks, estimate): mature tooling, trivial unpackaged self-contained deployment that suits Velopack, MIT licenses, Fluent look through WPF-UI (9,672 stars, updated 2026), and Microsoft states WPF remains fully supported through .NET 11. Cons: Fluent theme and Mica are still partly third-party; no official markdown control; Windows-only forever.
2. **Avalonia 12.** Largest, most active community (31.6k stars, 24.4M NuGet downloads, v12.1.3 on 2026-09-22), MIT, WebView open-sourced, trivial unpackaged deployment, and keeps a Mac/Linux door open. Cons: self-drawn controls (look not truly native), fewer chat-UI and streaming-markdown references, v12 is a new major version.

WinUI 3 is the right long-term bet for pure native look and Microsoft is now loudly committed, but for a one-developer, stdlib-and-ship-it project its tooling gaps, unpackaged-deployment caveats and Labs-only markdown control make it third.

Either way, the Python engine stays a child process over JSON-RPC/stdio, which also makes the UI choice reversible.

---

## Part 4 - Windows integration features (WPF vs WinUI 3)

Sources: https://learn.microsoft.com/en-us/windows/apps/develop/notifications/ ; https://learn.microsoft.com/en-us/windows/apps/develop/windows-integration/jump-list ; https://learn.microsoft.com/en-us/windows/apps/develop/launch/multi-instance-apps ; https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/choose-distribution-path (several via search summaries).

| Feature | WPF | WinUI 3 | Needs MSIX / sparse identity? |
|---|---|---|---|
| Toast on task finish | `Microsoft.WindowsAppSDK` `AppNotificationManager` (works from WPF, WinForms, plain EXE); legacy `Microsoft.Toolkit.Uwp.Notifications` (`ToastNotificationManagerCompat`) is archived, last updated 2022 | `AppNotificationManager.Default.Register()` then `Show()` | **No.** Works packaged and unpackaged; call Register explicitly when unpackaged (https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-quickstart) |
| Taskbar progress | Built in: `TaskbarItemInfo.ProgressState/ProgressValue` (https://learn.microsoft.com/en-us/dotnet/api/system.windows.shell.taskbariteminfo.progressstate) | No managed API; call `ITaskbarList3` via CsWin32 on the window HWND (my knowledge, UNVERIFIED) | No |
| Jump list of recent workspaces | `System.Windows.Shell.JumpList` + `AddToRecentCategory` (https://learn.microsoft.com/en-us/dotnet/api/system.windows.shell.jumplist); classic API, expected to work unpackaged (UNVERIFIED) | Windows App SDK JumpList: **requires package identity**, not available unpackaged (jump-list doc above) | WinUI: yes (MSIX or sparse). WPF classic: no (UNVERIFIED) |
| Explorer "Open in TMT" | Legacy registry verb `HKCU\Software\Classes\Directory\shell\TMT\command` (appears under "Show more options" on Win11) | same | Registry verb: no. Top-level Win11 menu entry: **yes**, needs an `IExplorerCommand` COM DLL plus package identity via MSIX or a **sparse package** (https://blogs.windows.com/windowsdeveloper/2021/07/19/extending-the-context-menu-and-share-dialog-in-windows-11/). Recommendation: ship the registry verb only |
| Single instance | Named `Mutex` plus a named pipe (or `WM_COPYDATA`) to forward arguments to the first instance | `AppInstance.FindOrRegisterForKey` + `RedirectActivationToAsync` (https://learn.microsoft.com/en-us/windows/apps/develop/launch/multi-instance-apps) | No |
| `tmt://` protocol | Registry `HKCU\Software\Classes\tmt` (`URL Protocol`) -> `shell\open\command`, written by the installer; app forwards argv to the running instance | Same registry route unpackaged; manifest `windows.protocol` when packaged; Windows App SDK `ActivationRegistrationManager` for unpackaged registration (exists; not verified here) | No for registry; yes only for the manifest route |

Summary: toast, taskbar progress, single instance, protocol handler and a classic context-menu verb all work **unpackaged**. WinRT jump lists and the new Win11 top-level context-menu entry need identity. The "no MSIX" Velopack stack therefore covers everything except those two.

## Part 5 - MSIX full trust vs TMT's engine (Job Objects, child processes)

- A full-trust MSIX desktop app runs at medium integrity with normal permissions, but Windows gives it a virtualized view: writes under `%APPDATA%`/`%LOCALAPPDATA%` go to `%LOCALAPPDATA%\Packages\<PFN>\LocalCache\...` and HKCU writes go to a private per-package hive (https://learn.microsoft.com/en-us/windows/msix/msix-containerization-overview). The `unvirtualizedResources` capability with `RegistryWriteVirtualization`/`FilesystemWriteVirtualization` opts out (https://learn.microsoft.com/en-us/windows/msix/desktop/flexible-virtualization).
- **Directly relevant evidence**: Claude Code's own Windows MSIX desktop app hit this. Every descendant process inherits the AppData virtualization, so tools a session installs (uv Python, caches) land in the package LocalCache and are invisible outside the app (https://github.com/anthropics/claude-code/issues/93152), and `${__dirname}` resolving to an MSIX-virtualized path broke external process spawns (https://github.com/anthropics/claude-code/issues/47977). One search summary claimed child processes "do not inherit package context"; the two conflict and the Claude Code reports are first-hand, so assume file-system virtualization IS inherited and test.
- Impact on TMT: the agent runs git, shells, pip and build tools; any `%APPDATA%` write (pip/npm/cargo caches, credential files, TMT's own config) would be silently redirected, invisible to the user's normal terminal, and deleted on uninstall. Job Objects are a kernel feature and packaging does not change them, but I found no source testing CreateJobObject/AssignProcessToJobObject inside MSIX (UNVERIFIED; test early).
- **Velopack avoids all of this**: it installs an ordinary per-user directory app with no package identity or virtualization (https://docs.velopack.io/; MSIX vs Velopack: https://comcomponent.com/en/blog/2026/03/20/000-windows-app-deployment-msi-msix-clickonce-xcopy-custom-updater/). That is a strong reason to prefer Velopack over MSIX for a coding agent. If MSIX/Store is wanted later, declare `unvirtualizedResources` (restricted capability, needs Store approval) and keep TMT state outside AppData (e.g. `%USERPROFILE%\.tmt`; whether that path escapes redirection is UNVERIFIED).

## Part 6 - Accessibility and DPI

- WPF: UIA via automation peers and `AutomationProperties.Name/HelpText`; per-monitor v2 needs an app manifest `<dpiAwareness>PerMonitorV2` (otherwise it is not per-monitor aware) (https://learn.microsoft.com/en-us/answers/questions/2033882/how-to-set-permonitor-dpiawareness-for-net-8-windo). WebView2 hosted in WPF has had per-monitor DPI quirks (https://github.com/MicrosoftEdge/WebViewFeedback/issues/58; title only seen).
- WinUI 3: per-monitor v2 and UIA by default via XAML `AutomationProperties` (https://learn.microsoft.com/en-us/uwp/api/windows.ui.xaml.automation.automationproperties). Best out-of-box story.
- Avalonia 12: UIA automation peers on Windows, works with Narrator/NVDA, keyboard navigation (https://docs.avaloniaui.net/docs/app-development/accessibility); an open issue about focus not changing on type-ahead navigation on Windows (https://github.com/AvaloniaUI/Avalonia/issues/22341) shows rough edges. Streaming-text chat transcripts need live-region style announcements in all three; how each exposes that is UNVERIFIED.

## Part 7 - Windows on ARM64

- .NET: `win-arm64` is a first-class RID for `dotnet publish -r win-arm64` (general knowledge, not source-checked); x64 builds also run under emulation.
- PyInstaller: Windows ARM64 supported since v5.7.0; needs a native ARM64 Python 3.11+ (https://github.com/pyinstaller/pyinstaller/pull/7263; https://github.com/pyinstaller/pyinstaller/issues/7257). Hosted ARM64 GitHub runners: not verified; otherwise ship the x64 engine, which runs under emulation. Nuitka: native ARM64 compile exists, but standalone/onefile were reported not to work there for lack of dependency analysis (older report, possibly stale) (https://github.com/Nuitka/Nuitka/issues/1439; search summary).
- Velopack: `win-arm64` is a supported runtime/channel (https://docs.velopack.io/packaging/runtime), but an issue reports the arm64 installer failing on ARM64 machines (https://github.com/velopack/velopack/issues/85; date not checked, may be fixed), while the win-x64 installer runs on ARM64. Simplest plan: ship **x64 only** first and add ARM64 later.

---

## Part 8 - Extra verified details (pass 3)
- WPF markdown libraries are stale: Markdig.Wpf last updated 2021-01-15 (482,618 total downloads, https://libraries.io/nuget/Markdig.Wpf); Neo.Markdig.Xaml last updated 2021-07-25 (https://www-0.nuget.org/packages/Neo.Markdig.Xaml/); Shinexyt.Markdig.Wpf (FlowDocument + MarkdownViewer) is the only recently updated one (v0.43.4, 2025-11-01, 341 downloads, https://www.nuget.org/packages/Shinexyt.Markdig.Wpf/). Markdig itself is healthy: v1.4.0, 80.5M downloads, BSD-2-Clause, updated 2026-09-20 (https://www.nuget.org/packages/Markdig). Conclusion: for WPF write a thin custom renderer over Markdig (block-level incremental updates), do not depend on a third-party viewer.
- NuGet snapshot (2026-10-01): Avalonia 24.4M total / 12.1.3; Markdig 80.5M; StreamJsonRpc 45.6M / 2.25.29; CommunityToolkit.Mvvm 29.5M / 8.4.2; WPF-UI 1.2M / 4.3.0; Velopack 1.9M / 1.2.161. Pages: https://www.nuget.org/packages/<name>.
- Licenses re-checked: Avalonia MIT, WPF-UI MIT, StreamJsonRpc MIT (NuGet page), Velopack MIT, pythonnet MIT, Markdig BSD-2, Uno Apache-2.0, Flutter BSD-3, Nuitka AGPL-3.0 (output unaffected), PyInstaller GPL with bootloader exception (GitHub API returned NOASSERTION; exception from PyInstaller docs, not re-fetched).
- Inno Setup status: active (6.7.x Jan-May 2026, 7.0.2 on 2026-07-14) (https://jrsoftware.org/isdl.php via search summary). WiX v6 (2025-04-07) with the Open Source Maintenance Fee for organisations above $10k revenue; enforcement via EULA acceptance from v7 (https://docs.firegiant.com/wix/osmf/).
- IronPython 3.4.2 (2024-12-19) is the latest stable, Python 3.4 baseline plus f-strings, so it cannot run 3.8+ code (https://ironpython.net/download/).
- Qt: FluentWinUI3 style since Qt 6.8; unsupported controls fall back to Fusion (https://doc.qt.io/qt-6/qtquickcontrols-fluentwinui3.html).

---

## Verified locally (2026-10-01, this machine: Python 3.14.0, .NET SDK 8.0.413, StreamJsonRpc latest from NuGet)

Files (scratchpad, not in the repo): `...\scratchpad\dotnet-poc\engine_stub.py`, `test_engine.py`, `client\Program.cs`.

1. **Python engine stub + Python client test: PASS.** `engine_stub.py` is the Rpc sketch above plus a stub turn loop. `test_engine.py` spawns it with `subprocess`, sends `turn.start`, receives `stream.text` notifications ("Hello " ...), answers the engine-to-UI `approval.request` with `"always"` (engine reports `approved=always`), starts a second slow turn, sends `turn.cancel` after 3 ticks (engine replies `{"status":"cancelled"}` and emits `[cancelled]`), then closes stdin and checks the engine exits with code 0. Output: `PASS [('approval', 'git status'), ('sent', 'cancel')] ticks=3`.
2. **C# client on StreamJsonRpc: PASS** (a .NET 8 SDK was present, so item 3 was done). Console app: spawns `python engine_stub.py` hidden (`CreateNoWindow`), puts it in a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, uses `NewLineDelimitedMessageHandler` + `SystemTextJsonFormatter`, handles `stream.text`, auto-answers `approval.request` with "always", runs one turn, then a cancelled turn, then closes the job handle. Output:
   ```
   status=done answer=always text='Hello approved=always' pattern='git status'
   turn2 status=cancelled
   engine exited after job close: True
   PASS
   ```
   So all of these are now confirmed on a real compiler and runtime: NDJSON framing interoperates between StreamJsonRpc and a stdlib Python peer; engine-to-UI request/response with blocking Python caller works; `NotifyAsync("turn.cancel")` interrupts a running turn because the Python side dispatches each incoming message on its own thread; KILL_ON_JOB_CLOSE kills the engine.
3. **Corrections the compiler/runtime forced on the C# sketch above:**
   - `AddLocalRpcMethod` needs a typed delegate: `new Action<string>(chunk => ...)` and `new Func<string,string,Task<string>>((question, pattern) => ...)`. A bare lambda does not compile.
   - Python sends named params `{question, pattern}`. StreamJsonRpc binds named params to method **parameter names**, so the handler must take `(string question, string pattern)`. The sketch's single `ApprovalRequest r` object parameter would NOT bind (untested but follows from the binding rule; the two-string form is what ran).
   - UI to engine calls with named params use `InvokeWithParameterObjectAsync<T>(method, new { text })` (the form that ran). Notifications with no params use `NotifyAsync("turn.cancel")` and the Python handler `turn_cancel()` takes none.
   - Job Object P/Invoke was hand-written (`CreateJobObject`, `SetInformationJobObject` class 9, `AssignProcessToJobObject`, `CloseHandle`) with `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`; it passes `Process.Handle`. The sketch's CsWin32 names (`SafeHandle`, `JOB_OBJECT_LIMIT` enum) are not exercised.
   - The WPF `Dispatcher` marshalling (`_ui.BeginInvoke`, `InvokeAsync` + `await await`) and the `ContentDialog`/dialog code were NOT run: the test host is a console app. They remain unverified sketches.
   - Not tested: the interaction of this outer job with TMT's real per-command jobs in `agent_sandbox.py`, and a PyInstaller-frozen engine.
4. **Not verified:** real TMT engine (`TMT.py`) behind this protocol; the `python` launched is whatever is on PATH.

### Measured sizes and builds (2026-10-01, same machine; Windows 11, Python 3.14.0 venv, PyInstaller 6.22.3, .NET SDK 8.0.413, vpk 1.2.161)

| Artifact | Size | Files | Notes |
|---|---|---|---|
| **Real TMT engine, PyInstaller `--onedir`** (`TMT.py`, with `rich` and `requests` installed in the venv) | **24.8 MB** | 27 (24 entries in `_internal`) | Built in 51 s with no errors. Contains `python314.dll`, `_ssl`, `_ctypes`, certifi etc. Built from a clean venv, so no extra packages |
| Engine cold start: `TMT.exe --help` | 1.41 s first run, then 0.64 s and 0.69 s | n/a | Exit code 0; prints `usage: tmtcode ...` (argparse prog name is `tmtcode`, so the executable is named TMT.exe but the help says tmtcode). No task run, no API key entered |
| Nuitka `--standalone` | NOT MEASURED | n/a | Nuitka refused: `--mingw64` is not allowed on Python 3.13+, and no MSVC (`cl`) is installed, only MSYS2 gcc. Needs Visual Studio Build Tools to try |
| .NET console client, framework-dependent (win-x64) | 5 MB | 55 | Needs .NET 8 runtime installed |
| .NET console client, self-contained (win-x64) | 76 MB | 237 | Ran and passed (1.4-1.7 s end to end including the Python engine start) |
| .NET console client, self-contained + single-file + **trimmed** | 20 MB | 2 | **Does NOT work with StreamJsonRpc's `SystemTextJsonFormatter`**: the trimmer produced IL2026 warnings at publish time and at runtime `System.Text.Json` threw "Reflection-based serialization has been disabled", so the first RPC call crashed. Do not trim (or move to source-generated JSON contexts / MessagePack) |
| WPF + WPF-UI + StreamJsonRpc + Velopack app, framework-dependent | 11 MB | n/a | |
| WPF app, self-contained (win-x64, untrimmed; WPF cannot be trimmed anyway) | 172 MB | 516 | |
| Velopack staging dir, self-contained WPF + engine (`engine\`) + stub | 197 MB | 544 | |
| **Velopack output, self-contained**: `Setup.exe` / `full.nupkg` / Portable.zip | **94.5 MB / 87.4 MB / 87.4 MB** | | `vpk pack` took 23 s. Compression ratio about 2.1x on this payload |
| Velopack staging, framework-dependent WPF + engine | 36 MB | n/a | |
| **Velopack output, framework-dependent** (`--framework net8.0-x64-desktop`): `Setup.exe` / `full.nupkg` | **27.8 MB / 20.6 MB** | | `vpk pack` took 8 s. The Setup bootstraps the .NET 8 Desktop runtime on machines that lack it (download of the runtime not tested here; the runtime installer is tens of MB) |

Realistic installer for the full app (WPF + frozen engine, Velopack-compressed): **about 28 MB if framework-dependent, about 95 MB if self-contained**, measured with the stub front end. A real UI (more XAML, Markdig, a Monaco bundle if added at about 2 MB) adds a few MB at most. Updates: the nupkg is the full package; Velopack generates delta packages between releases, and since `engine\` and the runtime rarely change, deltas should be small (delta size not measured).

Defender / SmartScreen: no Defender detection event for the freshly built unsigned `TMT.exe` or the Velopack Setup during these runs (`Microsoft-Windows-Windows Defender/Operational` events 1116/1117 for the last 15 minutes returned none; an unrelated older detection in the Recycle Bin exists). This says nothing about other machines or about SmartScreen on download, which only applies to files carrying the Mark of the Web, and nothing was downloaded.

TMT repository untouched: `git status` shows only `?? gui-report/`; all build output is under the scratchpad `dotnet-poc` folder.

### WPF + WPF-UI app: build result (not launched)
- `dotnet build -c Release` of `dotnet-poc\wpf`: **Build succeeded, 0 warnings, 0 errors** (about 3 s incremental). Contents: `ui:FluentWindow` with Mica backdrop and `ui:TitleBar`, a `FlowDocument` transcript whose single live `Run` is appended to from `stream.text` through `Dispatcher.BeginInvoke`, a prompt `ui:TextBox`, Send and Cancel buttons (`NotifyAsync("turn.cancel")`), and an approval dialog using `Wpf.Ui.Controls.MessageBox` with Allow once / Always / Deny returning "y" / "always" / "n".
- Corrections the compiler or tooling forced:
  1. `MessageBox` and `MessageBoxResult` are ambiguous between `System.Windows` and `Wpf.Ui.Controls`; fully qualify the WPF-UI ones.
  2. My first approval handler blocked with `ShowDialogAsync().GetAwaiter().GetResult()` on the UI thread, which would deadlock; replaced with an async method awaited through `await await Dispatcher.InvokeAsync(() => AskApproval(...))`, which compiled (not run).
  3. `vpk pack` refuses an app that never calls `VelopackApp.Build().Run()` ("Unable to verify VelopackApp is called"). A WPF project with `App.xaml` has a generated `Main`, so add a `Program.cs` with `[STAThread] Main` that calls `VelopackApp.Build().Run()` first, then `new App().InitializeComponent(); app.Run()`, and set `<StartupObject>wpf.Program</StartupObject>`.
  4. WPF-UI 4.3.0 XAML namespace `http://schemas.lepo.co/wpfui/2022/xaml` and `ThemesDictionary` + `ControlsDictionary` in App.xaml were accepted as written.
  5. Only the build was verified; Mica rendering, the dialog, streaming behaviour and DPI were not (no window was launched by instruction).

### Nuitka retry (time-boxed, 2026-10-01 21:06-21:12): DID NOT FINISH within the time box
- Environment: `py -0p` lists 3.14, 3.12, 3.11 (plus a uv 3.11). Used Python 3.12 in `dotnet-poc\venv-nuitka` with Nuitka 4.2.2, rich and requests; command `python -m nuitka --standalone --assume-yes-for-downloads --mingw64 TMT.py`.
- Findings: Nuitka ignored the MSYS2 gcc ("Nuitka is very dependent on the precise one") and auto-downloaded winlibs gcc 15.2.0 into `%LOCALAPPDATA%\Nuitka\Nuitka\Cache` (a per-user cache outside the scratch folder, not system-wide). Python-to-C translation of TMT's code took about 2 minutes; C generation produced about 438 C files, of which about 177 had been compiled (about 40 percent) when the box closed at 21:12 after roughly 3 minutes of C compiling. I killed the build, so no output size, start time, or Defender result exists for Nuitka.
- Implication: expect a Nuitka build of this 48k-line codebase on one machine to take on the order of 8-15 minutes (extrapolated from 40 percent in about 3 minutes plus 2 minutes of translation, so a rough guess), versus 51 s for PyInstaller. Nuitka is feasible without installing a compiler on Python 3.12 (it self-downloads gcc), but not on Python 3.13+ with MinGW, where it requires MSVC. If AV false positives from PyInstaller become an issue, run Nuitka once in CI (windows-latest has MSVC) and compare; the PyInstaller baseline remains 24.8 MB and 1.4 s cold start.