// The Ink screen against the hand-written renderer it replaced: the same
// calls and keys, step by step, must leave the two terminals holding the same
// thing -- every row of the scrollback and the window, every cell's colour
// and attributes, and the cursor. That holds for everything that is not the
// look: the scrollback, the resizes, the keys, the field and the questions.
// The Ink screen has since taken the signal rail look (the reply, the status
// row, the footer, the plan), so the calls that draw those are not made here
// -- a footer is only a `setStatus` row on both. Its composer is the field
// behind a bar, where the Python UI's box is a rule, the field and a rule. So
// what the box drew is read in the Ink look before the two are compared (the
// rules are not rows, the `>` is the bar), and blank rows are not compared:
// the two rules of a window made narrower are re-wrapped by the terminal into
// rows of their own, which scroll the record up by as many blank ones. Where
// the box sits on the foot is the shared scenarios' (test/live.test.ts runs on
// both screens). (Goes with src/live when that is removed.)

import { afterEach, describe, expect, it } from 'vitest';
import { createInkSessionScreen } from '../src/ink/screen.js';
import { createSessionScreen, type SessionScreen } from '../src/live/session.js';
import { FakeTerminal } from './harness.js';

type Step = ((screen: SessionScreen, term: FakeTerminal) => void | Promise<unknown>) & { cursorMayDiffer?: boolean };

/**
 * A step after which the hidden cursor may be on another row on the Ink screen:
 * while a bare one-key question is open the frame has no rows, and Ink ends
 * even an empty frame with a line break, so the cursor waits on a blank row
 * under the output rather than on the row right under it; and once the frame
 * is taken down (`suspend`) the cursor is left on the row it started on, which
 * is two rows lower for a composer that has no rules. Nothing else differs.
 */
const cursorMayDiffer = (step: Step): Step => Object.assign(step, { cursorMayDiffer: true });

let screens: SessionScreen[] = [];
afterEach(() => {
  for (const screen of screens) screen.stop();
  screens = [];
});

/**
 * Everything the terminal holds but its blank rows: each row's text, whether
 * it is wrapped, the runs of cells drawn in anything but the default look, and
 * the cursor (its row counted from the foot of the window). A cell never
 * written and a space written in no colour look the same, and are the same
 * here: Ink leaves off a row's trailing spaces. With `python`, the Python UI's
 * box is read as the Ink composer: its rules are not rows, and the `>` before
 * the field is the bar.
 */
function dump(term: FakeTerminal, python: boolean): string[] {
  const buffer = term.term.buffer.active;
  const rows: string[] = [];
  for (let y = 0; y < buffer.length; y += 1) {
    const line = buffer.getLine(y)!;
    let text = line.translateToString(true).trimEnd();
    if (python && /^ ─+$/.test(text)) continue;
    const blank = text === '' && !line.isWrapped;
    const field = python && /^ >( |$)/.test(text);
    const runs: string[] = [];
    let run = '';
    for (let x = 0; x < term.term.cols; x += 1) {
      const cell = line.getCell(x)!;
      const look = [cell.getFgColorMode(), cell.getFgColor(), cell.getBgColorMode(), cell.getBgColor(),
        cell.isBold(), cell.isDim(), cell.isItalic(), cell.isUnderline(), cell.isInverse()].join(',');
      if (look === '0,-1,0,-1,0,0,0,0,0') continue;
      if (look !== run) runs.push(`${field ? x - 1 : x}:${look}`);
      run = look;
    }
    if (field) text = '▌' + text.slice(2);
    if (blank && runs.length === 0) continue;
    rows.push(`${line.isWrapped ? 'w' : ' '}${text}${runs.length ? `  [${runs.join(' ')}]` : ''}`);
  }
  rows.push(`cursor ${buffer.cursorX},${term.term.rows - 1 - buffer.cursorY}`);
  return rows;
}

/** Run `steps` on both screens side by side; after each, both terminals hold the same. */
async function same(columns: number, rows: number, steps: Step[], options: { color?: boolean; conpty?: boolean } = {}): Promise<void> {
  const sides = [createSessionScreen, createInkSessionScreen].map((create) => {
    const term = new FakeTerminal(columns, rows, { conpty: options.conpty ?? false });
    const screen = create({ stdin: term.stdin, stdout: term.stdout, color: options.color ?? false, truecolor: true, clock: () => 10_000 });
    screens.push(screen);
    return { term, screen };
  });
  for (const [index, step] of steps.entries()) {
    for (const { term, screen } of sides) {
      await step(screen, term);
      await term.settle();
    }
    const [live, ink] = sides.map(({ term }, side) => {
      const all = dump(term, side === 0);
      return step.cursorMayDiffer ? all.slice(0, -1) : all;
    });
    expect(ink, `after step ${index}`).toEqual(live);
  }
}

const lines = (from: number, count: number): Step => (screen) => {
  for (let index = from; index < from + count; index += 1) screen.commit(`line ${index} ${'x'.repeat(index % 50)}`);
};
const status: Step = (screen) => screen.setStatus('status: thinking');

describe('the Ink screen holds what the hand-written one holds', () => {
  it.each([['xterm.js', false], ['conpty', true]] as const)('through a long record and resizes, a shrink below the height of the frame included (%s)', async (_name, conpty) => {
    await same(80, 24, [
      status,
      lines(0, 30),
      (_screen, term) => term.resize(40, 12),
      lines(30, 5),
      (_screen, term) => term.resize(200, 50),
      lines(35, 5),
      (_screen, term) => term.resize(80, 10),
      lines(40, 3),
      (_screen, term) => term.resize(80, 24),
      (screen) => screen.commit('the answer'),
      (screen) => screen.stop(),
    ], { conpty });
  }, 60_000);

  it('through typing, a paste, both kinds of question and a hand-over', async () => {
    await same(120, 36, [
      (screen) => { screen.setStatus('status: thinking'); screen.setPlaceholder('Describe your first task'); },
      lines(0, 8),
      (_screen, term) => term.type('look at '),
      (_screen, term) => term.type('\x1b[200~Traceback:\r  File "x.py"\r\x1b[201~'),
      (_screen, term) => term.type('\x1b[D\x1b[D'),
      (screen) => { void screen.ask({ hint: '1-2, Esc to skip', keys: ['1', '2'] }); },
      (_screen, term) => term.type('2'),
      (screen) => { void screen.ask({ hint: 'y / a / n  then Enter' }); },
      (_screen, term) => term.type('y'),
      (_screen, term) => term.type('\r'),
      (_screen, term) => term.resize(90, 30),
      cursorMayDiffer((screen) => screen.suspend()),
      (screen) => screen.resume(),
      lines(8, 3),
      (screen) => screen.stop(),
    ]);
  }, 60_000);

  it('through a young session resized, and the bare questions the Python UI asks', async () => {
    await same(100, 30, [
      (screen) => { screen.setStatus('status: thinking'); screen.commit('header\n\n> a task'); },
      (_screen, term) => term.resize(60, 16),
      (_screen, term) => term.resize(120, 36),
      (screen) => { void screen.ask({ bare: true }); },
      (_screen, term) => term.type('typed at column 0'),
      (_screen, term) => term.type('\r'),
      cursorMayDiffer((screen) => { void screen.ask({ bare: true, keys: ['y', 'n'] }); }),
      cursorMayDiffer((screen) => screen.commit('a question above the cursor')),
      (_screen, term) => term.resize(80, 24),
      (_screen, term) => term.type('n'),
      lines(0, 3),
      (screen) => screen.stop(),
    ]);
  }, 60_000);
});
