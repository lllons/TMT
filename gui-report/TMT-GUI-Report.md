# Turning TMT into a native Windows desktop app: merged report (FINAL, 2026-10-01)

Built from the section files in this folder (index at the end): `00` base analysis, `10` Python toolkits, `20` web shells and ACP, `30` .NET and packaging, `40`/`41` independent fact-checks, `50` browser UI / Textual / CLI-only, `60` UX patterns, `70` an independent review of draft 2 (applied here). "(independently verified)" is used only for figures that `40` or `41` re-fetched from a primary source; figures marked "(from `30`)" or "(from `20`)" were not re-checked. "UNVERIFIED" marks claims resting on one weak source or on reasoning. All effort figures are estimates for one developer who knows the code, not measurements. `10`, `20` and `30` retain some pre-correction wording (for example WPF Fluent called experimental, PySide6 "2.7-3.4M", Goose on "Electron 40", keyring issue 439); `40`/`41` and this report take precedence.

---

## 1. Executive summary

**Recommendation.** First split TMT's engine from its terminal drawing and give it a JSON-RPC 2.0 server mode (shaped as ACP plus `_tmt/*` extensions); then build a **native WPF front end (WPF + WPF-UI)** on it, with the Python engine as a frozen child process. About **11-15 weeks for one developer end to end (estimate): 3-5 weeks engine, 8-10 weeks front end including panels, signing and installer.**

**Why not the base report's WinUI 3?** WinUI 3 looks best but has no XAML designer, flaky Hot Reload, a Labs-only markdown control, and its repo is still not accepting outside code (independently verified); WPF's .NET 9 Fluent theme is not experimental when applied in XAML (independently verified). WPF is estimated at 8-10 weeks against 11-15 for WinUI 3 (both bottom-up estimates from `30`). MSIX, which suits WinUI, virtualises `%APPDATA%` for a coding agent's child processes.

**Caveats.** No third-party ACP client (Zed, JetBrains) has been tested with TMT, only the official SDK's own client; the WPF app builds with 0 errors and 0 warnings but was never launched; engine, WPF, Electron and PySide6 sizes are measured (section 2), Nuitka is not. Every GUI toolkit needs Python 3.10+ while TMT claims 3.8+, so ship a frozen interpreter inside the app (details in section 2).

> **Start tomorrow (about one day each, in this order)**
> 1. Run the tested ACP stub against Zed with the `agent_servers` snippet in `20` section 18 (1-2 hours; proves ACP in a real client).
> 2. Launch the WPF app built from `30` and exercise Mica, streaming in a real window, DPI and the approval dialog (sizes are already measured, section 2); install Visual Studio Build Tools to try Nuitka.
> 3. Start Phase 1 in `TMT.py`: add a `TurnCancelled(KeyboardInterrupt)` exception and a per-turn `CancelToken`, check it first in `stream_handler.handle` (`TMT.py:81`), then at the round boundaries of `_session_loop` (`TMT.py:1343`), the smallest engine change every route needs (about 2-4 days, section 4). The `_session_loop` split follows.

**Glossary.**
- **ACP**: Agent Client Protocol, an open standard from Zed for editors and GUIs to drive coding agents over JSON messages.
- **JSON-RPC / NDJSON / stdio**: request-response messages as JSON, one per line, over a program's standard input and output.
- **Sidecar**: a helper program a desktop shell launches and talks to. **Engine**: TMT's Python agent code.
- **onedir**: a PyInstaller build as a folder of files rather than one self-extracting exe; fewer antivirus flags and no orphan process.
- **Job Object**: a Windows mechanism that kills a child process tree when its parent dies.
- **MSIX**: Microsoft's packaging format, which redirects some file writes. **Velopack**: an installer and auto-updater for desktop apps.
- **SmartScreen**: Windows' reputation filter that warns on unfamiliar downloads. **LGPL**: a licence letting you ship the library if users can replace it.
- **WebView2**: Edge's embeddable browser control. **UIA / DPAPI**: Windows accessibility API and per-user encryption API. **PWA**: a website installed as an app.
- **`_tmt/*`**: TMT's own extension messages for features ACP lacks.

---

## 2. What was verified locally on this machine

