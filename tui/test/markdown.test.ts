// The answer's markdown as terminal rows: what it recognises, and that
// everything it does not recognise comes out as it went in.

import { describe, expect, it } from 'vitest';
import { stripAnsi } from '../src/input/width.js';
import { markdownLines } from '../src/markdown.js';

const B = '\x1b[1m';
const B_OFF = '\x1b[22m';
const I = '\x1b[3m';
const I_OFF = '\x1b[23m';
const C = '\x1b[36m';
const C_OFF = '\x1b[39m';
const DIM = '\x1b[2m';
const RESET = '\x1b[0m';

const on = (text: string): string[] => markdownLines(text, true);
const off = (text: string): string[] => markdownLines(text, false);

describe('text with no markdown', () => {
  const PLAIN = [
    'Done — wrote notes.txt ✓',
    'a_b and snake_case_name and __init__.py and _private',
    'src/**/*.ts and lib/**/*.js, plus foo/*/bar/*/baz',
    'def f(*args, **kwargs): return 2 * 3 * 4',
    'rm *.txt and *.log; the glob *.{js,ts} too',
    '#hashtag and #include <stdio.h> and C# and a # b',
    'costs 3 - 2 = 1; version 1.5; 10) not a list; ***',
    'a lone * star, a lone _ underscore, a lone ` tick, a lone [bracket and ] another',
    'arr[0](1) and f(x)[1](y) and ![image](pic.png)',
    'unclosed **bold and *emphasis and `code and [link](http://x',
    'trailing spaces   ',
    '    indented four\n\tand a tab',
    'two\nlines\n\nand a blank one',
    '',
  ];

  it.each(PLAIN)('comes out exactly as it went in: %j', (text) => {
    expect(off(text).join('\n')).toBe(text);
    // With colour on, nothing is painted either.
    expect(on(text).join('\n')).toBe(text);
    expect(stripAnsi(on(text).join('\n'))).toBe(text);
  });

  it('keeps every character of a plain answer, line by line', () => {
    const text = 'I read the project layout first, then wrote the notes file.\n\nWhat changed so far:\nthe test suite still passes (14 tests)\n';
    expect(on(text).join('\n')).toBe(text);
    expect(on(text)).toHaveLength(text.split('\n').length);
  });
});

describe('inline markup', () => {
  it('reads **bold** as bold, with the markers gone', () => {
    expect(on('wrote **notes.txt** today')).toEqual([`wrote ${B}notes.txt${B_OFF} today`]);
    expect(on('**Note:** careful')).toEqual([`${B}Note:${B_OFF} careful`]);
    expect(off('wrote **notes.txt** today')).toEqual(['wrote notes.txt today']);
  });

  it('reads *em* and _em_ as italic', () => {
    expect(on('a *slight* change and an _odd_ one')).toEqual([`a ${I}slight${I_OFF} change and an ${I}odd${I_OFF} one`]);
    expect(on('(_aside_)')).toEqual([`(${I}aside${I_OFF})`]);
    expect(off('a *slight* change and an _odd_ one')).toEqual(['a slight change and an odd one']);
  });

  it('reads `code` in a colour of its own, and reads nothing inside it', () => {
    expect(on('run `npm test` now')).toEqual([`run ${C}npm test${C_OFF} now`]);
    expect(on('keep `**not bold** and _not em_` as written')).toEqual([`keep ${C}**not bold** and _not em_${C_OFF} as written`]);
    expect(on('two ticks ``a ` b`` done')).toEqual([`two ticks ${C}a \` b${C_OFF} done`]);
    expect(off('run `npm test` now')).toEqual(['run npm test now']);
  });

  it('nests: code and emphasis inside bold, bold and code inside a link, without closing the outer early', () => {
    expect(on('**the `file.ts` change**')).toEqual([`${B}the ${C}file.ts${C_OFF} change${B_OFF}`]);
    expect(on('**bold with *em* inside**')).toEqual([`${B}bold with ${I}em${I_OFF} inside${B_OFF}`]);
    expect(on('*em with **bold** inside*')).toEqual([`${I}em with ${B}bold${B_OFF} inside${I_OFF}`]);
    expect(on('[**docs**](http://x.y/z)')).toEqual([`${B}docs${B_OFF} (http://x.y/z)`]);
  });

  it('reads [text](url) as text (url)', () => {
    expect(off('see [the docs](https://example.com/a?b=1) now')).toEqual(['see the docs (https://example.com/a?b=1) now']);
    expect(on('see [the docs](https://example.com) now')).toEqual(['see the docs (https://example.com) now']);
  });

  it('gives the same rows with colour on and off, apart from the colour', () => {
    const text = '# Title with `code`\n\nSome **bold**, *em*, `code` and [a link](http://x.y).\n- item **one**\n1. item *two*\n```ts\nconst a = **b**;\n```\nend';
    expect(on(text).map(stripAnsi)).toEqual(off(text));
    expect(off(text).join('')).not.toContain('\x1b');
  });
});

