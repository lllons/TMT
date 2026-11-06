// The live frame is a pure function of its state and the window: the rows of
// the Python UI's look, laid out and measured without a terminal. The Ink
// screen draws the Claude Code look instead; its frame is in ink-frame.test.ts.

import { describe, expect, it } from 'vitest';
import { LineEditor } from '../src/input/editor.js';
import { displayWidth, stripAnsi } from '../src/input/width.js';
import type { FrameState } from '../src/live/frame.js';
import { composeFrame, INK } from './screens.js';

const CAPTION = { clock: '14:39:37', provider: 'OpenRouter', model: 'Nemotron 3 Ultra 550B' };
const COUNTED = { tokens_in: 14500, tokens_out: 48, tokens_out_exact: false, lines_added: 0, lines_removed: 0 };
const NOW = 7000; // the activity glyph is a ✻ at this moment, and still one 1200 ms on
const WORKING = { state: 'working', label: 'Write File', percent: 23, since: NOW - 6000, tokens: 48 } as const;
const PLAN_COLUMN = {
  width: 33,
  rows: ['CAPABILITIES 1', '● /plan', '', 'PLAN 0/3', '─'.repeat(33), 'S1 ● Read README.md', 'S2 ○ Add a Contents line', 'S3 ○ Check the result'],
};

const state = (over: Partial<FrameState> = {}): FrameState => ({
  editor: new LineEditor(), reply: '', status: '', caption: '', meter: null, progress: null, panel: null,
  pending: [], busy: false, ask: null, color: false, truecolor: true, now: NOW, ...over,
});
const plain = (over: Partial<FrameState>, columns = 120, rows = 36): string[] => composeFrame(state(over), columns, rows).plain;
/** What a box row of the reply holds, without the edges and the margin. */
const inside = (row: string): string => row.slice(1).replace(/ 』$/, '').trim();
const waiting =(text: string): LineEditor => Object.assign(new LineEditor(text), { placeholderVisible: true });

describe.skipIf(INK)('the live reply in the frame', () => {
  const frame = (reply: string, columns: number) => plain({ reply, busy: true }, columns, 24);

  it('wraps at word boundaries, never in the middle of a word', () => {
    const reply = 'streamed line 15: the quick brown fox jumps over the lazy dog and keeps going a while';
    const body = frame(reply, 40).slice(1, -4).map(inside);
    expect(body.length).toBeGreaterThan(1);
    expect(body.join(' ').split(/\s+/).filter(Boolean)).toEqual(reply.split(/\s+/));
  });

  it('keeps a trailing line break as an empty last row', () => {
    const body = frame('one\ntwo\n', 40).slice(1, -4).map(inside);
    expect(body.slice(-3)).toEqual(['one', 'two', '']);
  });

  it('is drawn in the Python box: corners, wide edges, and the rows the same width', () => {
    const reply = "I'll start with notes.txt: add a short status section under the shopping list and keep the list itself as it is.";
    const rows = plain({ reply, busy: true });
    // As the Python capture has it at 120 columns: 119 wide, the text one space in.
    expect(rows[0]).toBe('┌' + '─'.repeat(117) + '┐');
    expect(rows[1]).toBe(`『  ${reply} 』`);
    expect(rows[2]).toBe('└' + '─'.repeat(117) + '┘');
    for (const row of rows.slice(0, 3)) expect(displayWidth(row)).toBe(119);
  });

  it('shows its last ten rows at most, and gives them up to the box when the window is short', () => {
    const reply = Array.from({ length: 40 }, (_, index) => `row ${index}`).join('\n');
    const tall = plain({ reply, busy: true }, 80, 40);
    expect(tall.filter((row) => row.startsWith('『'))).toHaveLength(10);
    expect(tall.filter((row) => row.startsWith('『')).at(-1)).toContain('row 39');
    // Three rows are the least a box can have; with fewer there is none.
    expect(plain({ reply, busy: true }, 80, 6).some((row) => /[┌└『]/.test(row))).toBe(false);
    expect(plain({ reply, busy: true }, 80, 7).filter((row) => row.startsWith('『'))).toHaveLength(1);
  });
});

