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

//
// Where the blank rows go, as in the Python UI: output fills the window from
// the top, the frame is held on the last rows by blank rows above it (the
// pad), and the next output is written over them. A window made shorter keeps
// its bottom and pushes its top rows into the scrollback (xterm.js because the
// cursor is on the last row; Windows Terminal because it keeps the last
// written row and the cursor in view; conhost because it keeps the cursor in
// view), so while the pad is there a shrink takes the conversation above it
// first and the pad stays: the same as the Python UI does.

import type { EventEmitter } from 'node:events';
import { sanitizeCommitLine, screenRows, wrapPlain } from '../input/width.js';
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
  // Every row of the frame, pad included, as plain text, as it was written.
  // The cursor waits at column 0 of the row under the last of them.
  private drawn: string[] = [];
  // Screen rows from the frame's top down to the cursor: one per drawn row,
  // until a resize re-wraps them.
  private above = 0;
  // Rows of the frame a shrink pushed into the scrollback, as lines, oldest
  // first. Out of reach until a terminal that pulls rows back out of the
  // scrollback on a grow (xterm.js) puts them back over the frame; then they
  // are erased with it. Forgotten once output is written under them.
  private lost: string[] = [];
  // What the last resize found pushed out, kept as `lost` by the next paint.
  private losing: string[] | null = null;
  // The rows under the frame's top no longer match `drawn` (a resize).
  private dirty = false;
  private shown: string[] | null = null;
  private held = true;
  private content = 0;
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
    this.pad = 0;
    this.drawn = [];
    this.above = 0;
    this.lost = [];
    this.losing = null;
    this.dirty = false;
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
    const hold = frame.hold !== false;
    if (commits.length === 0 && this.shown !== null && hold === this.held && same(frame.rows, this.shown)) return;
    // A paint that cannot wait for the terminal's answer (the screen is being
    // handed over) takes the answer it would have had on a silent terminal.
    if (this.awaiting > 0) this.settle();
    let out = BEGIN_SYNC + up(this.above) + '\r';
    const lines = commits.flatMap(splitLines).map(sanitizeCommitLine);
    // Erased first: a committed line wider than the window wraps onto the
    // rows below, and those still hold the old frame -- as do rows a resize
    // re-wrapped.
    if (lines.length > 0 || this.dirty) out += ERASE_DOWN;
    for (const line of lines) {
      out += line + (line.includes('\x1b[') ? '\x1b[0m' : '') + '\r\n';
      // Counted as the terminal will wrap it. It need not be exact: once the
      // window is full the clamp below makes it exact again.
      this.top += screenRows(line, this.columns);
    }
    this.content = frame.rows.length;
    // A frame that would cross the foot of the window scrolls it, and then
    // ends on the row above the foot like every other frame.
    if (this.top + this.content > this.rows - 1) this.top = Math.max(0, this.rows - 1 - this.content);
    // A frame that is not held (a bare question) follows the output instead,
    // with nothing under it but the cursor's row.
    this.pad = hold ? Math.max(0, this.rows - 1 - this.top - this.content) : 0;
    for (let row = 0; row < this.pad; row += 1) out += ERASE_LINE + '\r\n';
    for (const row of frame.rows) out += ERASE_LINE + row + '\r\n';
    out += ERASE_DOWN + END_SYNC;
    this.out.write(out);
    this.drawn = [...Array<string>(this.pad).fill(''), ...frame.plain];
    this.above = this.drawn.length;
    // Output written at the frame's top separates it from rows it lost.
    this.lost = lines.length > 0 ? [] : this.losing ?? this.lost;
    this.losing = null;
    this.dirty = false;
    this.shown = frame.rows;
    this.held = hold;
  }

  /**
   * The window changed size. The terminal is asked where the cursor is now,
   * and the frame is erased and painted again once it has said, because how
   * a terminal moves its content on a resize is its own business: xterm.js
   * pulls rows back out of the scrollback when the window grows, Windows
   * Terminal grows at the bottom, and every one of them pushes the top of the
   * window into the scrollback when it shrinks.
   *
   * Where the frame is follows from where the cursor is, because the cursor
   * is parked under the frame. A terminal that re-wraps on a narrower window
   * turns each old row into as many rows as it now needs, and those all sit
   * above the cursor. A cursor in the input row would not do: xterm.js moves
   * it down for every row re-wrapped below it, so its distance to the
   * frame's top stops being knowable (measured; see test/live.test.ts).
   */
  resize(): void {
    this.columns = this.out.columns || 80;
    this.rows = this.out.rows || 24;
    this.awaiting += 1;
    this.out.write(HIDE_CURSOR + REPORT_CURSOR);
    clearTimeout(this.timer);
    // A terminal that never answers: assume the cursor is on the foot of the
    // window, which is where it is always parked. Wrong only when the
    // terminal moved it off the foot, and then the box rides back down with
    // the output; the erase is relative to the cursor and exact either way.
    this.timer = setTimeout(() => {
      if (this.awaiting === 0) return;
      this.settle();
      this.onReady();
    }, CPR_TIMEOUT_MS);
  }

  /** The terminal's answer to a resize: the cursor, under the frame, is on `row` (1-based). */
  report(row: number): void {
    if (this.awaiting === 0) return;
    this.awaiting -= 1;
    if (this.awaiting > 0) return;
    clearTimeout(this.timer);
    this.measure(Math.max(0, row - 1));
    this.onReady();
  }

  /** Take the frame down and give the cursor back. */
  close(): void {
    if (this.awaiting > 0) this.settle();
    this.out.write(up(this.above) + '\r' + ERASE_DOWN + SHOW_CURSOR);
    this.drawn = [];
    this.above = 0;
    this.shown = null;
  }

  /** Stop waiting for an answer, and assume the cursor is on the foot. */
  private settle(): void {
    clearTimeout(this.timer);
    this.awaiting = 0;
    this.measure(this.rows - 1);
  }

  /**
   * Find the frame again, the cursor being on window row `cursor` (0-based).
   * The frame as the terminal holds it now is the rows it lost to the
   * scrollback earlier and the rows it drew, each re-wrapped at the new
   * width; as many as fit sit directly above the cursor and are erased by the
   * next paint, and the rest are above the window. Worked out from what was
   * drawn, never from the last answer, so a second resize before the paint
   * counts nothing twice.
   */
  private measure(cursor: number): void {
    const lines = [...this.lost, ...this.drawn];
    const heights = lines.map((line) => screenRows(line, this.columns));
    const total = heights.reduce((sum, rows) => sum + rows, 0);
    this.above = Math.min(total, cursor);
    this.top = cursor - this.above;
    // A line cut by the top of the window keeps only its head up there: the
    // paint erases the rest from column 0, which also unjoins the two.
    this.losing = [];
    let cut = total - this.above;
    for (let index = 0; cut > 0; index += 1) {
      const line = lines[index]!;
      this.losing.push(heights[index]! <= cut ? line : wrapPlain(line, this.columns).slice(0, cut).join(''));
      cut -= heights[index]!;
    }
    this.dirty = true;
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
