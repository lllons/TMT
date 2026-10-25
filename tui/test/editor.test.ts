import { describe, expect, it } from 'vitest';
import { LineEditor, normalizePaste, pasteLines } from '../src/input/editor.js';

describe('paste folding', () => {
  it('settles every kind of line break, and drops the trailing ones', () => {
    expect(normalizePaste('a\r\nb\rc\nd\n\n')).toBe('a\nb\nc\nd');
    expect(pasteLines('one line\n')).toBe(1);
    expect(pasteLines('a\r\nb')).toBe(2);
  });

  it('puts a single-line paste in verbatim, however long', () => {
    const editor = new LineEditor();
    const url = 'https://example.com/' + 'x'.repeat(400);
    editor.insert(url + '\n', true);
    expect(editor.value).toBe(url);
    expect(editor.pastes).toEqual([]);
  });

  it('folds a multi-line paste to a token that states its size', () => {
    const editor = new LineEditor();
    editor.insert('fix this: ');
    editor.insert('line 1\r\nline 2\r\nline 3\r\n', true);
    expect(editor.value).toBe('fix this: [Pasted text #1 +3 lines]');
    expect(editor.expanded()).toBe('fix this: line 1\nline 2\nline 3');
  });

  it('numbers successive pastes and expands each in place', () => {
    const editor = new LineEditor();
    editor.insert('a\nb', true);
    editor.insert(' and ');
    editor.insert('c\nd\ne', true);
    expect(editor.value).toBe('[Pasted text #1 +2 lines] and [Pasted text #2 +3 lines]');
    expect(editor.expanded()).toBe('a\nb and c\nd\ne');
  });

  it('leaves a token with no stored paste exactly as typed', () => {
    const editor = new LineEditor();
    editor.insert('see [Pasted text #3 +9 lines] here');
    expect(editor.expanded()).toBe('see [Pasted text #3 +9 lines] here');
    editor.insert('x\ny', true);
    expect(editor.expanded()).toBe('see [Pasted text #3 +9 lines] herex\ny');
  });

  it('keeps control characters out of the field', () => {
    const editor = new LineEditor();
    editor.insert('a\x1b[31mb\x07c');
    expect(editor.value).toBe('a[31mbc');
  });
});

describe('editing', () => {
  it('dismisses the placeholder on the first character and does not bring it back', () => {
    const editor = new LineEditor('Describe your next task');
    expect(editor.placeholderVisible).toBe(true);
    editor.insert('x');
    expect(editor.placeholderVisible).toBe(false);
    editor.handle('backspace');
    expect(editor.value).toBe('');
    expect(editor.placeholderVisible).toBe(false);
    editor.reset();
    expect(editor.placeholderVisible).toBe(true);
  });

  it('moves and deletes by grapheme, never through one', () => {
    const editor = new LineEditor();
    editor.insert('a👨‍👩‍👧é');
    editor.handle('left');
    expect(editor.cursor).toBe(editor.value.length - 2);
    editor.handle('left');
    expect(editor.cursor).toBe(1);
    editor.handle('delete');
    expect(editor.value).toBe('aé');
    editor.handle('end');
    editor.handle('backspace');
    expect(editor.value).toBe('a');
  });

  it('snaps a caret moved into the middle of a grapheme back to its start', () => {
    const editor = new LineEditor();
    editor.insert('a👨‍👩‍👧b');
    editor.moveTo(3);
    expect(editor.cursor).toBe(1);
    editor.moveTo(99);
    expect(editor.cursor).toBe(editor.value.length);
  });

  it('deletes the word before the caret', () => {
    const editor = new LineEditor();
    editor.insert('run the tests  ');
    editor.handle('delete-word');
    expect(editor.value).toBe('run the ');
  });

  it('submits on Enter, cancels on Escape, ends on Ctrl-D only when empty', () => {
    const editor = new LineEditor();
    expect(editor.handle('eof')).toBe('end');
    editor.insert('ab');
    editor.handle('home');
    expect(editor.handle('eof')).toBe('continue');
    expect(editor.value).toBe('b');
    expect(editor.handle('escape')).toBe('cancel');
    expect(editor.handle('enter')).toBe('submit');
  });
});
