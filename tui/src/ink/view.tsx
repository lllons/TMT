// What Ink draws: the rows committed since the last frame, once, through
// <Static> -- Ink writes them above the live output and never draws them
// again, so they stay in the terminal's own scrollback -- and the live frame
// under them: the blank rows that hold it on the foot of the window, then the
// frame's rows (src/ink/layout.ts composes them, region by region).

import { Box, Static, Text } from 'ink';
import { visibleWidth } from '../live/rows.js';

export interface ViewProps {
  /** Which batch of commits this is: a new one is a new <Static>, written once. */
  batch: number;
  /** Committed rows, sanitized, one terminal line each before the terminal wraps them. */
  commits: string[];
  /** Blank rows above the frame. */
  pad: number;
  /** The frame's rows, styled, each narrower than the window. */
  rows: string[];
}

export function View({ batch, commits, pad, rows }: ViewProps) {
  return (
    <>
      <Static key={batch} items={commits}>
        {(row, index) => <CommittedRow key={index} text={row} />}
      </Static>
      <Box flexDirection="column">
        {pad > 0 ? <Box height={pad} /> : null}
        {rows.map((row, index) => (
          // Cut, never wrapped: one frame row is one screen row, which is what
          // the erase after a resize counts on. The rows already fit.
          <Text key={index} wrap="truncate-end">{row || ' '}</Text>
        ))}
      </Box>
    </>
  );
}

/**
 * A committed row exactly as wide as itself, so Ink never wraps it: a row
 * wider than the window is left to the terminal to wrap, as any program's
 * output is, and a window made wider later joins it up again.
 */
function CommittedRow({ text }: { text: string }) {
  return (
    <Box width={Math.max(1, visibleWidth(text))}>
      <Text>{text || ' '}</Text>
    </Box>
  );
}
