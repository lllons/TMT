// A whole turn of the scripted session (test/turn.ts) through the front end and
// a headless terminal, at 80x24, 40x12 and 200x50: a task, tool calls, a plan,
// an approval, a failing command and a streamed markdown answer. What is
// asserted is what the terminal holds at each step: the frame never taller
// than the window less one, the box on the last rows, no sequence that costs
// the scrollback, and every row of the record there exactly once.
//
// With TMT_CAPTURE_DIR set it also writes the screen at the approval, in the
// middle of the turn and at the end, as plain text, for looking at.

import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { displayWidth } from '../src/input/width.js';
import { forbiddenIn, isField, isRule } from './harness.js';
import { start, type Run } from './run.js';
import { ASK, turn, TURN } from './turn.js';

vi.setConfig({ testTimeout: 60_000 });

const SIZES = [[80, 24], [40, 12], [200, 50], [120, 40]] as const;
const CAPTURES = process.env['TMT_CAPTURE_DIR'];
/** The status row: a rail cell and the words, which end in an ellipsis, then the readout when it fits. */
const STATUS = /^┃ .*…( \(.*\))?$/;

/** The whole buffer, trailing blank rows dropped, as the plain text a person would see by scrolling. */
const capture = (run: Run): string => {
  const rows = [...run.rows()];
  while (rows.length > 0 && rows[rows.length - 1] === '') rows.pop();
  return rows.join('\n') + '\n';
};

function save(run: Run, columns: number, rows: number, name: string): void {
  if (!CAPTURES) return;
  mkdirSync(CAPTURES, { recursive: true });
  writeFileSync(join(CAPTURES, `turn-${columns}x${rows}-${name}.txt`), capture(run));
}

/** The box is on the last rows: the field, the footer, then the cursor's own row; no rule above or below it. */
function expectBoxOnFoot(run: Run, rows: number): void {
  const view = run.term.viewport();
  expect(view[rows - 1], 'the cursor row').toBe('');
  expect(view[rows - 2]!.startsWith(' '), `the footer: ${view[rows - 2]}`).toBe(true);
  expect(isField(view[rows - 3]!), `the field: ${view[rows - 3]}`).toBe(true);
  expect(view.some(isRule), 'no rule in the window').toBe(false);
}

/** Where the live frame starts: the first row of the window that belongs to it (the last status or blank row above the box). */
function frameRows(run: Run, rows: number): string[] {
  const view = run.term.viewport();
  return view.slice(0, rows - 1);
}

