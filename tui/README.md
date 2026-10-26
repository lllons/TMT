# tmt-tui

The optional TypeScript front end for the TMT CLI. The Python UI stays the
default; see `docs/typescript-ui-design.md`. It owns the terminal: the live
frame pinned to the bottom, the input field in it, and the client for the
Python backend (`TMT.py --backend-stdio`, protocol in `docs/tui-protocol.md`).

## Build and test

```
cd tui
npm install
npm run build       # tsc -> tui/dist (git-ignored); dist/main.js is the entry
npm run typecheck   # tsc --noEmit
npm test            # vitest: unit tests plus @xterm/headless emulator tests
```

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
| `src/format.ts` | Pure: an event in, committed rows out |
| `src/protocol.ts` | The wire contract as types |
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
   The reply (`delta`) is only drawn live, its last ten rows, and cleared by
   `final`, which commits the answer once: committing streamed lines would
   print them twice and cannot be taken back by `reply_reset`. `task`, `event`,
   `notice`, `command`, `final` and the panels (`plan`, `review`, `verify`,
   `agents`, only when they change) are committed; `status`, `meter` and
   `prompt` set the status row, caption and placeholder.
4. **Keys.** A submit turns the screen busy at once and goes out as `submit`;
   lines typed meanwhile queue and go out after the next `prompt`. Ctrl-C
   during a turn or a command sends `interrupt`; between prompts it sends
   `quit` (a second press, or 3 s without an `exit`, takes the screen down).
   Ctrl-D between prompts is the same as Ctrl-C; during a turn it does nothing.
5. **Approvals and questions** use `screen.ask` (below). An approval's answer
   is the typed key (any case), the last choice (the refusal) for Esc or an
   empty line, and anything else as typed. A question takes a key it offered,
   `""` for Esc or an empty line, and asks again, with a notice, for any other
   key. Ctrl-C during one sends only `interrupt`; the backend abandons it.
6. **End.** `exit` stops the screen and ends with its `code`. A backend that
   ends without `exit` is an error row above the box with the last ten lines
   of the log, and exit 1. The terminal is restored on every path.

## The session screen

```ts
import { createSessionScreen } from './index.js';

const screen = createSessionScreen({
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
// on an event: screen.commit(row), screen.setReply(tail), screen.setStatus(text) ...
// on the next `prompt`: screen.setBusy(false)
const key = await screen.ask({ hint: 'Type y and Enter' }); // an approval
```

| Call | What it does |
|---|---|
| `commit(text)` | Writes `text` into the terminal's scrollback above the frame, once, never redrawn. One row per line; a final newline adds none; `''` is one blank line. Colour (SGR) is kept; every other escape and control character is stripped, so output cannot erase or narrow the scrollback. |
| `setReply(text)` | The reply in progress, plain text, drawn live above the box (its last 10 rows at most). Commit each finished line and keep only the unfinished tail here: the live frame should stay small, because a frame taller than a window that is then made shorter leaves rows in the scrollback. |
| `setStatus(text)` | One plain row under the box (the instrument row). `''` removes it. |
| `setCaption(text)` | One dim row above the box, right-aligned (clock, provider, model). `''` removes it. |
| `setPlaceholder(text)` | Shadow text in an empty, untouched box between turns. Dismissed by the first character, back after each submit. |
| `setBusy(bool)` | A turn is running. While busy, Enter queues the line; the queued lines are drawn above the box as `queued  <line>` and the empty box says `Working. Type to queue the next task. Ctrl-C to stop.` `setBusy(false)` sends the queue: one line per turn if each handler calls `setBusy(true)`, all of them if none does. |
| `ask({ hint })` | Ask one question in the box; resolves with the line. The next Enter (an empty line included) answers it, bypasses the busy queue and never reaches `onSubmit`. Esc, Ctrl-C, Ctrl-D and `stop()` resolve `null` (Ctrl-C and Ctrl-D still reach `onInterrupt`, after the question is closed). `hint` is drawn like the placeholder, over the typing hint, in an empty box. A draft already typed is set aside and comes back afterwards; queued lines stay queued and are not sent while it is open. A second ask replaces the first (which resolves `null`). |
| `onSubmit(cb)` / `onInterrupt(cb)` | Subscribe; each returns an unsubscribe function. |
| `queued()` | The waiting lines, oldest first, expanded. |
| `suspend()` / `resume()` | Hand the terminal to something else (a delegated Python screen, an Ink screen) and take it back. Suspend writes pending commits, erases the frame, and turns raw mode, bracketed paste and mouse off; resume starts a fresh screen. |
| `stop()` | Writes pending commits, erases the frame, restores the terminal. |

