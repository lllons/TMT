// The bytes. Two regions, as in the Python UI's LiveRegion and BottomPad:
// committed lines are written once into the terminal's own scrollback and
// never redrawn; the live frame under them is the only thing repainted, by
// moving the cursor up by its height, rewriting it, and erasing what is left.
//
// Never the alternate screen, never a scroll region (DECSTBM), never an erase
// of the scrollback (CSI 3 J; test/sources.test.ts greps for all three).
// The scrollback is the permanent record of the session and each of those
// loses it: lines scrolled out of a narrowed region are discarded rather than
// kept, and 3J erases them outright.

import type { EventEmitter } from 'node:events';
import { sanitizeCommitLine, screenRows } from '../input/width.js';
import type { Frame } from './frame.js';

export interface Output extends EventEmitter {
  write(data: string): unknown;
  columns?: number;
  rows?: number;
}

const BEGIN_SYNC = '\x1b[?2026h';
const END_SYNC = '\x1b[?2026l';
const HIDE_CURSOR = '\x1b[?25l';
const SHOW_CURSOR = '\x1b[?25h';
const ERASE_DOWN = '\x1b[J';
const ERASE_LINE = '\x1b[2K';
const REPORT_CURSOR = '\x1b[6n';
const up = (rows: number): string => (rows > 0 ? `\x1b[${rows}A` : '');

/** How long a resize waits for the terminal to say where the cursor is. */
export const CPR_TIMEOUT_MS = 250;

export class LiveTerminal {
  columns: number;
  rows: number;
  /** The screen row the frame starts on, counted from the top of the window. */
  top = 0;
  /** Blank rows at the top of the frame, holding the box against the foot. */
  pad = 0;
  /** Cursor position reports still owed by the terminal. */
  awaiting = 0;
  // Every row on screen now, pad included, as plain text. The cursor waits at
  // column 0 of the row under the last of them.
  private drawn: string[] = [];
  private shown: string[] | null = null;
  private content = 0;
  // Screen rows the last resize erased: the old frame, as re-wrapped.
  private erased = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(private readonly out: Output, private readonly onReady: () => void) {
    this.columns = out.columns || 80;
    this.rows = out.rows || 24;
  }

  /**
   * Start a screen of our own: what is in the window is pushed up into the
   * scrollback -- kept there, not erased -- and the session starts on the top
   * row, which is the only moment the cursor's row is known without asking.
   * The cursor is hidden for the life of the screen; the caret is drawn.
   */
  open(): void {
    this.columns = this.out.columns || 80;
    this.rows = this.out.rows || 24;
    const lift = Math.max(0, this.rows - 1);
    this.out.write(HIDE_CURSOR + '\r\n'.repeat(lift) + up(lift) + '\r');
    this.top = 0;
    this.drawn = [];
    this.shown = null;
  }

  /** Whether a frame may be painted now (no cursor report outstanding). */
  get ready(): boolean {
    return this.awaiting === 0;
  }

  /**
   * Write `commits` into the scrollback and the frame under them, in one
   * write. Skipped when nothing would change, so an untouched box is never
   * rewritten under the reader.
   */
  paint(frame: Frame, commits: string[]): void {
    if (commits.length === 0 && this.shown !== null && same(frame.rows, this.shown)) return;
    let out = BEGIN_SYNC + up(this.drawn.length) + '\r';
    if (commits.length > 0) {
      // Erased first: a committed line wider than the window wraps onto the
      // rows below, and those still hold the old frame.
      out += ERASE_DOWN;
      for (const text of commits) {
        for (const raw of splitLines(text)) {
          const line = sanitizeCommitLine(raw);
          out += line + (line.includes('\x1b[') ? '\x1b[0m' : '') + '\r\n';
          // Counted as the terminal will wrap it. It need not be exact: once
          // the window is full the clamp below makes it exact again.
          this.top += screenRows(line, this.columns);
        }
      }
    }
    this.content = frame.rows.length;
    // A frame that would cross the foot of the window scrolls it, and then
    // ends on the row above the foot like every other frame.
    if (this.top + this.content > this.rows - 1) this.top = Math.max(0, this.rows - 1 - this.content);
    this.pad = Math.max(0, this.rows - 1 - this.top - this.content);
    for (let row = 0; row < this.pad; row += 1) out += ERASE_LINE + '\r\n';
    for (const row of frame.rows) out += ERASE_LINE + row + '\r\n';
    out += ERASE_DOWN + END_SYNC;
    this.out.write(out);
    this.drawn = [...Array<string>(this.pad).fill(''), ...frame.plain];
    this.shown = frame.rows;
  }

  /**
   * The window changed size. The old frame is erased and the terminal is
   * asked where that left the cursor, because how a terminal moves its
   * content on a resize is its own business: xterm.js pulls rows back out of
   * the scrollback when the window grows, others grow at the bottom.
   *
   * The erase can be exact because the cursor is parked under the frame. A
   * terminal that re-wraps on a narrower window turns each old row into as
   * many rows as it now needs, and those all sit above the cursor. A cursor
   * in the input row would not do: xterm.js moves it down for every row
   * re-wrapped below it, so its distance to the frame's top stops being
   * knowable (measured; see test/live.test.ts).
   */
  resize(): void {
    const columns = this.out.columns || 80;
    const rows = this.out.rows || 24;
    if (this.drawn.length > 0) {
      const narrower = columns < this.columns;
      this.erased = this.drawn.reduce((sum, row) => sum + (narrower ? screenRows(row, columns) : 1), 0);
      this.out.write(HIDE_CURSOR + up(this.erased) + '\r' + ERASE_DOWN);
      this.drawn = [];
      this.shown = null;
    }
    this.columns = columns;
    this.rows = rows;
    this.awaiting += 1;
    this.out.write(REPORT_CURSOR);
    clearTimeout(this.timer);
    // A terminal that never answers: assume the frame that was erased sat on
    // the foot of the window, which is where it is always drawn, so its top
    // was as far above the foot as it had grown tall. Wrong only when the
    // terminal moved it off the foot, and then the box rides back down with
    // the output.
    this.timer = setTimeout(() => {
      if (this.awaiting === 0) return;
      this.awaiting = 0;
      this.top = Math.max(0, this.rows - 1 - this.erased);
      this.onReady();
    }, CPR_TIMEOUT_MS);
  }

  /** The terminal's answer to a resize: the cursor, at the frame's top, is on `row` (1-based). */
  report(row: number): void {
    if (this.awaiting === 0) return;
    this.awaiting -= 1;
    if (this.awaiting > 0) return;
    clearTimeout(this.timer);
    this.top = Math.max(0, row - 1);
    this.onReady();
  }

  /** Take the frame down and give the cursor back. */
  close(): void {
    clearTimeout(this.timer);
    this.awaiting = 0;
    this.out.write(up(this.drawn.length) + '\r' + ERASE_DOWN + SHOW_CURSOR);
    this.drawn = [];
    this.shown = null;
  }
}

function same(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((row, index) => row === b[index]);
}

/** A committed text's lines: one row per line, and none extra for a final newline. */
function splitLines(text: string): string[] {
  return text.replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n');
}
