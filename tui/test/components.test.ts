// The blocks of the record, rendered by Ink to a string at 40, 80 and 120
// columns, with colour and without: the rows they hold, the colours the theme
// says, and that no row is wider than the block.

import { renderToString } from 'ink';
import { createElement, type ComponentType } from 'react';
import { describe, expect, it } from 'vitest';
import { Answer } from '../src/ink/components/Answer.js';
import { Dialog } from '../src/ink/components/Dialog.js';
import { Header } from '../src/ink/components/Header.js';
import { Notice } from '../src/ink/components/Notice.js';
import { Task } from '../src/ink/components/Task.js';
import { factRows, ToolCall } from '../src/ink/components/ToolCall.js';
import { displayWidth, stripAnsi } from '../src/input/width.js';
import { fgCode, GREEN, INFO, makeTheme, ORANGE, RED, YELLOW } from '../src/theme.js';

const WIDTHS = [40, 80, 120] as const;
const COLOR = makeTheme({ color: true, truecolor: true });
const PLAIN = makeTheme({ color: false, truecolor: true });

/** The rows of a component at `width`, as Ink writes them. */
function rows<P extends object>(component: ComponentType<P>, props: Omit<P, 'theme' | 'width'>, width: number, color = false): string[] {
  const element = createElement(component as ComponentType<object>, { ...props, theme: color ? COLOR : PLAIN, width });
  return renderToString(element, { columns: width }).split('\n');
}

const fits = (all: string[], width: number): void => {
  for (const row of all) expect(displayWidth(stripAnsi(row)), row).toBeLessThanOrEqual(width);
};

describe('the rail', () => {
  it('is made of glyphs one column wide, by string-width', () => {
    for (const glyph of ['┃', '│', '║', '━', '▌', '┇', '○', '◐', '◉', '!', '✓', '✗', '◆', '·', '⎇']) expect(displayWidth(glyph), glyph).toBe(1);
  });

  it('holds in 200 columns as in the narrower ones: every row of a block on its rail, none wider than the block', () => {
    const long = 'word '.repeat(80).trim();
    const blocks: [string, string[], string][] = [
      ['║', rows(Task, { text: long }, 200), '║'],
      ['┃', rows(Answer, { text: long + '\n\n- one\n- two' }, 200), '┃'],
      ['│', rows(ToolCall, { label: 'Bash', target: 'x'.repeat(300), tone: 'ok', message: long }, 200), '│'],
      ['┃', rows(Dialog, { title: 'Run this command?', body: [long], options: [{ key: 'y', label: long }] }, 200).slice(2), '┃'],
      ['│', rows(Header, { version: '0.1.1', workspace: long }, 200).slice(1, -1), '│'],
    ];
    for (const [, out, rail] of blocks) {
      fits(out, 200);
      for (const row of out.filter((text) => text !== '')) expect(row.startsWith(rail), row.slice(0, 20)).toBe(true);
    }
  });
});

