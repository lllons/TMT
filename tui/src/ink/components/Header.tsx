// The session header: no banner, three dim rows on a thin rail. `TMT 0.1.1`,
// the workspace, and the tip of the day, with a blank row on each side.

import { wrapLine } from '../../wrap.js';
import { RAIL, railed } from '../live.js';
import { Rows, type BlockProps } from './Rows.js';

export interface HeaderProps extends BlockProps {
  version?: string;
  workspace?: string;
  tip?: { gesture?: string; detail?: string };
}

export function headerRows({ theme, width, version, workspace, tip }: HeaderProps): string[] {
  const rail = theme.dim(RAIL.thin);
  const rows = [railed(rail, theme.bold(theme.success('TMT')) + (version ? ' ' + theme.dim(version) : ''))];
  const dim = (text: string): string[] => wrapLine(text, Math.max(1, width - 2)).map((row) => railed(rail, theme.dim(row)));
  if (workspace) rows.push(...dim(`workspace  ${workspace}`));
  if (tip?.gesture) rows.push(...dim(`tip  ${tip.gesture}${tip.detail ? '  ' + tip.detail : ''}`));
  return ['', ...rows, ''];
}

export function Header(props: HeaderProps) {
  return <Rows rows={headerRows(props)} width={props.width} />;
}
