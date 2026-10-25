import { describe, expect, it } from 'vitest';
import { KeyParser } from '../src/input/keys.js';

describe('KeyParser', () => {
  it('reads typed text and control keys', () => {
    const parser = new KeyParser();
    expect(parser.feed('a')).toEqual([{ kind: 'text', text: 'a' }]);
    expect(parser.feed('\r')).toEqual([{ kind: 'key', key: 'enter' }]);
    expect(parser.feed('\x7f')).toEqual([{ kind: 'key', key: 'backspace' }]);
    expect(parser.feed('\x03')).toEqual([{ kind: 'key', key: 'ctrl-c' }]);
    expect(parser.feed('\x1b[D\x1bOC\x1b[3~\x1b[1;5H')).toEqual([
      { kind: 'key', key: 'left' }, { kind: 'key', key: 'right' },
      { kind: 'key', key: 'delete' }, { kind: 'key', key: 'home' },
    ]);
  });

  it('reads a bracketed paste whole, line breaks and all', () => {
    const parser = new KeyParser();
    expect(parser.feed('\x1b[200~one\rtwo\r\x1b[201~')).toEqual([{ kind: 'paste', text: 'one\rtwo\r' }]);
  });

  it('reads a bracketed paste split across reads, markers included', () => {
    const parser = new KeyParser();
    expect(parser.feed('\x1b[20')).toEqual([]);
    expect(parser.feed('0~first\nsec')).toEqual([]);
    expect(parser.feed('ond\x1b[20')).toEqual([]);
    expect(parser.feed('1~x')).toEqual([{ kind: 'paste', text: 'first\nsecond' }, { kind: 'text', text: 'x' }]);
  });

  it('reads one read of text carrying a line break as a paste, never as Enter', () => {
    const parser = new KeyParser();
    expect(parser.feed('ls\r\npwd\r\n')).toEqual([{ kind: 'paste', text: 'ls\r\npwd\r\n' }]);
  });

  it('reads a run that starts with a control character as that character', () => {
    const parser = new KeyParser();
    expect(parser.feed('\rabc')).toEqual([{ kind: 'key', key: 'enter' }, { kind: 'text', text: 'abc' }]);
  });

  it('holds a lone ESC until it is known to be the Escape key', () => {
    const parser = new KeyParser();
    expect(parser.feed('\x1b')).toEqual([]);
    expect(parser.pending).toBe(true);
    expect(parser.flush()).toEqual([{ kind: 'key', key: 'escape' }]);
    expect(parser.feed('\x1b')).toEqual([]);
    expect(parser.feed('[A')).toEqual([{ kind: 'key', key: 'up' }]);
  });

  it('reads SGR mouse presses and releases', () => {
    const parser = new KeyParser();
    expect(parser.feed('\x1b[<0;12;5M\x1b[<0;12;5m')).toEqual([
      { kind: 'mouse', button: 0, x: 12, y: 5, press: true },
      { kind: 'mouse', button: 0, x: 12, y: 5, press: false },
    ]);
  });

  it('reads a cursor position report only while one is expected', () => {
    let expecting = false;
    const parser = new KeyParser(() => expecting);
    expect(parser.feed('\x1b[1;2R')).toEqual([]);
    expecting = true;
    expect(parser.feed('\x1b[12;1R')).toEqual([{ kind: 'cpr', row: 12, column: 1 }]);
  });
});