describe('a tool call', () => {
  const READ = { label: 'Read File', target: 'parser.py', tone: 'ok' as const, kind: 'file_read', message: 'Read parser.py', detail: { lines: 42 } };
  const RAIL = (rgb: readonly [number, number, number], glyph = '│') => `${fgCode(rgb, true)}${glyph}\x1b[39m`;

  it.each(WIDTHS)('is a blank row, the call, and its result under the same rail (%i columns)', (width) => {
    const out = rows(ToolCall, READ, width);
    expect(out).toEqual(['', '│ Read File parser.py', '│ Read parser.py', '│ Read 42 lines']);
    fits(out, width);
    expect(rows(ToolCall, READ, width, true).map(stripAnsi)).toEqual(out);
  });

  it('paints the rail green and the name bold when it worked, red when it did not, the result dim', () => {
    const [, header, message, facts] = rows(ToolCall, READ, 80, true);
    expect(header).toBe(`${RAIL(GREEN)} \x1b[1mRead File\x1b[22m parser.py`);
    expect(message).toBe(`${RAIL(GREEN)} \x1b[2mRead parser.py\x1b[22m`);
    // The count is dim with the rest of the sentence.
    expect(facts).toBe(`${RAIL(GREEN)} \x1b[2mRead 42 lines\x1b[22m`);
    const failed = rows(ToolCall, { ...READ, tone: 'error' as const }, 80, true);
    for (const row of failed.slice(1)) expect(row.startsWith(RAIL(RED))).toBe(true);
  });

  it('says what was added and removed, wrote for a new file, removed for a deletion, all dim', () => {
    const facts = (kind: string, detail: Record<string, unknown>) => factRows(PLAIN, kind, detail);
    expect(facts('file_edit', { added: 3, removed: 1 })).toEqual(['Added 3 lines, removed 1 line']);
    expect(facts('file_edit', { added: 1, removed: 0 })).toEqual(['Added 1 line']);
    expect(facts('file_edit', { added: 0, removed: 5 })).toEqual(['Removed 5 lines']);
    expect(facts('file_create', { added: 12, removed: 0 })).toEqual(['Wrote 12 lines']);
    expect(facts('file_create', { added: 12, removed: 3 })).toEqual(['Added 12 lines, removed 3 lines']);
    expect(facts('file_delete', { added: 0, removed: 9 })).toEqual(['Removed 9 lines']);
    expect(facts('file_edit', { added: 0, removed: 0 })).toEqual([]);
    expect(factRows(COLOR, 'file_edit', { added: 3, removed: 1 })).toEqual(['\x1b[2mAdded 3 lines, removed 1 line\x1b[22m']);
  });

  it('shows files, bytes and calls as dim facts, and the exit status red when it is not zero', () => {
    expect(factRows(PLAIN, 'tool', { files: 3, bytes: 120, calls: 1 })).toEqual(['3 files · 120 bytes · 1 call']);
    expect(factRows(PLAIN, 'command', { status: 0 })).toEqual(['exit 0']);
    expect(factRows(COLOR, 'command', { status: 0 })).toEqual(['\x1b[2mexit 0\x1b[22m']);
    expect(factRows(COLOR, 'command', { status: 2 })).toEqual([`${fgCode(RED, true)}exit 2\x1b[39m`]);
    expect(factRows(PLAIN, 'command', { status: 1, files: 2 })).toEqual(['2 files · exit 1']);
    expect(factRows(PLAIN, 'tool', undefined)).toEqual([]);
    expect(factRows(PLAIN, 'tool', { lines: 'many', added: null })).toEqual([]);
  });

  it.each(WIDTHS)('cuts a long target to the row with an ellipsis (%i columns)', (width) => {
    const out = rows(ToolCall, { label: 'Bash', target: 'npm run build && npm test -- --coverage --reporter=verbose ' + 'x'.repeat(100), tone: 'ok' }, width);
    expect(out).toHaveLength(2 + 0);
    expect(out[1]!.startsWith('│ Bash npm run build')).toBe(true);
    expect(out[1]!.endsWith('…')).toBe(true);
    expect(displayWidth(out[1]!)).toBe(width);
  });

  it('puts a command on one row, and has no result row when there is nothing to say', () => {
    expect(rows(ToolCall, { label: 'Bash', target: 'npm test &&\n  npm run lint', tone: 'ok' }, 80)).toEqual(['', '│ Bash npm test && npm run lint']);
  });

  it.each(WIDTHS)('shows three lines of a long result and then how many more (%i columns)', (width) => {
    const message = Array.from({ length: 12 }, (_, index) => `line ${index} of the output`).join('\n');
    const out = rows(ToolCall, { label: 'Bash', target: 'ls', tone: 'ok', kind: 'command', message }, width);
    expect(out.slice(2)).toEqual(['│ line 0 of the output', '│ line 1 of the output', '│ line 2 of the output', '│ … +9 lines']);
    fits(out, width);
  });

  it('shows four lines in full: folding one would say as much as it saves', () => {
    const message = Array.from({ length: 4 }, (_, index) => `line ${index}`).join('\n');
    expect(rows(ToolCall, { label: 'Bash', target: '', tone: 'ok', message }, 80).slice(2)).toEqual(['│ line 0', '│ line 1', '│ line 2', '│ line 3']);
  });

  it('wraps a long result sentence on the rail at the width', () => {
    const out = rows(ToolCall, { label: 'Bash', target: 'x', tone: 'ok', message: 'one two three four five six seven eight nine ten eleven twelve' }, 30);
    expect(out.slice(2)).toEqual(['│ one two three four five six', '│ seven eight nine ten eleven', '│ twelve']);
  });

  it('gives an error as a red result line on a red rail, and no facts', () => {
    const props = { label: 'Patch File', target: 'cli.py', tone: 'error' as const, error: 'the file changed since it was read', detail: { added: 3 } };
    expect(rows(ToolCall, props, 80)).toEqual(['', '│ Patch File cli.py', '│ the file changed since it was read']);
    const [, header, result] = rows(ToolCall, props, 80, true);
    expect(header.startsWith(RAIL(RED))).toBe(true);
    expect(result).toBe(`${RAIL(RED)} ${fgCode(RED, true)}the file changed since it was read\x1b[39m`);
  });

  it('has no colour at all when it is off', () => {
    expect(rows(ToolCall, { ...READ, tone: 'error' as const }, 80).join('\n')).not.toContain('\x1b');
  });
});

