// The pure parts of format.ts that decide what the box and the record say.

import { describe, expect, it } from 'vitest';
import {
  approvalEcho, approvalHint, approvalTitle, commandLines, elapsedText, isResultKind, makeStyle, oneLine, questionHint, renderedRows, tokenCount, toolActivity,
} from '../src/format.js';
import { displayWidth } from '../src/input/width.js';

describe('approvalHint', () => {
  it('says which keys the approval takes, in order, and is short enough for the box', () => {
    const choices = [{ key: 'y', label: 'Run it once' }, { key: 'a', label: 'Allow it' }, { key: 'n', label: 'Refuse' }];
    expect(approvalHint(choices, 'Type y and Enter to run it once, a to always allow it, or anything else to refuse:')).toBe('Press y, a or n then Enter');
    expect(approvalHint(choices.slice(0, 2))).toBe('Press y or a then Enter');
    expect(approvalHint(choices.slice(0, 1))).toBe('Press y then Enter');
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

describe('toolActivity', () => {
  it('says what the model is doing from the call that runs, not a random verb', () => {
    expect(toolActivity('bash', 'Bash', 'npm test')).toBe('Running npm test');
    expect(toolActivity('read_file', 'Read File', 'parser.py')).toBe('Reading parser.py');
    expect(toolActivity('patch_file', 'Patch File', 'src/app.ts')).toBe('Editing src/app.ts');
    expect(toolActivity('write_file', 'Write File', 'notes.txt')).toBe('Writing notes.txt');
    expect(toolActivity('grep', 'Grep', 'TODO')).toBe('Searching for TODO');
    expect(toolActivity('delete_file', 'Delete File', 'old.txt')).toBe('Deleting old.txt');
    expect(toolActivity('ask_user', 'Ask User', 'Which colour?')).toBe('Waiting for your answer');
  });

  it('collapses a command that runs over several lines, and names the call when the target is not known', () => {
    expect(toolActivity('bash', 'Bash', 'npm test &&\n  npm run lint')).toBe('Running npm test && npm run lint');
    expect(toolActivity('bash', 'Bash', '')).toBe('Running a command');
    expect(toolActivity('read_file', 'Read File', '')).toBe('Reading a file');
    expect(toolActivity('future_tool', 'Future Tool', 'x')).toBe('Future Tool x');
    expect(toolActivity('future_tool', '', '')).toBe('future_tool');
  });
});

describe('the status row readout', () => {
  it('counts time the way the installed CLI does', () => {
    expect([0, 999, 6000, 59_999, 60_000, 83_000, 3_723_000].map(elapsedText)).toEqual(['0s', '0s', '6s', '59s', '1m 0s', '1m 23s', '1h 2m 3s']);
  });

  it('counts tokens compactly, in lower case', () => {
    expect([0, 48, 999, 1000, 1240, 12_000, 1_500_000].map(tokenCount)).toEqual(['0', '48', '999', '1k', '1.2k', '12k', '1.5m']);
  });
});

describe('the record of a tool call', () => {
  it('takes the events that say what a call did as its result, and no others', () => {
    for (const kind of ['file_read', 'file_edit', 'file_create', 'file_delete', 'command', 'test', 'tool', 'background_agent']) expect(isResultKind(kind)).toBe(true);
    for (const kind of ['progress', 'milestone', 'warning', 'success', 'error', 'future', '', undefined]) expect(isResultKind(kind)).toBe(false);
  });

  it('puts a command on one line', () => {
    expect(oneLine('  npm  test\n&&  echo   done ')).toBe('npm test && echo done');
  });
});

describe('approvals', () => {
  const choices = [{ key: 'y', label: 'Run it once' }, { key: 'a', label: 'Always' }, { key: 'n', label: 'Refuse' }];

  it('are titled by what asked', () => {
    expect(approvalTitle('bash')).toBe('Run this command?');
    expect(approvalTitle('delete_file')).toBe('Delete this file?');
    expect(approvalTitle('delete_folder')).toBe('Delete this folder?');
    expect(approvalTitle('workspace')).toBe('Permission needed');
    expect(approvalTitle(undefined)).toBe('Permission needed');
  });

  it('are echoed by the choice the answer was: once, always, or refused', () => {
    expect(approvalEcho('y', choices)).toBe('Allowed once');
    expect(approvalEcho('A', choices)).toBe('Always allowed');
    expect(approvalEcho('n', choices)).toBe('Refused');
    expect(approvalEcho('maybe', choices)).toBe('Refused');
    expect(approvalEcho('y', choices.filter((choice) => choice.key !== 'a'))).toBe('Allowed');
  });
});
