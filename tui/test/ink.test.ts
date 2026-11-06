// What only the Ink screen has to prove: every byte written through a whole
// session -- streaming, resizes, a shrink mid-stream, the exit -- is free of
// the sequences that cost the scrollback; Ink never owns the keyboard or the
// resize; the cursor waits under the frame with the caret drawn; and nothing
// of Ink's reaches the terminal once the screen has let it go. The shared
// scenarios (test/live.test.ts and the rest) run against it too, in the
// `ink` project.

import { afterEach, describe, expect, it } from 'vitest';
import { Box, Text } from 'ink';
import { createElement } from 'react';
import { createInkSessionScreen, type InkSessionScreen } from '../src/ink/screen.js';
import { FakeTerminal, forbiddenIn, isField, isRule, rowsWhere } from './harness.js';

const STATUS = 'status: thinking';

let screens: InkSessionScreen[] = [];
afterEach(() => {
  for (const screen of screens) screen.stop();
  screens = [];
});

function open(term: FakeTerminal, options: { mouseCaret?: boolean } = {}): InkSessionScreen {
  const screen = createInkSessionScreen({ stdin: term.stdin, stdout: term.stdout, color: false, ...options });
  screens.push(screen);
  return screen;
}

// Ink's full-screen path: home the cursor and erase the window, committed
// rows on it included. Never taken, because the frame is never that tall.
const HOME = /\x1b\[(?:1;1)?H|\x1b\[2J/;

/** No frame left anywhere but the live one: one box, one status row, in the whole buffer. */
function expectNoStaleRows(term: FakeTerminal): void {
  const all = term.all();
  expect(rowsWhere(all, isRule)).toHaveLength(2);
  expect(rowsWhere(all, isField)).toHaveLength(1);
  expect(rowsWhere(all, (row) => row.includes(STATUS))).toHaveLength(1);
}

/** The box on the last rows: field, bottom rule, status, then the cursor's row. */
function expectBoxOnFoot(term: FakeTerminal): void {
  const view = term.viewport();
  const rows = term.term.rows;
  expect(view[rows - 1]).toBe('');
  expect(view[rows - 2]).toBe(` ${STATUS}`);
  expect(isRule(view[rows - 3]!)).toBe(true);
}

describe('the Ink screen, byte for byte', () => {
  it('writes none of the forbidden sequences through streaming, resizes, a shrink mid-stream and the exit', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.setStatus(STATUS);
    screen.setBusy(true);
    const committed: string[] = [];
    const stream = async (count: number): Promise<void> => {
      for (let index = 0; index < count; index += 1) {
        const line = committed.length;
        screen.setReply(Array.from({ length: (line % 3) + 1 }, (_, row) => `partial ${line} row ${row}`).join('\n'));
        await term.settle(1);
        committed.push(`line ${line} ${'x'.repeat(line % 50)}`);
        screen.commit(committed.at(-1)!);
        await term.settle(1);
      }
      await term.settle();
    };
    await stream(30);
    expectBoxOnFoot(term);
    // Each resize lands mid-stream: the frame keeps changing while the
    // terminal is asked where the cursor is.
    for (const [columns, rows] of [[40, 12], [200, 50], [80, 24]] as const) {
      term.resize(columns, rows);
      await stream(15);
      expectNoStaleRows(term);
      expectBoxOnFoot(term);
    }
    // A shrink below the height of the frame while a tall reply streams, and back.
    screen.setReply(Array.from({ length: 12 }, (_, row) => `tall reply row ${row}`).join('\n'));
    await term.settle();
    term.resize(80, 10);
    screen.setReply(Array.from({ length: 13 }, (_, row) => `tall reply row ${row}`).join('\n'));
    await term.settle();
    expectBoxOnFoot(term);
    term.resize(80, 24);
    await term.settle();
    expectNoStaleRows(term);
    await stream(5);
    expectBoxOnFoot(term);
    screen.setReply('');
    screen.stop();
    await term.settle();
    expect(forbiddenIn(term.raw)).toEqual([]);
    expect(term.raw).not.toMatch(HOME);
    // Every committed row, once and in order, and nothing of the frame left
    // behind -- but the reply rows the first, narrower window pushed above
    // itself mid-stream, which stay in the scrollback with either renderer
    // (README, "Known limits"; test/ink-vs-live.test.ts).
    const all = term.all();
    expect(all.filter((row) => /^line \d+\b/.test(row))).toEqual(committed.map((text) => text.slice(0, 80).trimEnd()));
    expect(all.some((row) => row.includes('tall reply') || isField(row) || row.includes(STATUS))).toBe(false);
    expect(term.viewport().some((row) => row.includes('partial'))).toBe(false);
    expect(term.raw.endsWith('\x1b[?2004l\x1b[?25h')).toBe(true);
    expect(term.rawMode).toBe(false);
  }, 60_000);

  it('leaves a committed row wider than the window to the terminal to wrap, and to join up again', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.commit('y'.repeat(120));
    await term.settle();
    expect(term.all().filter((row) => row.startsWith('y'))).toEqual(['y'.repeat(80), 'y'.repeat(40)]);
    term.resize(160, 24);
    await term.settle();
    expect(term.all().filter((row) => row.startsWith('y'))).toEqual(['y'.repeat(120)]);
  });

  it('commits an Ink component once, measured, like text', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.setStatus(STATUS);
    screen.commitNode(createElement(Box, { flexDirection: 'column' },
      createElement(Text, null, 'a component'), createElement(Text, null, '  on two rows')));
    screen.commit('then text');
    await term.settle();
    const all = term.all();
    expect(all.filter((row) => row === 'a component')).toHaveLength(1);
    expect(all[all.indexOf('a component') + 1]).toBe('  on two rows');
    expect(all[all.indexOf('a component') + 2]).toBe('then text');
    expectBoxOnFoot(term);
  });
});

