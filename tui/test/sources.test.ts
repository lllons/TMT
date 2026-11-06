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
  ["Ink's alternate screen", /alternateScreen\s*:\s*(?!false\b)/],
];
const INK = join(import.meta.dirname, '..', 'node_modules', 'ink');

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? files(path) : [path];
  });
}

describe('forbidden sequences in tui/src', () => {
  it('finds sources to check, the Ink screen among them', () => {
    expect(files(SOURCES).filter((path) => path.endsWith('.ts')).length).toBeGreaterThan(5);
    const ink = files(join(SOURCES, 'ink')).map((path) => path.slice(SOURCES.length + 1).replace(/\\/g, '/'));
    expect(ink).toEqual(expect.arrayContaining(['ink/layout.ts', 'ink/screen.ts', 'ink/terminal.tsx', 'ink/view.tsx']));
  });

  it('would catch each one, however it is spelled', () => {
    const samples = ["'\\x1b[?1049h'", "'\\x1b[3J'", "`\\x1b[${top};${bottom}r`", "'\\u001b[1;24r'", "'\\033[r'", 'ansiEscapes.clearTerminal',
      'render(view, { alternateScreen: true })', 'alternateScreen: options.full'];
    for (const sample of samples) expect(PATTERNS.some(([, pattern]) => pattern.test(sample))).toBe(true);
    expect(PATTERNS.some(([, pattern]) => pattern.test("'\\x1b[2K' + '\\x1b[12;1R'"))).toBe(false);
  });

  it.each(files(SOURCES))('%s has none', (path) => {
    const text = readFileSync(path, 'utf8');
    for (const [name, pattern] of PATTERNS) expect(pattern.test(text), `${name} in ${path}`).toBe(false);
  });
});

// Ink draws the screen, so what Ink itself can write counts too. Its clear
// paths were read for the version pinned in package.json (src/ink/terminal.tsx
// says how each is kept closed); a different version has to be read again.
describe('the Ink the screen is drawn with', () => {
  it('is the version that was read', () => {
    expect(JSON.parse(readFileSync(join(INK, 'package.json'), 'utf8')).version).toBe('8.0.0');
  });

  it('calls no library clear anywhere in its code', () => {
    const code = (path: string): string => readFileSync(path, 'utf8').split('\n').filter((line) => !/^\s*(?:\/\/|\*)/.test(line)).join('\n');
    const build = files(join(INK, 'build')).filter((path) => path.endsWith('.js'));
    expect(build.length).toBeGreaterThan(20);
    for (const path of build) expect(/clearTerminal|clearViewport|eraseScreen/.test(code(path)), path).toBe(false);
  });
});
