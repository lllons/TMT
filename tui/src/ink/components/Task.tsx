// What the user asked, in the record: a blank row, then the text, whitespace
// collapsed, behind a double rail in the terminal's own colour. No background,
// no marker: the double rail is the user's.

import { oneLine } from '../../format.js';
import { wrapLine } from '../../wrap.js';
import { RAIL, railed } from '../live.js';
import { Rows, type BlockProps } from './Rows.js';

export interface TaskProps extends BlockProps {
  text: string;
}

export function taskRows({ width, text }: TaskProps): string[] {
  return ['', ...wrapLine(oneLine(text), Math.max(1, width - 2)).map((row) => railed(RAIL.double, row))];
}

export function Task(props: TaskProps) {
  return <Rows rows={taskRows(props)} width={props.width} />;
}
