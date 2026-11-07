# tmt-tui

The optional TypeScript front end for the TMT CLI. The Python UI stays the
default; see `docs/typescript-ui-design.md`. It owns the terminal: the live
frame pinned to the bottom, the input field in it, and the client for the
Python backend (`TMT.py --backend-stdio`, protocol in `docs/tui-protocol.md`).
It draws with Ink and has a look of its own, the signal rail: a two-column
gutter down the left of every block, its glyph saying who speaks and its colour
the state (red failed, orange needs you, yellow warns, lime works, green done).
Tool calls are `│ Name target` with their result under the same rail, the answer
is on a heavy `┃` rail and the user's own task on a double `║` one; a status
row says what the model is doing, the plan is a checklist, the composer is one
coloured bar `▌` and the field (no rules, no box), and approvals are dialogs
under a heavy `━` rule. The sections below say how.

## Build and test

```
cd tui
npm install
npm run build       # tsc -> tui/dist (git-ignored); dist/main.js is the entry
npm run typecheck   # tsc --noEmit
npm test            # vitest: unit tests plus @xterm/headless emulator tests
```

`vitest.config.ts` runs the screen tests (`frame`, `live`, `look`, `mouse`)
twice: in the `tui` project against the hand-written renderer in `src/live`,
and in the `ink` project against the Ink screen the app uses
(`test/screens.ts` picks one). They are about the terminal's behaviour (the
scrollback, the resizes, the keys, the field, the questions), which the two
share; what each draws in the live frame differs (the Python UI's look, the
signal rail), so the scenarios about the Python look are skipped on the
Ink screen (`INK` in `test/screens.ts`) and the Ink screen's look has its own
tests: `ink-frame` (the frame as a pure function), `ink-look` (in a terminal),
`components` (the committed blocks at 40, 80 and 120 columns, in colour and
not), `markdown-blocks`, `app-tools` (the tool events), and `turn`, which
streams a scripted turn (`test/turn.ts`) through the app at 80x24, 40x12,
120x40 and 200x50. With `TMT_CAPTURE_DIR` set, `turn.test.ts` also writes the
screen at the approval, mid-turn and at the end as plain text.
`test/ink-vs-live.test.ts` drives both screens side by side through everything
that is not the look and compares what the terminal holds after every step (the
Python box read as the composer, blank rows left out).

## Entry point and flow

`agent_tsui.launch` runs `node tui/dist/main.js <original arguments>` with the
terminal inherited and `TMT_PYTHON` / `TMT_ENTRY` set (run by hand from a
checkout they default to `python` and `../../TMT.py`). `src/main.ts` only
passes the real process streams to `runApp` (`src/app.ts`) and exits with its
result, so a test can call `runApp` with a headless terminal and a fake backend.

