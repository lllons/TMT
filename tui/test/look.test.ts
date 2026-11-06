// The Python UI's look in the live frame, driven through a headless terminal:
// the progress row moving with the clock, the colours it asks the terminal
// for, the right-hand column across resizes, and the bytes all of it writes.
// (The Ink screen draws the Claude Code look; test/ink-look.test.ts has its own.)

import { afterEach, describe, expect, it, vi } from 'vitest';
import { displayWidth } from '../src/input/width.js';
import type { SessionScreen } from '../src/live/session.js';
import { createSessionScreen, INK } from './screens.js';
import { FakeTerminal, forbiddenIn, isField, isRule, rowsWhere } from './harness.js';

let screens: SessionScreen[] = [];
afterEach(() => {
  for (const screen of screens) screen.stop();
  screens = [];
});

function open(term: FakeTerminal, options: { color?: boolean; truecolor?: boolean; clock?: () => number } = {}): SessionScreen {
  const screen = createSessionScreen({ stdin: term.stdin, stdout: term.stdout, color: false, ...options });
  screens.push(screen);
  return screen;
}

const WORKING = { state: 'working', label: 'Write File', percent: 23, since: 4000, tokens: 48 } as const;
const COLUMN = {
  width: 33,
  rows: ['CAPABILITIES 1', '● /plan', '', 'PLAN 0/3', '─'.repeat(33), 'S1 ● Read README.md', 'S2 ○ Add a Contents line', 'S3 ○ Check the result'],
};
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** The rule rows of the box, with or without a column beside them: two in the whole buffer, or the frame is stale. */
const boxRules = (term: FakeTerminal) => rowsWhere(term.all(), (row) => /^ ─+( {2,}\S.*)?$/.test(row) && !row.includes('PLAN'));

