// The session screen driven through a headless terminal: where the box sits,
// what reaches the scrollback, what a resize leaves behind, and the raw bytes.

import { afterEach, describe, expect, it } from 'vitest';
import { INPUT_MAX_ROWS, layoutField } from '../src/input/field.js';
import { LIVE_BODY_LINES, MAX_CONTENT_WIDTH, PROMPT_PREFIX } from '../src/live/frame.js';
import { createSessionScreen, type SessionScreen } from '../src/live/session.js';
import { FakeTerminal, forbiddenIn, isField, isRule, rowsWhere } from './harness.js';

const SIZES: [number, number][] = [[80, 24], [40, 12], [160, 50]];
const STATUS = 'status: thinking';

let screens: SessionScreen[] = [];
afterEach(() => {
  for (const screen of screens) screen.stop();
  screens = [];
});

function open(term: FakeTerminal, options: { mouseCaret?: boolean } = {}): SessionScreen {
  const screen = createSessionScreen({ stdin: term.stdin, stdout: term.stdout, color: false, ...options });
  screens.push(screen);
  return screen;
}

/** Where the box is in the window: the rule above the field, the field, the rule under it. */
function box(term: FakeTerminal): { top: number; field: number; bottom: number; status: number } {
  const view = term.viewport();
  const rules = rowsWhere(view, isRule);
  const fields = rowsWhere(view, isField);
  return { top: rules[0]!, field: fields[0]!, bottom: rules[1]!, status: view.indexOf(` ${STATUS}`) };
}

/** No frame left anywhere but the live one: one box, one status row, in the whole buffer. */
function expectNoStaleRows(term: FakeTerminal): void {
  const all = term.all();
  expect(rowsWhere(all, isRule)).toHaveLength(2);
  expect(rowsWhere(all, isField)).toHaveLength(1);
  expect(rowsWhere(all, (row) => row.includes(STATUS))).toHaveLength(1);
}

describe.each(SIZES)('live frame at %ix%i', (columns, rows) => {
  it('puts the box on the last rows from the first frame', async () => {
    const term = new FakeTerminal(columns, rows);
    const screen = open(term);
    screen.setStatus(STATUS);
    await term.settle();
    // Field, bottom rule, status, then the row the cursor waits on.
    expect(box(term)).toEqual({ top: rows - 5, field: rows - 4, bottom: rows - 3, status: rows - 2 });
    expect(term.viewport()[rows - 1]).toBe('');
  });

  it('keeps the box still while lines stream past, and keeps every line in the scrollback', async () => {
    const term = new FakeTerminal(columns, rows);
    const screen = open(term);
    screen.setStatus(STATUS);
    screen.setBusy(true);
    await term.settle();
    const where = box(term);
    const count = rows * 2;
    for (let index = 0; index < count; index += 1) {
      screen.setReply(`partial ${index}`);
      await term.settle(1);
      expect(box(term)).toEqual(where);
      screen.commit(`line ${index}`);
      screen.setReply('');
      await term.settle(1);
      expect(box(term)).toEqual(where);
    }
    await term.settle();
    const all = term.all();
    const lines = all.filter((row) => /^line \d+$/.test(row));
    expect(lines).toEqual(Array.from({ length: count }, (_, index) => `line ${index}`));
    // The oldest lines are above the window, in the terminal's own scrollback.
    expect(term.scrollback()).toContain('line 0');
    expect(term.viewport()).toContain(`line ${count - 1}`);
    expect(all.some((row) => row.startsWith('partial'))).toBe(false);
    expectNoStaleRows(term);
    expect(forbiddenIn(term.raw)).toEqual([]);
  }, 30_000);

  it('strips every escape but colour from committed text, so output cannot erase the scrollback', async () => {
    const term = new FakeTerminal(columns, rows);
    const screen = open(term);
    screen.commit('tool said \x1b[3J\x1b[2J\x1b[1;5r\x1b[?1049h\x1bc\x9b3J and \x1b[31mred\x1b[0m\tok');
    await term.settle();
    expect(forbiddenIn(term.raw)).toEqual([]);
    expect(term.raw).toContain('\x1b[31mred\x1b[0m');
    // The eight-bit CSI goes and leaves its parameters as text; the tab is
    // expanded to the next stop of eight, so the row count is the terminal's.
    expect(term.all()).toContain('tool said 3J and red    ok');
  });

  it('cuts a reply taller than the window to fit above the box', async () => {
    const term = new FakeTerminal(columns, rows);
    const screen = open(term);
    screen.setStatus(STATUS);
    screen.setReply(Array.from({ length: 80 }, (_, index) => `reply row ${index}`).join('\n'));
    await term.settle();
    const view = term.viewport();
    expect(view[rows - 1]).toBe('');
    expect(box(term).status).toBe(rows - 2);
    // The window less the cursor's row, the box and the status row; and never
    // more than the ten rows the Python relay shows.
    const shown = view.filter((row) => row.startsWith('reply row'));
    expect(shown).toHaveLength(Math.min(LIVE_BODY_LINES, rows - 1 - 3 - 1));
    expect(shown.at(-1)).toBe('reply row 79');
    expect(term.scrollback().some((row) => row.startsWith('reply row'))).toBe(false);
    expect(forbiddenIn(term.raw)).toEqual([]);
  });

  it('keeps a long typed line inside the box, wrapped, with the box on the last rows', async () => {
    const term = new FakeTerminal(columns, rows);
    const screen = open(term);
    screen.setStatus(STATUS);
    const text = 'word '.repeat(60).trim();
    term.type(text);
    await term.settle();
    const view = term.viewport();
    const { top, bottom, status } = box(term);
    expect(status).toBe(rows - 2);
    const inner = Math.min(MAX_CONTENT_WIDTH, columns - 1) - PROMPT_PREFIX;
    expect(bottom - top - 1).toBe(Math.min(INPUT_MAX_ROWS, layoutField(text, inner).length));
    // Only the first row carries the marker; the rest line up under the text.
    const rows_ = view.slice(top + 1, bottom);
    expect(rows_[0]!.startsWith(' > word')).toBe(true);
    for (const row of rows_.slice(1)) expect(row.startsWith('   word')).toBe(true);
    for (const row of view) expect(row.length).toBeLessThan(columns);
  });
});

