// The live frame is a pure function of its state and the window.

import { describe, expect, it } from 'vitest';
import { LineEditor } from '../src/input/editor.js';
import { composeFrame } from '../src/live/frame.js';

const frame = (reply: string, columns: number) =>
  composeFrame({ editor: new LineEditor(), reply, status: '', caption: '', pending: [], busy: true, color: false }, columns, 24);

describe('the live reply in the frame', () => {
  it('wraps at word boundaries, never in the middle of a word', () => {
    const reply = 'streamed line 15: the quick brown fox jumps over the lazy dog and keeps going a while';
    const rows = frame(reply, 40).plain.slice(0, -3).map((row) => row.trimEnd());
    expect(rows.length).toBeGreaterThan(1);
    expect(rows.join(' ').split(/\s+/).filter(Boolean)).toEqual(reply.split(/\s+/));
  });

  it('keeps a trailing line break as an empty last row', () => {
    const rows = frame('one\ntwo\n', 40).plain.slice(0, -3);
    expect(rows.map((row) => row.trimEnd()).slice(-3)).toEqual(['one', 'two', '']);
  });
});