describe('the task', () => {
  const TEXT = 'Please refactor the   authentication module\nso the login handler lives in its own file and add tests for it';

  it.each(WIDTHS)('is a blank row, then the text with its whitespace collapsed behind a double rail, wrapped under itself (%i columns)', (width) => {
    const out = rows(Task, { text: TEXT }, width);
    expect(out[0]).toBe('');
    expect(out[1]!.startsWith('║ Please refactor')).toBe(true);
    for (const row of out.slice(1)) expect(row.startsWith('║ ')).toBe(true);
    expect(out.slice(1).map((row) => row.slice(2)).join(' ')).toBe(TEXT.replace(/\s+/g, ' '));
    fits(out, width);
  });

  it('is in the terminal\'s own colours: no background, no colour on the rail or the text', () => {
    const out = rows(Task, { text: 'hi' }, 40, true);
    expect(out[1]).toBe('║ hi');
  });
});

describe('the answer', () => {
  it.each(WIDTHS)('is a blank row, the markdown on a heavy rail, and a blank row (%i columns)', (width) => {
    const out = rows(Answer, { text: '## Done\n\nI wrote **notes.txt**.\n\n- one\n- two' }, width);
    expect(out).toEqual(['', '┃ Done', '┃', '┃ I wrote notes.txt.', '┃', '┃ - one', '┃ - two', '']);
    fits(out, width);
  });

  it('has the rail green on every row of the block, the blank rows inside it included, and none outside it', () => {
    const out = rows(Answer, { text: 'one\n\ntwo' }, 40, true);
    const rail = `${fgCode(GREEN, true)}┃\x1b[39m`;
    expect(out).toEqual(['', `${rail} one`, rail, `${rail} two`, '']);
  });

  it('wraps a long line at the block width less the gutter, and says nothing for an empty answer', () => {
    const out = rows(Answer, { text: 'word '.repeat(30).trim() }, 40);
    expect(out.length).toBeGreaterThan(3);
    fits(out, 40);
    for (const row of out.slice(1, -1)) expect(row.startsWith('┃ ')).toBe(true);
    expect(rows(Answer, { text: '' }, 40)).toEqual(['']);
  });

  it('has inline code and links in the reference hue and a quote\'s bar dim', () => {
    const out = rows(Answer, { text: 'run `npm test`\n\n> said so' }, 80, true);
    expect(out[1]).toContain(`${fgCode(INFO, true)}npm test\x1b[39m`);
    expect(out[3]).toContain('\x1b[2m┇\x1b[22m');
    expect(rows(Answer, { text: '> said so' }, 80)[1]).toBe('┃ ┇ said so');
  });
});

describe('a notice', () => {
  it.each(WIDTHS)('is a glyph and the text, wrapped under itself (%i columns)', (width) => {
    const text = 'Stream failed: the provider did not answer within the time it was given and the request was dropped';
    const out = rows(Notice, { kind: 'error', text }, width);
    expect(out[0]!.startsWith('✗ Stream failed')).toBe(true);
    for (const row of out.slice(1)) expect(row.startsWith('  ')).toBe(true);
    expect(out.map((row) => row.trim()).join(' ').replace('✗ ', '')).toBe(text);
    fits(out, width);
  });

  it('has a glyph for each kind, and none for plain information', () => {
    const first = (kind: 'error' | 'warning' | 'success' | 'milestone' | 'progress' | 'info') => rows(Notice, { kind, text: 'text' }, 80)[0];
    expect(['error', 'warning', 'success', 'milestone', 'progress', 'info'].map((kind) => first(kind as 'error'))).toEqual(
      ['✗ text', '! text', '✓ text', '◆ text', '· text', 'text']);
  });

  it('paints an error red and a warning yellow whole, a milestone\'s glyph in the reference hue, a success\'s green, progress dim', () => {
    const one = (kind: 'error' | 'warning' | 'success' | 'milestone' | 'progress') => rows(Notice, { kind, text: 'x' }, 80, true)[0];
    expect(one('error')).toBe(`${fgCode(RED, true)}✗ x\x1b[39m`);
    expect(one('warning')).toBe(`${fgCode(YELLOW, true)}! x\x1b[39m`);
    expect(one('success')).toBe(`${fgCode(GREEN, true)}✓\x1b[39m x`);
    expect(one('milestone')).toBe(`${fgCode(INFO, true)}◆\x1b[39m x`);
    expect(one('progress')).toBe('\x1b[2m· x\x1b[22m');
  });
});

