// The Ink screen's live frame in the signal rail look, as a pure function of
// its state and the window: the status row, the plan, the reply, the composer
// and the footer, and that however much there is to show it is never taller
// than the window less the cursor's row.

import { describe, expect, it } from 'vitest';
import { LineEditor } from '../src/input/editor.js';
import { displayWidth, stripAnsi } from '../src/input/width.js';
import { composeFrame, type FrameState } from '../src/ink/layout.js';
import { agentRows, footerRow, noteRows, planRows, replyRows, statusRow, type Activity, type Live } from '../src/ink/live.js';
import type { PlanStep } from '../src/protocol.js';
import { fgCode, GRADIENT_CYCLE_MS, gradientAt, gradientPhase, LIME, makeTheme, ORANGE, YELLOW } from '../src/theme.js';

const NOW = 10_000;
const PLAIN = makeTheme({ color: false, truecolor: true });
const COLOR = makeTheme({ color: true, truecolor: true });
const CAPTION = { clock: '14:39:37', provider: 'OpenRouter', model: 'Nemotron 3 Ultra 550B' };
const COUNTED = { tokens_in: 14500, tokens_out: 48, tokens_out_exact: false, lines_added: 3, lines_removed: 1 };
const THINKING: Activity = { state: 'working', label: 'Thinking', percent: null, since: NOW - 6000, tokens: 48 };
const STEPS: PlanStep[] = [
  { id: 'S1', title: 'Read cli.py', status: 'completed' },
  { id: 'S2', title: 'Add the flag', status: 'in_progress' },
  { id: 'S3', title: 'Run the tests', status: 'pending' },
  { id: 'S4', title: 'Fix the lint', status: 'blocked' },
];
const LIVE: Live = {
  calls: [{ label: 'Bash', target: 'npm test' }], plan: STEPS,
  agents: [{ id: '1', number: 1, kind: 'worker', status: 'running', task: 'port the parser', activity: 'Reading parser.py' }],
  notes: [{ state: 'running', text: 'Reviewing the diff' }],
};

const state = (over: Partial<FrameState> = {}): FrameState => ({
  editor: new LineEditor(), reply: '', status: '', caption: '', meter: null, progress: null, panel: null,
  pending: [], busy: false, ask: null, color: false, truecolor: true, now: NOW, ...over,
});
const plain = (over: Partial<FrameState>, columns = 80, rows = 24): string[] => composeFrame(state(over), columns, rows).plain;
/** A field with `text` typed into it (the constructor's argument is the placeholder). */
const typed = (text: string): LineEditor => {
  const editor = new LineEditor();
  editor.insert(text);
  return editor;
};
const BUSY = { busy: true, progress: THINKING, live: LIVE, reply: 'one\ntwo\nthree\nfour\nfive\nsix', meter: COUNTED };

