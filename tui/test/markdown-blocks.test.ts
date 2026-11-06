// The answer's markdown laid out for a width, in the Claude Code look: hanging
// indents, the quote bar, the code block, the table, and the same rows with no
// escape in them when colour is off.

import { describe, expect, it } from 'vitest';
import { displayWidth, stripAnsi } from '../src/input/width.js';
import { renderMarkdown } from '../src/markdown.js';
import { makeTheme } from '../src/theme.js';

const COLOR = makeTheme({ color: true, truecolor: true });
const PLAIN = makeTheme({ color: false, truecolor: true });
const off = (text: string, width = 40): string[] => renderMarkdown(text, PLAIN, width);
const on = (text: string, width = 40): string[] => renderMarkdown(text, COLOR, width);

describe('text with no markdown', () => {
  const SAMPLES = [
    'Done — wrote notes.txt ✓',
    'a_b and snake_case_name and __init__.py and _private',
    'src/**/*.ts and lib/**/*.js, plus foo/*/bar/*/baz',
    'rm *.txt and *.log; the glob *.{js,ts} too',
    '#hashtag and #include <stdio.h> and C# and a # b',
    'unclosed **bold and *emphasis and `code and [link](http://x',
    'two\nlines\n\nand a blank one',
  ];

  it.each(SAMPLES)('comes out as it went in, in colour or not: %j', (text) => {
    expect(off(text, 100).join('\n')).toBe(text);
    expect(on(text, 100).join('\n')).toBe(text);
  });

  it('wraps a long line at a word and keeps every word', () => {
    const text = 'the quick brown fox jumps over the lazy dog and keeps running across the field until night';
    const rows = off(text, 30);
    expect(rows.length).toBeGreaterThan(2);
    for (const row of rows) expect(displayWidth(row)).toBeLessThanOrEqual(30);
    expect(rows.join(' ').split(/\s+/)).toEqual(text.split(' '));
  });
});

describe('inline markup', () => {
  it('paints bold, italic, strike and code in the theme, and nothing else', () => {
    expect(on('wrote **notes.txt** and *briefly* ~~twice~~ with `code`', 100)).toEqual([
      `wrote ${COLOR.bold('notes.txt')} and ${COLOR.italic('briefly')} ${COLOR.strike('twice')} with ${COLOR.code('code')}`,
    ]);
    expect(off('wrote **notes.txt** and *briefly* ~~twice~~ with `code`', 100)).toEqual(['wrote notes.txt and briefly twice with code']);
  });

  it('reads a link as underlined text and its url dim after it, or the url alone when they are the same', () => {
    expect(on('see [the docs](https://x.y/z) now', 100)).toEqual([`see ${COLOR.link(COLOR.underline('the docs'))} ${COLOR.dim('(https://x.y/z)')} now`]);
    expect(off('see [the docs](https://x.y/z) now', 100)).toEqual(['see the docs (https://x.y/z) now']);
    expect(off('see [https://x.y](https://x.y)', 100)).toEqual(['see https://x.y']);
  });

  it('reads nothing inside code', () => {
    expect(off('keep `**not bold** and _not em_` as written', 100)).toEqual(['keep **not bold** and _not em_ as written']);
  });
});

describe('headings, rules and quotes', () => {
  it('sets a heading bold between blank rows, the first level underlined too', () => {
    expect(on('before\n# One\ntext\n## Two\nafter', 40)).toEqual([
      'before', '', COLOR.bold(COLOR.underline('One')), '', 'text', '', COLOR.bold('Two'), '', 'after',
    ]);
    expect(off('# Summary\nbody')).toEqual(['Summary', '', 'body']);
  });

  it('draws a rule as a dim line, no wider than forty columns', () => {
    expect(off('---', 100)).toEqual(['─'.repeat(40)]);
    expect(off('***', 20)).toEqual(['─'.repeat(20)]);
    expect(on('---', 100)).toEqual([COLOR.dim('─'.repeat(40))]);
  });

  it('puts a dim bar before every row of a quote, its text in italic, wrapped inside the bar', () => {
    const rows = on('> the lint failure was there before this change\n>\n> and it stays', 20);
    expect(rows.map(stripAnsi)).toEqual(['▎ the lint failure', '▎ was there before', '▎ this change', '▎', '▎ and it stays']);
    for (const row of rows) expect(row.startsWith(COLOR.dim('▎')), row).toBe(true);
    expect(rows[0]).toContain('\x1b[3m');
  });
});

