// The Ink screen's signal rail look in a terminal: the status row moving with
// the clock, the plan and the tool call around it, the colours it asks the
// terminal for, and the footer, with the bytes all of it writes.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { displayWidth } from '../src/input/width.js';
import { createInkSessionScreen, type InkSessionScreen } from '../src/ink/screen.js';
import { fgCode, gradientAt, gradientPhase, LIME } from '../src/theme.js';
import { FakeTerminal, forbiddenIn, isField } from './harness.js';

let screens: InkSessionScreen[] = [];
afterEach(() => {
  for (const screen of screens) screen.stop();
  screens = [];
});

function open(term: FakeTerminal, options: { color?: boolean; truecolor?: boolean; clock?: () => number; mouseCaret?: boolean } = {}): InkSessionScreen {
  const screen = createInkSessionScreen({ stdin: term.stdin, stdout: term.stdout, color: false, ...options });
  screens.push(screen);
  return screen;
}

const WORKING = { state: 'working', label: 'Running npm test', percent: null, since: 4000, tokens: 48 } as const;
const STEPS = [
  { id: 'S1', title: 'Read cli.py', status: 'completed' },
  { id: 'S2', title: 'Add the flag', status: 'in_progress' },
  { id: 'S3', title: 'Run the tests', status: 'pending' },
];
const LIVE = { calls: [{ label: 'Bash', target: 'npm test' }], plan: STEPS, agents: [], notes: [] };
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const writes = (term: FakeTerminal): number => (term.raw.match(/\x1b\[\?2026h/g) ?? []).length;

describe('the status row against the clock', () => {
  it('counts the seconds from the turn\'s start, above the box, and is followed by the plan and the footer', async () => {
    let now = 10_750;
    const term = new FakeTerminal(100, 30);
    const screen = open(term, { clock: () => now });
    screen.setBusy(true);
    screen.setProgress({ ...WORKING });
    screen.setLive({ ...LIVE, calls: [] });
    await term.settle();
    const view = term.viewport();
    const at = view.findIndex((row) => row.startsWith('┃ Running npm test…'));
    expect(view[at]).toBe('┃ Running npm test… (6s · ↓ 48 tokens · esc to interrupt)');
    expect(view.slice(at + 1, at + 4)).toEqual(['│ ◉ Read cli.py', '│ ◐ Add the flag', '│ ○ Run the tests']);
    expect(view[at + 4]!.trim()).toBe('▌');
    expect(view[at + 5]).toBe(' esc/ctrl-c to interrupt · enter queues the next task');
    expect(view[view.length - 1]).toBe('');
    now = 14_750;
    await pause(250);
    await term.settle();
    expect(term.viewport().some((row) => row.startsWith('┃ Running npm test… (10s · '))).toBe(true);
    expect(forbiddenIn(term.raw)).toEqual([]);
  });

  it('leaves no timer running once the turn and the row are gone, the screen is handed over, or it is stopped', async () => {
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
      screen.setProgress({ ...WORKING, label: 'Reading' });
      screen.setLive(LIVE);
      expect(running()).toBe(1);
      screen.setProgress(null);
      // A running tool call is steady: it needs no clock of its own.
      expect(running()).toBe(0);
      // A turn that runs keeps the colour cycle of the composer's bar moving.
      screen.setBusy(true);
      expect(running()).toBe(1);
      screen.setBusy(false);
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

  it('writes nothing while nothing changes, and a frame when the second does', async () => {
    let now = 10_000;
    const term = new FakeTerminal(80, 24);
    const screen = open(term, { clock: () => now });
    screen.setProgress({ ...WORKING });
    await term.settle();
    const first = writes(term);
    await pause(350);
    await term.settle();
    // No colour: only the elapsed time moves, so the frame is rewritten when the second changes, no oftener.
    const idle = writes(term);
    expect(idle).toBe(first);
    now = 10_500;
    await pause(250);
    await term.settle();
    expect(writes(term)).toBe(idle);
    now = 11_000;
    await pause(250);
    await term.settle();
    expect(writes(term)).toBeGreaterThan(idle);
  });

  it('writes a frame when the colour of the rail cell moves, in colour, and not within the same colour', async () => {
    let now = 10_000;
    const term = new FakeTerminal(80, 24);
    const screen = open(term, { color: true, truecolor: true, clock: () => now });
    screen.setProgress({ ...WORKING, since: 9000 });
    await term.settle();
    const first = writes(term);
    await pause(250);
    await term.settle();
    expect(writes(term)).toBe(first);
    now = 10_600;
    await pause(250);
    await term.settle();
    expect(writes(term)).toBeGreaterThan(first);
  });

  it('keeps the box and the footer where they were while the row changes', async () => {
    let now = 10_000;
    const term = new FakeTerminal(80, 24);
    const screen = open(term, { clock: () => now });
    screen.setBusy(true);
    screen.setProgress({ ...WORKING });
    await term.settle();
    const where = term.viewport().findIndex(isField);
    for (let step = 0; step < 6; step += 1) {
      now += 300;
      screen.setProgress({ ...WORKING, label: step % 2 ? 'Reading parser.py' : 'Running npm test', tokens: 48 + step * 100 });
      await pause(30);
      await term.settle();
      expect(term.viewport().findIndex(isField)).toBe(where);
      expect(term.all().filter((row) => /^┃ \S.*…( \(.*\))?$/.test(row))).toHaveLength(1);
    }
  });
});

describe('the plan and the running call', () => {
  it('draws the call on a steady rail above the status row, and the plan under it', async () => {
    let now = 10_000;
    const term = new FakeTerminal(100, 30);
    const screen = open(term, { clock: () => now });
    screen.setBusy(true);
    screen.setProgress({ ...WORKING, since: 9000 });
    screen.setLive(LIVE);
    await term.settle();
    const call = () => term.viewport().findIndex((row) => row === '│ Bash npm test');
    expect(term.viewport()[call()]).toBe('│ Bash npm test');
    expect(term.viewport()[call() + 1]).toMatch(/^┃ Running npm test…/);
    // The old bullet blinked every 600 ms; the rail does not.
    now = 10_600;
    await pause(250);
    await term.settle();
    expect(term.viewport()[call()]).toBe('│ Bash npm test');
  });

  it('strips every escape from what the model and the backend named, so a plan or a command cannot move the cursor', async () => {
    const term = new FakeTerminal(100, 30);
    const screen = open(term);
    screen.setBusy(true);
    screen.setProgress({ ...WORKING, label: 'Running \x1b[2J\x1b[3Jrm -rf' });
    screen.setLive({
      calls: [{ label: 'Bash\x1b[?1049h', target: 'echo \x1b[1;5r hi\x07' }],
      plan: [{ id: 'S1', title: '\x1b[2Jstep\x1b[3J one', status: 'pending' }],
      agents: [{ id: '1', number: 1, kind: 'work\x1b[2Jer', status: 'running', task: 'x\x1b]0;title\x07y' }],
      notes: [{ state: 'running', text: 'review\x1b[3J' }],
    });
    await term.settle();
    expect(forbiddenIn(term.raw)).toEqual([]);
    expect(term.raw).not.toMatch(/\x1b\[2J|\x1b\]0;/);
    expect(term.viewport().some((row) => row === '│ Bash echo hi')).toBe(true);
    expect(term.viewport().some((row) => row.endsWith('○ step one'))).toBe(true);
    expect(term.viewport().some((row) => row.endsWith('⎇ 1 worker  running  xy') || row.includes('running  x'))).toBe(true);
  });
});

describe('colour', () => {
  async function drawn(options: { color: boolean; truecolor: boolean }): Promise<string> {
    const term = new FakeTerminal(120, 36);
    const screen = open(term, { ...options, clock: () => 10_750 });
    screen.setBusy(true);
    screen.setMeter({ tokens_in: 14500, tokens_out: 48, lines_added: 3, lines_removed: 1 });
    screen.setProgress({ ...WORKING, since: 4750 });
    screen.setLive(LIVE);
    await term.settle();
    return term.raw;
  }

  it('is truecolor where the terminal has it: the colour cycle of the rail cell and the bar, the lime call, the dim brackets, the checklist', async () => {
    const raw = await drawn({ color: true, truecolor: true });
    const cycle = fgCode(gradientAt(gradientPhase(10_750)), true);
    expect(raw).toContain(`${cycle}┃\x1b[39m Running npm test…`);
    expect(raw).toContain(`${cycle}▌\x1b[39m `);
    expect(raw).toContain(`${fgCode(LIME, true)}│\x1b[39m \x1b[1mBash\x1b[22m npm test`);
    expect(raw).toContain('\x1b[2m (6s · ↓ 48 tokens · esc to interrupt)\x1b[22m');
    expect(raw).toContain(`${fgCode(LIME, true)}◐\x1b[39m`);
    expect(raw).toContain('\x1b[9mRead cli.py\x1b[29m');
    // No rule above or below the box.
    expect(raw).not.toMatch(/[─━]{3}/);
    // The counts: +N green, -N red.
    expect(raw).toMatch(/\x1b\[38;2;34;197;94m\+3\x1b\[(?:0|39)m/);
    expect(raw).not.toMatch(/\x1b\[38;5;/);
  });

  it('is the nearest of 256 where it has not', async () => {
    const raw = await drawn({ color: true, truecolor: false });
    expect(raw).toContain(fgCode(gradientAt(gradientPhase(10_750)), false) + '┃');
    expect(raw).toContain(fgCode(LIME, false) + '│');
    expect(fgCode(LIME, false)).toMatch(/^\x1b\[38;5;\d+m$/);
    expect(raw).not.toMatch(/\x1b\[38;2;/);
  });

  it('is none at all with NO_COLOR: the glyphs stay, no colour or attribute is asked for', async () => {
    const term = new FakeTerminal(120, 36);
    const screen = open(term, { color: false, truecolor: true, clock: () => 10_750 });
    screen.setBusy(true);
    screen.setProgress({ ...WORKING, since: 4750 });
    screen.setLive(LIVE);
    await term.settle();
    // Only the caret is drawn in inverse; nothing is coloured, dimmed, bold or struck through.
    expect(term.raw).not.toMatch(/\x1b\[(?:38|39|48|49|1m|2m|3m|4m|9m|22m|29m)/);
    expect(term.viewport().some((row) => row.includes('◉ Read cli.py'))).toBe(true);
  });
});

describe('the footer', () => {
  it('says Enter sends between turns, and the caption at its right edge', async () => {
    const term = new FakeTerminal(100, 24);
    const screen = open(term);
    screen.setCaption({ clock: '09:30:00', provider: 'Prov', model: 'Model' });
    screen.setMeter({ tokens_in: 14500, tokens_out: 48, lines_added: 3, lines_removed: 1 });
    await term.settle();
    const view = term.viewport();
    const footer = view[view.length - 2]!;
    expect(footer.startsWith(' Enter to send  +3 lines, -1 line, ~14k context, ~48 out  ')).toBe(true);
    expect(footer.endsWith('  09:30:00 · Prov · Model')).toBe(true);
    // As wide as the box: the window less its spare column.
    expect(footer).toHaveLength(99);
    expect(view[view.length - 1]).toBe('');
  });

  it('says how to stop a turn, that Enter queues, and how many lines wait, in the footer and not the box', async () => {
    const term = new FakeTerminal(100, 24);
    const screen = open(term);
    const sent: string[] = [];
    screen.onSubmit((line) => sent.push(line));
    screen.setBusy(true);
    await term.settle();
    const view = () => term.viewport();
    expect(view()[view().length - 2]).toBe(' esc/ctrl-c to interrupt · enter queues the next task');
    expect(view().find(isField)!.trim()).toBe('▌');
    term.type('run the tests');
    term.type('\r');
    await term.settle();
    expect(screen.queued()).toEqual(['run the tests']);
    expect(view()[view().length - 2]).toBe(' esc/ctrl-c to interrupt · enter queues the next task · 1 queued');
    // The queued line is not listed anywhere, and the box is empty again.
    expect(view().some((row) => row.includes('run the tests'))).toBe(false);
    expect(view().find(isField)!.trim()).toBe('▌');
    screen.setBusy(false);
    expect(sent).toEqual(['run the tests']);
  });

  it('is the question\'s own line while one is open, and comes back after', async () => {
    const term = new FakeTerminal(100, 24);
    const screen = open(term);
    screen.setBusy(true);
    const answer = screen.ask({ hint: 'Press y or n then Enter', footer: 'esc refuses · ctrl-c interrupts' });
    await term.settle();
    expect(term.viewport()[term.viewport().length - 2]).toBe(' esc refuses · ctrl-c interrupts');
    expect(term.viewport().find(isField)).toBe('▌ Press y or n then Enter');
    term.type('y');
    term.type('\r');
    expect(await answer).toBe('y');
    await term.settle();
    expect(term.viewport()[term.viewport().length - 2]).toBe(' esc/ctrl-c to interrupt · enter queues the next task');
  });

  it('is cut to the window, never wider', async () => {
    const term = new FakeTerminal(30, 12);
    const screen = open(term);
    screen.setBusy(true);
    screen.setProgress({ ...WORKING });
    screen.setLive(LIVE);
    await term.settle();
    for (const row of term.viewport()) expect(displayWidth(row)).toBeLessThanOrEqual(29);
  });
});

describe('Esc', () => {
  it('stops a running turn when the line is empty, and clears the line when there is one', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const keys: string[] = [];
    screen.onInterrupt((key) => keys.push(key));
    screen.setBusy(true);
    term.type('half a line');
    term.type('\x1b');
    await pause(60);
    expect(keys).toEqual([]);
    await term.settle();
    expect(term.viewport().find(isField)!.trim()).toBe('▌');
    term.type('\x1b');
    await pause(60);
    expect(keys).toEqual(['escape']);
    // Between turns it stops nothing.
    screen.setBusy(false);
    term.type('\x1b');
    await pause(60);
    expect(keys).toEqual(['escape']);
  });

  it('closes an open question first, and does not stop the turn', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const keys: string[] = [];
    screen.onInterrupt((key) => keys.push(key));
    screen.setBusy(true);
    const answer = screen.ask({ hint: 'Press y' });
    term.type('\x1b');
    await pause(60);
    expect(await answer).toBeNull();
    expect(keys).toEqual([]);
  });
});