describe('resize', () => {
  it('parks the cursor under the frame because xterm.js moves a cursor left inside it', async () => {
    // Why the caret is drawn rather than placed. With the terminal's cursor
    // in the input row, xterm.js moves it down one row for every row that
    // re-wraps BELOW it on a narrower window, so its distance to the frame's
    // top stops being knowable and a cursor-relative erase leaves stale rows.
    const term = new FakeTerminal(40, 10, { answers: false });
    const rule = ' ' + '-'.repeat(38);
    const write = (data: string) => new Promise<void>((resolve) => term.term.write(data, resolve));
    await write(['above', rule, ' > ' + 'x'.repeat(30), rule, 'status'].join('\r\n') + '\r\n');
    await write('\x1b[3A\r\x1b[5C');
    expect(term.cursorRow()).toBe(2);
    term.term.resize(20, 10);
    await write('');
    // Only the rule above it re-wrapped above it: kept with its row, the
    // cursor would be on row 3. It is lower, by the rows wrapped under it.
    expect(term.cursorRow()).toBeGreaterThan(3);
  });

  for (const [fromColumns, fromRows] of SIZES) {
    for (const [toColumns, toRows] of SIZES) {
      if (fromColumns === toColumns && fromRows === toRows) continue;
      it(`leaves no stale rows going from ${fromColumns}x${fromRows} to ${toColumns}x${toRows}`, async () => {
        const term = new FakeTerminal(fromColumns, fromRows);
        const screen = open(term);
        screen.setStatus(STATUS);
        screen.setCaption('12:00  anthropic  claude-sonnet');
        screen.setBusy(true);
        for (let index = 0; index < fromRows + 5; index += 1) screen.commit(`line ${index} ${'x'.repeat(index % 50)}`);
        screen.setReply('a reply still arriving');
        await term.settle();
        term.type('half a question that wraps onto a second row of the field, and more');
        await term.settle();
        term.resize(toColumns, toRows);
        await term.settle();
        const view = term.viewport();
        expectNoStaleRows(term);
        expect(box(term).status).toBe(toRows - 2);
        expect(view[toRows - 1]).toBe('');
        for (const row of view) expect(row.length).toBeLessThan(toColumns);
        // Output after the resize lands above the box, at the new width.
        screen.commit('after the resize');
        await term.settle();
        expect(term.viewport()).toContain('after the resize');
        expect(box(term).status).toBe(toRows - 2);
        expectNoStaleRows(term);
        expect(forbiddenIn(term.raw)).toEqual([]);
      });
    }
  }

  it('leaves no stale rows when a young session is resized', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.setStatus(STATUS);
    screen.commit('header');
    await term.settle();
    for (const [columns, rows] of [[40, 12], [160, 50], [80, 24], [30, 10]] as const) {
      term.resize(columns, rows);
      await term.settle();
      expectNoStaleRows(term);
      expect(box(term).status).toBe(rows - 2);
      expect(term.all()).toContain('header');
    }
  });

  it('re-wraps wide characters in the field without stale rows', async () => {
    const term = new FakeTerminal(40, 12);
    const screen = open(term);
    screen.setStatus(STATUS);
    for (let index = 0; index < 20; index += 1) screen.commit(`line ${index}`);
    term.type('漢字かなカナ'.repeat(8));
    await term.settle();
    for (const [columns, rows] of [[80, 24], [40, 12], [25, 12]] as const) {
      term.resize(columns, rows);
      await term.settle();
      expectNoStaleRows(term);
      expect(box(term).status).toBe(rows - 2);
      for (const row of term.viewport()) expect(row.length).toBeLessThan(columns);
    }
  });

  it('survives a burst of resizes, as dragging a window edge sends', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.setStatus(STATUS);
    for (let index = 0; index < 30; index += 1) screen.commit(`line ${index}`);
    await term.settle();
    for (const columns of [78, 70, 61, 55, 47, 40, 52, 66, 90]) term.resize(columns, 24);
    await term.settle();
    expectNoStaleRows(term);
    expect(box(term).status).toBe(22);
  });

  it('falls back to the foot of the window when the terminal never answers', async () => {
    const term = new FakeTerminal(80, 24, { answers: false });
    const screen = open(term);
    screen.setStatus(STATUS);
    for (let index = 0; index < 30; index += 1) screen.commit(`line ${index}`);
    await term.settle();
    term.resize(60, 20);
    await new Promise((resolve) => setTimeout(resolve, 300));
    await term.settle();
    expectNoStaleRows(term);
    expect(box(term).status).toBe(18);
  });
});