| File | What |
|---|---|
| `src/main.ts` | The entry: real streams in, `process.exit(code)` out |
| `src/app.ts` | `runApp`: the flow below, and every backend event's handler |
| `src/backend.ts` | `Backend`: spawns `<python> <entry> --backend-stdio <args>`, JSON lines both ways, typed send helpers, stderr appended to `<install dir>/logs/tui-backend.log` (git-ignored) |
| `src/format.ts` | Pure: what a tool call, the status row and a dialog say (`toolActivity`, `approvalTitle`, `approvalEcho`, `elapsedText`, `tokenCount`), the box hints, a command's rows |
| `src/markdown.ts` | The answer's markdown. `renderMarkdown(text, theme, width)` lays the blocks out for the signal rail look; `markdownLines` is the first reader the hand-written renderer's reply box uses |
| `src/protocol.ts` | The wire contract as types |
| `src/theme.ts` | The colours: the Python UI's gradient and dim grey, and the state roles `makeTheme` paints them as (`success` green, `error` red, `warning` yellow, `working` lime, `needsYou` orange, `ref` the one cool hue for code, links and paths, the diff tints, dim as the terminal's own faint). Truecolor where `COLORTERM` says `truecolor`/`24bit` or `WT_SESSION` is set, the nearest of the 256 otherwise, none under `NO_COLOR` |
| `src/ink/screen.ts` | `createInkSessionScreen`, the session screen the app uses: state, keys (read from stdin here, not by Ink), the queue, `ask`, terminal modes |
| `src/ink/terminal.tsx` | `InkTerminal`: the Ink instance and the stdout it is given, the pad, the resize, suspend and stop |
| `src/ink/view.tsx` | The Ink tree: `<Static>` for committed rows, the live frame's rows under it |
| `src/ink/layout.ts` | Pure: `composeFrame`, which gives each region of the live frame its rows |
| `src/ink/live.ts` | Pure: the rows of the live regions (status row, running call, plan, agents, notes, reply, footer) |
| `src/ink/components/` | The blocks of the record as Ink components: `ToolCall`, `Task`, `Answer`, `Notice`, `Dialog`, `Header`. Each is rows laid out for a width, built by a pure function next to it and handed to Ink by `Rows` |
| `src/live/` | The hand-written ANSI renderer the Ink screen replaced, kept for comparison until it is removed; `rows.ts` (the row helpers) is shared |
| `test/fixtures/fake-backend.mjs` | A scripted stand-in for the backend, plain Node |

1. **Prelude.** `<python> <entry> --tui-prelude <args>` runs the Python splash,
   menu, key entry and git identity on the real terminal, before any screen
   exists (injectable as `runPrelude`). Exit 0 starts; 10 (Exit) ends with 0;
   2 (workspace refused) ends with 2; anything else is an error, exit 1.
2. **Backend.** Spawned, then `hello` with the terminal size. Nothing is drawn
   until `ready` arrives, because the saved mouse setting is in it and the
   screen takes it at creation. A backend that cannot start, or ends before
   `ready`, is reported on stderr (exit 1). `resize` goes on every resize.
3. **Events.** Handled one at a time, in order. A `command` with `to_menu`
   suspends the screen, runs the prelude on the terminal, resumes, and sends
   `reload` (exit 10 from it sends `quit`); the events behind it wait.
   The record is drawn from the events' structured fields; the Python `rows`
   are used only by a `command` and by an event that has none of the fields to
   draw it from (see "Rendered rows"). How each event looks:

   | Event | In the record | In the frame |
   |---|---|---|
   | `session` | three dim rows on a thin `│` rail: `TMT <version>`, `workspace`, `tip` | |
   | `task` | a blank row, then the text on a double `║` rail, in the terminal's own colours (no background, no marker) | |
   | `tool_start` | | a `│ Name target` row above the status row, the rail lime and steady (nothing blinks); the status row says `Running npm test…`, `Reading parser.py…`, `Editing app.ts…` |
   | `tool_end`, then its `event` | one block on a thin `│` rail, green (red if it raised, was interrupted or a command exited non-zero): `│ **Name** target`, then the event's sentence, then the facts: `Added N lines, removed M lines`, `Wrote N lines`, `Read N lines`, `exit S` (red when not 0), files, bytes, calls, all dim. Three lines of a long result, then `… +N lines` | |
   | `tool_end` `raised` / `interrupted` | the same block on a red rail with the `error` in red (`Interrupted` when it has none) | |
   | `event` of another kind | one row: `·` dim progress, `◆` milestone in the reference hue, `!` yellow warning, `✓` green success, `✗` red error. It also ends the wait for a call's result: the call is committed bare, first | |
   | `delta`, `reply_reset` | | the reply as markdown on a heavy lime `┃` rail, its last ten rows |
   | `final` | a blank row, the markdown on a heavy green `┃` rail, a blank row | the reply is gone |
   | `status`, `meter` | | the status row `┃ <what> (6s · ↓ 48 tokens · esc to interrupt)` and the counts in the footer |
   | `plan` | | while a turn runs, on a dim `│` rail: `○` waiting, `◐` in progress (lime), `◉` done (dim, struck through), `!` blocked, six rows at most and `… +N more`, under the status row |
   | `review`, `verify` | a row when one ends (`✓` passed, `!` warnings, `✗` failed), once | a `·` row while it runs |
   | `agents` | a row when an agent stops | `⎇ 1 worker  running  Reading parser.py` while one works, the glyph lime |
   | `approval`, `question` | a dialog (a heavy orange `━` rule, then the title, the text and the keys on an orange `┃` rail), then what was chosen on the same rail, dim | the box asks, its bar orange |
   | `notice` | `✗` red for an error, `!` yellow for a warning, plain for information | |
   | `command` | the backend's rows, verbatim | |
   | `panel` | | nothing: the plan is drawn from `plan`, one column |
   | `prompt`, `turn_end` | `turn_end` commits a dim `outcome` when nothing else said why the turn ended | the placeholder, the caption and counts in the footer |

   What the status row says is what is known: the question that waits
   (`Waiting for your approval`), else the call that runs (`toolActivity`:
   a name and its target, not a random verb), else `Reviewing…` / `Verifying…`
   / `Finalizing…` when the backend's `status.label` or a `review`/`verify`
   state says so, else `Writing the reply…` once the reply streams, else
   `Thinking…` (and `Working…` for a slash command, which has no turn).
   The reply (`delta`) is only drawn live, its last ten rows, and cleared by
   `final`, which commits the answer once: committing streamed lines would
   print them twice and cannot be taken back by `reply_reset`.