describe.skipIf(INK)('the caption row', () => {
  it('is the clock, provider and model, right-aligned to the box, with Python\'s separator', () => {
    const row = plain({ caption: CAPTION })[0]!;
    expect(row).toBe(' '.repeat(100 - 45) + '14:39:37 · OpenRouter · Nemotron 3 Ultra 550B');
    expect(row).toHaveLength(100);
  });

  it('is #585858, and the nearest of the 256 where the terminal has no truecolor', () => {
    const [row] = composeFrame(state({ caption: CAPTION, color: true }), 120, 36).rows;
    expect(row).toContain('\x1b[38;2;88;88;88m14:39:37 · OpenRouter · Nemotron 3 Ultra 550B\x1b[0m');
    expect(composeFrame(state({ caption: CAPTION, color: true, truecolor: false }), 120, 36).rows[0]).toContain('\x1b[38;5;240m14:39:37');
    expect(composeFrame(state({ caption: CAPTION }), 120, 36).rows[0]).not.toContain('\x1b');
  });

  it('gives up the provider, then the model, as the box narrows', () => {
    expect(plain({ caption: CAPTION }, 40, 24)[0]!.trim()).toBe('14:39:37 · Nemotron 3 Ultra 550B');
    expect(plain({ caption: CAPTION }, 30, 24)[0]!.trim()).toBe('14:39:37');
  });

  it('carries the counts at its left end between turns, when they fit', () => {
    const row = plain({ caption: CAPTION, meter: COUNTED })[0]!;
    expect(row).toBe(' +0 lines, -0 lines, ~14k context, ~48 out' + ' '.repeat(100 - 1 - 41 - 45) + '14:39:37 · OpenRouter · Nemotron 3 Ultra 550B');
    // Too narrow for the sentence: the short form, then the lines alone.
    expect(plain({ caption: CAPTION, meter: COUNTED }, 80, 24)[0]).toContain('+0 -0');
  });

  it('is not drawn while a turn runs: the question above already carries it', () => {
    expect(plain({ caption: CAPTION, busy: true }).some((row) => row.includes('OpenRouter'))).toBe(false);
  });
});

describe.skipIf(INK)('the meter row', () => {
  const during = (meter: object, columns = 120): string[] => plain({ meter, busy: true }, columns, 24);

  it('is its own row above the box during a turn: wide, short and nothing', () => {
    expect(during(COUNTED)[0]).toBe(' +0 lines, -0 lines, ~14k context, ~48 out');
    // 38 columns: the short form; 22: the lines alone.
    expect(during(COUNTED, 40)[0]).toBe(' +0 -0  ~14k ctx  ~48 out');
    expect(during(COUNTED, 22)[0]).toBe(' +0 -0');
    // Nothing counted yet is no row at all: the box is the first row.
    expect(during({})[0]).toMatch(/^ ─+$/);
    expect(during({ tokens_out_exact: true, agent_tokens: 500 })[0]).toMatch(/^ ─+$/);
  });

  it('says "line" for one, and "~" only for what was estimated', () => {
    expect(during({ tokens_in: 999, tokens_out: 1500, tokens_out_exact: true, lines_added: 1, lines_removed: 12 })[0])
      .toBe(' +1 line, -12 lines, ~999 context, 1k out');
    expect(during({ tokens_in: 2_500_000, tokens_out: 3, tokens_out_exact: false, lines_added: 2, lines_removed: 1 })[0])
      .toBe(' +2 lines, -1 line, ~2.5M context, ~3 out');
  });

  it('colours +N green and -N red and nothing else', () => {
    const [row] = composeFrame(state({ meter: COUNTED, busy: true, color: true }), 120, 24).rows;
    expect(row).toBe(' \x1b[38;2;34;197;94m+0\x1b[0m lines, \x1b[38;2;220;38;38m-0\x1b[0m lines, ~14k context, ~48 out');
    expect(stripAnsi(row!)).toBe(' +0 lines, -0 lines, ~14k context, ~48 out');
  });

  it('adds what the background agents spent', () => {
    expect(during({ ...COUNTED, agent_tokens: 22000 })[0]).toBe(' +0 lines, -0 lines, ~14k context, ~48 out, agents ~22k tokens');
  });
});

