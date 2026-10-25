# TypeScript UI: where it stands and how to continue

Hand-off written 2026-10-25 when the usage limit was reached. Paste "Session prompt" below
into a new Claude Code session in `C:\Coding\TMT`. Read `docs/typescript-ui-design.md`
first (binding decisions and rules), then `docs/tui-protocol.md` (the wire contract).
The original plans are `docs/typescript-ui-handoff.md` and `docs/bottom-input-prompt.md`.

## State: three commits on `alpha`, NOT pushed

| Commit | What |
|---|---|
| `7d4a7e9` | Milestone 1: `.tmt_ui` and `.tmt_mouse` settings, Settings and `/config` rows, `--ui` / `TMT_UI`, hidden `--backend-stdio`, `agent_tsui.launch` (falls back to the Python UI when node < 22, `tui/dist/main.js` is missing, or there is no TTY) |
| `36fc7a9` | `tui/`: custom renderer (not Ink: Ink left stale rows after a resize), pinned input box, wrapping field, paste folding, queued typing during a turn, optional click-to-caret. 95 vitest tests, typecheck and build clean |
| `a39b4ae` | `agent_backend.py` (headless session loop over JSON lines) and `docs/tui-protocol.md`. TMT.py is unchanged by it: it swaps the 14 UI names over TMT's globals for the length of `run()` |

Tests run and green before this hand-off: `test_agent_ui_setting` 45, `test_agent_backend_protocol` 13,
`test_agent_backend_events` 15, `test_agent_cli` 46, plus the related modules the lanes ran.
**The full suite has not been run, so nothing was pushed.** The user wants: push to `alpha`
only when all tests pass.

## Not done (in this order)

1. **Wire the TS session to the backend** (Sonnet is enough). Create `tui/src/main.ts` (the
   entry `agent_tsui.launch` runs as `tui/dist/main.js`; add a `bin`/build step so
   `npm run build` emits it) and a backend client:
   - spawn `TMT_PYTHON TMT_ENTRY --backend-stdio <args>` (env set by `agent_tsui.launch`),
     stdin/stdout pipes, stderr to `logs/tui-backend.log`;
   - send `hello`; handle every event in `docs/tui-protocol.md` and ignore unknown ones;
   - drive `createSessionScreen` from `tui/src/live/session.ts` (API in `tui/README.md`):
     `delta`/`reply_reset` -> `setReply`; `final` and finished blocks -> `commit`; `status`,
     `meter`, `session` -> `setStatus` / `setCaption`; `submit` from `onSubmit`;
     `onInterrupt` -> `interrupt`; `approval` and `question` as a small choice prompt in the
     box; `plan`/`review`/`verify`/`agents`/`text`/`notice` as committed text;
   - `setBusy(true)` must be called synchronously inside the submit handler (queue rule);
   - commit each finished line (a shrinking window leaves rows in scrollback otherwise);
   - **reuse the Python menus:** add a small Python mode (e.g. `--legacy-screen prelude`
     in `agent_backend.py`/`TMT.py`) that runs `run_splash()`, `run_startup(workspace=root)`,
     `ensure_api_key()`, `ensure_git_identity()` on the real terminal and exits 0 (start) or
     10 (exit). The TS UI calls `screen.suspend()`, runs it with inherited stdio, then
     `resume()`, and sends `reload`. This keeps Settings, including the toggle back to
     Python, reachable without porting any menu.
   - remove `ink` and `react` from `tui/package.json` if nothing uses them.
   - extend `tui/test/sources.test.ts`-style greps to the new sources; add a vitest that
     drives main against a fake backend script.
2. **Screenshot run, shown inline in chat** (the user asked for this twice). Drive
   `node tui/dist/main.js` through the ConPTY + pyte driver `output/sim/tui_driver.py`
   (see how `docs/ui-review-2026-10-25.md` used it), at 120x36 and 80x24: the pinned box
   idle, mid-stream with output above, an approval, a queued line, after a resize. Send the
   PNGs with `SendUserFile` (`display: "render"`). This is the first run in a real
   conpty/Windows terminal; the resize re-wrap and cursor-position query are so far only
   proven in xterm.js.
3. **Full suite in a clean clone** (`git clone` into the scratchpad, `PYTHONIOENCODING=utf-8
   python run_tests.py`, about 15 minutes, background it), plus `cd tui && npm ci && npm test`.
   Report failures verbatim. Then **push to `alpha` only if all pass** (explicit
   `git push origin alpha`; check `git log origin/alpha..alpha` first).
4. **Deferred:** native TS splash, main menu and Settings; capture parity against the
   Python UI at 80x24 and a wide size; Up/Down history and Tab completion in the TS input.

## Known facts and traps

- `run_tests.py` has NO options: running it runs everything (one live test makes real API
  calls and the reviewer may approve, failing `test_retire_is_not_an_operation_the_model_can_reach`:
  see the notes file). Run single modules with a script that adds every `testing/` dir to
  `sys.path` and calls each `test_*` function (the first session kept one in its scratchpad).
- Failures already present on a clean HEAD, caused by this machine's `.tmt_context` being
  `off`: `test_agent_launch.test_the_frame_draws_the_state_of_every_other_row_unchanged` and
  7 `test_agent_context_wiring` tests when run after `test_agent_tips_wiring`. The lane
  also reported `test_agent_checkpoint_wiring.test_a_turn_that_writes_leaves_a_checkpoint_labelled_with_the_task`
  failing alone; confirm in a clean clone whether that one is pre-existing.
- `pyproject.toml` lists modules by name: any new Python module must be added to py-modules.
- Backend gaps: `/back` sends `to_menu` and resumes at once (no menu); background agents'
  own actions show only in the `agents` event; a question answered with a key it did not
  offer gets `protocol_error` and keeps waiting; approvals have no deadline.
- `tui/dist` and `tui/node_modules` are git-ignored: `cd tui && npm install && npm run build`
  is required before the toggle can start the TS UI. The npm package (`files: bin/, scripts/`)
  does not ship `tui/`; decide that when the owner flips the default.
- Rules: never touch `app/`; stage explicit paths only; never push unless the owner said
  so (they did say: after all tests pass); do not kill a running `release\win-unpacked\TMT.exe`.
- The unrelated `D assets/TMT_main_icon.png` in `git status` is not ours: do not stage it.

## Session prompt

Continue the TypeScript UI for TMT. Read `docs/typescript-ui-next-session.md`, then
`docs/typescript-ui-design.md` and `docs/tui-protocol.md`. Do "Not done" steps 1 to 3 in
order, delegating building to subagents (Sonnet for the wiring, Haiku for easy lookups),
committing each step with explicit paths, and showing the screenshots inline in chat. Push
to `alpha` only when the full suite and the vitest suite pass. Do not block on questions;
list every default you chose in the final report.