describe.skipIf(INK)('the progress row against the clock', () => {
  it('counts the seconds and works the estimate out from them, and is the last row but the cursor\'s', async () => {
    let now = 10_000;
    const term = new FakeTerminal(120, 36);
    const screen = open(term, { clock: () => now });
    screen.setBusy(true);
    screen.setProgress({ ...WORKING });
    await term.settle();
    expect(term.viewport()[34]).toMatch(/^███░░░░░░░░░ {2}23% Write File \| Estimated finish: ~20 seconds {29}[✻✽] thinking… \(6s · ↓ 48 tokens\)$/);
    expect(term.viewport()[35]).toBe('');
    now = 14_000;
    await pause(250);
    await term.settle();
    expect(term.viewport()[34]).toContain('Estimated finish: ~33 seconds');
    expect(term.viewport()[34]).toContain('(10s · ↓ 48 tokens)');
    expect(forbiddenIn(term.raw)).toEqual([]);
  });

  it('leaves no timer running once the row is gone, the screen is handed over, or it is stopped', async () => {
    const started = vi.spyOn(globalThis, 'setInterval');
    const cleared = vi.spyOn(globalThis, 'clearInterval');
    const running = () => started.mock.calls.length - cleared.mock.calls.length;
    try {
      const term = new FakeTerminal(80, 24);
      const screen = open(term);
      await term.settle();
      expect(running()).toBe(0);
      screen.setProgress({ ...WORKING });
      expect(running()).toBe(1);
      screen.setProgress({ ...WORKING, percent: 50 });
      expect(running()).toBe(1);
      screen.setProgress(null);
      expect(running()).toBe(0);
      screen.setProgress({ ...WORKING });
      screen.suspend();
      expect(running()).toBe(0);
      screen.resume();
      expect(running()).toBe(1);
      screen.stop();
      expect(running()).toBe(0);
    } finally {
      started.mockRestore();
      cleared.mockRestore();
    }
  });

  it('repaints once a second with no colour (the text changes), and not at all while nothing changes', async () => {
    let now = 10_000;
    const term = new FakeTerminal(80, 24);
    const screen = open(term, { clock: () => now });
    screen.setProgress({ ...WORKING });
    await term.settle();
    const writes = () => (term.raw.match(/\x1b\[\?2026h/g) ?? []).length;
    const first = writes();
    await pause(350);
    await term.settle();
    expect(writes()).toBe(first);
    now += 1000;
    await pause(250);
    await term.settle();
    expect(writes()).toBe(first + 1);
  });
});

describe.skipIf(INK)('colour', () => {
  async function drawn(options: { color: boolean; truecolor: boolean }): Promise<string> {
    const term = new FakeTerminal(120, 36);
    const screen = open(term, { ...options, clock: () => 10_000 });
    screen.setBusy(true);
    screen.setMeter({ tokens_in: 14500, tokens_out: 48, lines_added: 3, lines_removed: 1 });
    screen.setProgress({ ...WORKING });
    await term.settle();
    return term.raw;
  }

  it('is truecolor where the terminal has it: the meter, the bar, the dim empty cells, the readout', async () => {
    const raw = await drawn({ color: true, truecolor: true });
    // A colour ends with a reset, or with the default-colour code Ink closes it with.
    expect(raw).toMatch(/\x1b\[38;2;34;197;94m\+3\x1b\[(?:0|39)m/);
    expect(raw).toMatch(/\x1b\[38;2;220;38;38m-1\x1b\[(?:0|39)m/);
    expect(raw).toContain('\x1b[38;2;88;88;88m░');
    expect(raw).toMatch(/\x1b\[38;2;88;88;88m \(6s · ↓ 48 tokens\)\x1b\[(?:0|39)m/);
    expect(raw).not.toMatch(/\x1b\[38;5;/);
  });

  it('is the nearest of 256 where it has not', async () => {
    const raw = await drawn({ color: true, truecolor: false });
    expect(raw).toContain('\x1b[38;5;240m░');
    expect(raw).toMatch(/\x1b\[38;5;\d+m\+3\x1b\[(?:0|39)m/);
    expect(raw).not.toMatch(/\x1b\[38;2;/);
  });

  it('is none at all with NO_COLOR, and that includes the backend\'s own colours in the column', async () => {
    const term = new FakeTerminal(120, 36);
    const screen = open(term, { color: false, truecolor: true, clock: () => 10_000 });
    screen.setProgress({ ...WORKING });
    screen.setPanel({ width: 33, rows: ['\x1b[38;2;34;197;94mCAPABILITIES 1\x1b[0m'] });
    await term.settle();
    expect(term.raw).not.toMatch(/\x1b\[(38|39|1m|2m)/);
    expect(term.viewport().some((row) => row.includes('CAPABILITIES 1'))).toBe(true);
  });
});

describe.skipIf(INK)('the right-hand column in a terminal', () => {
  it('draws, moves with the window, hides when the box would be too narrow, and is back when it fits', async () => {
    const term = new FakeTerminal(120, 36);
    const screen = open(term, { clock: () => 10_000 });
    screen.setBusy(true);
    screen.setReply('a reply that is on its way');
    screen.setProgress({ ...WORKING });
    screen.setPanel(COLUMN);
    for (let index = 0; index < 50; index += 1) screen.commit(`line ${index}`);
    await term.settle();
    expect(term.viewport().some((row) => row.endsWith('  PLAN 0/3'))).toBe(true);
    expect(boxRules(term)).toHaveLength(2);
    for (const [columns, rows, shown] of [[80, 24, true], [60, 20, true], [59, 20, false], [40, 12, false], [120, 36, true]] as const) {
      term.resize(columns, rows);
      await term.settle();
      expect(term.viewport().some((row) => row.includes('PLAN 0/3')), `${columns}x${rows}`).toBe(shown);
      expect(boxRules(term), `${columns}x${rows}`).toHaveLength(2);
      expect(rowsWhere(term.all(), isField), `${columns}x${rows}`).toHaveLength(1);
      expect(term.viewport()[rows - 2]).toContain('% Write File');
      expect(term.viewport()[rows - 1]).toBe('');
      for (const row of term.viewport()) expect(displayWidth(row)).toBeLessThanOrEqual(columns - 1);
    }
    expect(forbiddenIn(term.raw)).toEqual([]);
    // Taken away, it takes its rows with it.
    screen.setPanel(null);
    await term.settle();
    expect(term.viewport().some((row) => row.includes('PLAN 0/3') || row.includes('CAPABILITIES'))).toBe(false);
  });

  it('keeps the whole screen when a young session is resized with it open', async () => {
    const term = new FakeTerminal(120, 36);
    const screen = open(term);
    screen.setPanel(COLUMN);
    screen.commit('header');
    await term.settle();
    for (const [columns, rows] of [[80, 24], [150, 50], [120, 36]] as const) {
      term.resize(columns, rows);
      await term.settle();
      expect(term.all()).toContain('header');
      // The box's two rules, with the column's rows beside them (flush right, so a wide window leaves a gap).
      expect(rowsWhere(term.all(), (row) => isRule(row) || /^ ─+ {2,}\S/.test(row))).toHaveLength(2);
      expect(term.viewport()[rows - 1]).toBe('');
    }
  });

  it('writes one frame per change, and only what the terminal may be sent', async () => {
    const term = new FakeTerminal(120, 36);
    const screen = open(term, { color: true, truecolor: true });
    await term.settle();
    const writes: string[] = [];
    const write = term.stdout.write;
    term.stdout.write = (data: string) => { writes.push(data); return write(data); };
    // A column that tries to move the cursor, clear the screen and break its row.
    screen.setPanel({ width: 33, rows: ['\x1b[2J\x1b[3J\x1b[1;5r\x1b[?1049hPLAN\r\nSNEAK\tTAB', '\x1b[31mred\x1b[0m'] });
    await term.settle();
    expect(writes).toHaveLength(1);
    expect(forbiddenIn(writes[0]!)).toEqual([]);
    expect(writes[0]).not.toMatch(/\x1b\[2J/);
    expect(writes[0]).toMatch(/\x1b\[31mred\x1b\[(?:0|39)m/);
    expect(term.viewport().some((row) => row.includes('PLANSNEAK'))).toBe(true);
  });
});