4. **Keys.** A submit turns the screen busy at once and goes out as `submit`;
   lines typed meanwhile queue and go out after the next `prompt`. Ctrl-C
   during a turn or a command sends `interrupt`; between prompts it sends
   `quit` (a second press, or 3 s without an `exit`, takes the screen down).
   Ctrl-D between prompts is the same as Ctrl-C; during a turn it does nothing.
5. **Approvals and questions** use `screen.ask` (below). An approval's answer
   is the typed key (any case), the last choice (the refusal) for Esc or an
   empty line, and anything else as typed. A question takes ONE key it offered,
   at once and with no Enter, or `""` for Esc; any other key is ignored (the
   Python UI reads a question the same way). Ctrl-C during either sends only
   `interrupt`; the backend abandons it.
6. **End.** `exit` stops the screen and ends with its `code`. A backend that
   ends without `exit` is an error row above the box with the last ten lines
   of the log, and exit 1. The terminal is restored on every path.

### Rendered rows

`session`, `task`, `event`, `command`, `final`, `notice`, `approval` and
`question` may carry `rows: string[]`: what the Python UI would print for the
event, laid out by its own renderer for the width the front end reported. The
signal rail look is drawn from the structured fields instead (the `text`, the
`message` and `detail`, the `choices`), so those rows are ignored for every
event that has them. They are committed verbatim (no wrapping, no markdown, no
blank row added) when the event has none of the fields to draw it from: a
`task` without `text`, a `notice` without `text`, an `event` without `message`,
a `final` without `text`, an `approval` or a `question` without `question` /
`text` and choices. A `command` has only rows (its older, object rows are
formatted by `format.ts` as before), and its rows are always committed
verbatim: `rows` that is not a non-empty list of strings is the older kind.

## The session screen

```ts
import { createInkSessionScreen } from './index.js';

const screen = createInkSessionScreen({
  stdin: process.stdin,
  stdout: process.stdout,
  mouseCaret: ready.mouse === 'on', // the saved .tmt_mouse value; default off
});

screen.onSubmit((line) => {           // folded pastes already expanded
  screen.setBusy(true);               // call it HERE, synchronously, if the line starts a turn
  backend.submit(line);
});
screen.onInterrupt((key) => {         // 'ctrl-c' | 'ctrl-d'
  if (working) backend.interrupt(); else backend.quit();
});
// on an event: screen.commit(row), screen.setReply(tail), screen.setProgress(...) ...
// on the next `prompt`: screen.setBusy(false)
const key = await screen.ask({ hint: 'Type y and Enter' }); // an approval
```

