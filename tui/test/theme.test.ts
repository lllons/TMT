// The palette is the Python UI's (agent_ui.py), and degrades to 256 colours.

import { describe, expect, it } from 'vitest';
import {
  cycleBar, cycleText, detectTruecolor, DIM, fgCode, gradient, gradientAt, gradientPhase, GREEN, LIGHT_GREEN, ORANGE, paint, RED, YELLOW,
} from '../src/theme.js';

describe('the gradient', () => {
  it('has the stops of agent_ui._gradient, clamped to 10..100', () => {
    expect(gradient(10)).toEqual(RED);
    expect(gradient(0)).toEqual(RED);
    expect(gradient(40)).toEqual(ORANGE);
    expect(gradient(95)).toEqual(GREEN);
    expect(gradient(100)).toEqual(LIGHT_GREEN);
    expect(gradient(250)).toEqual(LIGHT_GREEN);
    expect(gradient(25)).toEqual([235, 77, 30]);
  });

  it('runs red to green over the first half of the cycle and back over the second', () => {
    expect(gradientAt(0)).toEqual(RED);
    expect(gradientAt(0.5)).toEqual(LIGHT_GREEN);
    expect(gradientAt(1)).toEqual(RED);
    expect(gradientAt(0.25)).toEqual(gradientAt(0.75));
    expect(gradientAt(-0.25)).toEqual(gradientAt(0.75));
    expect(gradientPhase(2400 * 5 + 600)).toBeCloseTo(0.25);
  });
});

describe('painting', () => {
  it('is truecolor, or the nearest of the 256', () => {
    expect(fgCode(DIM, true)).toBe('\x1b[38;2;88;88;88m');
    expect(fgCode(DIM, false)).toBe('\x1b[38;5;240m');
    expect(fgCode(YELLOW, false)).toBe('\x1b[38;5;178m');
    expect(fgCode(GREEN, false)).toBe('\x1b[38;5;41m');
    expect(paint(RED, 'x', true)).toBe('\x1b[38;2;220;38;38mx\x1b[0m');
    expect(paint(RED, '', true)).toBe('');
  });

  it('cycles a word and a bar along the gradient, and leaves the empty cells dim', () => {
    // The last character of a full-spread word is back where the first one is: the cycle wraps.
    expect(cycleText('ab', 0, true)).toBe('\x1b[38;2;220;38;38ma\x1b[38;2;220;38;38mb\x1b[0m');
    expect(cycleText('ab', 0, true, 0.5)).toBe('\x1b[38;2;220;38;38ma\x1b[38;2;74;222;128mb\x1b[0m');
    expect(cycleBar(50, 4, 0, true)).toBe(
      '\x1b[38;2;220;38;38m█' + `\x1b[38;2;${gradientAt(0.6 / 3).join(';')}m█` + '\x1b[38;2;88;88;88m░\x1b[38;2;88;88;88m░\x1b[0m');
    expect(cycleText('', 0, true)).toBe('');
  });
});

describe('what the terminal advertises', () => {
  it('is truecolor for COLORTERM truecolor or 24bit, and for Windows Terminal', () => {
    expect(detectTruecolor({ COLORTERM: 'truecolor' })).toBe(true);
    expect(detectTruecolor({ COLORTERM: '24BIT' })).toBe(true);
    expect(detectTruecolor({ WT_SESSION: 'abc' })).toBe(true);
    expect(detectTruecolor({})).toBe(false);
    expect(detectTruecolor({ COLORTERM: 'yes', TERM: 'xterm-256color' })).toBe(false);
  });
});