describe.skipIf(INK)('the progress row', () => {
  const under = (over: Partial<FrameState>, columns = 120): string => plain({ busy: true, ...over }, columns, 36).at(-1)!;

  it('is the bar, the percentage, the label and the estimate, with the readout at the right edge', () => {
    expect(under({ progress: WORKING })).toBe(
      '███░░░░░░░░░  23% Write File | Estimated finish: ~20 seconds' + ' '.repeat(29) + '✻ thinking… (6s · ↓ 48 tokens)');
  });

  it('gives up the estimate before the readout when the window is narrow', () => {
    expect(under({ progress: WORKING }, 80)).toBe(
      '██░░░░░░░░  23% Write File' + ' '.repeat(23) + '✻ thinking… (6s · ↓ 48 tokens)');
    const small = under({ progress: WORKING }, 40);
    expect(small.length).toBeLessThanOrEqual(39);
    expect(small).toContain('23%');
  });

  it('leaves out the tokens when there are none, and the readout when the bar is full', () => {
    expect(under({ progress: { ...WORKING, tokens: 0 } })).toContain('✻ thinking… (6s)');
    const done = under({ progress: { ...WORKING, label: 'Complete!', percent: 100 } });
    expect(done).toBe('████████████ 100% Complete!');
  });

  it('says what a nearly finished turn is doing instead of guessing', () => {
    expect(under({ progress: { ...WORKING, label: 'FINALIZING', percent: 95 } })).toContain('| Estimated finish: completing...');
    expect(under({ progress: { ...WORKING, label: 'Almost', percent: 96 } })).toContain('| Estimated finish: calculating...');
  });

  it('is the word THINKING before the first progress, and the label alone for work with no measure', () => {
    expect(under({ progress: { ...WORKING, state: 'thinking', percent: null } })).toBe('THINKING');
    expect(under({ progress: { ...WORKING, percent: null, label: 'Asking' } })).toBe('Asking');
  });

  it('cycles with the clock: the bar and the word are other colours a moment later, the text is the same', () => {
    const at = (now: number) => composeFrame(state({ busy: true, color: true, progress: { ...WORKING, since: now - 6000 }, now }), 120, 36).rows.at(-1)!;
    expect(at(NOW)).toContain('\x1b[38;2;');
    expect(at(NOW)).not.toBe(at(NOW + 600));
    expect(stripAnsi(at(NOW))).toBe(stripAnsi(at(NOW + 1200)));
    // The empty cells are the dim grey; the readout's detail too.
    expect(at(NOW)).toContain('\x1b[38;2;88;88;88m░');
    expect(at(NOW)).toContain('\x1b[38;2;88;88;88m (6s · ↓ 48 tokens)\x1b[0m');
  });

  it('is the only row under the box, and never wider than the window less one', () => {
    for (const columns of [30, 60, 120, 200]) {
      const rows = plain({ busy: true, progress: WORKING }, columns, 24);
      expect(displayWidth(rows.at(-1)!)).toBeLessThanOrEqual(columns - 1);
    }
  });
});

describe.skipIf(INK)('the queued lines', () => {
  const editor = (): LineEditor => new LineEditor();

  it('are counted in the empty box, not listed above it', () => {
    const rows = plain({ busy: true, pending: ['one line', 'another'], editor: editor() }, 120, 24);
    expect(rows.some((row) => row.includes('queued  '))).toBe(false);
    expect(rows.some((row) => row.includes('another'))).toBe(false);
    expect(rows.find((row) => row.startsWith(' > '))).toBe(' > 2 queued tasks - they run when this one finishes');
    expect(plain({ busy: true, pending: ['one'], editor: editor() }, 120, 24).find((row) => row.startsWith(' > '))).toBe(' > 1 queued task - they run when this one finishes');
  });

  it('say less in a narrow box, and give way to what is being typed', () => {
    expect(plain({ busy: true, pending: ['a', 'b', 'c'], editor: editor() }, 40, 24).find((row) => row.startsWith(' > '))).toBe(' > 3 queued tasks');
    const typing = Object.assign(editor(), { value: 'typing', cursor: 6 });
    expect(plain({ busy: true, pending: ['a'], editor: typing }, 120, 24).find((row) => row.startsWith(' > '))).toBe(' > typing ');
    expect(plain({ busy: true, pending: [], editor: editor() }, 120, 24).find((row) => row.startsWith(' > '))).toContain('Working. Type to queue the next task.');
  });

  it('are not told while a question has the box', () => {
    expect(plain({ busy: true, pending: ['a'], ask: 'y / n  then Enter', editor: editor() }, 120, 24).find((row) => row.startsWith(' > '))).toBe(' > y / n  then Enter');
  });
});

