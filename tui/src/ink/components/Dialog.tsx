// A question the turn waits on, in the record above the box: a rule in the
// permission colour, a bold title, the backend's text indented two columns,
// and the keys that answer it with the key in the accent colour.

import { wrapLine } from '../../wrap.js';
import { Rows, type BlockProps } from './Rows.js';

export interface DialogProps extends BlockProps {
  /** `Run this command?`, `Delete this file?`, `Permission needed`, or the question itself. */
  title: string;
  /** The text under the title, one entry a row; blank rows are kept. */
  body?: string[];
  /** The answers: the key to press and what it does. */
  options?: { key: string; label: string }[];
}

export function dialogRows({ theme, width, title, body = [], options = [] }: DialogProps): string[] {
  const inside = Math.max(1, width - 2);
  const rows = ['', theme.code('─'.repeat(width)), ' ' + theme.bold(theme.code(title))];
  for (const line of body) rows.push(...(line.trim() === '' ? [''] : wrapLine(line, inside).map((row) => '  ' + row)));
  if (options.length > 0) {
    rows.push('');
    for (const { key, label } of options) {
      const lead = `  ${theme.bold(theme.code(key))}  `;
      wrapLine(label, Math.max(1, width - 5)).forEach((row, index) => rows.push((index === 0 ? lead : ' '.repeat(5)) + row));
    }
  }
  return rows;
}

export function Dialog(props: DialogProps) {
  return <Rows rows={dialogRows(props)} width={props.width} />;
}