describe('typing during a turn', () => {
  it('queues a line, shows it above the box, and sends it when the turn ends', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const sent: string[] = [];
    screen.onSubmit((line) => sent.push(line));
    screen.setBusy(true);
    await term.settle();
    expect(term.viewport().some((row) => row.includes('Type to queue the next task'))).toBe(true);
    term.type('run the tests');
    term.type('\r');
    await term.settle();
    expect(sent).toEqual([]);
    expect(screen.queued()).toEqual(['run the tests']);
    const view = term.viewport();
    const queuedRow = view.findIndex((row) => row.includes('queued  run the tests'));
    expect(queuedRow).toBeGreaterThanOrEqual(0);
    expect(queuedRow).toBeLessThan(box(term).top);
    screen.setBusy(false);
    expect(sent).toEqual(['run the tests']);
    expect(screen.queued()).toEqual([]);
    await term.settle();
    expect(term.all().some((row) => row.includes('queued'))).toBe(false);
  });

  it('sends one queued line per turn when each one starts a turn', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const sent: string[] = [];
    screen.onSubmit((line) => {
      sent.push(line);
      screen.setBusy(true);
    });
    screen.setBusy(true);
    for (const line of ['first', 'second', 'third']) {
      term.type(line);
      term.type('\r');
    }
    expect(screen.queued()).toEqual(['first', 'second', 'third']);
    screen.setBusy(false);
    expect(sent).toEqual(['first']);
    screen.setBusy(false);
    expect(sent).toEqual(['first', 'second']);
  });

  it('submits straight away between turns, and folds a pasted block until then', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const sent: string[] = [];
    screen.onSubmit((line) => sent.push(line));
    term.type('look at ');
    term.type('\x1b[200~Traceback:\r  File "x.py"\r    boom\r\x1b[201~');
    await term.settle();
    expect(term.viewport().some((row) => row.includes('> look at [Pasted text #1 +3 lines]'))).toBe(true);
    term.type('\r');
    expect(sent).toEqual(['look at Traceback:\n  File "x.py"\n    boom']);
  });

  it('never submits a pasted block that arrived without bracketed paste', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const sent: string[] = [];
    screen.onSubmit((line) => sent.push(line));
    term.type('one\rtwo\rthree\r');
    await term.settle();
    expect(sent).toEqual([]);
    term.type('\r');
    expect(sent).toEqual(['one\ntwo\nthree']);
  });

  it('gives Ctrl-C to the caller during a turn and keeps what was typed', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const keys: string[] = [];
    screen.onInterrupt((key) => keys.push(key));
    screen.setBusy(true);
    term.type('draft');
    term.type('\x03');
    await term.settle();
    expect(keys).toEqual(['ctrl-c']);
    expect(term.viewport().some((row) => row.startsWith(' > draft'))).toBe(true);
    screen.setBusy(false);
    term.type('\x03');
    expect(keys).toEqual(['ctrl-c']);
    term.type('\x03');
    expect(keys).toEqual(['ctrl-c', 'ctrl-c']);
  });
});

