// The pure parts of format.ts that decide what the box and the record say.

import { describe, expect, it } from 'vitest';
import { approvalHint, finalLines, questionHint } from '../src/format.js';
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
  it('gives a range for numbered keys and a list for the others', () => {
    expect(questionHint(['1', '2'])).toBe('1-2, then Enter, Esc to skip');
    expect(questionHint(['1', '2', '3', '4'])).toBe('1-4, then Enter, Esc to skip');
    expect(questionHint(['1'])).toBe('1, then Enter, Esc to skip');
    expect(questionHint(['1', '3'])).toBe('1 / 3, then Enter, Esc to skip');
    expect(questionHint(['y', 'n'])).toBe('y / n, then Enter, Esc to skip');
    expect(questionHint([])).toBe('Enter or Esc to skip');
  });
});

describe('finalLines', () => {
  it('sets the answer between blank rows and reads its markdown, painted only when colour is on', () => {
    expect(finalLines('Wrote **notes.txt**.\n\n', false)).toEqual(['', 'Wrote notes.txt.', '']);
    expect(finalLines('Wrote **notes.txt**.', true)).toEqual(['', 'Wrote \x1b[1mnotes.txt\x1b[22m.', '']);
    expect(finalLines('line one\nline two', true)).toEqual(['', 'line one', 'line two', '']);
  });
});