describe('the status row', () => {
  it('is a rail cell, what is happening and the readout, the hint last', () => {
    expect(stripAnsi(statusRow(THINKING, NOW, 100, PLAIN))).toBe('┃ Thinking… (6s · ↓ 48 tokens · esc to interrupt)');
    expect(stripAnsi(statusRow({ ...THINKING, label: 'Running npm test', tokens: 1240, since: NOW - 83_000 }, NOW, 100, PLAIN)))
      .toBe('┃ Running npm test… (1m 23s · ↓ 1.2k tokens · esc to interrupt)');
    expect(stripAnsi(statusRow({ ...THINKING, tokens: 0 }, NOW, 100, PLAIN))).toBe('┃ Thinking… (6s · esc to interrupt)');
  });

  it('does not say the model is working, or offer to interrupt, while it waits for the user', () => {
    expect(stripAnsi(statusRow({ ...THINKING, label: 'Waiting for your approval', waiting: true }, NOW, 100, PLAIN))).toBe('┃ Waiting for your approval… (6s · ↓ 48 tokens)');
  });

  it('gives up the hint, then the tokens, then the clock, as the window narrows, and then cuts the words', () => {
    const at = (width: number) => stripAnsi(statusRow({ ...THINKING, label: 'Reading parser.py' }, NOW, width, PLAIN));
    expect(at(60)).toBe('┃ Reading parser.py… (6s · ↓ 48 tokens · esc to interrupt)');
    expect(at(50)).toBe('┃ Reading parser.py… (6s · ↓ 48 tokens)');
    expect(at(30)).toBe('┃ Reading parser.py… (6s)');
    expect(at(22)).toBe('┃ Reading parser.py…');
    expect(at(12)).toBe('┃ Reading p…');
    for (const width of [5, 12, 22, 30, 50, 60, 100]) expect(displayWidth(at(width))).toBeLessThanOrEqual(width);
  });

  it('draws its rail cell on the shared colour cycle: red at the start, green half a cycle on, red again after a cycle', () => {
    const cell = (now: number) => statusRow(THINKING, now, 100, COLOR).split('┃')[0]!;
    expect(cell(0)).toBe(fgCode(gradientAt(0), true));
    expect(cell(0)).toBe('\x1b[38;2;220;38;38m');
    expect(cell(GRADIENT_CYCLE_MS / 2)).toBe(fgCode(gradientAt(0.5), true));
    expect(cell(GRADIENT_CYCLE_MS / 2)).not.toBe(cell(0));
    expect(cell(GRADIENT_CYCLE_MS)).toBe(cell(0));
    expect(cell(300)).toBe(fgCode(gradientAt(gradientPhase(300)), true));
  });

  it('has the words in the terminal\'s own colour and the suffix dim, and the same words at every moment of the cycle', () => {
    const row = statusRow({ ...THINKING, since: 101_800 - 6000 }, 101_800, 100, COLOR);
    expect(row).toContain('┃\x1b[39m Thinking…');
    expect(row.endsWith('Thinking…\x1b[2m (6s · ↓ 48 tokens · esc to interrupt)\x1b[22m')).toBe(true);
    expect(stripAnsi(statusRow({ ...THINKING, since: 101_800 - 6000 }, 101_900, 100, COLOR))).toBe(stripAnsi(row));
  });

  it('keeps the rail orange and still while the user is being asked', () => {
    const waiting = { ...THINKING, label: 'Waiting for your approval', waiting: true };
    const rail = `${fgCode(ORANGE, true)}┃\x1b[39m`;
    expect(statusRow(waiting, 0, 100, COLOR).startsWith(rail)).toBe(true);
    expect(statusRow(waiting, 1234, 100, COLOR).startsWith(rail)).toBe(true);
  });
});

describe('the plan', () => {
  it('is a checklist on a thin rail: ○ waiting, ◐ in progress, ◉ done, ! blocked', () => {
    expect(planRows(STEPS, 6, 80, PLAIN)).toEqual(['│ ◉ Read cli.py', '│ ◐ Add the flag', '│ ○ Run the tests', '│ ! Fix the lint']);
  });

  it('paints the step in progress lime and bold, a done step dim and struck through, a blocked one yellow', () => {
    const [done, active, waiting, blocked] = planRows(STEPS, 6, 80, COLOR);
    const rail = '\x1b[2m│\x1b[22m';
    expect(done).toBe(`${rail} \x1b[2m◉ \x1b[9mRead cli.py\x1b[29m\x1b[22m`);
    expect(active).toBe(`${rail} ${fgCode(LIME, true)}◐\x1b[39m \x1b[1mAdd the flag\x1b[22m`);
    expect(waiting).toBe(`${rail} ○ Run the tests`);
    expect(blocked.startsWith(`${rail} ${fgCode(YELLOW, true)}!\x1b[39m`)).toBe(true);
  });

  it('shows a window of the steps and how many more, when there are more than the room', () => {
    const steps: PlanStep[] = Array.from({ length: 9 }, (_, index) => ({ id: `S${index}`, title: `step ${index}`, status: index < 4 ? 'completed' : index === 4 ? 'in_progress' : 'pending' }));
    // Six rows: five steps from the one before the first not done, and the rest counted.
    expect(planRows(steps, 6, 80, PLAIN)).toEqual(['│ ◉ step 3', '│ ◐ step 4', '│ ○ step 5', '│ ○ step 6', '│ ○ step 7', '│ … +4 more']);
    expect(planRows(steps, 2, 80, PLAIN)).toEqual(['│ ◐ step 4', '│ … +8 more']);
    expect(planRows(steps, 1, 80, PLAIN)).toEqual(['│ ◐ step 4']);
    expect(planRows(steps, 0, 80, PLAIN)).toEqual([]);
    expect(planRows([], 6, 80, PLAIN)).toEqual([]);
    // Everything done: the last steps.
    expect(planRows(steps.map((step) => ({ ...step, status: 'completed' })), 3, 80, PLAIN)).toEqual(['│ ◉ step 7', '│ ◉ step 8', '│ … +7 more'].slice(0, 3));
  });

  it('cuts a long title to the row', () => {
    const rows = planRows([{ id: 'S1', title: 'x'.repeat(100), status: 'pending' }], 6, 30, PLAIN);
    expect(displayWidth(rows[0]!)).toBeLessThanOrEqual(30);
    expect(rows[0]!.endsWith('…')).toBe(true);
  });
});

