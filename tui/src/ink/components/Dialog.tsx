// A question the turn waits on, in the record above the box: a heavy orange
// rule, then a heavy orange rail down the bold title, the backend's text
// indented two columns and the keys that answer it, the key in orange.

import { wrapLine } from '../../wrap.js';
import { RAIL, railed } from '../live.js';
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
  const rail = theme.needsYou(RAIL.heavy);
  const rows = ['', theme.needsYou('━'.repeat(width)), railed(rail, theme.bold(title))];
  for (const line of body) rows.push(...(line.trim() === '' ? [railed(rail, '')] : wrapLine(line, Math.max(1, width - 4)).map((row) => railed(rail, '  ' + row))));
  if (options.length > 0) {
    rows.push(railed(rail, ''));
    for (const { key, label } of options) {
      wrapLine(label, Math.max(1, width - 5)).forEach((row, index) => rows.push(railed(rail, (index === 0 ? theme.bold(theme.needsYou(key)) + '  ' : '   ') + row)));
    }
  }
  return rows;
}

export function Dialog(props: DialogProps) {
  return <Rows rows={dialogRows(props)} width={props.width} />;
}
