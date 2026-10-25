// Click to place the caret (milestone 5): off unless asked for, and when on,
// a click on a field row puts the caret under it -- on wrapped rows, on a
// scrolled field, and on wide characters.

import { afterEach, describe, expect, it } from 'vitest';
import { layoutField, visibleField } from '../src/input/field.js';
import { MAX_CONTENT_WIDTH, PROMPT_PREFIX } from '../src/live/frame.js';
import { createSessionScreen, type SessionScreen } from '../src/live/session.js';
import { FakeTerminal, isField, isRule, rowsWhere } from './harness.js';

let screens: SessionScreen[] = [];
afterEach(() => {
  for (const screen of screens) screen.stop();
  screens = [];
});

async function setup(text: string, mouseCaret = true, columns = 80, rows = 24) {
  const term = new FakeTerminal(columns, rows);
  const screen = createSessionScreen({ stdin: term.stdin, stdout: term.stdout, color: false, mouseCaret });
  screens.push(screen);
  const sent: string[] = [];
  screen.onSubmit((line) => sent.push(line));
  term.type(text);
  await term.settle();
  const fieldTop = rowsWhere(term.viewport(), isRule)[0]! + 1;
  const inner = Math.min(MAX_CONTENT_WIDTH, columns - 1) - PROMPT_PREFIX;
  /** Click visible field row `row`, column `column` after the prefix, then type X and Enter. */
  const clickAndMark = async (row: number, column: number): Promise<string> => {
    term.type(`\x1b[<0;${PROMPT_PREFIX + column + 1};${fieldTop + row + 1}M\x1b[<0;${PROMPT_PREFIX + column + 1};${fieldTop + row + 1}m`);
    term.type('X');
    term.type('\r');
    await term.settle();
    return sent.at(-1)!;
  };
  return { term, inner, clickAndMark };
}

describe('click to place the caret', () => {
  it('is off by default: no mouse reporting, and clicks are ignored', async () => {
    const { term, clickAndMark } = await setup('hello', false);
    expect(term.raw).not.toContain('\x1b[?1000h');
    expect(await clickAndMark(0, 1)).toBe('helloX');
  });

  it('turns SGR mouse reporting on and off with the screen', async () => {
    const { term } = await setup('', true);
    expect(term.raw).toContain('\x1b[?1000h\x1b[?1006h');
    screens.pop()!.stop();
    expect(term.raw).toContain('\x1b[?1006l\x1b[?1000l');
  });

  it('puts the caret before the character clicked', async () => {
    const { clickAndMark } = await setup('hello world');
    expect(await clickAndMark(0, 6)).toBe('hello Xworld');
  });

  it('maps a click on a wrapped row', async () => {
    const text = Array.from({ length: 30 }, (_, index) => `word${index}`).join(' ');
    const { inner, clickAndMark } = await setup(text);
    const rows = layoutField(text, inner);
    expect(rows.length).toBeGreaterThan(1);
    const at = rows[1]!.start + 3;
    expect(await clickAndMark(1, 3)).toBe(text.slice(0, at) + 'X' + text.slice(at));
  });

  it('maps a click through a scrolled field', async () => {
    const text = Array.from({ length: 120 }, (_, index) => `w${index}`).join(' ');
    const { inner, clickAndMark } = await setup(text);
    const view = visibleField(text, text.length, inner);
    expect(view.above).toBeGreaterThan(0);
    const at = view.rows[0]!.start + 2;
    expect(await clickAndMark(0, 2)).toBe(text.slice(0, at) + 'X' + text.slice(at));
  });

  it('maps a click on a wide character to the nearer side of it', async () => {
    const { clickAndMark } = await setup('ab漢字cd');
    // 漢 covers columns 2 and 3: the left cell puts the caret before it, the right one after.
    expect(await clickAndMark(0, 3)).toBe('ab漢X字cd');
  });

  it('ignores a click outside the field', async () => {
    const { term, clickAndMark } = await setup('hello');
    expect(rowsWhere(term.viewport(), isField)).toHaveLength(1);
    expect(await clickAndMark(-2, 1)).toBe('helloX');
  });
});
