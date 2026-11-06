// The Ink screen's live frame in the Claude Code look, as a pure function of
// its state and the window: the status row, the plan, the reply, the box and
// the footer, and that however much there is to show it is never taller than
// the window less the cursor's row.

import { describe, expect, it } from 'vitest';
import { LineEditor } from '../src/input/editor.js';
import { displayWidth, stripAnsi } from '../src/input/width.js';
import { composeFrame, type FrameState } from '../src/ink/layout.js';
import { agentRows, footerRow, noteRows, planRows, replyRows, statusRow, type Activity, type Live } from '../src/ink/live.js';
import type { PlanStep } from '../src/protocol.js';
import { ACCENT, ACCENT_SHIMMER, BORDER, fgCode, makeTheme } from '../src/theme.js';

const NOW = 10_000;
/** A moment at which the star is a ✻ (750 ms into its two-second breath), and six seconds into the turn. */
const STAR = NOW + 750;
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
const BUSY = { busy: true, progress: THINKING, live: LIVE, reply: 'one\ntwo\nthree\nfour\nfive\nsix', meter: COUNTED };

describe('the status row', () => {
  it('is a star, what is happening and the readout, the hint last', () => {
    expect(stripAnsi(statusRow(THINKING, STAR, 100, PLAIN))).toBe('✻ Thinking… (6s · ↓ 48 tokens · esc to interrupt)');
    expect(stripAnsi(statusRow({ ...THINKING, label: 'Running npm test', tokens: 1240, since: STAR - 83_000 }, STAR, 100, PLAIN)))
      .toBe('✻ Running npm test… (1m 23s · ↓ 1.2k tokens · esc to interrupt)');
    expect(stripAnsi(statusRow({ ...THINKING, tokens: 0 }, STAR, 100, PLAIN))).toBe('✻ Thinking… (6s · esc to interrupt)');
  });

  it('does not say the model is working, or offer to interrupt, while it waits for the user', () => {
    expect(stripAnsi(statusRow({ ...THINKING, label: 'Waiting for your approval', waiting: true }, STAR, 100, PLAIN))).toBe('✻ Waiting for your approval… (6s · ↓ 48 tokens)');
  });

  it('gives up the hint, then the tokens, then the clock, as the window narrows, and then cuts the words', () => {
    const at = (width: number) => stripAnsi(statusRow({ ...THINKING, label: 'Reading parser.py' }, STAR, width, PLAIN));
    expect(at(60)).toBe('✻ Reading parser.py… (6s · ↓ 48 tokens · esc to interrupt)');
    expect(at(50)).toBe('✻ Reading parser.py… (6s · ↓ 48 tokens)');
    expect(at(30)).toBe('✻ Reading parser.py… (6s)');
    expect(at(22)).toBe('✻ Reading parser.py…');
    expect(at(12)).toBe('✻ Reading p…');
    for (const width of [5, 12, 22, 30, 50, 60, 100]) expect(displayWidth(at(width))).toBeLessThanOrEqual(width);
  });

  it('turns the star on a cosine over two seconds, through the six glyphs and back', () => {
    const at = (now: number) => stripAnsi(statusRow(THINKING, now, 100, PLAIN))[0]!;
    const frames = ['·', '✢', '*', '✶', '✻', '✽'];
    // The first half of the breath goes up through the frames, the second comes down; a breath is 2000 ms.
    const up = Array.from({ length: 21 }, (_, step) => frames.indexOf(at(step * 50)));
    expect(up[0]).toBe(0);
    expect(up[20]).toBe(5);
    expect(new Set(up).size).toBe(6);
    expect(up).toEqual([...up].sort((a, b) => a - b));
    expect(at(1000)).toBe('✽');
    expect(at(2000)).toBe('·');
    expect(at(300)).toBe(at(1700));
    expect(at(300)).toBe(at(2300));
  });

  it('is the accent colour with a brighter window of three characters moving across, the suffix dim', () => {
    // One cell on every 100 ms; at this moment the window is over the fourth character.
    const at = 101_800;
    const thinking = { ...THINKING, since: at - 6000 };
    const row = statusRow(thinking, at, 100, COLOR);
    expect(row.startsWith(fgCode(ACCENT, true))).toBe(true);
    expect(row).toContain(`${fgCode(ACCENT_SHIMMER, true)}nki\x1b[39m`);
    expect(row.endsWith('\x1b[2m (6s · ↓ 48 tokens · esc to interrupt)\x1b[22m')).toBe(true);
    // The shimmer moves with the clock; the words do not.
    expect(statusRow(thinking, at + 100, 100, COLOR)).toContain(`${fgCode(ACCENT_SHIMMER, true)}kin\x1b[39m`);
    expect(stripAnsi(statusRow(thinking, at + 100, 100, COLOR)).slice(1)).toBe(stripAnsi(row).slice(1));
  });
});