describe('agents, notes and the reply', () => {
  it('shows an agent that works as ⎇ number kind status and what it is doing', () => {
    expect(agentRows(LIVE.agents, 3, 80, PLAIN)).toEqual(['⎇ 1 worker  running  Reading parser.py']);
    const many = Array.from({ length: 5 }, (_, index) => ({ id: String(index), number: index + 1, kind: 'worker', status: 'running', task: `task ${index}` }));
    expect(agentRows(many, 3, 80, PLAIN)).toEqual(['⎇ 1 worker  running  task 0', '⎇ 2 worker  running  task 1', '  … +3 more']);
  });

  it('shows a review or a verification that runs as a glyph and its headline, the glyph lime while it runs', () => {
    expect(noteRows(LIVE.notes, 2, 80, PLAIN)).toEqual(['· Reviewing the diff']);
    expect(noteRows([{ state: 'passed', text: 'ok' }, { state: 'warning', text: 'hm' }, { state: 'failed', text: 'no' }], 2, 80, PLAIN)).toEqual(['✓ ok', '! hm']);
    expect(noteRows(LIVE.notes, 2, 80, COLOR)).toEqual([`${fgCode(LIME, true)}·\x1b[39m Reviewing the diff`]);
  });

  it('is the markdown on a heavy lime rail, the last rows of it, the blank rows inside it on the rail too', () => {
    expect(replyRows('I wrote **notes.txt**.\n\n- one\n- two', 10, 80, PLAIN)).toEqual(['┃ I wrote notes.txt.', '┃', '┃ - one', '┃ - two']);
    expect(replyRows('a\nb\nc\nd', 2, 80, PLAIN)).toEqual(['┃ c', '┃ d']);
    expect(replyRows('word '.repeat(40), 10, 30, PLAIN).every((row) => displayWidth(row) <= 30)).toBe(true);
    expect(replyRows('a', 10, 80, COLOR)).toEqual([`${fgCode(LIME, true)}┃\x1b[39m a`]);
  });
});

