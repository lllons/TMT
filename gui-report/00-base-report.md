# TMT → Windows desktop app: migration report (base analysis of the codebase)

## 1. What we're starting from

| Fact | Detail |
|---|---|
| Size | ~48,000 lines of Python in 52 flat `agent_*.py` modules, plus `TMT.py` as the entry point |
| Dependencies | Standard library only; `requests` and `rich` are optional |
| Tests | 2,625 test functions (unit and integration) |
| Distribution | npm launcher (`bin/tmtcode.js`) that installs a git checkout into `~/.tmtcode`, or `pip install -e .` |
| Model access | Raw HTTP to 4 providers (OpenRouter, OpenAI, Anthropic, Gemini) in `agent_providers.py`. No vendor SDKs. |

### How the code splits

**Terminal presentation: about 10,600 lines (~22%).** This is what gets replaced:

| Module | Lines |
|---|---|
| `agent_menu.py` | 4,586 |
| `agent_panel.py` | 2,163 |
| `agent_splash.py` | 1,085 |
| `agent_ui.py` | 1,103 |
| `agent_live_renderer.py` | 737 |
| `agent_markdown.py` | 540 (renders markdown to ANSI escape codes) |
| `agent_setup.py` | 289 |
| `agent_tips.py` | 147 |

All of this is ANSI escapes, raw keyboard input (`msvcrt`/`termios` at `agent_menu.py:2238`), cursor arithmetic and gradient animation. None of it carries over to a GUI.

**Engine: about 37,000 lines (~78%).** This is reused unchanged. The codebase already keeps it deliberately free of UI. Several module docstrings say outright "no UI, no terminal, nothing here prints":
- policy and sandbox
- the shell parser
- the bash tool
- file operations
- git, plan, review, verify, checkpoint/undo
- delegation and workers
- the agent manager, providers, credentials and session

### The coupling problem: one function

`TMT._session_loop` (`TMT.py:1343`–`2250`) is about 900 lines. It mixes running a turn with drawing it: there are 67 references to `console`, `relay`, `live_ui`, `prompt_box` and `pad` inside it. Separating those two jobs is the main refactor, whichever GUI option is picked.

### Seams that already exist

1. **A stream event vocabulary.** `stream_handler` (`TMT.py:81`) turns model output into typed events: `text`, `output`, `usage`, `action`, `progress`, `next_step`, `error`. These become the wire protocol almost as they are.
2. **Human input goes through injected callables, not direct reads.** `approve(question, pattern)` (`TMT.py:488`) and `choose(text, keys)` (`TMT.py:548`) are passed into the action context. A GUI provides different implementations: an approval dialog and an option picker.
3. **A third front end has been proven possible.** CI mode passes an `agent_ci.Run` into the same loop. That object already swaps where tasks come from, how approvals and questions are answered, and the turn and time limits. A GUI front end is the same idea with different answers.
4. **Background agents already report through events.** `AgentManager.subscribe` calls listeners outside its lock, and workers never print (`agent_worker.py`). That maps directly to "agent changed" notifications for a sidebar.
5. **Events can be serialised.** `AgentEvent.from_payload` and `Transcript(writer=...)` in `agent_ui.py:864` and `:969` already exist.
6. **Windows-specific code already exists.** `agent_sandbox.py:764` uses Win32 Job Objects through `ctypes`, which is the right primitive for a desktop app.

### Things that won't survive as-is in an installed app