describe('the plan', () => {
  it('is a checklist under a ⎿: ☐ waiting, ◼ in progress, ☒ done, ! blocked', () => {
    expect(planRows(STEPS, 6, 80, PLAIN)).toEqual(['  ⎿  ☒ Read cli.py', '     ◼ Add the flag', '     ☐ Run the tests', '     ! Fix the lint']);
  });

  it('paints the step in progress in the accent colour and bold, a done step dim and struck through', () => {
    const [done, active, waiting, blocked] = planRows(STEPS, 6, 80, COLOR);
    expect(done).toBe('\x1b[2m  ⎿  ☒ \x1b[9mRead cli.py\x1b[29m\x1b[22m');
    expect(active).toBe(`\x1b[2m     \x1b[22m${fgCode(ACCENT, true)}◼\x1b[39m \x1b[1mAdd the flag\x1b[22m`);
    expect(waiting).toBe('\x1b[2m     \x1b[22m☐ Run the tests');
    expect(blocked.startsWith('\x1b[2m     \x1b[22m\x1b[38;2;255;193;7m!\x1b[39m')).toBe(true);
  });

  it('shows a window of the steps and how many more, when there are more than the room', () => {
    const steps: PlanStep[] = Array.from({ length: 9 }, (_, index) => ({ id: `S${index}`, title: `step ${index}`, status: index < 4 ? 'completed' : index === 4 ? 'in_progress' : 'pending' }));
    // Six rows: five steps from the one before the first not done, and the rest counted.
    expect(planRows(steps, 6, 80, PLAIN)).toEqual(['     ☒ step 3', '     ◼ step 4', '     ☐ step 5', '     ☐ step 6', '     ☐ step 7', '     … +4 more']);
    expect(planRows(steps, 2, 80, PLAIN)).toEqual(['     ◼ step 4', '     … +8 more']);
    expect(planRows(steps, 1, 80, PLAIN)).toEqual(['     ◼ step 4']);
    expect(planRows(steps, 0, 80, PLAIN)).toEqual([]);
    expect(planRows([], 6, 80, PLAIN)).toEqual([]);
    // Everything done: the last steps.
    expect(planRows(steps.map((step) => ({ ...step, status: 'completed' })), 3, 80, PLAIN)).toEqual(['     ☒ step 7', '     ☒ step 8', '     … +7 more'].slice(0, 3));
  });

  it('cuts a long title to the row', () => {
    const rows = planRows([{ id: 'S1', title: 'x'.repeat(100), status: 'pending' }], 6, 30, PLAIN);
    expect(displayWidth(rows[0]!)).toBeLessThanOrEqual(30);
    expect(rows[0]!.endsWith('…')).toBe(true);
  });
});