describe('block markup', () => {
  it('reads a heading as a bold line without its #', () => {
    expect(on('# Summary')).toEqual([`${B}Summary${B_OFF}`]);
    expect(on('### A **bold** word and `code`')).toEqual([`${B}A bold word and ${C}code${C_OFF}${B_OFF}`]);
    expect(off('## Next steps')).toEqual(['Next steps']);
    // Not a heading: no space after the #s, or more than six.
    expect(on('#Summary')).toEqual(['#Summary']);
    expect(on('####### seven')).toEqual(['####### seven']);
  });

  it('keeps list items as written, their inline markup read after the marker', () => {
    expect(on('- notes.txt is **new** (12 lines)')).toEqual([`- notes.txt is ${B}new${B_OFF} (12 lines)`]);
    expect(on('* README.md got `a section`')).toEqual([`* README.md got ${C}a section${C_OFF}`]);
    expect(on('1. first *step*')).toEqual([`1. first ${I}step${I_OFF}`]);
    expect(on('  - nested')).toEqual(['  - nested']);
    expect(on('* **bold** start')).toEqual([`* ${B}bold${B_OFF} start`]);
    expect(on('---')).toEqual(['---']);
    expect(on('* * *')).toEqual(['* * *']);
  });

  it('keeps a fenced block as written and dimmed, with no markup read inside it', () => {
    const text = 'before **x**\n```js\nconst a = **b** + `c` + _d_;\n# not a heading\n\n  - not a list\n```\nafter **y**';
    expect(on(text)).toEqual([
      `before ${B}x${B_OFF}`,
      DIM + '```js' + RESET,
      DIM + 'const a = **b** + `c` + _d_;' + RESET,
      DIM + '# not a heading' + RESET,
      '',
      DIM + '  - not a list' + RESET,
      DIM + '```' + RESET,
      `after ${B}y${B_OFF}`,
    ]);
    // Colour off: the block is the same text, fences included, so it is still a block.
    expect(off(text)).toEqual([
      'before x', '```js', 'const a = **b** + `c` + _d_;', '# not a heading', '', '  - not a list', '```', 'after y',
    ]);
  });

  it('closes a fence only with a fence of the same kind, and runs an open one to the end', () => {
    expect(off('~~~\n```\nstill code **x**\n~~~\n**y**')).toEqual(['~~~', '```', 'still code **x**', '~~~', 'y']);
    expect(off('````\n```\nstill code\n````\n**y**')).toEqual(['````', '```', 'still code', '````', 'y']);
    expect(off('```\nnever closed **x**\nnor this')).toEqual(['```', 'never closed **x**', 'nor this']);
    // An opening fence line with a closing one on it is inline code, not a block.
    expect(off('```inline``` and **y**')).toEqual(['inline and y']);
  });

  it('keeps one row per line, and the same text with colour on or off, whatever it is fed', () => {
    let seed = 99;
    const next = (limit: number): number => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed % limit;
    };
    const alphabet = ['*', '**', '_', '`', '``', '[', ']', '(', ')', '[a](b)', 'a', 'bc', ' ', ' ', '#', '# ', '- ', '1. ', '\n', '\n', '```', '~~~', '!', '.'];
    for (let trial = 0; trial < 500; trial += 1) {
      const text = Array.from({ length: next(40) }, () => alphabet[next(alphabet.length)]!).join('');
      const plain = off(text);
      expect(plain, JSON.stringify(text)).toHaveLength(text.split('\n').length);
      expect(on(text).map(stripAnsi), JSON.stringify(text)).toEqual(plain);
      expect(plain.join('')).not.toContain('\x1b');
    }
  });

  it('turns CRLF line breaks into rows, and keeps blank rows', () => {
    expect(off('a\r\n\r\nb')).toEqual(['a', '', 'b']);
  });
});