describe('asking a question', () => {
  it('answers with the next line, an empty one too, and keeps it from onSubmit even while busy', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const sent: string[] = [];
    screen.onSubmit((line) => sent.push(line));
    screen.setBusy(true);
    const first = screen.ask({ hint: 'Type y to run it' });
    await term.settle();
    expect(term.viewport().some((row) => row.startsWith(' > ') && row.includes('Type y to run it'))).toBe(true);
    term.type('y');
    term.type('\r');
    expect(await first).toBe('y');
    const second = screen.ask({ hint: 'again' });
    term.type('\r');
    expect(await second).toBe('');
    expect(sent).toEqual([]);
    expect(screen.queued()).toEqual([]);
  });

  it('draws the hint only in the empty box, and the typing hint comes back after', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.setBusy(true);
    const answer = screen.ask({ hint: 'Pick one' });
    await term.settle();
    expect(term.viewport().some((row) => row.includes('Pick one'))).toBe(true);
    expect(term.viewport().some((row) => row.includes('Type to queue the next task'))).toBe(false);
    term.type('2');
    await term.settle();
    expect(term.viewport().some((row) => row.includes('Pick one'))).toBe(false);
    term.type('\r');
    expect(await answer).toBe('2');
    await term.settle();
    expect(term.viewport().some((row) => row.includes('Type to queue the next task'))).toBe(true);
  });

  it('resolves null on Esc, Ctrl-C and Ctrl-D, and Ctrl-C still reaches onInterrupt', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const keys: string[] = [];
    screen.onInterrupt((key) => keys.push(key));
    screen.setBusy(true);
    const esc = screen.ask();
    term.type('\x1b');
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(await esc).toBeNull();
    expect(keys).toEqual([]);
    const ctrlC = screen.ask();
    term.type('typed');
    term.type('\x03');
    expect(await ctrlC).toBeNull();
    expect(keys).toEqual(['ctrl-c']);
    const ctrlD = screen.ask();
    term.type('\x04');
    expect(await ctrlD).toBeNull();
    expect(keys).toEqual(['ctrl-c', 'ctrl-d']);
  });

  it('gives the draft back after the answer and leaves the queue untouched meanwhile', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const sent: string[] = [];
    screen.onSubmit((line) => sent.push(line));
    screen.setBusy(true);
    term.type('later');
    term.type('\r');
    term.type('half a draft');
    const answer = screen.ask({ hint: 'Pick one' });
    await term.settle();
    expect(term.viewport().some((row) => row.includes('half a draft'))).toBe(false);
    expect(term.viewport().some((row) => row.includes('queued  later'))).toBe(true);
    // The turn ending while the question is open must not send the queue.
    screen.setBusy(false);
    expect(sent).toEqual([]);
    term.type('n');
    term.type('\r');
    expect(await answer).toBe('n');
    expect(sent).toEqual(['later']);
    await term.settle();
    expect(term.viewport().some((row) => row.startsWith(' > half a draft'))).toBe(true);
    term.type('\r');
    expect(sent).toEqual(['later', 'half a draft']);
  });

  it('resolves null when stopped or replaced, and at once after a stop', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const first = screen.ask({ hint: 'one' });
    const second = screen.ask({ hint: 'two' });
    expect(await first).toBeNull();
    screen.stop();
    expect(await second).toBeNull();
    expect(await screen.ask()).toBeNull();
    expect(forbiddenIn(term.raw)).toEqual([]);
  });
});

describe('terminal modes', () => {
  it('turns on bracketed paste and raw mode, and restores both, the cursor and the screen on stop', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.setStatus(STATUS);
    screen.commit('kept');
    await term.settle();
    expect(term.raw).toContain('\x1b[?2004h');
    expect(term.rawMode).toBe(true);
    expect(term.raw).not.toContain('\x1b[?1000h');
    screen.stop();
    await term.settle();
    expect(term.raw.endsWith('\x1b[?2004l\x1b[?25h')).toBe(true);
    expect(term.rawMode).toBe(false);
    expect(term.all()).toContain('kept');
    expect(term.all().some((row) => isRule(row) || isField(row) || row.includes(STATUS))).toBe(false);
  });

  it('restores the terminal modes when the process exits without a stop', async () => {
    const term = new FakeTerminal(80, 24);
    const before = process.listeners('exit');
    open(term, { mouseCaret: true });
    await term.settle();
    const ours = process.listeners('exit').filter((listener) => !before.includes(listener));
    expect(ours).toHaveLength(1);
    // Called directly: emitting 'exit' would run the test runner's own handlers too.
    (ours[0] as () => void)();
    expect(term.raw).toContain('\x1b[?1006l\x1b[?1000l\x1b[?2004l\x1b[?25h');
    expect(term.rawMode).toBe(false);
  });

  it('suspends for another screen and comes back', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.setStatus(STATUS);
    screen.commit('before');
    screen.suspend();
    await term.settle();
    expect(term.rawMode).toBe(false);
    expect(term.all()).toContain('before');
    expect(term.all().some(isField)).toBe(false);
    screen.resume();
    await term.settle();
    expect(term.rawMode).toBe(true);
    expect(box(term).status).toBe(22);
  });
});
