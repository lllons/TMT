// The bytes, with Ink drawing them. Two regions, as in the Python UI's
// LiveRegion and BottomPad: committed lines are written once into the
// terminal's own scrollback (Ink's <Static>) and never redrawn; the live frame
// under them is the only thing Ink repaints, erasing the rows it drew last and
// writing the frame again, in one synchronized write.
//
// Never the alternate screen, never a scroll region (DECSTBM), never an erase
// of the scrollback (CSI 3 J; test/sources.test.ts greps for all three, and
// test/ink.test.ts records every byte). Ink 8 writes none of them on the
// primary screen, but it has two paths that would still cost the record, and
// both are kept closed here:
//
// - A frame as tall as the window is "full-screen" to Ink (ink.js,
//   renderInteractiveFrame and shouldClearTerminalForFrame): it is drawn
//   without its trailing line break, and the next one erases the whole window
//   from its home row, committed rows on it included -- on Windows whenever
//   either frame is that tall. The frame is never taller than `rows - 1`, pad
//   included (layout.ts), so Ink never takes it for one.
// - Ink's own resize handler (ink.js, `resized`) erases the rows it counted
//   when it drew them, not the rows the terminal re-wrapped them into, and
//   leaves stale rows behind. Ink is given a stdout of its own (`Sink`) that
//   never emits 'resize'; the resize is handled here, as the hand-written
//   renderer did, and Ink is then told to forget its frame without erasing it.
//
// The sink is also the gate: while the screen is suspended or stopped nothing
// Ink writes reaches the terminal (its exit handler, its cursor), and Ink is
// given an inert stdin, so it never reads or puts the real one in raw mode.
//
// Where the blank rows go, as in the Python UI: output fills the window from
// the top, the frame is held on the last rows by blank rows above it (the
// pad), and the next output is written over them. A window made shorter keeps
// its bottom and pushes its top rows into the scrollback (xterm.js because the
// cursor is on the last row; Windows Terminal because it keeps the last
// written row and the cursor in view; conhost because it keeps the cursor in
// view), so while the pad is there a shrink takes the conversation above it
// first and the pad stays: the same as the Python UI does.

import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { render, type Instance } from 'ink';
import { sanitizeCommitLine, screenRows, wrapPlain } from '../input/width.js';
import type { Frame } from './layout.js';
import { View } from './view.js';

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
const REPORT_CURSOR = '\x1b[6n';
const up = (rows: number): string => (rows > 0 ? `\x1b[${rows}A` : '');

/** How long a resize waits for the terminal to say where the cursor is. */
export const CPR_TIMEOUT_MS = 250;

/**
 * The stdout Ink writes to: the real one's size, its writes passed on only
 * while `open`, and no 'resize' events ever.
 */
class Sink extends EventEmitter {
  readonly isTTY = true;
  open = false;
  // What Ink writes while a paint gathers it, instead of passing it on.
  private gathered: string | null = null;

  constructor(private readonly out: Output) {
    super();
  }

  get columns(): number {
    return this.out.columns || 80;
  }

  get rows(): number {
    return this.out.rows || 24;
  }

  write(data: string, callback?: () => void): boolean {
    if (this.open && this.gathered !== null) this.gathered += data;
    else if (this.open && data) this.out.write(data);
    callback?.();
    return true;
  }

  /** Everything Ink writes while `draw` runs, held back and handed over as one string. */
  gather(draw: () => void): string {
    this.gathered = '';
    try {
      draw();
      return this.gathered;
    } finally {
      this.gathered = null;
    }
  }
}

