# tmt-tui

The optional TypeScript front end for the TMT CLI. The Python UI stays the
default; see `docs/typescript-ui-design.md`. This folder currently holds the
session screen (lane 3): the live frame pinned to the bottom of the terminal
and the input field in it. The backend client and `src/main.tsx` come from
lane 4.

## Build and test

```
cd tui
npm install
npm run build       # tsc -> tui/dist (git-ignored)
npm run typecheck   # tsc --noEmit
npm test            # vitest: unit tests plus @xterm/headless emulator tests
```

## The session screen

A sketch of the wiring (the backend event names are lane 4's):

```ts
import { createSessionScreen } from './index.js';

const screen = createSessionScreen({
  stdin: process.stdin,
  stdout: process.stdout,
  mouseCaret: ready.mouse === 'on', // the saved .tmt_mouse value; default off
});

screen.onSubmit((line) => {           // folded pastes already expanded
  screen.commit(`> ${line}`);         // the screen does not echo; you write the record
  screen.setBusy(true);               // call it HERE, synchronously, if the line starts a turn
  backend.send({ type: 'task', text: line });
});
screen.onInterrupt((key) => {         // 'ctrl-c' | 'ctrl-d'
  if (busy) backend.send({ type: 'stop' }); else { screen.stop(); process.exit(0); }
});
backend.on('token', (tail) => screen.setReply(tail));       // the unfinished line(s)
backend.on('line', (line) => screen.commit(line));          // each finished line
backend.on('status', (text) => screen.setStatus(text));
backend.on('done', () => { screen.setReply(''); screen.setBusy(false); });
```

| Call | What it does |
|---|---|
| `commit(text)` | Writes `text` into the terminal's scrollback above the frame, once, never redrawn. One row per line; a final newline adds none; `''` is one blank line. Colour (SGR) is kept; every other escape and control character is stripped, so output cannot erase or narrow the scrollback. |
| `setReply(text)` | The reply in progress, plain text, drawn live above the box (its last 10 rows at most). Commit each finished line and keep only the unfinished tail here: the live frame should stay small, because a frame taller than a window that is then made shorter leaves rows in the scrollback. |
| `setStatus(text)` | One plain row under the box (the instrument row). `''` removes it. |
| `setCaption(text)` | One dim row above the box, right-aligned (clock, provider, model). `''` removes it. |
| `setPlaceholder(text)` | Shadow text in an empty, untouched box between turns. Dismissed by the first character, back after each submit. |
| `setBusy(bool)` | A turn is running. While busy, Enter queues the line; the queued lines are drawn above the box as `queued  <line>` and the empty box says `Working. Type to queue the next task. Ctrl-C to stop.` `setBusy(false)` sends the queue: one line per turn if each handler calls `setBusy(true)`, all of them if none does. |
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
`rows - 1`. Blank rows at the top of the frame hold the box on the last rows
from the first frame while output fills the window from the top.

Ink 8 was the default, and was measured first (an `@xterm/headless` probe).
8.0.0 no longer writes `clearTerminal` (its full-screen path erases from the
home position), so CSI 3 J was not the problem; but after a narrower resize
its frame erase counts the rows it drew, not the rows the terminal re-wrapped
them into, and it left a stale status row and half a rule behind.
xterm.js also moves a cursor left inside the frame down for every row that
re-wraps below it (pinned by a test). So this is a small custom renderer:
the terminal's cursor is hidden and parked at column 0 under the frame,
where nothing below can move it, the caret is drawn as an inverse cell, and
a resize erases the old frame by its re-wrapped height and then asks the
terminal where the cursor is (`\x1b[6n`) to place the next frame. No answer
in 250 ms: the frame is assumed to have been on the foot of the window.

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

- Not yet driven in a real terminal (Windows Terminal/conpty): reflow and the
  cursor position report are verified only against xterm.js.
- A terminal that does not re-wrap on resize (X11 xterm) would have a few
  committed rows above the frame erased on a narrower resize.
- The caret is drawn, so an IME's composition window opens at the parked
  cursor, under the box, not at the caret.
- Click to place the caret turns on mouse reporting, which takes the wheel
  and plain text selection from the terminal (Shift restores them). Off by
  default for that reason.
