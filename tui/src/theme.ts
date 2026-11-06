// The colours of the Python UI (agent_ui.py), so a row drawn here is the colour
// of the same row there. Truecolor where the terminal says it has it, the
// nearest of the 256 otherwise; none at all when the caller turns colour off.

export type Rgb = readonly [number, number, number];

// The stops of the progress gradient: red -> orange -> yellow -> lime -> green.
export const RED: Rgb = [220, 38, 38];
export const ORANGE: Rgb = [249, 115, 22];
export const YELLOW: Rgb = [234, 179, 8];
export const LIME: Rgb = [132, 204, 22];
export const GREEN: Rgb = [34, 197, 94];
export const LIGHT_GREEN: Rgb = [74, 222, 128];
/** DIM: the one neutral under everything that is context rather than the message. */
export const DIM: Rgb = [88, 88, 88];

const STOPS: readonly (readonly [number, Rgb])[] = [[10, RED], [40, ORANGE], [60, YELLOW], [80, LIME], [95, GREEN], [100, LIGHT_GREEN]];

/** Seconds for one there-and-back pass of the shared colour cycle (GRADIENT_CYCLE). */
export const GRADIENT_CYCLE_MS = 2400;
/** The glyph that turns beside the activity readout, and how long each is held. */
export const ACTIVITY_GLYPHS = ['✻', '✽', '✻'] as const;
export const ACTIVITY_TICK_MS = 400;

const RESET = '\x1b[0m';

/** Whether the terminal advertises 24-bit colour: COLORTERM, or Windows Terminal. */
export function detectTruecolor(env: NodeJS.ProcessEnv = process.env): boolean {
  const colorterm = (env['COLORTERM'] ?? '').toLowerCase();
  return colorterm === 'truecolor' || colorterm === '24bit' || Boolean(env['WT_SESSION']);
}

/** The gradient at `progress` (10..100), as agent_ui._gradient. */
export function gradient(progress: number): Rgb {
  const at = Math.max(10, Math.min(100, progress));
  for (let index = 0; index < STOPS.length - 1; index += 1) {
    const [p1, c1] = STOPS[index]!;
    const [p2, c2] = STOPS[index + 1]!;
    if (at <= p2) {
      const ratio = (at - p1) / (p2 - p1);
      const mix = (channel: 0 | 1 | 2): number => Math.round(c1[channel] + (c2[channel] - c1[channel]) * ratio);
      return [mix(0), mix(1), mix(2)];
    }
  }
  return STOPS[STOPS.length - 1]![1];
}

/** The colour at a point of the repeating cycle: red -> green over the first half, back over the second. */
export function gradientAt(ratio: number): Rgb {
  const wrapped = ((ratio % 1) + 1) % 1;
  const travel = wrapped <= 0.5 ? wrapped * 2 : (1 - wrapped) * 2;
  return gradient(Math.round(10 + 90 * travel));
}

/** Where the shared colour cycle stands at `now` (ms), in [0, 1). */
export const gradientPhase = (now: number): number => (now % GRADIENT_CYCLE_MS) / GRADIENT_CYCLE_MS;

const CUBE = [0, 95, 135, 175, 215, 255];

/** The xterm-256 colour nearest to `rgb`: the closer of the 6x6x6 cube and the grey ramp. */
export function nearest256([r, g, b]: Rgb): number {
  const level = (value: number): number => CUBE.reduce((best, step, index) => (Math.abs(step - value) < Math.abs(CUBE[best]! - value) ? index : best), 0);
  const [ri, gi, bi] = [level(r), level(g), level(b)];
  const distance = (x: Rgb, y: Rgb): number => (x[0] - y[0]) ** 2 + (x[1] - y[1]) ** 2 + (x[2] - y[2]) ** 2;
  const cube = distance([r, g, b], [CUBE[ri]!, CUBE[gi]!, CUBE[bi]!]);
  const grey = Math.max(0, Math.min(23, Math.round((((r + g + b) / 3) - 8) / 10)));
  const greyValue = 8 + 10 * grey;
  return distance([r, g, b], [greyValue, greyValue, greyValue]) < cube ? 232 + grey : 16 + 36 * ri + 6 * gi + bi;
}

/** The escape that sets the foreground to `rgb`. */
export function fgCode(rgb: Rgb, truecolor: boolean): string {
  return truecolor ? `\x1b[38;2;${rgb[0]};${rgb[1]};${rgb[2]}m` : `\x1b[38;5;${nearest256(rgb)}m`;
}

/** `text` in `rgb`, closed with a reset. */
export function paint(rgb: Rgb, text: string, truecolor: boolean): string {
  return text ? fgCode(rgb, truecolor) + text + RESET : text;
}

/** `text` across the gradient, offset by the running cycle (agent_ui.cycle_text). */
export function cycleText(text: string, phase: number, truecolor: boolean, spread = 1): string {
  const chars = [...text];
  if (chars.length === 0) return text;
  const span = Math.max(1, chars.length - 1);
  return chars.map((char, index) => fgCode(gradientAt(phase + (spread * index) / span), truecolor) + char).join('') + RESET;
}