| Current behaviour | Problem in an installed app | Fix |
|---|---|---|
| State files sit next to the code (`.tmt_key`, `.tmt_providers.json`, `.tmt_git`, `.tmt_effort`, `.tmt_autoupdate`; see `agent_config.py:193`, `:379`, `:451`) | `Program Files` is read-only | Move them to `%APPDATA%\TMT` |
| API keys are base64-obfuscated (the module itself says this isn't encryption) | Weak for a desktop app | Windows Credential Manager or DPAPI |
| `agent_update.py` updates by fast-forwarding a git checkout | A packaged exe isn't a git checkout | MSIX App Installer or Velopack auto-update |
| Ctrl-C raises `KeyboardInterrupt` to cancel a turn | A GUI has no Ctrl-C signal | Cancellation token (`agent_worker` already checks cancellation at three boundaries; reuse that) |
| `stdout` is the display | Under the separate-process options (§2 B/C), stdout becomes the protocol channel | Send `console` output to a log in server mode. Only about 34 `print(` calls exist, mostly in `TMT.py`. |

## 2. The options

### Option 0: Package the existing command-line app (not a GUI)
- **What:** an installer with bundled Python, a Start Menu shortcut, and a Windows Terminal profile.
- **Languages:** none new. Inno Setup script plus a PyInstaller build.
- **Effort:** a few days.
- **Use it if:** only "installable on Windows" is wanted, not a GUI.

### Option A: Python GUI in the same process
Keep everything in Python and import the engine directly.

| Toolkit | Verdict |
|---|---|
| **PySide6 (Qt 6, LGPL)** | Best of the Python choices. Qt signals and slots move events between threads cleanly, and Qt 6.7+ has a Windows 11 style. Markdown renders through `QTextBrowser`, or `QWebEngineView` for full HTML. |
| Tkinter + sv-ttk | Uses only the standard library, which fits the project's style. Weak at rich text and markdown, and looks dated. |
| wxPython | Uses real Win32 controls, but has a smaller community. |
| pywebview | Python process with an Edge WebView2 window and an HTML/TS front end. A good middle ground: small, renders modern web UI, and stays one process. |

- **Languages:** Python, plus Qt Designer `.ui` files or QML if wanted.
- **Packaging:** PyInstaller `--onedir` or Nuitka. Expect 60–120 MB.
- **Pros:** one language, fastest route to a working app, no protocol to maintain.
- **Cons:** doesn't look fully native; frozen Python apps sometimes trigger antivirus false positives; slower startup; the GUI and engine share a process, so an engine crash takes the window down with it.

### Option B: Native C# front end with the Python engine as a separate process (recommended)
- **What:** the UI is a .NET 8 app using **WinUI 3** (Windows App SDK, Fluent design, Mica); **WPF with the `WPF-UI` library** is a more mature alternative with the same look. The Python engine runs as a child process (`tmt-engine.exe`). They talk **JSON-RPC 2.0 over stdio**, the pattern the Language Server Protocol (LSP) and the Model Context Protocol (MCP) use.
- **Languages:** C# and XAML for the UI, Python for the engine, JSON for the protocol.
- **Pros:** genuinely native (Fluent controls, toast notifications, taskbar progress, jump lists, MSIX); the Python engine and its 2,625 tests stay as they are; a crash in one process doesn't take down the other; the protocol can later drive a VS Code extension or a web UI.
- **Cons:** two languages; the protocol has to be versioned; running two processes needs care (put the engine in a Job Object so closing the GUI kills the engine and everything it spawned).

### Option C: Web-technology shell with the Python engine as a separate process
- **What:** **Tauri 2** (a small Rust shell, ~10 MB, using WebView2, with the Python engine registered as a bundled companion executable — an `externalBin` sidecar), or **Electron** (already shipping through npm, but about 150 MB).
- **Languages:** TypeScript with React or Svelte, a little Rust (Tauri) or Node (Electron), and Python.
- **Pros:** the best markdown, code and diff rendering (`markdown-it`, `shiki`, Monaco's diff view); works on macOS and Linux for free, matching the `os` list in `package.json`.
- **Cons:** the controls aren't native Windows controls; three languages to maintain.

### Option D: Full rewrite in C# or Rust (not recommended)
Porting 48,000 lines and 2,625 tests. The value of TMT is in the guards — the policy, the sandbox and the shell parser — which the comments show were shaped by real failures. A rewrite would bring those failures back.

### Comparison

| | A: PySide6 | **B: WinUI 3 + engine** | C: Tauri + engine | D: Rewrite |
|---|---|---|---|---|
| Native Windows feel | Good | **Best** | Fair | Best |
| Engine code reused | 100% | **100%** | 100% | 0% |
| Languages | 1 | 2 | 3 | 1 |
| Size of the shipped app | 60–120 MB | 40–80 MB | 30–60 MB | Small |
| Cross-platform | Yes | No | Yes | Depends |
| Rough effort | Lowest | Medium | Medium | Very high |

**Recommendation: Option B.** It is the only one that delivers a native Windows app without touching the engine. If one language matters more than a native look, use **PySide6**. It needs the same engine refactor (Phase 1 below), just without the protocol layer.

## 3. The engine refactor (needed by A, B and C)

1. **Define a front-end interface** so the engine has three interchangeable front ends:
   ```python
   class Frontend(Protocol):
       def emit(self, event: AgentEvent) -> None: ...          # streaming, progress, usage
       def approve(self, question: str, pattern: str) -> str | bool: ...
       def choose(self, text: str, keys: tuple) -> str | None: ...
       def cancelled(self) -> bool: ...
   ```
   TerminalFrontend (today's behaviour), CiFrontend (`agent_ci.Run` already does this job), RpcFrontend (the new one for the GUI).
2. **Split `_session_loop`** into `Engine.run_turn(task, frontend)` (pure orchestration) and a thin terminal shell that keeps the splash, prompt box and type-ahead. Success criterion: the existing integration tests pass with no change in behaviour.
3. **Add a server mode,** `tmtcode --serve`, which speaks JSON-RPC on stdin/stdout and logs to stderr or a file.
4. **Send raw markdown, not ANSI.** The front end renders it.
5. **Send structured agent records, not drawn rows.**

### What the protocol looks like

```jsonc
→ {"jsonrpc":"2.0","id":1,"method":"turn.start","params":{"task":"fix the failing test"}}
← {"jsonrpc":"2.0","method":"stream.text","params":{"turn":1,"text":"Looking at "}}
← {"jsonrpc":"2.0","method":"turn.progress","params":{"turn":1,"message":"Running test suite"}}
← {"jsonrpc":"2.0","id":"a7","method":"approval.request","params":{"question":"Run `rm -rf build`?","pattern":"rm -rf build"}}
→ {"jsonrpc":"2.0","id":"a7","result":{"answer":"y"}}
← {"jsonrpc":"2.0","method":"agents.changed","params":{"name":"worker-2","state":"running","progress":0.4}}
→ {"jsonrpc":"2.0","method":"turn.cancel","params":{"turn":1}}
← {"jsonrpc":"2.0","id":1,"result":{"status":"cancelled"}}
```

Approvals and `ask_user` questions are requests from the engine to the GUI, as in LSP. Answers keep today's vocabulary (`y`, "always", option keys). Slash commands become methods: `undo.preview`, `undo.apply`, `plan.get`, `review.run`, `verify.run`, `note.ask`. The protocol carries a version number from the first release.

## 4. What the GUI gets beyond the terminal

| Terminal today | Windows app |
|---|---|
| Agent panel squeezed into the bottom of the screen | A real sidebar with live agent cards |
| Scrollback is the only record of a session | A saved transcript you can search and export |
| `/undo` describes the changes in text | A side-by-side diff view before you confirm |
| `/plan` shown as text | A checklist pane that updates live |
| Type-ahead queue | Queued messages shown as chips you can edit or reorder |
| Image paths typed into the prompt | Drag-and-drop or paste screenshots |
| Nothing happens when a long task finishes | Toast notification and taskbar progress |
| One workspace per process | Tabs, each with its own engine process and session |
| First-run key prompt in the terminal | A settings page for providers, models and effort |

## 5. Phased plan (rough, one developer who knows the code)

| Phase | Deliverable | Estimate |
|---|---|---|
| **0.** *(optional)* | Installer for the existing command-line app | Days |
| **1. Engine** | Front-end interface, `Engine.run_turn`, `--serve` JSON-RPC, state moved to `%APPDATA%`, cancellation token, protocol tests | 2–4 weeks |
| **2. GUI MVP** | Workspace picker, streaming markdown chat, prompt box, cancel, approval and `ask_user` dialogs, settings | 3–5 weeks |
| **3. Panels** | Agents sidebar, plan pane, review and verify results, diff and undo view, image drag-and-drop | 3–6 weeks |
| **4. Ship** | Code signing, MSIX or Velopack, auto-update replacing `agent_update`, winget manifest, crash logging | 1–2 weeks |

## 6. Languages, formats and tools (Option B)

| Layer | Choice |
|---|---|
| UI | C# 12 / .NET 8, WinUI 3 (Windows App SDK) and XAML, CommunityToolkit.Mvvm |
| Markdown and code display | Markdig, plus a WebView2 control for code blocks and diffs (Monaco) |
| Engine | Python 3.12, existing modules; frozen with PyInstaller `--onedir` or Nuitka, or shipped as the Python embeddable zip |
| Communication | JSON-RPC 2.0, newline-delimited over stdio (StreamJsonRpc on the C# side) |
| Secrets | Windows Credential Manager (`PasswordVault`) or DPAPI |
| Config and state | JSON in `%APPDATA%\TMT` |
| Packaging | MSIX with App Installer auto-update, or Velopack; Authenticode-signed |
| Distribution | winget, GitHub Releases, optionally the Microsoft Store |
| CI | GitHub Actions on `windows-latest`: Python tests, build engine, build app, sign, publish |

## 7. Risks

- **Phase 1 is where the risk is.** Splitting the 900-line loop is the part most likely to break things. Lean on the existing integration tests and change nothing else in that phase.
- **The protocol channel can be corrupted.** In server mode, any stray `print` or `rich` output breaks the protocol. Add a test that fails if anything writes to stdout outside the protocol writer.
- **UI updates must happen on the UI thread.** Engine events arrive on reader threads (WinUI 3: `DispatcherQueue`; Qt: signals and slots).
- **Antivirus and SmartScreen will flag an unsigned app.** Budget for code signing from day one; Nuitka tends to get fewer false positives than PyInstaller.
- **MSIX behaves differently on disk.** Test the sandbox's Job Objects and git subprocess calls inside the packaged app.