describe('the Ink screen and the terminal', () => {
  it('reads the keyboard itself: Ink never listens to stdin, puts it in raw mode, or hears a resize', async () => {
    const term = new FakeTerminal(80, 24);
    const modes: boolean[] = [];
    term.stdin.setRawMode = (mode: boolean) => { modes.push(mode); term.rawMode = mode; };
    const screen = open(term);
    screen.commit('kept');
    await term.settle();
    expect(term.stdin.listenerCount('data')).toBe(1);
    expect(term.stdin.listenerCount('readable')).toBe(0);
    expect(term.stdout.listenerCount('resize')).toBe(1);
    expect(modes).toEqual([true]);
    // Nothing Ink would ask the terminal (the keyboard protocol query).
    expect(term.raw).not.toContain('\x1b[?u');
    screen.stop();
    expect(modes).toEqual([true, false]);
    expect(term.stdin.listenerCount('data')).toBe(0);
    expect(term.stdout.listenerCount('resize')).toBe(0);
  });

  it('parks the hidden cursor at column 0 under the frame and draws the caret in the field', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    screen.setStatus(STATUS);
    term.type('hello world');
    term.type('\x1b[D\x1b[D\x1b[D');
    await term.settle();
    const buffer = term.term.buffer.active;
    expect(buffer.cursorY).toBe(23);
    expect(buffer.cursorX).toBe(0);
    expect(term.raw.lastIndexOf('\x1b[?25l')).toBeGreaterThan(term.raw.lastIndexOf('\x1b[?25h'));
    const field = term.viewport().findIndex(isField);
    expect(term.viewport()[field]).toBe(' > hello world');
    const line = buffer.getLine(buffer.viewportY + field)!;
    // The caret is on the r of "world", three to the left of the end.
    expect(line.getCell(3 + 8)!.getChars()).toBe('r');
    expect(line.getCell(3 + 8)!.isInverse()).toBeTruthy();
    expect(line.getCell(3 + 7)!.isInverse()).toBeFalsy();
    screen.stop();
    expect(term.raw.endsWith('\x1b[?25h')).toBe(true);
  });

  it('queues lines typed during a turn and answers both kinds of question while a reply streams', async () => {
    const term = new FakeTerminal(80, 24);
    const screen = open(term);
    const sent: string[] = [];
    screen.onSubmit((text) => sent.push(text));
    screen.setStatus(STATUS);
    screen.setBusy(true);
    screen.setReply('streaming');
    term.type('next task');
    term.type('\r');
    await term.settle();
    expect(screen.queued()).toEqual(['next task']);
    // The box stays empty while a turn runs; how many wait is in the footer (test/ink-look.test.ts).
    expect(term.viewport().find(isField)!.trim()).toBe('>');
    const key = screen.ask({ hint: '1-2, Esc to skip', keys: ['1', '2'] });
    await term.settle();
    expect(term.viewport().find(isField)).toBe(' > 1-2, Esc to skip');
    term.type('2');
    expect(await key).toBe('2');
    const line = screen.ask({ hint: 'y / n  then Enter' });
    term.type('y');
    term.type('\r');
    expect(await line).toBe('y');
    expect(sent).toEqual([]);
    screen.setBusy(false);
    expect(sent).toEqual(['next task']);
    await term.settle();
    expectNoStaleRows(term);
    expect(forbiddenIn(term.raw)).toEqual([]);
  });

  it('restores the terminal when stopped or suspended while a resize is still unanswered', async () => {
    for (const how of ['stop', 'suspend'] as const) {
      const term = new FakeTerminal(80, 24, { answers: false });
      const screen = open(term, { mouseCaret: true });
      screen.setStatus(STATUS);
      screen.commit('before');
      await term.settle();
      term.resize(50, 20);
      screen.commit('during');
      if (how === 'stop') screen.stop();
      else screen.suspend();
      await term.settle();
      expect(term.rawMode).toBe(false);
      expect(term.raw).toContain('\x1b[?1006l\x1b[?1000l\x1b[?2004l\x1b[?25h');
      expect(term.all()).toContain('during');
      expect(term.all().some((row) => isField(row) || row.includes(STATUS))).toBe(false);
      if (how === 'suspend') {
        screen.resume();
        await term.settle();
        expect(term.rawMode).toBe(true);
        expectBoxOnFoot(term);
        expectNoStaleRows(term);
      }
    }
  });

  it('lets nothing of Ink through once the process is on its way out', async () => {
    const term = new FakeTerminal(80, 24);
    const before = process.listeners('exit');
    const screen = open(term);
    screen.setStatus(STATUS);
    await term.settle();
    const ours = process.listeners('exit').filter((listener) => !before.includes(listener));
    expect(ours).toHaveLength(1);
    (ours[0] as () => void)();
    const written = term.raw.length;
    // Ink's own exit handler, run the way signal-exit runs it at exit.
    const exits = (process as unknown as { __signal_exit_emitter__?: { listeners(event: string): ((...args: unknown[]) => void)[] } }).__signal_exit_emitter__;
    for (const listener of exits?.listeners('exit') ?? []) listener(0, null);
    await term.settle();
    expect(term.raw.length).toBe(written);
    expect(term.rawMode).toBe(false);
  });
});