describe.each(SIZES)('a turn at %ix%i', (columns, rows) => {
  it('keeps the box on the last rows and the record whole from the task to the answer', async () => {
    const run = start(TURN, { columns, rows });
    await run.showing('Describe your first task');
    expectBoxOnFoot(run, rows);
    await run.enter(ASK);

    // The approval: a dialog in the record, the box asking, the status row saying whose turn it is.
    await run.showing('Run this command?');
    await run.term.settle();
    expectBoxOnFoot(run, rows);
    expect(run.term.viewport().find(isField)!.trim()).toBe('▌ Press y, a or n then Enter');
    expect(run.term.viewport().some((row) => /^┃ Waiting for your approval…/.test(row))).toBe(rows > 8);
    save(run, columns, rows, 'approval');
    run.term.type('y');
    run.term.type('\r');

    // In the middle: a tool call is running, the plan is up, the reply is streaming.
    await run.showing('Running npm test -- --coverage');
    await new Promise((resolve) => setTimeout(resolve, 400));
    await run.term.settle();
    expectBoxOnFoot(run, rows);
    const mid = frameRows(run, rows);
    expect(mid.filter((row) => STATUS.test(row))).toHaveLength(1);
    expect(mid.some((row) => row.includes('│ Bash npm test -- --coverage'))).toBe(true);
    expect(mid.some((row) => /[◉◐○]/.test(row))).toBe(true);
    // The reply is live only: nothing of it is in the record yet.
    expect(run.term.scrollback().some((row) => row.includes('parser.add_argument'))).toBe(false);
    expect(run.term.viewport().some((row) => row.includes('parser.add_argument'))).toBe(true);
    for (const row of run.term.viewport()) expect(displayWidth(row)).toBeLessThanOrEqual(columns - 1);
    save(run, columns, rows, 'mid');

    // The end: the answer is committed once, the frame is the idle box.
    run.term.resize(columns, rows);
    await run.showing('Fix the long line');
    await run.term.settle();
    expectBoxOnFoot(run, rows);
    save(run, columns, rows, 'end');

    const all = run.rows();
    const once = (text: string): void => expect(all.filter((row) => row === text), text).toHaveLength(1);
    once('│ TMT 0.1.1');
    once('· Looking at the project first.');
    once('│ Read File cli.py');
    once('│ Patch File cli.py');
    once('│ Bash npm test');
    once('│ Bash npm run lint');
    once('│ Bash npm test -- --coverage');
    once('✓ The tests pass.');
    once('┃ Allowed once');
    once('┃ Run this command?');
    expect(all.filter((row) => row.startsWith('┃ I added --verbose'))).toHaveLength(1);
    // The task is on the double rail, every row of it, once.
    expect(all.filter((row) => row.startsWith('║ ')).map((row) => row.slice(2)).join(' ')).toBe(ASK);
    // The facts of each call are under it.
    const at = all.indexOf('│ Patch File cli.py');
    expect(all.slice(at + 1, at + 3)).toEqual(['│ Edited cli.py', '│ Added 4 lines, removed 1 line']);
    const lint = all.indexOf('│ Bash npm run lint');
    expect(all.slice(lint + 1, lint + 4).join('\n')).toContain('exit 1');
    // Nothing of the live frame was left in the record: no status row, no plan, no live reply, no second box.
    expect(all.filter((row) => STATUS.test(row))).toEqual([]);
    expect(all.filter((row) => /^│ [◉◐○] /.test(row))).toEqual([]);
    expect(all.filter(isField)).toHaveLength(1);
    expect(all.filter(isRule)).toHaveLength(0);
    // The idle frame: the hint and the counts, and the placeholder the turn left.
    expect(run.term.viewport().find(isField)!.trim()).toBe('▌ Fix the long line');
    expect(run.term.viewport()[rows - 2]!.startsWith(' Enter to send')).toBe(true);

    expect(forbiddenIn(run.term.raw)).toEqual([]);
    expect(run.term.raw).not.toMatch(/\x1b\[(?:1;1)?H|\x1b\[2J/);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
    expect(run.stderr).toEqual([]);
  });
});

describe('a turn resized while it runs', () => {
  it('keeps the box on the foot and every row of the record once, through a shrink to 40x12 and a grow to 200x50 and back', async () => {
    const run = start(turn({ type: 'interrupt' }), { columns: 80, rows: 24 });
    await run.showing('Describe your first task');
    await run.enter(ASK);
    await run.showing('Run this command?');
    run.term.type('y');
    run.term.type('\r');
    await run.showing('Running npm test -- --coverage');
    await new Promise((resolve) => setTimeout(resolve, 300));
    for (const [columns, rows] of [[40, 12], [200, 50], [60, 16], [80, 24]] as const) {
      run.term.resize(columns, rows);
      await run.term.settle();
      await new Promise((resolve) => setTimeout(resolve, 300));
      await run.term.settle();
      expectBoxOnFoot(run, rows);
      expect(run.term.viewport().filter((row) => STATUS.test(row))).toHaveLength(1);
      for (const row of run.term.viewport()) expect(displayWidth(row)).toBeLessThanOrEqual(columns - 1);
    }
    // Ctrl-C stops the turn, which the backend answers by finishing it.
    run.term.type('\x03');
    await run.showing('Fix the long line');
    await run.term.settle();
    expectBoxOnFoot(run, 24);
    const all = run.rows();
    for (const text of ['│ Read File cli.py', '│ Patch File cli.py', '│ Bash npm test', '│ Bash npm run lint']) {
      expect(all.filter((row) => row.trimEnd() === text), text).toHaveLength(1);
    }
    expect(forbiddenIn(run.term.raw)).toEqual([]);
    expect(run.term.raw).not.toMatch(/\x1b\[(?:1;1)?H|\x1b\[2J/);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});