| Call | What it does |
|---|---|
| `commit(text)` | Writes `text` into the terminal's scrollback above the frame, once, never redrawn. One row per line; a final newline adds none; `''` is one blank line. Colour (SGR) is kept; every other escape and control character is stripped, so output cannot erase or narrow the scrollback. |
| `commitNode(node)` | The Ink screen only: an Ink component, rendered once at the window's width and committed like `commit` (measured, kept, never redrawn; a blank last row is kept). |
| `setReply(text)` | The reply in progress, drawn live above the status row as its markdown on a heavy lime `┃` rail (the same rows `final` commits, on a green one; its last 10 rows at most, fewer when the window is short). Keep only the tail here: the live frame should stay small, because a frame taller than a window that is then made shorter leaves rows in the scrollback. |
| `setStatus(text)` | One plain row under the box, which takes the footer's place. `''` removes it. |
| `setProgress(progress)` | The status row above the box while a turn runs: `{ state, label, percent, since, tokens, waiting? }`, `null` removes it. It is `┃ <label>… (6s · ↓ 48 tokens · esc to interrupt)`: the heavy rail cell takes the colour of the shared gradient cycle (red to green and back over 2.4 s; orange and still while `waiting`), the words are the terminal's own colour, the brackets dim. `label` is what is happening (the app says it); `percent` is ignored. `since` is ms on the screen's clock. `waiting` leaves the interrupt hint out (the user is being asked something). The suffix gives up the hint, then the tokens, then the clock as the window narrows. While a turn runs or a row is set the frame is recomposed every 100 ms (an unchanged frame is not written; the composer's bar moves with the same cycle); the timer is cleared with them, on `suspend()` and on `stop()`. |
| `setLive(live)` | `{ calls, plan, agents, notes }`, the rest of what is drawn around the status row: the running tool calls (`│ Name target`, the rail lime and steady), the plan as a checklist on a dim rail, the working agents and the reviews that run, one row each. Text from the model and the backend has every escape stripped. |
| `setMeter(meter)` | The counts (the `meter` event's fields), in the footer after the hint: `+0 lines, -0 lines, ~14k context, ~48 out` (shorter forms when narrow; nothing until something is counted). `+N` is green, `-N` red. |
| `setCaption(caption)` | Between turns, the right end of the footer: `{ clock, provider, model }` as `09:30:00 · OpenRouter · Model` in dim, the narrowest of three forms that fits; or a string taken as it is. `''` removes it. Not drawn while a turn runs. |
| `setPanel(panel)` | The right-hand column the Python UI draws: `{ rows, width }`, rows styled (SGR kept, anything else stripped), beside the live area, flush with the top. The app does not call it (the plan is drawn from `plan`); it is kept for the contract the hand-written renderer shares. `null` removes it. |
| `setPlaceholder(text)` | Shadow text in an empty, untouched box between turns. Dismissed by the first character, back after each submit. |
| `setBusy(bool)` | A turn is running. While busy, Enter queues the line; the box stays empty, and the footer says `esc/ctrl-c to interrupt · enter queues the next task` and, once lines wait, `· 2 queued` (the lines themselves are not drawn). `setBusy(false)` sends the queue: one line per turn if each handler calls `setBusy(true)`, all of them if none does. |
| `ask({ hint, keys, footer })` | Ask one question in the box; resolves with the line. The next Enter (an empty line included) answers it, bypasses the busy queue and never reaches `onSubmit`. Esc, Ctrl-C, Ctrl-D and `stop()` resolve `null` (Ctrl-C and Ctrl-D still reach `onInterrupt`, after the question is closed). `hint` is drawn like the placeholder in an empty box, and `footer` replaces the footer while it is open. With `keys` (a non-empty list) it takes ONE key instead: a typed character that is one of them (exact match) resolves it at once with that character, no Enter, nothing put in the box; Esc resolves `null`; other characters, a paste and Enter are ignored. A draft already typed is set aside and comes back afterwards; queued lines stay queued and are not sent while it is open. A second ask replaces the first (which resolves `null`). |
| `onSubmit(cb)` / `onInterrupt(cb)` | Subscribe; each returns an unsubscribe function. `onInterrupt` gets `'ctrl-c'`, `'ctrl-d'` or `'escape'`. |
| `queued()` | The waiting lines, oldest first, expanded. |
| `suspend()` / `resume()` | Hand the terminal to something else (a delegated Python screen, an Ink screen) and take it back. Suspend writes pending commits, erases the frame, and turns raw mode, bracketed paste and mouse off; resume starts a fresh screen. |
| `stop()` | Writes pending commits, erases the frame, restores the terminal. |

Do not render anything else to the same stdout, or read the same stdin,
while the screen is live: suspend it first.

### Keys

Enter submits (queues while busy; an empty line is ignored). Esc clears the
line; while a turn runs and the line is empty it goes to `onInterrupt` as
`'escape'` (the footer says `esc/ctrl-c to interrupt`), and an open question
takes it first. Ctrl-C clears a written line between turns; on an empty line,
or at any time during a turn (the typed line is kept), it goes to `onInterrupt`.
Ctrl-D on an empty line goes to `onInterrupt`, otherwise deletes forward.
Left/Right/Home/End, Ctrl-A/E/B/F, Backspace/Delete, Ctrl-U (clear line),
Ctrl-W and Alt-Backspace (delete word). Up/Down and Tab do nothing yet
(history and completion are not part of this lane).

## How it draws

Ink 8 (pinned, with React 19) draws the screen. Two regions, as in the
Python UI (`LiveRegion`, `BottomPad`): committed lines go once into the
terminal's own scrollback through Ink's `<Static>` (a block of the record is an
Ink component rendered to rows once, at the width the window has then, by
`commitNode`), and the live frame under them is the only thing Ink repaints.
Top to bottom it is the reply, a blank row, the running tool call, the status
row, the plan, the notes and the agents, the box (the field behind its bar, no
rules) and the footer. When the window is short the box and the footer are kept, then
the status row, then the call; the reply keeps three rows before the plan
and the notes get any, and at most ten. It is the only thing Ink repaints -- it erases the rows it
drew and writes the frame again, gathered into one write inside
synchronized-output markers. Ink is rendered with `maxFps: 0`, so a paint is
on the terminal when it returns; paints are coalesced to one per turn of the
event loop by the screen, as before. The frame is never taller than
`rows - 1`. The box is on the last rows from the first frame: output fills
the window from the top, and blank rows above the frame (part of what Ink
draws) hold the box on the foot until the output reaches it.

What Ink does not do is kept from the hand-written renderer, in
`InkTerminal`. Ink's own resize handling erases the rows it counted when it
drew them, not the rows the terminal re-wrapped them into, and leaves a stale
status row and half a rule behind; and xterm.js moves a cursor left inside the
frame down for every row that re-wraps below it (pinned by a test), so the
frame cannot be found from a cursor at the caret. So Ink is given a stdout of
its own that never reports a resize, and no cursor position: the terminal's
cursor is hidden and parked at column 0 under the frame (where Ink's output
ends), where nothing below can move it, and the caret is drawn as an inverse
cell. A resize asks the terminal where the cursor is (`\x1b[6n`), erases the
old frame by its re-wrapped height, has Ink forget the frame it drew, and
lets it paint the next one, in one write. No answer in 250 ms: the frame is
assumed to have been on the foot of the window (the old frame stays visible
during the wait). Frame rows a shrink pushed into the scrollback are tracked
and erased if a grow brings them back. The keyboard is read by the screen
(`src/input/keys.ts`); Ink is given an inert stdin and never sees a key.

Never the alternate screen, never a scroll region (DECSTBM), never CSI 3 J:
`test/sources.test.ts` greps the sources for all three, and the pinned Ink's
build for a library clear; the emulator tests assert on the raw bytes written
at 80x24, 40x12 and 160x50, and `test/ink.test.ts` on every byte of a session
streamed through resizes to 40x12 and 200x50 and a shrink mid-stream. Ink's
full-screen path (a frame as tall as the window: erase from the home row,
committed rows on screen included; on Windows whenever either frame is that
tall) is never taken, because the frame is shorter.

## The input field

`src/input/field.ts` holds the three pure functions the Python side has
(`layoutField`, `caretIn`, `visibleField`) plus `caretAt` for a click.
Widths are display widths (`string-width`, by grapheme). One column is kept
for the caret (`inner - 1`). Rows wrap at a word boundary, the break space
staying on the row it ends, so no continuation row starts with a space
(ui-review F12). Five rows, then the window follows the caret. Every
row keeps the composer's bar `▌` (lime between turns, the colour cycle while a
turn runs, orange while a question is open), so a wrapped line is one bar. The
placeholder never wraps. A paste of more than one
line folds to `[Pasted text #N +M lines]` and is expanded on submit; a token
with no stored paste is left as typed. Pastes are detected by bracketed
paste and, as a fallback, by a single read that starts with text and holds a
line break (never read as Enter).

## Known limits

- The hand-written renderer was driven in a real ConPTY (screenshots in
  `output/sim/shots-ts/`, git-ignored), but ConPTY under pyte does not reflow,
  so resize behaviour is verified against xterm.js, not against Windows
  Terminal itself. IL/DL (young-session mode) and cursor save/restore are
  likewise only checked in xterm.js. The Ink screen has only been driven
  through xterm.js so far (where it leaves the terminal holding what the
  hand-written renderer leaves, `test/ink-vs-live.test.ts`).
- A link in the answer is `text (url)` with the text underlined, not an OSC 8
  hyperlink: every committed row goes through `sanitizeCommitLine`, which
  removes OSC sequences so output cannot set a title or worse, and the row
  wrapper does not know a zero-width escape other than colour.
- A result event that is a `warning` (a failed action) is not the call's
  result by the protocol's kinds, so the call is committed bare, its rail
  green, and the `!` row follows it.
- Ink re-spells colour codes: a colour the text closed with a reset
  (`\x1b[0m`) is closed with its own off code (`\x1b[39m`, `\x1b[22m`), the
  same cells either way, and a row's trailing uncoloured spaces are left off.
- A bare one-key question (`ask({ bare: true, keys })`) has a frame of no
  rows; Ink ends even that with a line break, so the hidden cursor waits one
  row lower than with the hand-written renderer. Nothing visible differs.
- Windows Terminal never returns rows a shrink pushed into the scrollback, so
  reply rows stranded by a large mid-turn shrink stay there (pinned by a test).
  A shrink of a young session also leaves blank rows above the header.
- The `/back` menu hand-off (`suspend()`, a Python child on the inherited
  terminal, `resume()`) works with the real splash and a fake menu; it was not
  run to a real menu "start" because no API key is saved on the test machine.
- A terminal that does not re-wrap on resize (X11 xterm) would have a few
  committed rows above the frame erased on a narrower resize.
- The caret is drawn, so an IME's composition window opens at the parked
  cursor, under the box, not at the caret. Ink's cursor API would put the
  terminal's cursor at the caret, but then a narrower resize leaves stale
  rows (see "How it draws").
- Click to place the caret turns on mouse reporting, which takes the wheel
  and plain text selection from the terminal (Shift restores them). Off by
  default for that reason.