| Item | Result |
|---|---|
| **Stdlib ACP stub** (`20` Appendix A, `acp_stub.py` 116 lines plus a scripted client test of 64 lines) | Printed `PASS: happy path (allow_once -> end_turn) and cancel (-> cancelled)`: initialize, `session/new`, streamed chunk, `session/request_permission`, `tool_call_update`, `end_turn`, and `session/cancel` while a permission request is pending returning `cancelled`. Stdlib only; scripted test on Python 3.14, SDK validation on 3.12; vermin minimum 3.3. **Not run on Python 3.8 or with Zed** (see the SDK row). Not covered: `session/load`, `fs/*`, `terminal/*`, schema validation, concurrent sessions |
| **Python JSON-RPC engine stub + Python client test** (`30`, "Verified locally") | PASS: `turn.start`, `stream.text` notifications, engine-to-UI blocking `approval.request` answered "always", a second slow turn cancelled by `turn.cancel` (reply `cancelled`), engine exits with code 0 on stdin close. Output `PASS [('approval','git status'), ('sent','cancel')] ticks=3`. Run on Python 3.14.0 |
| **C# StreamJsonRpc client, .NET SDK 8.0.413** (console app) | PASS end to end: spawns the Python engine hidden, assigns it to a Job Object with `KILL_ON_JOB_CLOSE`, `NewLineDelimitedMessageHandler` + `SystemTextJsonFormatter`, handles `stream.text`, answers `approval.request`, runs a turn and a cancelled turn, closes the job handle and the engine dies (`engine exited after job close: True`). Confirms NDJSON interoperates between StreamJsonRpc and a stdlib Python peer, blocking engine-to-UI requests work, and cancel works because the Python side dispatches each message on its own thread |
| **Two corrections the compiler forced** | (1) `AddLocalRpcMethod` needs a typed delegate (`new Action<string>(...)`, `new Func<string,string,Task<string>>(...)`); a bare lambda does not compile. (2) Named params bind to method parameter **names**, so the approval handler must take `(string question, string pattern)`, not one `ApprovalRequest` object (the single-object form is untested, follows from the binding rule). UI-to-engine named-param calls use `InvokeWithParameterObjectAsync`; parameterless notifications use `NotifyAsync` |
| **Syntax-checked only (`py_compile`, not run)** | The PySide6, NiceGUI and Flet sketches in `10` (rewritten against TMT's real `approve`/`choose`/`subscribe` signatures); the engine-side Python sketches in `20`/`30` outside the tested stubs; framework imports were never executed. Flet 1.0 names (`page.show_dialog`, `ft.Button`, `ft.run`) are UNVERIFIED |
| **NOT run** | WPF UI code (dispatcher marshalling, dialogs; the test host was a console app); Zed, JetBrains or any real third-party ACP client (only the SDK's client); the engine on Python 3.8; the real `TMT.py` engine behind the protocol; the outer Job Object interacting with TMT's per-command jobs in `agent_sandbox.py`; the frozen engine running a real task (only `--help` was run); Nuitka and Briefcase builds; the WPF app launched; installer delta updates and any signing step |
| **PySide6 offscreen proof of concept** (`10`, "Verified locally (PySide6)") | **PASS.** `app.py` (PySide6 6.11.2, Python 3.12.8, `QT_QPA_PLATFORM=offscreen`, no window shown) spawns `engine_stub.py`, reads it on a worker `QThread`, appends `stream.text` to a `QTextBrowser` through signals, shows an approval `QDialog` that a 200 ms timer auto-clicks (Always, so the engine receives `'always'`), cancels the second turn with `turn.cancel` (reply `cancelled`), engine exits 0: 14 events. **PyInstaller onedir is 116 MB in 163 files plain, or 108 MB in 156 files with Qt `--exclude-module`s, and both frozen builds pass the same self-test.** Not tested: a visible window, DPI, antivirus, an installer |
| **Electron headless protocol test + NSIS build** (`20` section 21) | **PASS** for the protocol: the same `engine.js` the app uses, window never launched; approval answered `always`, cancel in 327 ms, engine exit 0. `electron` 44.5.1 + `electron-builder` 26.15.3, NSIS x64 unsigned, built cold in 2 min 14 s: installer 106.1 MiB, unpacked about 368 MB (the exe alone is 245.7 MB). **Not run:** the window, preload bridge, CSP and `taskkill` tree-kill |
| **Measured sizes and builds** | See the table below the verified-locally table (real `TMT.py` frozen with PyInstaller: 24.8 MB; Velopack installer 94.5 MB self-contained or 27.8 MB framework-dependent) |
| **ACP SDK validation** (`20` sections 19-20, Appendix B) | **PASS, no corrections needed.** In a Python 3.12 venv with the official `agent-client-protocol` 0.12.1 (pydantic 2.13.5), **all 20 messages of the stdlib stub validate against the SDK's strict pydantic models in both directions** (the models were confirmed strict: they reject a bogus `stopReason`, a flat `outcome`, an unknown `sessionUpdate`). The stub also passes when driven by the SDK's own `spawn_agent_process`/`ClientSideConnection` client with no changes: initialize, `session/new`, prompt, 2 chunks, permission `allow_once`, `end_turn`; cancel returns `cancelled`. The SDK contains `CreateElicitationRequest` (`elicitation/create`, form and URL modes), which can carry `choose`; its stability and client support are untested. **Not tested in Zed or JetBrains** |
| **Python floor** (vermin 1.8.0, static) | 3.8 for **52 of 53** engine files; `agent_uninstall.py` reports 3.12 only because `shutil.rmtree(onexc=...)` (line 289) sits inside `if sys.version_info >= (3, 12):` with an `onerror=` fallback, a **false positive**. So the "3.8+" claim holds statically, **but was not run on 3.8** (no 3.8 interpreter on the machine). The **test suite needs 3.11+** (`tomllib` in `test_agent_cli.py`/`test_agent_npm.py`, `rmtree(onexc)` in two tests). A stdlib ACP layer keeps the 3.8 floor; the official SDK would raise it to 3.10 and add pydantic, so use it only for tests or an optional extra |

**Measured sizes, all routes (2026-10-01, same machine: Windows 11, PyInstaller 6.22.3, .NET SDK 8.0.413, vpk 1.2.161, Node 22.18.0; from `30`, `10` and `20`).** Installers and on-disk folders are labelled; **the frozen engine is counted once per row and is not added twice.**

| Route / artifact | Size | Kind | Notes |
|---|---|---|---|
| Real `TMT.py` frozen with PyInstaller `--onedir` (engine alone) | **24.8 MB**, 27 files, built in 51 s | on-disk folder | Clean venv with `rich` and `requests`. `TMT.exe --help`: 1.4 s cold, about 0.65 s warm, exit 0; **no Defender event**; only `--help` was run |
| **WPF + WPF-UI + engine, Velopack, framework-dependent** | **27.8 MB `Setup.exe`** (nupkg 20.6 MB) | **installer, engine included** | Bootstraps the .NET 8 Desktop runtime; **the bootstrap was not tested** |
| **WPF + WPF-UI + engine, Velopack, self-contained** | **94.5 MB `Setup.exe`** (nupkg 87.4 MB) | **installer, engine included** | `vpk pack` 23 s; staging folder 197 MB (172 MB WPF self-contained without the engine); compression about 2.1x |
| **Electron 44.5.1 + electron-builder 26.15.3, NSIS x64 unsigned** | **106.1 MiB installer**; unpacked about 368 MB (exe alone 245.7 MB) | installer, **engine NOT included** | Cold build 2 min 14 s. Adding the 24.8 MB engine gives about **131 MB (estimate, not packaged together)** |
| **PySide6 6.11.2 + PyInstaller onedir** | **116 MB plain / 108 MB with Qt excludes**, 163 / 156 files | **on-disk folder before installer compression, engine NOT included** | Not packaged into an installer. Adding the 24.8 MB engine would give about 133-141 MB on disk (estimate); compressed installer size not measured |
| Nuitka `--standalone` | **No size, start-time or Defender result: the build did not finish** | n/a | Attempted on Python 3.12 with Nuitka 4.2.2, which auto-downloaded winlibs gcc 15.2.0 (so no compiler install is needed on 3.12; it refuses mingw64 on Python 3.14). It was stopped at 21:12 after about 10 minutes with 177 of 438 C files compiled; the full build is estimated at 8-15 minutes (an extrapolation, not measured). PyInstaller remains the measured baseline: 24.8 MB, 1.4 s cold start, 51 s build |
| .NET console client: framework-dependent / self-contained | 5 MB / 76 MB | on-disk folders | Self-contained ran and passed in 1.4-1.7 s end to end including the engine start |
| .NET client, self-contained + single-file + **trimmed** | 20 MB, **crashes at runtime** | on-disk | "Reflection-based serialization has been disabled": StreamJsonRpc's `SystemTextJsonFormatter` breaks under trimming. **Do not trim** (or use source-generated JSON contexts or MessagePack) |
| WPF app alone: framework-dependent / self-contained | 11 MB / 172 MB (516 files) | on-disk folders | WPF cannot be trimmed |
| Delta-update size | **NOT measured** | n/a | |

**Comparison including the engine (installers where measured):** WPF framework-dependent about **28 MB**; WPF self-contained about **95 MB**; Electron about 106 MiB + about 25 MB engine = about **131 MB (estimated)**; PySide6 about 108-116 MB on disk **before** installer compression (plus the engine, not packaged). The 94.5 MB and 27.8 MB WPF installers already contain the engine, so the earlier idea of adding 24.8 MB to them (`20` section 21's table) double counts: that table's WPF totals of about 53 MB and 119 MB should read 28 MB and 95 MB. On installer size the self-contained WPF route is the smallest self-contained option, Electron's real cost is its footprint on disk (about 368 MB unpacked) and RAM, and the 80-150 MB estimates held up.

The WPF + WPF-UI app **builds with 0 errors and 0 warnings but was not launched**. Three corrections the real build forced on the sketches: (1) WPF-UI's `MessageBox` is ambiguous with `System.Windows.MessageBox`; (2) a blocking `GetResult()` in the approval dialog would deadlock, so use `await await Dispatcher.InvokeAsync(...)`; (3) `vpk pack` requires `VelopackApp.Build().Run()`, so the app needs a custom `Program.Main` and a `StartupObject`. **Not exercised:** Mica, streaming in a real window, DPI, the approval dialog UI.


---

## 3. The codebase today (from `00`)

| Fact | Detail |
|---|---|
| Size and dependencies | About 48,000 lines in 51 `agent_*.py` modules plus `TMT.py` and `run_tests.py` (53 top-level files); stdlib only (`requests`, `rich` optional); `requires-python >=3.8` |
| Tests | 2,625 test functions |
| Distribution | npm launcher (`bin/tmtcode.js`) installing a git checkout into `~/.tmtcode`, or `pip install -e .` |
| Model access | Raw HTTP to 4 providers (`agent_providers.py`), no vendor SDKs |
| Terminal UI (about 10,600 lines, 22%) | `agent_menu` 4,586, `agent_panel` 2,163, `agent_splash` 1,085, `agent_ui` 1,103, `agent_live_renderer` 737, `agent_markdown` 540, `agent_setup` 289, `agent_tips` 147: ANSI, raw keys, cursor arithmetic |
| Engine (about 37,000 lines, 78%) | Deliberately UI-free: policy, sandbox, shell parser, bash tool, file ops, git, plan, review, verify, undo, delegation, workers, manager, providers, credentials |

**Coupling:** one function, `TMT._session_loop` (`TMT.py:1343`-`2250`), mixes running a turn with drawing it (67 references to `console`, `relay`, `live_ui`, `prompt_box`, `pad`).
**Existing seams:** `stream_handler` events (`TMT.py:81`); injected `approve` (`TMT.py:488`) and `choose` (`TMT.py:548`); CI mode as a proven second front end (`agent_ci.Run`); `AgentManager.subscribe`; serialisable `AgentEvent`/`Transcript(writer=...)`; Win32 Job Objects in `agent_sandbox.py`.
**Will not survive an installed app:** state beside the code (move to `%APPDATA%\TMT`); base64-obfuscated keys (Credential Manager or DPAPI); `agent_update.py` git fast-forward updates; Ctrl-C cancel; stdout as the display.

---

## 4. The engine refactor, including the mismatches found

1. **Front-end interface** with three implementations: `TerminalFrontend` (today), `CiFrontend` (`agent_ci.Run`), `RpcFrontend` (new): `emit`, `approve`, `choose`, `cancelled`.
2. **Split `_session_loop`** into `Engine.run_turn(task, frontend)` plus a thin terminal shell; success test: existing integration tests unchanged.
3. **`--serve` mode:** NDJSON JSON-RPC on stdin/stdout, `sys.stdout` rebound to stderr, a test failing on any non-protocol stdout write.
4. **Raw markdown, not ANSI; structured agent records, not drawn rows.**

| # | Mismatch found reading the real code | Consequence |
|---|---|---|
| 1 | **No foreground cancel token**: cancel is Ctrl-C raising `KeyboardInterrupt` (`TMT.py:1088`, `~2191`, `619`); only workers have `record.cancel` (`agent_worker.py:357`) | Engine change below |
| 2 | **`choose(text, keys)` has three outcomes**: key, `""` (dismissed with Esc), `None` (nobody asked); `agent_ask.answer` reports them differently | Wire format and Dismiss button must preserve it |
| 3 | **`approve(question, pattern="")` returns the raw typed lowercase string or `False`**; `agent_bash.py:335` maps `y/yes/true/1/run/once` to once and `a/always/allow/remember` to always, anything else to no; the pattern is optional and `delete_file`/`delete_folder` also call it | A GUI returning `'y'`/`'always'`/`'n'` works; hide "Always" when the pattern is empty; `question` is a pre-formatted terminal string, so expose structured fields (command, rule, reason, pattern) |
| 4 | `file_edit` events carry `added`/`removed` counts, **not diff text** | Add old/new text or a unified diff to `detail` |
| 5 | **No plan-change event found** (only `Plan`/`PlanStep.as_dict()`; mutation point not located) | Add a hook; map extra statuses to ACP's three |
| 6 | Both callables call `is_interactive(sys.stdout)` first and refuse over a pipe | GUI adapters replace them wholesale; type-ahead and `write_above` are terminal-only |
| 7 | `console.print` (rich) may hold its own stdout handle (UNVERIFIED) | Check where `console` is built |
| 8 | Rendering is interleaved with emission (`lines_for`, `write_above`, repaint arithmetic) | Publish a structured `AgentEvent` first; `emit` needs a sink list |
| 9 | `agent_token_update` fires at action rate; listeners run on worker threads; listener exceptions are swallowed | Debounce about 100 ms; sinks only enqueue; log inside listeners |
| 10 | `output` events are character counts for the token meter, `text` is the reply | Do not put `output` on the wire |

### Engine change required: cancellation (full design in `10`)
- Today `except KeyboardInterrupt` in `_session_loop` calls `relay.abort()`, `live_ui.stop()` and sets the outcome; workers check `record.cancel` at three boundaries, and `agent_worker.py` itself says an in-flight request keeps arriving but "a streamed one does stop being read".
- `PyThreadState_SetAsyncExc` is only a stop-gap: it lands only at a bytecode boundary and cannot interrupt a thread blocked in `socket.recv`, `Lock.acquire` or a dialog wait ([CPython #144748](https://github.com/python/cpython/issues/144748)), and can fire inside a `with lock:` ([discussion](https://discuss.python.org/t/is-there-a-safe-way-to-use-pythreadstate-setasyncexc-without-causing-deadlocks/106637)).
- With `requests`, `response.close()` from another thread does not promptly wake a blocked `iter_lines()` (in the reproduction it blocked until the next packet, about 3 s) ([psf/requests #5372](https://github.com/psf/requests/issues/5372); resolution not checked). Use layers: per-chunk cooperative check, a short read timeout, best-effort socket shutdown (private attributes, UNVERIFIED across urllib3 2.x).
- Minimal change: `TurnCancelled(KeyboardInterrupt)` plus a per-turn `CancelToken` (Event plus registered closers) checked at the round top, in `stream_handler.handle`, after `ask_model`, and before each action; `ask_model`/`_ask_model_streaming` and `agent_providers.stream_completion` take `cancel=` and register the response to close; GUI approval waits poll the token; the bash tool must kill the child process tree (how `agent_bash` waits was not read, UNVERIFIED). Subclassing `KeyboardInterrupt` keeps existing handlers working and keeps `except Exception` blocks from swallowing it. Workers need no change (`AgentManager.kill`). About 2-4 days with tests (estimate excludes bash child-process tree-kill, whose wait logic was not read; `20` section 20 item 7 gives 2-3 days including tree-kill).

---

## 5. The ways to turn TMT into a GUI

**Runner-ups by constraint.**

| Priority | Choose | Why | Estimate (after engine work) |
|---|---|---|---|
| Most native, lowest risk | **WPF + WPF-UI** + engine process (recommended) | Mature, unpackaged deploy, Fluent look | 8-10 weeks |
| Best native look, accept risk | WinUI 3 | Fluent/Mica default; weakest tooling | 11-15 weeks |
| One language only (Python) | **PySide6** in-process | LGPL, Qt signals, `windows11` style, PyGPT precedent | 6-9 weeks |
| Cross-platform | **Electron** + engine (or Avalonia 12 if C# is acceptable) | Codex and Goose pattern; best markdown/diff rendering | 5-7 weeks (Avalonia 9-12) |
| Fastest to something usable | **ACP adapter** (Zed, JetBrains, VS Code clients) | No custom UI; validated against the official SDK | about 8-10 days thin, 20-26 days full (estimates) |
| Cheapest "installable" | Package the CLI (Option 0) | Installer, shortcut, Windows Terminal profile | 3-5 days |
| Fastest custom GUI, Python | NiceGUI `native=True` | Best markdown for least code; web look | 4-6 weeks |

Routes in detail:

### 5.1 Python GUI in-process (PySide6 / NiceGUI / Flet / wxPython)
- **What:** import the engine into a Python GUI process. **Languages and formats:** Python only (plus optional Qt Designer `.ui` files or QML); output is a PyInstaller/Nuitka onedir folder wrapped in an Inno Setup `.exe`.
- **Popularity (2026-10-01):** PySide6 6.11.2, LGPL-3.0, **about 2.67M downloads/month** (independently verified); PyQt6 GPL-3.0 only, 1.37M/month (independently verified); NiceGUI 3.17.1, MIT, about 840k/month (independently verified); Flet 1.0 shipped 2026-09-15 NZ time with three patch releases in 16 days (independently verified). Other toolkits' stars and downloads are in `10` (from `10`, not re-checked).
- **Fit:** PySide6 is best: Qt 6.7+ `windows11` style with dark mode, signals/slots for worker-thread callbacks, real modal dialogs, `QTextBrowser.setMarkdown()` or QtWebEngine with markdown-it/highlight.js (PyGPT does the latter). NiceGUI gives the best markdown for least code but a web look and frozen-app/WebView2 quirks. **Flet 1.0 moved to a single-threaded event loop** ("code that was fine before will make a migrated app feel unresponsive", independently verified), which sits badly with TMT's threaded engine. Gooey-style "wrap argparse" tools do not fit an interactive chat. Kivy, Dear PyGui, Toga, Tk/CustomTkinter are weak for streamed rich text; PySimpleGUI is discontinued.
- **Effort:** PySide6 6-9 weeks; NiceGUI 4-6; Flet 5-8. **Pros:** one language, no protocol, fastest. **Cons:** not fully native, 108-116 MB on disk before compression (measured), PyInstaller antivirus flags, engine crash takes the window down, LGPL duties (section 9), PyQt6 unusable without a commercial licence.

### 5.2 Web shell plus Python engine process (Electron / Tauri 2 / pywebview)
- **What:** HTML/TypeScript UI in Electron or Tauri 2 (system WebView2); the shell spawns the frozen engine and relays NDJSON JSON-RPC. pywebview keeps Python in-process. **Languages and formats:** Python + TypeScript/HTML/CSS + JavaScript (Electron main process) or Rust (Tauri); output is an NSIS `.exe` with `latest.yml` (Electron) or an NSIS/MSI installer with a minisign `latest.json` (Tauri).
- **Popularity:** Electron v44.5.1 (2026-09-30, independently verified; 123.4k stars from `20`, not re-checked); Tauri 111.5k stars and CLI v2.12.1 (2026-09-30, independently verified); pywebview 6.2.1 (independently verified); Eel archived and Wails v3 still beta (both independently verified); pytauri pre-1.0 (from `20`).
- **Fit:** best markdown/code/diff rendering (markdown-it, shiki, Monaco or diff2html), block-wise memoised streaming. **Python-engine-plus-Tauri has one real precedent, gptme**: its Tauri 2 app bundles `gptme-server` as a PyInstaller sidecar, a loopback HTTP server on port 5700 with a per-run bearer token (framework and streaming mechanism not verified), and needed about 1,500 lines of Rust for process management because the PyInstaller onefile bootloader orphans the Python process (`--watch-pid`, `taskkill /T`, port sweep) (`20` section 17b). A stdio design removes the port and token problems; onedir removes the double-process problem.
- **Effort:** pywebview 4-6 weeks; Electron 5-7; Tauri 6-9. **Pros:** polished rendering, cross-platform, mature updater, TMT already ships on npm. **Cons:** non-native controls, 106 MiB installer and about 368 MB unpacked (Electron, measured, engine not included) or about 127 MB extra for an offline WebView2 install (Tauri), three languages, rendering XSS becomes RCE if shell settings are loose (section 8).

### 5.3 Native .NET front end plus Python engine process (WPF / WinUI 3 / Avalonia)
- **What:** C# UI (C# and XAML; Velopack `Setup.exe` plus `.nupkg` releases), Python engine as a frozen child process, StreamJsonRpc over stdio with `NewLineDelimitedMessageHandler`; the host puts the engine in a kill-on-close Job Object. Note StreamJsonRpc's default framing is `Content-Length` (LSP style), so NDJSON must be chosen explicitly (independently verified).
- **Popularity:** WinUI (`microsoft-ui-xaml`) 8.5k stars, 2,422 open issues (independently verified); Avalonia 31.6k stars (independently verified); StreamJsonRpc 2.25.29 (independently verified); WPF-UI 9.7k stars (MIT), Avalonia 24.4M NuGet downloads and Markdig 80.5M downloads are from `30`, not re-checked. WPF markdown viewers are stale (Markdig.Wpf last updated 2021), so write a thin custom renderer over Markdig that re-renders only the trailing block.
- **Fit:** native controls; toast, taskbar progress, single instance, protocol handler and an Explorer registry verb all work unpackaged; jump lists and the top-level Win11 context menu need package identity. **Proven locally:** the Python/C# protocol loop, approval, cancel and Job Object (section 2).
- **Effort:** WPF + WPF-UI 8-10 weeks; Avalonia 9-12; WinUI 3 11-15. **Pros:** native feel, crash isolation, engine and 2,625 tests untouched. **Cons:** two languages, a protocol to version, Windows-only (WPF/WinUI), Avalonia is self-drawn.

### 5.4 Protocol-first: ACP, so Zed and JetBrains become the GUI
- **What:** make TMT an Agent Client Protocol agent (JSON-RPC 2.0 over stdio), hand-rolled on stdlib. **Languages and formats:** Python only; NDJSON JSON-RPC; no installer, just a CLI flag (`tmtcode --acp`). Created by Zed (Aug 2025), co-developed with JetBrains.
- **Evidence:** repo 4.4k stars, Apache-2.0, `schema-v1.24.1` (2026-09-30) (independently verified); clients include Zed, JetBrains IDEs, VS Code extensions, Neovim, Emacs, Sublime and standalone Windows clients (AcpAgentClient, OctoShell, Superlite, VibeAround, ACP UI; quality UNVERIFIED; adoption rests on the ACP site's client list); Goose is migrating to ACP (UNVERIFIED here).
- **Fit:** exact for prompt, chunks, tool calls with diffs, plan, cancel, usage, slash commands, `session/request_permission` (note the nested `outcome` object); for `choose`, `elicitation/create` exists in SDK 0.12.1 (form and URL modes; stability and client support untested), with a permission-request fallback; gaps for agent fleet, undo, settings (use `_tmt/`). **Proven locally:** the stdlib stub passes a scripted client and the official SDK's own client and models (section 2); `run_ci` already rebinds stdout and `agent_ci.Run` already supplies replacement `approve`/`choose` callables, so ACP follows an existing pattern in the code (`20` section 20). **Zed and JetBrains on Windows are untested**, and whether Zed can launch an npm `.cmd` shim is UNVERIFIED (`cmd /c tmtcode --acp` is the safe default).
- **Effort:** about 8-10 working days for a thin `--acp` mode (text, approvals, cancel), about 20-26 days (about 5 weeks) for a full one (estimates; see the checklist in section 11). **Pros:** cheapest usable GUI, protocol maintained by others, doubles as the custom GUI's wire format. **Cons:** generic clients lack TMT's sidebar, undo, `choose` dialog and settings; spec moves fast (pin a version).

### 5.5 Local browser UI (OpenHands style) (`50`)
- **What:** the engine runs an HTTP server on `127.0.0.1`; the UI is a web app in the default browser, an Edge `--app` window, or an installed PWA. **Languages and formats:** Python (stdlib `http.server`) + TypeScript/HTML/CSS; JSON over SSE and POST; no installer, the output is a URL (optional `manifest.json` for the PWA).
- **How others do it:** OpenHands V1 uses a FastAPI `agent-server` with REST for commands and WebSocket for events (first frame must carry a session key), the local GUI run via `openhands serve` in Docker about 2 GB on port 3000; gptme runs a loopback HTTP server on port 5700 with a per-run bearer token and a React UI, the same UI inside a Tauri app (framework and streaming mechanism not verified); Open WebUI is FastAPI serving a SvelteKit build (`pip install open-webui`, port 8080); Jupyter is the security reference (random per-launch token, Host-header check); Aider's Streamlit `--browser` stayed experimental and SWE-agent's web UI was dropped for maintenance cost.
- **Security (mandatory):** loopback bind only; random per-launch token exchanged for an `HttpOnly; SameSite=Strict` cookie; Host check (DNS rebinding: official MCP SDKs shipped fixes in 2025, CVE-2025-66414/66416); Origin check; CSRF header; token kept off the command line; approvals carry engine-issued ids; plus section 8's XSS rules. Windows Firewall does not prompt for loopback listeners (weak sources, UNVERIFIED on a clean Windows 11).
- **Stdlib vs frameworks:** stdlib `ThreadingHTTPServer` (3.7+) with Server-Sent Events plus POST is enough and keeps the stdlib-only rule; WebSockets are not in the stdlib (hand-rolling RFC 6455 is not recommended); FastAPI/aiohttp only as an optional `tmt[web]` extra.
- **PWA / app mode:** installable from `localhost`/`127.0.0.1` per MDN (not tested with Edge on loopback, UNVERIFIED); an installed PWA is bound to its origin including the port, so use a fixed port; `--app` alone cannot pin a custom icon. No taskbar progress, jump lists or reliable notifications.
- **Effort:** about 4-6 weeks (server 1-2, front end 3-4); the TypeScript front end is reusable in Electron/Tauri later. **Verdict:** a legitimate interim route, not "a native Windows app".

### 5.6 Modernise the TUI with Textual (`50`)
- **What:** rewrite the ~10,600 lines of ANSI UI on Textual 8.2.8 (2026-06-30, MIT, 37.4k stars). **Languages and formats:** Python plus Textual CSS (TCSS); output is the same CLI package. Needs Python 3.9+ and pulls markdown-it-py, mdit-py-plugins, platformdirs, pygments, rich >=14.2, tree-sitter >=0.25, typing-extensions, breaking the stdlib-only rule (from PyPI metadata, read in `50`).
- **Widgets that fit:** `Markdown.get_stream()`/`MarkdownStream.write()` for streaming; `ModalScreen` with `dismiss()` or `push_screen_wait()` (worker-only) for approvals; a docked sidebar fed through `call_from_thread`; `@work(thread=True)` for the engine thread.
- **The conflict:** TMT bans the alternate screen buffer in its own source (`agent_splash.py:17`: "THERE IS NO ALTERNATE SCREEN BUFFER, AND THERE MUST NOT BE"; scrollback is "the only record a finished session leaves"; a test greps the modules). Textual defaults to the alternate screen. Its inline mode avoids it, but **Textual's guide states "Inline mode is not currently supported on Windows"**. A Windows Textual TMT would drop the founding scrollback principle (mitigation: dump the transcript to the normal screen on exit, keep a session log).
- **textual-serve / textual-web:** last textual-serve release 2025-11-01, textual-web still beta, advised for demos not production; a browser TUI still needs section 5.5's security checklist.
- **Effort:** about 6-10 weeks. **Verdict:** not recommended; still a terminal app, not a Windows GUI, and it abandons a core principle on Windows.

### 5.7 Package the CLI only (Option 0) (`50`)
- **What:** (Python only; output is an Inno Setup `.exe` plus a Windows Terminal JSON fragment and a winget YAML manifest) PyInstaller onedir (or Nuitka standalone) CLI, Inno Setup per-user installer (adds PATH, Start Menu shortcut), a **Windows Terminal JSON fragment** at `%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\TMT\tmt.json` (or ProgramData) defining a "TMT" profile (a new profile needs a `name`; use a UUID v5 GUID; local icons need Windows Terminal 1.24+), a shortcut running `wt.exe -p "TMT"`, an optional Explorer verb (`wt.exe -d "%V" -p "TMT"`, `%V` UNVERIFIED), and a winget manifest (zip with `NestedInstallerType: portable` and `PortableCommandAlias`; portable needs winget 1.3, zip 1.5).
- **Effort:** 3-5 days, plus 1-2 days each for winget and signing. Replace the git-based self-update. **Verdict:** do it as a stepping stone: it exercises freezing, signing and winget that every GUI route needs. Not a GUI.

### 5.8 Full rewrite (not recommended)
**Languages and formats:** C# or Rust only; output as the chosen shell.
Porting 48,000 lines and 2,625 tests to C# or Rust reintroduces the bugs the policy, sandbox and shell parser were shaped by. Very high effort.

### 5.9 Master comparison

| Approach | Native feel | Engine reuse | Languages | Output format | Cross-platform | Shipped size | Effort | Main risk |
|---|---|---|---|---|---|---|---|---|
| 1a PySide6 | Good | 100% in-process | Python (+ optional `.ui`/QML) | onedir folder in Inno `.exe` | Yes | **Measured: 108-116 MB on disk before installer compression (+ about 25 MB engine, not packaged)** | 6-9 wk | LGPL, AV flags, crash coupling |
| 1b NiceGUI native | Web look | 100% | Python | PyInstaller folder via `nicegui-pack` | Yes | 85-130 MB | 4-6 wk | WebView2/frozen quirks |
| 1c Flet 1.0 | Material | 100% | Python | `flet build` / `flet pack` output | Yes | 78-100+ MB | 5-8 wk | Days-old 1.0, single-threaded loop |
| 2a Electron + engine | Chromium | 100% | Python + TypeScript/HTML/CSS + JavaScript | NSIS `.exe` + `latest.yml` | Yes | **Measured: 106.1 MiB installer (+ about 25 MB engine = about 131 MB, estimated); 368 MB unpacked** | 5-7 wk | Rendering XSS, 2 processes |
| 2b Tauri 2 + engine | WebView2 | 100% | Python + Rust + TypeScript/HTML | NSIS/MSI + minisign `latest.json` | Yes | 5 MB shell + engine (about 130 MB offline WebView2) | 6-9 wk | Rust, sidecar-kill bugs |
| 2c pywebview | WebView2 | 100% | Python + HTML/JS | frozen folder, DIY updater | Yes | small + Python | 4-6 wk | Weak updater and bridge security |
| **3a WPF + WPF-UI + engine** | **Good, Fluent** | **100%** | **Python + C#/XAML** | **Velopack `Setup.exe` + `.nupkg`** | No | **Measured: installer 94.5 MB self-contained or 27.8 MB framework-dependent, both already including the engine (needs .NET 8 runtime)** | **8-10 wk** | Streaming-markdown renderer |
| 3b WinUI 3 + engine | Best | 100% | Python + C#/XAML | Velopack or MSIX | No | not measured; expected similar to 3a (UNVERIFIED) | 11-15 wk | Tooling, packaging |
| 3c Avalonia 12 + engine | Self-drawn Fluent | 100% | Python + C#/AXAML | Velopack | Yes | not measured; expected similar to 3a (UNVERIFIED) | 9-12 wk | Not native controls |
| 4 ACP adapter only | Client-dependent | 100% | Python; JSON-RPC/NDJSON | none, a CLI flag | Yes | none | 8-10 days thin, 20-26 full (standalone) | Generic clients lack TMT features |
| 5 Local browser UI | Browser/PWA | 100% | Python + TypeScript/HTML | none, a URL (+ `manifest.json`) | Yes | none | 4-6 wk | Open local port, not a desktop app |
| 6 Textual TUI | Terminal | 100% | Python + TCSS | CLI package | Yes | small | 6-10 wk | No Windows inline mode; breaks scrollback rule; deps |
| 7 Package the CLI | Terminal | 100% | none new (Inno script, JSON, YAML) | Inno `.exe`, WT fragment, winget manifest | Windows | 60-120 MB | 3-5 days | Not a GUI |
| 8 Rewrite | Best | 0% | C# or Rust | as chosen | Depends | small | Very high | Reintroduces solved bugs |

WPF (3a), Electron (2a), PySide6 (1a) and the frozen engine were measured (section 2; WPF figures include the engine, Electron and PySide6 do not); every other size is an estimate from `10`, `20` and `00` and is UNVERIFIED. `00` had guessed 40-80 MB for the .NET routes; the measured installer is 94.5 MB self-contained or 27.8 MB framework-dependent. "Effort" for routes 1-3, 5, 6 excludes Phase 1 engine work; route 4's standalone figures include its own engine work (section 11).

---

## 6. How other AI apps and coding agents built their GUIs

| App | Shell | Backend and transport | Source / check |
|---|---|---|---|
| Codex desktop | Electron (UNVERIFIED from a primary source; docs name the VS Code extension and a "rich client") | Spawns `codex app-server`; JSONL over stdio, also WebSocket/Unix socket; **omits the `"jsonrpc":"2.0"` field on the wire** (independently verified) | [docs](https://developers.openai.com/codex/app-server) |
| Goose (54.8k stars, Apache-2.0) | Electron 43.4.0 on main (`ui/desktop/package.json`) (independently verified) | Rust `goosed`, REST+SSE on 127.0.0.1; ACP migration UNVERIFIED here | [package.json](https://raw.githubusercontent.com/block/goose/main/ui/desktop/package.json) |
| Jan (44.7k stars, Apache-2.0) | Tauri v2 + React (independently verified) | Local OpenAI-compatible API (port 1337 unchecked) | `src-tauri/tauri.conf.json` |
| gptme (4.4k stars, MIT) | React web UI plus Tauri 2 desktop with a PyInstaller `gptme-server` sidecar and an ACP package (independently verified) | Loopback HTTP server on port 5700 with a per-run bearer token (framework and streaming mechanism not verified); Windows first-run issues ([#3906](https://github.com/gptme/gptme/issues/3906)) | Closest analogue to TMT |
| OpenHands | React + FastAPI; V1 `agent-server` REST + WebSocket | localhost or Docker sandbox | [SDK repo](https://github.com/OpenHands/software-agent-sdk) |
| Aider | CLI plus optional Streamlit | In-process; stayed experimental | [docs](https://aider.chat/docs/usage/browser.html) |
| Claude Desktop | Electron (secondary source quoting a Claude Code engineer; no first-party page found) | Not documented | [dbreunig](https://www.dbreunig.com/2026/02/21/why-is-claude-an-electron-app.html) |
| ChatGPT desktop, Cursor | Electron | Closed | press reports |
| Open Interpreter Workstation | Electron | Own app-server protocol (16 stars) | repo |
| LM Studio | Electron | Headless daemon plus REST | [docs](https://lmstudio.ai/docs/app/basics/lmstudio-vs-llmster-vs-lms) |
| Cline | VS Code webview | gRPC over postMessage; desktop sidecar orphaned processes on Windows ([#14221](https://github.com/cline/cline/issues/14221)) | DeepWiki |
| Zed | Native Rust (GPUI) | Hosts external agents as ACP child processes | [Zed](https://zed.dev/acp/agent/claude-agent) |
| PyGPT | PySide6 + QWebEngine (markdown-it, highlight.js), v2.8.36 | In-process; MSI and Store | [repo](https://github.com/szczyglis-dev/py-gpt) |
| Claude Code Windows MSIX app | MSIX | Hit AppData virtualisation bugs ([#93152](https://github.com/anthropics/claude-code/issues/93152)) | issues |
| SWE-agent, Plandex | Flask web UI (now maintenance-only); CLI only (dormant) | | `20` section 17 |

**Patterns (interpretation mine):** every durable GUI made the engine a separately launchable process with a structured protocol first. Python projects mostly chose a local HTTP server (gptme, OpenHands with REST plus WebSocket, SWE-agent); newer agent-first apps converged on JSON-RPC over stdio (Codex, ACP). Of nine apps surveyed, seven use Electron (Codex desktop [one of them UNVERIFIED], Goose, Claude Desktop, ChatGPT, Cursor, LM Studio, Open Interpreter Workstation) and two use Tauri (Jan, gptme). Only Zed is native, and it sponsors ACP. **No source found describes a Python CLI agent converted to a WPF or WinUI app**: the .NET route rests on the architecture pattern, and the local proof of concept (section 2), not on a direct precedent.

---

## 6b. What the GUI should look like (from `60-ux-patterns.md`)

Evidence is product docs fetched on 2026-10-01 plus search results; the complaint material is user-reported forum and issue content, not measured prevalence. Goose permission modes, gptme UI, Windsurf internals and Codex-app approvals could not be documented (UNVERIFIED; no claims made).

**TMT feature to best pattern seen (condensed).**

| TMT feature | Best pattern seen | Seen in |
|---|---|---|
| `approve(question, pattern)`, "always" = remembered pattern | Approval card: **Allow once / Always allow `<pattern>` / Deny**, pattern visible and editable, saved rules listed in settings with deny > confirm > allow precedence; scope in the label ("Always for this project: `git push *`") | Zed (pattern rules created from the dialog), Claude Code (per repository and command), VS Code (session / workspace / always) |
| `ask_user`, up to 5 one-key options | Inline question card with numbered buttons bound to keys 1-5, Esc = dismiss (the `""` answer) | Cursor "clarification while continuing work"; ACP `elicitation/create` |
| `/plan` held to completion | Persistent plan panel with step status, blocking step highlighted | Windsurf todo list, Claude Code plan mode |
| `/undo` checkpoints | Restore control per turn with a menu (code / conversation / both), preview first, say what is NOT covered (shell side effects) | Claude Code `/rewind`, Zed per-message restore |
| `/review`, `/verify` | Multi-file review view, per-hunk accept/reject, gate pass/fail chip | Zed Review Changes, Copilot Keep/Undo |
| Up to 10 background agents | Thread sidebar: status, last activity, tokens, stop per row | Zed thread sidebar, Claude Code subagent panel |
| Type-ahead queue | Removable chips above the prompt; **Enter = queue, Ctrl+Enter = steer now** | Cursor, Zed (Steer toggle), Claude Code |
| Image attach | Paste/drag thumbnail chips; disable with a tooltip when the model has no vision | Zed |
| Token and elapsed readout | Status bar: model, context used/limit, elapsed, cost | Zed token tracking |
| Cancel | Prominent Stop plus Esc; keep work done; show "cancelled, N changes kept" | Claude Code Esc semantics |

**Inline approval card, not a modal.** VS Code documents an "inline confirmation dialog" for tool calls ([agent tools](https://code.visualstudio.com/docs/copilot/agents/agent-tools)); Zed shows approvals in the agent thread (wording UNVERIFIED; its docs describe a "dialog" with pattern creation) ([tool permissions](https://zed.dev/docs/ai/tool-permissions)); Claude Code's prompt is part of the conversation flow. No source found uses a blocking OS modal for tool approval. Inline keeps the prompt box, scrollback and sidebar usable while the engine waits. Use a modal only for destructive workspace-level questions at startup (for example adopting a non-git folder), where nothing is running. This also fits the self-approval defence in section 8 (render approvals on a trusted path).

**Enter queues, Ctrl+Enter steers.** Cursor: Enter queues, Cmd+Enter sends immediately to steer ([overview](https://cursor.com/docs/agent/overview)); Zed queues by default with a "Steer" toggle ([agent panel](https://zed.dev/docs/ai/agent-panel)); Windsurf queues on Enter; Claude Code lists queued entries and notes that messages joining a running turn are not checkpointed ([checkpointing](https://code.claude.com/docs/en/checkpointing)). Queue and steer must be two explicit actions.

**"Always" is scoped per pattern.** Claude Code remembers a bash approval permanently per repository and command, file edits until session end, web fetch per repository and domain ([permissions](https://code.claude.com/docs/en/permissions)); Zed turns an approval into a regex pattern rule such as `^cargo\s+(build|test)` with precedence `always_deny`, `always_confirm`, `always_allow`; VS Code offers session / workspace / always with warnings before auto-approving environment-altering commands. TMT's remembered pattern maps one to one onto this.

**Wireframe (Windows main window).**

```
+----------------------------------------------------------------------------------------------+
| TMT   [C:\proj\app v] [ + tab ]   Tabs: [ fix-login ][ docs ]                 [Settings] [_][#][x]|
+------------------------------------------------------------------+---------------------------+
| TRANSCRIPT                                                       | SIDEBAR   [Plan][Agents][Review]|
|                                                                  |---------------------------|
|  You  10:41  Fix the failing login test                          | PLAN  (held to completion)|
|                                                                  |  [x] S1 Reproduce failure |
|  TMT  Looking at tests/test_login.py ...                         |  [>] S2 Fix token expiry  |
|   > read_file  auth.py                         0.4s  [v]         |  [ ] S3 Run verify gate   |
|   > edit auth.py   +6 -2                       [Open diff]       |---------------------------|
|   | @@ -41,7 +41,11 @@ def refresh(token):                      | AGENTS  3 / 10            |
|   | -    if token.exp < now:                                     |  * tests   running  1.2k t|
|   | +    if token.exp <= now + SKEW:                             |  * lint    done            |
|   |        [Accept] [Reject]   (per hunk)                        |  * docs    waiting  [Stop]|
|                                                                  |---------------------------|
|  +--------------------------------------------------------+      | REVIEW  /verify: PASS     |
|  | Run command?                                           |      |  2 files, +9 -3           |
|  | git push origin fix-login                              |      |  [Review changes]         |
|  | [Allow once] [Always: git push *  (this project)] [Deny]|     |  [Undo last turn v]       |
|  +--------------------------------------------------------+      |                           |
|  (engine is paused until you choose; Esc = Deny)                 |                           |
+------------------------------------------------------------------+---------------------------+
| Queued: [ also update the README (x) ] [ run lint after (x) ]        [Steer now]            |
| +----------------------------------------------------------------------------------------+ |
| | Type a task...   [img: shot.png (x)]                             [Attach] [Send] [Stop] | |
| +----------------------------------------------------------------------------------------+ |
| claude-sonnet-5-5 | effort: med | ctx 38k/200k | 12.4k out | 00:42 elapsed | $0.08 | main* |
+----------------------------------------------------------------------------------------------+
```
Enter queues while a turn runs and Ctrl+Enter steers; the `ask_user` card uses the same inline slot with numbered buttons; the transcript shows a "jump to latest" pill instead of force-scrolling.

**Five do / don't rules (from documented complaints).**
1. **Tier approvals and make "always" explicit; do not prompt for everything.** Cursor users report constant permission prompts that "defeat the purpose" ([forum](https://forum.cursor.com/t/agent-mode-constantly-asking-permission-for-doing-anything/114313)); about 93% of Claude Code prompts being approved is quoted from a search snippet of Anthropic's [auto-mode post](https://www.anthropic.com/engineering/claude-code-auto-mode) (UNVERIFIED figure). Keep read-only and policy-ALLOW silent (TMT's `agent_policy` already does), show scope in the button label, let users promote a prompt to a saved pattern.
2. **Let the reader own the scroll position; do not auto-scroll while they read.** Open bugs in several agent UIs ([Claude Code desktop #53382](https://github.com/anthropics/claude-code/issues/53382), [Cline PR 14635](https://github.com/cline/cline/pull/14635)). Suspend follow-mode on any upward scroll, resume at the bottom or via "jump to latest", keep streamed blocks height-stable.
3. **Make checkpoints trustworthy and honest.** Cursor "Restore Checkpoint" reports of a missing button and lost history ([forum](https://forum.cursor.com/t/restore-checkpoint-permanently-destroys-change-history/129652)); Windsurf reverts "irreversible"; Copilot Keep/Undo has overwritten valid code. Preview before restore (`undo.preview`), snapshot before restoring, and state what is not covered (shell and external effects).
4. **Separate queue from steer; do not let a follow-up silently interrupt or vanish.** Show queued items as visible, removable chips.
5. **Keep per-hunk control; do not force all-or-nothing review.** Windsurf's loss of per-change accept/reject was reported as a regression ([codeium #131](https://github.com/Exafunction/codeium/issues/131)). Offer per-hunk accept/reject, an auto-accept option and "revert all".

---

## 7. The wire protocol

Framing: UTF-8 NDJSON, JSON-RPC 2.0 **keeping the literal `"jsonrpc":"2.0"` field** (Codex omits it, which strict libraries and ACP clients reject); stdout carries only protocol, stderr logs; integer `protocolVersion` negotiated in `initialize` with a capabilities object; unknown notifications ignored, unknown requests answered -32601; JSON Schema from one source of truth and golden NDJSON transcripts in the test suite; `_tmt/` prefix and `_meta` for extensions; engine-initiated request ids use their own prefix.

| Message | Direction | Kind | ACP equivalent | TMT seam |
|---|---|---|---|---|
| `initialize` | client to engine | request | `initialize` | n/a |
| `turn.start` (response carries `stopReason`) | client to engine | request | `session/prompt` | session loop |
| `turn.cancel` | client to engine | notification | `session/cancel` | new `CancelToken` |
| `stream.text` | engine to client | notification | `agent_message_chunk` | `stream_handler` kind `text` |
| `turn.progress` (tool kind, status, content incl. diff) | engine to client | notification | `tool_call`/`tool_call_update` | `Transcript.emit_kind`; needs diff text |
| `usage` | engine to client | notification | `usage_update` | `stream_handler` kind `usage` |
| `approval.request` (blocks) | engine to client | request | `session/request_permission` | `approve`; options allow_once/allow_always/reject_once; pattern in `_meta` |
| `question.request` (blocks) | engine to client | request | `elicitation/create` (in SDK 0.12.1; client support untested) | `choose`; key, `""` or null |
| `_tmt/agents.changed` | engine to client | notification | none | `AgentManager.subscribe`, debounced |
| `plan.changed` | engine to client | notification | `plan` (full replacement) | needs hook |
| `_tmt/undo.preview`, `_tmt/undo.apply` | client to engine | request | none | undo/checkpoint |
| `settings.get`, `settings.set` | client to engine | request | partial | config |

The blocking pattern everywhere: the engine thread creates an Event, sends the request and waits; the reader thread resolves it; on disconnect all waiters are released with "no"/`None`. In real ACP the permission outcome is nested (`{"outcome":{"outcome":"selected","optionId":...}}`). Full examples, mapping and the tested ACP stub are in `20` sections 5, 6, 13 and Appendix A; C# and Python sketches in `30` Part 2 (with the compiler corrections in section 2 above).

---

## 8. Security of rendering model output

Model output is attacker-influenced (prompt injection via repo files, web pages, tool output), so every string and link is untrusted and the engine bridge is the prize.
- **Real incident (independently verified):** AnythingLLM Desktop <= 1.11.1: a crafted markdown image alt reached `dangerouslySetInnerHTML` without DOMPurify, and with `nodeIntegration: true` and `contextIsolation: false` became RCE (CVE-2026-32626, CVSS 9.6, published 2026-03-13, fixed in 1.11.2) ([advisory](https://github.com/Mintplex-Labs/anything-llm/security/advisories/GHSA-rrmw-2j6x-4mf2)). Related: markdown-image exfiltration (EchoLeak, CVE-2025-32711) and Cursor CVE-2025-54135.
- **Renderer rules (any web UI, including WebView2 in WPF):** markdown-it `html: false` or react-markdown without raw HTML; never `innerHTML` of model text; sanitise if HTML is allowed (DOMPurify / `rehype-sanitize`, names not re-verified); escape attributes; block remote images by default (CSP `img-src 'self' data:`) with click-to-load; allow-list link schemes and open in the system browser; strict CSP, no inline script or `eval`.
- **Self-approval:** if the renderer can send JSON-RPC replies, injected script can approve commands. Render approvals on a trusted path (native dialog, sender-validated IPC) and accept replies only for engine-issued request ids.
- **Shells:** Electron `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`, CSP, IPC sender validation, minimal `contextBridge` ([checklist](https://www.electronjs.org/docs/latest/tutorial/security)); Tauri 2 default-deny capabilities ([docs](https://v2.tauri.app/security/capabilities/)); pywebview `js_api` exposes every public method (weakest). Native WPF/WinUI controls avoid the HTML-injection class entirely; a WebView2 transcript brings the rules above back.

---

## 9. Packaging, code signing (NZ), auto-update and distribution

**Freezing the engine.** PyInstaller (13.1k stars, GPL with bootloader exception): use **onedir** (onefile self-extracts, orphans the real process when the parent kills the bootloader, and draws antivirus flags; one blog benchmark: onedir 0.4 s vs onefile 1.8 s startup, CLI tool). Nuitka (15.2k stars, AGPL compiler, output unrestricted): simple apps 10-30 MB vs PyInstaller 25-60 MB, startup 0.06-0.08 s, builds take minutes; one developer reported 11/61 AV detections falling to 1/60 (anecdote); onefile can still be flagged, use `--standalone`. Reported sizes: `flet pack` 77.8 MB, `flet build` 100+ MB ([#4620](https://github.com/flet-dev/flet/issues/4620)); NiceGUI 85-130 MB (700 MB untrimmed); Briefcase 125 MB MSI (old report, UNVERIFIED for 2026). Measured on this project (section 2): the real `TMT.py` frozen with PyInstaller onedir is 24.8 MB, `--help` starts in 1.4 s cold and 0.65 s warm with no Defender event; Nuitka was not measured. The embeddable zip is the fallback if flags persist.

**PySide6 LGPL (not legal advice).** Use onedir/standalone so Qt DLLs stay replaceable; ship a NOTICE naming Qt and PySide6 with versions, LGPL-3.0 and GPL-3.0 texts, a source offer, a README line on replacing the DLLs; no static linking; no terms that block modification ([Qt LGPL page](https://doc.qt.io/qt-6/lgpl.html), [PyInstaller discussion 5499](https://github.com/orgs/pyinstaller/discussions/5499)). No authoritative Qt page on freezing was found; get legal review.

**Code signing for a New Zealand developer.**

| Option | Price | Eligibility and caveats | CI |
|---|---|---|---|
| **Artifact Signing** (formerly Trusted Signing) | from US$9.99/month (5,000 signatures); Premium US$99.99 is UNVERIFIED | Public Trust for **organisations in NZ** and 11 other regions; **individuals only in US/Canada** ([quickstart](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart), independently verified). A page dated 2026-08-29 lists fewer countries; confirm in the portal. Paid Azure subscription; validation 1-20 business days; a NZ sole trader may not count (UNVERIFIED); no NZ region, use Japan East | `azure/artifact-signing-action@v2`, OIDC |
| **SignPath Foundation** | Free | TMT is Apache-2.0 and public; OSI licence, no proprietary parts, maintained, MFA, GitHub-hosted runners ([terms](https://signpath.org/terms.html)); publisher shown is "SignPath Foundation"; NZ acceptance unchecked (UNVERIFIED) | Official action |
| Certum Open Source cloud | about EUR 49 (US$58)/year | Individuals OK; cloud HSM; fragile CI (TOTP automation) | Community actions |
| SSL.com OV + eSigner | about US$309/year | Cloud signing | Official action |
| Sectigo/DigiCert OV | US$150-300/year | Token or HSM | Varies |
| Microsoft Store MSIX | Free signing | Store re-signs; no SmartScreen warning | Partner Center |

Rule: NZ company, Artifact Signing; else SignPath Foundation; else Certum or SSL.com. OV private keys must be on hardware/HSM since June 2023. **EV no longer bypasses SmartScreen** (independently verified: reputation takes "several weeks and hundreds of clean installs"; the Store is the only warning-free route); Velopack's "instant reputation" claim is contradicted by Microsoft. Smart App Control can block unsigned executables outright.

**Installers and updaters.** Recommended **Velopack** (MIT, v1.2.161 on 2026-09-29, Python SDK; independently verified; delta updates, per-user install and signing flags were not visible in the docs page the checker read, UNVERIFIED by them). **MSIX not recommended**: it virtualises `%APPDATA%` writes for the app and all children, so pip/npm/git caches and TMT files would be redirected and deleted on uninstall ([claude-code #93152](https://github.com/anthropics/claude-code/issues/93152), [#47977](https://github.com/anthropics/claude-code/issues/47977)); Job Objects inside MSIX untested (UNVERIFIED). Inno Setup is active (7.0.2, 2026-07-14); WiX has a maintenance fee over US$10k revenue (avoid). Electron: electron-updater with NSIS; Tauri: updater plugin with minisign.

**Distribution.** GitHub Releases (Velopack host); winget via `winget-releaser` (first manifest by hand; Velopack silent switch UNVERIFIED); Store optional later.

**API keys.** UI process owns secrets: Credential Manager / `PasswordVault` / DPAPI. Python `keyring` stores UTF-16 against the Win32 limit `CRED_MAX_CREDENTIAL_BLOB_SIZE` of 2,560 bytes (1,280 characters): fine for API keys, too small for large OAuth tokens; the earlier citation of keyring issue 439 was wrong (independently verified). Pass secrets to the engine by RPC or inherited environment, never the command line. State goes to `%APPDATA%\TMT`.

**WebView2 runtime.** Evergreen is preinstalled on Windows 11; Tauri `embedBootstrapper` adds about 1.8 MB, `offlineInstaller` about 127 MB, `fixedRuntime` about 180 MB; Electron bundles Chromium.

**Pipeline.** Tests, PyInstaller onedir engine, self-contained `dotnet publish`, stage engine under `engine\`, sign EXE/DLL/PYD, `vpk pack`, upload to GitHub Releases, winget (YAML in `30` Part 3; non-Azure action versions are assumptions).

---

## 10. Testing, accessibility, ARM64, crash reporting

**Testing (layered).** Layer 0: all engine tests unchanged. Layer 1 (about 80% of new tests): adapter/protocol tests with no widgets (golden NDJSON transcripts, `approve()` called from a thread and resolved by the test, `y/always/n` and key/`""`/`None`, a stdout-pollution test, listener thread safety). Layer 2: `pytest-qt` with offscreen Qt (PySide6); NiceGUI `user` fixture (fast, no browser) and `screen` fixture (Selenium; neither covers the native window); `flet test` (needs Flutter SDK); xUnit view-model tests plus FlaUI for C#. Layer 3: 5-10 packaged-app smoke tests with FlaUI. **WinAppDriver is effectively abandoned** (no commits since April 2025, last release v1.2.1 in 2020, about 1,155 open issues; [#1371](https://github.com/microsoft/WinAppDriver/issues/1371)); prefer FlaUI or its [WebDriver](https://github.com/FlaUI/FlaUI.WebDriver); `pywinauto` is untested here.

**Accessibility (not tested by us).** WPF: UIA peers, per-monitor v2 needs a manifest entry; WinUI 3 best out of the box; Avalonia UIA works with rough edges; Qt Widgets support UIA but `QTextEdit` is poor with NVDA; web UIs need `role="log"`/`aria-live="polite"` throttled so tokens are not read one by one, and `role="alertdialog"` for approvals; WebView2 has an open report of being unreadable by NVDA/JAWS in some hosts ([#2330](https://github.com/MicrosoftEdge/WebView2Feedback/issues/2330), 2026 status unconfirmed); Flet claims JAWS/NVDA support, test it. Announce completed messages and approvals, not chunks. Plan a manual NVDA + Narrator + keyboard pass.

**ARM64.** Ship x64 first (runs under emulation). Reported, not independently checked: PySide6 6.11.2 has `win_arm64` wheels; PyInstaller native ARM64 since 5.7.0 (ARM64 Python 3.11+); Nuitka from 3.11, standalone analysis from 2.7 (verify); .NET `win-arm64` RID (not source-checked); Velopack lists `win-arm64` but one installer issue is open (date unchecked); Flet/Flutter ARM64 unknown (UNVERIFIED); Electron/Tauri ARM64 not researched.

**Crash reporting.** Local first: `faulthandler`, `sys.excepthook`/`threading.excepthook` writing a crash file (version and OS only, no prompts, paths or keys), .NET global handlers. Opt-in only for any service: Sentry's Python SDK sends no PII by default (`send_default_pii=False`), add `before_send` scrubbing, no local variables, initialise only after opt-in (desktop apps enabling it by default drew privacy complaints). TMT sees source and secrets, so send only counts and exception types; honour `TMT_NO_TELEMETRY`.

---

## 11. Phased plan (estimates)

| Phase | Deliverable | Estimate |
|---|---|---|
| 0 (optional) | Option 0: CLI installer, Windows Terminal profile, winget | 3-5 days (+signing) |
| 1 Engine | Front-end interface, split `_session_loop`, one server mode (`--serve`, which is also the ACP mode: ACP core + `_tmt/`), cancel token (2-4 days), structured approval fields, diff text, plan-change event, state to `%APPDATA%`, protocol tests | 3-5 weeks (`00` said 2-4; the section 4 mismatches add about a week) |
| 1b (optional) | ACP naming and validation on top of Phase 1: `elicitation/create`, slash commands, test against Zed and one Windows client | about 3-6 extra days (estimate); the 8-10 / 20-26 day figures in the checklist below are standalone costs if ACP is done instead of Phase 1 |
| 2 GUI MVP | Workspace picker, streaming chat, prompt box, cancel, approval and `ask_user` dialogs, settings; UI behaviour follows `60`: inline approval card, queue vs steer, per-hunk accept/reject, honest undo scope | 3-5 weeks |
| 3 Panels | Agents sidebar, plan pane, review/verify, diff and undo view, image drag-and-drop | 3-6 weeks |
| 4 Ship | Signing, Velopack, update path replacing `agent_update`, winget, crash logging, accessibility pass | 1-3 weeks (`00` said 1-2; signing identity validation alone can take 1-20 business days) |

**One total.** The recommended route is **about 11-15 weeks for one developer end to end (estimate): 3-5 weeks engine (Phase 1) plus 8-10 weeks WPF front end (Phases 2-4 collapsed).** The phase rows 2-4 sum to 7-14 weeks while `30` estimated the WPF front end at 8-10 weeks including dialogs, sidebar, settings, tests and installer; the two were derived top-down and bottom-up and agree within noise, so the total uses the 8-10 week figure and does not add them. Other front ends, after Phase 1 (bottom-up): Avalonia 9-12, WinUI 3 11-15, PySide6 6-9, Electron 5-7, Tauri 6-9, pywebview 4-6, NiceGUI 4-6, Flet 5-8, browser UI 4-6 weeks. Read all totals as a range, not a promise.

---

## 12. Risks, open questions and unverified claims

**ACP readiness checklist (from `20` section 20, read from TMT source; day counts are my rough estimates).** TMT already has the pattern: `--ci` mode (`TMT.py:run_ci`) rebinds `sys.stdout = sys.stderr` so one JSON result is the only stdout, and `agent_ci.Run` supplies replacement `approve(question, pattern="")` and `choose(text, keys)` callables that `_session_loop` injects into the action context (`TMT.py` about 1635-1646). An ACP mode is an interactive-over-JSON variant of that seam.

| # | Change | Days |
|---|---|---|
| 1 | `--acp` flag routed in `main()` before the splash, like `--ci` | 0.5 |
| 2 | Stdlib NDJSON JSON-RPC server module (the stub's about 116 lines) | 2 |
| 3 | Pollution guard: stdout rebind, silence splash/menu/key prompts, replace live-region writes; stray `print(` calls (TMT.py 22, agent_config 2, agent_model 1, agent_prompt 3); check 7 `subprocess` uses | 2 |
| 4 | **Split `_session_loop` into "get next task" + "run one turn"** (the biggest, riskiest item) | 4-6 |
| 5 | Stream events to `session/update` (text, usage, tool calls, diffs; diff text must be added to edit events) | 3 |
| 6 | Approvals via `session/request_permission` (return `"y"`/`"always"`/`"n"`; `False` when no client); `choose` via `elicitation/create`, preserving `None` vs `""` | 1.5 |
| 7 | Cancellation: cooperative flag, unblock pending approvals, tree-kill tool subprocesses | 2-3 |
| 8 | `AgentManager.subscribe` to `_tmt/agents.changed` (enqueue to writer thread, debounce) | 1 |
| 9 | Plan-change hook to ACP `plan` updates | 1 |
| 10 | Slash commands via `available_commands_update`; hide or adapt menu-drawing commands | 1.5 |
| 11 | Sessions: `session/new` cwd to `resolve_workspace` as CI does; one session per process initially | 1 |
| 12 | `_tmt/*` extensions plus golden NDJSON tests and an SDK-client conformance test (needs Python 3.10+ in CI) | 2-3 |

Total about 20-26 working days for a solid mode, or about 8-10 days for a thin one (items 1, 2, 3, 5 partial, 6, 7, 11); item 4 dominates the uncertainty. Features with no ACP equivalent need `_tmt/*` extensions: agent fleet and notes, checkpoint/undo, review/verify gates, `/note`, settings and model/effort switching, memory controls, auto-update, next-step suggestion.

**Risks.** Phase 1 splitting the 900-line loop (lean on integration tests); protocol corruption by stray stdout; orphaned engines (Job Object, stdin-EOF watchdog, tree-kill on cancel); UI-thread marshalling bugs; streaming-markdown performance (re-render only the trailing block, batch at 30-60 ms); antivirus and SmartScreen (sign from day one, onedir); XSS and self-approval in web-rendered transcripts; ACP spec drift (pin a version); a one-week-old Flet 1.0.

**Decisions the user must make.**
1. **Code-signing route (New Zealand).** Artifact Signing (from US$9.99/month) is open to **NZ organisations but not NZ individuals** (quickstart page, updated 2026-09-29; independently verified). With a NZ company use it; otherwise apply to the free **SignPath Foundation** (TMT is Apache-2.0 and public), or Certum Open Source cloud (about US$58/year, awkward CI) or SSL.com (about US$309/year). Section 9.
2. **Adopt ACP as the wire format?** Recommended yes, hand-rolled on stdlib: the official Python SDK `agent-client-protocol` 0.12.1 requires Python 3.10+ (<3.15) and Pydantic (independently verified) while TMT is stdlib-only and 3.8+. Generic ACP clients will not show TMT's sidebar, undo or `choose` dialog.
3. **WPF vs WinUI 3 vs Avalonia.** Reversible, because the protocol is shared; a 1-2 week spike (streaming transcript plus approval dialog) can settle it.
4. **Windows-only?** WPF and WinUI drop the macOS/Linux support the npm package implies (the terminal version still works there).
5. **Transcript rendering:** native controls, or WebView2 (markdown-it, shiki, Monaco) inside the native shell. The second lowers streaming-markdown risk and raises accessibility and security surface.
6. **Python floor:** PySide6, Flet, NiceGUI and the ACP SDK all need Python 3.10+ (independently verified) while TMT claims 3.8+, so any GUI ships a frozen interpreter (Python 3.8 is past end of life; bundle 3.13). Full vermin finding in section 2.

**Open questions.** Is a NZ company available for Artifact Signing? Is Windows-only acceptable? Native or WebView2 transcript? Adopt ACP? Keep the 3.8 source floor? Who tests ARM64?

**Unverified claims, split by impact.**

*Could change the decision:*
- Build 2026 "primary framework" statements about WinUI (press-reported only) and the WinUI Phase 3 date (no Microsoft primary source).
- ACP in a real client: no third-party client (Zed, JetBrains) tested; Zed launching an npm `.cmd` shim; `elicitation/create` client support.
- The engine on Python 3.8 (static check only); the real `TMT.py` engine behind the protocol; the WPF dispatcher/dialog code; Job Object nesting with TMT's per-command jobs; Job Objects inside MSIX.
- NZ edge cases: sole traders for Artifact Signing, SignPath acceptance for NZ, Artifact Signing regions; Premium price; Velopack delta/per-user/signing flags.
- Nuitka sizes and startup (not measured); a visible PySide6 window and Electron window, preload, CSP and tree-kill (only headless/offscreen protocol tests ran); the frozen engine running a real task and the WPF app launched (only builds and `--help` ran); the framework-dependent installer's .NET 8 bootstrap; delta-update size.
- Cancellation details: `agent_bash` child-process waiting, where the plan is mutated, whether rich's Console holds its own stdout handle, psf/requests #5372 resolution and the socket attribute path across urllib3 2.x.

*Minor:*
- Whether Codex desktop is Electron (docs name the VS Code extension); Goose REST+SSE and ACP migration; gptme framework, streaming mechanism and "v0.32.1" detail; Flet 1.0 API names and foreign-thread `update()`.
- WinUI/Avalonia markdown streaming; Tauri + frozen Python size (30-80 MB estimate); PySide6 compressed installer size; Briefcase 125 MB (old); Toga download count; Gooey release date; pytest-qt release date; Neutralino and Jan licences; WebView2 accessibility in 2026; Qt freezing and Nuitka-LGPL guidance; QtWebEngine, Flet, Electron and Tauri ARM64; the single-`ApprovalRequest`-parameter binding form.
- Edge installing a PWA from a plain-http loopback origin; Windows Firewall silence on loopback; Textual inline-mode lag status; `%LOCALAPPDATA%` expansion in a fragment `commandline`; `%V` in an Explorer verb; 60-ux: the 93% approval figure and Zed's inline approval wording.
- Effort figures for every route are estimates; Textual and browser-UI numbers are guesses.

---

## File index (`gui-report/`)

| File | Contents |
|---|---|
| `00-base-report.md` | Codebase analysis: module split, seams, original options and phased plan, first protocol sketch |
| `10-python-native.md` | Python toolkit comparison (13 options), real-world conversions, PySide6 streaming-markdown notes, packaging evidence, **sketches for PySide6/NiceGUI/Flet against TMT's real signatures**, LGPL checklist, testing layers, accessibility, ARM64, crash reporting, **cancellation design** |
| `20-web-shells.md` (its Top-2 picks, Electron and Tauri, apply within web shells only; the overall recommendation is in this report's section 1) | Electron/Tauri/pywebview comparison, real AI apps, IPC pitfalls, **ACP deep dive and event mapping**, JSON-RPC catalogue, Tauri/Electron/pywebview sketches, rendering libraries, XSS security, WebView2 distribution, gptme/OpenHands source reading, Zed on Windows, **tested stdlib ACP stub (Appendix A)** |
| `30-native-dotnet-packaging.md` | WPF/WinUI 3/Avalonia head-to-head, StreamJsonRpc C# and Python sketches, **signing for NZ**, installers, GitHub Actions pipeline, Windows integration features, MSIX vs the engine, accessibility/DPI, ARM64, **local end-to-end test results** |
| `40-fact-check.md` | Independent re-verification of 17 claims from `10` and `30` |
| `41-fact-check-web.md` | Independent re-verification of 14 claims from `20` |
| `50-browser-tui-cli.md` | Local browser UI (security checklist, stdlib server, PWA), Textual migration (alternate-screen conflict), CLI-only packaging with Windows Terminal fragments |
| `60-ux-patterns.md` | How Zed, Claude Code, Cursor, Copilot, Windsurf and others handle approvals, diffs, plans, agents, queueing and undo; TMT feature to pattern table; full wireframe; five do/don't rules with sources |
| `70-review-notes.md` | Independent QA review of draft 2: 25 items, applied in this version (a few lowest-priority items listed under "Review items not applied") |
| `TMT-GUI-Report.md` | This merged report |
| `scratchpad\acp-poc\` (outside `gui-report/`, under `C:\Users\ALiam\AppData\Local\Temp\claude\C--Coding-compressarea-TMT\<session>\scratchpad\`) | ACP proof of concept: `acp_stub.py`, `acp_client_test.py`, `validate_schema.py`, `sdk_client_test.py` |
| `scratchpad\dotnet-poc\` (same scratchpad) | .NET proof of concept: `engine_stub.py`, `test_engine.py`, `client\Program.cs` (StreamJsonRpc client with Job Object) |

## Review items not applied
- Review item 23 (trimming section 5.1/5.2 popularity bullets) was applied only partly: the number dumps for 5.1 and 5.2 were shortened, the rest stays in `10`/`20`.
- Review item 22's glossary is condensed to the terms that appear in sections 1-5; others are explained at first use.
- `20-web-shells.md` section 3 and `30` still carry the pre-correction wording noted in the intro; they were not edited by this refresh.