export class InkTerminal {
  columns: number;
  rows: number;
  /** The screen row the frame starts on, counted from the top of the window. */
  top = 0;
  /** Blank rows at the top of the frame, holding the box against the foot. */
  pad = 0;
  /** Cursor position reports still owed by the terminal. */
  awaiting = 0;
  private readonly sink: Sink;
  private ink: Instance | undefined;
  // Each paint that commits is a new <Static>: written once, then done.
  private batch = 0;
  // Ink has been told to forget its frame (`forget`) and has drawn none since.
  private forgotten = false;
  // Every row of the frame, pad included, as plain text, as it was written.
  // The cursor waits at column 0 of the row under the last of them: Ink ends
  // its output with a line break and is given no cursor position.
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
    this.sink = new Sink(out);
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
    this.sink.open = true;
  }

  /** Whether a frame may be painted now (no cursor report outstanding). */
  get ready(): boolean {
    return this.awaiting === 0;
  }

  /**
   * Have Ink write `commits` into the scrollback and the frame under them, in
   * one write. Skipped when nothing would change, so an untouched box is never
   * rewritten under the reader.
   */
  paint(frame: Frame, commits: string[]): void {
    const hold = frame.hold !== false;
    if (commits.length === 0 && this.shown !== null && hold === this.held && same(frame.rows, this.shown)) return;
    // A paint that cannot wait for the terminal's answer (the screen is being
    // handed over) takes the answer it would have had on a silent terminal.
    if (this.awaiting > 0) this.settle();
    const lines = commits.flatMap(splitLines).map(sanitizeCommitLine);
    // After a resize the old frame is erased here, by the rows the terminal
    // re-wrapped it into, and Ink draws from where it was as if it had drawn
    // nothing before.
    const erase = this.dirty ? BEGIN_SYNC + up(this.above) + '\r' + ERASE_DOWN : '';
    if (this.dirty) this.forget();
    // Counted as the terminal will wrap them. It need not be exact: once the
    // window is full the clamp below makes it exact again.
    for (const line of lines) this.top += screenRows(line, this.columns);
    // Ink ends what it draws with a line break, so a frame of no rows is still
    // one blank row above the cursor.
    const body = frame.rows.length > 0 ? frame.plain : [''];
    this.content = body.length;
    // A frame that would cross the foot of the window scrolls it, and then
    // ends on the row above the foot like every other frame.
    if (this.top + this.content > this.rows - 1) this.top = Math.max(0, this.rows - 1 - this.content);
    // A frame that is not held (a bare question) follows the output instead,
    // with nothing under it but the cursor's row.
    this.pad = hold ? Math.max(0, this.rows - 1 - this.top - this.content) : 0;
    // One write: Ink writes a frame in three (begin sync, the rows, end sync),
    // and the erase after a resize goes in front of them.
    const drawn = this.sink.gather(() => this.draw(lines, frame.rows));
    if (!drawn && this.forgotten) {
      // Ink found the same empty frame it had forgotten: nothing is drawn, and
      // the cursor is on the frame's top row.
      if (erase) this.out.write(erase + END_SYNC);
      this.drawn = [];
    } else {
      if (erase || drawn) this.out.write(erase + drawn);
      this.forgotten = false;
      this.drawn = [...Array<string>(this.pad).fill(''), ...body];
    }
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
   * That is also why Ink is given no cursor position.
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

  /** Take the frame down, give the cursor back, and let nothing of Ink's through until `open`. */
  close(): void {
    if (this.awaiting > 0) this.settle();
    this.out.write(up(this.above) + '\r' + ERASE_DOWN + SHOW_CURSOR);
    this.forget();
    this.mute();
    this.drawn = [];
    this.above = 0;
    this.shown = null;
  }

  /** Stop passing on what Ink writes: the screen is being handed over, or the process is going. */
  mute(): void {
    this.sink.open = false;
  }

  /** Unmount Ink for good. Nothing it writes on the way out reaches the terminal. */
  unmount(): void {
    this.mute();
    this.ink?.unmount();
    this.ink = undefined;
  }

  private draw(commits: string[], rows: string[]): void {
    if (commits.length > 0) this.batch += 1;
    const view = <View batch={this.batch} commits={commits} pad={this.pad} rows={rows} />;
    if (this.ink) {
      this.ink.rerender(view);
      return;
    }
    this.ink = render(view, {
      stdout: this.sink as unknown as NodeJS.WriteStream,
      // Never read: the keyboard is read by the screen (src/ink/screen.ts).
      stdin: new PassThrough() as unknown as NodeJS.ReadStream,
      // Ctrl-C is a key like any other here; the screen gives it to onInterrupt.
      exitOnCtrlC: false,
      patchConsole: false,
      // Ink would otherwise draw nothing until it exits when CI is set.
      interactive: true,
      // Every render is written at once (Ink's throttle runs with no wait), so
      // what is on the screen after a paint is what the paint drew. Paints are
      // already coalesced to one per turn of the event loop by the screen.
      maxFps: 0,
    });
  }

  /** Make Ink forget the frame it drew, without letting it erase anything. */
  private forget(): void {
    const open = this.sink.open;
    this.sink.open = false;
    this.ink?.clear();
    this.sink.open = open;
    this.forgotten = true;
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
