# TypeScript UI: design decisions (2026-10-25)

Decided by the manager before building, so every lane builds to the same defaults.
Sources: `docs/typescript-ui-handoff.md` (the plan and invariants) and
`docs/bottom-input-prompt.md` (the pinned input box). Where this file and the code
disagree, the code is right.

## Non-negotiables

- **The Python UI is not removed or changed.** It is the default and the fallback. The
  TypeScript UI is an alternative the user toggles in Settings (`UI: Python | TypeScript`,
  takes effect on the next start, never a live switch).
- `--ci`, piped and non-TTY runs behave exactly as before, whichever UI is set.
- Never use the alternate screen buffer. Never narrow the scroll region (DECSTBM). Never
  send `\x1b[3J`. These are checked by source greps AND by asserting on the raw bytes the
  TS UI writes at run time (Ink's own `clearTerminal` path emits `\x1b[3J` when a frame is
  as tall as the screen, so the live frame must always be cut to `rows - 1`).
- Never edit or stage anything under `app/`. Never push. Never touch the MSI.
- Stage explicit paths only. **Lanes do not commit; the manager commits.**
- Python is standard library only. Do not add a Python dependency. Node dependencies live
  in `tui/package.json` only.
- Edit with the Edit tool, never rewrite a file with Python `open(..., newline="")`
  (CRLF files). If a tool ever flips line endings, fix it with `git diff --ignore-cr-at-eol`.
- Do not run the full Python suite (about 15 minutes, and one test makes real API calls).
  Run the modules you touched: see `testing/README.md` and `run_tests.py`. If an unrelated
  module fails while another lane is mid-edit, re-run once before investigating.

## Process model

```
tmtcode  ->  python TMT.py main()          (UI setting = typescript, interactive TTY, not --ci)
               agent_tsui.launch(argv)     spawns, inherited stdio:
                 node tui/dist/main.js     OWNS THE TERMINAL: raw keys, rendering
                   spawns: python TMT.py --backend-stdio <args>
                           stdin/stdout = JSON lines (UTF-8), stderr -> logs/tui-backend.log
```

- The Node process owns the terminal. The Python backend never touches the TTY in
  `--backend-stdio` mode: every UI call (`stream_handler`, `_command_approval`,
  `_question_asker`, `finish_response`, plan/review/verify blocks) becomes an event, and
  approvals/answers come back as replies.
- `agent_tsui.launch` returns `None` (and the Python UI runs, after one stderr line saying
  why when stderr is a TTY) if: node is missing or older than 22, `tui/dist/main.js` does
  not exist, stdin/stdout is not a TTY, or `TMT_UI=python` / `--ui python` is given.
  It must never stop TMT starting.
- **Escape hatch so Python is always reachable:** `--ui python|typescript` flag and the
  `TMT_UI` environment variable override the saved setting for one run only. Saved
  setting file: `.tmt_ui` in `INSTALL_DIR` (values `python` | `typescript`, anything else
  or missing = `python`).
- A second saved setting, `.tmt_mouse` (`off` default | `on`), "click to place the caret",
  used by the TS UI only. Same pattern as `.tmt_ui`.
- Backend handshake: the first event the backend sends is `ready`, carrying the saved
  settings the front end needs (reply format, mouse, model, provider, workspace).
- Build: `cd tui && npm install && npm run build` produces `tui/dist/` (git-ignored, as
  `dist/` and `node_modules/` already are). Tests: `cd tui && npm test` (vitest).
- Wire format and event list: `docs/tui-protocol.md` (written by the backend lane).

## Where code lives

| Area | Path | Owner lane |
|---|---|---|
| Settings, dispatch, flags | `agent_config.py`, `agent_menu.py`, `agent_commands.py`, `TMT.py` (`parse_args`, `main`), new `agent_tsui.py` | 1 |
| Headless backend + protocol | new `agent_backend.py`, event hooks in `TMT.py`/`agent_*`, `docs/tui-protocol.md` | 2 |
| Live frame, input field, resize | `tui/src/live/`, `tui/src/input/`, `tui/test/` | 3 |
| Front end wiring, parity screens | `tui/src/` (screens, backend client, `main.tsx`) | 4, 5 |

## Defaults chosen (each is listed in the final report)

- Renderer: Ink 8 + React 19 (what Claude Code uses). If Ink cannot keep the invariants,
  the live-frame lane may replace it with a thin custom renderer and says why.
- Typing during a turn: queued, shown as pending lines above the box, sent when the turn ends.
- Click-to-caret: off by default, behind the `.tmt_mouse` Settings row.
- Screens too complex to port (API key entry, model catalogue, uninstall, update) are
  delegated to the Python screens: the TS UI suspends rendering and raw mode, runs the
  Python screen on the real terminal, then resumes. The report names each delegated screen.
