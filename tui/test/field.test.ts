import { describe, expect, it } from 'vitest';
import { caretAt, caretIn, INPUT_MAX_ROWS, layoutField, visibleField } from '../src/input/field.js';
import { displayWidth, fieldWidth } from '../src/input/width.js';

const texts = (text: string, inner: number) => layoutField(text, inner).map((row) => row.text);

describe('layoutField', () => {
  it('gives an empty field one empty row', () => {
    expect(layoutField('', 10)).toEqual([{ start: 0, text: '' }]);
  });

  it('keeps one column for the caret: inner - 1 columns of text per row', () => {
    // inner 5 -> 4 usable columns. Exactly 4 is one row; exactly 5 wraps.
    expect(texts('abcd', 5)).toEqual(['abcd']);
    expect(texts('abcde', 5)).toEqual(['abcd', 'e']);
    expect(texts('abcdefgh', 5)).toEqual(['abcd', 'efgh']);
  });

  it('wraps at a word boundary and leaves the break space on the row it ends (F12)', () => {
    expect(texts('aa bbbb', 5)).toEqual(['aa ', 'bbbb']);
    expect(texts('one two three four', 10)).toEqual(['one two ', 'three ', 'four']);
    for (const row of layoutField('please review the changed files now', 12).slice(1)) {
      expect(row.text.startsWith(' ')).toBe(false);
    }
  });

  it('hangs a space that falls exactly on the caret column instead of starting a row with it', () => {
    expect(texts('aaaa bbbb', 5)).toEqual(['aaaa ', 'bbbb']);
  });

  it('breaks a word longer than the row at the column', () => {
    expect(texts('a bbbbbb', 5)).toEqual(['a ', 'bbbb', 'bb']);
  });

  it('measures wide characters as two columns and never splits one', () => {
    expect(texts('ab漢字', 5)).toEqual(['ab漢', '字']);
    expect(texts('漢字漢字', 5)).toEqual(['漢字', '漢字']);
    for (const row of layoutField('漢字かなカナ漢字かなカナ', 7)) expect(fieldWidth(row.text)).toBeLessThanOrEqual(6);
  });

  it('measures emoji and ZWJ sequences as one wide character', () => {
    const family = '👨‍👩‍👧';
    expect(displayWidth(family)).toBe(2);
    const rows = layoutField(`${family}${family}${family}`, 5);
    expect(rows.map((row) => row.text)).toEqual([family + family, family]);
  });

  it('gives zero-width characters no columns and keeps combining marks with their base', () => {
    const accented = 'é';
    expect(texts(`${accented}${accented}${accented}${accented}x`, 5)).toEqual([accented.repeat(4), 'x']);
    expect(texts('ab​cd', 5)).toEqual(['ab​cd']);
  });

  it('ends a row at an explicit line break', () => {
    expect(layoutField('ab\ncd', 10)).toEqual([{ start: 0, text: 'ab' }, { start: 3, text: 'cd' }]);
  });
});

describe('caretIn', () => {
  it('puts the caret of an empty field at row 0, column 0', () => {
    expect(caretIn(layoutField('', 10), '', 0)).toEqual([0, 0]);
  });

  it('puts a caret at the end after the last character, in the reserved column', () => {
    const text = 'abcd';
    expect(caretIn(layoutField(text, 5), text, 4)).toEqual([0, 4]);
  });

  it('gives a caret exactly on a wrap boundary to the row it is about to type into', () => {
    const text = 'abcdef';
    const rows = layoutField(text, 5);
    expect(caretIn(rows, text, 4)).toEqual([1, 0]);
    expect(caretIn(rows, text, 3)).toEqual([0, 3]);
  });

  it('finds a caret in the middle of a wrapped row', () => {
    const text = 'one two three four';
    const rows = layoutField(text, 10);
    expect(caretIn(rows, text, text.indexOf('hree'))).toEqual([1, 1]);
  });

  it('counts columns, not characters, before the caret', () => {
    const text = '漢字ab';
    expect(caretIn(layoutField(text, 20), text, 2)).toEqual([0, 4]);
  });
});

describe('visibleField', () => {
  const long = Array.from({ length: 12 }, (_, index) => `w${index}`).join(' ');

  it('shows everything when it fits', () => {
    const view = visibleField('hello', 5, 20);
    expect(view.rows.map((row) => row.text)).toEqual(['hello']);
    expect([view.caretRow, view.caretColumn, view.above, view.below]).toEqual([0, 5, 0, 0]);
  });

  it('stops growing at INPUT_MAX_ROWS and follows the caret down', () => {
    const view = visibleField(long, long.length, 5);
    expect(view.rows).toHaveLength(INPUT_MAX_ROWS);
    expect(view.caretRow).toBe(INPUT_MAX_ROWS - 1);
    expect(view.below).toBe(0);
    expect(view.above).toBe(layoutField(long, 5).length - INPUT_MAX_ROWS);
  });

  it('follows the caret back up when it moves to the start', () => {
    const view = visibleField(long, 0, 5);
    expect(view.above).toBe(0);
    expect(view.caretRow).toBe(0);
    expect(view.below).toBeGreaterThan(0);
  });

  it('keeps the caret row on screen wherever it is', () => {
    const all = layoutField(long, 5);
    for (let cursor = 0; cursor <= long.length; cursor += 1) {
      const view = visibleField(long, cursor, 5, 3);
      const [row] = caretIn(all, long, cursor);
      expect(view.above + view.caretRow).toBe(row);
      expect(view.caretRow).toBeGreaterThanOrEqual(0);
      expect(view.caretRow).toBeLessThan(3);
    }
  });
});

describe('caretAt (click to place the caret)', () => {
  it('puts the caret before the character clicked', () => {
    expect(caretAt('hello world', 0, 20, 5, 0, 4)).toBe(4);
    expect(caretAt('hello world', 0, 20, 5, 0, 0)).toBe(0);
  });

  it('puts the caret at the end of the last row when clicked past it', () => {
    expect(caretAt('hello', 0, 20, 5, 0, 15)).toBe(5);
  });

  it('maps a click on a wrapped row through the row start', () => {
    const text = 'one two three four';
    // rows: 'one two ', 'three ', 'four'
    expect(caretAt(text, 0, 10, 5, 1, 2)).toBe(text.indexOf('ree'));
    expect(caretAt(text, 0, 10, 5, 2, 1)).toBe(text.indexOf('our'));
  });

  it('keeps the caret on a wrapped row clicked past its end', () => {
    const text = 'aaaa bbbb';
    // 'aaaa ' then 'bbbb': past the end of row 0 is before its hanging space.
    expect(caretAt(text, 0, 5, 5, 0, 9)).toBe(4);
  });

  it('maps rows through the scroll window', () => {
    const text = Array.from({ length: 12 }, (_, index) => `w${index}`).join(' ');
    const cursor = text.length;
    const view = visibleField(text, cursor, 5, 3);
    const target = view.rows[0]!;
    expect(caretAt(text, cursor, 5, 3, 0, 1)).toBe(target.start + 1);
  });

  it('splits a wide character between its two cells', () => {
    const text = 'a漢b';
    expect(caretAt(text, 0, 20, 5, 0, 1)).toBe(1);
    expect(caretAt(text, 0, 20, 5, 0, 2)).toBe(2);
    expect(caretAt(text, 0, 20, 5, 0, 3)).toBe(2);
  });

  it('ignores a click outside the field rows', () => {
    expect(caretAt('hello', 3, 20, 5, 4, 0)).toBe(3);
    expect(caretAt('hello', 3, 20, 5, 0, -1)).toBe(3);
  });
});