describe('agents, notes and the reply', () => {
  it('shows an agent that works as ⎇ number kind status and what it is doing', () => {
    expect(agentRows(LIVE.agents, 3, 80, PLAIN)).toEqual(['  ⎇ 1 worker  running  Reading parser.py']);
    const many = Array.from({ length: 5 }, (_, index) => ({ id: String(index), number: index + 1, kind: 'worker', status: 'running', task: `task ${index}` }));
    expect(agentRows(many, 3, 80, PLAIN)).toEqual(['  ⎇ 1 worker  running  task 0', '  ⎇ 2 worker  running  task 1', '  … +3 more']);
  });

  it('shows a review or a verification that runs as a glyph and its headline', () => {
    expect(noteRows(LIVE.notes, 2, 80, PLAIN)).toEqual(['  · Reviewing the diff']);
    expect(noteRows([{ state: 'passed', text: 'ok' }, { state: 'warning', text: 'hm' }, { state: 'failed', text: 'no' }], 2, 80, PLAIN)).toEqual(['  ✓ ok', '  ! hm']);
  });

  it('is ● and the markdown, the last rows of it, indented two columns', () => {
    expect(replyRows('I wrote **notes.txt**.\n\n- one\n- two', 10, 80, PLAIN)).toEqual(['● I wrote notes.txt.', '', '  - one', '  - two']);
    // Scrolled: the bullet is gone with the first row.
    expect(replyRows('a\nb\nc\nd', 2, 80, PLAIN)).toEqual(['  c', '  d']);
    expect(replyRows('word '.repeat(40), 10, 30, PLAIN).every((row) => displayWidth(row) <= 30)).toBe(true);
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
    expect(at('● one')).toBe(0);
    expect(rows[6]).toBe('');
    expect(rows[7]).toMatch(/^[● ] Bash\(npm test\)$/);
    expect(rows[8]).toMatch(/^[·✢*✶✻✽] Thinking… \(6s · ↓ 48 tokens · esc to interrupt\)$/);
    expect(rows.slice(9, 13)).toEqual(['  ⎿  ☒ Read cli.py', '     ◼ Add the flag', '     ☐ Run the tests', '     ! Fix the lint']);
    expect(rows[13]).toBe('  · Reviewing the diff');
    expect(rows[14]).toBe('  ⎇ 1 worker  running  Reading parser.py');
    expect(rows[15]).toMatch(/^ ─+$/);
    expect(rows[16].trimEnd()).toBe(' >');
    expect(rows[17]).toMatch(/^ ─+$/);
    expect(rows[18]).toMatch(/^ esc\/ctrl-c to interrupt · enter queues the next task {2}\+3 -1/);
    expect(rows).toHaveLength(19);
  });

  it('has the rule above and below the field only, no sides, in the border grey, the marker grey, the placeholder dim', () => {
    const editor = Object.assign(new LineEditor('Describe your first task'), { placeholderVisible: true });
    const frame = composeFrame(state({ editor, color: true, caption: CAPTION }), 120, 36);
    expect(frame.plain.slice(0, 3)).toEqual([' ' + '─'.repeat(99), ' > Describe your first task', ' ' + '─'.repeat(99)]);
    expect(frame.rows[0]).toBe(fgCode(BORDER, true) + ' ' + '─'.repeat(99) + '\x1b[39m');
    expect(frame.rows[1]!.startsWith(' \x1b[38;2;153;153;153m>\x1b[39m ')).toBe(true);
    expect(frame.rows[1]).toContain('\x1b[2m');
    expect(frame.field).toMatchObject({ first: 1, count: 1, prefix: 3 });
  });

  it('draws a blank row above the status row only when there are rows to spare', () => {
    expect(plain({ progress: THINKING, busy: true }, 80, 24)[0]).toBe('');
    expect(plain({ progress: THINKING, busy: true }, 80, 24)[1]).toMatch(/^[·✢*✶✻✽] Thinking/);
    expect(plain({ progress: THINKING, busy: true }, 80, 12)[0]).toMatch(/^[·✢*✶✻✽] Thinking/);
  });

  it('keeps the field, both rules, the footer and the status row when the window is short, the reply three rows first', () => {
    for (const rows of [5, 6, 8, 12]) {
      const frame = plain({ ...BUSY, reply: 'a\nb\nc\nd\ne\nf' }, 80, rows);
      expect(frame.length, `${rows} rows`).toBeLessThanOrEqual(rows - 1);
      expect(frame.filter((row) => /^ ─+$/.test(row))).toHaveLength(rows >= 5 ? 2 : 1);
    }
    const at12 = plain({ ...BUSY, reply: 'a\nb\nc\nd\ne\nf', live: { ...LIVE, plan: STEPS } }, 80, 12);
    expect(at12.some((row) => /^[·✢*✶✻✽] Thinking/.test(row))).toBe(true);
    expect(at12.filter((row) => row.startsWith('  ') && /^ {2}[a-f]$/.test(row))).toHaveLength(3);
    // The footer is the last row of the frame.
    expect(at12.at(-1)).toMatch(/^ esc/);
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
    // 13 rows: the box and the footer 4, the status row and a blank above it 2, leaves 7: three for the reply, four for the plan.
    expect(frame.filter((row) => /^ {2}[a-e]$|^● a$/.test(row))).toEqual(['  c', '  d', '  e']);
    expect(frame.filter((row) => row.includes('☐'))).toHaveLength(3);
    expect(frame.filter((row) => row.includes('… +27 more'))).toHaveLength(1);
  });

  it('puts a question\'s hint in the empty box and its own footer under it, over the turn\'s', () => {
    const rows = plain({ busy: true, progress: THINKING, ask: 'Press y, a or n then Enter', askFooter: 'esc refuses · ctrl-c interrupts' }, 80, 24);
    expect(rows).toContain(' > Press y, a or n then Enter');
    expect(rows.at(-1)).toBe(' esc refuses · ctrl-c interrupts');
  });

  it('leaves the box empty while a turn runs, and counts the queue in the footer instead', () => {
    const rows = plain({ busy: true, pending: ['a', 'b'] }, 80, 24);
    expect(rows.find((row) => row.startsWith(' > '))!.trimEnd()).toBe(' >');
    expect(rows.at(-1)).toBe(' esc/ctrl-c to interrupt · enter queues the next task · 2 queued');
  });

  it('still sits the right-hand column beside the left, when a caller gives one', () => {
    const panel = { width: 20, rows: ['PLAN 0/2', '─'.repeat(20), 'S1 ● Read'] };
    const frame = composeFrame(state({ panel, busy: true, progress: THINKING }), 100, 24);
    expect(frame.plain[0]!.endsWith('  PLAN 0/2')).toBe(true);
    for (const row of frame.plain) expect(displayWidth(row)).toBeLessThanOrEqual(99);
  });
});
