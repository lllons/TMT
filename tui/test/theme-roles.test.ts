// The signal rail look's colours: the gradient read as a state ramp, painted in
// truecolor or the nearest of 256, or not at all.

import { describe, expect, it } from 'vitest';
import { DIFF_ADDED, DIFF_REMOVED, GREEN, INFO, LIME, makeTheme, nearest256, ORANGE, RED, YELLOW } from '../src/theme.js';

describe('the roles', () => {
  it('are the gradient\'s own stops, one cool hue and darker tints of green and red for a diff', () => {
    expect(RED).toEqual([220, 38, 38]);
    expect(ORANGE).toEqual([249, 115, 22]);
    expect(YELLOW).toEqual([234, 179, 8]);
    expect(LIME).toEqual([132, 204, 22]);
    expect(GREEN).toEqual([34, 197, 94]);
    expect(INFO).toEqual([0, 204, 204]);
    // Darker than the colour they tint, the same hue (the channel that leads stays the one that leads).
    expect(Math.max(...DIFF_ADDED)).toBeLessThan(Math.max(...GREEN));
    expect(DIFF_ADDED.indexOf(Math.max(...DIFF_ADDED))).toBe(GREEN.indexOf(Math.max(...GREEN)));
    expect(Math.max(...DIFF_REMOVED)).toBeLessThan(Math.max(...RED));
    expect(DIFF_REMOVED.indexOf(Math.max(...DIFF_REMOVED))).toBe(RED.indexOf(Math.max(...RED)));
  });
});

describe('a theme with colour', () => {
  const truecolor = makeTheme({ color: true, truecolor: true });
  const palette = makeTheme({ color: true, truecolor: false });

  it('paints a role in truecolor, closed with the default colour only, so what is around it keeps its own', () => {
    expect(truecolor.success('ok')).toBe('\x1b[38;2;34;197;94mok\x1b[39m');
    expect(truecolor.error('no')).toBe('\x1b[38;2;220;38;38mno\x1b[39m');
    expect(truecolor.warning('hm')).toBe('\x1b[38;2;234;179;8mhm\x1b[39m');
    expect(truecolor.working('go')).toBe('\x1b[38;2;132;204;22mgo\x1b[39m');
    expect(truecolor.needsYou('you')).toBe('\x1b[38;2;249;115;22myou\x1b[39m');
    expect(truecolor.ref('x')).toBe('\x1b[38;2;0;204;204mx\x1b[39m');
    expect(truecolor.fg([1, 2, 3], 'y')).toBe('\x1b[38;2;1;2;3my\x1b[39m');
  });

  it('paints the same role in the nearest of the 256 where the terminal has no truecolor', () => {
    expect(palette.needsYou('hi')).toBe(`\x1b[38;5;${nearest256(ORANGE)}mhi\x1b[39m`);
    expect(palette.success('ok')).toBe(`\x1b[38;5;${nearest256(GREEN)}mok\x1b[39m`);
    expect(palette.success('ok')).not.toContain('38;2');
  });

  it('has the attributes as the terminal\'s own, each closed by its own code', () => {
    expect(truecolor.dim('d')).toBe('\x1b[2md\x1b[22m');
    expect(truecolor.bold('b')).toBe('\x1b[1mb\x1b[22m');
    expect(truecolor.italic('i')).toBe('\x1b[3mi\x1b[23m');
    expect(truecolor.underline('u')).toBe('\x1b[4mu\x1b[24m');
    expect(truecolor.strike('s')).toBe('\x1b[9ms\x1b[29m');
  });

  it('leaves an empty string alone, so no empty span is written', () => {
    for (const paint of [truecolor.dim, truecolor.bold, truecolor.success, truecolor.working]) expect(paint('')).toBe('');
  });
});

describe('a theme with no colour (NO_COLOR)', () => {
  const none = makeTheme({ color: false, truecolor: true });

  it('hands every text back as it is: the glyphs stay, nothing is written but them', () => {
    for (const paint of [none.dim, none.bold, none.italic, none.underline, none.strike, none.success, none.error, none.warning, none.working, none.needsYou, none.ref]) {
      expect(paint('┃ text')).toBe('┃ text');
    }
    expect(none.fg([1, 2, 3], 'z')).toBe('z');
  });
});
