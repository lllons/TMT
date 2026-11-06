// A committed block is rows of text, already laid out for the width: the
// component only hands them to Ink, one `<Text>` a row, cut rather than
// wrapped so one row is one terminal row.

import { Box, Text } from 'ink';

/** What every committed block is given: the theme to paint with and the columns it may use. */
export interface BlockProps {
  theme: import('../../theme.js').Theme;
  width: number;
}

export function Rows({ rows, width }: { rows: string[]; width: number }) {
  return (
    <Box flexDirection="column" width={Math.max(1, width)}>
      {rows.map((row, index) => (
        <Text key={index} wrap="truncate-end">{row || ' '}</Text>
      ))}
    </Box>
  );
}
