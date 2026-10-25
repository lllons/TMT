// Committed text wrapped at a word boundary, in columns, with colour left out
// of the arithmetic and carried across the break.

import { describe, expect, it } from 'vitest';
import { displayWidth, stripAnsi } from '../src/input/width.js';
import { wrapLine, wrapRows } from '../src/wrap.js';

const RESET = '\x1b[0m';
const words = (rows: string[]): string[] => rows.flatMap((row) => stripAnsi(row).split(/\s+/).filter(Boolean));
const widest = (rows: string[]): number => Math.max(...rows.map((row) => displayWidth(stripAnsi(row))));
const sentence = (count: number): string => Array.from({ length: count }, (_, index) => `word${index}`).join(' ');

describe('wrapLine', () => {
  it('returns a row that fits as it is, a row of exactly the width included', () => {
    expect(wrapLine('hello there', 20)).toEqual(['hello there']);
    expect(wrapLine('x'.repeat(20), 20)).toEqual(['x'.repeat(20)]);
    expect(wrapLine('', 20)).toEqual(['']);
    expect(wrapLine('\x1b[1mbold\x1b[0m', 4)).toEqual(['\x1b[1mbold\x1b[0m']);
  });

  it('breaks at a space, never inside a word, and drops the space it broke at', () => {
    const rows = wrapLine('the quick brown fox jumps over the lazy dog', 16);
    expect(rows).toEqual(['the quick brown', 'fox jumps over', 'the lazy dog']);
    // A word ending exactly at the edge stays; the space after it is not carried over.
    expect(wrapLine('aaaa bbbb cccc', 9)).toEqual(['aaaa bbbb', 'cccc']);
    expect(wrapLine('aaaa bbbb  cccc', 9)).toEqual(['aaaa bbbb', 'cccc']);
  });

  it('wraps a long row to the width and loses no word', () => {
    const text = sentence(60);
    for (const width of [20, 33, 79, 100]) {
      const rows = wrapLine(text, width);
      expect(widest(rows)).toBeLessThanOrEqual(width);
      expect(rows.length).toBeGreaterThan(1);
      expect(words(rows)).toEqual(text.split(' '));
      // No row starts or ends with a space.
      for (const row of rows) expect(row).toBe(row.trim());
    }
  });

  it('lines a continuation up under the text of a task, event, list or plan row', () => {
    const body = sentence(30);
    const cases: [string, string][] = [
      ['> ' + body, '  '],
      ['  • ' + body, '    '],
      ['  ! ' + body, '    '],
      ['- ' + body, '  '],
      ['* ' + body, '  '],
      ['12. ' + body, '    '],
      ['  [>] ' + body, '      '],
      ['    ' + body, '    '],
      [body, ''],
    ];
    for (const [row, indent] of cases) {
      const rows = wrapLine(row, 40);
      expect(rows.length).toBeGreaterThan(1);
      expect(widest(rows)).toBeLessThanOrEqual(40);
      for (const next of rows.slice(1)) {
        expect(next.startsWith(indent), JSON.stringify([row.slice(0, 8), next])).toBe(true);
        expect(next[indent.length]).not.toBe(' ');
      }
      // The first row is never broken right after its marker.
      expect(stripAnsi(rows[0]!).trim().length).toBeGreaterThan(3);
    }
  });

  it('indents a continuation by at most half the row', () => {
    const rows = wrapLine(' '.repeat(30) + sentence(12), 40);
    expect(widest(rows)).toBeLessThanOrEqual(40);
    for (const next of rows.slice(1)) expect(next.length - next.trimStart().length).toBeLessThanOrEqual(20);
    expect(words(rows)).toEqual(sentence(12).split(' '));
  });

  it('cuts a word longer than the row where the row ends', () => {
    const rows = wrapLine('see ' + 'a'.repeat(50) + ' end', 20);
    expect(widest(rows)).toBeLessThanOrEqual(20);
    expect(rows.join('').replace(/ /g, '')).toBe('see' + 'a'.repeat(50) + 'end');
    expect(wrapLine('b'.repeat(45), 20)).toEqual(['b'.repeat(20), 'b'.repeat(20), 'b'.repeat(5)]);
    // A long word starts a row of its own when it will not fit after the words before it.
    expect(wrapLine('go ' + 'c'.repeat(18) + ' now', 20).slice(0, 2)).toEqual(['go', 'c'.repeat(18)]);
  });

  it('measures in columns: a wide character is never split or left past the edge', () => {
    const text = '漢字'.repeat(30) + ' ' + '日本語 '.repeat(10);
    const rows = wrapLine(text, 21);
    expect(widest(rows)).toBeLessThanOrEqual(21);
    expect(rows.join('').replace(/ /g, '')).toBe(text.replace(/ /g, ''));
    // Eleven wide characters do not fit in 21 columns, ten do.
    expect(wrapLine('漢'.repeat(11), 21)).toEqual(['漢'.repeat(10), '漢']);
    // And an emoji sequence stays whole.
    const family = '👨‍👩‍👧';
    expect(wrapLine(family.repeat(8), 10).join('')).toBe(family.repeat(8));
  });

  it('does not count colour as width, and carries it across the break', () => {
    const text = '\x1b[1m> ' + sentence(30) + RESET;
    const rows = wrapLine(text, 40);
    expect(widest(rows)).toBeLessThanOrEqual(40);
    expect(rows.length).toBeGreaterThan(2);
    rows.forEach((row, index) => {
      // Every row is closed, and every continuation opens the colour again after its indent.
      expect(row.endsWith(RESET)).toBe(true);
      if (index > 0) expect(row.startsWith('  \x1b[1m')).toBe(true);
    });
    expect(words(rows)).toEqual(['>', ...sentence(30).split(' ')]);
  });

  it('keeps a colour that ends mid-row from leaking into the next row, and one that opens there', () => {
    const rows = wrapLine('\x1b[31mred words that go on and on\x1b[0m then plain words here and \x1b[32mgreen to the end\x1b[0m', 24);
    expect(widest(rows)).toBeLessThanOrEqual(24);
    for (const row of rows) {
      // Colour opened in a row is closed in it.
      const opens = (row.match(/\x1b\[[1-9][0-9;]*m/g) ?? []).length;
      const resets = (row.match(/\x1b\[0m/g) ?? []).length;
      expect(resets).toBeGreaterThanOrEqual(opens > 0 ? 1 : 0);
    }
    // The red carried onto the second row ends on it, before the plain words; the green opens on the third.
    expect(rows[1]).toBe('\x1b[31mon\x1b[0m then plain words here');
    expect(rows[2]).toBe('and \x1b[32mgreen to the end\x1b[0m');
    expect(words(rows)).toEqual('red words that go on and on then plain words here and green to the end'.split(' '));
  });

  it('takes the colour codes at a break along with the row they belong to', () => {
    // The reset sits in the gap that is broken at: the next row must not start red.
    const rows = wrapLine('\x1b[31maaaa bbbb\x1b[0m cccc dddd', 9);
    expect(rows).toEqual(['\x1b[31maaaa bbbb\x1b[0m', 'cccc dddd']);
    // A code in the gap that opens colour belongs to the next row.
    const next = wrapLine('aaaa bbbb \x1b[32mcccc dddd\x1b[0m', 9);
    expect(next[0]).toBe('aaaa bbbb');
    expect(next[1]).toBe('\x1b[32mcccc dddd\x1b[0m');
  });

  it('expands tabs and drops escapes other than colour, as the screen would, before it measures', () => {
    expect(wrapLine('a\tb', 20)).toEqual(['a       b']);
    expect(wrapLine('x\x1b[2Jy\x1b[3Jz', 20)).toEqual(['xyz']);
  });

  it('is safe at a width of one or none', () => {
    expect(wrapLine('ab cd', 1)).toEqual(['a', 'b', 'c', 'd']);
    expect(wrapLine('ab', 0)).toEqual(['a', 'b']);
  });

  it('loses no character, whatever the text and the width', () => {
    let seed = 12345;
    const next = (limit: number): number => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed % limit;
    };
    const alphabet = ['a', 'b', 'cc', 'ddd', ' ', ' ', '  ', '漢', '-', '\x1b[1m', '\x1b[0m', '\x1b[31m', '😀', '*', '1.', '> '];
    for (let trial = 0; trial < 300; trial += 1) {
      const text = Array.from({ length: 1 + next(60) }, () => alphabet[next(alphabet.length)]!).join('');
      const width = 1 + next(30);
      const rows = wrapLine(text, width);
      expect(widest(rows), JSON.stringify([text, width, rows])).toBeLessThanOrEqual(Math.max(width, 2));
      expect(stripAnsi(rows.join('')).replace(/ /g, ''), JSON.stringify([text, width])).toBe(stripAnsi(text).replace(/ /g, ''));
    }
  });
});

describe('wrapRows', () => {
  it('wraps each line of the text, and a final line break adds no row', () => {
    expect(wrapRows('one\ntwo', 20)).toEqual(['one', 'two']);
    expect(wrapRows('one\n', 20)).toEqual(['one']);
    expect(wrapRows('', 20)).toEqual(['']);
    expect(wrapRows('\n', 20)).toEqual(['']);
    expect(wrapRows('a\n\nb\r\nc', 20)).toEqual(['a', '', 'b', 'c']);
    const rows = wrapRows('  • ' + sentence(10) + '\n    indented ' + sentence(10), 30);
    expect(widest(rows)).toBeLessThanOrEqual(30);
    expect(rows.filter((row) => row.startsWith('  • '))).toHaveLength(1);
  });
});