Do not render anything else to the same stdout, or read the same stdin,
while the screen is live: suspend it first.

### Keys

Enter submits (queues while busy; an empty line is ignored). Esc clears the
line. Ctrl-C clears a written line between turns; on an empty line, or at
any time during a turn (the typed line is kept), it goes to `onInterrupt`.
Ctrl-D on an empty line goes to `onInterrupt`, otherwise deletes forward.
Left/Right/Home/End, Ctrl-A/E/B/F, Backspace/Delete, Ctrl-U (clear line),
Ctrl-W and Alt-Backspace (delete word). Up/Down and Tab do nothing yet
(history and completion are not part of this lane).

## How it draws, and why not Ink

Two regions, as in the Python UI (`LiveRegion`, `BottomPad`): committed
lines go once into the terminal's own scrollback, and the live frame under
them (reply tail, queued lines, caption, box, status) is the only thing
repainted -- cursor up by its height, rewrite, erase down, all in one write
inside synchronized-output markers. The frame is never taller than
`rows - 1`. The box is on the last rows from the first frame. In a young
session (the conversation does not yet fill the window) the conversation sits
directly on the box and the blank rows are above it: they are deleted at the
top (DL) as output arrives and inserted (IL) when the frame shrinks, so none
reach the scrollback and a shorter window keeps the newest conversation on
screen instead of a blank page. That mode ends once the window fills or is
resized. On a resize the cursor position is asked for first, then the old
frame is erased and the new one painted in one write; frame rows a shrink
pushed into the scrollback are tracked and erased if a grow brings them back.

Ink 8 was the default, and was measured first (an `@xterm/headless` probe).
8.0.0 no longer writes `clearTerminal` (its full-screen path erases from the
home position), so CSI 3 J was not the problem; but after a narrower resize
its frame erase counts the rows it drew, not the rows the terminal re-wrapped
them into, and it left a stale status row and half a rule behind.
xterm.js also moves a cursor left inside the frame down for every row that
re-wraps below it (pinned by a test). So this is a small custom renderer:
the terminal's cursor is hidden and parked at column 0 under the frame,
where nothing below can move it, the caret is drawn as an inverse cell, and
a resize asks the terminal where the cursor is (`\x1b[6n`) and then erases
the old frame by its re-wrapped height and paints the next one in one write.
No answer in 250 ms: the frame is assumed to have been on the foot of the
window (the old frame stays visible during the wait).

Never the alternate screen, never a scroll region (DECSTBM), never CSI 3 J:
`test/sources.test.ts` greps the sources for all three and the emulator
tests assert on the raw bytes written at 80x24, 40x12 and 160x50.

## The input field

`src/input/field.ts` holds the three pure functions the Python side has
(`layoutField`, `caretIn`, `visibleField`) plus `caretAt` for a click.
Widths are display widths (`string-width`, by grapheme). One column is kept
for the caret (`inner - 1`). Rows wrap at a word boundary, the break space
staying on the row it ends, so no continuation row starts with a space
(ui-review F12). Five rows, then the window follows the caret. Only the
first row carries `>`. The placeholder never wraps. A paste of more than one
line folds to `[Pasted text #N +M lines]` and is expanded on submit; a token
with no stored paste is left as typed. Pastes are detected by bracketed
paste and, as a fallback, by a single read that starts with text and holds a
line break (never read as Enter).

## Known limits

- Driven in a real ConPTY (screenshots in `output/sim/shots-ts/`, git-ignored),
  but ConPTY under pyte does not reflow, so resize behaviour is verified against
  xterm.js, not against Windows Terminal itself. IL/DL (young-session mode) and
  cursor save/restore are likewise only checked in xterm.js.
- Windows Terminal never returns rows a shrink pushed into the scrollback, so
  reply rows stranded by a large mid-turn shrink stay there (pinned by a test).
  A shrink of a young session also leaves blank rows above the header.
- The `/back` menu hand-off (`suspend()`, a Python child on the inherited
  terminal, `resume()`) works with the real splash and a fake menu; it was not
  run to a real menu "start" because no API key is saved on the test machine.
- A terminal that does not re-wrap on resize (X11 xterm) would have a few
  committed rows above the frame erased on a narrower resize.
- The caret is drawn, so an IME's composition window opens at the parked
  cursor, under the box, not at the caret.
- Click to place the caret turns on mouse reporting, which takes the wheel
  and plain text selection from the terminal (Shift restores them). Off by
  default for that reason.