describe('a dialog', () => {
  const props = {
    title: 'Run this command?',
    body: ['TMT wants to run a command that needs your approval.', '', '    npm test', '    in .'],
    options: [{ key: 'y', label: 'Run it once' }, { key: 'a', label: 'Allow `npm test` in this project from now on' }, { key: 'n', label: 'Refuse' }],
  };

  it.each(WIDTHS)('is a heavy rule, a railed title, the text indented two columns, and the keys (%i columns)', (width) => {
    const out = rows(Dialog, props, width);
    expect(out.slice(0, 3)).toEqual(['', '━'.repeat(width), '┃ Run this command?']);
    expect(out[3]!.startsWith('┃   TMT')).toBe(true);
    expect(out).toContain('┃       npm test');
    expect(out).toContain('┃');
    const keys = out.filter((row) => /^┃ [yan] {2}/.test(row));
    expect(keys.map((row) => row.slice(0, 3))).toEqual(['┃ y', '┃ a', '┃ n']);
    expect(out.filter((row) => row === '┃ n  Refuse')).toHaveLength(1);
    // Every row under the rule is on the rail.
    for (const row of out.slice(2)) expect(row.startsWith('┃')).toBe(true);
    fits(out, width);
  });

  it('hangs a long choice under its own text', () => {
    const out = rows(Dialog, props, 40);
    const at = out.findIndex((row) => row.startsWith('┃ a  Allow'));
    expect(out[at + 1]!.startsWith('┃    ')).toBe(true);
    expect(out[at + 1]!.slice(2).trim().length).toBeGreaterThan(0);
  });

  it('draws the rule and the rail in orange, the title bold, and the key bold in orange', () => {
    const out = rows(Dialog, props, 40, true);
    const rail = `${fgCode(ORANGE, true)}┃\x1b[39m`;
    expect(out[1]).toBe(`${fgCode(ORANGE, true)}${'━'.repeat(40)}\x1b[39m`);
    expect(out[2]).toBe(`${rail} \x1b[1mRun this command?\x1b[22m`);
    expect(out.find((row) => row.includes('Run it once'))).toBe(`${rail} \x1b[1m${fgCode(ORANGE, true)}y\x1b[39m\x1b[22m  Run it once`);
  });

  it('is the text alone, with no choices, for a question the backend did not structure', () => {
    expect(rows(Dialog, { title: 'Question', body: ['Which colour?', '', '  1. Red'] }, 40)).toEqual(['', '━'.repeat(40), '┃ Question', '┃   Which colour?', '┃', '┃     1. Red']);
  });
});

describe('the header', () => {
  const props = { version: '0.1.1', workspace: 'C:\\work\\app', tip: { gesture: '/notes', detail: 'shows what TMT knows about this project and what is left' } };

  it.each(WIDTHS)('is TMT and its version, the workspace and the tip on a thin rail, between blank rows (%i columns)', (width) => {
    const out = rows(Header, props, width);
    expect(out[0]).toBe('');
    expect(out[1]).toBe('│ TMT 0.1.1');
    expect(out[2]).toBe('│ workspace  C:\\work\\app');
    expect(out[3]!.startsWith('│ tip  /notes  shows what TMT knows')).toBe(true);
    expect(out.at(-1)).toBe('');
    for (const row of out.slice(1, -1)) expect(row.startsWith('│ ')).toBe(true);
    fits(out, width);
  });

  it('is dim but for the name, which is bold green, and says nothing it was not told', () => {
    const out = rows(Header, props, 120, true);
    const rail = '\x1b[2m│\x1b[22m';
    expect(out[1]).toBe(`${rail} \x1b[1m${fgCode(GREEN, true)}TMT\x1b[39m\x1b[22m \x1b[2m0.1.1\x1b[22m`);
    expect(out[2]).toBe(`${rail} \x1b[2mworkspace  C:\\work\\app\x1b[22m`);
    expect(rows(Header, {}, 80)).toEqual(['', '│ TMT', '']);
  });
});