/** A progress bar whose filled cells ride the cycle (agent_ui.cycle_bar); empty cells are dim. */
export function cycleBar(percent: number, width: number, phase: number, truecolor: boolean, spread = 0.6): string {
  const filled = Math.round((width * percent) / 100);
  const span = Math.max(1, width - 1);
  const cells: string[] = [];
  for (let index = 0; index < width; index += 1) {
    cells.push(index < filled
      ? fgCode(gradientAt(phase + (spread * index) / span), truecolor) + '█'
      : fgCode(DIM, truecolor) + '░');
  }
  return cells.join('') + RESET;
}

// ---------------------------------------------------------------------------
// The Claude Code look: one role per kind of thing a row can be, and a theme
// that paints them. The values are the dark theme of the study copy of the
// installed CLI (success green, error red, the orange accent, the code and
// permission blue, the user row's grey). Dim is the terminal's own faint
// attribute, not a grey of its own, so it follows whatever the text would be.

export const SUCCESS: Rgb = [78, 186, 101];
export const ERROR: Rgb = [255, 107, 128];
export const WARNING: Rgb = [255, 193, 7];
export const ACCENT: Rgb = [215, 119, 87];
export const ACCENT_SHIMMER: Rgb = [235, 159, 127];
/** The grey of the user's own line marker and of anything inactive. */
export const INACTIVE: Rgb = [153, 153, 153];
/** The prompt box's rules. */
export const BORDER: Rgb = [136, 136, 136];
/** Inline code, the accent key of a dialog, and the dialog's rule. */
export const CODE: Rgb = [177, 185, 249];
export const INFO: Rgb = [0, 204, 204];
export const LINK: Rgb = [122, 180, 232];
/** The background of the user's task row. */
export const USER_BACKGROUND: Rgb = [55, 55, 55];
export const DIFF_ADDED: Rgb = [34, 92, 43];
export const DIFF_REMOVED: Rgb = [122, 41, 54];

/** The star that turns beside the status row, swept back and forth as one breath of 2 s. */
export const STAR_FRAMES = ['·', '✢', '*', '✶', '✻', '✽'] as const;
export const STAR_PERIOD_MS = 2000;

/** The star at `now` (ms): a cosine sweep through the frames, as the installed CLI breathes it. */
export function starAt(now: number): string {
  const breath = (1 - Math.cos((2 * Math.PI * (now % STAR_PERIOD_MS)) / STAR_PERIOD_MS)) / 2;
  return STAR_FRAMES[Math.round(breath * (STAR_FRAMES.length - 1))]!;
}

const bgCode = (rgb: Rgb, truecolor: boolean): string => (truecolor ? `\x1b[48;2;${rgb[0]};${rgb[1]};${rgb[2]}m` : `\x1b[48;5;${nearest256(rgb)}m`);

type Paint = (text: string) => string;

/**
 * Paints text in the roles above. With colour off every function hands the
 * text back untouched: the glyphs stay, no escape is written (NO_COLOR).
 * Close codes are the narrow ones (`22` bold and dim, `39` colour), so a span
 * inside another keeps the outer one, except that bold and dim share a close.
 */
export interface Theme {
  readonly color: boolean;
  readonly truecolor: boolean;
  dim: Paint;
  bold: Paint;
  italic: Paint;
  underline: Paint;
  strike: Paint;
  success: Paint;
  error: Paint;
  warning: Paint;
  accent: Paint;
  shimmer: Paint;
  muted: Paint;
  border: Paint;
  code: Paint;
  info: Paint;
  link: Paint;
  fg(rgb: Rgb, text: string): string;
  /** `text` on the user row's grey. */
  userRow: Paint;
}

export function makeTheme(look: { color: boolean; truecolor: boolean }): Theme {
  const { color, truecolor } = look;
  const wrap = (on: string, off: string): Paint => (text) => (color && text ? on + text + off : text);
  const fg = (rgb: Rgb, text: string): string => (color && text ? fgCode(rgb, truecolor) + text + '\x1b[39m' : text);
  const role = (rgb: Rgb): Paint => (text) => fg(rgb, text);
  return {
    color, truecolor, fg,
    dim: wrap('\x1b[2m', '\x1b[22m'),
    bold: wrap('\x1b[1m', '\x1b[22m'),
    italic: wrap('\x1b[3m', '\x1b[23m'),
    underline: wrap('\x1b[4m', '\x1b[24m'),
    strike: wrap('\x1b[9m', '\x1b[29m'),
    success: role(SUCCESS), error: role(ERROR), warning: role(WARNING), accent: role(ACCENT), shimmer: role(ACCENT_SHIMMER),
    muted: role(INACTIVE), border: role(BORDER), code: role(CODE), info: role(INFO), link: role(LINK),
    userRow: wrap(bgCode(USER_BACKGROUND, truecolor), '\x1b[49m'),
  };
}