describe('lists', () => {
  it('keeps the marker of an item, and hangs the rest of a long item under its text', () => {
    expect(off('- a long item that needs a second row to say all of it\n1. second kind of marker, with numbers', 24)).toEqual([
      '- a long item that needs',
      '  a second row to say',
      '  all of it',
      '1. second kind of',
      '   marker, with numbers',
    ]);
  });

  it('indents a nested item by its indent, and hangs it under its own text', () => {
    expect(off('- top\n  - nested item that is long enough to wrap\n    - third', 22)).toEqual([
      '- top',
      '  - nested item that',
      '    is long enough to',
      '    wrap',
      '    - third',
    ]);
  });

  it('goes on with an item on an indented line after it', () => {
    expect(off('- first\n  and more of it\n- second', 40)).toEqual(['- first', '  and more of it', '- second']);
  });

  it('reads the inline markup of an item', () => {
    expect(on('- is **new** and `code`', 40)).toEqual([`- is ${COLOR.bold('new')} and ${COLOR.code('code')}`]);
  });
});

describe('fenced code', () => {
  const FENCED = 'before\n```ts\nconst a = **b** + `c`;\n\n  indented();\n```\nafter';

  it('is its language in dim, then the code indented two columns, read for nothing and with no fence', () => {
    expect(off(FENCED)).toEqual(['before', '  ts', '  const a = **b** + `c`;', '', '    indented();', 'after']);
    expect(on(FENCED)).toEqual(['before', '  ' + COLOR.dim('ts'), '  const a = **b** + `c`;', '', '    indented();', 'after']);
  });

  it('has no label when it has no language, and runs an open block to the end', () => {
    expect(off('```\nnever closed **x**\nnor this')).toEqual(['  never closed **x**', '  nor this']);
  });

  it('closes only with a fence of the same kind and at least as long', () => {
    expect(off('~~~\n```\nstill code\n~~~\n**y**')).toEqual(['  ```', '  still code', 'y']);
  });

  it('cuts a line of code that is wider than the block, rather than leaving it to run off', () => {
    const rows = off('```\n' + 'x'.repeat(50) + '\n```', 20);
    expect(rows).toEqual(['  ' + 'x'.repeat(18), '  ' + 'x'.repeat(18), '  ' + 'x'.repeat(14)]);
  });
});

describe('tables', () => {
  const TABLE = '| name | lines |\n|:-----|------:|\n| cli.py | 120 |\n| app.ts | 7 |';

  it('is a box when it fits, the header bold, numbers to the right', () => {
    expect(off(TABLE, 40)).toEqual([
      '┌────────┬───────┐',
      '│ name   │ lines │',
      '├────────┼───────┤',
      '│ cli.py │   120 │',
      '│ app.ts │     7 │',
      '└────────┴───────┘',
    ]);
    expect(on(TABLE, 40)[1]).toContain(COLOR.bold('name  '));
  });

  it('is its lines as written when it does not fit', () => {
    expect(off(TABLE, 17)).toEqual(['| name | lines |', '|:-----|------:|', '| cli.py | 120 |', '| app.ts | 7 |']);
  });
});

describe('whatever it is fed', () => {
  it('has the same text with colour on and off, and never a row wider than the block', () => {
    let seed = 7;
    const next = (limit: number): number => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed % limit;
    };
    const alphabet = ['*', '**', '_', '`', '``', '[', ']', '(', ')', '[a](b)', 'a', 'bc', 'word ', ' ', '#', '# ', '- ', '1. ', '> ', '|', '---', '~~', '\n', '\n', '```', '~~~'];
    for (let trial = 0; trial < 400; trial += 1) {
      const text = Array.from({ length: next(40) }, () => alphabet[next(alphabet.length)]!).join('');
      const plain = off(text, 24);
      expect(on(text, 24).map(stripAnsi), JSON.stringify(text)).toEqual(plain);
      expect(plain.join('')).not.toContain('\x1b');
      for (const row of plain) expect(displayWidth(row), JSON.stringify(text)).toBeLessThanOrEqual(24);
    }
  });
});
