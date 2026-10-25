// The TypeScript twin of the Python tests that grep the modules for DECSTBM:
// the sources may not contain the alternate screen, a scroll region or an
// erase of the scrollback, however they spell the escape.

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SOURCES = join(import.meta.dirname, '..', 'src');
// ESC as source code spells it: \x1b, \u001b, \u{1b}, \033, \e, or the raw byte.
const ESC = String.raw`(?:\\x1[bB]|\\u001[bB]|\\u\{1[bB]\}|\\033|\\e|\x1b)`;
const PATTERNS: [string, RegExp][] = [
  ['alternate screen', /\?(?:1049|1047|47)[hl]/],
  ['erase scrollback (3J)', new RegExp(ESC + String.raw`\[3J`)],
  ['scroll region (DECSTBM)', new RegExp(ESC + String.raw`\[(?:[0-9;?]|\$\{[^}]*\})*r`)],
  ['a library clear', /clearTerminal|eraseScreen|enterAlternativeScreen/],
];

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? files(path) : [path];
  });
}

describe('forbidden sequences in tui/src', () => {
  it('finds sources to check', () => {
    expect(files(SOURCES).filter((path) => path.endsWith('.ts')).length).toBeGreaterThan(5);
  });

  it('would catch each one, however it is spelled', () => {
    const samples = ["'\\x1b[?1049h'", "'\\x1b[3J'", "`\\x1b[${top};${bottom}r`", "'\\u001b[1;24r'", "'\\033[r'", 'ansiEscapes.clearTerminal'];
    for (const sample of samples) expect(PATTERNS.some(([, pattern]) => pattern.test(sample))).toBe(true);
    expect(PATTERNS.some(([, pattern]) => pattern.test("'\\x1b[2K' + '\\x1b[12;1R'"))).toBe(false);
  });

  it.each(files(SOURCES))('%s has none', (path) => {
    const text = readFileSync(path, 'utf8');
    for (const [name, pattern] of PATTERNS) expect(pattern.test(text), `${name} in ${path}`).toBe(false);
  });
});
