// The session header: no banner, three dim rows. `TMT 0.1.1`, the workspace,
// and the tip of the day, with a blank row on each side.

import { wrapLine } from '../../wrap.js';
import { Rows, type BlockProps } from './Rows.js';

export interface HeaderProps extends BlockProps {
  version?: string;
  workspace?: string;
  tip?: { gesture?: string; detail?: string };
}

export function headerRows({ theme, width, version, workspace, tip }: HeaderProps): string[] {
  const rows = [theme.bold(theme.accent('TMT')) + (version ? ' ' + theme.dim(version) : '')];
  if (workspace) rows.push(...wrapLine(`workspace  ${workspace}`, width).map(theme.dim));
  if (tip?.gesture) rows.push(...wrapLine(`tip  ${tip.gesture}${tip.detail ? '  ' + tip.detail : ''}`, width).map(theme.dim));
  return ['', ...rows, ''];
}

export function Header(props: HeaderProps) {
  return <Rows rows={headerRows(props)} width={props.width} />;
}
