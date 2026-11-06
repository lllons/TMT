// The Claude Code look's colours: the roles of the study copy of the installed
// CLI's dark theme, painted in truecolor or the nearest of 256, or not at all.

import { describe, expect, it } from 'vitest';
import {
  ACCENT, ACCENT_SHIMMER, BORDER, CODE, DIFF_ADDED, DIFF_REMOVED, ERROR, INACTIVE, makeTheme, nearest256, STAR_FRAMES, starAt, SUCCESS, USER_BACKGROUND, WARNING,
} from '../src/theme.js';

describe('the roles', () => {
  it('are the values of the installed CLI\'s dark theme', () => {
    expect(SUCCESS).toEqual([78, 186, 101]);
    expect(ERROR).toEqual([255, 107, 128]);
    expect(WARNING).toEqual([255, 193, 7]);
    expect(ACCENT).toEqual([215, 119, 87]);
    expect(ACCENT_SHIMMER).toEqual([235, 159, 127]);
    expect(INACTIVE).toEqual([153, 153, 153]);
    expect(BORDER).toEqual([136, 136, 136]);
    expect(CODE).toEqual([177, 185, 249]);
    expect(USER_BACKGROUND).toEqual([55, 55, 55]);
    expect(DIFF_ADDED).toEqual([34, 92, 43]);
    expect(DIFF_REMOVED).toEqual([122, 41, 54]);
  });
});

describe('a theme with colour', () => {
  const truecolor = makeTheme({ color: true, truecolor: true });
  const palette = makeTheme({ color: true, truecolor: false });

  it('paints a role in truecolor, closed with the default colour only, so what is around it keeps its own', () => {
    expect(truecolor.success('ok')).toBe('\x1b[38;2;78;186;101mok\x1b[39m');
    expect(truecolor.error('no')).toBe('\x1b[38;2;255;107;128mno\x1b[39m');
    expect(truecolor.accent('hi')).toBe('\x1b[38;2;215;119;87mhi\x1b[39m');
    expect(truecolor.code('x')).toBe('\x1b[38;2;177;185;249mx\x1b[39m');
    expect(truecolor.fg([1, 2, 3], 'y')).toBe('\x1b[38;2;1;2;3my\x1b[39m');
  });

  it('paints the same role in the nearest of the 256 where the terminal has no truecolor', () => {
    expect(palette.accent('hi')).toBe(`\x1b[38;5;${nearest256(ACCENT)}mhi\x1b[39m`);
    expect(palette.success('ok')).toBe(`\x1b[38;5;${nearest256(SUCCESS)}mok\x1b[39m`);
    expect(palette.success('ok')).not.toContain('38;2');
    expect(palette.userRow('x')).toBe(`\x1b[48;5;${nearest256(USER_BACKGROUND)}mx\x1b[49m`);
  });

  it('has the attributes as the terminal\'s own, each closed by its own code', () => {
    expect(truecolor.dim('d')).toBe('\x1b[2md\x1b[22m');
    expect(truecolor.bold('b')).toBe('\x1b[1mb\x1b[22m');
    expect(truecolor.italic('i')).toBe('\x1b[3mi\x1b[23m');
    expect(truecolor.underline('u')).toBe('\x1b[4mu\x1b[24m');
    expect(truecolor.strike('s')).toBe('\x1b[9ms\x1b[29m');
    expect(truecolor.userRow('x')).toBe('\x1b[48;2;55;55;55mx\x1b[49m');
  });

  it('leaves an empty string alone, so no empty span is written', () => {
    for (const paint of [truecolor.dim, truecolor.bold, truecolor.success, truecolor.userRow]) expect(paint('')).toBe('');
  });
});

describe('a theme with no colour (NO_COLOR)', () => {
  const none = makeTheme({ color: false, truecolor: true });

  it('hands every text back as it is: the glyphs stay, nothing is written but them', () => {
    for (const paint of [none.dim, none.bold, none.italic, none.underline, none.strike, none.success, none.error, none.warning, none.accent, none.shimmer, none.muted,
      none.border, none.code, none.info, none.link, none.userRow]) expect(paint('● text')).toBe('● text');
    expect(none.fg([1, 2, 3], 'z')).toBe('z');
  });
});

describe('the star', () => {
  it('has the six glyphs of the installed CLI, in order', () => {
    expect(STAR_FRAMES).toEqual(['·', '✢', '*', '✶', '✻', '✽']);
  });

  it('breathes through them in a cosine over two seconds: the first at the start, the last in the middle', () => {
    expect(starAt(0)).toBe('·');
    expect(starAt(1000)).toBe('✽');
    expect(starAt(2000)).toBe('·');
    expect(starAt(123_000)).toBe(starAt(1000));
    const seen = new Set(Array.from({ length: 41 }, (_, step) => starAt(step * 50)));
    expect(seen.size).toBe(6);
  });
});