describe.skipIf(INK)('the right-hand column', () => {
  const live = (columns: number, rows: number, over: Partial<FrameState> = {}) => composeFrame(state({
    busy: true, reply: 'a short reply\nof two rows',
    meter: COUNTED, progress: WORKING, ...over,
  }), columns, rows);

  for (const [columns, rows] of [[120, 36], [80, 24]] as const) {
    it(`sits beside the live area at ${columns} columns: the count of rows is the left column's, nothing is wider than the window`, () => {
      const without = live(columns, rows);
      const column = { width: 33, rows: PLAN_COLUMN.rows };
      const frame = live(columns, rows, { panel: column });
      expect(frame.rows).toHaveLength(without.rows.length);
      // Left column: what the panel leaves, the two columns apart.
      const left = columns - 1 - 2 - 33;
      expect(frame.plain[0]).toBe(frame.plain[0]!.slice(0, left) + '  CAPABILITIES 1');
      expect(frame.plain[0]!.slice(0, left)).toMatch(/^┌─+┐$/);
      expect(displayWidth(frame.plain[0]!.slice(0, left))).toBe(left);
      // The box is the left column's width, never the 100 of the wide window.
      const rules = frame.plain.map((row, index) => [row, index] as const).filter(([row]) => /^ ─+/.test(row));
      expect(rules).toHaveLength(2);
      expect(rules[0]![0].slice(0, left)).toBe(' ' + '─'.repeat(left - 1));
      expect(frame.plain.some((row) => row.includes('S3 ○ Check the result'))).toBe(true);
      // The progress row is under both columns, at the full width.
      expect(frame.plain.at(-1)).toContain('% Write File');
      expect(frame.plain.at(-1)).toContain('thinking…');
      for (const row of frame.plain) expect(displayWidth(row)).toBeLessThanOrEqual(columns - 1);
      // The caret's row is still found: the field is where the click maps.
      expect(frame.plain[frame.field.first]!.startsWith(' > ')).toBe(true);
    });
  }

  it('lines up with the top of the live area when it is shorter, and the box stays at the foot when it is taller', () => {
    const short = live(120, 36, { panel: { width: 33, rows: ['PLAN 0/1', '─'.repeat(33)] } });
    expect(short.plain[0]).toMatch(/^┌─+┐ {2}PLAN 0\/1$/);
    const tall = live(120, 36, { panel: { width: 33, rows: Array.from({ length: 14 }, (_, index) => `S${index}`.padEnd(33, '.')) } });
    const flat = live(120, 36);
    // Taller than the live area: the left column drops to the foot, blank above it, the box on the same last rows.
    expect(tall.rows).toHaveLength(14 + 1);
    expect(tall.plain.slice(-5).map((row) => row.slice(0, 20))).toEqual(flat.plain.slice(-5).map((row) => row.slice(0, 20)));
    expect(tall.plain[0]).toBe(' '.repeat(84) + '  S0' + '.'.repeat(31));
    expect(tall.field.first).toBe(flat.field.first + 14 - (flat.rows.length - 1));
  });

  it('measures colour as nothing: a styled row is cut and padded by what it shows', () => {
    const styled = { width: 33, rows: ['\x1b[38;2;34;197;94mCAPABILITIES 1\x1b[0m', '\x1b[31m' + 'x'.repeat(60) + '\x1b[0m', '\x1b[1mPLAN\x1b[22m'] };
    const frame = live(120, 36, { panel: styled, color: true });
    const widths = frame.plain.map((row) => displayWidth(row));
    expect(Math.max(...widths)).toBeLessThanOrEqual(119);
    expect(frame.rows[0]).toContain('\x1b[38;2;34;197;94mCAPABILITIES 1\x1b[0m');
    // Its own colour is closed before the row ends, and the cut one is cut at the window.
    expect(frame.rows[1]!.endsWith('\x1b[0m')).toBe(true);
    expect(frame.plain[1]!.endsWith('x')).toBe(true);
    expect(displayWidth(frame.plain[1]!)).toBe(119);
  });

  it('is drawn only while it leaves the live area wide enough, and never when empty', () => {
    // 24 columns for the box, the gutter, the panel and the spare column: 24 + 2 + 33 + 1 = 60.
    expect(live(60, 24, { panel: PLAN_COLUMN }).plain.some((row) => row.includes('PLAN 0/3'))).toBe(true);
    expect(live(59, 24, { panel: PLAN_COLUMN }).plain.some((row) => row.includes('PLAN 0/3'))).toBe(false);
    expect(live(120, 24, { panel: { width: 0, rows: [] } }).rows).toEqual(live(120, 24).rows);
  });

  it('is cut to the room the window has, and never makes the frame taller than rows - 1', () => {
    const big = { width: 33, rows: Array.from({ length: 80 }, (_, index) => `row ${index}`) };
    for (const rows of [8, 12, 24]) {
      const frame = live(120, rows, { panel: big });
      expect(frame.rows.length).toBeLessThanOrEqual(rows - 1);
      expect(frame.plain.at(-1)).toContain('% Write File');
    }
  });

  it('sits beside the idle box too, with the caption', () => {
    const frame = composeFrame(state({ caption: CAPTION, meter: COUNTED, panel: PLAN_COLUMN, editor: waiting('Describe your first task') }), 120, 36);
    expect(frame.plain).toHaveLength(8);
    expect(frame.plain[4]).toContain('14:39:37');
    expect(frame.plain[6]!.startsWith(' > Describe your first task')).toBe(true);
    expect(frame.plain[6]).toContain('S2 ○ Add a Contents line');
  });
});