describe('the footer', () => {
  const footer = (over: Partial<Parameters<typeof footerRow>[0]> = {}, width = 100) => stripAnsi(footerRow({
    status: '', asking: false, askFooter: '', busy: false, queued: 0, caption: CAPTION, meter: null, ...over,
  }, width, { color: false, truecolor: true }, PLAIN));

  it('says Enter sends between turns, with the clock, provider and model at the right edge', () => {
    expect(footer()).toBe(' Enter to send' + ' '.repeat(100 - 14 - 45) + '14:39:37 · OpenRouter · Nemotron 3 Ultra 550B');
    expect(footer()).toHaveLength(100);
  });

  it('carries the counts after the hint when they fit, the caption giving up the provider and the model first', () => {
    const wide = footer({ meter: COUNTED }, 120);
    expect(wide).toBe(' Enter to send  +3 lines, -1 line, ~14k context, ~48 out' + ' '.repeat(120 - 56 - 45) + '14:39:37 · OpenRouter · Nemotron 3 Ultra 550B');
    // Between the full sentence and nothing, the short form of the counts; the caption is not given up for them.
    const narrow = footer({ meter: COUNTED }, 95);
    expect(narrow.startsWith(' Enter to send  +3 -1  ~14k ctx  ~48 out')).toBe(true);
    expect(narrow.endsWith('14:39:37 · OpenRouter · Nemotron 3 Ultra 550B')).toBe(true);
    expect(displayWidth(narrow)).toBe(95);
    expect(footer({ meter: COUNTED }, 70)).toBe(' Enter to send  +3 -1' + ' '.repeat(70 - 14 - 7 - 45) + '14:39:37 · OpenRouter · Nemotron 3 Ultra 550B');
    expect(footer({}, 50)).toBe(' Enter to send' + ' '.repeat(50 - 14 - 32) + '14:39:37 · Nemotron 3 Ultra 550B');
    expect(footer({}, 40)).toBe(' Enter to send' + ' '.repeat(40 - 14 - 8) + '14:39:37');
    expect(footer({}, 20)).toBe(' Enter to send');
  });

  it('says how to stop a turn and that Enter queues, and how many lines wait', () => {
    expect(footer({ busy: true })).toBe(' esc/ctrl-c to interrupt · enter queues the next task');
    expect(footer({ busy: true, queued: 2 })).toBe(' esc/ctrl-c to interrupt · enter queues the next task · 2 queued');
    expect(footer({ busy: true, queued: 2 }, 50)).toBe(' esc to interrupt · enter queues · 2 queued');
    expect(footer({ busy: true, queued: 2 }, 35)).toBe(' esc to interrupt · 2 queued');
    expect(footer({ busy: true, queued: 2 }, 20)).toBe(' esc to interrupt ·…');
    expect(footer({ busy: true, meter: COUNTED }, 120)).toContain('  +3 lines, -1 line, ~14k context, ~48 out');
  });

  it('is the question\'s own line while one is open, and a status row replaces all of it', () => {
    expect(footer({ asking: true, askFooter: 'esc refuses · ctrl-c interrupts' })).toBe(' esc refuses · ctrl-c interrupts');
    expect(footer({ asking: true })).toBe(' esc to cancel');
    expect(footer({ status: 'status: thinking', busy: true })).toBe(' status: thinking');
  });
});

