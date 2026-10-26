// The pure parts of format.ts that decide what the box and the record say.

import { describe, expect, it } from 'vitest';
import { approvalHint, commandLines, finalLines, makeStyle, questionHint, renderedRows } from '../src/format.js';
import { displayWidth } from '../src/input/width.js';

describe('approvalHint', () => {
  it('lists the keys the approval takes, in order, and is short enough for the box', () => {
    const choices = [{ key: 'y', label: 'Run it once' }, { key: 'a', label: 'Allow it' }, { key: 'n', label: 'Refuse' }];
    expect(approvalHint(choices, 'Type y and Enter to run it once, a to always allow it, or anything else to refuse:')).toBe('y / a / n  then Enter');
    expect(approvalHint(choices.slice(0, 1))).toBe('y  then Enter');
    expect(displayWidth(approvalHint(choices))).toBeLessThan(40);
  });

  it('falls back to the backend sentence, then to a generic one, when there are no choices', () => {
    expect(approvalHint([], 'Type yes or no')).toBe('Type yes or no');
    expect(approvalHint([])).toBe('Type your answer, then Enter');
  });
});

describe('questionHint', () => {
  it('gives a range for numbered keys and a list for the others, with no Enter: one key answers', () => {
    expect(questionHint(['1', '2'])).toBe('1-2, Esc to skip');
    expect(questionHint(['1', '2', '3', '4'])).toBe('1-4, Esc to skip');
    expect(questionHint(['1'])).toBe('1, Esc to skip');
    expect(questionHint(['1', '3'])).toBe('1 / 3, Esc to skip');
    expect(questionHint(['y', 'n'])).toBe('y / n, Esc to skip');
    expect(questionHint([])).toBe('Enter or Esc to skip');
  });
});

describe('renderedRows', () => {
  it('takes a non-empty list of strings as it is, blank rows and colour included', () => {
    const rows = ['', '\x1b[38;2;1;2;3mTMT\x1b[0m', '', 'x'];
    expect(renderedRows(rows)).toBe(rows);
  });

  it('is null for anything else: absent, not a list, empty, or with a row that is not a string', () => {
    for (const value of [undefined, null, 'rows', 5, {}, [], [1, 2], ['a', 1], [{ label: 'Steps', value: '3' }], ['a', null]]) {
      expect(renderedRows(value)).toBeNull();
    }
  });
});

describe('commandLines', () => {
  it('draws the older object rows and passes over strings', () => {
    const style = makeStyle(false);
    const rows = [{ label: 'Steps', value: '3' }, 'a string', { text: 'a note' }] as never;
    expect(commandLines({ type: 'command', title: 'Plan', rows }, style)).toEqual(['Plan', '  Steps  3', '  a note']);
  });
});

describe('finalLines', () => {
  it('sets the answer between blank rows and reads its markdown, painted only when colour is on', () => {
    expect(finalLines('Wrote **notes.txt**.\n\n', false)).toEqual(['', 'Wrote notes.txt.', '']);
    expect(finalLines('Wrote **notes.txt**.', true)).toEqual(['', 'Wrote \x1b[1mnotes.txt\x1b[22m.', '']);
    expect(finalLines('line one\nline two', true)).toEqual(['', 'line one', 'line two', '']);
  });
});
