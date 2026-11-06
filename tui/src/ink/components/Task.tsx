// What the user asked, in the record: a blank row, then `❯` in grey and the
// text, whitespace collapsed, on the user row's background to the edge of the
// block (no background with NO_COLOR; the glyph stays).

import { oneLine } from '../../format.js';
import { displayWidth } from '../../input/width.js';
import { wrapLine } from '../../wrap.js';
import { Rows, type BlockProps } from './Rows.js';

export interface TaskProps extends BlockProps {
  text: string;
}

export function taskRows({ theme, width, text }: TaskProps): string[] {
  const body = wrapLine(oneLine(text), Math.max(1, width - 2));
  const rows = body.map((row, index) => {
    const line = (index === 0 ? theme.muted('❯') + ' ' : '  ') + row;
    // The background runs to the edge of the block, so the row is padded inside it.
    return theme.userRow(line + ' '.repeat(Math.max(0, width - 2 - displayWidth(row))));
  });
  return ['', ...rows];
}

export function Task(props: TaskProps) {
  return <Rows rows={taskRows(props)} width={props.width} />;
}