describe('the frame', () => {
  it('is, top to bottom: the reply, a blank, the call, the status, the plan, the notes, the agents, the box, the footer', () => {
    const rows = plain(BUSY, 80, 40);
    const at = (text: string) => rows.findIndex((row) => row.includes(text));
    expect(at('┃ one')).toBe(0);
    expect(rows[6]).toBe('');
    expect(rows[7]).toBe('│ Bash npm test');
    expect(rows[8]).toMatch(/^┃ Thinking… \(6s · ↓ 48 tokens · esc to interrupt\)$/);
    expect(rows.slice(9, 13)).toEqual(['│ ◉ Read cli.py', '│ ◐ Add the flag', '│ ○ Run the tests', '│ ! Fix the lint']);
    expect(rows[13]).toBe('· Reviewing the diff');
    expect(rows[14]).toBe('⎇ 1 worker  running  Reading parser.py');
    expect(rows[15].trimEnd()).toBe('▌');
    expect(rows[16]).toMatch(/^ esc\/ctrl-c to interrupt · enter queues the next task {2}\+3 -1/);
    expect(rows).toHaveLength(17);
  });

  it('has no rule above or below the field, one bar in front of it, the placeholder dim', () => {
    const editor = Object.assign(new LineEditor('Describe your first task'), { placeholderVisible: true });
    const frame = composeFrame(state({ editor, color: true, caption: CAPTION }), 120, 36);
    expect(frame.plain.slice(0, 2)).toEqual(['▌ Describe your first task', expect.stringMatching(/^ Enter to send/)]);
    expect(frame.plain.some((row) => /^ ?[─━]+$/.test(row))).toBe(false);
    expect(frame.rows[0]!.startsWith(`${fgCode(LIME, true)}▌\x1b[39m `)).toBe(true);
    expect(frame.rows[0]).toContain('\x1b[2m');
    expect(frame.field).toMatchObject({ first: 0, count: 1, prefix: 2 });
  });

  it('keeps the bar on every row of a field that wraps', () => {
    const frame = composeFrame(state({ editor: typed('word '.repeat(30)) }), 40, 24);
    const rows = frame.plain.slice(frame.field.first, frame.field.first + frame.field.count);
    expect(rows.length).toBeGreaterThan(2);
    for (const row of rows) expect(row.startsWith('▌ ')).toBe(true);
    expect(frame.field.inner).toBe(40 - 1 - 2);
  });

  it('colours the bar by state: lime between turns, the colour cycle while one runs, orange while a question is open', () => {
    const bar = (over: Partial<FrameState>, now = NOW) => composeFrame(state({ color: true, now, ...over }), 80, 24).rows.find((row) => row.includes('▌'))!.split('▌')[0]!;
    expect(bar({})).toBe(fgCode(LIME, true));
    expect(bar({ busy: true }, 0)).toBe(fgCode(gradientAt(0), true));
    expect(bar({ busy: true }, GRADIENT_CYCLE_MS / 2)).toBe(fgCode(gradientAt(0.5), true));
    expect(bar({ busy: true }, 0)).not.toBe(bar({ busy: true }, GRADIENT_CYCLE_MS / 2));
    // A question open in the middle of a turn wins over the turn.
    expect(bar({ busy: true, ask: 'Press y or n' })).toBe(fgCode(ORANGE, true));
    expect(bar({ ask: 'Press y or n' })).toBe(fgCode(ORANGE, true));
  });

  it('draws a blank row above the status row only when there are rows to spare', () => {
    expect(plain({ progress: THINKING, busy: true }, 80, 24)[0]).toBe('');
    expect(plain({ progress: THINKING, busy: true }, 80, 24)[1]).toMatch(/^┃ Thinking/);
    expect(plain({ progress: THINKING, busy: true }, 80, 12)[0]).toMatch(/^┃ Thinking/);
  });

  it('keeps the field, the footer and the status row when the window is short, the reply three rows first', () => {
    for (const rows of [5, 6, 8, 12]) {
      const frame = plain({ ...BUSY, reply: 'a\nb\nc\nd\ne\nf' }, 80, rows);
      expect(frame.length, `${rows} rows`).toBeLessThanOrEqual(rows - 1);
      expect(frame.filter((row) => row.startsWith('▌')), `${rows} rows`).toHaveLength(1);
      expect(frame.at(-1), `${rows} rows`).toMatch(/^ esc/);
    }
    const at12 = plain({ ...BUSY, reply: 'a\nb\nc\nd\ne\nf', live: { ...LIVE, plan: STEPS } }, 80, 12);
    expect(at12.some((row) => /^┃ Thinking/.test(row))).toBe(true);
    expect(at12.filter((row) => /^┃ [a-f]$/.test(row))).toHaveLength(3);
    // The footer is the last row of the frame.
    expect(at12.at(-1)).toMatch(/^ esc/);
  });

  it.each([[2, 1], [3, 1], [4, 2], [6, 4], [8, 5]] as const)('gives a long field every row but the footer\'s, and the footer when it fits (%i rows, %i field rows)', (rows, fieldRows) => {
    const frame = composeFrame(state({ ...BUSY, editor: typed('word '.repeat(60)) }), 40, rows);
    expect(frame.rows.length).toBeLessThanOrEqual(rows - 1);
    expect(frame.field.count).toBe(fieldRows);
    expect(frame.field.maxRows).toBeGreaterThanOrEqual(1);
    expect(frame.plain.filter((row) => row.startsWith('▌ ') || row === '▌')).toHaveLength(frame.field.count);
    // The footer is the row under the field when there is one to spare.
    if (rows > 2) expect(frame.plain.at(-1)).toMatch(/^ esc/);
  });

  it.each([[80, 24], [40, 12], [200, 50], [30, 8], [120, 6]] as const)('is never taller than the window less one, nor wider than it, with everything to show (%i x %i)', (columns, rows) => {
    const many: Live = {
      calls: [{ label: 'Bash', target: 'x'.repeat(300) }, { label: 'Read File', target: 'y' }, { label: 'Third', target: 'z' }],
      plan: Array.from({ length: 30 }, (_, index) => ({ id: `S${index}`, title: `a plan step with a long title number ${index}`, status: index < 9 ? 'completed' : 'pending' })),
      agents: Array.from({ length: 8 }, (_, index) => ({ id: String(index), number: index, kind: 'worker', status: 'running', task: 't'.repeat(200) })),
      notes: [{ state: 'running', text: 'r'.repeat(300) }, { state: 'running', text: 'v' }, { state: 'running', text: 'w' }],
    };
    const editor = new LineEditor('word '.repeat(60));
    for (const color of [false, true]) {
      const frame = composeFrame(state({
        busy: true, progress: { ...THINKING, label: 'x'.repeat(300) }, live: many, reply: Array.from({ length: 80 }, (_, index) => `reply row ${index}`).join('\n'),
        meter: COUNTED, pending: ['one', 'two'], editor, color,
      }), columns, rows);
      expect(frame.rows.length, `${columns}x${rows}`).toBeLessThanOrEqual(rows - 1);
      for (const row of frame.plain) expect(displayWidth(row)).toBeLessThanOrEqual(columns - 1);
      expect(frame.rows.map(stripAnsi)).toEqual(frame.plain);
    }
  });

  it('is cut to its room by the plan before the reply, the reply keeping three rows', () => {
    const big: Live = { ...LIVE, calls: [], notes: [], agents: [], plan: Array.from({ length: 30 }, (_, index) => ({ id: `S${index}`, title: `step ${index}`, status: 'pending' })) };
    const frame = plain({ busy: true, progress: THINKING, live: big, reply: 'a\nb\nc\nd\ne' }, 80, 14);
    // 13 rows: the box and the footer 2, the status row and a blank above it 2, leaves 9: three for the reply, six for the plan.
    expect(frame.filter((row) => /^┃ [a-e]$/.test(row))).toEqual(['┃ c', '┃ d', '┃ e']);
    expect(frame.filter((row) => row.includes('○'))).toHaveLength(5);
    expect(frame.filter((row) => row.includes('… +25 more'))).toHaveLength(1);
  });

  it('puts a question\'s hint in the empty box and its own footer under it, over the turn\'s', () => {
    const rows = plain({ busy: true, progress: THINKING, ask: 'Press y, a or n then Enter', askFooter: 'esc refuses · ctrl-c interrupts' }, 80, 24);
    expect(rows).toContain('▌ Press y, a or n then Enter');
    expect(rows.at(-1)).toBe(' esc refuses · ctrl-c interrupts');
  });

  it('leaves the box empty while a turn runs, and counts the queue in the footer instead', () => {
    const rows = plain({ busy: true, pending: ['a', 'b'] }, 80, 24);
    expect(rows.find((row) => row.startsWith('▌'))!.trimEnd()).toBe('▌');
    expect(rows.at(-1)).toBe(' esc/ctrl-c to interrupt · enter queues the next task · 2 queued');
  });

  it('still sits the right-hand column beside the left, when a caller gives one', () => {
    const panel = { width: 20, rows: ['PLAN 0/2', '─'.repeat(20), 'S1 ● Read'] };
    const frame = composeFrame(state({ panel, busy: true, progress: THINKING }), 100, 24);
    expect(frame.plain[0]!.endsWith('  PLAN 0/2')).toBe(true);
    for (const row of frame.plain) expect(displayWidth(row)).toBeLessThanOrEqual(99);
  });
});
