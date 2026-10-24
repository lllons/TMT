# Python-native GUI approaches for TMT (GUI in the same Python process)

Research date: 2026-10-01. Stars and last-push dates come from the GitHub API (api.github.com/repos/<owner>/<repo>); downloads from pypistats.org (recent endpoint); versions from pypi.org. All were read through a summarising fetch tool. Items marked (UNVERIFIED) have one weak source only. Code sketches are untested design sketches, not copied from sources.

## Comparison table

| Framework | License | Popularity (2026-10-01) | Native look | Markdown / streaming fit | Packaging | Verdict |
|---|---|---|---|---|---|---|
| PySide6 (Widgets/QML) | LGPL-3.0 / GPL / commercial ([PyPI](https://pypi.org/project/PySide6/)) | 6.11.2 released 2026-08-18; 2.67M downloads/month ([pypistats](https://pypistats.org/packages/pyside6)), a search snippet quoted 3.42M (different date); range 2.7-3.4M | Real Qt; Qt 6.7+ `windows11` style uses WinUI-like drawing, dark mode works out of the box ([Qt forum](https://forum.qt.io/topic/153499/fusion-and-windows11-style-currently-broken-in-qt-6-7beta1)) | QTextBrowser `setMarkdown()` (no syntax highlighting of its own) or QtWebEngine + markdown-it/highlight.js as PyGPT does | PyInstaller, Nuitka (`pyside6-deploy`), Qt-recommended | Top pick |
| PyQt6 | GPL-3 or paid Riverbank licence ([pythonguis](https://www.pythonguis.com/faq/licensing-differences-between-pyqt6-and-pyside6/)) | 1.37M/month ([pypistats](https://pypistats.org/api/packages/pyqt6/recent)) | Same as Qt | Same | Same | Only if TMT can be GPL |
| Tkinter / ttk | PSF (permissive) | stdlib | Partial native; blurry at 150% DPI without explicit scaling ([pistack](https://www.pistack.xyz/posts/2026-08-30-python-gui-frameworks-tkinter-pyside6-flet-comparison/)) | Text widget tags, hand-rolled markdown | PyInstaller, smallest | Cheap but weak UX |
| CustomTkinter | MIT | 13.6k stars, 612k/month, pushed 2026-06-24 ([GitHub](https://github.com/TomSchimansky/CustomTkinter), [pypistats](https://pypistats.org/api/packages/customtkinter/recent)) | Custom, modern, not native | Same Text limits | PyInstaller | Pretty but thin widget set |
| sv-ttk | MIT | 2.6k stars, last push 2025-06-08, quiet >1 year ([GitHub](https://github.com/rdbende/Sun-Valley-ttk-theme)) | Win11-like ttk theme | Same | PyInstaller | Flag: slow maintenance |
| wxPython | wxWindows Library Licence (permissive, LGPL-like) | 4.3.1 released 2026-07-30 ([PyPI](https://pypi.org/project/wxPython/)); 2.6k stars, 159k/month, pushed 2026-09-30 ([GitHub](https://github.com/wxWidgets/Phoenix)) | Genuinely native Win32 controls | RichTextCtrl, or WebView (Edge) with HTML | PyInstaller | Solid, small ecosystem |
| Flet | Apache-2.0 | 1.0 released 2026-09-15; 1.0.3 on 2026-09-30 ([PyPI](https://pypi.org/project/flet/)); 17.2k stars ([GitHub](https://github.com/flet-dev/flet)); 250k/month; 9M+ total ([flet.dev](https://flet.dev/blog/flet-1-0/)) | Flutter Material/Cupertino, not native | Built-in `Markdown` control with `code_theme`, `selectable` ([docs](https://flet.dev/docs/controls/markdown/)) | `flet build windows`, `flet pack` | Pretty, young |
| NiceGUI `native=True` | MIT | 3.17.1 released 2026-09-18 ([PyPI](https://pypi.org/project/nicegui/)) (my first-pass "3.4.0" was stale); 16.3k stars, 840k/month, pushed 2026-09-29 | Web UI (Quasar) in a pywebview/WebView2 window | `ui.markdown` (markdown2 + Pygments), `ui.chat_message`, `set_content()` ([docs](https://nicegui.io/documentation/markdown)) | `nicegui-pack` (PyInstaller wrapper) | Strong dark horse |
| Dear PyGui | MIT | 15.6k stars, 192k/month, last push 2026-05-13 | GPU custom, no native | No rich text | PyInstaller | Wrong tool |
| Kivy / KivyMD | MIT | 19.0k stars, 169k/month, pushed 2026-09-24 | Non-native | Poor desktop text | PyInstaller/Buildozer | Wrong tool |
| Toga + Briefcase | BSD-3 | 5.4k stars, pushed 2026-10-01; pypistats shows 3,486/month for `toga` (probably undercounts a meta-package; UNVERIFIED) | Native (WinForms) | No markdown/rich text widget | Briefcase MSI | Too thin |
| Gooey | MIT | 21.9k stars, pushed 2026-09-12; 4,057/month; latest release 1.0.8.1 (date not verified; search snippets suggest 2021, i.e. stale) ([PyPI stats](https://pypistats.org/packages/gooey)) | wxPython form | Argparse form only; no interactive chat | PyInstaller | Does not fit |
| Textual (+serve/web) | MIT | 37.4k stars, 8.2.8 on 2026-06-30 ([GitHub](https://github.com/Textualize/textual), [releases](https://github.com/Textualize/textual/releases)); pypistats says 54M/month, inflated by transitive installs; textual-serve last release 2025-11-01 (quiet); textual-web "beta" | Terminal | Excellent Markdown widget + streaming | pip/PyInstaller | Modernises the TUI, not a GUI |

Also: PySimpleGUI is discontinued (v5 commercial) ([pythonguis](https://www.pythonguis.com/faq/which-python-gui-library/)); cli2gui last release 2022, stale ([PyPI](https://pypi.org/project/cli2gui/2022.2.1)).

## Short notes per option

**PySide6.** LGPL permits closed distribution if Qt is dynamically linked (use onedir/standalone, not static). Threading: worker threads emit Qt signals; slots run on the GUI thread via queued connection. Touching widgets from a worker causes crashes or silent corruption ([pistack](https://www.pistack.xyz/posts/2026-08-30-python-gui-frameworks-tkinter-pyside6-flet-comparison/), [Qt forum](https://forum.qt.io/topic/160665/pyside6-slot-executed-in-signal-s-thread-even-with-auto-queuedconnection)). Gotcha: a slot of a plain Python object (not QObject) can run in the emitter thread, so make receivers QObjects living in the GUI thread. Modal dialogs: `QDialog.exec()` is trivial. Wheels are ~100-150 MB installed ([pistack](https://www.pistack.xyz/posts/2026-08-30-python-gui-frameworks-tkinter-pyside6-flet-comparison/)).

**Flet 1.0.** Apache-2.0, very active (pushed the day of this research). 1.0 moved to a single-threaded event loop; previously every sync handler had its own thread, now blocking code must be moved off with `page.run_thread` or async ([flet.dev](https://flet.dev/blog/flet-1-0/), [pistack](https://www.pistack.xyz/posts/2026-08-30-python-gui-frameworks-tkinter-pyside6-flet-comparison/)). API volatility across 0.x was high; 1.0 is one week old. Windows sizes reported 77.8 MB (`flet pack`) and 100+ MB (`flet build`) with a 28 MB libmpv DLL ([issue 4620](https://github.com/flet-dev/flet/issues/4620)).

**NiceGUI native.** App is really a local web server (main process) with a pywebview window in a separate process, so config under a main guard is ignored by the window process ([NiceGUI docs](https://nicegui.io/documentation/section_configuration_deployment)). Needs WebView2 / .NET on Windows; reported WebView2Loader.dll and init problems when frozen ([#4882](https://github.com/zauberzeug/nicegui/issues/4882), [#1714](https://github.com/zauberzeug/nicegui/discussions/1714)). Best markdown/code experience of the Python-native options because it is a browser engine. Threads: you can update UI from other threads only via `ui.timer` polling, `app.storage`, or `background_tasks`/`asyncio` loop `call_soon_threadsafe`; its UI is async-first.

**Others.** wxPython is the best "truly native controls" choice but no markdown widget and smaller community. Tk/CustomTkinter: lowest effort, lowest ceiling for streamed rich text. Dear PyGui/Kivy: wrong fit. Toga: native but missing rich-text. Gooey: wraps argparse only; needs wxPython; wxPython 4.10 once broke it ([HN thread via search](https://news.ycombinator.com/item?id=38586767)); no model for a live chat.

**Textual route.** Keeps the TUI paradigm; textual-serve serves it in a browser but is described as not production-grade ([search summary](https://textual.textualize.io/blog/2024/09/08/towards-textual-web-applications/)). Does not deliver a native Windows GUI window.

## Real-world conversions
- **yt-dlp GUIs** are overwhelmingly PySide6 wrappers; one project replaced a deprecated Tkinter version with a Qt one ([example](https://github.com/Plutoeat/yt-dlp-gui)). No stated rationale found (UNVERIFIED reasons).
- **Aider** kept the CLI and added an optional Streamlit browser mode (`--browser`) instead of a desktop app ([docs](https://aider.chat/docs/usage/browser.html)); Streamlit's rerun model is described as awkward for agent UIs ([search summary](https://www.innonexa.com/2026/07/02/streamlit-alternatives-python-ai-agent-apps-2026/)).
- **PyGPT**: ~2k stars, v2.8.36 (2026-09-30), PySide6 desktop AI assistant shipped as MSI, Microsoft Store, Snap, PyPI ([GitHub](https://github.com/szczyglis-dev/py-gpt)). Chat output uses QWebEngine with markdown-it and highlight.js; streaming supported. Strongest precedent for "PySide6 + streaming LLM markdown".
- **Flet local LLM chat client** with streaming throttling and stop button ([Deonik80/LLM_Local_Chat](https://github.com/Deonik80/LLM_Local_Chat)).
- General 2026 guidance: tkinter for scripts, PySide6 for professional desktop products, Flet when mobile/web are also needed ([pistack](https://www.pistack.xyz/posts/2026-08-30-python-gui-frameworks-tkinter-pyside6-flet-comparison/)).

## PySide6 streaming markdown (how real apps do it)
- PyGPT's approach (verified): render in QWebEngine, parse with markdown-it, highlight with highlight.js ([GitHub](https://github.com/szczyglis-dev/py-gpt)). I could not find its internal chunk-update code (UNVERIFIED how it avoids re-parsing).
- Generic technique (my recommendation, not sourced): (1) append raw text deltas to a buffer; (2) throttle re-render to ~20-30 Hz with a QTimer; (3) re-render only the *last open block* (current paragraph/fence) and freeze finished blocks; split on blank lines outside fences; (4) in a QTextBrowser, use a per-message QTextDocument or `QTextCursor.insertFragment` per block; in QWebEngine, send deltas to JS via `runJavaScript`/QWebChannel and let JS mutate only the last block's DOM; (5) code blocks: QTextBrowser has no highlighter, so either pre-render fences to HTML with Pygments (`HtmlFormatter(noclasses=True)`) once the fence closes, or use QWebEngine + highlight.js.
- Tradeoff: QtWebEngine adds ~100+ MB (Chromium) and a separate process; QTextBrowser is light but needs Pygments glue and has weak diff/table styling.

## Packaging evidence (installer sizes, startup, antivirus)
- PyInstaller vs Nuitka, simple app: Nuitka 10-30 MB vs PyInstaller 25-60 MB; CLI startup: PyInstaller onefile 1.8 s, onedir 0.4 s, Nuitka onefile 0.08 s, standalone 0.06 s; Nuitka builds take minutes vs seconds ([Thought Parameters](https://blog.thoughtparameters.com/post/nuitka_vs_pyinstaller_python_packaging/), a single blog benchmark, not PySide-specific). A PySide6 app will be far larger than these simple-app numbers (Qt alone ~100 MB+).
- Antivirus: PyInstaller bootloader is on some AV lists; mitigate with onedir, code signing, rebuilding the bootloader, submitting false-positive reports ([pythonguis](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/)). One developer reported 11/61 detections with PyInstaller falling to 1/60 with Nuitka ([DEV post](https://dev.to/weisshufer/from-pyinstaller-to-nuitka-convert-python-to-exe-without-false-positives-19jf), anecdote); Nuitka onefile can still be flagged (Wacatac/Sabsik) because of temp extraction; Nuitka standalone avoids that ([search summary](https://www.sublimearts.io/en/blog/nuitka-python-standalone)).
- NiceGUI + PyInstaller: ~700 MB with numpy/matplotlib, 85 MB after trimming, typically 100-130 MB ([discussion 1160](https://github.com/zauberzeug/nicegui/discussions/1160)); `nicegui-pack` wraps PyInstaller. No NiceGUI-specific AV report found (UNVERIFIED).
- Flet: 77.8 MB `flet pack`, 100+ MB `flet build windows` ([issue 4620](https://github.com/flet-dev/flet/issues/4620)); 1.0 claims faster startup via bytecode compilation ([flet.dev](https://flet.dev/blog/flet-1-0/)). No AV reports found (UNVERIFIED); Flutter-based EXEs are unsigned like any other, so SmartScreen warnings apply.
- Briefcase: one tutorial user reported a 125 MB MSI for hello world ([issue 492](https://github.com/beeware/briefcase/issues/492), old, likely stale; UNVERIFIED for 2026).
- Not found: measured *installer* (setup.exe/MSI) sizes for PySide6 apps; typical PySide6 onedir ~100-200 MB uncompressed, installer roughly half after compression (my estimate, UNVERIFIED). Code signing (e.g. SignPath, free for OSS) matters more than the freezer for SmartScreen.

## How TMT maps onto the top picks (grounded in TMT's real code)

What I read in the repo, and what it means for a GUI:

- **Stream events** (`TMT.py:81`, `stream_handler`): the engine calls `handle(event)` with a `(kind, value)` tuple. Kinds: `first_content`, `text` (reply deltas, normally fed to `relay.feed(value)`), `output`, `usage`, `action`, `progress` (goes to `transcript.emit_kind`), `next_step`, `error`. A GUI replaces `live_ui`/`relay`/`transcript` with an object that has the same `handle(event)`.
- **Approval** (`TMT.py:488`, `_command_approval`): `approve(question, pattern="")` returns the RAW lowercased answer, or `False` when not interactive. `agent_bash.py:335` interprets: `y/yes/true/1/run/once` = once, `a/always/allow/remember` = always, anything else (False, None, "") = no. So returning `'y'`, `'always'`, `'n'` from a GUI works as briefed. MISMATCH with the brief: the pattern argument is optional (default `""`), and the callable is also reached by `delete_file`/`delete_folder` (`agent_actions._confirmation`), so dialogs must handle an empty pattern (hide the "Always" button then).
- **Question** (`TMT.py:548`, `_question_asker`): `choose(text, keys)` returns the key, `""` when the user dismissed (Esc), `None` when no one could be asked. MISMATCH: the brief said "key or None"; there is a third meaning, dismissed = `""`, and `agent_ask.answer` reports the two differently to the model, so a GUI Dismiss button must return `""`, not `None`.
- **Agent bus** (`agent_manager.py:458`): `AgentManager.subscribe(listener)` with `listener(event_name, record)`; names are module constants (`AGENT_CREATED`, `AGENT_STARTED`, `AGENT_ACTIVITY_CHANGED`, `AGENT_TOKEN_UPDATE`, `AGENT_COMPLETED`, `AGENT_KILLED`, `AGENT_FAILED`, `AGENT_TIMED_OUT`, `AGENT_VIOLATION`, `AGENT_REMOVED_FROM_UI`, `AGENT_RESULT_AVAILABLE`, `NOTE_STARTED`, `NOTE_COMPLETED`). Listeners are called outside the manager lock from worker threads; exceptions are swallowed (so a buggy GUI listener fails silently - log inside it). `AgentRecord` has `id, number, kind, task, status, activity, tokens_in, tokens_out, steps, max_steps, cancel (threading.Event), done`. `manager.kill(agent_id)` sets `record.cancel` and marks KILLED, so the sidebar's per-agent Kill button needs no new engine code. Records are mutable and shared: copy the fields you need in the listener or read them on the GUI thread knowing they may change under you.
- **Cancel of the foreground turn - BIGGEST MISMATCH.** There is no `threading.Event` for the main turn. Today cancellation is Ctrl-C raising `KeyboardInterrupt` in the session loop (`TMT.py:1088`, `2191`; `choose` re-raises it at `TMT.py:619`). A GUI Cancel button therefore needs a small engine change (a cancel Event checked at the same boundaries that catch `KeyboardInterrupt`, mirroring `record.cancel` / `WorkerCancelled` for background agents). The sketches below use `PyThreadState_SetAsyncExc(thread_id, KeyboardInterrupt)` as a stop-gap: it injects the exception into the engine thread, but only fires when the thread is executing Python bytecode, not while blocked in a socket read or in the `done.wait()` of a dialog. Prefer the Event.
- **Transcript/AgentEvent** (`agent_ui.py:864`): `AgentEvent(kind, message, stage, detail, at)` with `EVENT_KINDS` (progress, milestone, warning, success, error, tool, file_read, file_edit, file_create, file_delete, command, test, background_agent, next_step_suggestion, final) and an append-only `ResponseHistory`. `Transcript.emit(event)` prints once and records; a GUI should subclass or replace `Transcript` so `emit` posts the event to the UI thread, and map each kind to a chat-row style. The `writer` hook (`Transcript(writer=...)`) is the existing seam for redirecting output with no engine change.

All three sketches below were syntax-checked with `python -m py_compile` (not run; framework imports were not executed, and the Flet 1.0 names `page.show_dialog`, `ft.Button`, `ft.run` are UNVERIFIED against the 1.0 docs). Importing them needs the frameworks installed.

### 1. PySide6

```python
import ctypes
import threading
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QMessageBox


class Bridge(QObject):
    """Lives in the GUI thread. Engine threads only call its plain methods."""
    stream = Signal(str, object)      # (kind, value) exactly as stream_handler gets them
    agent = Signal(str, object)       # (event_name, AgentRecord)
    ask = Signal(object)

    def __init__(self, view, sidebar, engine_thread_id=None):
        super().__init__()
        self.view, self.sidebar = view, sidebar
        self.engine_thread_id = engine_thread_id
        self.stream.connect(self._on_stream)
        self.agent.connect(self._on_agent)
        self.ask.connect(self._on_ask)

    # ---- engine threads -------------------------------------------------
    def handle(self, event):                       # same shape as stream_handler's handle(event)
        kind, value = event
        self.stream.emit(kind, value)

    def on_agent_event(self, name, record):        # AgentManager.subscribe(bridge.on_agent_event)
        self.agent.emit(name, record)              # read record fields on GUI thread; snapshot if racy

    def _roundtrip(self, req):
        req["done"] = threading.Event()
        self.ask.emit(req)
        req["done"].wait()
        return req["result"]

    def approve(self, question, pattern=""):       # TMT's signature; agent_bash maps y/a/always/else-no
        return self._roundtrip({"kind": "approve", "q": question, "pattern": pattern})

    def choose(self, text, keys):                  # key | "" (dismissed) | None (nobody asked)
        return self._roundtrip({"kind": "choose", "q": text, "keys": [str(k) for k in keys]})

    def cancel_turn(self):                         # Cancel button; see mismatch note
        if self.engine_thread_id:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(self.engine_thread_id), ctypes.py_object(KeyboardInterrupt))

    # ---- GUI thread -----------------------------------------------------
    @Slot(str, object)
    def _on_stream(self, kind, value):
        if kind == "text":
            self.view.append_markdown_delta(value)  # buffered, flushed by a QTimer
        elif kind == "progress":
            self.view.add_progress_row(str(value))

    @Slot(str, object)
    def _on_agent(self, name, record):
        self.sidebar.upsert(record.id, record.task, str(record.status), record.activity)

    @Slot(object)
    def _on_ask(self, req):
        if req["kind"] == "approve":
            box = QMessageBox(self.view)
            box.setText(req["q"])
            once = box.addButton("Allow once", QMessageBox.AcceptRole)
            always = box.addButton("Always: " + req["pattern"], QMessageBox.AcceptRole)
            box.addButton("Deny", QMessageBox.RejectRole)
            box.exec()
            req["result"] = {once: "y", always: "always"}.get(box.clickedButton(), "n")
        else:
            req["result"] = self.view.ask_choice(req["q"], req["keys"])
        req["done"].set()
```

Design notes: signals carry `(kind, value)` unchanged so the existing `handle(event)` shape is kept; the blocking `done.wait()` happens only on the engine thread. For a prompt box that stays live, show an inline approval card in the transcript instead of `QMessageBox.exec()` (which nests an event loop and blocks interaction with other windows); set `done` from the card's buttons.

### 2. NiceGUI native

```python
import asyncio
import ctypes
import threading
from nicegui import app, ui

loop = None
engine_thread_id = None
chunks = []


@app.on_startup
async def _capture_loop():
    global loop
    loop = asyncio.get_running_loop()


async def _ask(title, buttons):
    fut = asyncio.get_running_loop().create_future()

    def pick(value, dialog):
        if not fut.done():
            fut.set_result(value)
        dialog.close()

    with ui.dialog().props("persistent") as dialog, ui.card():
        ui.label(title)
        with ui.row():
            for label, value in buttons:
                ui.button(label, on_click=lambda v=value: pick(v, dialog))
    dialog.open()
    return await fut


def _blocking(title, buttons):
    return asyncio.run_coroutine_threadsafe(_ask(title, buttons), loop).result()


def approve(question, pattern=""):
    return _blocking(question, [("Allow once", "y"), ("Always: " + pattern, "always"), ("Deny", "n")])


def choose(text, keys):
    return _blocking(text, [(str(k), str(k)) for k in keys] + [("Dismiss", "")])


def handle(event):                                 # TMT stream event (kind, value)
    kind, value = event
    if kind == "text":
        chunks.append(value)                       # list.append is thread-safe


def on_agent_event(name, record):                  # AgentManager.subscribe(on_agent_event)
    loop.call_soon_threadsafe(sidebar.refresh, name, record.id, record.activity)


def cancel_turn():
    if engine_thread_id:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(engine_thread_id), ctypes.py_object(KeyboardInterrupt))


@ui.refreshable
def sidebar(name="", agent_id="", activity=""):
    ui.label("%s %s %s" % (name, agent_id, activity))


md = ui.markdown("")
ui.timer(0.05, lambda: chunks and (md.set_content(md.content + "".join(chunks)), chunks.clear()))
ui.button("Cancel", on_click=cancel_turn)
if __name__ in {"__main__", "__mp_main__"}:
    ui.run(native=True, reload=False, window_size=(1200, 800))
```

Design notes: text deltas are buffered in a list and flushed by `ui.timer(0.05)`, so websocket traffic is bounded; `run_coroutine_threadsafe(...).result()` is the blocking bridge. `ui.refreshable` for the sidebar is a simplification (it redraws one label); a real one would keep a dict of agent-id to element.

### 3. Flet 1.0

```python
import asyncio
import ctypes
import threading
import flet as ft


async def main(page: ft.Page):
    loop = asyncio.get_running_loop()
    md = ft.Markdown("", extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                     code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK, selectable=True)
    sidebar = ft.Column()
    state = {"thread": None, "text": ""}

    def on_ui(fn, *args):                          # hop from any engine thread onto Flet's loop
        loop.call_soon_threadsafe(fn, *args)

    def handle(event):                             # TMT stream event (kind, value)
        kind, value = event
        if kind == "text":
            on_ui(append, value)

    def append(chunk):
        state["text"] += chunk
        md.value = state["text"]
        md.update()

    def on_agent_event(name, record):              # AgentManager.subscribe(on_agent_event)
        on_ui(show_agent, record.id, record.task, record.activity)

    def show_agent(agent_id, task, activity):
        sidebar.controls.append(ft.Text("%s %s %s" % (agent_id, task, activity)))
        sidebar.update()

    def ask(title, buttons):                       # engine thread; blocks until clicked
        done, out = threading.Event(), {}

        def open_dialog():
            def pick(value):
                out["v"] = value
                dialog.open = False
                dialog.update()
                done.set()
            dialog = ft.AlertDialog(modal=True, title=ft.Text(title), actions=[
                ft.TextButton(label, on_click=lambda e, v=value: pick(v)) for label, value in buttons])
            page.show_dialog(dialog)               # name per 1.0 API: UNVERIFIED
        on_ui(open_dialog)
        done.wait()
        return out["v"]

    def approve(question, pattern=""):
        return ask(question, [("Allow once", "y"), ("Always", "always"), ("Deny", "n")])

    def choose(text, keys):
        return ask(text, [(str(k), str(k)) for k in keys] + [("Dismiss", "")])

    def cancel(e):
        if state["thread"]:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(state["thread"]), ctypes.py_object(KeyboardInterrupt))

    page.add(md, sidebar, ft.Button("Cancel", on_click=cancel))

ft.run(main)
```

Design notes: the engine must be started from a thread (not awaited on Flet's loop); every UI mutation hops through `loop.call_soon_threadsafe` because the Flet docs only promise safety on the single event loop ([async docs](https://flet.dev/docs/cookbook/async-apps/)).

## Effort estimates (one developer, engine already decoupled from the terminal UI)

| Pick | Estimate | Reasoning |
|---|---|---|
| PySide6 (QTextBrowser + Pygments) | 6-9 weeks | Widgets for chat/prompt/sidebar/settings are standard; time goes into incremental markdown rendering, diff view, approval cards, theming, and packaging/signing. +1-2 weeks if QtWebEngine for richer rendering. Most precedent (PyGPT). |
| NiceGUI native | 4-6 weeks | Fastest for markdown/code/diff (browser engine), async-first dialogs are easy; time goes into thread-to-loop bridging, native window frozen-app bugs, WebView2 checks, and a "Windows-native" feel that will remain web-like. |
| Flet 1.0 | 5-8 weeks | Quick UI, but 1.0 is one week old, threading semantics changed, Markdown control re-render cost for streaming is unmeasured, and diff/code block styling is limited; add risk buffer. |

These are my judgement estimates, not sourced.

## Top 3 picks for this project and why
1. **PySide6**: LGPL, huge download base (~2.7-3.4M/month), best native Windows 11 look (`windows11` style, dark mode, high-DPI), mature thread/signal model that fits worker-thread callbacks, real modal dialogs, proven by PyGPT for streaming LLM chat. Packaging is heavy (~100+ MB) but well understood; use onedir + installer + code signing, or Nuitka standalone.
2. **NiceGUI `native=True`**: best streaming markdown/code/diff rendering for least code, MIT, very active (3.17.1 on 2026-09-18). Cost: not truly native look, WebView2 dependency and frozen-app quirks, 100-130 MB packages.
3. **Flet 1.0**: pretty and fast to build, Apache-2.0, built-in Markdown with code themes; but just hit 1.0 (2026-09-15), threading model changed, size 78-100+ MB. Alternate for slot 3 if native controls matter more: wxPython (native widgets, permissive licence, but no markdown widget). Textual stays the "modernise the TUI" fallback only.

## Third pass additions

### LGPL compliance when shipping PySide6 (PyInstaller / Nuitka)
Not legal advice. Facts from sources:
- PySide6 is offered under LGPL-3.0-only OR GPL-2.0/3.0 ([PyPI](https://pypi.org/project/PySide6/)); you choose LGPL-3.0. Qt's own LGPL page ([doc.qt.io/qt-6/lgpl.html](https://doc.qt.io/qt-6/lgpl.html)) lists the duties under LGPLv3 section 4: (1) prominent notice with each copy that Qt/PySide6 is used; (2) ship copies of the GNU GPL and LGPL texts; (3) either use a suitable shared-library mechanism, i.e. Qt stays as separate dynamic libraries that work with a modified interface-compatible build, or provide Minimal Corresponding Source / object files so users can relink; (4) no terms that block modification of the library and reverse engineering for debugging it (anti-tivoization in v3, which matters if you add DRM or signature checks over Qt DLLs).
- Your own TMT code can stay under any licence as long as the user can swap the Qt libraries. That is why **onedir/standalone is the safe choice**: the Qt DLLs and the `PySide6/` folder sit on disk and can be replaced. The PyInstaller maintainers' discussion says with onedir "DLLs are always on the disk and you can replace them", while a single exe that hides everything is the risky case, though on Windows a user can drop replacement DLLs beside the exe via DLL redirection ([PyInstaller discussion 5499](https://github.com/orgs/pyinstaller/discussions/5499); also [Qt forum thread](https://forum.qt.io/topic/106356/qt-for-python-closed-source-application-as-single-executable)). Onefile extracts to a temp dir at run time, so replacement is possible in principle but awkward to document; avoid it for compliance and for antivirus reasons.
- Nuitka: the Nuitka LGPL issue I found ([#1191](https://github.com/Nuitka/Nuitka/issues/1191)) concerns LGPL *Python* packages being compiled into the exe, not Qt, and gave no Qt guidance (UNVERIFIED for PySide6). Practical rule: use `--standalone` (not onefile), keep PySide6/Qt DLLs as separate files, never statically link Qt.
- Checklist for the installer: a NOTICE file naming Qt and PySide6 with exact versions; LICENSE texts (LGPL-3.0, GPL-3.0); a written offer or link to the Qt/PySide6 source for those versions (PyPI sdist / code.qt.io); a README line saying users may replace the DLLs in the `PySide6` folder with an interface-compatible build; do not modify Qt, or publish the changes if you do. Do not use PyQt6 (GPL) in a proprietary build without Riverbank's commercial licence. QtWebEngine adds Chromium third-party notices (not researched, UNVERIFIED).
- Not found: an authoritative Qt for Python page that says exactly how to comply when freezing. Get a lawyer to review.

### GUI testing strategy that keeps the 2,600-test culture
- **Layer 0, engine tests unchanged.** They are UI-free. The GUI work adds a thin adapter implementing the seams the engine already has (`handle(event)`, `approve`, `choose`, `Transcript(writer=...)`, `AgentManager.subscribe`).
- **Layer 1, adapter/protocol tests (no widgets).** Test the bridge with fake engines: feed `(kind, value)` sequences and assert the view-model; call `approve()` from a thread and resolve it from the test; assert `approve` returns `y/always/n` and `choose` returns key / `""` / `None`; assert listeners survive being called from many threads. Most new GUI tests (say 80%) should live here.
- **Layer 2, framework-level UI tests.**
  - PySide6: `pytest-qt` (`qtbot`) drives real widgets headlessly with `QT_QPA_PLATFORM=offscreen`; supports PyQt5/6 and PySide2/6; the 4.5.x line per [libraries.io](https://libraries.io/pypi/pytest-qt) (release date not verified); `qtbot.waitSignal` / `waitUntil` handle cross-thread signals ([docs](https://pytest-qt.readthedocs.io/en/latest/intro.html)).
  - NiceGUI: the `user` fixture simulates the browser in pure Python, "as fast as unit tests", no Selenium; the `screen` fixture drives headless Chrome through Selenium and chromedriver and is slow ([NiceGUI testing](https://nicegui.io/documentation/section_testing)). Neither covers the native pywebview window (docs are silent), so test the web app, not the shell. There is some `user` fixture configuration friction in NiceGUI 3 ([#5219](https://github.com/zauberzeug/nicegui/discussions/5219)).
  - Flet: `flet test` with the `flet_app` fixture, pytest-based, finds controls by key or text, taps, asserts; needs the Flutter SDK and runs the real app per test; screenshots are "not available on desktop" ([Flet testing](https://flet.dev/docs/getting-started/integration-testing/)). Heavier than the other two.
  - Web shells in general: Playwright can drive the NiceGUI URL (not the window); I did not fetch its current release data.
- **Layer 3, 5-10 smoke tests on the packaged app.** FlaUI (UIA3, .NET; Katalon 10.3.0 replaced WinAppDriver with a FlaUI-based driver per [Katalon docs](https://docs.katalon.com/katalon-studio/manage-projects/set-up-projects/windows-desktop-apps-testing/desktop-applications-testing-with-flaui-driver-in-katalon-studio)) or Appium's Windows driver. **WinAppDriver status: not formally archived, but no commits since April 2025, last release v1.2.1 (2020-11), about 1,155 open issues, maintainers unresponsive** ([issue 1371](https://github.com/microsoft/WinAppDriver/issues/1371) and search summary); treat it as abandoned. Appium's Windows driver still relies on WinAppDriver underneath per the same summary; the [FlaUI WebDriver](https://github.com/FlaUI/FlaUI.WebDriver) is the live alternative. `pywinauto` (UIA backend) is the Python option (not researched, UNVERIFIED).
- CI: run Layer 2 on a Windows runner; offscreen Qt needs no display.

### Accessibility (screen readers, keyboard)
- **PySide6 / Qt Widgets**: Qt exposes UI Automation on Windows since 5.11 (replacing MSAA), so Narrator/NVDA/JAWS see standard widgets ([Qt accessibility](https://doc.qt.io/qt-5/accessible.html); NVDA teething problems in [issue 8604](https://github.com/nvaccess/nvda/issues/8604)). Weak spot: `QTextEdit`/rich text is poorly accessible to NVDA; a third-party `cwidgets` library exists ([DEV post](https://dev.to/hedi_douz/qtextedit-is-finally-accessible-in-qt-pyqt6-pyside6-270e)). A transcript built from many labels or a QTextBrowser needs explicit `setAccessibleName`, and streamed text is not announced by default (my inference; needs QAccessibleEvents). Keyboard navigation and focus order are first-class.
- **NiceGUI + WebView2**: content is a web page, so ARIA and tab order work like a site; NiceGUI has added aria labels and `ui.skip_link` ([releases](https://github.com/zauberzeug/nicegui/releases), search summary). But WebView2 hosted in native shells has had reports of being unreadable by NVDA/JAWS ([WebView2Feedback #2330](https://github.com/MicrosoftEdge/WebView2Feedback/issues/2330); age and pywebview applicability not verified). Use `aria-live="polite"` on the streaming region and throttle updates.
- **Flet**: Flutter semantics tree; Flet's docs say JAWS and NVDA are supported on Windows, with `semantics_label`, `tooltip` and `label` to fill gaps ([Flet accessibility](https://flet.dev/docs/cookbook/accessibility/)). Other Flutter Windows apps have had NVDA problems ([FlClash #1576](https://github.com/chen08209/FlClash/issues/1576)), so test it.
- Ranking (my judgement; none tested): PySide6 Widgets > NiceGUI > Flet. Plan a manual NVDA + Narrator pass whichever is chosen.

### Windows on ARM64
- **PySide6**: ARM64 wheels were a Technical Preview in 6.9 ([Qt blog](https://www.qt.io/blog/qt-for-python-release-6.9)); PySide6-Essentials and Addons 6.11.2 list `win_arm64` wheels ([PyPI](https://pypi.org/project/PySide6-Essentials/)). QtWebEngine on ARM64: not verified.
- **Python**: official ARM64 Windows installer since 3.11 ([python.org](https://www.python.org/downloads/windows/)); TMT's "3.8+" floor means native ARM64 needs 3.11+ (x64 Python also runs under emulation).
- **PyInstaller**: native ARM64 since 5.7.0 (ARM64 Python plus ARM64 bootloader); an emulation-detection bug was fixed in 6.5.0 ([changelog](https://pyinstaller.org/en/v6.7.0/CHANGES.html), [issue 7257](https://github.com/pyinstaller/pyinstaller/issues/7257)). **Nuitka**: ARM64 native from Python 3.11; standalone dependency analysis added in 2.7 via pefile (search summary; verify on the current Nuitka).
- **Flet/Flutter**: Flutter's Windows ARM64 target was still tracked as an open issue ([flutter #62597](https://github.com/flutter/flutter/issues/62597), [#179777](https://github.com/flutter/flutter/issues/179777)); nothing found on Flet ARM64 (UNVERIFIED). x64 builds run under Windows' emulation.
- **NiceGUI**: pure Python plus WebView2 (ARM64 availability not verified).
- Advice: ship x64 first (runs on ARM via emulation); add native ARM64 later, most easily with PySide6 + PyInstaller on an ARM64 CI runner.

### Crash reporting and opt-in telemetry
- Local first: `faulthandler.enable(file=<log>)` (stdlib) for hard crashes including native Qt/C-extension faults; `sys.excepthook` and `threading.excepthook` to write a crash file with TMT version and OS (not prompts, paths or API keys); `qInstallMessageHandler` for Qt warnings. The Python SDK does not produce minidumps for a Python process (my understanding, UNVERIFIED), so faulthandler is the realistic native-crash tool.
- Sentry Python SDK: does not send PII by default (`send_default_pii=False`); `before_send` can drop or scrub events ([options](https://docs.sentry.io/platforms/python/configuration/options/), [scrubbing](https://docs.sentry.io/platforms/python/data-management/sensitive-data/)). Desktop apps that enabled Sentry by default have drawn privacy complaints because the IP address is sent ([hyperwhisper #712](https://github.com/ray-amjad/hyperwhisper-app/issues/712)). Recommended: do not initialise the SDK until the user opts in; keep `send_default_pii=False`, `include_local_variables=False`, minimal breadcrumbs, and a `before_send` that strips source lines and any path containing the user name; never attach transcripts or prompts.
- TMT is a coding agent that sees source and secrets, so send only counts and exception types. Provide a settings toggle and honour an env var such as `TMT_NO_TELEMETRY`. Alternative with no third party: write a local report and let the user attach it to an issue.

### Status
Sketches were rewritten against TMT's real code and syntax-checked with `py_compile` (not run). Items still UNVERIFIED are marked inline.


## Engine change required: cancellation

### What the code does today (read from the repo)
- **Foreground turn**: the only stop is Ctrl-C. `_session_loop` wraps the round loop in `except KeyboardInterrupt:` (`TMT.py` about line 2191) which calls `relay.abort()`, `live_ui.stop()`, prints "Task cancelled" and sets `turn_state["outcome"] = "you stopped it with Ctrl-C"`. CI mode catches the same exception (`TMT.py:1088`). `choose` re-raises Ctrl-C as `KeyboardInterrupt` (`TMT.py:619`) "the exception the session loop already knows how to end a turn on".
- **Background workers** (`agent_worker.py`): `record.cancel` is a `threading.Event`; `_guard(record)` (line 357) raises `WorkerCancelled` and is called on three boundaries: before each step, in the stream sink (`_StreamSink.handle`, the "second cancellation boundary"), and inside a batch before each action. `agent_worker.py` documents the limits honestly: a request already in flight keeps arriving, but "a streamed one does stop being read". `ask_model` catches `except Exception` around the stream (`agent_model.py` about line 636), so a `WorkerCancelled` raised from the stream sink can be swallowed and turned into a fabricated reply; the step loop's own check catches the flag afterwards, so the guarantee does not depend on the exception escaping.
- **Where the stream is read**: `agent_providers.stream_completion` -> `_send(..., stream=True)` -> `iter_sse(response)` -> `response.iter_lines()` (requests; urllib fallback at `agent_providers.py:233` for non-streamed GETs), with `_close(response)` in a `finally`. `agent_model._legacy_openrouter_stream` is the older direct reader (timeout=120). So there is already one place that closes the response: the generator's `finally` runs when the consumer stops iterating or the generator is closed.

### Why the stop-gap `PyThreadState_SetAsyncExc` is not enough
- It only lands at a bytecode boundary: it "cannot interrupt a thread blocked in a C-level call (time.sleep, socket.recv, select.select, Lock.acquire, etc.)" ([CPython issue 144748](https://github.com/python/cpython/issues/144748), search summary of the same). The engine thread spends most of a turn blocked in `socket.recv` waiting for tokens, or in `done.wait()` for an approval dialog, exactly where it does nothing.
- It can fire at any bytecode, including inside a `with lock:` or in a `finally` of the bash sandbox, leaving locks held or state half-written ([discussion on deadlocks](https://discuss.python.org/t/is-there-a-safe-way-to-use-pythreadstate-setasyncexc-without-causing-deadlocks/106637)); with a ~20-agent manager and a shared `AgentManager._lock` that is a real risk.
- Sentinel behaviour must also cover the case where the exception is caught by `except Exception` blocks (it is a `KeyboardInterrupt` in my sketch so it escapes those, which is the one thing it has going for it).
- Verdict: use only as a development stop-gap; ship a cooperative token.

### Aborting an in-flight streaming response from another thread (sources)
- `requests`: calling `response.close()` from another thread does **not** promptly interrupt a thread blocked in `iter_lines()`; in the reproduction, `close()` blocked until the server sent the next packet, about 3 seconds later ([psf/requests #5372](https://github.com/psf/requests/issues/5372); I could not read the comments for a fix, UNVERIFIED whether resolved). Reported workaround pattern: signal with a `threading.Event`, and call `response.close()` in a `finally` ([search summary](https://community.openai.com/t/interrupting-completion-stream-in-python/30628)).
- So there are three layers, in order of reliability: (1) **cooperative check on each chunk** (works as soon as the next SSE line arrives; models stream tokens at tens per second, so delay is typically well under a second except during long "thinking" gaps); (2) **a short read timeout** (e.g. `timeout=(10, 5)` connect/read, retrying on `ReadTimeout` while the token is clear) so the consumer wakes at least every few seconds even when the server stalls; (3) **forceful socket shutdown from the cancelling thread**: `sock.shutdown(socket.SHUT_RDWR)` on the underlying socket makes the blocked `recv` return immediately. Getting at the socket through requests is version-dependent (`response.raw._fp.fp.raw._sock` or `response.raw._connection.sock`; private attributes, UNVERIFIED across urllib3 2.x), so treat layer 3 as best-effort and wrap in try/except. For urllib the response object wraps `http.client.HTTPResponse` whose `fp.raw._sock` is the same kind of handle (private, UNVERIFIED). `httpx` has per-request cancellation by task cancellation (async) but TMT is stdlib + optional `requests`, so I would not add it.
- Not found: an authoritative Python doc stating a supported API for aborting a blocked stream from another thread. This is a known gap.

### Proposed minimal engine change
Design: one **`TurnCancelled`** exception that subclasses `KeyboardInterrupt` (so every existing `except KeyboardInterrupt` handler at `TMT.py:1088` and `~2191` works unchanged and `except Exception` blocks such as the one in `_ask_model_streaming` do not swallow it) plus one cancel token the GUI can set.

1. **New small module or a few lines in `agent_manager.py`/`agent_ui.py`**: `class TurnCancelled(KeyboardInterrupt)` and `class CancelToken` wrapping a `threading.Event` with `.cancel()`, `.is_set()`, `.check()` (raises `TurnCancelled`), and `.on_cancel(callable)` to register closers (the open response).
2. **`TMT.py` `_session_loop`** (round loop around lines 1845-2191): create `token = CancelToken()` per turn, expose it on `session` (e.g. `session.cancel_token`), call `token.check()` at the same three boundaries the worker uses: top of each round, after `ask_model` returns, and before each `execute_action`. No other change: the existing `except KeyboardInterrupt` block already does the cleanup and sets the outcome (adjust the outcome string to "you cancelled it" for `TurnCancelled`).
3. **`stream_handler`** (`TMT.py:81`): call `token.check()` at the top of `handle(event)`, mirroring `_StreamSink.handle`. This is the cheapest per-chunk check and sits on the stream path already.
4. **`agent_model.ask_model` / `_ask_model_streaming`** (`agent_model.py:652`, `589`): accept an optional `cancel=None` and pass it down; in the streaming loop, before iterating, register `token.on_cancel(response.close)` (or the socket shutdown) so a cancel from the GUI thread closes the connection; add `except TurnCancelled: raise` ahead of `except Exception` (not strictly needed if it subclasses `KeyboardInterrupt`, but keeps intent explicit).
5. **`agent_providers.stream_completion`** (line 1283): take `cancel=None`; check it inside the `for event in iter_sse(response)` loop and register the response with the token; the existing `finally: _close(response)` already releases it. Optionally use a `(connect, read)` timeout tuple so a stalled stream wakes up.
6. **Blocking dialogs**: `approve`/`choose` in the GUI adapter should wait in a loop (`done.wait(0.2)`) and return `""`/`False`/`None` (deny) if the token is set, so Cancel also dismisses a pending approval; the engine then hits `check()`.
7. **Running commands** (`agent_bash` subprocess): Cancel should also terminate the child process tree; the existing comment at `TMT.py:1819` says the interrupt "cannot interrupt a command already running" so this needs the sandbox to poll the token (UNVERIFIED how `agent_bash` waits; I did not read it).
8. **Background agents** need no change: `AgentManager.kill(id)` already sets `record.cancel`.

Effort: about 2-4 days including tests (mirror the worker's cancellation tests: a fake stream that blocks, set the token, assert prompt exit and that nothing is dispatched afterwards). Risk: low, because the new exception reuses the existing KeyboardInterrupt path.

## Verified locally (PySide6)

Measured 2026-10-01 on this machine (Windows 11), venv with Python 3.12.8, PySide6 6.11.2 and PyInstaller from PyPI. Files in `scratchpad\pyside-poc\` (`app.py`, `build_full.log`, `build_slim.log`); not in the TMT repo.

- **App.** `app.py` spawns the existing engine stub (`dotnet-poc\engine_stub.py`, NDJSON JSON-RPC 2.0) from a worker `QThread` that reads the pipe and emits Qt signals; the GUI thread appends `stream.text` to a `QTextBrowser` via `setMarkdown`; an `approval.request` opens a `QDialog` with Allow once / Always / Deny, which a `QTimer.singleShot(200)` "clicks" Always in self-test mode; the reply goes back to the engine; a second slow turn is ended by the Cancel button (clicked by a timer 400 ms in), which sends the `turn.cancel` notification; the app then closes the engine's stdin and quits.
- **Run with `QT_QPA_PLATFORM=offscreen` (no window shown): PASS, exit code 0.** Event sequence: text `Hello`, approval `always`, text `approved=always`, reply 1 `{"status":"done","answer":"always"}`, text `tick0` to `tick6`, cancel, reply 2 `{"status":"cancelled"}` with the text `[cancelled]` appended, engine exit code 0 (14 events). So the worker-thread to signal to GUI-thread pattern, the blocking engine-side approval (the engine waited while the dialog ran) and cooperative cancel all work with PySide6 against the same engine stub that the C# client was tested with.
- **PyInstaller `--onedir --noconsole`** (build about 90 s each, run in parallel): **116 MB in 163 files** for the plain build; **108 MB in 156 files** with `--exclude-module` for QtWebEngine, QtQml, QtQuick, QtMultimedia, Qt3D, QtCharts, QtPdf, QtSql, QtNetwork, QtOpenGL and tkinter (PyInstaller's hooks already leave out Qt modules the app never imports, so the explicit excludes saved only about 8 MB). The largest single files under `_internal\PySide6` are `opengl32sw.dll` (20 MB, a software OpenGL fallback that might be removable; untested), `Qt6Gui.dll` and `Qt6Core.dll` (10 MB each), the translations (7 MB) and `Qt6Widgets.dll` (7 MB).
- **Both frozen builds were run with the same offscreen self-test** (using the venv Python as the engine interpreter, because the frozen exe cannot run `engine_stub.py` itself) and printed PASS with exit code 0.
- **Not tested:** a visible window, the real Windows 11 style, DPI, `QtWebEngine` rendering, real markdown streaming throttling (the sketch re-parses the whole buffer on every chunk), antivirus behaviour of the frozen build, installer size, the real `TMT.py` engine in-process, and Nuitka. This is a stub engine over a pipe, not TMT's in-process callbacks; the in-process variant is the sketch in "How TMT maps onto the top picks" (syntax-checked only).
- **Reading for the report:** the PySide6 route is confirmed workable and about 108-116 MB uncompressed for a trivial app, versus the measured 24.8 MB frozen real TMT engine alone and the 94.5 MB self-contained WPF + engine installer (from `30`); an Inno Setup installer would compress the Qt folder roughly 2x (an assumption from Velopack's measured 2.1x, UNVERIFIED for Qt).
